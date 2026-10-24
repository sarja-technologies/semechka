"""Model discovery metadata, engine status and load/unload against the Sarness double (tests/fake_sarness.py).

The double follows engine 0.4.0's real behaviour where it matters for safety: loading a chat model stops the loaded
one even mid-reply, so Semechka must refuse; unload refuses mid-reply; image/video models are freed through
/v1/models/{id}/unload and refused while a job runs.
"""
import json

from semechka.models import AuditEvent, Model
from sqlalchemy import select

from . import fake_sarness as fs
from .conftest import make_user
from .test_media import grant, img


def models_by_real(st):
    with st.db.session() as s:
        return {m.real_id: (m.id, dict(m.meta or {})) for m in s.execute(select(Model)).scalars()}


def actions(st, prefix="model."):
    with st.db.session() as s:
        return [e.action for e in s.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars()
                if e.action.startswith(prefix)]


def mid(st, real):
    return models_by_real(st)[real][0]


def resync(api):
    bid = api.get("/api/admin/backends").json()[0]["id"]
    assert api.post(f"/api/admin/backends/{bid}/sync").status_code == 200


# ---- discovery -----------------------------------------------------------------------------------------
def test_discovery_reports_the_engines_real_metadata_and_no_server_paths(mo, st):
    ms = models_by_real(st)
    chat = ms["local:fake-chat"][1]
    assert chat["engine_name"] == "Fake chat · Q8_0" and chat["architecture"] == "qwen35" and chat["layers"] == 25
    assert chat["context"] == 4096 and chat["context_trained"] == 262144 and chat["default_ctx"] == 2048
    assert chat["quantization"] == "Q8_0" and chat["format"] == "GGUF" and chat["runtime"] == "llama.cpp"
    assert chat["license"] == "Apache-2.0" and chat["source_repo"] == "someone/Fake-Chat-GGUF"
    assert chat["vram_estimate_mb"] == 1500 and chat["reasoning"] is True and chat["loaded"] is False
    big = ms["local:fake-big"][1]
    assert "license" not in big and "source_repo" not in big  # not reported, so absent (never guessed)
    turbo = ms["image:fake-turbo"][1]
    assert turbo["architecture"] == "fakediffusion" and turbo["source_repo"] == "fakelab/fake-turbo"
    assert turbo["media"]["limits"]["max_side"] == 768
    every = json.dumps(mo.get("/api/admin/models").json()) + json.dumps(mo.get("/api/engines").json())
    assert "/srv/private" not in every


def test_discovery_still_works_with_an_engine_that_predates_capabilities(mo, st):
    fs.STATE["legacy"] = True
    resync(mo)
    ms = models_by_real(st)
    assert {"local:fake-chat", "local:fake-big", "image:fake-turbo", "video:fake-clip"} <= set(ms)
    assert ms["local:fake-chat"][1]["context"] == 4096 and "architecture" in ms["local:fake-chat"][1]
    assert "source_repo" not in ms["local:fake-chat"][1]


# ---- load / unload -------------------------------------------------------------------------------------
def test_load_and_unload_a_chat_model_with_audit_and_reconciled_state(mo, st):
    m = mid(st, "local:fake-chat")
    pre = mo.get(f"/api/admin/models/{m}/lifecycle").json()
    assert pre["state"] == "idle" and pre["load"]["allowed"] and pre["unload"]["noop"] == "Not loaded."
    r = mo.post(f"/api/admin/models/{m}/load")
    assert r.status_code == 200, r.text
    assert r.json()["operation"]["state"] == "done"
    assert fs.STATE["loads"] == ["fake-chat"]
    assert models_by_real(st)["local:fake-chat"][1]["loaded"] is True
    assert mo.get(f"/api/admin/models/{m}/lifecycle").json()["state"] == "loaded"
    again = mo.post(f"/api/admin/models/{m}/load").json()
    assert again["noop"] == "Already loaded." and fs.STATE["loads"] == ["fake-chat"]
    r = mo.post(f"/api/admin/models/{m}/unload")
    assert r.status_code == 200 and r.json()["operation"]["state"] == "done"
    assert fs.STATE["unloads"] == ["fake-chat"] and fs.STATE["llm"]["model_id"] is None
    assert models_by_real(st)["local:fake-chat"][1]["loaded"] is False
    assert actions(st) == ["model.load", "model.load.done", "model.unload", "model.unload.done"]


def test_loading_another_chat_model_is_refused_while_someone_is_getting_a_reply(mo, st):
    fs.STATE["llm"].update(state="ready", model_id="fake-big", active_streams=1)
    r = mo.post(f"/api/admin/models/{mid(st, 'local:fake-chat')}/load")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "busy"
    assert "cut them off" in r.json()["detail"]["message"]
    assert fs.STATE["loads"] == [] and fs.STATE["llm"]["model_id"] == "fake-big"
    r = mo.post(f"/api/admin/models/{mid(st, 'local:fake-big')}/unload")
    assert r.status_code == 409 and fs.STATE["unloads"] == []
    assert actions(st) == ["model.load.refused", "model.unload.refused"]


