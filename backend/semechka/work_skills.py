"""Work Mode skills: Markdown instructions a session's agent can read (docs/WORK_MODE.md section 5 and 9.8).

A skill is DATA. It has a name, a one-line description, a category, a Markdown body (at most 64 KB) and optional small text
files (at most 1 MB together). The session prompt carries only the index (name and description); `read_skill` returns the text
and copies the files into the container. Nothing in a skill is ever interpreted as configuration: the front matter parser
reads exactly three keys (name, description, category; `title` on import) and drops every other line, so a skill cannot grant
tools, network, approvals or limits no matter what it says.

Scopes: `org` (managed with `work.skills.manage`, visible to every member of the organisation) and `user` (visible to the
owner alone; everyone else gets the same 404 as for a missing skill). Names are unique per scope (and per organisation or owner).
Body and files are encrypted with the organisation's content key.
"""
from __future__ import annotations

import json
import re
import time

from sqlalchemy import select

from . import audit, work_svc
from .models import Meta
from .models_work import WorkSession, WorkSkill

CATEGORIES = ("coding", "research", "image-generation", "video-production", "media-editing", "document-creation", "data-analysis", "general")
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
FILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
BODY_MAX = 64 * 1024
FILES_MAX = 1024 * 1024
DESCRIPTION_MAX = 300
TITLE_MAX = 200
MAX_PER_SCOPE = 200
SESSION_SKILLS_MAX = 20


class SkillError(work_svc.WorkError):
    pass


# ---- front matter ------------------------------------------------------------------------------------------------------
_FM = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.S)
_KV = re.compile(r"^([A-Za-z_][\w-]*):[ \t]*(.*)$")
KNOWN_KEYS = ("name", "description", "category", "title")


def _unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v.endswith('"'):
        try:
            return str(json.loads(v))
        except ValueError:
            return v[1:-1]
    if len(v) >= 2 and v[0] == "'" and v.endswith("'"):
        return v[1:-1].replace("''", "'")
    return re.sub(r"\s+#.*$", "", v)


def parse_markdown(src: str) -> tuple[dict, str, bool]:
    """-> (meta, body, had_front_matter). Only flat `key: value` lines for name, description, category and title are read; nested
    values, lists, anchors, tags and every other key are ignored. Nothing is evaluated."""
    text = src.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    m = _FM.match(text)
    if not m:
        return {}, text, False
    meta: dict = {}
    lines = m.group(1).split("\n")
    i = 0
    while i < len(lines):
        kv = _KV.match(lines[i])
        i += 1
        if not kv:
            continue
        key, val = kv.group(1).lower(), kv.group(2)
        if re.fullmatch(r"[|>][+-]?", val.strip()):  # a block scalar: the indented lines that follow
            parts = []
            while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                parts.append(lines[i].strip())
                i += 1
            val = (" " if val.strip().startswith(">") else "\n").join(parts).strip()
            if key in KNOWN_KEYS:
                meta[key] = val
            continue
        if key in KNOWN_KEYS:
            meta[key] = _unquote(val)
    return meta, text[m.end():].lstrip("\n"), True


def _utf8(s: str) -> int:
    return len(s.encode("utf-8"))


def clean_input(body: dict, *, partial_of: WorkSkill | None = None) -> dict:
    """Validate a create/update body. Unknown keys are ignored (never stored). -> normalised fields."""
    def pick(k, default=None):
        return body[k] if k in body and body[k] is not None else default
    name = str(pick("name", partial_of.name if partial_of else "")).strip()
    title = str(pick("title", partial_of.title if partial_of else "")).strip()
    description = " ".join(str(pick("description", partial_of.description if partial_of else "")).split())
    category = str(pick("category", partial_of.category if partial_of else "general")).strip() or "general"
    enabled = bool(pick("enabled", partial_of.enabled if partial_of else True))
    if not NAME_RE.match(name):
        raise SkillError("invalid", "The name may use lower-case letters, digits and hyphens only (at most 64 characters), "
                                    "and must start and end with a letter or digit.", 422)
    if not description:
        raise SkillError("invalid", "Add a one-line description: the agent sees only this line until it opens the skill.", 422)
    if len(description) > DESCRIPTION_MAX:
        raise SkillError("invalid", f"The description is longer than {DESCRIPTION_MAX} characters.", 422)
    if len(title) > TITLE_MAX:
        raise SkillError("invalid", f"The title is longer than {TITLE_MAX} characters.", 422)
    if category not in CATEGORIES:
        raise SkillError("invalid", f"The category must be one of: {', '.join(CATEGORIES)}.", 422)
    return {"name": name, "title": title or name, "description": description, "category": category, "enabled": enabled}


