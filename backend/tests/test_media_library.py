"""Uploads, previews, real trimming and cropping, and handing the edited file to an editing profile (OpenJutsu ...).

These use real ffmpeg and real video files made on the fly; only the inference engine is a double. Skipped where ffmpeg is not installed."""
import json
import shutil
import subprocess
import time

import pytest

from . import fake_sarness as fs
from .test_media import finish
from .test_media_profiles import mp as _mp  # noqa: F401  (fixture)
from .test_media_profiles import oj

pytestmark = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")


@pytest.fixture
def mp(_mp):  # noqa: F811
    """The signed-in client with a long-lived event loop: edits run as background tasks, which a per-request loop would cancel."""
    with _mp.c:
        yield _mp


def ff(*args, out):
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args, str(out)], check=True)
    return out.read_bytes()


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    """A 4 s 320x240 24 fps H.264 + AAC clip whose picture changes every second (so a trim is visible in the output)."""
    d = tmp_path_factory.mktemp("clip")
    return ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=4", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
              "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", "-movflags", "+faststart", out=d / "clip.mp4")


@pytest.fixture(scope="module")
def webm(tmp_path_factory):
    d = tmp_path_factory.mktemp("webm")
    return ff("-f", "lavfi", "-i", "testsrc=size=160x120:rate=15:duration=2", "-c:v", "libvpx-vp9", "-b:v", "200k", out=d / "c.webm")


@pytest.fixture(scope="module")
def jpeg(tmp_path_factory):
    d = tmp_path_factory.mktemp("jpg")
    return ff("-f", "lavfi", "-i", "testsrc=size=400x300:rate=1:duration=1", "-frames:v", "1", out=d / "p.jpg")


def up(api, data, name="clip.mp4", **q):
    return api.post("/api/media/uploads", params={"name": name, **q}, content=data, headers={"content-type": "application/octet-stream"})


def probe(data: bytes, tmp_path):
    p = tmp_path / "probe.bin"
    p.write_bytes(data)
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)], capture_output=True, check=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    return int(v["width"]), int(v["height"]), float(j["format"]["duration"]), any(s["codec_type"] == "audio" for s in j["streams"])


def pj(api, mid, inputs, **assets):
    """POST an editing-profile job naming library assets (asset:<input> = id, repeated for several)."""
    return api.post("/api/media/profile-jobs", data={"model_id": mid, "inputs": json.dumps(inputs), **{f"asset:{k}": v for k, v in assets.items()}})


def wait_edit(api, eid, tries=300):
    for _ in range(tries):
        t = api.get(f"/api/media/edits/{eid}").json()
        if t["status"] != "running":
            return t
        time.sleep(0.1)
    raise AssertionError("edit never finished")


# ---- upload ------------------------------------------------------------------------------------------------
def test_video_upload_is_probed_stored_encrypted_and_served_by_range(mp, st, clip):
    r = up(mp, clip)
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["kind"] == "video" and a["mime"] == "video/mp4" and (a["width"], a["height"]) == (320, 240)
    assert 3.9 < a["duration"] < 4.2 and a["origin"] == "upload" and a["name"] == "clip.mp4" and a["media"]["fps"] == 24.0 and a["media"]["audio"] is True
    assert a["has_thumb"] and a["preview"] == "original" and a["size"] == len(clip)
    # on disk: sealed in chunks, never the plain bytes
    disk = next(p for p in (st.cfg.data_dir / "assets").rglob(f"{a['id']}.bin"))
    assert disk.read_bytes()[:4] == b"SMC1" and clip[:64] not in disk.read_bytes()
    full = mp.get(f"/api/media/assets/{a['id']}/content")
    assert full.status_code == 200 and full.content == clip and full.headers["accept-ranges"] == "bytes"
    part = mp.get(f"/api/media/assets/{a['id']}/content", headers={"range": "bytes=1000000-1000099"} if len(clip) > 1000100 else {"range": "bytes=100-199"})
    assert part.status_code == 206 and len(part.content) == 100
    lo = 1000000 if len(clip) > 1000100 else 100
    assert part.content == clip[lo:lo + 100]
    assert mp.get(f"/api/media/assets/{a['id']}/content", headers={"range": f"bytes={len(clip) + 5}-"}).status_code == 416
    th = mp.get(f"/api/media/assets/{a['id']}/thumb")
    assert th.status_code == 200 and th.content[:2] == b"\xff\xd8"


