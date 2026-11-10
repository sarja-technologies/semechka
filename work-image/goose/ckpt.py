"""Workspace checkpoints for Work Mode (runs inside the session container; plain stdlib; no model, no network).

  ckpt.py snap <id>          record every file below /work (path, size, sha1); keep a copy of small text files in a
                             content-addressed store, so a later diff can show what they said before
  ckpt.py diff <idA> <idB>   print JSON {files: [{path,status,additions,deletions,binary}], diff, truncated}
Paths below .home, .git, node_modules, virtualenvs and caches are not part of a checkpoint.
"""
import difflib
import hashlib
import json
import os
import sys

ROOT = "/work"
STORE = "/work/.home/snap"
SKIP = {".home", ".git", "node_modules", "__pycache__", ".venv", "venv", ".cache", ".npm", ".pytest_cache", ".mypy_cache", ".tox"}
MAX_FILES, MAX_COPY, MAX_DIFF = 20000, 512 * 1024, 512 * 1024


def is_text(b):
    return b"\0" not in b[:4096]


def snap(sid):
    os.makedirs(STORE + "/objects", exist_ok=True)
    man, n = {}, 0
    for d, dn, fn in os.walk(ROOT):
        dn[:] = sorted(x for x in dn if x not in SKIP)
        for f in sorted(fn):
            p = os.path.join(d, f)
            if os.path.islink(p) or n >= MAX_FILES:
                continue
            try:
                st = os.stat(p)
                with open(p, "rb") as fh:
                    data = fh.read(MAX_COPY + 1) if st.st_size > MAX_COPY else fh.read()
                    h = hashlib.sha1(data) if st.st_size <= MAX_COPY else hashlib.sha1(data)
                    if st.st_size > MAX_COPY:
                        for chunk in iter(lambda: fh.read(1 << 20), b""):
                            h.update(chunk)
            except OSError:
                continue
            n += 1
            sha = h.hexdigest()
            kept = st.st_size <= MAX_COPY and is_text(data)
            if kept:
                o = f"{STORE}/objects/{sha}"
                if not os.path.exists(o):
                    with open(o, "wb") as w:
                        w.write(data)
            man[os.path.relpath(p, ROOT)] = [sha, st.st_size, 1 if kept else 0]
    with open(f"{STORE}/{sid}.json", "w") as w:
        json.dump(man, w)
    old = sorted((os.path.getmtime(f"{STORE}/{x}"), x) for x in os.listdir(STORE) if x.endswith(".json"))
    for _, x in old[:-60]:
        os.unlink(f"{STORE}/{x}")
    print(json.dumps({"id": sid, "files": len(man)}))


def body(sha, kept):
    if not kept:
        return None
    try:
        with open(f"{STORE}/objects/{sha}", "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines(keepends=True)
    except OSError:
        return None


def diff(a, b):
    A = json.load(open(f"{STORE}/{a}.json"))
    B = json.load(open(f"{STORE}/{b}.json"))
    files, out, size, trunc = [], [], 0, False
    for path in sorted(set(A) | set(B)):
        x, y = A.get(path), B.get(path)
        if x and y and x[0] == y[0]:
            continue
        status = "added" if not x else "deleted" if not y else "modified"
        la = body(*x[0::2]) if x else []
        lb = body(*y[0::2]) if y else []
        binary = (x and not x[2]) or (y and not y[2])
        add = dele = 0
        if not binary and la is not None and lb is not None:
            lines = list(difflib.unified_diff(la or [], lb or [], f"a/{path}" if x else "/dev/null", f"b/{path}" if y else "/dev/null"))
            add = sum(1 for l in lines if l.startswith("+") and not l.startswith("+++"))
            dele = sum(1 for l in lines if l.startswith("-") and not l.startswith("---"))
            text = "".join(l if l.endswith("\n") else l + "\n\\ No newline at end of file\n" for l in lines)
            if size + len(text) > MAX_DIFF:
                trunc = True
            else:
                size += len(text)
                out.append(text)
        else:
            binary = True
        files.append({"path": path, "status": status, "additions": add, "deletions": dele, "binary": bool(binary)})
    print(json.dumps({"files": files[:500], "diff": "".join(out), "truncated": trunc or len(files) > 500}))


if __name__ == "__main__":
    if sys.argv[1] == "snap":
        snap(sys.argv[2])
    else:
        diff(sys.argv[2], sys.argv[3])