def clean_body(body_md, files) -> tuple[str, list[dict]]:
    if not isinstance(body_md, str) or not body_md.strip():
        raise SkillError("invalid", "The skill needs some instructions.", 422)
    if _utf8(body_md) > BODY_MAX:
        raise SkillError("invalid", f"The instructions are over the {BODY_MAX // 1024} KB limit.", 422)
    out, seen, total = [], set(), 0
    for f in files or []:
        if not isinstance(f, dict) or not isinstance(f.get("name"), str) or not isinstance(f.get("content", ""), str):
            raise SkillError("invalid", "Each supporting file needs a name and text content.", 422)
        n, content = f["name"], f.get("content", "")
        if not FILE_NAME_RE.match(n):
            raise SkillError("invalid", f'"{n[:40]}" is not a valid file name. Use letters, digits, dots, hyphens and underscores.', 422)
        if n.lower() in seen or n.lower() == "skill.md":
            raise SkillError("invalid", f'The file name "{n}" is already used.', 422)
        seen.add(n.lower())
        total += _utf8(content)
        out.append({"name": n, "content": content})
    if total > FILES_MAX:
        raise SkillError("invalid", "Supporting files are over the 1 MB limit together.", 422)
    if len(out) > 20:
        raise SkillError("invalid", "A skill can have at most 20 supporting files.", 422)
    return body_md, out


# ---- storage -----------------------------------------------------------------------------------------------------------
def _body(st, sk: WorkSkill) -> str:
    return work_svc.dec(st, sk.org_id, sk.body_enc)


def _files(st, sk: WorkSkill) -> list[dict]:
    try:
        return json.loads(work_svc.dec(st, sk.org_id, sk.files_enc)) if sk.files_enc else []
    except ValueError:
        return []


def can_edit(principal, user_id: str, sk: WorkSkill) -> bool:
    if sk.scope == "user":
        return sk.owner_user_id == user_id
    return principal.can("work.skills.manage")


def summary(sk: WorkSkill, principal=None, user_id: str = "") -> dict:
    out = {"id": sk.id, "scope": sk.scope, "name": sk.name, "title": sk.title, "description": sk.description, "category": sk.category,
           "enabled": sk.enabled, "updated_at": work_svc.iso(sk.updated_at), "created_at": work_svc.iso(sk.created_at)}
    if principal is not None:
        out["can_edit"] = can_edit(principal, user_id, sk)
    return out


def full(st, sk: WorkSkill, principal=None, user_id: str = "") -> dict:
    return {**summary(sk, principal, user_id), "body_md": _body(st, sk), "files": _files(st, sk)}


def visible_query(org_id: str, user_id: str):
    from sqlalchemy import and_, or_
    return select(WorkSkill).where(or_(and_(WorkSkill.scope == "org", WorkSkill.org_id == org_id),
                                       and_(WorkSkill.scope == "user", WorkSkill.owner_user_id == user_id)))


def list_for(st, s, user, principal, *, include_disabled: bool = True) -> list[WorkSkill]:
    org_id = work_svc.user_org(s, user.id)
    if org_id is None:
        return []
    ensure_builtin(st, s, org_id)
    rows = s.execute(visible_query(org_id, user.id).order_by(WorkSkill.scope.desc(), WorkSkill.name)).scalars().all()
    return [r for r in rows if include_disabled or r.enabled]


def get_visible(s, user, sid: str) -> WorkSkill:
    """Someone else's personal skill, another organisation's skill and a missing one are all the same 404."""
    sk = s.get(WorkSkill, sid)
    org_id = work_svc.user_org(s, user.id)
    if sk is None or org_id is None or (sk.scope == "org" and sk.org_id != org_id) or (sk.scope == "user" and sk.owner_user_id != user.id):
        raise SkillError("not_found", "No such skill.", 404)
    return sk