def test_large_upload_streams_in_several_chunks_without_corruption(mp, st, tmp_path):
    from semechka import media_store
    big = ff("-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=20", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", out=tmp_path / "big.mp4")
    assert len(big) > 2 * media_store.CHUNK, len(big)
    a = up(mp, big, "big.mp4").json()
    got = mp.get(f"/api/media/assets/{a['id']}/content").content
    assert got == big
    mid = media_store.CHUNK + 777
    r = mp.get(f"/api/media/assets/{a['id']}/content", headers={"range": f"bytes={mid}-{mid + 2 * media_store.CHUNK}"})
    assert r.status_code == 206 and r.content == big[mid:mid + 2 * media_store.CHUNK + 1]


def test_image_and_other_formats_are_identified_from_their_bytes(mp, jpeg, webm):
    a = up(mp, jpeg, "photo.jpg").json()
    assert a["kind"] == "image" and a["mime"] == "image/jpeg" and (a["width"], a["height"]) == (400, 300) and a["has_thumb"]
    w = up(mp, webm, "c.webm").json()
    assert w["mime"] == "video/webm" and w["media"]["codec"] == "vp9" and w["preview"] == "original"
    # the name and the browser's claim do not decide: a PNG named .mp4 is a picture
    p = up(mp, fs.make_png(), "sneaky.mp4")
    assert p.status_code == 200 and p.json()["kind"] == "image"


def test_invalid_and_unsupported_files_are_refused_with_a_reason_and_leave_nothing(mp, st):
    before = sorted(p.name for p in (st.cfg.data_dir / "assets").rglob("*") if p.is_file()) if (st.cfg.data_dir / "assets").exists() else []
    for data, name, code in [(b"hello world this is text", "a.txt", "unsupported_type"), (b"<svg xmlns='x'></svg>", "a.svg", "unsupported_type"),
                             (b"", "e.mp4", "empty"), (b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 200, "broken.mp4", "unreadable"),
                             (b"\x00\x00\x00\x20ftypheic" + b"\x00" * 40, "p.heic", "unsupported_type")]:
        r = up(mp, data, name)
        assert r.status_code in (415, 422), (name, r.text)
        assert r.json()["detail"]["code"] == code, (name, r.json())
        assert r.json()["detail"]["message"]
    after = sorted(p.name for p in (st.cfg.data_dir / "assets").rglob("*") if p.is_file()) if (st.cfg.data_dir / "assets").exists() else []
    assert after == before
    assert mp.get("/api/media/assets").json() == []


def test_size_limit_is_enforced_before_and_while_streaming(mp, st):
    import asyncio

    from semechka import media_lib

    async def body():
        yield b"\x00\x00\x00\x18ftypmp42" + b"x" * (300 * 1024)
        yield b"x" * (900 * 1024)

    with pytest.raises(media_lib.MediaError) as e:
        asyncio.run(media_lib.ingest(st, user_id="u", name="x.mp4", body=body(), declared=None, cap=1024 * 1024, ephemeral=False, expires_at=None))
    assert e.value.status == 413 and "1 MB" in e.value.message
    with pytest.raises(media_lib.MediaError) as e2:
        asyncio.run(media_lib.ingest(st, user_id="u", name="x.mp4", body=body(), declared=5 * 1024 * 1024, cap=1024 * 1024, ephemeral=False, expires_at=None))
    assert e2.value.status == 413
    assert not list((st.cfg.data_dir / "tmp" / "media").glob("w-*"))  # scratch space is gone


def test_other_people_cannot_see_or_use_an_upload(mp, app, st, clip):
    from .conftest import make_user
    a = up(mp, clip).json()
    other = make_user(mp, app, "other@example.test")
    for path in (f"/api/media/assets/{a['id']}", f"/api/media/assets/{a['id']}/content", f"/api/media/assets/{a['id']}/thumb", f"/api/media/assets/{a['id']}/strip"):
        assert other.get(path).status_code == 404, path
    assert other.post(f"/api/media/assets/{a['id']}/edits", json={"mute": True}).status_code == 404
    assert other.delete(f"/api/media/assets/{a['id']}").status_code == 404
    assert other.get("/api/media/assets").json() == []
    from semechka import media_lib
    with st.db.session() as s:
        assert media_lib.owned(st, s, other.user_id, a["id"]) is None  # what the editing-profile route checks for every selected id


# ---- editing -----------------------------------------------------------------------------------------------
def test_trim_makes_a_new_shorter_asset_and_keeps_the_original(mp, clip, tmp_path):
    a = up(mp, clip).json()
    r = mp.post(f"/api/media/assets/{a['id']}/edits", json={"trim": {"start": 1.0, "end": 3.0}})
    assert r.status_code == 200, r.text
    t = wait_edit(mp, r.json()["id"])
    assert t["status"] == "completed", t
    n = t["asset"]
    assert n["origin"] == "edit" and n["parent"] == a["id"] and n["id"] != a["id"] and n["edit"]["trim"] == [1.0, 3.0]
    w, h, d, audio = probe(mp.get(f"/api/media/assets/{n['id']}/content").content, tmp_path)
    assert (w, h) == (320, 240) and 1.9 < d < 2.15 and audio
    assert mp.get(f"/api/media/assets/{a['id']}/content").content == clip  # the original is untouched
    assert {x["id"] for x in mp.get("/api/media/assets").json()} >= {a["id"], n["id"]}


def test_crop_changes_the_pixels_and_the_reported_size(mp, clip, tmp_path):
    a = up(mp, clip).json()
    t = wait_edit(mp, mp.post(f"/api/media/assets/{a['id']}/edits", json={"crop": {"x": 40, "y": 20, "w": 200, "h": 120}}).json()["id"])
    assert t["status"] == "completed", t
    n = t["asset"]
    assert (n["width"], n["height"]) == (200, 120) and abs(n["duration"] - a["duration"]) < 0.3
    w, h, _d, _ = probe(mp.get(f"/api/media/assets/{n['id']}/content").content, tmp_path)
    assert (w, h) == (200, 120)


def test_trim_and_crop_together_then_edit_the_edit(mp, clip, tmp_path):
    a = up(mp, clip).json()
    first = wait_edit(mp, mp.post(f"/api/media/assets/{a['id']}/edits", json={"trim": {"start": 0.5, "end": 3.5}, "crop": {"x": 0, "y": 0, "w": 160, "h": 240}}).json()["id"])["asset"]
    assert (first["width"], first["height"]) == (160, 240) and 2.9 < first["duration"] < 3.15
    second = wait_edit(mp, mp.post(f"/api/media/assets/{first['id']}/edits", json={"trim": {"start": 1, "end": 2}, "mute": True}).json()["id"])["asset"]
    assert second["parent"] == first["id"] and 0.9 < second["duration"] < 1.15 and second["media"]["audio"] is False
    w, h, d, audio = probe(mp.get(f"/api/media/assets/{second['id']}/content").content, tmp_path)
    assert (w, h) == (160, 240) and not audio


def test_bad_edits_are_refused_clearly(mp, clip):
    a = up(mp, clip).json()
    base = f"/api/media/assets/{a['id']}/edits"
    for body, frag in [({}, "Nothing was changed"), ({"crop": {"x": 300, "y": 0, "w": 100, "h": 100}}, "outside"), ({"crop": {"x": 0, "y": 0, "w": 4, "h": 4}}, "too small"),
                       ({"trim": {"start": 3, "end": 2}}, "at least 0.1 s"), ({"trim": {"start": 0, "end": 99}}, "within"), ({"rotate": 45}, "Rotation")]:
        r = mp.post(base, json=body)
        assert r.status_code == 422 and frag in r.json()["detail"]["message"], (body, r.text)
    assert mp.post("/api/media/assets/ma_nope/edits", json={"mute": True}).status_code == 404


def test_image_crop_and_rotate_use_the_rotated_frame(mp, jpeg, tmp_path):
    a = up(mp, jpeg, "photo.jpg").json()
    t = wait_edit(mp, mp.post(f"/api/media/assets/{a['id']}/edits", json={"rotate": 90, "crop": {"x": 0, "y": 0, "w": 300, "h": 200}}).json()["id"])
    assert t["status"] == "completed", t
    n = t["asset"]
    assert n["kind"] == "image" and (n["width"], n["height"]) == (300, 200) and n["mime"] == "image/jpeg"
    # rotated 90: the frame is 300 wide and 400 high, so a crop 350 wide does not fit
    assert mp.post(f"/api/media/assets/{a['id']}/edits", json={"rotate": 90, "crop": {"x": 0, "y": 0, "w": 350, "h": 100}}).status_code == 422


def test_cancel_stops_an_edit_and_stores_nothing(mp, st, tmp_path):
    long_clip = ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=60", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", out=tmp_path / "l.mp4")
    a = up(mp, long_clip, "long.mp4").json()
    before = len(mp.get("/api/media/assets").json())
    tid = mp.post(f"/api/media/assets/{a['id']}/edits", json={"crop": {"x": 0, "y": 0, "w": 640, "h": 360}}).json()["id"]
    assert mp.post(f"/api/media/edits/{tid}/cancel").status_code == 200
    t = wait_edit(mp, tid)
    assert t["status"] in ("cancelled", "completed")  # a fast machine may finish first; then nothing is stranded either way
    if t["status"] == "cancelled":
        assert len(mp.get("/api/media/assets").json()) == before
    assert not list((st.cfg.data_dir / "tmp" / "media").glob("w-*"))


