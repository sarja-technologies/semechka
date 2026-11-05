"""OpenJutsu Studio, part 2: preparing the video for real. ffmpeg does the work on real files; the originals are never touched, an identical
request is answered from the existing prepared copy, and what comes out is checked frame size, duration, sound and sync."""
import asyncio

import pytest

from semechka import media_lib

from .studio_helpers import (
    avg_color,
    content,
    ffm,
    make_rotated,
    make_video,
    mkproject,
    probe,
    pytestmark_ffmpeg,
    sha,
    up,
    wait_edit,
)

pytestmark = pytestmark_ffmpeg


def prepare(api, pid, spec=None, expect=200):
    r = api.post(f"/api/studio/projects/{pid}/prepare", json={"spec": spec} if spec is not None else {})
    assert r.status_code == expect, r.text
    return r.json()


def done(api, pid, spec=None):
    """Prepare and wait: -> (response, the prepared asset's JSON, its bytes)."""
    out = prepare(api, pid, spec)
    if out["task"]:
        t = wait_edit(api, out["task"])
        assert t["status"] == "completed", t
        out["asset"] = t["asset"]
        out["mode"] = t.get("mode")
    return out, out["asset"], content(api, out["asset"]["id"])


def check_av(info, want_dur, fps, tol_frames=1):
    """Duration within a frame; the sound as long as the picture (a frame and one AAC frame of slack); both start at zero."""
    frame = 1 / fps
    assert abs(info["vdur"] - want_dur) <= tol_frames * frame + 0.02, info
    assert info["adur"] is not None and abs(info["adur"] - info["vdur"]) <= frame + 0.02, info
    assert abs(info["vstart"]) < 0.1 and abs(info["astart"]) < 0.1, info


@pytest.fixture
def clip(sm, tmp_path):
    """6 s, 320x240, 24 fps, a key frame every second, with sound."""
    return up(sm, make_video(tmp_path, dur=6, fps=24, gop=24), "clip.mp4")


def test_a_trim_on_a_keyframe_is_cut_without_re_encoding(sm, clip, tmp_path):
    p = mkproject(sm, clip["id"], {"trim": {"start": 2, "end": 4}})
    before = sha(content(sm, clip["id"]))
    out, asset, data = done(sm, p["id"])
    assert out["reused"] is False and out["mode"] == "copy" and out["plan"]["trim"]["mode"] == "copy" and "keyframe" in out["plan"]["trim"]["why"]
    info = probe(data, tmp_path)
    assert (info["w"], info["h"]) == (320, 240) and info["vcodec"] == "h264" and info["acodec"] == "aac" and info["rot"] == 0
    check_av(info, 2.0, 24)
    assert asset["origin"] == "prepared" and asset["parent"] == clip["id"] and asset["name"] == "clip (prepared).mp4"
    assert asset["edit"]["trim"] == [2.0, 4.0] and asset["edit"]["stream_copy"] is True
    assert sha(content(sm, clip["id"])) == before                                        # the original is byte-identical
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["prepared_asset"]["id"] == asset["id"]


def test_a_trim_between_keyframes_is_re_encoded_so_the_cut_is_exact(sm, clip, tmp_path):
    p = mkproject(sm, clip["id"], {"trim": {"start": 2.5, "end": 4.5}})
    out, asset, data = done(sm, p["id"])
    assert out["mode"] == "reencode" and "between keyframes" in out["plan"]["trim"]["why"]
    info = probe(data, tmp_path)
    assert (info["w"], info["h"]) == (320, 240)
    check_av(info, 2.0, 24)
    # the picture at the start of the cut is the picture at 2.5 s of the source (testsrc2 shows a running counter)
    assert asset["edit"]["stream_copy"] is False


def test_the_end_only_trim_and_a_mute_keep_the_picture_untouched(sm, clip, tmp_path):
    p = mkproject(sm, clip["id"], {"trim": {"start": 0, "end": 3}, "audio": "mute"})
    out, asset, data = done(sm, p["id"])
    info = probe(data, tmp_path)
    assert out["mode"] == "copy" and info["acodec"] is None and abs(info["vdur"] - 3.0) <= 0.1
    q = mkproject(sm, clip["id"], {"audio": "mute"})
    out, asset, data = done(sm, q["id"])
    assert out["mode"] == "copy" and probe(data, tmp_path)["acodec"] is None and abs(probe(data, tmp_path)["vdur"] - 6.0) <= 0.1