def _require_scope(principal, scope: str) -> None:
    if scope not in ("org", "user"):
        raise SkillError("invalid", "scope must be 'org' or 'user'.", 422)
    if scope == "org" and not principal.can("work.skills.manage"):
        raise SkillError("forbidden", "You do not have permission to manage organisation skills.", 403)


def _taken(s, org_id: str, user_id: str, scope: str, name: str, except_id: str | None = None) -> bool:
    q = select(WorkSkill.id).where(WorkSkill.scope == scope, WorkSkill.name == name)
    q = q.where(WorkSkill.org_id == org_id) if scope == "org" else q.where(WorkSkill.owner_user_id == user_id)
    return any(i != except_id for (i,) in s.execute(q))


def create(st, s, user, principal, body: dict, ip: str = "") -> WorkSkill:
    scope = str(body.get("scope") or "user")
    _require_scope(principal, scope)
    org_id = work_svc.user_org(s, user.id)
    if org_id is None:
        raise SkillError("no_org", "Work Mode needs an organisation membership.", 409)
    f = clean_input(body)
    text, files = clean_body(body.get("body_md"), body.get("files"))
    if _taken(s, org_id, user.id, scope, f["name"]):
        raise SkillError("exists", f'A {"organisation" if scope == "org" else "personal"} skill called "{f["name"]}" already exists.', 409)
    n = s.query(WorkSkill).filter(WorkSkill.scope == scope, (WorkSkill.org_id == org_id) if scope == "org" else (WorkSkill.owner_user_id == user.id)).count()
    if n >= MAX_PER_SCOPE:
        raise SkillError("quota", f"There are already {n} skills in this scope.", 429)
    sk = WorkSkill(scope=scope, org_id=org_id, owner_user_id=user.id if scope == "user" else None, **f,
                   body_enc=work_svc.enc(st, org_id, text), files_enc=work_svc.enc(st, org_id, json.dumps(files)) if files else "")
    s.add(sk)
    s.flush()
    audit.record(s, user.id, "work.skill.create", sk.id, {"name": sk.name, "scope": scope, "category": sk.category}, ip, org_id=org_id)
    return sk


def update(st, s, user, principal, sk: WorkSkill, body: dict, ip: str = "") -> WorkSkill:
    if not can_edit(principal, user.id, sk):
        raise SkillError("forbidden", "You cannot change this skill.", 403)
    if "scope" in body and body["scope"] not in (None, sk.scope):
        raise SkillError("invalid", "A skill cannot move between scopes; create a copy instead.", 422)
    f = clean_input(body, partial_of=sk)
    text = body.get("body_md") if body.get("body_md") is not None else _body(st, sk)
    files = body["files"] if "files" in body and body["files"] is not None else _files(st, sk)
    text, files = clean_body(text, files)
    if f["name"] != sk.name and _taken(s, sk.org_id, user.id, sk.scope, f["name"], sk.id):
        raise SkillError("exists", f'A skill called "{f["name"]}" already exists.', 409)
    for k, v in f.items():
        setattr(sk, k, v)
    sk.body_enc = work_svc.enc(st, sk.org_id, text)
    sk.files_enc = work_svc.enc(st, sk.org_id, json.dumps(files)) if files else ""
    sk.updated_at = time.time()
    audit.record(s, user.id, "work.skill.update", sk.id, {"name": sk.name, "scope": sk.scope}, ip, org_id=sk.org_id)
    return sk


def delete(st, s, user, principal, sk: WorkSkill, ip: str = "") -> None:
    if not can_edit(principal, user.id, sk):
        raise SkillError("forbidden", "You cannot delete this skill.", 403)
    sid, name, scope, org_id = sk.id, sk.name, sk.scope, sk.org_id
    s.delete(sk)
    audit.record(s, user.id, "work.skill.delete", sid, {"name": name, "scope": scope}, ip, org_id=org_id)