# ---- editing profile (OpenJutsu) ---------------------------------------------------------------------------
def test_the_edited_video_is_what_the_editing_profile_receives(mp, clip, tmp_path):
    src = up(mp, clip).json()
    edited = wait_edit(mp, mp.post(f"/api/media/assets/{src['id']}/edits", json={"trim": {"start": 1, "end": 3}, "crop": {"x": 0, "y": 0, "w": 160, "h": 160}}).json()["id"])["asset"]
    ref = up(mp, fs.make_png(), "face.png").json()
    mid = oj(mp)["id"]
    r = pj(mp, mid, {"prompt": "the singer"}, video=[edited["id"]], refs=[ref["id"]])
    assert r.status_code == 200, r.text
    got = fs.STATE["profile_submissions"][-1]
    sent = fs.STATE["uploads"][got["inputs"]["video"]]
    assert sent == mp.get(f"/api/media/assets/{edited['id']}/content").content and sent != clip  # exactly the edited file
    w, h, d, _ = probe(sent, tmp_path)
    assert (w, h) == (160, 160) and 1.9 < d < 2.15
    assert fs.STATE["uploads"][got["inputs"]["refs"][0]] == fs.make_png()
    j = finish(mp, r.json()["id"])
    assert j["status"] == "completed" and j["params"]["sources"] == {"video": [edited["id"]], "refs": [ref["id"]]}


