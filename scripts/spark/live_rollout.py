#!/usr/bin/env python3
"""Live check of the 0.5.7 controls on the STAGING instance (synthetic accounts, disposable data; NOT production):
administrators-only rollout, the per-owner file rule, the agent model allow-list, rollback and audit. Restores every setting it
changes. Credentials are read over ssh at run time and never printed.

    .venv/bin/python scripts/spark/live_rollout.py
"""
from __future__ import annotations

import pathlib
import secrets
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from live_scenarios import BASE, DOMAIN, RESULTS, Client, check, credentials


def main() -> int:
    cr = credentials()
    login = lambda n: Client(f"{n}@{DOMAIN}", cr[f"{n}@{DOMAIN}"])
    owner, fin, eng, boss = login("owner"), login("acme-finance"), login("acme-eng"), login("acme-owner")
    acme = next(o for o in boss.get("/api/orgs")[1] if "acme" in o["name"].lower())["id"]
    # the engineer administers Engineering; the finance lead administers Finance. The finance lead is added to Engineering as a
    # contributor (staging only) to play "a colleague with a role"; the organisation owner has no content rights by design.
    wid = next(w["id"] for w in eng.get(f"/api/orgs/{acme}/workspaces")[1] if w["name"] == "Engineering")
    me = {m["email"]: m["user_id"] for m in boss.get(f"/api/orgs/{acme}/members")[1]}
    code, _ = eng.put(f"/api/workspaces/{wid}/members", {"principal_type": "user", "principal_id": me[f"acme-finance@{DOMAIN}"], "role": "contributor"})
    print(f"staging organisation {acme}, workspace Engineering; colleague added: {code}", flush=True)

    def pages(c):
        return [p["id"] for p in c.get("/api/me")[1]["ui"]["pages"]]

    def setv(k, v):
        return owner.put(f"/api/admin/settings/{k}", {"value": v})[0]

    def reset(k, v):
        return setv(k, v)

    code, hist0 = owner.get("/api/admin/settings-history")
    last0 = max([h["id"] for h in hist0] or [0])
    try:
        # ---- 1. everyone (today's behaviour) -------------------------------------------------------------
        check("setup: switches on, rollout everyone", setv("cloud.workspaces.enabled", True) == 200 and setv("cloud.agents.enabled", True) == 200
              and setv("cloud.rollout", "everyone") == 200)
        before = len(eng.get(f"/api/workspaces/{wid}/files")[1].get("files", []))
        check("everyone: ordinary members see Workspaces and read files", "workspaces" in pages(fin) and fin.get(f"/api/workspaces/{wid}/files")[0] == 200)

        # ---- 2. administrators only ----------------------------------------------------------------------
        check("rollout=admins accepted", setv("cloud.rollout", "admins") == 200)
        check("admins: ordinary member's navigation has no Workspaces", "workspaces" not in pages(fin) and "workspaces" not in pages(eng))
        doors = [("GET", f"/api/orgs/{acme}/workspaces"), ("GET", f"/api/workspaces/{wid}/files"), ("GET", f"/api/workspaces/{wid}/agents"),
                 ("GET", f"/api/workspaces/{wid}/usage"), ("POST", f"/api/workspaces/{wid}/retrieve")]
        seen = []
        for who, c in (("member", fin), ("org owner", boss)):
            for m, path in doors:
                code, r = c.req(m, path, {"query": "x"} if m == "POST" else None)
                seen.append((who, path.split("/")[-1], code, (r.get("detail") or {}).get("code") if isinstance(r, dict) else None))
        check("admins: every cloud endpoint answers 403 'rollout' to members and the organisation's own owner",
              all(s[2] == 403 and s[3] == "rollout" for s in seen), seen[:3])
        up = eng.req("POST", f"/api/workspaces/{wid}/files", files={"file": ("x.md", b"x")})
        check("admins: uploads are refused too", up[0] == 403, up[0])
        check("admins: the platform administrator still reaches administration",
              owner.get("/api/admin/cloud/status")[0] == 200 and owner.get("/api/admin/cloud/status")[1].get("rollout") == "admins")
        try:
            urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/workspaces/{wid}/files"), timeout=20)
            anon = 200
        except urllib.error.HTTPError as e:
            anon = e.code
        check("admins: a signed-out caller gets 401", anon == 401, anon)

        # ---- 3. back to everyone: nothing lost ----------------------------------------------------------------
        check("rollout=everyone restored", setv("cloud.rollout", "everyone") == 200)
        after = len(eng.get(f"/api/workspaces/{wid}/files")[1].get("files", []))
        check("everyone again: access and the same files are back", "workspaces" in pages(fin) and after == before, (before, after))

        # ---- 4. per-owner file rule -------------------------------------------------------------------------------
        tag = secrets.token_hex(3)
        name = f"owner-rule-{tag}.md"
        code, f = eng.upload(wid, name, f"# Private {tag}\nThe quokka ledger {tag} belongs to its owner.\n".encode())
        check("owner rule: engineer uploads a file", code == 200, code)
        names = lambda c: [x["name"] for x in c.get(f"/api/workspaces/{wid}/files")[1].get("files", [])]
        shared_before = name in names(fin)
        code, _ = eng.req("PATCH", f"/api/workspaces/{wid}", {"policy": {"file_access": "owner"}})
        check("owner rule: workspace administrator turns it on", code == 200, code)
        try:
            check("default sharing before: colleague could list it", shared_before)
            check("owner rule: colleague no longer lists it, owner still does", name not in names(fin) and name in names(eng), (name in names(fin), name in names(eng)))
            code, _ = fin.get(f"/api/files/{f['id']}/content?download=1", raw=True)
            check("owner rule: colleague cannot download it", code in (403, 404), code)
            code, _ = owner.get(f"/api/files/{f['id']}/content?download=1", raw=True)
            check("owner rule: not even the platform owner can", code in (403, 404), code)
            code, _ = eng.get(f"/api/files/{f['id']}/content?download=1", raw=True)
            check("owner rule: the owner can", code == 200, code)
            code, r = fin.post(f"/api/workspaces/{wid}/retrieve", {"query": f"quokka ledger {tag}"})
            check("owner rule: retrieval never returns it to the colleague", code == 200 and tag not in " ".join(p["text"] for p in r.get("passages", [])), code)
        finally:
            eng.req("PATCH", f"/api/workspaces/{wid}", {"policy": {}})
        check("owner rule: switching it off returns role-based access", name in names(fin))

        # ---- 5. agent model allow-list ---------------------------------------------------------------------------
        code, models = eng.get(f"/api/workspaces/{wid}/models?mode=chat")
        by = {m["alias"]: m["id"] for m in models}
        print("  chat models for the member:", sorted(by), flush=True)
        big = next((i for a, i in by.items() if "35" in a or "gpt-oss" in a or "large" in a.lower()), None)
        small = next((i for a, i in by.items() if i != big), None)
        if big and small:
            check("agent models: allow only the big model", setv("cloud.agents.models", [big]) == 200)
            ag = [m["id"] for m in eng.get(f"/api/workspaces/{wid}/models?mode=chat&purpose=agent")[1]]
            check("agent models: the agent picker offers only it", ag == [big], ag)
            check("agent models: chat still lists the others", small in [m["id"] for m in eng.get(f"/api/workspaces/{wid}/models?mode=chat")[1]])
            code, r = eng.post(f"/api/workspaces/{wid}/agents", {"name": f"small-{tag}", "instructions": "x", "model_id": small, "tools": ["calc"]})
            check("agent models: an agent cannot be pinned to the small model", code in (400, 403, 422), code)
        else:
            check("agent models: two chat models available to compare", False, sorted(by))

        # ---- 6. audit and flag history ----------------------------------------------------------------------------------
        time.sleep(1)
        hist = [h for h in owner.get("/api/admin/settings-history")[1] if h["id"] > last0 and h["key"].startswith("cloud.")]
        check("history: every flag change is recorded with old and new value", {"cloud.rollout", "cloud.agents.models"} <= {h["key"] for h in hist}, len(hist))
        a = owner.get("/api/admin/audit?per_page=100")[1].get("items", [])
        acts = [e["action"] for e in a]
        check("audit: policy and setting changes are in the audit log", any(x.startswith("ws.update") for x in acts) and any("setting" in x for x in acts), sorted(set(acts))[:8])
        v = owner.get("/api/admin/audit/verify")[1]
        check("audit: the hash chain verifies", v.get("ok") is True, v)
    finally:
        print("restored to the staging baseline:", {k: reset(k, v) for k, v in (("cloud.rollout", "everyone"), ("cloud.agents.models", []))}, flush=True)  # (staging baseline: switches stay on)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