def set_enabled(st, s, user, principal, sk: WorkSkill, on: bool, ip: str = "") -> WorkSkill:
    if not can_edit(principal, user.id, sk):
        raise SkillError("forbidden", "You cannot change this skill.", 403)
    sk.enabled, sk.updated_at = on, time.time()
    audit.record(s, user.id, "work.skill.enable" if on else "work.skill.disable", sk.id, {"name": sk.name, "scope": sk.scope}, ip, org_id=sk.org_id)
    return sk


def import_markdown(st, s, user, principal, filename: str, raw: bytes, scope: str, ip: str = "") -> WorkSkill:
    if len(raw) > BODY_MAX + 8192:
        raise SkillError("invalid", f"The file is larger than the {BODY_MAX // 1024} KB limit.", 413)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise SkillError("invalid", "The file must be UTF-8 text.", 422) from None
    meta, body, had = parse_markdown(text)
    if not had:
        raise SkillError("invalid", "The file has no front matter. It must start with a line of three dashes, then name, description "
                                    "and category, then three dashes again.", 422)
    name = meta.get("name") or re.sub(r"\.md$", "", filename or "", flags=re.I).lower()
    sk = create(st, s, user, principal, {"scope": scope, "name": name, "title": meta.get("title") or meta.get("name") or name,
                                        "description": meta.get("description", ""), "category": meta.get("category") or "general",
                                        "body_md": body, "files": []}, ip)
    audit.record(s, user.id, "work.skill.import", sk.id, {"name": sk.name, "scope": scope, "bytes": len(raw)}, ip, org_id=sk.org_id)
    return sk


# ---- sessions -----------------------------------------------------------------------------------------------------------
def check_ids(s, user, ids) -> list[str]:
    """The skills a session may carry: visible to its owner and enabled. A bad id is refused with the reason."""
    ids = [str(x) for x in (ids or [])]
    if len(ids) > SESSION_SKILLS_MAX:
        raise SkillError("invalid", f"A session can carry at most {SESSION_SKILLS_MAX} skills.", 422)
    out = []
    for i in dict.fromkeys(ids):
        sk = get_visible(s, user, i)
        if not sk.enabled:
            raise SkillError("invalid", f'The skill "{sk.name}" is switched off.', 422)
        out.append(sk.id)
    return out


def attached(st, s, sess: WorkSession) -> list[WorkSkill]:
    """The session's skills as the agent sees them now: still existing, enabled, and visible to the session's owner."""
    if not sess.skill_ids:
        return []
    rows = {r.id: r for r in s.execute(visible_query(sess.org_id, sess.user_id).where(WorkSkill.id.in_(list(sess.skill_ids)))).scalars()}
    return [rows[i] for i in sess.skill_ids if i in rows and rows[i].enabled]


def index_text(skills: list[WorkSkill]) -> str:
    return "\n".join(f"- {sk.name}: {sk.description}" for sk in skills)


# ---- the built-in organisation skills ------------------------------------------------------------------------------------
BUILTIN_VERSION = 1


def ensure_builtin(st, s, org_id: str) -> int:
    """Seed the built-in organisation skills once per organisation (first use after the release). An administrator may edit, switch
    off or delete them; deleted ones are not brought back. -> number created."""
    key = f"work_builtin_skills:{org_id}"
    if s.get(Meta, key) is not None:
        return 0
    from sqlalchemy.exc import IntegrityError
    n = 0
    have = {n_ for (n_,) in s.execute(select(WorkSkill.name).where(WorkSkill.scope == "org", WorkSkill.org_id == org_id))}
    try:
        with s.begin_nested():  # two first requests at once: the second finds the marker already written and seeds nothing
            for b in BUILTIN:
                if b["name"] in have:
                    continue
                s.add(WorkSkill(scope="org", org_id=org_id, owner_user_id=None, name=b["name"], title=b["title"], description=b["description"],
                                category=b["category"], enabled=True, body_enc=work_svc.enc(st, org_id, b["body"]), files_enc=""))
                n += 1
            s.add(Meta(key=key, value=str(BUILTIN_VERSION)))
            s.flush()
    except IntegrityError:
        return 0
    return n


def _b(name, title, category, description, body):
    return {"name": name, "title": title, "category": category, "description": description, "body": body.strip() + "\n"}