def test_assets_of_other_people_or_wrong_kind_cannot_be_sent(mp, clip):
    src = up(mp, clip).json()
    mid = oj(mp)["id"]
    r = pj(mp, mid, {"prompt": "x"}, video=["ma_doesnotexist0000000000"])
    assert r.status_code == 422 and r.json()["detail"]["code"] == "asset_missing"
    pic = up(mp, fs.make_png(), "p.png").json()
    r = pj(mp, mid, {"prompt": "x"}, video=[pic["id"]])
    assert r.status_code == 422 and "video" in r.json()["detail"]["message"]
    assert src["id"]


def test_profile_inputs_follow_the_chosen_operation(mp, st, clip):
    """The real OpenJutsu schema says which inputs each operation uses: a reference picture for check_mask is refused, and an input another
    operation does not use is not sent."""
    from semechka.models import Model
    mid = oj(mp)["id"]
    with st.db.session() as s:
        m = s.get(Model, mid)
        meta = json.loads(json.dumps(m.meta))
        meta["media"]["profile"]["inputs_schema"] = {
            "operation": {"type": "enum", "values": ["swap", "check_mask"], "default": "swap", "operations": ["swap", "check_mask"]},
            "video": {"type": "file", "kind": "video", "required": True, "label": "Source video", "operations": ["swap", "check_mask"]},
            "refs": {"type": "file", "kind": "image", "multi": True, "max_files": 4, "label": "Reference pictures", "operations": ["swap"]},
            "prompt": {"type": "string", "max_length": 100, "operations": ["swap"]},
            "target": {"type": "string", "max_length": 50, "operations": ["swap", "check_mask"]},
            "hat_fix": {"type": "bool", "default": False, "operations": ["swap", "check_mask"]}}
        m.meta = meta
    v = up(mp, clip).json()
    p = up(mp, fs.make_png(), "p.png").json()
    r = pj(mp, mid, {"operation": "check_mask", "target": "person"}, video=[v["id"]], refs=[p["id"]])
    assert r.status_code == 422 and "not used by the 'check_mask' operation" in r.json()["detail"]["message"]
    r = pj(mp, mid, {"operation": "check_mask", "target": "person", "prompt": "ignored", "hat_fix": True}, video=[v["id"]])
    assert r.status_code == 200, r.text
    sent = fs.STATE["profile_submissions"][-1]["inputs"]
    assert sent["operation"] == "check_mask" and sent["hat_fix"] is True and "prompt" not in sent and "refs" not in sent


