"""Work Mode tools: registered in `agent_svc.TOOLS` like every other tool, run on the HOST, reach the session's container only
through work_runtime (docs/WORK_MODE.md, section 4).

Names carry the `work_` prefix because `shell`, `python` and `read_file` already exist as cloud-agent tools (and mean
something different there: workspace files, per-call sandbox).

Contract of every tool here
  * arguments are validated against the schema in the tool's declaration; a bad call returns an error that says what was
    expected (the model reads it);
  * no database transaction is held while the container works;
  * a result either reports what the container really did (exit code, duration, truncation, kill reason) or a real error;
  * results never contain environment variables, tokens or host paths (`scrub`).

Seam for phase B (engine tools, delegation, skills): add tools with `@work_tool(...)` below or in a new module imported at
the bottom of work_svc.py, and add the name to WORK_TOOLS. A tool receives `(tc, w, a)`: `w` is a `Wk` (session id, container
name, effective limits, step number, run id) and `w.container()` returns a running container name. To make files appear in
the workspace use `put(w, path, data)`; to make them downloadable call `register_artifact(...)`.
"""
from __future__ import annotations

import functools
import hashlib
import json
import logging
import mimetypes
import re
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from . import agent_svc, work_runtime, work_svc
from .agent_svc import ToolError, ToolOut
from .models_work import WorkArtifact, WorkSession
from .tenancy_svc import TenancyError

log = logging.getLogger(__name__)
MB = 1048576
MAX_WRITE_CHARS = 1_000_000
MAX_READ_BYTES = 3 * MB
MAX_ARTIFACTS = 200
WORK_TOOLS = ["work_shell", "work_python", "work_read_file", "work_write_file", "work_edit_file", "work_list_dir", "work_search_files",
              "work_git", "work_publish_artifact", "work_note"]

_HOST_PATH = re.compile(r"""(?<![\w.])(/home/|/run/user/|/var/lib/|/root/|/etc/docker|/usr/lib/docker|/proc/self)[^\s:;,'")]*""")


def scrub(text: str) -> str:
    """Nothing about the host leaves through a tool result."""
    return _HOST_PATH.sub("[host path]", text or "")


# ---- argument validation ---------------------------------------------------------------------------------------
def a_str(a: dict, key: str, *, required: bool = True, default: str = "", maxlen: int = 20000) -> str:
    v = a.get(key, None)
    if v is None:
        if required:
            raise ToolError(f"'{key}' is required.")
        return default
    if not isinstance(v, str):
        raise ToolError(f"'{key}' must be a string.")
    if len(v) > maxlen:
        raise ToolError(f"'{key}' is too long ({len(v):,} characters; at most {maxlen:,}).")
    if required and not v.strip():
        raise ToolError(f"'{key}' must not be empty.")
    return v


def a_int(a: dict, key: str, default: int, lo: int, hi: int) -> int:
    v = a.get(key, None)
    if v is None:
        return default
    if isinstance(v, bool) or not isinstance(v, (int, float, str)):
        raise ToolError(f"'{key}' must be a whole number.")
    try:
        n = int(float(v))
    except ValueError:
        raise ToolError(f"'{key}' must be a whole number.") from None
    return max(lo, min(n, hi))


def a_bool(a: dict, key: str, default: bool = False) -> bool:
    v = a.get(key, default)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes")
    return bool(v)


def norm_path(p: str, *, allow_empty: bool = True) -> str:
    """-> a path relative to /work. Absolute paths must start with /work; the helper then resolves them in the container
    and refuses anything that ends up outside (.., symlinks). Nothing here is ever used as a host path."""
    p = (p or "").strip()
    if "\x00" in p:
        raise ToolError("Invalid path.")
    if p == "~" or p.startswith("~/"):
        p = ".home" + p[1:]
    if p.startswith("/"):
        if p == "/work" or p.startswith("/work/"):
            p = p[len("/work"):]
        else:
            raise ToolError("Paths must be inside /work (use /work/... or a relative path).")
    p = p.lstrip("/")
    if not p and not allow_empty:
        raise ToolError("A file path is required.")
    return p


