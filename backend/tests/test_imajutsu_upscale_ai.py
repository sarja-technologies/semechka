"""AI upscaling (SeedVR2 / Real-ESRGAN on the Spark through OpenSfield's narrow upscale API): the contract, the honest labelling, and every failure path.

OpenSfield is replaced by an in-process fake (httpx.MockTransport) that implements the same routes and checks the same token header; nothing touches a GPU."""
import json

import httpx
import pytest

from semechka import opensfield_upscale as osf

from .imajutsu_helpers import content, gradient, mkproject, png_of, pytestmark_ffmpeg, solid, up, wait_task

pytestmark = pytestmark_ffmpeg
TOKEN = "upscale-token-for-tests"


class FakeOsf:
    """The four routes Semechka uses. ``job_polls`` = how many status calls answer 'running' before 'done'."""

    def __init__(self):
        self.calls, self.jobs, self.fail_with, self.job_polls, self.ready = [], {}, None, 2, {"seedvr2": True, "esrgan": True}
        self.caps_override = {}

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.calls.append((req.method, req.url.path, req.headers.get("x-osf-upscale")))
        if req.headers.get("x-osf-upscale") != TOKEN:
            return httpx.Response(401, json={"detail": "upscale token required"})
        p = req.url.path
        if p.endswith("/capabilities"):
            eng = [{"id": e, "label": e, "kind": "generative" if e == "seedvr2" else "cnn", "note": f"AI model ({e}).", "ready": self.ready[e],
                    "reason": None if self.ready[e] else "model weights are not installed", "max_scale_per_pass": 4} for e in ("seedvr2", "esrgan")]
            return httpx.Response(200, json={"engines": eng, "max_output_pixels": 90_000_000, "max_edge": 12288, "max_source_pixels": 64_000_000,
                                             "esrgan_max_source_pixels": 5_625_000, **self.caps_override})
        if req.method == "POST" and p == "/api/internal/upscale":
            if self.fail_with:
                return httpx.Response(422, json={"detail": self.fail_with})
            body = req.content.decode("latin1")
            form = {k: body.split(f'name="{k}"\r\n\r\n', 1)[1].split("\r\n", 1)[0] for k in ("engine", "width", "height", "fmt")}
            jid = f"job{len(self.jobs) + 1}"
            self.jobs[jid] = {**form, "polls": 0, "deleted": False, "size_bytes": len(req.content)}
            return httpx.Response(202, json={"job": jid, "upload": "u.png"})
        jid = p.rstrip("/").split("/")[4] if p.count("/") >= 4 else None
        j = self.jobs.get(jid or "")
        if j is None:
            return httpx.Response(404, json={"detail": "unknown upscale job"})
        if req.method == "DELETE":
            j["deleted"] = True
            return httpx.Response(204)
        if p.endswith("/result"):
            w, h = int(j["width"]), int(j["height"])
            return httpx.Response(200, content=png_of(solid(w, h, (90, 140, 200))))
        if p.endswith("/cancel"):
            return httpx.Response(200, json={})
        j["polls"] += 1
        if j["polls"] <= self.job_polls:
            return httpx.Response(200, json={"id": jid, "status": "running", "phase": "generating", "progress": 0.3 * j["polls"]})
        return httpx.Response(200, json={"id": jid, "status": "done", "phase": "completed", "progress": 1.0,
                                         "result": {"name": "x.png", "width": int(j["width"]), "height": int(j["height"]), "format": j["fmt"],
                                                    "honest": f"AI model ({j['engine']}). Re-draws fine detail.", "passes": [2]}})


@pytest.fixture()
def osfake(tmp_path, monkeypatch):
    tok = tmp_path / "osf-upscale.token"
    tok.write_text(TOKEN + "\n")
    f = FakeOsf()
    monkeypatch.setenv("SEMECHKA_OPENSFIELD_UPSCALE_TOKEN_FILE", str(tok))
    monkeypatch.setattr(osf, "make_client", lambda timeout=30.0: httpx.AsyncClient(transport=httpx.MockTransport(f.handler), base_url="http://osf.test", timeout=timeout))
    monkeypatch.setattr(osf, "POLL_S", 0.01)
    osf._CAPS.update(at=0.0, value=None)
    return f


