"""Imajutsu: photographic adjustments, checked on real pixels. One code path makes the preview and the final file; the original is never touched."""
import hashlib

import numpy as np
import pytest

from semechka import imajutsu_adjust as A
from semechka import imajutsu_pix as P

from .conftest import make_user
from .imajutsu_helpers import (
    content,
    decode,
    gradient,
    jpeg_of,
    png_of,
    pytestmark_ffmpeg,
    solid,
    up,
    wait_task,
    with_orientation,
)

pytestmark = pytestmark_ffmpeg


def noisy(w=200, h=150, mean=100, sd=20, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return np.clip(mean + rng.normal(0, sd, (h, w, 3)), 0, 255).astype(np.uint8)


def render(tmp_path, arr, recipe, **kw):
    f = tmp_path / "in.png"
    f.write_bytes(png_of(arr))
    r = A.render(f, "image/png", A.normalize_recipe(recipe), **kw)
    return r, decode(r.data, tmp_path, "out.bin")


def lin_mean(a):
    return float(A.srgb_to_linear_u8(a).mean())


# ---- the recipe ------------------------------------------------------------------------------------------------------------------------------------
def test_recipe_validation_and_canonical_form():
    assert A.normalize_recipe(None) == {} and A.normalize_recipe({"exposure": 0, "contrast": 0.0, "flip": False, "rotate": 0}) == {}
    r = A.normalize_recipe({"exposure": 1.23456, "grain": 0.2, "crop": {"x": 1.4, "y": 2, "w": 100, "h": 50}, "output": {"width": 200, "height": 100}})
    assert r == {"exposure": 1.235, "grain": 0.2, "crop": {"x": 1, "y": 2, "w": 100, "h": 50}, "output": {"width": 200, "height": 100}}
    for bad in ({"exposure": 3}, {"exposure": "1"}, {"contrast": True}, {"sharpness": -1}, {"grain": 2}, {"rotate": 45}, {"crop": {"x": 0, "y": 0, "w": 4, "h": 4}}, {"nope": 1},
                {"straighten": 11}, {"output": {"width": 5, "height": 5}}, {"exposure": float("nan")}, [1], {"temperature": 101}):
        with pytest.raises(A.RecipeError):
            A.normalize_recipe(bad)
    assert A.recipe_key({"exposure": 1.0}) == A.recipe_key({"exposure": 1.0}) != A.recipe_key({"exposure": 1.1})


def test_neutral_recipe_is_identity(tmp_path):
    img = noisy()
    r, out = render(tmp_path, img, {})
    assert (r.width, r.height) == (200, 150) and np.array_equal(out, img)
    zeros = {"exposure": 0.0, "contrast": 0.0, "saturation": 0.0, "temperature": 0.0, "tint": 0.0, "highlights": 0.0, "shadows": 0.0, "sharpness": 0.0, "grain": 0.0, "vignette": 0.0}
    back = A.apply_tonal(img, zeros)         # even with every tonal stage switched on at zero, the picture comes back within one level
    assert np.abs(back.astype(int) - img.astype(int)).max() <= 1


# ---- tonal operations on numbers ---------------------------------------------------------------------------------------------------------------------
def test_exposure_plus_and_minus_one_ev_double_and_halve_linear_brightness(tmp_path):
    img = noisy(mean=100, sd=10)
    base = lin_mean(img)
    _, up1 = render(tmp_path, img, {"exposure": 1.0})
    _, dn1 = render(tmp_path, img, {"exposure": -1.0})
    assert lin_mean(up1) / base == pytest.approx(2.0, rel=0.03)
    assert lin_mean(dn1) / base == pytest.approx(0.5, rel=0.03)
    _, half = render(tmp_path, img, {"exposure": 0.5})
    assert lin_mean(half) / base == pytest.approx(2 ** 0.5, rel=0.03)


def test_contrast_saturation_wb_and_tint(tmp_path):
    img = noisy(mean=128, sd=25)
    _, hi = render(tmp_path, img, {"contrast": 60})
    _, lo = render(tmp_path, img, {"contrast": -60})
    assert hi.astype(float).std() > img.astype(float).std() * 1.4 > lo.astype(float).std() * 1.4 or lo.astype(float).std() < img.astype(float).std() * 0.8
    assert abs(hi.mean() - img.mean()) < 4
    col = np.zeros((60, 80, 3), np.uint8)
    col[:] = (200, 90, 60)
    _, grey = render(tmp_path, col, {"saturation": -100})
    assert np.abs(grey[..., 0].astype(int) - grey[..., 1]).max() <= 2 and np.abs(grey[..., 1].astype(int) - grey[..., 2]).max() <= 2
    _, vivid = render(tmp_path, col, {"saturation": 60})
    assert (vivid[..., 0].astype(int) - vivid[..., 2]).mean() > (col[..., 0].astype(int) - col[..., 2]).mean()
    mid = solid(80, 60, (120, 120, 120))
    _, warm = render(tmp_path, mid, {"temperature": 60})
    assert warm[..., 0].mean() > 125 and warm[..., 2].mean() < 115
    assert abs(float(np.dot(warm.reshape(-1, 3).mean(0), [0.2126, 0.7152, 0.0722])) - 120) < 4          # warmer, not brighter
    _, cool = render(tmp_path, mid, {"temperature": -60})
    assert cool[..., 2].mean() > 125 and cool[..., 0].mean() < 115
    _, mag = render(tmp_path, mid, {"tint": 60})
    assert mag[..., 1].mean() < 118 and mag[..., 0].mean() > 120


def test_shadows_and_highlights_touch_only_their_tones(tmp_path):
    img = np.zeros((100, 200, 3), np.uint8)
    img[:, :100] = 40
    img[:, 100:] = 220
    _, lifted = render(tmp_path, img, {"shadows": 80})
    assert lifted[:, :100].mean() > 40 + 25 and abs(float(lifted[:, 100:].mean()) - 220) < 6
    _, deep = render(tmp_path, img, {"shadows": -80})
    assert deep[:, :100].mean() < 32
    _, rec = render(tmp_path, img, {"highlights": -80})
    assert rec[:, 100:].mean() < 220 - 25 and abs(float(rec[:, :100].mean()) - 40) < 6


def test_sharpness_grain_and_vignette(tmp_path):
    edge = np.zeros((60, 120, 3), np.uint8)
    edge[:, 60:] = 200
    _, sharp = render(tmp_path, edge, {"sharpness": 100})
    assert sharp[:, 60:63].max() > 200 and sharp[:, 57:60].max() == 0          # overshoot on the bright side of the edge, clipped black on the dark one
    assert np.abs(sharp[:, :40].astype(int)).max() <= 2                                                # flat areas stay flat
    flat = solid(120, 60, (130, 130, 130))
    _, same = render(tmp_path, flat, {"sharpness": 100})
    assert np.abs(same.astype(int) - 130).max() <= 1
    r1, g1 = render(tmp_path, flat, {"grain": 0.6})
    r2, g2 = render(tmp_path, flat, {"grain": 0.6})
    assert r1.data == r2.data and np.array_equal(g1, g2)                                               # deterministic
    assert g1.astype(float).std() > 2.0 and abs(float(g1.mean()) - 130) < 1.5
    _, g3 = render(tmp_path, flat, {"grain": 0.6, "grain_seed": 7})
    assert not np.array_equal(g1, g3)
    _, g4 = render(tmp_path, flat, {"grain": 0.2})
    assert g4.astype(float).std() < g1.astype(float).std()
    wide = solid(200, 120, (150, 150, 150))
    _, vg = render(tmp_path, wide, {"vignette": 0.8})
    assert vg[60, 100].mean() > 148 and vg[2, 2].mean() < 150 * 0.6 and vg[2, 197].mean() < 150 * 0.6
    assert abs(float(vg[60, 100].mean()) - 150) <= 2


def test_grain_pattern_is_the_same_at_every_size():
    a = A.grain_field(768, 1024, 1)                      # the preview's size (long edge 1024) is the canonical grid itself
    b = A.grain_field(1536, 2048, 1)                     # the export, twice as large: the same pattern, resampled
    small = P.scale(((np.clip(b, -3, 3) + 3) / 6 * 255).astype(np.uint8), 1024, 768, "area").astype(float)
    ref = ((np.clip(a, -3, 3) + 3) / 6 * 255)
    assert np.corrcoef(small.ravel(), ref.ravel())[0, 1] > 0.8                       # the preview shows the grain the export has


# ---- geometry ----------------------------------------------------------------------------------------------------------------------------------------
def test_rotate_flip_crop_and_output_size_are_exact(tmp_path):
    img = gradient(200, 100)
    r, out = render(tmp_path, img, {"rotate": 90})
    assert (r.width, r.height) == (100, 200) and np.array_equal(out, np.rot90(img, -1))          # clockwise
    r, out = render(tmp_path, img, {"rotate": 180})
    assert np.array_equal(out, img[::-1, ::-1])
    r, out = render(tmp_path, img, {"flip": True})
    assert np.array_equal(out, img[:, ::-1])
    r, out = render(tmp_path, img, {"crop": {"x": 20, "y": 10, "w": 100, "h": 50}})
    assert (r.width, r.height) == (100, 50) and np.array_equal(out, img[10:60, 20:120])
    r, out = render(tmp_path, img, {"crop": {"x": 20, "y": 10, "w": 100, "h": 50}, "output": {"width": 200, "height": 100}})
    assert (r.width, r.height) == (200, 100) and out.shape == (100, 200, 3)
    r, out = render(tmp_path, img, {"rotate": 90, "crop": {"x": 0, "y": 0, "w": 100, "h": 100}})           # the crop is in the turned picture
    assert (r.width, r.height) == (100, 100)
    with pytest.raises(A.RecipeError):
        render(tmp_path, img, {"crop": {"x": 150, "y": 0, "w": 100, "h": 50}})
    with pytest.raises(A.RecipeError):
        render(tmp_path, img, {"output": {"width": 100, "height": 100}})                            # another shape: refused, never stretched


def test_straighten_keeps_the_largest_rectangle_without_empty_corners(tmp_path):
    img = solid(400, 300, (200, 160, 120))
    r, out = render(tmp_path, img, {"straighten": 5})
    import math
    t = math.radians(5)
    s = min(1.0, 400 / (400 * math.cos(t) + 300 * math.sin(t)), 300 / (400 * math.sin(t) + 300 * math.cos(t)))
    assert (r.width, r.height) == (int(400 * s), int(300 * s)) and r.width < 400
    assert out.min() > 100                                               # no black corners
    assert abs(r.width / r.height - 4 / 3) < 0.02


def test_exif_orientation_is_honoured(tmp_path):
    img = gradient(80, 40)                                               # stored landscape, left dark -> right bright in the red channel
    jpg = with_orientation(jpeg_of(img), 6)                              # 'rotate 90 clockwise to view'
    f = tmp_path / "o.jpg"
    f.write_bytes(jpg)
    r = A.render(f, "image/jpeg", {}, orientation=P.orientation_of(jpg, "image/jpeg"))
    assert (r.width, r.height) == (40, 80)
    out = decode(r.data, tmp_path, "o2.bin")
    # after turning clockwise the stored left edge (dark red) is at the top
    assert out[:8, :, 0].mean() < out[-8:, :, 0].mean()


def test_preview_and_final_share_the_code_path(tmp_path, monkeypatch):
    calls = []
    real = A.apply_tonal
    monkeypatch.setattr(A, "apply_tonal", lambda rgb, r: (calls.append((rgb.shape, dict(r))), real(rgb, r))[1])
    img = gradient(640, 480)
    f = tmp_path / "in.png"
    f.write_bytes(png_of(img))
    recipe = A.normalize_recipe({"exposure": 0.5, "contrast": 20, "saturation": 10, "vignette": 0.3, "temperature": 10})
    pv = A.render(f, "image/png", recipe, max_edge=160)
    fin = A.render(f, "image/png", recipe)
    assert len(calls) == 2 and calls[0][1] == calls[1][1] == recipe and calls[0][0][1] == 160 and calls[1][0][1] == 640
    assert pv.mime == "image/jpeg" and (pv.width, pv.height) == (160, 120) and fin.mime == "image/png"
    a = decode(pv.data, tmp_path, "pv.bin").astype(float)
    b = P.scale(decode(fin.data, tmp_path, "fin.bin"), 160, 120, "area").astype(float)
    assert np.abs(a - b).mean() < 3.0                                    # the preview is the final, smaller


# ---- over HTTP -----------------------------------------------------------------------------------------------------------------------------------------
def test_preview_route_is_a_small_cached_jpeg(im, tmp_path):
    a = up(im, png_of(gradient(2400, 1600)), "big.png")
    r = im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": '{"exposure":0.5}'})
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    out = decode(r.content, tmp_path)
    assert max(out.shape[:2]) == 1024 and out.shape[:2] == (683, 1024)
    key = r.headers["x-recipe-key"]
    r2 = im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": '{"exposure": 0.5}'})
    assert r2.content == r.content and r2.headers["x-recipe-key"] == key           # same recipe, same cache entry
    assert im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": '{"exposure":0.6}'}).headers["x-recipe-key"] != key
    assert im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": '{"exposure":9}'}).status_code == 422
    assert im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": "{nope"}).status_code == 422
    assert im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview").status_code == 200       # no recipe = the picture as it is


def test_adjust_makes_a_new_asset_and_never_touches_the_original(im, tmp_path):
    orig = png_of(np.clip(gradient(300, 200).astype(float) * 0.6 + 40, 0, 255).astype(np.uint8))        # smooth, so the JPEG preview can be compared with the PNG file
    a = up(im, orig, "photo.png")
    before = hashlib.sha256(content(im, a["id"])).hexdigest()
    assert im.post(f"/api/imajutsu/assets/{a['id']}/adjust", json={"recipe": {}}).status_code == 422
    r = im.post(f"/api/imajutsu/assets/{a['id']}/adjust", json={"recipe": {"exposure": 1.0, "grain": 0.3}})
    assert r.status_code == 200, r.text
    t = wait_task(im, r.json()["task"])
    assert t["status"] == "completed", t
    new = t["asset"]
    assert new["origin"] == "adjusted" and new["parent"] == a["id"] and new["id"] != a["id"] and new["name"] == "photo (adjusted).png"
    assert new["imajutsu"]["kind"] == "adjusted" and new["imajutsu"]["recipe"] == {"exposure": 1.0, "grain": 0.3} and new["imajutsu"]["model"] is None
    assert hashlib.sha256(content(im, a["id"])).hexdigest() == before == hashlib.sha256(orig).hexdigest()
    out = decode(content(im, new["id"]), tmp_path)
    assert out.shape == (200, 300, 3) and lin_mean(out) > lin_mean(decode(orig, tmp_path, "o.bin")) * 1.7
    # the export equals what the preview showed, only bigger: preview of the ORIGINAL with the same recipe, compared at the preview's size
    pv = decode(im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": '{"exposure":1.0,"grain":0.3}'}).content, tmp_path, "pv.bin")
    assert pv.shape == out.shape and np.abs(pv.astype(float) - out.astype(float)).mean() < 4                 # (the preview is a JPEG; the file a PNG)
    listed = im.get("/api/media/assets", params={"origin": "adjusted"}).json()
    assert [x["id"] for x in listed] == [new["id"]]


def test_scaling_is_lanczos_labelled_and_exact(im, tmp_path):
    a = up(im, png_of(gradient(300, 200)), "small.png")
    r = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 600})
    assert r.status_code == 200 and r.json()["scaling"] == "upscale" and r.json()["ai"] is False and r.json()["method"] == "lanczos"
    t = wait_task(im, r.json()["task"])
    new = t["asset"]
    assert (new["width"], new["height"]) == (900, 600) and new["origin"] == "scaled" and new["parent"] == a["id"]
    assert new["imajutsu"]["kind"] == "scaled" and new["imajutsu"]["ai"] is False and new["imajutsu"]["label"] == "Scaled, no new detail" and new["imajutsu"]["method"] == "lanczos"
    out = decode(content(im, new["id"]), tmp_path)
    assert out.shape == (600, 900, 3) and abs(float(out[:, :, 0].mean()) - 127.5) < 3
    d = im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 150, "height": 100})
    assert d.json()["scaling"] == "downscale" and d.json()["label"] == "Scaled down"
    wait_task(im, d.json()["task"])
    assert im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 900, "height": 900}).status_code == 422          # another shape
    assert im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 300, "height": 200}).status_code == 422          # already that size
    assert im.post(f"/api/imajutsu/assets/{a['id']}/upscale", json={"width": 9000, "height": 6000}).status_code == 422         # beyond 8K