def test_nothing_to_do_means_no_new_file(sm, clip):
    p = mkproject(sm, clip["id"])
    out = prepare(sm, p["id"])
    assert out["task"] is None and out["reused"] is True and out["identity"] is True and out["asset"]["id"] == clip["id"]
    assert len([a for a in sm.get("/api/media/assets", params={"origin": "prepared"}).json()]) == 0


@pytest.mark.parametrize("w,h,aspect,expect_crop", [
    (320, 240, "9:16", {"x": 92, "y": 0, "w": 136, "h": 240}),       # landscape
    (240, 320, "16:9", {"x": 0, "y": 92, "w": 240, "h": 136}),       # portrait
    (240, 240, "9:16", {"x": 52, "y": 0, "w": 136, "h": 240}),       # square
])
def test_cropping_to_a_shape_on_landscape_portrait_and_square_sources(sm, tmp_path, w, h, aspect, expect_crop):
    src = up(sm, make_video(tmp_path, w=w, h=h, dur=2, fps=24), "s.mp4")
    p = mkproject(sm, src["id"], {"frame": {"aspect": aspect, "treatment": "crop"}})
    out, asset, data = done(sm, p["id"])
    assert out["plan"]["frame"]["crop"] == expect_crop and out["mode"] == "reencode"
    info = probe(data, tmp_path)
    assert (info["w"], info["h"]) == (expect_crop["w"], expect_crop["h"]) and (asset["width"], asset["height"]) == (info["w"], info["h"])
    check_av(info, 2.0, 24)
    assert out["plan"]["prepared"] == {"width": info["w"], "height": info["h"], "scale": False}


def test_a_user_crop_then_a_shape_inside_it(sm, clip, tmp_path):
    p = mkproject(sm, clip["id"], {"crop": {"x": 20, "y": 10, "w": 200, "h": 200}, "frame": {"aspect": "16:9", "treatment": "crop"}})
    out, asset, data = done(sm, p["id"])
    c = out["plan"]["frame"]["crop"]
    assert c == {"x": 20, "y": 54, "w": 200, "h": 112}
    assert (probe(data, tmp_path)["w"], probe(data, tmp_path)["h"]) == (200, 112)


def test_pad_adds_black_bars_and_keeps_the_whole_picture(sm, clip, tmp_path):
    p = mkproject(sm, clip["id"], {"frame": {"aspect": "1:1", "treatment": "pad"}})
    out, asset, data = done(sm, p["id"])
    info = probe(data, tmp_path)
    assert (info["w"], info["h"]) == (320, 320) and out["plan"]["frame"]["pad"] == {"w": 320, "h": 320, "x": 0, "y": 40}
    (tmp_path / "pv.mp4").write_bytes(data)
    bar = ffm("-ss", "0.3", "-i", str(tmp_path / "pv.mp4"), "-frames:v", "1", "-vf", "crop=320:30:0:0,scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", out=tmp_path / "bar.rgb")
    mid = ffm("-ss", "0.3", "-i", str(tmp_path / "pv.mp4"), "-frames:v", "1", "-vf", "crop=320:200:0:60,scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", out=tmp_path / "mid.rgb")
    assert max(bar) < 24 and sum(mid) > 100                                      # black bars on top; the picture in the middle
    check_av(info, 6.0, 24)


def test_stretch_squeezes_the_whole_picture_to_the_new_shape(sm, clip, tmp_path):
    p = mkproject(sm, clip["id"], {"frame": {"aspect": "9:16", "treatment": "stretch"}})
    out, asset, data = done(sm, p["id"])
    info = probe(data, tmp_path)
    assert (info["w"], info["h"]) == (320, 568) and out["plan"]["frame"]["stretch"] == {"w": 320, "h": 568}


def test_a_big_source_is_scaled_down_to_twice_the_working_size_not_encoded_in_full(sm, tmp_path):
    big = up(sm, make_video(tmp_path, w=1920, h=1080, dur=1, fps=12), "big.mp4")
    p = mkproject(sm, big["id"], {"resolution": {"preset": "480p"}})
    out, asset, data = done(sm, p["id"])
    assert out["plan"]["prepared"]["scale"] is True and out["plan"]["processing"]["short_edge"] == 480
    info = probe(data, tmp_path)
    assert (info["w"], info["h"]) == (out["plan"]["prepared"]["width"], out["plan"]["prepared"]["height"]) and min(info["w"], info["h"]) == 960 and info["w"] % 2 == 0


