from acc_common import *
import uuid
a = C("admin@staging.test"); u = C("owner@staging.test")
rows = [m for m in a.call("GET", "/api/admin/models?q=reverse-video&per_page=20").get("items", []) if m["alias"] == "reverse-video"]
print("reverse-video rows:", [(m["id"], m["backend"], m["enabled"]) for m in rows])
row = next(m for m in rows if "media engine" in m["backend"].lower())
a.call("PATCH", f"/api/admin/models/{row['id']}", {"enabled": True})
a.call("PUT", f"/api/admin/models/{row['id']}/grants", {"grants": [{"subject_type": "everyone", "effect": "allow", "modes": ["video"]}]})
vo = u.call("GET", "/api/media/options?kind=video"); rea = next((m for m in vo["models"] if m["alias"] == "reverse-video"), None)
check("REA offered to the person", bool(rea)); prof = rea.get("profile") or {}; print("profile keys:", sorted(prof.keys())); print("profile inputs:", json.dumps(prof.get("inputs") or prof.get("files"))[:300])
assets = u.call("GET", "/api/media/assets?limit=100"); items = assets if isinstance(assets, list) else assets.get("items", [])
vid = next((x for x in items if (x.get("kind") or x.get("type")) == "video"), None); print("video asset:", vid and vid.get("id"), "of", len(items))
fi = None
for k in ("inputs", "files"):
    for i in prof.get(k) or []:
        if isinstance(i, dict) and i.get("type") in ("video", "file", None): fi = i.get("name") or i.get("id") or i.get("key"); break
    if fi: break
fi = "video"
def multipart(fields):
    bd = uuid.uuid4().hex; body = b""
    for k, v in fields.items(): body += f'--{bd}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    return bd, body + f"--{bd}--\r\n".encode()
bd, body = multipart({"model_id": rea["id"], "inputs": json.dumps({}), f"asset:{fi}": vid["id"], "save": "true"})
req = urllib.request.Request(BASE + "/api/media/profile-jobs", method="POST", data=body, headers={"Content-Type": f"multipart/form-data; boundary={bd}", "x-csrf-token": u.csrf, "Origin": BASE})
try:
    with u.op.open(req, timeout=120) as x: job = json.load(x)
except urllib.error.HTTPError as e: job = {"_http": e.code, "b": e.read()[:300].decode()}
check("REA job accepted", "_http" not in job and job.get("id"), job.get("_http") and job)
jid = job["id"]; t0 = time.time()
while time.time() - t0 < 600:
    j = u.call("GET", f"/api/media/jobs/{jid}")
    if j.get("status") in ("completed", "failed", "cancelled"): break
    time.sleep(3)
check("REA completed", j["status"] == "completed", f"{time.time()-t0:.0f}s {j.get('error')}")
an = u.call("GET", f"/api/media/jobs/{jid}/analysis"); check("analysis served through Semechka", "_http" not in an, an.get("_http") or "")
print("analysis keys:", sorted(an.keys())[:14]); print("shots:", json.dumps(an.get("shots") or an.get("scenes") or "")[:200])
print(f"\n{sum(RES)}/{len(RES)} passed")
