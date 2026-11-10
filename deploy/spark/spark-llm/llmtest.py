#!/usr/bin/env python3
"""Real end-to-end checks against the llama.cpp router. Run ON the Spark. Never prints the API key.
usage: llmtest.py MODEL [--url http://127.0.0.1:8099] [--skip-unload]"""
import json, os, sys, time, subprocess, urllib.request, argparse

ap = argparse.ArgumentParser(); ap.add_argument("model"); ap.add_argument("--url", default="http://127.0.0.1:8099")
ap.add_argument("--skip-unload", action="store_true"); a = ap.parse_args()
KEY = open(os.path.expanduser("~/spark-llm/api.key")).read().strip()
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

def avail():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1]) // 1024
def post(path, body, stream=False, timeout=900):
    r = urllib.request.Request(a.url + path, json.dumps(body).encode(), H)
    return urllib.request.urlopen(r, timeout=timeout)
def get(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(a.url + path, headers=H), timeout=30))
def status():
    for m in get("/v1/models")["data"]:
        if m["id"] == a.model: return m["status"]["value"] if isinstance(m.get("status"), dict) else m.get("status")
def children():
    out = subprocess.run(["pgrep", "-af", "llama-server"], capture_output=True, text=True).stdout.splitlines()
    return [l for l in out if "--alias" in l]

def stream_chat(msgs, tools=None, max_tokens=300):
    body = {"model": a.model, "stream": True, "max_tokens": max_tokens, "messages": msgs,
            "stream_options": {"include_usage": True}}
    if tools: body["tools"] = tools
    t0 = time.time(); ttft = None; content = ""; reasoning = ""; calls = {}; usage = None; fin = None; n = 0
    with post("/v1/chat/completions", body) as resp:
        for raw in resp:
            line = raw.decode().strip()
            if not line.startswith("data:") or line.endswith("[DONE]"): continue
            j = json.loads(line[5:])
            if j.get("usage"): usage = j["usage"]
            for ch in j.get("choices", []):
                d = ch.get("delta", {}); fin = ch.get("finish_reason") or fin
                if d.get("content"): content += d["content"]; n += 1
                if d.get("reasoning_content"): reasoning += d["reasoning_content"]; n += 1
                for tc in d.get("tool_calls", []) or []:
                    c = calls.setdefault(tc["index"], {"id": tc.get("id"), "name": "", "args": ""})
                    f = tc.get("function", {}); c["name"] += f.get("name") or ""; c["args"] += f.get("arguments") or ""
                    c["id"] = tc.get("id") or c["id"]
                if ttft is None and (d.get("content") or d.get("reasoning_content") or d.get("tool_calls")): ttft = time.time() - t0
    return dict(content=content, reasoning=reasoning, calls=list(calls.values()), usage=usage, finish=fin, ttft=ttft, total=time.time() - t0)

R = {}
print(f"== {a.model}  status={status()}  MemAvailable={avail()} MiB  children={len(children())}")
m0 = avail()
t = time.time()
r = stream_chat([{"role": "user", "content": "Reply with exactly: PONG"}], max_tokens=200)
load_s = time.time() - t
print(f"first request incl. load: {load_s:.1f}s  status={status()}  MemAvailable={avail()} MiB (delta {m0 - avail()} MiB)")
print("answer:", repr(r["content"][:60]), "| reasoning chars:", len(r["reasoning"]), "| finish:", r["finish"])
R["stream_ok"] = bool(r["content"] or r["reasoning"])

# throughput
r = stream_chat([{"role": "user", "content": "Write a 120-word paragraph about the ocean."}], max_tokens=400)
u = r["usage"] or {}
gen = u.get("completion_tokens"); dur = r["total"] - (r["ttft"] or 0)
print(f"stream: ttft={r['ttft']:.2f}s completion_tokens={gen} -> {(gen or 0) / max(dur, 1e-6):.1f} tok/s")

# context retention
msgs = [{"role": "user", "content": "Remember this code word: TANGERINE-4471. Acknowledge briefly."}]
r1 = stream_chat(msgs, max_tokens=400); msgs.append({"role": "assistant", "content": r1["content"] or "ok"})
msgs.append({"role": "user", "content": "Unrelated: what is 17*3? Then tell me the code word I gave you."})
r2 = stream_chat(msgs, max_tokens=600)
R["context_ok"] = "TANGERINE-4471" in r2["content"]
print("context retention:", R["context_ok"], "|", repr(r2["content"][:110]))

# tool calling round-trip
tools = [{"type": "function", "function": {"name": "get_file_size", "description": "Return the size in bytes of a file by path.",
          "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}}]
msgs = [{"role": "user", "content": "Use the get_file_size tool to find the size of /etc/hostname, then tell me the number."}]
r = stream_chat(msgs, tools=tools, max_tokens=800)
R["tool_call_emitted"] = bool(r["calls"])
print("tool call emitted:", R["tool_call_emitted"], r["calls"][:1], "finish:", r["finish"])
if r["calls"]:
    c = r["calls"][0]
    msgs.append({"role": "assistant", "content": r["content"] or None, "tool_calls": [{"id": c["id"] or "call_1", "type": "function", "function": {"name": c["name"], "arguments": c["args"] or "{}"}}]})
    msgs.append({"role": "tool", "tool_call_id": c["id"] or "call_1", "content": "4242"})
    r = stream_chat(msgs, tools=tools, max_tokens=800)
    R["tool_result_used"] = "4242" in r["content"]
    print("final answer uses tool result:", R["tool_result_used"], "|", repr(r["content"][:100]))

R["mem_loaded_delta_mib"] = m0 - avail()
if not a.skip_unload:
    mb = avail()
    resp = post("/models/unload", {"model": a.model}).read().decode()
    for _ in range(60):
        if status() == "unloaded" and not any(a.model in c for c in children()): break
        time.sleep(1)
    time.sleep(2)
    R["unloaded"] = status() == "unloaded"; R["child_gone"] = not any(f"--alias {a.model} " in c for c in children())
    R["mem_released_mib"] = avail() - mb
    print(f"unload -> {resp.strip()} status={status()} child_process_gone={R['child_gone']} MemAvailable {mb} -> {avail()} MiB (released {avail()-mb} MiB)")
print("RESULT", json.dumps(R))
