from acc_common import *
a = C("admin@staging.test"); check("admin signs in", a.ok)
S = {"goose.enabled": True, "goose.router_url": "http://127.0.0.1:8080", "goose.router_key_file": os.path.expanduser("~/spark-llm/work.key"),
     "goose.verified_file": os.path.expanduser("~/spark-llm/models.verified.json"), "goose.docker_host": f"unix:///run/user/{os.getuid()}/semechka-sandbox.sock",
     "goose.default_model": "qwen3.6-35b-a3b", "goose.sock_root": os.path.expanduser("~/semechka-deploy/goose-sock"), "modes.work.enabled": True}
for k, v in S.items():
    r = a.call("PUT", f"/api/admin/settings/{k}", {"value": v}); check(f"set {k}", "_http" not in r, r.get("_http") and (r.get("message") or r.get("detail") or r))
u = C("owner@staging.test"); me = u.call("GET", "/api/me")
print("owner sees Work Mode in capabilities:", [k for k in json.dumps(me).split('"') if "work" in k.lower()][:6])
r = u.call("GET", "/api/work/goose/status"); print("goose status route:", json.dumps(r)[:300])
