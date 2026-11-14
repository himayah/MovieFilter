"""プラグイン作者向けテスト支援と、将来の API 拡張で壊してはいけないコントラクトのテスト。"""
import numpy as np
import pytest

from moviefilter.errors import PluginError, PluginRuntimeError
from moviefilter.plugin_api import (API_VERSION, AudioPlugin, FrameContext, ParamSpec, ParamType,
                                    TemporalWindow, VideoPlugin)
from moviefilter.testing import make_frame_context, make_test_frames, make_video_info, run_plugin


class Minimal(VideoPlugin):
    """process だけを実装した最小プラグイン(API 1.x のどの版でも動くこと)。"""
    name = "minimal"

    def process(self, frame, ctx):
        return frame


class Grain(VideoPlugin):
    name = "grain"
    params = (ParamSpec("sigma", ParamType.FLOAT, 10.0, min=0.0, max=100.0),)

    def process(self, frame, ctx):
        rng = np.random.default_rng(ctx.seed)
        noise = rng.normal(0, self.params_values["sigma"], frame.shape[:2])
        return np.clip(frame + noise[:, :, None], 0, 255).astype(np.uint8)


def test_minimal_plugin_contract():
    frames = make_test_frames(3)
    out = run_plugin(Minimal, frames)
    assert all((a == b).all() for a, b in zip(out, frames))
    assert API_VERSION == (1, 0) and Minimal.requires_api == (1, 0)
    assert Minimal.temporal_window == TemporalWindow(0, 0)


def test_default_process_window_delegates_to_process():
    f = make_test_frames(1)[0]
    assert Minimal().process_window([f], make_frame_context()) is f


def test_reserved_audio_plugin_class_exists():
    assert AudioPlugin.kind.value == "audio"


def test_same_seed_same_result_different_seed_differs():
    frames = make_test_frames(3, pattern="noise")
    a = run_plugin(Grain, frames, job_seed=7)
    b = run_plugin(Grain, frames, job_seed=7)
    c = run_plugin(Grain, frames, job_seed=8)
    assert all((x == y).all() for x, y in zip(a, b))
    assert any((x != y).any() for x, y in zip(a, c))


def test_stacked_noise_plugins_are_decorrelated():
    from moviefilter.pipeline import VideoStageChain, Frame
    f = Frame(make_test_frames(1, pattern="noise")[0], 0, 0.0)
    chain = VideoStageChain([Grain(sigma=20.0), Grain(sigma=20.0)], make_video_info(64, 48), 1)
    chain.setup()
    two = chain.run(f).pixels.astype(int) - f.pixels
    one = run_plugin(Grain, [f.pixels], {"sigma": 20.0}, job_seed=1)[0].astype(int) - f.pixels
    assert np.abs(two).mean() > np.abs(one).mean() * 1.2     # 同じ乱数なら打ち消し/同相で増えない


def test_string_params_like_cli():
    out = run_plugin(Grain, make_test_frames(1), {"sigma": "0"})
    assert (out[0] == make_test_frames(1)[0]).all()
    with pytest.raises(PluginError) as e:
        run_plugin(Grain, make_test_frames(1), {"sigma": "500"})
    assert "E_PARAM_RANGE" in str(e.value)


def test_input_is_read_only_and_violations_detected():
    class Writer(Minimal):
        def process(self, frame, ctx):
            frame[0, 0, 0] = 9
            return frame
    with pytest.raises(PluginRuntimeError):
        run_plugin(Writer, make_test_frames(1))


def test_output_shape_dtype_checked():
    class Resize(Minimal):
        def process(self, frame, ctx):
            return frame[::2]
    with pytest.raises(PluginError) as e:
        run_plugin(Resize, make_test_frames(1))
    assert e.value.code == "E_PLUGIN_BAD_OUTPUT"


def test_lifecycle_order():
    calls = []

    class Life(Minimal):
        def setup(self, info):
            calls.append(("setup", info.width, info.height))

        def process(self, frame, ctx):
            calls.append(("process", ctx.index))
            return frame

        def teardown(self):
            calls.append("teardown")

    run_plugin(Life, make_test_frames(3, 10, 8))
    assert calls == [("setup", 10, 8), ("process", 0), ("process", 1), ("process", 2), "teardown"]


def test_teardown_runs_even_when_process_fails():
    calls = []

    class Fail(Minimal):
        def process(self, frame, ctx):
            raise RuntimeError("x")

        def teardown(self):
            calls.append("teardown")

    with pytest.raises(PluginRuntimeError):
        run_plugin(Fail, make_test_frames(2))
    assert calls == ["teardown"]


def test_make_test_frames_patterns():
    for pat in ("gradient", "noise", "black", "white"):
        fs = make_test_frames(3, 9, 7, pat)
        assert len(fs) == 3 and fs[0].shape == (7, 9, 3) and fs[0].dtype == np.uint8
    assert make_test_frames(1, pattern="black")[0].max() == 0
    assert make_test_frames(1, pattern="white")[0].min() == 255
    assert (make_test_frames(2, pattern="noise")[0] == make_test_frames(2, pattern="noise")[0]).all()
    with pytest.raises(ValueError):
        make_test_frames(1, pattern="x")


def test_make_contexts():
    info = make_video_info(10, 8, 30000, 1001, total_frames=90)
    assert abs(info.fps - 29.97) < 0.01 and info.duration_sec > 3
    ctx = make_frame_context(30, info, seed=5)
    assert isinstance(ctx, FrameContext) and abs(ctx.pts_sec - 30 * 1001 / 30000) < 1e-9 and ctx.seed == 5


def test_template_loads_and_is_passthrough(tmp_path):
    import shutil
    from pathlib import Path
    from moviefilter.plugin_manager import PluginManager
    template = Path(__file__).parent.parent / "templates" / "plugin_template.py"
    # ガイドの手順どおり、ファイル名をプラグイン名(my_effect)に合わせてコピーするとそのままロードできる
    shutil.copy(template, tmp_path / "my_effect.py")
    info = PluginManager([tmp_path]).resolve("my_effect")
    frames = make_test_frames(3, 33, 25, "noise")
    out = run_plugin(info.cls, frames)
    assert all((a == b).all() for a, b in zip(out, frames))
    # コピーせずテンプレートのファイル名のままでは(name と不一致のため)ロードされない
    infos, issues = PluginManager([template.parent]).discover_all()
    assert not infos and any("一致しません" in i.reason for i in issues)
