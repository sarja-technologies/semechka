#!/usr/bin/env python3
"""Real Work Mode phase B scenarios (engine tools, delegation, skills) through the HTTP API of a Semechka STAGING instance.

Run it ON the Spark. It reads the synthetic staging credentials and the engine token from their owner-only files itself and never prints
either. Scenarios: models, image, delegate, openjutsu, skill. Each creates a session, sends one task, prints the real event timeline and
checks the real outcome (the PNG decodes, the MP4 passes ffprobe, the delegate event is done, the skill was read). It reports; it
never marks a failed run as passed.

    python3 work_phase_b.py --preflight
    python3 work_phase_b.py --scenario image --agent Balanced --cleanup
    python3 work_phase_b.py --scenario openjutsu --agent Balanced --cleanup --unload-engine openjutsu-minimax-h3

Needs work_e2e.py next to it (the API client and the timeline printer).
"""
from __future__ import annotations

import argparse
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from work_e2e import Api, show

TOKEN_FILE = os.path.expanduser("~/semechka-deploy/secrets/sarness.token")
ENGINE = "http://127.0.0.1:8752"
INPUTS = os.path.expanduser("~/sarness-comfyui/openjutsu/inputs")


def engine(path: str, method: str = "GET", body=None, timeout: int = 30):
    tok = open(TOKEN_FILE).read().strip()
    r = urllib.request.Request(ENGINE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                               headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:300]


def preflight() -> bool:
    """Nothing else may be running before a heavy run: Trinodal jobs, Schemra jobs, engine jobs; and what the ledger says is free."""
    ok = True
    units = subprocess.run(["systemctl", "--user", "list-units", "trinodal-job-*", "--no-legend"], capture_output=True, text=True).stdout.strip()
    print("trinodal-job units:", units or "none")
    ok &= not units
    for st in ("running", "queued"):
        code, jobs = engine(f"/v1/jobs?status={st}&limit=50")
        print(f"engine jobs {st}:", [j["id"] for j in jobs] if code == 200 else code)
        ok &= code == 200 and not jobs
    code, res = engine("/v1/resources")
    if code == 200:
        led = res["ledger"]
        print(f"ledger: free {led['free_mb'] / 1024:.1f} GiB, holders {led['holders']}, chat={res['this_engine']['llm']['state']}, worker={res['this_engine']['gpu_worker']}")
        ok &= not led["holders"]
    out = subprocess.run("ps -eo pid,etimes,cmd | grep -i -E 'schemra.*(train|job)|ComfyUI|comfyui' | grep -v grep | cut -c1-160",
                         shell=True, capture_output=True, text=True).stdout.strip()
    print("other heavy processes:", out or "none")
    ok &= not out
    return bool(ok)


def pick(api: Api, alias: str) -> dict:
    opts = api.req("GET", "/api/work/options")[1]
    m = next((x for x in opts["models"] if x["alias"] == alias), None)
    assert m, f"{alias!r} is not offered: {[x['alias'] for x in opts['models']]}"
    return m


def run_task(api: Api, sid: str, task: str, max_s: int) -> list[dict]:
    seen = api.req("GET", f"/api/work/sessions/{sid}/state")[1]["last_event_id"]
    code, r = api.req("POST", f"/api/work/sessions/{sid}/messages", {"content": task})
    assert code == 200, r
    out, t0 = [], time.time()
    while time.time() - t0 < max_s:
        code, body = api.req("GET", f"/api/work/sessions/{sid}/events?since={seen}&wait=20", timeout=60)
        assert code == 200, body
        for ev in body["events"]:
            print(show(ev), flush=True)
            out.append(ev)
            seen = ev["id"]
        if body["status"] == "awaiting_approval":
            for p in api.req("GET", f"/api/work/sessions/{sid}/state")[1]["pending_approvals"]:
                api.req("POST", f"/api/work/sessions/{sid}/approvals/{p['step']}", {"approve": True})
        if body["status"] in ("idle", "failed", "cancelled") and not body["events"]:
            break
    return out