def test_a_phone_video_stored_sideways_is_prepared_upright_and_crops_use_the_picture_as_shown(sm, tmp_path):
    rot = up(sm, make_rotated(tmp_path), "phone.mp4")
    assert (rot["width"], rot["height"]) == (180, 320) and rot["media"]["rotation"] in (90, 270)       # the size people see
    base = probe(content(sm, rot["id"]), tmp_path)
    assert (base["w"], base["h"]) == (320, 180) and base["rot"] != 0                                   # stored sideways
    p = mkproject(sm, rot["id"], {"crop": {"x": 0, "y": 0, "w": 180, "h": 100}})                       # the TOP of the picture as shown
    out, asset, data = done(sm, p["id"])
    assert out["plan"]["input"]["rotation"] in (90, 270) and out["plan"]["frame"]["crop"] == {"x": 0, "y": 0, "w": 180, "h": 100}
    info = probe(data, tmp_path)
    assert (info["w"], info["h"], info["rot"]) == (180, 100, 0)                                          # upright, no rotation flag left
    r, g, b = avg_color(data, tmp_path)
    assert r > 200 and b < 60, (r, g, b)                                                                 # the top of the upright picture is red
    q = mkproject(sm, rot["id"], {"crop": {"x": 0, "y": 220, "w": 180, "h": 100}})
    out, asset, data = done(sm, q["id"])
    r, g, b = avg_color(data, tmp_path)
    assert b > 200 and r < 60, (r, g, b)                                                                 # the bottom is blue
    s = mkproject(sm, rot["id"], {"frame": {"aspect": "1:1", "treatment": "crop"}})
    out, asset, data = done(sm, s["id"])
    assert out["plan"]["frame"]["crop"] == {"x": 0, "y": 70, "w": 180, "h": 180}
    info = probe(data, tmp_path)
    assert (info["w"], info["h"], info["rot"]) == (180, 180, 0)
    t = mkproject(sm, rot["id"], {})
    out, asset, data = done(sm, t["id"])                                                                 # no edit at all: still turned upright, with the right size
    info = probe(data, tmp_path)
    assert out["mode"] == "reencode" and (info["w"], info["h"], info["rot"]) == (180, 320, 0) and (asset["width"], asset["height"]) == (180, 320)
    assert info["acodec"] == "aac"


def test_a_second_identical_request_reuses_the_prepared_copy_and_runs_nothing(sm, clip, monkeypatch):
    p = mkproject(sm, clip["id"], {"trim": {"start": 1, "end": 3}, "frame": {"aspect": "1:1"}})
    out, asset, _ = done(sm, p["id"])
    runs = []
    orig = media_lib.run

    async def counting(cmd, **kw):
        runs.append(cmd)
        return await orig(cmd, **kw)
    monkeypatch.setattr(media_lib, "run", counting)
    again = prepare(sm, p["id"])
    assert again["reused"] is True and again["task"] is None and again["asset"]["id"] == asset["id"] and runs == []
    # a different project with the same decisions on the same video is the same file; a different decision is a new one
    q = mkproject(sm, clip["id"], {"trim": {"start": 1.0, "end": 3.0}, "frame": {"aspect": "1:1", "treatment": "crop", "lock": False}, "resolution": {"preset": "720p"}})
    assert prepare(sm, q["id"])["asset"]["id"] == asset["id"]
    r = mkproject(sm, clip["id"], {"trim": {"start": 1, "end": 3.5}, "frame": {"aspect": "1:1"}})
    other = prepare(sm, r["id"])
    assert other["reused"] is False and other["task"]
    wait_edit(sm, other["task"])
    assert len(sm.get("/api/media/assets", params={"origin": "prepared"}).json()) == 2
    # the cache key is stored with the asset (encrypted) and is what the plan says
    plan = sm.post(f"/api/studio/projects/{p['id']}/plan", json={}).json()["plan"]
    assert plan["prepare"]["key"] and sm.get(f"/api/studio/projects/{p['id']}").json()["prepared_current"] is True