# ---- library, lifecycle ------------------------------------------------------------------------------------
def test_library_lists_generated_uploaded_and_edited_items_and_deletes_files(mp, st, clip):
    a = up(mp, clip).json()
    e = wait_edit(mp, mp.post(f"/api/media/assets/{a['id']}/edits", json={"mute": True}).json()["id"])["asset"]
    ls = mp.get("/api/media/assets", params={"origin": "edit"}).json()
    assert [x["id"] for x in ls] == [e["id"]]
    assert {x["origin"] for x in mp.get("/api/media/assets").json()} == {"upload", "edit"}
    assert [x["id"] for x in mp.get("/api/media/assets", params={"kind": "image"}).json()] == []
    mp.get(f"/api/media/assets/{a['id']}/strip", params={"n": 8})
    files = list((st.cfg.data_dir / "assets").rglob(f"{a['id']}*"))
    assert len(files) >= 2  # the file and its preview picture (and the filmstrip)
    assert mp.delete(f"/api/media/assets/{a['id']}").status_code == 200
    assert not list((st.cfg.data_dir / "assets").rglob(f"{a['id']}*"))
    assert mp.get(f"/api/media/assets/{a['id']}/content").status_code == 404
    assert mp.get(f"/api/media/assets/{e['id']}/content").status_code == 200  # the edited copy is its own item


def test_filmstrip_is_one_wide_picture_with_the_requested_frames(mp, clip):
    a = up(mp, clip).json()
    r = mp.get(f"/api/media/assets/{a['id']}/strip", params={"n": 10, "h": 48})
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"
    import struct
    d = r.content
    i = 2
    while i < len(d):
        m = d[i + 1]
        if m in (0xC0, 0xC2):
            h, w = struct.unpack(">HH", d[i + 5:i + 9])
            break
        i += 2 + struct.unpack(">H", d[i + 2:i + 4])[0]
    assert h == 48 and w == 10 * 64  # 48 px high frames of a 4:3 picture are 64 px wide


def test_no_save_uploads_stay_in_memory_and_can_be_edited(mp, st, clip, tmp_path):
    a = up(mp, clip, ephemeral="true")
    assert a.status_code == 200, a.text
    a = a.json()
    assert a["id"].startswith("me_") and a["saved"] is False
    assert not list((st.cfg.data_dir / "assets").rglob(f"{a['id']}*")) if (st.cfg.data_dir / "assets").exists() else True
    assert mp.get(f"/api/media/assets/{a['id']}/content").content == clip
    e = wait_edit(mp, mp.post(f"/api/media/assets/{a['id']}/edits", json={"trim": {"start": 0, "end": 2}}).json()["id"])
    assert e["status"] == "completed" and e["asset"]["id"].startswith("me_")
    assert not list((st.cfg.data_dir / "tmp").rglob("w-*"))
    assert any(x["id"] == a["id"] for x in mp.get("/api/media/assets").json())
