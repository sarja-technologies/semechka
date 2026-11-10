#!/usr/bin/env python3
"""Concurrency / switching / eviction test against the production router (run ON the Spark)."""
import json, os, threading, time, urllib.request
URL = "http://127.0.0.1:8080"
KEY = open(os.path.expanduser("~/spark-llm/api.key")).read().strip()
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}
def avail():
    for l in open("/proc/meminfo"):
        if l.startswith("MemAvailable"): return int(l.split()[1]) // 1024
def states():
    r = json.load(urllib.request.urlopen(urllib.request.Request(URL + "/v1/models", headers=H), timeout=30))["data"]
    return {m["id"]: (m["status"]["value"] if isinstance(m.get("status"), dict) else m.get("status")) for m in r}
def loaded(): return sorted(k for k, v in states().items() if v == "loaded")
res = {}
def stream(tag, model, prompt, n=250):
    t0 = time.time(); first = None; toks = 0; err = None
    try:
        body = {"model": model, "stream": True, "max_tokens": n, "messages": [{"role": "user", "content": prompt}], "stream_options": {"include_usage": True}}
        with urllib.request.urlopen(urllib.request.Request(URL + "/v1/chat/completions", json.dumps(body).encode(), H), timeout=900) as r:
            for raw in r:
                line = raw.decode().strip()
                if not line.startswith("data:") or line.endswith("[DONE]"): continue
                j = json.loads(line[5:])
                if j.get("usage"): toks = j["usage"]["completion_tokens"]
                for ch in j.get("choices", []):
                    d = ch.get("delta", {})
                    if (d.get("content") or d.get("reasoning_content")) and first is None: first = time.time() - t0
    except Exception as e: err = str(e)[:120]
    res[tag] = dict(model=model, ttft=None if first is None else round(first, 2), tokens=toks, secs=round(time.time() - t0, 1), err=err)
def run(group):
    ts = [threading.Thread(target=stream, args=g) for g in group]
    [t.start() for t in ts]; [t.join() for t in ts]

print("start loaded:", loaded(), "avail MiB:", avail())
print("\n[1] three different models concurrently (cold/warm mix)")
run([("A", "qwen3-coder-next", "Write a haiku about compilers."), ("B", "qwen3.6-35b-a3b", "Write a haiku about oceans."), ("C", "qwen3-4b-instruct-2507", "Write a haiku about winter.")])
for k in "ABC": print(" ", k, res[k])
print("  loaded after:", loaded(), "avail MiB:", avail())
print("\n[2] two simultaneous requests on ONE model (parallel slots) + a third model idle")
res.clear()
run([("D1", "qwen3.6-35b-a3b", "Count from 1 to 40 separated by spaces.", 200), ("D2", "qwen3.6-35b-a3b", "List 20 fruit names.", 200)])
for k in ("D1", "D2"): print(" ", k, res[k])
print("  loaded after:", loaded())
print("\n[3] model switch: request a 4th model (models-max=3) -> which is evicted?")
before = set(loaded()); res.clear()
stream("E", "qwen3.8-27b", "Say hi in three words.", 60)
print(" ", res["E"]); after = set(loaded())
print("  evicted:", sorted(before - after), "| now loaded:", sorted(after), "| avail MiB:", avail())
print("\n[4] eviction while another model is mid-generation (does the router cut a live stream?)")
res.clear()
t = threading.Thread(target=stream, args=("LONG", "qwen3.8-27b", "Write 300 words about the history of computing.", 500)); t.start()
time.sleep(3)
others = [m for m in loaded() if m != "qwen3.8-27b"]
stream("F", "qwen3-4b-instruct-2507" if "qwen3-4b-instruct-2507" not in loaded() else "qwen3.5-0.8b", "Say hi.", 30)
t.join()
print("  long stream on", "qwen3.8-27b:", res["LONG"]); print("  trigger request:", res["F"]); print("  loaded:", loaded())