def test_engines_list_says_what_can_run_and_why_not(im, osfake, monkeypatch):
    r = im.get("/api/imajutsu/upscale/engines").json()["engines"]
    by = {e["id"]: e for e in r}
    assert by["lanczos"]["ready"] and by["lanczos"]["ai"] is False and "not AI" in by["lanczos"]["note"]
    assert by["seedvr2"]["ready"] and by["seedvr2"]["ai"] and by["seedvr2"]["max_pixels"] == 90_000_000 and by["esrgan"]["max_scale"] == 4
    osfake.ready["esrgan"] = False
    osf._CAPS.update(at=0.0, value=None)
    by = {e["id"]: e for e in im.get("/api/imajutsu/upscale/engines").json()["engines"]}
    assert not by["esrgan"]["ready"] and "not installed" in by["esrgan"]["reason"]
    monkeypatch.delenv("SEMECHKA_OPENSFIELD_UPSCALE_TOKEN_FILE")
    osf._CAPS.update(at=0.0, value=None)
    by = {e["id"]: e for e in im.get("/api/imajutsu/upscale/engines").json()["engines"]}
    assert by["lanczos"]["ready"] and not by["seedvr2"]["ready"] and "not set up" in by["seedvr2"]["reason"]


def test_ai_upscale_makes_a_labelled_new_asset_at_the_exact_size(im, osfake, tmp_path):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    before = content(im, a["id"])
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600, "engine": "seedvr2"})
    assert r.status_code == 200 and r.json()["ai"] is True and r.json()["method"] == "seedvr2" and r.json()["scaling"] == "upscale"
    t = wait_task(im, r.json()["task"])
    assert t["status"] == "completed", t
    new = t["asset"]
    assert (new["width"], new["height"]) == (900, 600) and new["origin"] == "upscaled" and new["parent"] == a["id"]
    block = new["imajutsu"]
    assert block["kind"] == "upscaled" and block["ai"] is True and block["method"] == "seedvr2" and block["label"].startswith("SeedVR2") and "Re-draws" in block["honest"]
    assert new["name"] == "small (900x600, SeedVR2).png" and content(im, a["id"]) == before                    # the original is untouched
    sent = list(osfake.jobs.values())[0]
    assert (sent["engine"], sent["width"], sent["height"]) == ("seedvr2", "900", "600")
    assert all(tok == TOKEN for _m, _p, tok in osfake.calls) and next(iter(osfake.jobs.values()))["deleted"]       # token on every call; the copy on the Spark is freed
    assert [x["id"] for x in im.get("/api/media/assets", params={"origin": "upscaled"}).json()] == [new["id"]]
    assert im.get("/api/media/assets", params={"origin": "scaled"}).json() == []                               # AI results never pose as plain scaling


def test_lanczos_stays_the_default_and_never_calls_opensfield(im, osfake):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 600, "height": 400})
    assert r.json()["ai"] is False and r.json()["method"] == "lanczos"
    assert wait_task(im, r.json()["task"])["asset"]["origin"] == "scaled" and osfake.calls == []


def test_large_ai_results_are_kept_as_jpeg(im, osfake, monkeypatch):
    from semechka import imajutsu_svc
    monkeypatch.setattr(imajutsu_svc, "AI_JPEG_ABOVE_PIXELS", 150_000)           # "large" for this test = anything above 0.15 MP
    a = up(im, png_of(gradient(300, 200)), "small.png")
    t = wait_task(im, im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600, "engine": "seedvr2"}).json()["task"])
    assert t["status"] == "completed" and list(osfake.jobs.values())[0]["fmt"] == "JPEG"
    t2 = wait_task(im, im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 450, "height": 300, "engine": "seedvr2"}).json()["task"])
    assert t2["status"] == "completed" and list(osfake.jobs.values())[1]["fmt"] == "PNG"


@pytest.mark.parametrize("body,code,word", [
    ({"width": 900, "height": 900, "engine": "seedvr2"}, 422, "different shape"),
    ({"width": 150, "height": 100, "engine": "seedvr2"}, 422, "only makes a picture larger"),
    ({"width": 300, "height": 200, "engine": "seedvr2"}, 422, "already"),
    ({"width": 900, "height": 600, "engine": "magic"}, 422, "engine"),
    ({"width": 30000, "height": 20000, "engine": "seedvr2"}, 422, ""),
])
def test_refusals_are_sentences_and_nothing_is_sent(im, osfake, body, code, word):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json=body)
    assert r.status_code == code and word in json.dumps(r.json()), r.text
    assert not [c for c in osfake.calls if c[0] == "POST"]


