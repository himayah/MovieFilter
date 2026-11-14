import numpy as np
import pytest

from moviefilter.errors import PluginError
from moviefilter.plugin_manager import builtin_dir, PluginManager
from moviefilter.testing import make_test_frames, run_plugin

Sepia = PluginManager([builtin_dir()]).resolve("sepia").cls


def px(*rgb):
    return [np.full((2, 2, 3), rgb, np.uint8)]


@pytest.mark.parametrize("src,expected", [
    ((0, 0, 0), (0, 0, 0)),
    ((255, 255, 255), (255, 255, 239)),
    ((255, 0, 0), (100, 89, 69)),
    ((0, 255, 0), (196, 175, 136)),
    ((0, 0, 255), (48, 43, 33)),
])
def test_known_pixels(src, expected):
    out = run_plugin(Sepia, px(*src))[0]
    assert (out == np.array(expected, np.uint8)).all()


def test_intensity_zero_is_identity():
    frames = make_test_frames(3, 33, 25, "noise")
    for a, b in zip(run_plugin(Sepia, frames, {"intensity": 0.0}), frames):
        assert (a == b).all()


def test_shape_dtype_and_input_unchanged():
    frames = make_test_frames(2, 33, 25, "noise")
    before = [f.copy() for f in frames]
    out = run_plugin(Sepia, frames)
    assert all(o.shape == f.shape and o.dtype == np.uint8 for o, f in zip(out, frames))
    assert all((f == b).all() for f, b in zip(frames, before))


def test_uniform_regardless_of_frame_index_and_position():
    out = run_plugin(Sepia, px(120, 80, 40) * 4)
    ref = out[0][0, 0]
    assert all((o == ref).all() for o in out)


def test_r_ge_g_ge_b_for_all_pixels():
    out = run_plugin(Sepia, make_test_frames(3, 64, 48, "noise"))
    for o in out:
        assert (o[..., 0] >= o[..., 1]).all() and (o[..., 1] >= o[..., 2]).all()


def test_consecutive_outputs_are_independent_copies():
    a, b = run_plugin(Sepia, px(255, 0, 0) + px(0, 0, 255))
    assert not (a == b).all()          # run_plugin は各フレームをコピーして返す(再利用バッファの影響を受けない)


@pytest.mark.parametrize("v", ["-0.1", "1.1"])
def test_intensity_out_of_range(v):
    with pytest.raises(PluginError) as e:
        run_plugin(Sepia, px(1, 2, 3), {"intensity": v})
    assert e.value.code == "E_PARAM_RANGE"


def test_half_intensity_is_between():
    full = run_plugin(Sepia, px(255, 0, 0))[0][0, 0]
    half = run_plugin(Sepia, px(255, 0, 0), {"intensity": 0.5})[0][0, 0]
    assert tuple(half) != tuple(full)
    assert abs(int(half[0]) - (255 + int(full[0])) / 2) <= 1
