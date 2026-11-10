"""scripts/spark/migrate_media_to_engine.py: dry run, apply, idempotence, rollback and the things it must never touch (a real SQLite database built by the app)."""
import json
import pathlib
import sqlite3
import subprocess
import sys

import pytest

from semechka.config import Config
from semechka.media_models import MediaAsset, MediaJob
from semechka.models import Backend, Model, ModelGrant, User
from semechka.state import AppState

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "spark" / "migrate_media_to_engine.py"
TOKEN = "tok-THIS-MUST-NEVER-BE-PRINTED-123"


def caps():
    def m(i, kind, **kw):
        return {"id": i, "kind": kind, "name": i, "supported": True, "modes": ["txt2img"], "audio": False, "negative_prompt": False, "schedulers": [], "references": 0,
                "defaults": {"width": 1024, "height": 1024}, "limits": {"min_side": 512, "max_side": 2048, "multiple": 16}, "vram_estimate_mb": 100, **kw}
    return {"api_version": 1, "models": [m("z-image-turbo", "image"), m("flux2-klein-4b", "image"), m("krea2-turbo", "image"), m("wan2.2-ti2v-5b", "video", modes=["t2v"])],
            "profiles": [{"id": "opensfield-video-edit", "name": "Edit", "family": "genjutsu", "category": "video", "readiness": {"ready": True, "state": "ready"},
                          "capabilities": {"operations": ["restyle"]}, "inputs_schema": {"video": {"type": "file", "kind": "video", "required": True}}}],
            "unavailable": [{"id": "sdxl-turbo", "kind": "image", "reason": "Diffusers only on the old engine."},
                            {"id": "openjutsu-minimax-h3", "kind": "video", "reason": "not an OpenSfield workflow"}]}


@pytest.fixture()
def inst(tmp_path):
    data = tmp_path / "inst"
    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.session(write=True) as s:
        s.add(User(id="u_1", email="a@x.test", display_name="A", password_hash="h"))
        old = Backend(id="b_old", name="Sarness", adapter="sarness", base_url="http://127.0.0.1:8752")
        s.add(old)
        s.flush()
        rows = [("m_chat", "local:qwen3", True), ("m_endpoint", "endpoint:trinodal:trinodal", True),
                ("m_zi", "image:z-image-turbo", False), ("m_klein", "image:flux2-klein-4b", True), ("m_sdxl", "image:sdxl-turbo", True),
                ("m_clip", "video:wan2.2-ti2v-5b", True), ("m_oj", "video:openjutsu-minimax-h3", True)]
        for mid, rid, en in rows:
            s.add(Model(id=mid, backend_id="b_old", real_id=rid, alias=rid.split(":")[-1], enabled=en, capabilities=["chat"] if rid[:3] not in ("ima", "vid") else [rid[:5]],
                        meta={"media": {"kind": rid[:5], "limits": {"old": True}}} if rid[:3] in ("ima", "vid") else {"context": 4096}, tags=["keep"], sort=3))
        s.add_all([ModelGrant(model_id="m_klein", subject_type="role", subject_id="member", effect="allow", modes=["image"]),
                   ModelGrant(model_id="m_clip", subject_type="everyone", subject_id="*", effect="allow", modes=None),
                   ModelGrant(model_id="m_oj", subject_type="role", subject_id="owner", effect="allow", modes=["video"]),
                   ModelGrant(model_id="m_oj", subject_type="user", subject_id="u_1", effect="deny", modes=None),
                   ModelGrant(model_id="m_chat", subject_type="everyone", subject_id="*", effect="allow", modes=None)])
        s.add(MediaJob(id="mj_done", user_id="u_1", kind="image", model_id="m_klein", backend_id="b_old", remote_id="r1", status="completed"))
        s.add(MediaAsset(id="ma_1", user_id="u_1", job_id="mj_done", kind="image", model_id="m_klein", mime="image/png", size=10))
    (data / "caps.json").write_text(json.dumps(caps()))
    (data / "token").write_text(TOKEN + "\n")
    return data


def run(data, *args, expect=0):
    r = subprocess.run([sys.executable, str(SCRIPT), str(data), "--catalog-file", str(data / "caps.json"), "--token-file", str(data / "token"), *args],
                       capture_output=True, text=True, env={"PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1]), "PATH": "/usr/bin:/bin"})
    assert r.returncode == expect, r.stdout + r.stderr
    assert TOKEN not in r.stdout + r.stderr
    return r.stdout


def table(data, sql):
    con = sqlite3.connect(data / "semechka.db")
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql)]
    finally:
        con.close()


