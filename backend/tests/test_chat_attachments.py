"""The normal chat: several reference pictures for models that take them, a clear refusal for those that do not, and the iPhone Photo preset
(the same prompt builder / 9:16 size / wording rules as Imajutsu) as an option of the chat image operation. Engine = the scripted image double."""
from . import fake_sarness as fs
from .imajutsu_helpers import finish_jobs, last_submission, model_id, png_of, pytestmark_ffmpeg, solid, solid_png, up

pytestmark = pytestmark_ffmpeg


def chat_model(api):
    return api.get("/api/models/catalog", params={"operation": "text"}).json()["default"]


def conv(api):
    return api.post("/api/chat/conversations", json={"model_id": chat_model(api)}).json()["id"]


def gen(api, cid, prompt, alias, **kw):
    return api.post(f"/api/chat/conversations/{cid}/generate", json={"operation": "image", "content": prompt, "model_id": model_id(api, alias), **kw})


def pic(api, n):
    """A real, different small picture in the library; its id (ma_...) is what the chat message refers to."""
    return up(api, solid_png((40 * n % 256, 90, 160), 48 + n, 64), f"pic{n}.png")["id"]


# ---- what the model takes is published, not guessed ------------------------------------------------------------------------------------------------
def test_options_publish_how_many_pictures_each_model_takes(im):
    got = {m["alias"]: m for m in im.get("/api/media/options", params={"kind": "image"}).json()["models"]}
    assert got["flux2-klein-4b"]["references"] == 4 and got["qwen-image-edit-2511"]["references"] == 3
    assert got["z-image-turbo"]["references"] == 0 and "edit" not in got["z-image-turbo"]["modes"]


