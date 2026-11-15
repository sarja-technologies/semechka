"""TEST DOUBLE of the ACP *peer* (what `goose acp` is on the other end of the pipe). It exists so the tests can exercise the relay,
approvals, cancel and resume logic of goose_svc without GPUs. It is NOT an agent and is never used outside the tests; the real
Goose is exercised by the Spark end-to-end run (docs/WORK_MODE_GOOSE_SECURITY.md, section 5).
Usage: fake_goose.py <state_dir> <work_dir>   (prompt keywords: tool, edit, slow, crash, think)"""
import json
import os
import sys
import threading
import time

state_dir, work = sys.argv[1], sys.argv[2]
mode = os.environ.get("FAKE_MODE", "approve")
out_lock = threading.Lock()
waiters, cancel = {}, threading.Event()
n_req = [0]


def send(o):
    with out_lock:
        sys.stdout.write(json.dumps(o) + "\n")
        sys.stdout.flush()


def upd(sid, u):
    send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})


def ask(sid, tcid, title, raw):
    n_req[0] += 1
    rid = 9000 + n_req[0]
    ev = threading.Event()
    waiters[rid] = [ev, None]
    send({"jsonrpc": "2.0", "id": rid, "method": "session/request_permission", "params": {
        "sessionId": sid, "toolCall": {"toolCallId": tcid, "kind": "other", "status": "pending", "title": title, "rawInput": raw},
        "options": [{"optionId": k, "name": k, "kind": k} for k in ("allow_always", "allow_once", "reject_once", "reject_always")]}})
    ev.wait(30)
    return (waiters[rid][1] or {}).get("outcome", {})


def tool(sid, name, tcid, raw, action):
    upd(sid, {"sessionUpdate": "tool_call", "toolCallId": tcid, "title": f"{name} · x", "rawInput": raw,
              "_meta": {"goose": {"toolCall": {"toolName": name, "extensionName": "developer"}}}})
    if mode != "auto":
        o = ask(sid, tcid, f"{name} · x", raw)
        if o.get("outcome") != "selected" or not o.get("optionId", "").startswith("allow"):
            upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tcid, "status": "failed",
                      "content": [{"type": "content", "content": {"type": "text", "text": "Permission denied"}}]})
            return False
    upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tcid, "status": "in_progress",
              "_meta": {"toolNotification": {"type": "live_output", "params": {"sequence": 1, "chunks": [{"stream": "stdout", "output": "running\n"}]}}}})
    action()
    upd(sid, {"sessionUpdate": "tool_call_update", "toolCallId": tcid, "status": "completed",
              "content": [{"type": "content", "content": {"type": "text", "text": "ok"}}], "rawOutput": {"exit_code": 0}})
    return True


def prompt(rid, p):
    sid, text = p["sessionId"], p["prompt"][0]["text"]
    cancel.clear()
    upd(sid, {"sessionUpdate": "agent_message_chunk", "messageId": "m1", "content": {"type": "text", "text": "Working. "}})
    if "think" in text:
        upd(sid, {"sessionUpdate": "agent_thought_chunk", "messageId": "m1", "content": {"type": "text", "text": "hmm"}})
    if "crash" in text:
        os._exit(3)
    if "slow" in text:
        for _ in range(100):
            if cancel.wait(0.1):
                return send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "cancelled"}})
    if "tool" in text:
        tool(sid, "shell", "tc1", {"command": "echo hi > out.txt"}, lambda: open(os.path.join(work, "out.txt"), "w").write("hi\n"))
    if "edit" in text:
        tool(sid, "edit", "tc2", {"path": "a.py", "before": "x = 1", "after": "x = 2"},
             lambda: open(os.path.join(work, "a.py"), "w").write("x = 2\n"))
    upd(sid, {"sessionUpdate": "usage_update", "used": 10, "size": 1000})
    upd(sid, {"sessionUpdate": "agent_message_chunk", "messageId": "m2", "content": {"type": "text", "text": "Done."}})
    send({"jsonrpc": "2.0", "id": rid, "result": {"stopReason": "end_turn", "usage": {"totalTokens": 3, "inputTokens": 2, "outputTokens": 1}}})


def sessions():
    p = os.path.join(state_dir, "fake_sessions.json")
    return json.load(open(p)) if os.path.exists(p) else {}


for line in sys.stdin:
    m = json.loads(line)
    meth, rid, p = m.get("method"), m.get("id"), m.get("params") or {}
    if meth is None and rid in waiters:
        waiters[rid][1] = m.get("result")
        waiters[rid][0].set()
    elif meth == "initialize":
        send({"jsonrpc": "2.0", "id": rid, "result": {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}, "agentInfo": {"name": "fake-goose"}}})
    elif meth == "session/new":
        s = sessions()
        sid = f"fake_{len(s) + 1}"
        s[sid] = {"mcp": p.get("mcpServers"), "cwd": p.get("cwd")}
        json.dump(s, open(os.path.join(state_dir, "fake_sessions.json"), "w"))
        send({"jsonrpc": "2.0", "id": rid, "result": {"sessionId": sid}})
    elif meth == "session/load":
        if p["sessionId"] not in sessions():
            send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32002, "message": "no such session"}})
        else:
            upd(p["sessionId"], {"sessionUpdate": "agent_message_chunk", "messageId": "old", "content": {"type": "text", "text": "REPLAYED HISTORY"}})
            send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif meth == "session/set_mode":
        mode = {"approve": "approve", "auto": "auto", "smart_approve": "approve"}.get(p.get("modeId"), mode)
        send({"jsonrpc": "2.0", "id": rid, "result": {}})
    elif meth == "session/cancel":
        cancel.set()
    elif meth == "session/prompt":
        threading.Thread(target=prompt, args=(rid, p), daemon=True).start()