def snapshot(data):
    return {t: table(data, f"select * from {t} order by 1") for t in ("backends", "models", "model_grants", "media_jobs", "media_assets", "users")}


def only(data, mid):
    return table(data, f"select * from models where id='{mid}'")[0]


def test_dry_run_writes_nothing(inst):
    before = snapshot(inst)
    out = run(inst)
    assert "DRY RUN" in out and "move + refresh" in out and "mark unavailable" in out
    assert snapshot(inst) == before
    assert not list(inst.glob("pre-update-media-engine-*"))


def test_apply_moves_rows_keeps_ids_grants_and_flags_the_unavailable(inst):
    before = snapshot(inst)
    run(inst, "--apply")
    be = table(inst, "select * from backends where adapter='spark-media'")
    assert len(be) == 1 and be[0]["base_url"] == "http://127.0.0.1:8780" and be[0]["api_key_enc"] and TOKEN not in be[0]["api_key_enc"]
    from semechka.crypto import Vault, load_or_create_master_key
    assert Vault(load_or_create_master_key(inst / "master.key")).decrypt(be[0]["api_key_enc"], "backend") == TOKEN
    nb = be[0]["id"]
    k = only(inst, "m_klein")
    assert k["backend_id"] == nb and k["enabled"] == 1 and k["tags"] == '["keep"]' and k["alias"] == "flux2-klein-4b" and k["available"] == 1
    assert json.loads(k["meta"])["media"]["limits"]["multiple"] == 16 and json.loads(k["capabilities"]) == ["image"]
    z = only(inst, "m_zi")
    assert z["backend_id"] == nb and z["enabled"] == 0
    s = only(inst, "m_sdxl")
    assert s["backend_id"] == nb and s["available"] == 0 and s["enabled"] == 1                        # moved, flagged, never deleted
    assert json.loads(s["meta"])["media"]["unavailable_reason"] == "Diffusers only on the old engine." and json.loads(s["meta"])["media"]["limits"] == {"old": True}
    assert only(inst, "m_oj")["available"] == 0
    # same ids and grants
    assert table(inst, "select * from model_grants order by id")[:5] == before["model_grants"]
    # new engine models: disabled, no grants
    new = table(inst, "select * from models where id not in (%s)" % ",".join(f"'{m['id']}'" for m in before["models"]))
    assert {m["real_id"] for m in new} == {"image:krea2-turbo", "video:opensfield-video-edit"} and all(m["enabled"] == 0 and m["backend_id"] == nb for m in new)
    assert len(table(inst, "select * from model_grants")) == len(before["model_grants"])
    # never touched
    after = snapshot(inst)
    for t in ("media_jobs", "media_assets", "users"):
        assert after[t] == before[t]
    for mid in ("m_chat", "m_endpoint"):
        assert only(inst, mid) == next(m for m in before["models"] if m["id"] == mid)
    assert next(b for b in after["backends"] if b["id"] == "b_old") == before["backends"][0]
    assert list(inst.glob("pre-update-media-engine-*/semechka.db")) and list(inst.glob("pre-update-media-engine-*/rollback.json"))


def test_second_apply_changes_nothing(inst):
    run(inst, "--apply")
    snap = snapshot(inst)
    n = len(list(inst.glob("pre-update-media-engine-*")))
    assert "Nothing to change" in run(inst, "--apply")
    assert snapshot(inst) == snap and len(list(inst.glob("pre-update-media-engine-*"))) == n


def test_grant_profiles_copies_the_openjutsu_grants_and_rollback_undoes_everything(inst):
    before = snapshot(inst)
    run(inst, "--apply", "--grant-profiles")
    pe = table(inst, "select * from models where real_id='video:opensfield-video-edit'")[0]
    assert pe["enabled"] == 1
    g = table(inst, f"select subject_type,subject_id,effect,modes from model_grants where model_id='{pe['id']}' order by id")
    assert g == [{"subject_type": "role", "subject_id": "owner", "effect": "allow", "modes": '["video"]'}, {"subject_type": "user", "subject_id": "u_1", "effect": "deny", "modes": "null"}]
    rb = next(inst.glob("pre-update-media-engine-*/rollback.json"))
    out = run(inst, "--rollback", str(rb))
    assert "restored to the backed-up state: YES" in out
    assert snapshot(inst) == before
    assert "restored to the backed-up state: YES" in run(inst, "--rollback", str(rb))                    # idempotent
    assert snapshot(inst) == before
    run(inst, "--apply")                                                                                     # and it can be migrated again afterwards
    assert only(inst, "m_klein")["backend_id"] != "b_old"


