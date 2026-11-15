"""Seconds <-> frames for video models: the single source of truth (semechka.video_duration). Pure functions, no app needed."""
import pytest

from semechka import video_duration as vd

LTX = {"frames": [9, 241], "frame_step": 8, "fps": [24, 24]}          # 8n+1 frames, fixed 24 fps
WAN = {"frames": [5, 129], "frame_step": 4, "fps": [8, 24]}           # 4n+1 frames, fps is genuinely configurable
DFL_LTX, DFL_WAN = {"fps": 24, "frames": 121}, {"fps": 16, "frames": 33}


def test_valid_frames_follow_the_models_alignment_rule():
    assert vd.valid_frames(LTX, DFL_LTX)[:3] == [9, 17, 25] and vd.valid_frames(LTX, DFL_LTX)[-1] == 241
    assert all((f - 1) % 8 == 0 for f in vd.valid_frames(LTX, DFL_LTX))
    assert vd.valid_frames({"frames": [6, 30], "frame_step": 4}, {"fps": 16}) == [9, 13, 17, 21, 25, 29]     # a range start that is not aligned begins at the next valid count


def test_spec_for_a_fixed_fps_model_hides_fps_and_shows_real_limits():
    s = vd.spec(LTX, DFL_LTX, max_seconds=15)
    assert s["fps"] == 24 and s["fps_configurable"] is False and s["available"]
    assert s["min"] == pytest.approx(9 / 24, abs=1e-3)
    assert s["max"] == pytest.approx(241 / 24, abs=1e-3) and s["step"] == pytest.approx(8 / 24, abs=1e-3)
    assert s["default"] == pytest.approx(121 / 24, abs=1e-3) and s["options"] is None


def test_spec_marks_fps_configurable_only_when_the_model_offers_a_range():
    s = vd.spec(WAN, DFL_WAN, max_seconds=60)
    assert s["fps_configurable"] is True and s["fps_range"] == [8, 24] and s["fps"] == 16


def test_few_valid_lengths_are_listed_and_declared_durations_win():
    s = vd.spec({"frames": [17, 33], "frame_step": 8, "fps": [24, 24]}, {"fps": 24, "frames": 17})
    assert s["options"] == [round(17 / 24, 3), round(25 / 24, 3), round(33 / 24, 3)]
    d = vd.spec({"frames": [9, 241], "frame_step": 8, "fps": [24, 24], "durations": [5, 10]}, {"fps": 24, "frames": 121}, max_seconds=8)
    assert d["options"] == [5.0] and d["max"] == 5.0 and d["declared"] is True and s["declared"] is False                    # only specific lengths exist, and the 10 s one exceeds the organisation limit
    t = vd.to_frames(9, {"frames": [9, 241], "frame_step": 8, "fps": [24, 24], "durations": [5, 10]}, {"fps": 24})
    assert t["adjusted"] is True and t["seconds"] == pytest.approx(10, abs=0.05)        # 9 s asked: snaps to the nearest declared length (10 s -> 241 frames at 24 fps)
    assert vd.to_frames(6, {"frames": [9, 241], "frame_step": 8, "fps": [24, 24], "durations": [5, 10]}, {"fps": 24})["seconds"] == pytest.approx(5, abs=0.05)


def test_to_frames_returns_the_actual_duration_never_a_silent_difference():
    t = vd.to_frames(6, WAN, DFL_WAN, max_seconds=60)
    assert t == {"frames": 97, "fps": 16, "seconds": 6.0625, "requested": 6, "adjusted": False}      # 97 = 1 + 4*24: the nearest valid count to 96
    assert (t["frames"] - 1) % 4 == 0
    u = vd.to_frames(6, LTX, DFL_LTX, max_seconds=60)
    assert u["fps"] == 24 and (u["frames"] - 1) % 8 == 0 and abs(u["seconds"] - 6) <= 8 / 24 / 2 + 1e-9
    assert u["frames"] / u["fps"] == pytest.approx(u["seconds"], abs=1e-3)


def test_to_frames_clamps_to_the_model_and_to_the_organisation_limit():
    assert vd.to_frames(0.1, WAN, DFL_WAN)["frames"] == 5 and vd.to_frames(0.1, WAN, DFL_WAN)["adjusted"] is True
    assert vd.to_frames(999, WAN, DFL_WAN)["frames"] == 129
    capped = vd.to_frames(30, WAN, DFL_WAN, max_seconds=5)
    assert capped["seconds"] <= 5 and capped["adjusted"] is True
    assert vd.to_frames(30, LTX, DFL_LTX, max_seconds=10)["seconds"] <= 10


def test_fps_override_only_inside_the_models_range():
    t = vd.to_frames(4, WAN, DFL_WAN, fps=24)
    assert t["fps"] == 24 and abs(t["seconds"] - 4) < 0.1 and (t["frames"] - 1) % 4 == 0
    with pytest.raises(vd.DurationError, match="between 8 and 24"):
        vd.to_frames(4, WAN, DFL_WAN, fps=60)
    with pytest.raises(vd.DurationError, match="between 24 and 24"):
        vd.to_frames(4, LTX, DFL_LTX, fps=30)


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, "6", None])
def test_nonsense_durations_are_refused_with_a_sentence(bad):
    with pytest.raises(vd.DurationError, match="seconds greater than zero"):
        vd.to_frames(bad, WAN, DFL_WAN)


def test_no_length_fits_an_organisation_limit_is_reported_not_crashed():
    s = vd.spec({"frames": [33, 65], "frame_step": 8, "fps": [24, 24]}, {"fps": 24}, max_seconds=1)
    assert s["available"] is False and "limit" in s["why"]
    with pytest.raises(vd.DurationError, match="shortest clip"):
        vd.to_frames(2, {"frames": [33, 65], "frame_step": 8, "fps": [24, 24]}, {"fps": 24}, max_seconds=1)


def test_every_requested_second_maps_to_a_valid_frame_count():
    for lim, dfl in ((LTX, DFL_LTX), (WAN, DFL_WAN)):
        step = lim["frame_step"]
        for tenth in range(5, 400, 7):
            t = vd.to_frames(tenth / 10, lim, dfl, max_seconds=12)
            assert (t["frames"] - 1) % step == 0 and lim["frames"][0] <= t["frames"] <= lim["frames"][1] and t["seconds"] <= 12 + 1e-9


def test_check_frames_keeps_the_older_frame_based_requests_valid():
    assert vd.check_frames(33, 16, WAN, DFL_WAN) == pytest.approx(33 / 16)
    for frames, fps, word in ((32, 16, "1 more than a multiple of 4"), (200, 16, "between 5 and 129"), (33, 99, "fps must be")):
        with pytest.raises(vd.DurationError, match=word):
            vd.check_frames(frames, fps, WAN, DFL_WAN)
    with pytest.raises(vd.DurationError, match="limits clips"):
        vd.check_frames(129, 8, WAN, DFL_WAN, max_seconds=5)