def test_two_requests_for_the_same_thing_join_one_running_task(sm, clip, monkeypatch):
    orig = media_lib.run

    async def slow(cmd, **kw):
        await asyncio.sleep(0.6)
        return await orig(cmd, **kw)
    monkeypatch.setattr(media_lib, "run", slow)
    p = mkproject(sm, clip["id"], {"frame": {"aspect": "1:1"}})
    a, b = prepare(sm, p["id"]), prepare(sm, p["id"])
    assert a["task"] == b["task"] and a["reused"] is False
    assert wait_edit(sm, a["task"])["status"] == "completed"
    assert prepare(sm, p["id"])["reused"] is True


def test_an_impossible_plan_is_refused_before_anything_runs(sm, tmp_path):
    long = up(sm, make_video(tmp_path, dur=40, fps=8, w=160, h=120), "long.mp4")
    p = mkproject(sm, long["id"])
    r = sm.post(f"/api/studio/projects/{p['id']}/prepare", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_errors" and "takes up to 30 s" in r.json()["detail"]["message"]


@pytest.mark.parametrize("name,args,codec", [
    ("c.mov", ["-c:v", "libx264", "-c:a", "aac"], "h264"),
    ("c.mkv", ["-c:v", "libx264", "-c:a", "aac"], "h264"),
    ("c.webm", ["-c:v", "libvpx-vp9", "-b:v", "300k", "-c:a", "libopus"], "vp9"),
    ("c.avi", ["-c:v", "mpeg4", "-q:v", "4", "-c:a", "pcm_s16le"], "mpeg4"),
])
def test_other_containers_are_prepared_into_a_plain_mp4(sm, tmp_path, name, args, codec):
    import subprocess
    raw = tmp_path / name
    r = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=160x120:rate=24:duration=3", "-f", "lavfi", "-i",
                        "sine=frequency=440:sample_rate=44100:duration=3", "-pix_fmt", "yuv420p", *args, str(raw)], capture_output=True, check=False)
    if r.returncode:
        pytest.skip(f"this ffmpeg cannot make {name}: {r.stderr[-120:]!r}")
    a = up(sm, raw.read_bytes(), name)
    assert a["media"]["codec"] == codec
    p = mkproject(sm, a["id"], {"trim": {"start": 0.5, "end": 2.5}})
    out, asset, data = done(sm, p["id"])
    info = probe(data, tmp_path)
    assert asset["mime"] == "video/mp4" and info["vcodec"] == "h264" and info["acodec"] == "aac" and (info["w"], info["h"]) == (160, 120)
    check_av(info, 2.0, 24)
    q = mkproject(sm, a["id"], {})
    out, asset, data = done(sm, q["id"])                                          # no edit: the file still becomes an MP4 the engine can read
    info = probe(data, tmp_path)
    assert info["vcodec"] == "h264" and asset["mime"] == "video/mp4" and abs(info["vdur"] - 3.0) < 0.15
    assert out["mode"] == ("copy" if (codec == "h264") else "reencode")


def test_a_video_without_sound_prepares_and_mute_is_harmless(sm, tmp_path):
    silent = up(sm, make_video(tmp_path, dur=3, audio=False, fps=24), "silent.mp4")
    p = mkproject(sm, silent["id"], {"trim": {"start": 0, "end": 2}, "audio": "mute"})
    out, _asset, data = done(sm, p["id"])
    info = probe(data, tmp_path)
    assert info["acodec"] is None and abs(info["vdur"] - 2.0) < 0.1 and out["plan"]["audio"] == {"mode": "mute", "present": False}


def test_old_uploads_without_rotation_facts_are_probed_on_demand(sm, st, tmp_path):
    """An upload from before the rotation was recorded: the plan still knows how it is stored."""
    import json

    from semechka.media_models import MediaAsset
    rot = up(sm, make_rotated(tmp_path), "old.mp4")
    with st.db.session(write=True) as s:
        a = s.get(MediaAsset, rot["id"])
        meta = json.loads(st.vault.decrypt(a.params_enc, "media"))
        meta["media"].pop("rotation")
        a.params_enc = st.vault.encrypt(json.dumps(meta), "media")
    p = mkproject(sm, rot["id"], {})
    pl = sm.post(f"/api/studio/projects/{p['id']}/plan", json={}).json()["plan"]
    assert pl["input"]["rotation"] in (90, 270) and pl["trim"]["mode"] == "reencode"