def test_refuses_while_a_media_job_is_running(inst):
    con = sqlite3.connect(inst / "semechka.db")
    con.execute("insert into media_jobs (id,user_id,kind,model_id,backend_id,remote_id,status,save,progress_text,created_at,last_ok,cancel_requested) values ('mj_run','u_1','video','m_clip','b_old','r','running',1,'',0,0,0)")
    con.commit()
    con.close()
    before = snapshot(inst)
    out = run(inst, "--apply", expect=2)
    assert "REFUSED" in out and "mj_run" in out
    assert snapshot(inst) == before and not list(inst.glob("pre-update-media-engine-*"))
    run(inst, "--apply", "--allow-active")


def test_an_instance_without_a_token_file_cannot_be_applied_but_can_be_planned(inst):
    r = subprocess.run([sys.executable, str(SCRIPT), str(inst), "--catalog-file", str(inst / "caps.json"), "--token-file", str(inst / "absent")], capture_output=True, text=True,
                       env={"PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1]), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and "DRY RUN" in r.stdout
    r = subprocess.run([sys.executable, str(SCRIPT), str(inst), "--apply", "--catalog-file", str(inst / "caps.json"), "--token-file", str(inst / "absent")], capture_output=True, text=True,
                       env={"PYTHONPATH": str(pathlib.Path(__file__).resolve().parents[1]), "PATH": "/usr/bin:/bin"})
    assert r.returncode != 0 and "no engine token" in r.stderr + r.stdout
    assert not table(inst, "select * from backends where adapter='spark-media'")


# ---------------------------------------------------------------- refresh of an instance that was migrated earlier (restored Imajutsu modes)
def newer_caps():
    c = caps()
    for m in c["models"]:
        if m["id"] == "flux2-klein-4b":
            m.update(modes=["txt2img", "edit", "inpaint"], references=4, mode_pictures={"txt2img": 0, "edit": 4, "inpaint": 4})
        if m["id"] == "z-image-turbo":
            m.update(modes=["txt2img", "img2img"])
    return c


def test_a_later_run_refreshes_rows_already_on_the_engine_without_touching_flags_or_grants_and_rolls_back(inst):
    run(inst, "--apply")
    before = snapshot(inst)
    (inst / "caps.json").write_text(json.dumps(newer_caps()))
    out = run(inst)
    assert "REFRESH" in out and "image:flux2-klein-4b" in out and "DRY RUN" in out
    assert snapshot(inst) == before                                                                          # dry run writes nothing
    run(inst, "--apply")
    k = only(inst, "m_klein")
    assert json.loads(k["meta"])["media"]["modes"] == ["txt2img", "edit", "inpaint"] and json.loads(k["meta"])["media"]["references"] == 4
    assert json.loads(k["meta"])["media"]["mode_pictures"]["edit"] == 4
    assert json.loads(only(inst, "m_zi")["meta"])["media"]["modes"] == ["txt2img", "img2img"]
    after = snapshot(inst)
    for t in ("model_grants", "media_jobs", "media_assets", "users"):
        assert after[t] == before[t]
    for mid in ("m_klein", "m_zi", "m_sdxl"):
        a, b = only(inst, mid), next(m for m in before["models"] if m["id"] == mid)
        assert {k_: v for k_, v in a.items() if k_ not in ("meta", "capabilities")} == {k_: v for k_, v in b.items() if k_ not in ("meta", "capabilities")}
    assert "Nothing to change" in run(inst, "--apply")                                                       # idempotent
    rb = sorted(inst.glob("pre-update-media-engine-*/rollback.json"))[-1]
    assert "restored to the backed-up state: YES" in run(inst, "--rollback", str(rb))
    assert snapshot(inst) == before


def test_enable_models_enables_and_gives_them_the_grants_of_the_named_analogue_and_rolls_back(inst):
    run(inst, "--apply")
    before = snapshot(inst)
    assert only(inst, "m_zi")["enabled"] == 0
    run(inst, "--apply", "--enable-models", "image:z-image-turbo", "--like", "image:flux2-klein-4b")
    assert only(inst, "m_zi")["enabled"] == 1
    g = table(inst, "select subject_type,subject_id,effect,modes from model_grants where model_id='m_zi'")
    assert g == [{"subject_type": "role", "subject_id": "member", "effect": "allow", "modes": '["image"]'}]
    assert len(table(inst, "select * from model_grants")) == len(before["model_grants"]) + 1
    assert only(inst, "m_klein") == next(m for m in snapshot(inst)["models"] if m["id"] == "m_klein")
    assert "Nothing to change" in run(inst, "--apply", "--enable-models", "image:z-image-turbo", "--like", "image:flux2-klein-4b")
    rb = sorted(inst.glob("pre-update-media-engine-*/rollback.json"))[-1]
    assert "restored to the backed-up state: YES" in run(inst, "--rollback", str(rb))
    assert snapshot(inst) == before


def test_restore_re_enables_rows_parked_unavailable_when_the_engine_offers_them_again_and_rolls_back(inst):
    """An instance migrated before OpenJutsu / Wan 14B text-to-video came back: its parked rows become available again, with the enabled flag and grants untouched."""
    run(inst, "--apply")                                              # first migration: the OpenJutsu row is parked unavailable
    assert only(inst, "m_oj")["available"] == 0
    c = caps()
    c["unavailable"] = [u for u in c["unavailable"] if u["id"] != "openjutsu-minimax-h3"]
    c["profiles"].append({"id": "openjutsu-minimax-h3", "name": "OpenJutsu", "family": "openjutsu", "category": "video", "readiness": {"ready": True, "state": "experimental"},
                          "capabilities": {"operations": ["swap"]}, "inputs_schema": {"video": {"type": "file", "kind": "video", "required": True}},
                          "modes": [{"id": "character_replacement", "operation": "swap", "status": "ready"}], "limits": {"max_source_seconds": 30},
                          "reference_policy": {"swap": {"min": 1, "active_max": 1}}, "subject_selection": {"text": True}, "operation_status": {"swap": {"status": "ready"}}})
    (inst / "caps.json").write_text(json.dumps(c))
    before = snapshot(inst)
    out = run(inst)                                                   # dry run
    assert "RESTORE m_oj" in out and snapshot(inst) == before
    run(inst, "--apply")
    oj = only(inst, "m_oj")
    prof = json.loads(oj["meta"])["media"]["profile"]
    assert oj["available"] == 1 and oj["enabled"] == 1 and "unavailable_reason" not in json.loads(oj["meta"])["media"]
    assert prof["modes"][0]["id"] == "character_replacement" and prof["limits"]["max_source_seconds"] == 30 and prof["reference_policy"]["swap"]["active_max"] == 1
    assert table(inst, "select * from model_grants order by id") == before["model_grants"]          # grants untouched
    assert only(inst, "m_sdxl")["available"] == 0                                                   # still unavailable: the engine still lacks it
    assert "Nothing to change" in run(inst, "--apply")
    rb = sorted(inst.glob("pre-update-media-engine-*/rollback.json"))[-1]
    run(inst, "--rollback", str(rb))
    assert only(inst, "m_oj")["available"] == 0


def test_grant_profiles_on_a_later_run_may_grant_a_profile_row_an_earlier_run_created(inst):
    run(inst, "--apply")                                                        # the profile row is added disabled, no grants
    pe = table(inst, "select * from models where real_id='video:opensfield-video-edit'")[0]
    assert pe["enabled"] == 0 and not table(inst, f"select * from model_grants where model_id='{pe['id']}'")
    run(inst, "--apply", "--grant-profiles")                                    # a later run copies the grants and enables it: not an invariant violation
    pe = table(inst, "select * from models where real_id='video:opensfield-video-edit'")[0]
    assert pe["enabled"] == 1 and len(table(inst, f"select * from model_grants where model_id='{pe['id']}'")) == 2


def test_engine_tools_are_added_disabled_and_the_non_media_invariant_still_holds(inst):
    c = caps()
    c["tools"] = [{"id": "kokoro-tts", "name": "Speech", "group": "audio", "ready": True, "inputs_schema": {}}]
    (inst / "caps.json").write_text(json.dumps(c))
    before = snapshot(inst)
    out = run(inst, "--apply")
    assert "tool:kokoro-tts" in out
    after = snapshot(inst)
    assert after["users"] == before["users"] and after["model_grants"] == before["model_grants"]
    t = next(m for m in after["models"] if m["real_id"] == "tool:kokoro-tts")
    assert t["enabled"] == 0
    assert "tool:kokoro-tts" not in run(inst, "--apply") or "nothing to do" in run(inst, "--apply")          # idempotent