# ---- the per-call context -----------------------------------------------------------------------------------
@dataclass
class Wk:
    st: object
    sid: str
    name: str
    lim: dict
    run_id: str
    org_id: str
    user_id: str
    step_n: int | None
    network: str
    tc: object
    result: dict = field(default_factory=dict)
    new_artifacts: list = field(default_factory=list)  # artifacts this call created or changed (reported to the run)

    @property
    def rt(self) -> work_runtime.WorkRuntime:
        return work_svc.runtime(self.st)

    def container(self) -> str:
        return work_svc.ensure_running(self.st, self.sid)

    def done(self, **kw) -> None:
        self.result.update(kw)


def work_tool(name: str, summary: str, args: str, *, mutating: bool = False):
    def deco(fn):
        @agent_svc.tool(name, summary, args, [], sensitive=False, mutating=mutating)
        @functools.wraps(fn)
        def run(tc, a):
            sess = getattr(tc, "work_session", None) or tc.s.get(WorkSession, tc.run.session_id)
            if sess is None or sess.deleted_at:
                raise ToolError("The session no longer exists.")
            lim = work_svc.effective(work_svc.cfg(tc.st, tc.s), sess)
            w = Wk(tc.st, sess.id, sess.container_name, lim, tc.run.id, sess.org_id, tc.run.user_id,
                   tc.step.n if tc.step is not None else None, sess.network, tc)
            tc.s.rollback()  # no transaction is open while the container works; ORM objects reload on next use
            try:
                try:
                    out = fn(tc, w, a if isinstance(a, dict) else {})
                except (TenancyError, work_runtime.RuntimeError_) as e:
                    raise ToolError(scrub(getattr(e, "message", None) or str(e))) from None
            finally:
                tc.s.rollback()
            tc.work_result = w.result
            return out
        return run
    return deco


# ---- workspace size -----------------------------------------------------------------------------------------
def used_bytes(w: Wk, name: str, *, fresh: bool = False) -> int:
    cache = w.st.__dict__.setdefault("work_usage", {})
    hit = cache.get(w.sid)
    if hit and not fresh and time.time() - hit[0] < 3:
        return hit[1]
    n = w.rt.usage_bytes(name)
    cache[w.sid] = (time.time(), n)
    return n


def check_quota(w: Wk, name: str, extra: int = 0, *, cleanup: bool = False) -> int:
    used = used_bytes(w, name)
    limit = w.lim["workspace_mb"] * MB
    if used + extra > limit and not (cleanup and extra == 0):
        over = f" This needs {extra / MB:.1f} MB more." if extra else ""
        raise ToolError(f"The workspace is full: {used / MB:.1f} MB used of {w.lim['workspace_mb']} MB.{over} "
                        "Delete files you no longer need (for example work_shell: rm -r path) and try again.")
    return used


CLEANUP_RE = re.compile(r"^\s*(rm|du|ls|df|find|git\s+(gc|clean))\b")


def _bump(w: Wk, delta: int) -> None:
    cache = w.st.__dict__.setdefault("work_usage", {})
    hit = cache.get(w.sid)
    if hit:
        cache[w.sid] = (hit[0], hit[1] + delta)