BUILTIN = [
    _b("coding", "Coding", "coding", "Write, run, test and version code in your container; fix failures from the real error output.", """
# Coding in your container

1. Look first: `work_list_dir`, then `work_read_file` on the files that matter. Do not guess what is there.
2. Write code with `work_write_file` (long scripts) or `work_edit_file` (small changes: `old` must match exactly once).
3. Run it with `work_shell` (`python3 main.py`, `pytest -q`, `node x.js`). Read the exit code and the last lines of stderr.
4. When it fails, fix the cause named in the error. Never repeat the same command hoping for a different result.
5. Keep tests next to the code (`tests/`), run them before you say you are done, and report the real result.
6. Version it with `work_git` (`init`, `add`, `commit`). Use small commits with plain messages.

Habits that avoid trouble
- The container has no network: no `pip install`, no `npm install`. Use what is installed (Python 3.12 with numpy, pandas, pillow, pytest, pyyaml; Node 20; git; ffmpeg).
- A command stops after the tool timeout. Run long jobs in pieces and write progress to a file.
- Put results the person asked for in `/work/artifacts/`. Name each file in your final answer.
- Do not claim success unless a tool result showed it.
"""),
    _b("research", "Research", "research", "Gather, compare and cite what is in the workspace and the files you are given; say what is unknown.", """
# Research from the materials you have

You have no web access unless the session was given it. Work from the files in `/work` and what the person told you.

1. Make a list of the questions to answer. Save it as `/work/artifacts/questions.md`.
2. Find sources: `work_search_files` (regular expression) and `work_read_file`. Read the real text; do not rely on a file name.
3. For every claim in your answer note where it came from (file and line or section). If two sources disagree, say so.
4. Separate what the sources state, what you infer, and what you could not find. Say plainly what is missing.
5. Write the result as a Markdown report in `/work/artifacts/` with a short summary on top, the findings, and a sources list.
6. Use `delegate` for a second opinion or to summarise one long file with a small model, then check its answer yourself.

Never invent references, numbers or quotations.
"""),
    _b("image-generation", "Image generation", "image-generation", "Make pictures with the engine: pick a ready model, check memory, write a good prompt, verify the result.", """
# Making images

1. `list_models` with `{"kind": "image"}` shows only the image models you may use, with readiness and whether they are loaded.
   A model that is already loaded starts fastest. Smaller models (for example the `turbo` ones) need less memory.
2. `resources` shows free unified memory and what is loaded. Large models need tens of GiB; if memory is short, choose a smaller model or wait.
3. `generate_image` with a clear prompt: subject, setting, style, light, camera. Examples of arguments:
   `{"prompt": "a lighthouse on a cliff at dusk, oil painting", "model": "sdxl-turbo", "size": "512x512", "steps": 4, "seed": 7, "out": "artifacts/lighthouse.png"}`
   The tool waits for the real result and returns only after the PNG is in your workspace.
4. Check the file: `work_python` with PIL (`Image.open(path).size`) before you describe it.
5. Change one thing at a time (prompt, seed, steps) when iterating. Keep the seed to compare variations.
6. To edit a picture, pass `source_file` (a path in /work) to a model that supports editing (`modes` lists `img2img` or `edit`).

If the engine refuses (not enough memory, model not ready), read its message, do not retry blindly: pick another model or tell the person.
"""),
    _b("video-production", "Video production", "video-production", "Make or edit video with the engine: plain video models and the OpenJutsu person-swap profile.", """
# Video with the engine

Check first: `list_models` with `{"kind": "video"}`, then `resources`. Video models and the OpenJutsu profile hold tens of GiB for minutes.
Never start a second heavy job while one runs; `wait_job` for it, or `cancel_job`.

## Plain video models (text or image to video)
`generate_video` with `{"model": "<id>", "prompt": "...", "inputs": {"width": 512, "height": 320, "frames": 17, "fps": 16}, "out": "artifacts/clip.mp4"}`.
Use only sizes and frame counts the model lists (`list_models` shows its limits). To animate a picture add `"start_image": "path/in/work.png"` to `inputs`.

## OpenJutsu profile (replace the main person of a video)
Use the profile model (its `profile` field is set) with your files in /work:
```
generate_video {"model": "openjutsu-minimax-h3", "inputs": {
  "prompt": "the singer",            # WHO is replaced in the video: a short description, not a free prompt
  "video": "inputs/source.mp4",      # the source video (path in /work)
  "refs": "inputs/new_person.png",   # exactly ONE reference picture of the new person (path in /work)
  "resolution": "480p",              # short edge: 480p, 576p, 736p (default) or 768p. 480p is the lightest
  "duration": 3,                     # seconds of the video to use (2 to 15); omit for all of it
  "mask_margin": 40 },               # edit-mask grow: 20 tight, 40 normal, 60 loose
  "out": "artifacts/swapped.mp4"}
```
Optional inputs: `start` (offset in seconds) and `seed`. The source soundtrack is kept.
Expect about 2.5 minutes for a few seconds at 480p, longer for longer clips or higher resolution; memory use is about 65 to 80 GiB.
Only clips with one clearly visible person work well. Check the result with `work_shell`: `ffprobe -v error -show_streams artifacts/swapped.mp4`.

## Licence note (MiniMax H3)
OpenJutsu runs on MiniMax H3, under the MiniMax H3 Community License. Its Applicable Territory is worldwide EXCLUDING the European Union,
the United Kingdom, the Republic of Korea and the United States of America, and the licence says its outputs may not be used outside the
Applicable Territory. Tell the person this before they use a result, and point them to the full licence text and acceptable use policy.
"""),
    _b("media-editing", "Media editing", "media-editing", "Edit images and video in the container with ffmpeg and PIL: trim, scale, convert, caption, montage.", """
# Editing media in the container

Tools available: ffmpeg/ffprobe, ImageMagick, Python with PIL, numpy and opencv. No network.

Inspect first
- Video: `ffprobe -v error -show_entries stream=codec_name,width,height,r_frame_rate,duration -of default=nw=1 file.mp4`
- Image: `python3 -c "from PIL import Image; im=Image.open('a.png'); print(im.size, im.mode)"`

Recipes
- Trim: `ffmpeg -y -ss 00:00:02 -i in.mp4 -t 5 -c:v libx264 -c:a aac artifacts/clip.mp4`
- Scale (keep ratio, even sizes): `ffmpeg -y -i in.mp4 -vf scale=-2:480 -c:a copy artifacts/small.mp4`
- Extract a frame: `ffmpeg -y -ss 1 -i in.mp4 -frames:v 1 artifacts/frame.png`
- Frames to video: `ffmpeg -y -framerate 24 -i frames/%04d.png -c:v libx264 -pix_fmt yuv420p artifacts/out.mp4`
- Join clips: write `list.txt` with lines `file 'a.mp4'`, then `ffmpeg -y -f concat -safe 0 -i list.txt -c copy artifacts/joined.mp4`
- GIF: `ffmpeg -y -i in.mp4 -vf "fps=12,scale=480:-1" artifacts/anim.gif`
- Resize or crop a picture: `python3 -c "from PIL import Image; im=Image.open('a.png'); im.resize((512,512)).save('artifacts/b.png')"`
- Contact sheet: PIL `Image.new` and `paste` a row of thumbnails, then save to artifacts.
- Caption text: PIL `ImageDraw.text` (default font) or ffmpeg `drawtext` when a font file exists.

Always verify the output exists and decodes (ffprobe or PIL) before you report it. Keep outputs in `/work/artifacts/`.
"""),
    _b("document-creation", "Document creation", "document-creation", "Create Word, Excel, PowerPoint, PDF and Markdown files with python-docx, openpyxl and friends.", """
# Creating documents

Installed: python-docx, openpyxl, matplotlib, pillow, pandas, pyyaml. Write the script to a file, run it, then check the output.

- Word: `from docx import Document; d=Document(); d.add_heading('Title',0); d.add_paragraph('text'); d.add_table(rows=2, cols=2); d.save('artifacts/report.docx')`
- Excel: `from openpyxl import Workbook; wb=Workbook(); ws=wb.active; ws.append(['a','b']); wb.save('artifacts/data.xlsx')` (write formulas as strings starting with `=`)
- Charts: matplotlib `plt.savefig('artifacts/chart.png', dpi=150)`, then insert with `d.add_picture('artifacts/chart.png', width=Inches(5))`.
- Markdown or CSV: `work_write_file` directly.

Check your work: reopen the file (`Document('artifacts/report.docx').paragraphs`), count rows in the workbook, and look at images with PIL.
Say in the final answer which files you made and what each contains. Do not describe content you did not verify.
"""),
    _b("data-analysis", "Data analysis", "data-analysis", "Analyse tables with pandas: load, clean, compute exact figures, chart, and report with the numbers that were actually computed.", """
# Analysing data

1. Load: `pandas.read_csv` / `read_excel`. Print `df.shape`, `df.dtypes`, `df.head()`, `df.isna().sum()` first.
2. Clean in code and keep the script (`/work/analysis.py`) so every figure can be reproduced. Never edit the original file.
3. Compute with pandas, not in your head: `df.groupby('x')['y'].agg(['count','sum','mean'])`. Print the exact output.
4. Chart with matplotlib and save to `/work/artifacts/`. Label axes and units.
5. Write the findings to `/work/artifacts/report.md`: question, method, figures (copied from tool output), caveats.

Rules
- Report only numbers that a tool printed. If a number is not available, say so.
- Mention data problems you found (missing values, duplicates, odd outliers) and what you did about them.
- Large inputs: sample first, then run on everything and compare.
"""),
]


