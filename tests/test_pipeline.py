import numpy as np
import pytest

from moviefilter.errors import PluginError, PluginRuntimeError
from moviefilter.pipeline import Frame, VideoStageChain, derive_seed
from moviefilter.plugin_api import VideoPlugin
from moviefilter.testing import make_test_frames, make_video_info

INFO = make_video_info(8, 6)


class Add(VideoPlugin):
    name = "add"

    def __init__(self, n=1, **kw):
        super().__init__(**kw)
        self.n = n
        self.log = []

    def setup(self, info):
        self.log.append("setup")

    def process(self, frame, ctx):
        self.log.append(("process", ctx.index, ctx.seed))
        return np.clip(frame.astype(np.int16) + self.n, 0, 255).astype(np.uint8)

    def teardown(self):
        self.log.append("teardown")


def frame0():
    return Frame(make_test_frames(1, 8, 6, "black")[0], 0, 0.0)


def test_plugins_applied_in_order():
    a, b = Add(10), Add(5)
    chain = VideoStageChain([a, b], INFO)
    chain.setup()
    out = chain.run(frame0())
    assert (out.pixels == 15).all()


def test_seed_derivation_is_deterministic_and_distinct():
    assert derive_seed(0, 0, 0) == derive_seed(0, 0, 0)
    seeds = {derive_seed(j, p, f) for j in (0, 1) for p in (0, 1) for f in (0, 1)}
    assert len(seeds) == 8                    # job_seed / plugin_index / frame_index のどれが違っても別の値
    assert derive_seed(5, 2, 100) < 2**64


def test_each_plugin_gets_its_own_seed_and_order_does_not_matter():
    a, b = Add(), Add()
    chain = VideoStageChain([a, b], INFO, job_seed=3)
    chain.setup()
    for i in (2, 0, 1):                       # 処理順を入れ替えても各フレームの seed は同じ
        chain.run(Frame(frame0().pixels, i, 0.0))
    seed_a = {e[1]: e[2] for e in a.log if isinstance(e, tuple)}
    seed_b = {e[1]: e[2] for e in b.log if isinstance(e, tuple)}
    assert seed_a != seed_b
    assert seed_a[2] == derive_seed(3, 0, 2) and seed_b[2] == derive_seed(3, 1, 2)


@pytest.mark.parametrize("bad,why", [
    (lambda f: f.astype(np.float32), "float32"),
    (lambda f: f[:, :-1], "shape"),
    (lambda f: list(f), "list"),
])
def test_bad_output_rejected(bad, why):
    class Bad(Add):
        def process(self, frame, ctx):
            return bad(frame)

    chain = VideoStageChain([Bad()], INFO)
    chain.setup()
    with pytest.raises(PluginError) as e:
        chain.run(frame0())
    assert e.value.code == "E_PLUGIN_BAD_OUTPUT" and e.value.exit_code == 3


def test_exception_wrapped_with_plugin_and_frame():
    class Boom(Add):
        name = "boom"
        def process(self, frame, ctx):
            raise ValueError("kaboom")

    chain = VideoStageChain([Boom()], INFO)
    with pytest.raises(PluginRuntimeError) as e:
        chain.run(Frame(frame0().pixels, 7, 0.0))
    assert e.value.plugin == "boom" and e.value.frame_index == 7 and "kaboom" in e.value.detail


def test_writing_to_input_is_detected():
    class Writer(Add):
        def process(self, frame, ctx):
            frame[0, 0, 0] = 1
            return frame

    px = frame0().pixels
    px.flags.writeable = False
    with pytest.raises(PluginRuntimeError):
        VideoStageChain([Writer()], INFO).run(Frame(px, 0, 0.0))


def test_noncontiguous_output_is_made_contiguous():
    class T(Add):
        def process(self, frame, ctx):
            return np.asfortranarray(frame)

    out = VideoStageChain([T()], INFO).run(frame0())
    assert out.pixels.flags.c_contiguous


def test_setup_failure_is_plugin_init_and_teardown_still_runs_for_all():
    class SetupFail(Add):
        def setup(self, info):
            raise RuntimeError("no memory")

    ok = Add()
    chain = VideoStageChain([ok, SetupFail()], INFO)
    with pytest.raises(PluginError) as e:
        chain.setup()
    assert e.value.code == "E_PLUGIN_INIT" and e.value.exit_code == 2
    chain.teardown()
    assert "teardown" in ok.log


def test_teardown_exception_does_not_stop_others(caplog):
    class TdFail(Add):
        def teardown(self):
            raise RuntimeError("td")

    ok = Add()
    chain = VideoStageChain([TdFail(), ok], INFO)
    with caplog.at_level("WARNING", logger="moviefilter"):
        chain.teardown()
    assert "teardown" in ok.log and "teardown が失敗" in caplog.text
