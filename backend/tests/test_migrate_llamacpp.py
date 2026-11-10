"""scripts/spark/migrate_to_llamacpp.py on a real database made by the app: the Sarness-style rows of an old install are moved to
the router backend without losing anything that refers to them, the second run is a no-op, and --rollback restores the old values."""
import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select

from semechka.models import (
    Backend,
    Conversation,
    Message,
    Model,
    ModelGrant,
    UsageEvent,
    User,
)

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "spark" / "migrate_to_llamacpp.py"
KEYTEXT = "router-key-not-to-be-printed"


@pytest.fixture
def old_install(owner, st, cfg, tmp_path):
    """The `owner` install (one openai_compat backend) plus Sarness-style model rows, a conversation and usage that name them."""
    with st.db.session(write=True) as s:
        b = s.execute(select(Backend)).scalar_one()
        b.adapter = "sarness"
        for real, alias, en in (("local:alpha-model@Q4_K_M", "Balanced", True), ("local:beta-model@UD-Q6_K", "beta", False),
                                ("local:orphan@Q8_0", "orphan", True), ("image:sdxl-turbo", "Sketch", True), ("endpoint:trinodal:trinodal", "Tri", True)):
            s.add(Model(backend_id=b.id, real_id=real, alias=alias, enabled=en, capabilities=["chat"], meta={"engine_name": "old"},
                        system_prompt="Be brief." if alias == "Balanced" else "", defaults={"temperature": 0.2} if alias == "Balanced" else {},
                        tags=["general"], pinned=alias == "Balanced", sort=7))
        s.flush()
        m = s.execute(select(Model).where(Model.alias == "Balanced")).scalar_one()
        uid_ = s.execute(select(User.id)).scalars().first()
        c = Conversation(user_id=uid_, title_enc=st.vault.encrypt("t", "title"), model_id=m.id)
        s.add(c)
        s.flush()
        s.add(Message(conversation_id=c.id, role="assistant", model_id=m.id, content_enc=st.vault.encrypt("old answer", "content")))
        s.add(UsageEvent(user_id=uid_, model_id=m.id, kind="chat", prompt_tokens=1, completion_tokens=1, tokens_source="backend", status="complete"))
        s.add(ModelGrant(model_id=m.id, subject_type="everyone", subject_id="", effect="allow"))
    spark = tmp_path / "spark-llm"
    spark.mkdir()
    (spark / "models.json").write_text(json.dumps([
        {"id": "alpha-model", "legacy_id": "alpha-model@Q4_K_M", "file": "/x/a.gguf", "ctx": 32768, "parallel": 4, "vision": False, "reasoning": False,
         "context_max": 262144, "est_mem_mb": 3900},
        {"id": "beta-model", "legacy_id": "beta-model@UD-Q6_K", "file": "/x/b.gguf", "ctx": 65536, "parallel": 2, "vision": True, "reasoning": True,
         "context_max": 262144, "est_mem_mb": 34000}]))
    (spark / "models.verified.json").write_text(json.dumps({"models": {"alpha-model": {"loaded_memory_mib": 8105, "tool_roundtrip": True}}}))
    key = tmp_path / "api.key"
    key.write_text(KEYTEXT + "\n")
    return cfg.data_dir, spark, key


def migrate(data, spark, key, *extra):
    return subprocess.run([sys.executable, "-I", str(SCRIPT), str(data), "--spark-llm-dir", str(spark), "--api-key-file", str(key),
                           "--router-url", "http://127.0.0.1:18080/v1", *extra], capture_output=True, text=True)