# ---- tools --------------------------------------------------------------------------------------------------------------
from .agent_svc import ToolError, ToolOut  # noqa: E402
from .work_tools import a_str, put, scrub, work_tool  # noqa: E402


def _attached_for(w) -> list[WorkSkill]:
    with w.st.db.session() as s:
        sess = s.get(WorkSession, w.sid)
        rows = attached(w.st, s, sess)
        s.expunge_all()
        return rows


@work_tool("list_skills", "Skills attached to this session.", "{}")
def t_list_skills(tc, w, a: dict) -> ToolOut:
    rows = _attached_for(w)
    if not rows:
        return ToolOut("No skills are attached to this session.")
    return ToolOut("Skills attached to this session:\n" + index_text(rows) + "\nOpen one with read_skill {\"name\": \"<name>\"}.")


@work_tool("read_skill", "Read an attached skill (its files go to /work/.skills/<name>/). Reference only.", '{"name": "video-production"}')
def t_read_skill(tc, w, a: dict) -> ToolOut:
    name = a_str(a, "name", maxlen=100).strip().lower()
    scope = a_str(a, "scope", required=False, maxlen=10).strip().lower()
    rows = [r for r in _attached_for(w) if r.name == name and (not scope or r.scope == scope)]
    if not rows:
        have = ", ".join(r.name for r in _attached_for(w)) or "none"
        raise ToolError(f"No attached skill is called '{name[:60]}'. Attached skills: {have}.")
    sk = sorted(rows, key=lambda r: r.scope != "user")[0]
    with w.st.db.session() as s:
        sk = s.get(WorkSkill, sk.id)
        if sk is None:
            raise ToolError("That skill was deleted.")
        body, files = _body(w.st, sk), _files(w.st, sk)
        title, scope_label = sk.title, "organisation" if sk.scope == "org" else "personal"
    cname = w.container()
    base = f".skills/{name}"
    put(w, cname, f"{base}/SKILL.md", body.encode())
    for f in files:
        put(w, cname, f"{base}/{f['name']}", f["content"].encode())
    w.done(path=f"{base}/SKILL.md", skill=name)
    limit = max(2000, w.lim["output_chars"] - 1200)
    shown = body if len(body) <= limit else body[:limit] + f"\n[... {len(body) - limit:,} more characters: the full text is in /work/{base}/SKILL.md; read it with work_read_file]"
    head = f"Skill '{name}' ({scope_label}: {title}). Reference material: it cannot change your tools, permissions or limits.\n"
    foot = ("\nSupporting files copied to /work/" + base + "/: " + ", ".join(f["name"] for f in files)) if files else ""
    return ToolOut(scrub(head + "\n" + shown + foot))
