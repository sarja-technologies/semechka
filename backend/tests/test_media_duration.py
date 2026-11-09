"""Video length in seconds through the real app and the engine double: the options carry the model's real limits, 'seconds' becomes the right frames and fps, and what
the engine receives is exactly what the page displayed. Frame-based requests (older clients) keep working."""
from . import fake_sarness as fs
from .test_media import finish, model_id, vid

# the double's video model: frames 5..49 in steps of 4, fps 8..24 (default 16); the organisation limit is 8 s by default


def _opts(mo):
    return mo.get("/api/media/options", params={"kind": "video"}).json()


def test_options_carry_the_models_duration_facts_in_seconds(mo):
    o = _opts(mo)
    d = o["models"][0]["duration"]
    assert d["available"] and d["fps"] == 16 and d["fps_configurable"] is True and d["fps_range"] == [8, 24]
    assert d["min"] == 0.312 and d["max"] == 3.062 and d["step"] == 0.25 and d["default"] == round(o["models"][0]["defaults"]["frames"] / 16, 3)
    assert o["models"][0]["defaults"]["seconds"] == d["default"]
    assert d["options"] is not None and len(d["options"]) == 12                 # 12 valid lengths: few enough to offer as a list
    assert "duration" not in mo.get("/api/media/options", params={"kind": "image"}).json()["models"][0]


def run(mo, **body):
    """Post a video job, let the double run it, and return (response, what the ENGINE received)."""
    base = {"kind": "video", "model_id": model_id(mo, "video"), "prompt": "waves", "width": 512, "height": 320}
    r = mo.post("/api/media/jobs", json={**base, **body})
    if r.status_code in (200, 201, 202):
        finish(mo, r.json()["id"] if "id" in r.json() else r.json()["job"])
        return r, [x for x in fs.STATE["submissions"] if x.get("frames")][-1]
    return r, None


def test_seconds_become_the_nearest_valid_frames_at_the_models_own_fps(mo):
    r, sub = run(mo, seconds=2)
    assert r.status_code in (200, 201, 202), r.text
    assert (sub["frames"], sub["fps"]) == (33, 16)                              # 2 s * 16 fps = 32 -> 33 frames (1 + 4*8): 2.06 s, the value the page shows
    assert (sub["frames"] - 1) % 4 == 0


def test_seconds_larger_than_the_model_or_organisation_allows_are_clamped_to_what_the_page_showed_as_maximum(mo):
    _r, sub = run(mo, seconds=30)
    assert sub["frames"] == 49 and sub["frames"] / sub["fps"] <= 8               # the model's longest clip (3.06 s at 16 fps)


def test_seconds_with_a_supported_fps_override_and_nonsense_values(mo):
    _r, sub = run(mo, seconds=1.5, fps=24)
    assert sub["fps"] == 24 and (sub["frames"] - 1) % 4 == 0 and abs(sub["frames"] / 24 - 1.5) < 0.2
    for bad in (0, -3, "six"):
        r, _ = run(mo, seconds=bad)
        assert r.status_code == 422 and "seconds" in str(r.json()).lower(), (bad, r.text)
    r, _ = run(mo, seconds=2, fps=60)
    assert r.status_code == 422 and "between 8 and 24" in str(r.json())


def test_frame_based_requests_still_work_and_win_when_both_are_sent(mo):
    r, sub = run(mo, frames=17, fps=16)                                         # the older shape
    assert r.status_code in (200, 201, 202) and (sub["frames"], sub["fps"]) == (17, 16)
    _r, sub = run(mo, frames=9, fps=16, seconds=3)
    assert sub["frames"] == 9                                                    # explicit frames are honoured exactly (documented)
    bad, _ = run(mo, frames=18, fps=16)
    assert bad.status_code == 422 and "1 more than a multiple of 4" in str(bad.json())
