from acc_common import *
import subprocess
OUT = os.path.expanduser("~/imajutsu-bench/accept-consol"); os.makedirs(OUT, exist_ok=True)
a = C("admin@staging.test"); u = C("owner@staging.test")
# enable the engine's H3 row (the migrated one) for video, through the audited admin API
r = a.call("GET", "/api/admin/models?q=minimax-h3&per_page=50"); rows = [m for m in r.get("items", []) if m["alias"] == "minimax-h3" and "8780" in (m.get("backend") or "") + json.dumps(m.get("meta") or {}) or m["alias"] == "minimax-h3"]
vo = u.call("GET", "/api/media/options?kind=video")
eng = {m["id"] for m in vo.get("models", [])}
print("video model rows named minimax-h3:", [(m["id"], m.get("backend"), m["enabled"]) for m in rows])
def wait(jid, limit=1500):
    t0 = time.time(); prog = []
    while time.time() - t0 < limit:
        j = u.call("GET", f"/api/media/jobs/{jid}")
        if isinstance(j.get("progress"), (int, float)): prog.append(j["progress"])
        if j.get("status") in ("completed", "failed", "cancelled"): return j, time.time() - t0, prog
        time.sleep(3)
    return j, time.time() - t0, prog
def monotonic(p): return all(b >= a - 1e-9 for a, b in zip(p, p[1:]))
def asset(j):
    outs = j.get("outputs") or j.get("assets") or []
    return (outs[0].get("id") if outs and isinstance(outs[0], dict) else outs[0]) if outs else None
io = u.call("GET", "/api/media/options?kind=image")
q = next(m for m in io["models"] if m["alias"] == "Qwen-Image 2512")
check("image model offered by the media engine", True, f"{q['alias']} limits={json.dumps(q.get('limits'))[:80]}")
r = u.call("POST", "/api/media/jobs", {"kind": "image", "model_id": q["id"], "prompt": "a red bicycle leaning on a brick wall in the rain, photo", "save": True})
check("image job accepted", "_http" not in r and r.get("id"), r.get("_http") or r.get("detail") or "")
j, secs, prog = wait(r["id"]); check("Qwen-Image 2512 generated on the GPU via the engine", j["status"] == "completed", f"{secs:.0f}s status={j['status']} err={j.get('error')}")
check("image progress monotonic", monotonic(prog) and len(prog) > 0, f"{len(prog)} samples, last={prog[-1] if prog else None}")
aid = asset(j); data = u.call("GET", f"/api/media/assets/{aid}/content", raw=True) if aid else b""
open(f"{OUT}/image.png", "wb").write(data); check("image is a real picture", data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff", f"{len(data)//1024} KiB")
# H3 text-to-video 5 s
h3 = next((m for m in vo["models"] if m["alias"] == "minimax-h3"), None)
if not h3:
    row = rows[0]; a.call("PATCH", f"/api/admin/models/{row['id']}", {"enabled": True})
    a.call("PUT", f"/api/admin/models/{row['id']}/grants", {"grants": [{"subject_type": "everyone", "effect": "allow", "modes": ["video"]}]})
    vo = u.call("GET", "/api/media/options?kind=video"); h3 = next((m for m in vo["models"] if m["alias"] == "minimax-h3"), None)
check("H3 offered to the person after the admin enabled it", bool(h3))
print("H3 duration option:", json.dumps(h3.get("duration"))[:240])
r = u.call("POST", "/api/media/jobs", {"kind": "video", "model_id": h3["id"], "prompt": "rain falling on a quiet city street at dusk, a red bicycle against a wall, steam rising from a grate", "seconds": 5, "save": True})
check("H3 text-to-video job accepted (5 s asked)", "_http" not in r and r.get("id"), r.get("_http") or r.get("detail") or "")
told = json.dumps({k: r.get(k) for k in ("params", "duration", "seconds", "frames", "fps", "effective_seconds") if r.get(k) is not None})[:300]; print("API said at submit:", told)
j, secs, prog = wait(r["id"], 1700); check("H3 text-to-video completed", j["status"] == "completed", f"{secs:.0f}s status={j['status']} err={j.get('error')}")
check("video progress monotonic and genuine", monotonic(prog) and len(set(prog)) > 2, f"{len(prog)} samples, distinct={len(set(prog))}")
aid = asset(j); data = u.call("GET", f"/api/media/assets/{aid}/content", raw=True) if aid else b""
open(f"{OUT}/h3.mp4", "wb").write(data)
p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,nb_frames,r_frame_rate,width,height", "-of", "json", f"{OUT}/h3.mp4"], capture_output=True, text=True)
info = json.loads(p.stdout or "{}"); dur = float(info.get("format", {}).get("duration", 0)); st = info.get("streams", [])
check("H3 video decodes (ffprobe)", dur > 0, f"{dur:.3f}s streams={[(s.get('codec_type'), s.get('width'), s.get('nb_frames'), s.get('r_frame_rate')) for s in st]}")
check("length is near the 5 s asked (frame-grid rounding allowed)", 4.5 <= dur <= 6.0, f"{dur:.3f}s")
print(f"\n{sum(RES)}/{len(RES)} passed")
