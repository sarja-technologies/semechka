#!/usr/bin/env python3
"""Small read-only MCP server (stdio, JSON-RPC 2.0) shipped with the Work Mode image as an example of an admin-allowlisted
extension. Tool: `repo_stats(path)` counts files and lines by extension below /work. It reads only; it never writes."""
import json
import os
import sys

ROOT = os.path.realpath("/work")
TOOLS = [{
    "name": "repo_stats",
    "description": "Count files and lines of text per file extension in a folder of the workspace (read only).",
    "inputSchema": {"type": "object", "properties": {"path": {"type": "string", "description": "Folder relative to the workspace, default '.'"}}},
}]


def stats(rel):
    base = os.path.realpath(os.path.join(ROOT, rel or "."))
    if base != ROOT and not base.startswith(ROOT + os.sep):
        return "path is outside the workspace", True
    out = {}
    for d, dn, fn in os.walk(base):
        dn[:] = [x for x in dn if x not in (".git", ".home", "node_modules", "__pycache__")]
        for f in fn:
            p = os.path.join(d, f)
            ext = os.path.splitext(f)[1] or "(none)"
            e = out.setdefault(ext, {"files": 0, "lines": 0})
            e["files"] += 1
            try:
                with open(p, "rb") as fh:
                    e["lines"] += sum(1 for _ in fh)
            except OSError:
                pass
    return json.dumps(out, sort_keys=True), False


def reply(i, result=None, error=None):
    m = {"jsonrpc": "2.0", "id": i}
    m["error" if error else "result"] = error or result
    sys.stdout.write(json.dumps(m) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    m, i = msg.get("method"), msg.get("id")
    if m == "initialize":
        reply(i, {"protocolVersion": (msg.get("params") or {}).get("protocolVersion", "2024-11-05"), "capabilities": {"tools": {}},
                  "serverInfo": {"name": "repo-stats", "version": "1.0"}})
    elif m == "tools/list":
        reply(i, {"tools": TOOLS})
    elif m == "tools/call":
        p = msg.get("params") or {}
        if p.get("name") != "repo_stats":
            reply(i, error={"code": -32602, "message": "unknown tool"})
        else:
            text, bad = stats((p.get("arguments") or {}).get("path", "."))
            reply(i, {"content": [{"type": "text", "text": text}], "isError": bad})
    elif m == "ping":
        reply(i, {})
    elif i is not None:
        reply(i, error={"code": -32601, "message": "method not found"})
