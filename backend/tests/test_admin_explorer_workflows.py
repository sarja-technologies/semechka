"""The explorer over REAL Imajutsu jobs: classification, inputs (source, references) and whitelisted parameters, from what the service stored."""
import json

from sqlalchemy import select

from semechka.models import AuditEvent

from .imajutsu_helpers import (
    finish_jobs,
    gradient,
    mkproject,
    model_id,
    png_of,
    pytestmark_ffmpeg,
    solid_png,
    up,
)
from .test_admin_explorer import EX, REASON, enable, media

pytestmark = pytestmark_ffmpeg


def test_a_real_imajutsu_edit_job_shows_its_source_references_parameters_and_results(im, st):
    refs = [up(im, solid_png(c), f"ref{i}.png") for i, c in enumerate([(200, 0, 0), (0, 200, 0)])]
    src = up(im, png_of(gradient(240, 320)), "src.png")
    spec = {"prompt": "put the subject of the other pictures on a beach", "model": model_id(im, "flux2-klein-4b"), "source_asset": src["id"], "aspect": {"id": "source"},
            "references": [{"asset": r["id"], "role": None, "active": True, "label": f"pic {i}"} for i, r in enumerate(refs)]}
    p = mkproject(im, "edit", spec)
    r = im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={})
    assert r.status_code == 200, r.text
    jid = r.json()["jobs"][0]
    assert finish_jobs(im, [jid])[0]["status"] == "completed"
    enable(im)
    listed = media(im, {"workflow": "imajutsu"}).json()["items"]
    assert [i["id"] for i in listed] == [jid] and listed[0]["operation"] == "edit" and listed[0]["kind"] == "image" and listed[0]["outputs"][0]["available"]
    assert media(im, {"workflow": "openjutsu"}).json()["items"] == []
    d = im.post(f"{EX}/media/jobs/{jid}", json={"reason": REASON}).json()
    roles = [(i["role"], i["id"], i["state"]) for i in d["inputs"]]
    assert roles[0] == ("source", src["id"], "available") and [x[0] for x in roles[1:]] == ["reference", "reference"] and {x[1] for x in roles[1:]} == {r["id"] for r in refs}
    assert d["workflow"] == "imajutsu" and d["workflow_detail"]["mode"] == "edit" and d["workflow_detail"]["source_asset"] == src["id"]
    assert {"width", "height", "steps", "seed"} <= set(d["params"]) and not {"plan", "spec", "accounting", "reference_image", "extra_references"} & set(d["params"])
    blob = json.dumps(d)
    assert "reference_image" not in blob and '"accounting"' not in blob  # engine file ids and the plan stay out
    thumb = im.get(d["inputs"][0]["thumb_url"])
    assert thumb.status_code == 200 and thumb.content.startswith(b"\xff\xd8")  # the stored preview picture (JPEG)
    assert d["outputs"][0]["id"] in {a["id"] for a in im.get("/api/media/assets").json()}
    with st.db.session() as s:
        rows = s.execute(select(AuditEvent).where(AuditEvent.action == "media.admin.view")).scalars().all()
        assert len(rows) == 1 and src["id"] not in json.dumps(rows[0].detail) and "beach" not in json.dumps(rows[0].detail)


def test_chat_edit_reference_pictures_are_listed_as_inputs_to_the_admin():
    """A chat edit stores the first picture as ``source`` and the further ones as ``refs`` in the message meta: the explorer must list all of them."""
    from semechka import admin_media as X
    got = X.input_refs({}, {"source": "ma_aaaa1111", "refs": ["ma_bbbb2222", "ma_cccc3333"]})
    assert got == [("source", "ma_aaaa1111"), ("reference", "ma_bbbb2222"), ("reference", "ma_cccc3333")]


def test_a_chat_edit_job_is_labelled_edit_and_an_animation_animate_not_generate():
    from types import SimpleNamespace as NS
    from semechka import admin_media as X
    chat = NS(conversation_id="cv_1", kind="image")
    assert X.classify_job(chat, None, {"prompt": "x"}) == ("chat_image", "generate")
    assert X.classify_job(chat, None, {"reference_image": "up_1"}) == ("chat_image", "edit")
    assert X.classify_job(chat, None, {"reference_image": "up_1", "extra_references": ["up_2"]}) == ("chat_image", "edit")
    assert X.classify_job(NS(conversation_id="cv_1", kind="video"), None, {"start_image": "up_1"}) == ("chat_video", "animate")
    assert X.classify_job(NS(conversation_id=None, kind="image"), None, {"reference_image": "up_1"}) == ("image_generation", "edit")


def test_a_chat_edit_is_read_from_its_message_because_the_engine_upload_ids_are_not_stored():
    from types import SimpleNamespace as NS
    from semechka import admin_media as X
    assert X.classify_job(NS(conversation_id="cv_1", kind="image"), None, {"steps": 40}, {"source": "ma_1", "refs": ["ma_2"]}) == ("chat_image", "edit")
    assert X.classify_job(NS(conversation_id="cv_1", kind="video"), None, {}, {"source": "ma_1"}) == ("chat_video", "animate")
    assert X.classify_job(NS(conversation_id="cv_1", kind="image"), None, {}, {"operation": "image"}) == ("chat_image", "generate")