# ---- several pictures ------------------------------------------------------------------------------------------------------------------------------
def test_chat_edit_sends_the_first_picture_as_source_and_the_rest_as_references_in_order(im):
    cid = conv(im)
    ids = [pic(im, n) for n in (1, 2, 3)]
    r = gen(im, cid, "Put the person from image 2 on the beach of image 1", "flux2-klein-4b", attachments=ids, source_id=ids[0], media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["meta"]["source"] == ids[0] and msg["meta"]["refs"] == ids[1:]
    sub = last_submission()
    assert sub["model"] == "flux2-klein-4b" and sub["engine_mode"] == "edit" and len(sub["pictures"]) == 3
    assert [r.json()["user_message"]["attachments"][i]["id"] for i in range(3)] == ids
    assert finish_jobs(im, [msg["media"]["id"]])[0]["status"] == "completed"
    # another take keeps the same pictures
    again = im.post(f"/api/chat/conversations/{cid}/generate", json={"mode": "regenerate", "parent_id": msg["id"]})
    assert again.status_code == 200, again.text
    assert len(last_submission()["pictures"]) == 3 and again.json()["message"]["meta"]["refs"] == ids[1:]


def test_more_pictures_than_the_model_takes_is_refused_in_words_never_truncated(im):
    cid = conv(im)
    ids = [pic(im, n) for n in range(1, 5)]
    before = len(fs.STATE["image_submissions"])
    r = gen(im, cid, "combine", "qwen-image-edit-2511", attachments=ids, source_id=ids[0], media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_pictures"
    assert "at most 3 pictures" in r.json()["detail"]["message"] and "4 are attached" in r.json()["detail"]["message"]
    assert len(fs.STATE["image_submissions"]) == before
    # a model that restyles from one picture takes exactly one
    r = gen(im, cid, "combine", "z-image-turbo", attachments=ids[:2], source_id=ids[0], media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 422 and "one picture" in r.json()["detail"]["message"]
    # a text-only model says it cannot edit at all
    r = gen(im, cid, "edit", "qwen-image-2.1", attachments=ids[:1], source_id=ids[0], media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 422 and "cannot edit pictures" in r.json()["detail"]["message"]


def test_one_picture_still_works_as_before(im):
    cid = conv(im)
    pid = pic(im, 1)
    r = gen(im, cid, "make it night", "flux2-klein-4b", attachments=[pid], media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 200, r.text
    assert len(last_submission()["pictures"]) == 1 and "refs" not in r.json()["message"]["meta"]


# ---- iPhone Photo ----------------------------------------------------------------------------------------------------------------------------------
def test_iphone_photo_in_chat_is_nine_sixteen_with_the_shared_prompt_builder(im):
    cid = conv(im)
    r = gen(im, cid, "two friends at a bus stop", "flux2-klein-4b", preset="iphone_photo", scenes=["night", "casual"])
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["meta"]["preset"]["id"] == "iphone_photo" and msg["meta"]["preset"]["scenes"] == ["night", "casual"]
    assert msg["content"] == "two friends at a bus stop"                        # the message is what the person wrote
    sub = last_submission()
    assert (sub["simple"]["width"], sub["simple"]["height"]) == (720, 1280)
    p = sub["simple"]["prompt"]
    assert p.startswith("two friends at a bus stop.") and "street lights" in p and "unedited handheld phone photo" in p
    assert sub["engine_mode"] == "txt2img" and not sub["pictures"]
    assert r.json()["user_message"]["meta"]["preset"]["id"] == "iphone_photo"


def test_iphone_photo_uses_plain_wording_for_a_model_that_draws_phones(im):
    cid = conv(im)
    r = gen(im, cid, "a red car", "z-image-turbo", preset="iphone_photo")
    assert r.status_code == 200, r.text
    p = last_submission()["simple"]["prompt"]
    assert p.startswith("A photograph of a red car.") and "phone" not in p.lower() and "9:16" not in p
    assert (last_submission()["simple"]["width"], last_submission()["simple"]["height"]) == (720, 1280)


def test_iphone_photo_larger_size_regenerate_and_refusals(im):
    cid = conv(im)
    mid = "flux2-klein-4b"
    r = gen(im, cid, "a cat on a sofa", mid, preset="iphone_photo", media={"width": 1008, "height": 1792})
    assert r.status_code == 200, r.text
    assert (last_submission()["simple"]["width"], last_submission()["simple"]["height"]) == (1008, 1792)
    # another take keeps the preset, the shape and the size
    again = im.post(f"/api/chat/conversations/{cid}/generate", json={"mode": "regenerate", "parent_id": r.json()["message"]["id"]})
    assert again.status_code == 200, again.text
    s2 = last_submission()["simple"]
    assert (s2["width"], s2["height"]) == (1008, 1792) and "unedited handheld phone photo" in s2["prompt"]
    assert again.json()["message"]["meta"]["preset"]["id"] == "iphone_photo"
    # not 9:16: refused in words
    bad = gen(im, cid, "a cat", mid, preset="iphone_photo", media={"width": 1024, "height": 1024})
    assert bad.status_code == 422 and "9:16" in bad.json()["detail"]["message"]
    # a picture cannot go with it
    pid = pic(im, 1)
    bad = gen(im, cid, "a cat", mid, preset="iphone_photo", attachments=[pid], source_id=pid)
    assert bad.status_code == 422 and "does not use attached pictures" in bad.json()["detail"]["message"]
    assert im.post(f"/api/chat/conversations/{cid}/generate", json={"operation": "image", "content": "x", "model_id": model_id(im, mid), "preset": "polaroid"}).status_code == 422   # only the one preset exists


def test_iphone_photo_without_a_conversation_through_the_media_api(im):
    r = im.post("/api/media/jobs", json={"kind": "image", "model_id": model_id(im, "flux2-klein-4b"), "prompt": "a bakery window", "save": False, "preset": "iphone_photo", "scenes": ["indoor"],
                                          "width": 720, "height": 1280})
    assert r.status_code == 200, r.text
    s = last_submission()["simple"]
    assert (s["width"], s["height"]) == (720, 1280) and "ordinary room light" in s["prompt"]