def test_not_configured_or_not_ready_is_503_with_a_reason(im, osfake, monkeypatch):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    osfake.ready["seedvr2"] = False
    osf._CAPS.update(at=0.0, value=None)
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600, "engine": "seedvr2"})
    assert r.status_code == 503 and "not ready" in json.dumps(r.json())
    monkeypatch.delenv("SEMECHKA_OPENSFIELD_UPSCALE_TOKEN_FILE")
    osf._CAPS.update(at=0.0, value=None)
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600, "engine": "esrgan"})
    assert r.status_code == 503 and "not set up" in json.dumps(r.json())


def test_opensfield_refusing_the_job_fails_the_task_with_its_sentence(im, osfake):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    osfake.fail_with = "Real-ESRGAN always enlarges 4× first, so it handles pictures up to about 5.6 megapixels."
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600, "engine": "esrgan"})
    t = wait_task(im, r.json()["task"])
    assert t["status"] == "failed" and "5.6 megapixels" in t["error"]


def test_opensfield_down_mid_upscale_is_a_plain_failure_and_not_a_hang(im, osfake):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    base = osfake.handler

    def flaky(req):
        if req.url.path.endswith("/result") or (req.method == "GET" and "job1" in req.url.path):
            raise httpx.ConnectError("boom")
        return base(req)
    osfake.handler = flaky
    osf.make_client = lambda timeout=30.0: httpx.AsyncClient(transport=httpx.MockTransport(osfake.handler), base_url="http://osf.test", timeout=timeout)
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600, "engine": "seedvr2"})
    t = wait_task(im, r.json()["task"])
    assert t["status"] == "failed" and "stopped answering" in t["error"]


def test_plan_for_an_ai_upscale_is_labelled_and_enforces_the_engine_limits(im, osfake):
    src = up(im, png_of(gradient(360, 640)), "small.png")
    proj = mkproject(im, "upscale", {"source_asset": src["id"], "aspect": {"id": "source"}, "size": {"native": None, "output": {"width": 1440, "height": 2560}},
                                     "upscale": {"refine": False, "engine": "seedvr2"}})
    pl = im.post(f"/api/imajutsu/projects/{proj['id']}/plan", json={}).json()["plan"]
    assert pl["scaling"]["ai"] is True and pl["scaling"]["method"] == "seedvr2" and pl["model"] is None and not pl["errors"]
    assert "check the result" in pl["output"]["note"] and "AI model (seedvr2)" in pl["output"]["note"]
    r = im.post("/api/imajutsu/projects", json={"mode": "upscale"})
    r = im.put(f"/api/imajutsu/projects/{r.json()['id']}", json={"spec": {"mode": "upscale", "source_asset": src["id"], "aspect": {"id": "source"},
                                                                          "size": {"native": None, "output": {"width": 12000, "height": 12000}},
                                                                          "upscale": {"refine": False, "engine": "seedvr2"}}})
    assert r.status_code == 422 and "upscales reliably" in json.dumps(r.json())          # beyond ~90 MP: refused when the settings are saved, with the real reason
    es = mkproject(im, "upscale", {"source_asset": src["id"], "aspect": {"id": "source"}, "size": {"native": None, "output": {"width": 2160, "height": 3840}},
                                   "upscale": {"refine": False, "engine": "esrgan"}})
    p3 = im.post(f"/api/imajutsu/projects/{es['id']}/plan", json={}).json()["plan"]
    assert any("4×" in e for e in p3["errors"])                    # 6x asked of Real-ESRGAN
    plain = mkproject(im, "upscale", {"source_asset": src["id"], "aspect": {"id": "source"}, "size": {"native": None, "output": {"width": 720, "height": 1280}}})
    p4 = im.post(f"/api/imajutsu/projects/{plain['id']}/plan", json={}).json()["plan"]
    assert p4["scaling"]["ai"] is False and "not AI" in p4["output"]["note"]


def test_the_token_is_never_returned_by_any_endpoint(im, osfake):
    for path in ("/api/imajutsu/upscale/engines",):
        assert TOKEN not in json.dumps(im.get(path).json())


def test_the_name_sent_to_opensfield_always_has_the_picture_extension():
    png, jpg = b"\x89PNG\r\n\x1a\n" + b"0" * 20, b"\xff\xd8\xff\xe0" + b"0" * 20
    assert osf.wire_name(png, "Z-Image: a red bicycle") == "Z-Image: a red bicycle.png"
    assert osf.wire_name(jpg, "street.png") == "street.jpg" and osf.wire_name(png, "street.png") == "street.png"
    assert osf.wire_name(b"RIFF....WEBPVP8 ", "x") == "x.webp" and osf.wire_name(b"unknown", "x") == "x"