def fetch_artifact(api: Api, aid: str) -> bytes:
    return api.req("GET", f"/api/work/artifacts/{aid}/content", raw=True)[1]


def summary(events: list[dict], extra: dict) -> None:
    calls = [e for e in events if e["kind"] == "tool_call"]
    res = [e for e in events if e["kind"] == "tool_result"]
    bad = [e for e in events if e["kind"] == "error"]
    print(f"\ntool calls={len(calls)} ok={sum(1 for e in res if e['data'].get('ok'))} failed={sum(1 for e in res if not e['data'].get('ok'))} "
          f"invalid model replies={sum(1 for e in bad if e['data'].get('code') == 'bad_action')}")
    for k, v in extra.items():
        print(f"  {k}: {v}")


def used(events: list[dict], tool: str) -> list[dict]:
    ids = {e["data"]["call_id"] for e in events if e["kind"] == "tool_call" and e["data"].get("tool") == tool}
    return [e["data"] for e in events if e["kind"] == "tool_result" and e["data"].get("call_id") in ids]


def check_png(data: bytes) -> str:
    from PIL import Image
    im = Image.open(io.BytesIO(data))
    im.load()
    return f"PNG decodes: {im.format} {im.size[0]}x{im.size[1]} {im.mode}, {len(data):,} bytes"


