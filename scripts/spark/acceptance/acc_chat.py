from acc_common import *
u = C("owner@staging.test"); check("owner signs in with the existing account", u.ok)
cat = u.call("GET", "/api/models/catalog?operation=text")
bal = next(m for m in cat["models"] if m["alias"] == "Balanced")
conv = u.call("POST", "/api/chat/conversations", {"model_id": bal["id"]}); cid = conv["id"]
def say(text, mid=bal["id"]):
    ev = u.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": text, "model_id": mid, "operation": "text"})
    return ev
def last_assistant():
    th = u.call("GET", f"/api/chat/conversations/{cid}/thread")
    msgs = th.get("messages") or th.get("items") or []
    a = [m for m in msgs if m.get("role") == "assistant"]
    return (a[-1].get("content") or a[-1].get("text") or "") if a else "", th
t0 = time.time(); ev = say("Remember this code word: TANGERINE-4471. Reply with just OK.")
kinds = sorted({e[0] for e in ev}); check("reply streamed as server-sent events", len(ev) > 3, f"{len(ev)} events, kinds={kinds}, {time.time()-t0:.1f}s")
txt, th = last_assistant(); check("assistant reply saved to history", bool(txt.strip()), txt[:60])
ev = say("What was the code word I gave you? Answer with the word only."); txt, th = last_assistant()
check("multi-turn context retained (via llama.cpp router)", "TANGERINE" in txt.upper(), txt[:80])
convs = u.call("GET", "/api/chat/conversations"); items = convs if isinstance(convs, list) else convs.get("items", [])
check("conversation appears in the person's history", any(c.get("id") == cid for c in items))
# isolation: another organisation's owner cannot see it
v = C("globex-owner@staging.test"); check("second organisation's owner signs in", v.ok)
r = v.call("GET", f"/api/chat/conversations/{cid}"); check("another person gets 404 for this conversation", r.get("_http") == 404, r.get("_http"))
r = v.call("GET", f"/api/chat/conversations/{cid}/thread"); check("…and for its thread", r.get("_http") == 404, r.get("_http"))
print(f"\n{sum(RES)}/{len(RES)} passed"); json.dump({"cid": cid}, open("/tmp/acc_chat.json", "w"))
