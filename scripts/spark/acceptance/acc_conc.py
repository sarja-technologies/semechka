from acc_common import *
import threading, urllib.request as ur
RK = open(os.path.expanduser("~/spark-llm/api.key")).read().strip()
def router_loaded():
    r = json.load(ur.urlopen(ur.Request("http://127.0.0.1:8080/v1/models", headers={"Authorization": "Bearer " + RK}), timeout=20))["data"]
    return sorted(m["id"] for m in r if (m["status"]["value"] if isinstance(m.get("status"), dict) else m.get("status")) == "loaded")
u1, u2 = C("owner@staging.test"), C("admin@staging.test")
def model(u, alias): return next(m for m in u.call("GET", "/api/models/catalog?operation=text")["models"] if m["alias"] == alias)
res = {}
def chat(tag, u, alias, prompt):
    m = model(u, alias); cid = u.call("POST", "/api/chat/conversations", {"model_id": m["id"]})["id"]; t0 = time.time()
    ev = u.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": prompt, "model_id": m["id"], "operation": "text"})
    th = u.call("GET", f"/api/chat/conversations/{cid}/thread"); msgs = th.get("messages") or th.get("items") or []
    a = [x for x in msgs if x.get("role") == "assistant"]; txt = (a[-1].get("content") or "") if a else ""
    res[tag] = (alias, round(time.time() - t0, 1), len(ev), len(txt.strip()) > 0); print("   events", tag, [(k, json.dumps(v)[:200]) for k, v in ev][:2])
print("loaded before:", router_loaded())
ts = [threading.Thread(target=chat, args=("A", u1, "Balanced", "Write a short haiku about autumn.")), threading.Thread(target=chat, args=("B", u2, "Agent", "List three primary colours, one per line."))]
[t.start() for t in ts]; [t.join() for t in ts]
for k, v in sorted(res.items()): print(f"  user {k}: model={v[0]} took {v[1]}s events={v[2]} reply_saved={v[3]}")
ld = router_loaded(); print("loaded after:", ld)
check("both users got replies at the same time", all(v[3] for v in res.values()) and len(res) == 2)
check("neither model was evicted to serve the other (both resident)", {"qwen3-4b-instruct-2507", "qwen3.6-35b-a3b"} <= set(ld), ld)
print(f"\n{sum(RES)}/{len(RES)} passed")
