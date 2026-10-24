"""The model picker API: naming, grouping, sorting, favourites, seeing every model versus using it, live state, logos.

Arranging models is presentation; every use is still decided by services.model_access, which these tests probe
through the generation endpoint directly."""
from semechka import catalog_svc
from semechka.models import Model
from sqlalchemy import select

from . import fake_sarness as fs
from .conftest import make_user
from .test_media import grant


def picker(api, **kw):
    r = api.get("/api/models/catalog", params={"operation": "text", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def by_alias(cat):
    return {m["alias"]: m for m in cat["models"]}


def ids(st):
    with st.db.session() as s:
        return {m.alias: m.id for m in s.execute(select(Model)).scalars()}


# ---- what an owner sees ----------------------------------------------------------------------------------
def test_owner_sees_every_usable_model_with_real_names_details_and_live_state(mo, st):
    cat = picker(mo)
    m = by_alias(cat)
    assert list(m) == ["fake-chat", "fake-big"]  # offered first; fake-big is usable only through models.use_all
    chat, big = m["fake-chat"], m["fake-big"]
    assert chat["restricted"] is False and big["restricted"] is True and big["usable"] is True
    # an alias nobody chose (the one made from the identifier) gives way to the engine's own name
    assert chat["label"] == "Fake chat · Q8_0" and chat["sub"] is None and chat["alias"] == "fake-chat"
    assert chat["real_id"] == "local:fake-chat"
    assert chat["details"] == {"architecture": "qwen35", "quantization": "Q8_0", "format": "GGUF", "context_length": 4096,
                               "license": "Apache-2.0", "memory_mb": 1500, "size_bytes": 1,
                               "source_repo": "someone/Fake-Chat-GGUF", "reasoning": True}
    assert chat["state"] == "idle" and cat["status"] is True
    fs.STATE["llm"].update(state="ready", model_id="fake-chat")
    st.health_cache.clear()
    assert by_alias(picker(mo))["fake-chat"]["state"] == "loaded"
    assert cat["display"] == {"names": "both", "group_by": "groups", "sort": "custom", "view": "compact",
                              "pinned_first": True}


def test_names_setting_shows_aliases_real_names_or_both_and_never_changes_the_identifier(mo, st):
    assert mo.put("/api/admin/settings/models.display.names", json={"value": "real"}).status_code == 200
    chat = by_alias(picker(mo))["fake-chat"]
    assert chat["label"] == "Fake chat · Q8_0" and chat["sub"] is None and chat["alias"] == "fake-chat"
    mid = ids(st)["fake-chat"]
    assert mo.patch(f"/api/admin/catalog/models/{mid}", json={"alias": "Quick helper"}).status_code == 200
    mo.put("/api/admin/settings/models.display.names", json={"value": "both"})
    chat = by_alias(picker(mo))["Quick helper"]
    assert chat["label"] == "Quick helper" and chat["sub"] == "Fake chat · Q8_0"
    mo.put("/api/admin/settings/models.display.names", json={"value": "alias"})
    chat = by_alias(picker(mo))["Quick helper"]
    assert chat["label"] == "Quick helper" and chat["sub"] is None and chat["real_id"] == "local:fake-chat"
    with st.db.session() as s:
        assert s.get(Model, mid).real_id == "local:fake-chat"


def test_personal_view_sort_grouping_and_favourites(mo, st):
    big = ids(st)["fake-big"]
    assert mo.patch("/api/me", json={"picker": {"sort": "name", "group_by": "capability", "view": "detailed"},
                                     "favourite_models": [big, "m_nonexistent"]}).status_code == 200
    cat = picker(mo)
    assert cat["display"]["sort"] == "name" and cat["display"]["view"] == "detailed"
    assert cat["favourites"] == [big] and cat["sections"][0] == {"id": "favourites", "title": "Favourites",
                                                                  "models": [big], "collapsed": False}
    assert [s["title"] for s in cat["sections"][1:]] == ["Text"]
    assert [m["alias"] for m in cat["models"]] == ["fake-big", "fake-chat"]  # the list follows the sections
    assert mo.patch("/api/me", json={"picker": {"sort": "best"}}).status_code == 422  # no invented rankings
    assert mo.patch("/api/me", json={"picker": {"sort": None}}).status_code == 200
    assert picker(mo)["display"]["sort"] == "custom"


# ---- seeing is not using -----------------------------------------------------------------------------------
def test_member_sees_only_offered_models_without_technical_details(mo, st, app):
    member = make_user(mo, app, "m1@example.test")
    cat = picker(member)
    assert [m["alias"] for m in cat["models"]] == ["fake-chat"]
    chat = cat["models"][0]
    assert chat["details"] == {"reasoning": True} and "real_id" not in chat and "state" not in chat
    assert cat["unusable"] == [] and cat["status"] is False


def test_view_all_lists_restricted_models_but_the_api_still_refuses_them(mo, st, app):
    member = make_user(mo, app, "m2@example.test")
    grant(mo, "member", ["models.view_all"])
    cat = picker(member)
    assert [m["alias"] for m in cat["unusable"]] == ["fake-big"]
    assert cat["unusable"][0]["usable"] is False and cat["unusable"][0]["reason"]
    big = ids(st)["fake-big"]
    cid = member.post("/api/chat/conversations", json={"model_id": big}).json()["id"]
    assert member.stream(f"/api/chat/conversations/{cid}/generate", {"content": "hi", "model_id": big})[0] in (403, 409)
    grant(mo, "member", ["models.use_all"])
    assert [m["alias"] for m in picker(member)["models"]] == ["fake-chat", "fake-big"]


def test_an_explicit_deny_beats_use_all(mo, st):
    big = ids(st)["fake-big"]
    owner_id = mo.get("/api/me").json()["user"]["id"]
    r = mo.put(f"/api/admin/models/{big}/grants", json={"grants": [
        {"subject_type": "user", "subject_id": owner_id, "effect": "deny"}]})
    assert r.status_code == 200, r.text
    assert [m["alias"] for m in picker(mo)["models"]] == ["fake-chat"]


# ---- logos -------------------------------------------------------------------------------------------------
def test_developer_logos_follow_the_registry_and_can_be_turned_off(mo, st):
    with st.db.session() as s:
        m = s.execute(select(Model).where(Model.real_id == "local:fake-chat")).scalar_one()
        m.meta = {**m.meta, "engine_name": "Huihui Qwen3.8 27B abliterated · Q6_K"}
    chat = by_alias(picker(mo))["fake-chat"]
    assert chat["provider"] == "huihui" and chat["based_on"] == "Qwen (Alibaba)"
    assert chat["icon"] == {"mark": "Hh", "tone": "#5A5A66"}  # a fine-tuner without a logo of its own
    with st.db.session() as s:
        m = s.execute(select(Model).where(Model.real_id == "local:fake-chat")).scalar_one()
        m.meta = {**m.meta, "engine_name": "Qwen3.5 0.8B · Q8_0"}
    assert by_alias(picker(mo))["fake-chat"]["icon"] == {"logo": "/model-logos/qwen-color.svg", "mask": False,
                                                          "dark": "/model-logos/qwen.svg"}
    mo.put("/api/admin/settings/models.developer_logos", json={"value": False})
    assert by_alias(picker(mo))["fake-chat"]["icon"] == {"mark": "Qw", "tone": "#3D5A80"}


def test_restricted_trademarks_get_lettermarks_and_a_quantiser_is_never_the_developer():
    assert catalog_svc.logo_icon("openai") is None and catalog_svc.logo_icon("meta") is None
    assert catalog_svc.logo_icon("stability") is None and catalog_svc.logo_icon("nvidia") is None
    assert catalog_svc.detect_provider("unsloth/Qwen3.6-35B-A3B-GGUF").id == "qwen"
    assert catalog_svc.detect_provider("local:gpt-oss-120b@MXFP4").id == "openai"
    assert catalog_svc.detect_provider("bartowski/cognitivecomputations_Dolphin3.0-R1-Mistral-24B-GGUF").id == "dolphin"
    assert catalog_svc.detect_provider("Dolphin3.0-R1-Mistral-24B", base=True).id == "mistral"
    assert catalog_svc.detect_provider("llama.cpp server") is None  # a runtime is not a developer


# ---- pure arrangement --------------------------------------------------------------------------------------
def rows():
    return [{"id": "a", "label": "Zeta", "sort": 0, "size_b": 4.0, "context": 32768, "modality": "text"},
            {"id": "b", "label": "alpha", "sort": 1, "size_b": None, "context": None, "modality": "text+image"},
            {"id": "c", "label": "Mid", "sort": 2, "size_b": 120.0, "context": 131072, "modality": "text",
             "provider_label": "OpenAI"}]


def test_sorting_keeps_unknowns_last_and_ties_in_custom_order():
    assert [r["id"] for r in catalog_svc.sort_rows(rows(), "custom")] == ["a", "b", "c"]
    assert [r["id"] for r in catalog_svc.sort_rows(rows(), "name")] == ["b", "c", "a"]
    assert [r["id"] for r in catalog_svc.sort_rows(rows(), "size_desc")] == ["c", "a", "b"]
    assert [r["id"] for r in catalog_svc.sort_rows(rows(), "size_asc")] == ["a", "c", "b"]
    assert [r["id"] for r in catalog_svc.sort_rows(rows(), "context")] == ["c", "a", "b"]
    assert catalog_svc.size_b("35B (A3B active)") == 35 and catalog_svc.size_b("350M") == 0.35
    assert catalog_svc.size_b(None) is None


def test_sections_by_family_size_capability_and_admin_groups():
    fam = catalog_svc.sections(rows(), "family", [])
    assert [(s["title"], s["models"]) for s in fam] == [("OpenAI", ["c"]), ("Other developers", ["a", "b"])]
    size = catalog_svc.sections(rows(), "size", [])
    assert [(s["title"], s["models"]) for s in size] == [("Over 100B", ["c"]), ("Under 10B", ["a"]),
                                                          ("Size not reported", ["b"])]
    cap = catalog_svc.sections(rows(), "capability", [])
    assert [(s["title"], s["models"]) for s in cap] == [("Reads images too", ["b"]), ("Text", ["a", "c"])]
    grp = catalog_svc.sections(rows(), "groups", [{"id": "g1", "name": "Fast", "models": ["c", "a"]}])
    assert [(s["title"], s["models"]) for s in grp] == [("Fast", ["c", "a"]), ("Other models", ["b"])]
    grp = catalog_svc.sections(rows(), "groups", [{"id": "g1", "name": "Fast", "models": ["c", "a"]}], custom=False)
    assert grp[0]["models"] == ["a", "c"]


# ---- composer keys (personal choice over the administrator's default) -------------------------------------
def test_enter_sends_and_prompt_history_follow_the_setting_until_a_person_chooses(mo, st):
    comp = lambda: mo.get("/api/me").json()["ui"]["composer"]  # noqa: E731
    assert comp()["enter_sends"] is True and comp()["prompt_history"] is False
    assert mo.put("/api/admin/settings/chat.enter_sends", json={"value": False}).status_code == 200
    assert comp()["enter_sends"] is False
    assert mo.patch("/api/me", json={"enter_sends": True, "prompt_history": True}).status_code == 200
    assert comp()["enter_sends"] is True and comp()["prompt_history"] is True
    assert mo.patch("/api/me", json={"enter_sends": None}).status_code == 200  # back to the setting
    assert comp()["enter_sends"] is False and comp()["prompt_history"] is True