def rows(data, sql):
    con = sqlite3.connect(f"file:{data}/semechka.db?mode=ro", uri=True)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def digest(data):
    con = sqlite3.connect(f"file:{data}/semechka.db?mode=ro", uri=True)
    try:
        return {t: hashlib.sha256(repr(con.execute(f'select * from "{t}" order by 1').fetchall()).encode()).hexdigest()
                for (t,) in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()


def test_dry_run_changes_nothing_and_says_what_it_would_do(old_install):
    data, spark, key = old_install
    before = (data / "semechka.db").read_bytes()
    p = migrate(data, spark, key)
    assert p.returncode == 0 and "DRY RUN" in p.stdout, p.stderr
    assert "local:alpha-model@Q4_K_M  ->  alpha-model" in p.stdout and "orphan" in p.stdout and "NO router equivalent" in p.stdout
    assert (data / "semechka.db").read_bytes() == before and not list(data.glob("*.rollback.json")) and not list(data.glob("semechka.db.pre-*"))
    assert KEYTEXT not in p.stdout + p.stderr


def test_apply_moves_models_keeping_every_reference(old_install, st):
    data, spark, key = old_install
    before = digest(data)
    p = migrate(data, spark, key, "--apply")
    assert p.returncode == 0, p.stderr
    assert KEYTEXT not in p.stdout + p.stderr
    after = digest(data)
    changed = {t for t in before if before[t] != after[t]}
    assert changed <= {"backends", "models", "audit_events", "setting_values", "setting_revisions"}, changed  # users, sessions, conversations... untouched
    new_b = rows(data, "select id, adapter, base_url, api_key_enc from backends where adapter='llamacpp'")
    assert len(new_b) == 1 and new_b[0][2] == "http://127.0.0.1:18080/v1"
    assert KEYTEXT not in new_b[0][3] and new_b[0][3].startswith("v1:")
    assert st.vault.decrypt(new_b[0][3], "backend") == KEYTEXT  # stored with the instance's own crypto
    by = {r[0]: r for r in rows(data, "select alias, real_id, backend_id, enabled, pinned, sort, system_prompt, defaults, id from models")}
    assert by["Balanced"][1] == "alpha-model" and by["Balanced"][2] == new_b[0][0] and by["Balanced"][3] == 1 and by["Balanced"][4] == 1
    assert by["Balanced"][6] == "Be brief." and json.loads(by["Balanced"][7]) == {"temperature": 0.2} and by["Balanced"][5] == 7
    assert by["beta"][1] == "beta-model" and by["beta"][3] == 0
    assert by["orphan"][1] == "local:orphan@Q8_0" and by["orphan"][2] != new_b[0][0]  # no router equivalent: left where it was
    assert by["Sketch"][1] == "image:sdxl-turbo" and by["Tri"][1] == "endpoint:trinodal:trinodal"
    caps = json.loads(rows(data, "select capabilities from models where alias='Balanced'")[0][0])
    meta = json.loads(rows(data, "select meta from models where alias='Balanced'")[0][0])
    assert caps == ["chat", "tools"] and meta["loaded_memory_mib"] == 8105 and meta["slot_ctx"] == 8192 and meta["router"] is True
    # the references still resolve: same model id on the conversation, message, usage event and grant
    mid_ = by["Balanced"][8]
    assert rows(data, "select count(*) from conversations where model_id=?".replace("?", repr(mid_)))[0][0] == 1
    assert rows(data, f"select count(*) from messages where model_id='{mid_}'")[0][0] == 1
    assert rows(data, f"select count(*) from usage_events where model_id='{mid_}'")[0][0] == 1
    assert rows(data, f"select count(*) from model_grants where model_id='{mid_}'")[0][0] == 1
    s = dict(rows(data, "select key, value from setting_values where key like 'embeddings.%'"))
    assert json.loads(s["embeddings.url"]) == {"v": "http://127.0.0.1:18080"} and json.loads(s["embeddings.model"]) == {"v": "qwen3-embedding-0.6b"}
    assert len(list(data.glob("semechka.db.pre-llamacpp-*"))) == 1 and len(list(data.glob("*.rollback.json"))) == 1


def test_second_apply_is_a_no_op(old_install):
    data, spark, key = old_install
    assert migrate(data, spark, key, "--apply").returncode == 0
    snap = digest(data)
    p = migrate(data, spark, key, "--apply")
    assert p.returncode == 0 and "models to move: 0" in p.stdout
    assert digest(data) == snap and len(list(data.glob("*.rollback.json"))) == 1


def test_rollback_restores_the_original_values_exactly(old_install):
    data, spark, key = old_install
    before = digest(data)
    assert migrate(data, spark, key, "--apply").returncode == 0
    rb = next(data.glob("*.rollback.json"))
    assert KEYTEXT not in rb.read_text()
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), str(data), "--rollback", str(rb)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    after = digest(data)
    for t in ("models", "backends", "setting_values", "users", "conversations", "messages", "model_grants", "usage_events", "sessions"):
        assert before[t] == after[t], t
    assert rows(data, "select count(*) from backends where adapter='llamacpp'")[0][0] == 0


def test_a_rollback_file_of_another_instance_is_refused(old_install, tmp_path):
    data, spark, key = old_install
    assert migrate(data, spark, key, "--apply").returncode == 0
    other = tmp_path / "other"
    other.mkdir()
    for f in ("semechka.db", "master.key"):
        (other / f).write_bytes((data / f).read_bytes())
    p = subprocess.run([sys.executable, "-I", str(SCRIPT), str(other), "--rollback", str(next(data.glob("*.rollback.json")))], capture_output=True, text=True)
    assert p.returncode != 0 and "belongs to" in p.stderr


def test_missing_models_json_stops_before_changing_anything(old_install, tmp_path):
    data, _, key = old_install
    p = migrate(data, tmp_path / "empty", key, "--apply")
    assert p.returncode != 0 and "models.json" in p.stderr and not list(data.glob("*.rollback.json"))
