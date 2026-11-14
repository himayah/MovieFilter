import numpy as np
import pytest

from moviefilter.errors import PluginError
from moviefilter.plugin_manager import builtin_dir, PluginManager
from moviefilter.testing import make_test_frames, run_plugin

Tv = PluginManager([builtin_dir()]).resolve("analog-tv").cls
OFF = {"softness": 0, "scanlines": 0, "grain": 0, "noise_rate": 0}


def test_all_off_is_identity():
    frames = make_test_frames(3, 33, 25, "noise")
    for a, b in zip(run_plugin(Tv, frames, OFF), frames):
        assert (a == b).all()


def test_shape_dtype_and_input_unchanged():
    frames = make_test_frames(3, 33, 25, "noise")
    before = [f.copy() for f in frames]
    out = run_plugin(Tv, frames, {"noise_rate": 1})
    assert all(o.shape == f.shape and o.dtype == np.uint8 for o, f in zip(out, frames))
    assert all((f == b).all() for f, b in zip(frames, before))


def test_scanlines_darken_odd_rows_only():
    out = run_plugin(Tv, make_test_frames(1, 16, 8, "white"), {**OFF, "scanlines": 1.0})[0]
    assert (out[0::2] == 255).all() and (out[1::2] < 255).all()


def test_softness_blurs_horizontal_edge():
    f = np.zeros((4, 16, 3), np.uint8)
    f[:, 8:] = 255
    out = run_plugin(Tv, [f], {**OFF, "softness": 1.0})[0]
    assert 0 < out[0, 7, 0] < 255 or 0 < out[0, 8, 0] < 255


def test_noise_rate_zero_means_no_bursts_and_one_means_all():
    frames = make_test_frames(10, 64, 48, "black")
    base = {**OFF, "noise_strength": 1.0}
    assert all((o == 0).all() for o in run_plugin(Tv, frames, {**base, "noise_rate": 0}))
    assert all((o > 0).any() for o in run_plugin(Tv, frames, {**base, "noise_rate": 1}))


def test_noise_is_occasional():
    frames = make_test_frames(200, 32, 24, "black")
    out = run_plugin(Tv, frames, {**OFF, "noise_rate": 0.1})
    hit = sum(1 for o in out if o.any())
    assert 5 <= hit <= 45


def test_same_seed_same_result_different_seed_differs():
    frames = make_test_frames(5, 32, 24, "noise")
    p = {"noise_rate": 1}
    a = run_plugin(Tv, frames, p, job_seed=7)
    b = run_plugin(Tv, frames, p, job_seed=7)
    c = run_plugin(Tv, frames, p, job_seed=8)
    assert all((x == y).all() for x, y in zip(a, b))
    assert any((x != y).any() for x, y in zip(a, c))


def test_tiny_frame():
    run_plugin(Tv, make_test_frames(2, 2, 2, "noise"), {"noise_rate": 1, "softness": 1})
    run_plugin(Tv, make_test_frames(2, 1, 1, "noise"), {"noise_rate": 1, "softness": 1})


@pytest.mark.parametrize("name", ["softness", "scanlines", "grain", "noise_rate", "noise_strength"])
@pytest.mark.parametrize("v", ["-0.1", "1.1"])
def test_params_out_of_range(name, v):
    with pytest.raises(PluginError) as e:
        run_plugin(Tv, make_test_frames(1), {name: v})
    assert e.value.code == "E_PARAM_RANGE"