def test_jpeg_with_exif_orientation_is_adjusted_upright(im, tmp_path):
    jpg = with_orientation(jpeg_of(gradient(120, 60)), 6)
    a = up(im, jpg, "phone.jpg")
    assert (a["width"], a["height"]) == (60, 120)                                         # the size people see
    t = wait_task(im, im.post(f"/api/imajutsu/assets/{a['id']}/adjust", json={"recipe": {"contrast": 10}}).json()["task"])
    assert (t["asset"]["width"], t["asset"]["height"]) == (60, 120) and t["asset"]["mime"] == "image/jpeg"
    pv = im.get(f"/api/imajutsu/assets/{a['id']}/adjust/preview", params={"recipe": '{"contrast":10}'})
    assert decode(pv.content, tmp_path).shape[:2] == (120, 60)


def test_adjust_routes_are_private(im, app, tmp_path):
    a = up(im, png_of(noisy(100, 80)), "mine.png")
    other = make_user(im, app, "eve@example.test")
    for method, url, kw in [("get", f"/api/imajutsu/assets/{a['id']}/adjust/preview", {"params": {"recipe": "{}"}}),
                            ("post", f"/api/imajutsu/assets/{a['id']}/adjust", {"json": {"recipe": {"exposure": 1}}}),
                            ("post", f"/api/imajutsu/assets/{a['id']}/upscale", {"json": {"width": 200, "height": 160}}),
                            ("post", f"/api/imajutsu/assets/{a['id']}/keep-outside", {"json": {}})]:
        r = getattr(other, method)(url, **kw)
        assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found", (url, r.text)
        assert getattr(im, method)("/api/imajutsu/assets/ma_doesnotexist" + url.split(a["id"])[1], **kw).status_code == 404