def test_this_servers_own_reply_in_progress_also_blocks_a_swap(mo, st):
    fs.STATE["llm"].update(state="ready", model_id="fake-big", active_streams=0)
    bid = mo.get("/api/admin/backends").json()[0]["id"]
    t = st.scheduler.submit(backend_id=bid, user_id="u_x", kind="chat", max_wait=0, model_id=mid(st, "local:fake-big"))
    try:
        assert t.state == "running"
        r = mo.post(f"/api/admin/models/{mid(st, 'local:fake-chat')}/load")
        assert r.status_code == 409 and "about to receive a reply" in r.json()["detail"]["message"]
    finally:
        t.release()
    assert mo.post(f"/api/admin/models/{mid(st, 'local:fake-chat')}/load").status_code == 200


def test_a_failed_load_is_reported_cleaned_and_audited(mo, st):
    fs.STATE["fail_load"] = True
    r = mo.post(f"/api/admin/models/{mid(st, 'local:fake-chat')}/load")
    assert r.status_code == 200
    op = r.json()["operation"]
    assert op["state"] == "failed" and "llama-server exited" in op["error"] and "/srv/private" not in op["error"]
    assert actions(st)[-1] == "model.load.failed"
    eng = mo.get("/api/engines").json()["engines"][0]
    assert eng["operation"]["state"] == "failed" and eng["status"]["llm"]["state"] == "error"


def test_image_models_are_freed_but_never_loaded_ahead_of_time(mo, st):
    turbo = mid(st, "image:fake-turbo")
    r = mo.post(f"/api/admin/models/{turbo}/load")
    assert r.status_code == 501 and r.json()["detail"]["code"] == "unsupported"
    assert mo.post(f"/api/admin/models/{turbo}/unload").json()["noop"] == "Not loaded."
    fs.STATE["worker_loaded"].add("fake-turbo")
    fs.STATE["jobs"]["j_busy"] = {"id": "j_busy", "kind": "image", "status": "running", "simple": {"model": "fake-turbo"},
                                  "polls": 0, "progress": 0.2, "generations": []}
    r = mo.post(f"/api/admin/models/{turbo}/unload")
    assert r.status_code == 409 and "image or video job" in r.json()["detail"]["message"]
    fs.STATE["jobs"]["j_busy"]["status"] = "completed"
    r = mo.post(f"/api/admin/models/{turbo}/unload")
    assert r.status_code == 200 and fs.STATE["worker_loaded"] == set() and fs.STATE["unloads"] == ["fake-turbo"]


# ---- engine status and queue ---------------------------------------------------------------------------
def test_engine_queue_shows_other_clients_jobs_without_their_content(mo, st):
    assert img(mo, prompt="slow own job").status_code == 200
    fs.STATE["jobs"]["j_foreign"] = {"id": "j_foreign", "kind": "video", "status": "queued", "polls": 0,
                                     "simple": {"model": "fake-clip", "prompt": "a confidential foreign prompt"},
                                     "title": "someone else's title", "generations": []}
    eng = mo.get("/api/engines").json()["engines"][0]
    text = json.dumps(eng)
    assert "confidential" not in text and "someone else" not in text and "slow own job" not in text
    own = [j for j in eng["queue"] if j["own"]]
    foreign = [j for j in eng["queue"] if not j["own"]]
    assert len(own) == 1 and own[0]["id"] and own[0]["model_ref"]["alias"] == "fake-turbo"
    assert foreign == [{**foreign[0], "id": None}] and foreign[0]["model"] == "fake-clip"
    assert eng["status"]["memory"]["total_mb"] == 120000 and eng["supports"] == {
        "load": True, "unload": True, "status": True, "queue": True}


# ---- permissions (server-side; the UI never decides) ---------------------------------------------------
def test_people_without_the_permission_cannot_load_unload_or_read_engine_state(mo, st, app):
    m = mid(st, "local:fake-chat")
    member = make_user(mo, app, "member@example.test")
    for call in (lambda: member.post(f"/api/admin/models/{m}/load"), lambda: member.post(f"/api/admin/models/{m}/unload"),
                 lambda: member.get(f"/api/admin/models/{m}/lifecycle"), lambda: member.get("/api/engines")):
        assert call().status_code == 403
    grant(mo, "member", ["models.status.view"])  # seeing loaded state is not operating
    assert member.post(f"/api/admin/models/{m}/load").status_code == 403
    assert fs.STATE["loads"] == [] and not any(a.startswith("model.load") for a in actions(st))


def test_queue_operator_sees_the_queue_but_cannot_load(mo, st, app):
    q = make_user(mo, app, "queue@example.test", role="queue_operator")
    eng = q.get("/api/engines").json()["engines"][0]
    assert eng["queue"] == [] and "llm" not in eng["status"]  # no models.status.view
    assert q.post(f"/api/admin/models/{mid(st, 'local:fake-chat')}/load").status_code == 403
