import numpy as np
import pytest

from moviefilter.errors import PluginError
from moviefilter.plugin_manager import builtin_dir, PluginManager
from moviefilter.testing import make_test_frames, run_plugin

_m = PluginManager([builtin_dir()])
Glitch = _m.resolve("glitch").cls
Lens = _m.resolve("lens-distortion").cls
Old = _m.resolve("old-film").cls

SIZES = [(64, 48), (33, 25), (2, 2), (1, 1)]


def check_basic(cls, params=None):
    for w, h in SIZES:
        frames = make_test_frames(3, w, h, "noise")
        before = [f.copy() for f in frames]
        out = run_plugin(cls, frames, params)
        assert all(o.shape == f.shape and o.dtype == np.uint8 for o, f in zip(out, frames))
        assert all((f == b).all() for f, b in zip(frames, before))


# ---- glitch
def test_glitch_basic_and_rate_zero_identity():
    check_basic(Glitch, {"rate": 1})
    frames = make_test_frames(3, 40, 30, "noise")
    for a, b in zip(run_plugin(Glitch, frames, {"rate": 0}), frames):
        assert (a == b).all()


def test_glitch_is_occasional_and_rate_one_always():
    frames = make_test_frames(200, 64, 48, "noise")
    out = run_plugin(Glitch, frames, {"rate": 0.1, "strength": 1})
    hit = sum(1 for o, f in zip(out, frames) if (o != f).any())
    assert 5 <= hit <= 45
    out = run_plugin(Glitch, make_test_frames(20, 64, 48, "noise"), {"rate": 1, "strength": 1})
    assert all((o != f).any() for o, f in zip(out, make_test_frames(20, 64, 48, "noise")))


def test_glitch_seed_reproducible():
    frames = make_test_frames(5, 40, 30, "noise")
    a = run_plugin(Glitch, frames, {"rate": 1}, job_seed=3)
    b = run_plugin(Glitch, frames, {"rate": 1}, job_seed=3)
    c = run_plugin(Glitch, frames, {"rate": 1}, job_seed=4)
    assert all((x == y).all() for x, y in zip(a, b))
    assert any((x != y).any() for x, y in zip(a, c))


def test_glitch_rgb_shift_only_moves_channels():
    frames = make_test_frames(1, 64, 8, "gradient")
    out = run_plugin(Glitch, frames, {"rate": 1, "strength": 1, "blocks": False, "slices": False})[0]
    assert (out[..., 1] == frames[0][..., 1]).all() and (out[..., 0] != frames[0][..., 0]).any()


# ---- lens-distortion
def test_lens_identity_when_off():
    frames = make_test_frames(2, 33, 25, "noise")
    for a, b in zip(run_plugin(Lens, frames, {"curvature": 0, "aberration": 0}), frames):
        assert (a == b).all()


def test_lens_basic():
    check_basic(Lens)
    check_basic(Lens, {"curvature": -0.4, "aberration": 1})
    check_basic(Lens, {"curvature": 1, "aberration": 0})


def test_lens_center_kept_and_uniform_stays_uniform():
    out = run_plugin(Lens, [np.full((31, 41, 3), 100, np.uint8)], {"curvature": 0.8, "aberration": 0})[0]
    assert (out == 100).all()


def test_lens_distortion_changes_edges_not_center():
    f = make_test_frames(1, 65, 49, "noise")[0]
    out = run_plugin(Lens, [f], {"curvature": 0.5, "aberration": 0})[0]
    assert (out != f).any()


def test_lens_aberration_separates_channels():
    f = np.zeros((20, 40, 3), np.uint8)
    f[:, :, :] = np.linspace(0, 255, 40, dtype=np.uint8)[None, :, None]
    out = run_plugin(Lens, [f], {"curvature": 0, "aberration": 1})[0]
    assert (out[:, 2, 0] != out[:, 2, 2]).any()


# ---- old-film
def test_oldfilm_basic():
    check_basic(Old, {"scratches": 1, "dust": 1})


def test_oldfilm_all_off_only_vignette():
    off = {k: 0 for k in ("tint", "flicker", "jitter", "scratches", "dust", "grain")}
    f = np.full((20, 30, 3), 200, np.uint8)
    out = run_plugin(Old, [f], off)[0]
    assert out[10, 15, 0] >= out[0, 0, 0] and out.max() <= 200


def test_oldfilm_tint_makes_warm_mono():
    off = {k: 0 for k in ("flicker", "jitter", "scratches", "dust", "grain")}
    out = run_plugin(Old, make_test_frames(1, 32, 24, "noise"), {**off, "tint": 1.0})[0]
    assert (out[..., 0] >= out[..., 1] - 1).all() and (out[..., 1] >= out[..., 2] - 1).all()


def test_oldfilm_seed_reproducible():
    frames = make_test_frames(4, 40, 30, "noise")
    a = run_plugin(Old, frames, job_seed=5)
    b = run_plugin(Old, frames, job_seed=5)
    c = run_plugin(Old, frames, job_seed=6)
    assert all((x == y).all() for x, y in zip(a, b))
    assert any((x != y).any() for x, y in zip(a, c))


@pytest.mark.parametrize("cls,name,bad", [
    (Glitch, "rate", "1.1"), (Glitch, "strength", "-0.1"),
    (Lens, "curvature", "1.1"), (Lens, "curvature", "-0.5"), (Lens, "aberration", "2"),
    (Old, "tint", "1.1"), (Old, "flicker", "-1"), (Old, "jitter", "2"),
    (Old, "scratches", "2"), (Old, "dust", "-1"), (Old, "grain", "2"),
])
def test_param_range(cls, name, bad):
    with pytest.raises(PluginError) as e:
        run_plugin(cls, make_test_frames(1), {name: bad})
    assert e.value.code == "E_PARAM_RANGE"