# ---- command results ----------------------------------------------------------------------------------------
def run_command(w: Wk, name: str, *, cmd: str | None = None, argv: list[str] | None = None, timeout_s: int, cwd: str = "",
                cleanup_ok: bool = False) -> ToolOut:
    limit = w.lim["workspace_mb"] * MB
    over = check_quota(w, name, 0, cleanup=cleanup_ok)  # raises unless the command is a cleanup and we are over
    # both streams plus the report lines must fit in what the model is shown, so each stream gets half (head and tail kept)
    res = w.rt.exec(name, cmd, argv=argv, timeout_s=timeout_s, max_output=max(500, (w.lim["output_chars"] - 700) // 2), cwd=cwd,
                    quota_bytes=int(limit * 1.05) if limit else 0)
    after = used_bytes(w, name, fresh=True)
    w.done(exit=res.exit, duration_s=res.duration_s, timed_out=res.timed_out)
    lines = [f"exit code {res.exit}" + (f" ({res.signal})" if res.signal else "") + f" in {res.duration_s:.2f}s"]
    if res.timed_out:
        lines.append(f"TIMED OUT after {timeout_s}s: the command and everything it started were killed.")
    if res.quota_killed:
        lines.append(f"STOPPED: the workspace grew past its {w.lim['workspace_mb']} MB limit while the command ran "
                     f"({after / MB:.1f} MB used). Delete files before running more.")
    if res.oom_killed or (res.signal == "SIGKILL" and not res.timed_out and not res.interrupted and not res.quota_killed):
        lines.append(f"KILLED: out of memory (the container is limited to {w.lim['memory_mb']} MB) or killed by the system.")
    if res.interrupted:
        lines.append("INTERRUPTED: the run was cancelled and the command was stopped.")
    if res.stdout.strip():
        lines.append("stdout:\n" + res.stdout.rstrip())
    if res.truncated_stdout:
        lines.append(f"[stdout: {res.truncated_stdout:,} bytes in the middle were left out]")
    if res.stderr.strip():
        lines.append("stderr:\n" + res.stderr.rstrip())
    if res.truncated_stderr:
        lines.append(f"[stderr: {res.truncated_stderr:,} bytes in the middle were left out]")
    if not res.stdout.strip() and not res.stderr.strip():
        lines.append("(no output)")
    if after > limit:
        lines.append(f"WARNING: the workspace is over its limit ({after / MB:.1f} of {w.lim['workspace_mb']} MB); writes are refused until you delete files.")
    note = sync_artifacts(w, name)
    if note:
        lines.append(note)
    return ToolOut(scrub("\n".join(lines)), artifacts=w.new_artifacts)


# ---- artifacts ----------------------------------------------------------------------------------------------
MIME = {".md": "text/markdown", ".csv": "text/csv", ".py": "text/x-python", ".json": "application/json", ".txt": "text/plain",
        ".log": "text/plain", ".yaml": "text/yaml", ".yml": "text/yaml", ".js": "text/javascript", ".ts": "text/plain",
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
        ".mp4": "video/mp4", ".webm": "video/webm", ".svg": "image/svg+xml", ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation"}


def guess_mime(name: str) -> str:
    ext = "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""
    return MIME.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def register_artifact(w: Wk, name: str, rel: str, *, title: str | None = None) -> tuple[dict, bool]:
    """Copy a workspace file out of the container into Semechka's encrypted store (so it outlives the container) and list it.
    -> (artifact, changed). The same path with new content updates the same artifact."""
    limit = w.lim["artifact_mb"] * MB
    st_ = w.rt.stat(name, rel)
    if not st_.get("exists"):
        raise ToolError(f"No such file: /work/{rel}")
    if st_.get("type") != "file":
        raise ToolError(f"/work/{rel} is not a regular file.")
    if st_["size"] > limit:
        raise ToolError(f"/work/{rel} is {st_['size'] / MB:.1f} MB; artifacts can be at most {w.lim['artifact_mb']} MB.")
    data, head = w.rt.get_file(name, rel, limit=limit + 1)
    sha = hashlib.sha256(data).hexdigest()
    base = rel.rsplit("/", 1)[-1]
    with w.st.db.session(write=True) as s:
        sess = s.get(WorkSession, w.sid)
        if sess is None:
            raise ToolError("The session no longer exists.")
        art = s.execute(select(WorkArtifact).where(WorkArtifact.session_id == w.sid, WorkArtifact.path == rel)).scalar_one_or_none()
        if art is not None and art.sha256 == sha:
            art.mtime = head.get("mtime", art.mtime)
            return work_svc.artifact_out(art), False
        if art is None:
            n = s.query(WorkArtifact).filter(WorkArtifact.session_id == w.sid).count()
            if n >= MAX_ARTIFACTS:
                raise ToolError(f"This session already lists {MAX_ARTIFACTS} artifacts.")
            art = WorkArtifact(session_id=w.sid, path=rel, name=(title or base)[:200], created_by_step=w.step_n)
            s.add(art)
            s.flush()
            fresh = True
        else:
            fresh = False
            if title:
                art.name = title[:200]
        art.mime, art.size, art.sha256, art.mtime, art.updated_at = guess_mime(base), len(data), sha, head.get("mtime", 0), time.time()
        art.stored_asset = work_svc.store_artifact_bytes(w.st, sess.user_id, art.id, data)
        art.saved_to_library = False if not fresh else art.saved_to_library
        out = work_svc.artifact_out(art)
        work_svc.emit(w.st, s, sess, "artifact", ("New artifact: " if fresh else "Updated artifact: ") + art.name, out, run_id=w.run_id, step_n=w.step_n)
    return out, True


def sync_artifacts(w: Wk, name: str) -> str:
    """Files under /work/artifacts/ are listed automatically. Never fails the tool that triggered it."""
    try:
        found = w.rt.search(name, "", "artifacts", names_only=True, max_results=MAX_ARTIFACTS)["matches"]
        found = [f for f in found if not f["path"].rsplit("/", 1)[-1].startswith(".")]
        if not found:
            return ""
        with w.st.db.session() as s:
            have = {a.path: (a.size, a.mtime) for a in s.execute(select(WorkArtifact).where(WorkArtifact.session_id == w.sid)).scalars()}
        new = []
        for f in found:
            old = have.get(f["path"])
            if old is None or old[0] != f["size"] or abs(old[1] - f["mtime"]) > 0.001:
                art, changed = register_artifact(w, name, f["path"])
                if changed:
                    new.append(art["path"])
                    w.new_artifacts.append({"file_id": art["id"], "name": art["name"], "version": 1})
        return ("Listed as artifacts: " + ", ".join("/work/" + p for p in new)) if new else ""
    except (ToolError, work_runtime.RuntimeError_, TenancyError) as e:
        return f"(Could not list artifacts: {scrub(str(getattr(e, 'message', None) or e))})"


def put(w: Wk, name: str, rel: str, data: bytes) -> dict:
    check_quota(w, name, len(data))
    head = w.rt.put_file(name, rel, data)
    _bump(w, len(data))
    return head


# ---- tools --------------------------------------------------------------------------------------------------
@work_tool("work_shell", "Run a shell command (bash) in your container, in /work. Output, exit code and duration are reported.",
           '{"cmd": "ls -la", "timeout_s": 120, "cwd": ""}', mutating=True)
def t_shell(tc, w: Wk, a: dict) -> ToolOut:
    cmd = a_str(a, "cmd", maxlen=20000)
    t = a_int(a, "timeout_s", min(120, w.lim["tool_timeout_s"]), 1, w.lim["tool_timeout_s"])
    return run_command(w, w.container(), cmd=cmd, timeout_s=t, cwd=norm_path(a_str(a, "cwd", required=False, maxlen=500)),
                       cleanup_ok=bool(CLEANUP_RE.match(cmd)))


@work_tool("work_python", "Run Python 3 code in your container (cwd /work). Prints are returned; write files to keep results.",
           '{"code": "print(2+2)", "timeout_s": 120}', mutating=True)
def t_python(tc, w: Wk, a: dict) -> ToolOut:
    code = a_str(a, "code", maxlen=60000)
    t = a_int(a, "timeout_s", min(120, w.lim["tool_timeout_s"]), 1, w.lim["tool_timeout_s"])
    return run_command(w, w.container(), argv=["python3", "-u", "-c", code], timeout_s=t)


def _read_text(w: Wk, name: str, rel: str, limit: int = MAX_READ_BYTES) -> tuple[str, dict]:
    data, head = w.rt.get_file(name, rel, limit=limit)
    if head.get("binary"):
        raise ToolError(f"/work/{rel} looks like a binary file ({head['size']:,} bytes); inspect it with work_python or work_shell (file, xxd, identify...).")
    return data.decode("utf-8", "replace"), head


@work_tool("work_read_file", "Read a text file (numbered lines). Use offset/limit to page through long files.",
           '{"path": "main.py", "offset": 1, "limit": 300}')
def t_read(tc, w: Wk, a: dict) -> ToolOut:
    rel = norm_path(a_str(a, "path", maxlen=1000), allow_empty=False)
    off = a_int(a, "offset", 1, 1, 10_000_000)
    lim = a_int(a, "limit", 300, 1, 2000)
    text, head = _read_text(w, w.container(), rel)
    lines = text.splitlines()
    cut = head["size"] > MAX_READ_BYTES
    page = lines[off - 1:off - 1 + lim]
    if not page and lines:
        raise ToolError(f"offset {off} is past the end ({len(lines)} lines).")
    body = "\n".join(f"{off + i:>6}\t{ln[:2000]}" for i, ln in enumerate(page))
    last = off - 1 + len(page)
    foot = f"\n[lines {off}-{last}" + (f" of {len(lines)}" if not cut else "") + (f"; the file is {head['size']:,} bytes and only the first {MAX_READ_BYTES // MB} MB were read" if cut else "") + "]"
    w.done(path=rel, bytes=head["size"])
    return ToolOut(scrub(body + foot if page else f"(empty file: /work/{rel})"))


@work_tool("work_write_file", "Create or overwrite a text file (parent folders are created). For long code prefer this over inline shell.",
           '{"path": "main.py", "content": "print(1)\\n"}', mutating=True)
def t_write(tc, w: Wk, a: dict) -> ToolOut:
    rel = norm_path(a_str(a, "path", maxlen=1000), allow_empty=False)
    content = a_str(a, "content", maxlen=MAX_WRITE_CHARS, required=False)
    if "content" not in a:
        raise ToolError("'content' is required.")
    data = content.encode()
    name = w.container()
    head = put(w, name, rel, data)
    w.done(path=rel, bytes=len(data), files=[rel])
    note = sync_artifacts(w, name)
    return ToolOut(f"Wrote /work/{rel} ({len(data):,} bytes, sha256 {head['sha256'][:12]})." + (f"\n{note}" if note else ""),
                   artifacts=w.new_artifacts)


@work_tool("work_edit_file", "Replace text in a file. 'old' must match exactly once unless replace_all is true.",
           '{"path": "main.py", "old": "x = 1", "new": "x = 2", "replace_all": false}', mutating=True)
def t_edit(tc, w: Wk, a: dict) -> ToolOut:
    rel = norm_path(a_str(a, "path", maxlen=1000), allow_empty=False)
    old = a_str(a, "old", maxlen=200000)
    new = a_str(a, "new", required=False, maxlen=200000)
    name = w.container()
    text, head = _read_text(w, name, rel, limit=4 * MB)
    if head["size"] > 4 * MB:
        raise ToolError("That file is larger than 4 MB; edit it with work_python or work_shell.")
    n = text.count(old)
    if n == 0:
        raise ToolError("The text to replace was not found (it must match exactly, including whitespace). Read the file again with work_read_file.")
    if n > 1 and not a_bool(a, "replace_all"):
        raise ToolError(f"The text to replace occurs {n} times. Add surrounding context to make it unique, or set replace_all to true.")
    out = text.replace(old, new)
    put(w, name, rel, out.encode())
    w.done(path=rel, bytes=len(out.encode()), files=[rel])
    note = sync_artifacts(w, name)
    return ToolOut(f"Replaced {n} occurrence{'s' if n != 1 else ''} in /work/{rel}." + (f"\n{note}" if note else ""), artifacts=w.new_artifacts)


@work_tool("work_list_dir", "List a folder (default /work).", '{"path": ""}')
def t_list(tc, w: Wk, a: dict) -> ToolOut:
    rel = norm_path(a_str(a, "path", required=False, maxlen=1000))
    ents = w.rt.list(w.container(), rel)
    lines = [f"{'dir ' if e.type == 'dir' else ('link' if e.type == 'link' else 'file')}  {e.name}{'/' if e.type == 'dir' else ''}"
             + (f"  {e.size:,} bytes" if e.type == 'file' else "") + ("  (link leaves /work)" if not e.link_ok else "") for e in ents[:300]]
    more = f"\n[{len(ents) - 300} more entries not shown]" if len(ents) > 300 else ""
    return ToolOut(scrub((f"/work/{rel}".rstrip("/") + ":\n" if rel else "/work:\n") + ("\n".join(lines) or "(empty)") + more))


@work_tool("work_search_files", "Search file contents with a regular expression (or, with names_only, file names). Skips .git, node_modules, binaries.",
           '{"pattern": "def main", "path": "", "glob": "*.py", "names_only": false}')
def t_search(tc, w: Wk, a: dict) -> ToolOut:
    pat = a_str(a, "pattern", required=False, maxlen=500)
    names_only = a_bool(a, "names_only")
    if not pat and not names_only:
        raise ToolError("'pattern' is required (a regular expression).")
    r = w.rt.search(w.container(), pat, norm_path(a_str(a, "path", required=False, maxlen=1000)), glob=a_str(a, "glob", required=False, maxlen=100),
                    names_only=names_only, max_results=100)
    if not r["matches"]:
        return ToolOut("No matches." + f" ({r['scanned']} files searched)" if not names_only else "No matching files.")
    lines = [m["path"] if names_only else f"{m['path']}:{m['line']}: {m['text']}" for m in r["matches"]]
    return ToolOut(scrub("\n".join(lines) + ("\n[more matches exist; narrow the search]" if r["truncated"] else "")))


GIT_OK = {"init", "status", "diff", "log", "add", "commit", "branch", "checkout", "switch", "restore", "show", "tag", "stash", "merge",
          "rebase", "rm", "mv", "reset", "blame", "shortlog", "ls-files", "cherry-pick", "revert", "config"}
GIT_NET = {"clone", "fetch", "pull", "push", "remote"}
GIT_BAD = re.compile(r"^(-c|-C|--git-dir|--work-tree|--exec-path|--exec|-x|--upload-pack|--receive-pack|--no-index|--template|"
                     r"--namespace|--super-prefix|--config-env|--open-files-in-pager|--output|-o)\b|^--exec=|core\.hooksPath|core\.sshCommand|"
                     r"core\.fsmonitor|core\.pager|credential\.helper|alias\.|\.hooksPath|^!")


@work_tool("work_git", "Run git (init, status, diff, log, add, commit -m, branch, checkout/switch, restore, show, tag, stash, merge, rebase, "
           "reset...). Hooks are disabled. clone/fetch/pull/push/remote need network access.",
           '{"args": ["commit", "-m", "message"], "cwd": ""}', mutating=True)
def t_git(tc, w: Wk, a: dict) -> ToolOut:
    args = a.get("args")
    if isinstance(args, str):
        import shlex
        try:
            args = shlex.split(args)
        except ValueError:
            raise ToolError("Could not split 'args'; pass a list of strings.") from None
    if not isinstance(args, list) or not args or not all(isinstance(x, str) and 0 < len(x) <= 2000 for x in args) or len(args) > 60:
        raise ToolError('args must be a list of up to 60 strings, for example ["status"].')
    sub = args[0]
    if sub in GIT_NET:
        if w.network == "none":
            raise ToolError(f"git {sub} needs network access, and this session has none. Work with local repositories only.")
    elif sub not in GIT_OK:
        raise ToolError(f"'git {sub}' is not allowed. Allowed: {', '.join(sorted(GIT_OK))}" + (f"; with network: {', '.join(sorted(GIT_NET))}." if w.network != "none" else "."))
    for x in args:
        if GIT_BAD.search(x):
            raise ToolError(f"The git option '{x[:40]}' is not allowed.")
    if sub == "rebase" and any(x.startswith("--exec") or x == "-x" for x in args):
        raise ToolError("git rebase --exec is not allowed.")
    if sub == "config" and not any(x in ("--get", "--list", "-l", "--get-all") for x in args) and args[1:2] and not args[1].startswith("user."):
        raise ToolError("git config may only read settings, or set user.name / user.email.")
    return run_command(w, w.container(), argv=["git", *args], timeout_s=min(120, w.lim["tool_timeout_s"]),
                       cwd=norm_path(a_str(a, "cwd", required=False, maxlen=500)))


@work_tool("work_publish_artifact", "Make a workspace file available to the person as a download (files in /work/artifacts/ are listed automatically).",
           '{"path": "artifacts/report.md", "title": "Report"}')
def t_publish(tc, w: Wk, a: dict) -> ToolOut:
    rel = norm_path(a_str(a, "path", maxlen=1000), allow_empty=False)
    art, changed = register_artifact(w, w.container(), rel, title=a_str(a, "title", required=False, maxlen=200) or None)
    w.done(path=rel, bytes=art["size"], files=[rel])
    return ToolOut(f"{'Published' if changed else 'Already published (unchanged)'}: /work/{rel} as artifact {art['id']} ({art['size']:,} bytes, {art['mime']}).",
                   artifacts=[{"file_id": art["id"], "name": art["name"], "version": 1}])


@work_tool("work_note", "Save a short note that stays in front of you for the rest of the session (decisions, where things are, what is left).",
           '{"text": "main.py parses the CSV; tests are in tests/"}')
def t_note(tc, w: Wk, a: dict) -> ToolOut:
    text = a_str(a, "text", maxlen=300).strip()
    w.done(note=text)
    return ToolOut("Noted.")