def ffprobe(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
        f.write(data)
        f.flush()
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name,width,height,r_frame_rate,duration,nb_frames",
                            "-of", "json", f.name], capture_output=True, text=True)
    if p.returncode != 0:
        return "ffprobe FAILED: " + p.stderr[:200]
    streams = json.loads(p.stdout).get("streams", [])
    return "ffprobe ok: " + json.dumps(streams)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://192.168.20.193:8790")
    ap.add_argument("--account", default="owner@staging.test")
    ap.add_argument("--scenario", choices=["models", "image", "delegate", "openjutsu", "skill"])
    ap.add_argument("--agent", default="Balanced", help="alias of the main agent model")
    ap.add_argument("--image-model", default="sdxl-turbo")
    ap.add_argument("--helper", default="qwen3.5-0.8b@Q8_0")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--preflight", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--unload-engine", default="", help="comma-separated engine model ids to unload through the engine API afterwards")
    a = ap.parse_args()
    if a.preflight:
        ok = preflight()
        print("PREFLIGHT", "clear" if ok else "NOT CLEAR")
        return 0 if ok else 3
    assert a.scenario, "--scenario is required"
    api = Api(a.base)
    api.login(a.account)
    agent = pick(api, a.agent)
    body = {"title": f"phase B {a.scenario}", "model_id": agent["id"], "autonomy": "contained"}
    task, check = "", None
    if a.scenario == "models":
        task = ("Call the list_models tool with kind image, then call the resources tool. Then tell me which image models are ready, whether any is "
                "loaded, and how much memory is free.")
    elif a.scenario == "image":
        task = (f'Make a picture with the generate_image tool. Use exactly these arguments: model "{a.image_model}", prompt "a red fox sitting in '
                'snow, photo", size "512", steps 4, out "artifacts/fox.png". When the tool says it is done, tell me the file name and its size.')
    elif a.scenario == "delegate":
        task = (f'Use the delegate tool: model "{a.helper}", task "What is 17 plus 25? Reply with just the number.". Then tell me the number the '
                "helper model answered and which model answered.")
    elif a.scenario == "openjutsu":
        task = ('The files inputs/src.mp4 (a short video) and inputs/ref.png (a picture of the new person) are in /work. Make a video with the '
                'generate_video tool using exactly these arguments: model "openjutsu-minimax-h3", inputs {"prompt": "the woman", "video": '
                '"inputs/src.mp4", "refs": "inputs/ref.png", "resolution": "480p", "mask_margin": 40}, out "artifacts/swap.mp4". It takes a few '
                "minutes; the tool waits for the result. When it is done, tell me the output file name and its size.")
    elif a.scenario == "skill":
        skills = api.req("GET", "/api/work/skills")[1]
        sk = next(s for s in skills if s["name"] == "video-production")
        body["skill_ids"] = [sk["id"]]
        task = ("Use the read_skill tool to read the skill video-production. Then answer from it: which inputs does the OpenJutsu profile need, "
                "which of them are required, and what does the licence note say about where the results may be used?")
    code, sess = api.req("POST", "/api/work/sessions", body)
    assert code == 200, sess
    sid = sess["id"]
    print(f"session {sid} agent={a.agent} scenario={a.scenario}")
    if a.scenario == "openjutsu":
        for name, src in (("inputs/src.mp4", "src_3s_480p.mp4"), ("inputs/ref.png", "ref_woman_yellow_coat.png")):
            data = open(os.path.join(INPUTS, src), "rb").read()
            # multipart upload through the documented route
            boundary = "----wpb" + str(int(time.time()))
            payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{src}\"\r\nContent-Type: application/octet-stream\r\n\r\n").encode() \
                + data + f"\r\n--{boundary}--\r\n".encode()
            r = urllib.request.Request(api.base + f"/api/work/sessions/{sid}/files?path={name}", data=payload, method="PUT")
            r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
            r.add_header("X-CSRF-Token", api.csrf)
            with api.op.open(r, timeout=120) as resp:
                print("uploaded", name, resp.status, json.loads(resp.read()))
    t0 = time.time()
    events = run_task(api, sid, task, a.max_seconds)
    wall = time.time() - t0
    state = api.req("GET", f"/api/work/sessions/{sid}/state")[1]
    arts = api.req("GET", f"/api/work/sessions/{sid}/artifacts")[1]
    extra = {"wall": f"{wall:.0f}s", "session status": state["status"], "artifacts": [(x["path"], x["size"], x["mime"]) for x in arts],
             "jobs": [(j["id"], j["status"], j.get("model")) for j in state.get("jobs", [])], "usage": state["usage"]}
    verdict = False
    if a.scenario == "models":
        r = {t: used(events, t) for t in ("list_models", "resources")}
        verdict = all(v and v[-1].get("ok") for v in r.values())
    elif a.scenario == "image":
        png = next((x for x in arts if x["path"] == "artifacts/fox.png"), None)
        if png:
            extra["file check"] = check_png(fetch_artifact(api, png["id"]))
            verdict = extra["file check"].startswith("PNG decodes")
    elif a.scenario == "delegate":
        d = [e["data"] for e in events if e["kind"] == "delegate"]
        extra["delegate events"] = [(x["status"], x.get("depth"), x.get("model"), (x.get("result") or x.get("error") or "")[:80]) for x in d]
        verdict = any(x["status"] == "done" and "42" in (x.get("result") or "") for x in d)
    elif a.scenario == "openjutsu":
        mp4 = next((x for x in arts if x["path"] == "artifacts/swap.mp4"), None)
        if mp4:
            extra["file check"] = ffprobe(fetch_artifact(api, mp4["id"]))
            verdict = extra["file check"].startswith("ffprobe ok") and mp4["size"] > 10_000
        lib = api.req("GET", "/api/media/jobs?kind=video")[1]
        extra["library"] = [(j["id"], j["status"], [x["mime"] for x in j["assets"]]) for j in lib[:3]]
    elif a.scenario == "skill":
        r = used(events, "read_skill")
        verdict = bool(r and r[-1].get("ok") and "OpenJutsu" in r[-1].get("output", ""))
    summary(events, extra)
    print(f"\nRESULT {a.scenario}: {'PASS' if verdict else 'FAIL'}")
    if a.cleanup:
        print("session deleted ->", api.req("DELETE", f"/api/work/sessions/{sid}")[0])
    for mid in [x for x in a.unload_engine.split(",") if x]:
        code, r = engine(f"/v1/models/{mid}/unload", "POST", None)
        print(f"engine unload {mid} -> {code} {str(r)[:120]}")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
