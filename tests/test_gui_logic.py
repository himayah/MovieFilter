"""GUI の tkinter 非依存部分(cli_text / param_kind / worker)の単体テスト。"""
import shlex
import threading
import time
from pathlib import Path

import pytest

from moviefilter.cli import build_parser, parse_plugin_spec
from moviefilter.errors import CancelledError, PluginError, PluginRuntimeError
from moviefilter.gui.cli_text import build_cli_command, format_spec
from moviefilter.gui.param_kind import CHECK, CHOICE, NUMBER, SLIDER, SPINBOX, TEXT, widget_kind
from moviefilter.gui.worker import Worker
from moviefilter.models import PluginSpec, ProcessRequest
from moviefilter.plugin_api import ParamSpec, ParamType
from moviefilter.plugin_manager import PluginManager, default_search_dirs


def req(**kw):
    base = dict(input_path=Path("in.mp4"), plugins=(PluginSpec("sepia", {}),))
    base.update(kw)
    return ProcessRequest(**base)


# ------------------------------------------------------------ cli_text
def test_cli_command_omits_defaults():
    assert build_cli_command(req(), windows=False) == "moviefilter apply in.mp4 -p sepia"


def test_cli_command_all_options():
    r = req(plugins=(PluginSpec("sepia", {"intensity": "0.8"}), PluginSpec("glitch", {"rate": "0.3", "blocks": "false"})),
            output_path=Path("o.mp4"), quality="high", keep_audio=False, overwrite=True, seed=7)
    text = build_cli_command(r, windows=False)
    assert text == ("moviefilter apply in.mp4 -p sepia:intensity=0.8 -p glitch:rate=0.3,blocks=false "
                    "-o o.mp4 --quality high --no-audio --overwrite --seed 7")


def test_cli_command_round_trips_through_real_parser():
    r = req(input_path=Path("my videos/in 1.mp4"),
            plugins=(PluginSpec("glitch", {"rate": "0.3"}), PluginSpec("sepia", {})),
            output_path=Path("out dir/o.mp4"), quality="low", seed=5, keep_audio=False)
    text = build_cli_command(r, windows=False)
    argv = shlex.split(text)[1:]
    args = build_parser().parse_args(argv)
    assert args.input == r.input_path and args.output == r.output_path
    assert [parse_plugin_spec(t) for t in args.plugin] == list(r.plugins)
    assert args.quality == "low" and args.seed == 5 and args.no_audio


def test_cli_command_windows_quotes_spaces():
    text = build_cli_command(req(input_path=Path("C:/my videos/in.mp4")), windows=True)
    assert text == 'moviefilter apply "C:/my videos/in.mp4" -p sepia'


def test_cli_command_unrepresentable_values():
    assert format_spec(PluginSpec("x", {"a": "1,2"})) is None
    assert format_spec(PluginSpec("x", {"a": "k=v"})) is None
    assert build_cli_command(req(plugins=(PluginSpec("x", {"a": "1,2"}),))) is None


# ------------------------------------------------------------ param_kind
def spec(t, **kw):
    return ParamSpec("p", t, kw.pop("default", 0), **kw)


@pytest.mark.parametrize("s,kind", [
    (spec(ParamType.FLOAT, min=0.0, max=1.0), SLIDER),
    (spec(ParamType.FLOAT), NUMBER),
    (spec(ParamType.FLOAT, min=0.0), NUMBER),
    (spec(ParamType.INT, min=0, max=9), SPINBOX),
    (spec(ParamType.INT), NUMBER),
    (spec(ParamType.BOOL, default=True), CHECK),
    (spec(ParamType.CHOICE, default="a", choices=("a", "b")), CHOICE),
    (spec(ParamType.STR, default=""), TEXT),
])
def test_widget_kind(s, kind):
    assert widget_kind(s) == kind


def test_widget_kind_covers_all_builtin_plugins():
    infos, issues = PluginManager(default_search_dirs()).discover_all()
    assert not issues and len(infos) >= 5
    for info in infos:
        for s in info.params:
            assert widget_kind(s) in {SLIDER, SPINBOX, NUMBER, CHECK, CHOICE, TEXT}


# ------------------------------------------------------------ worker
def wait_events(w, n, timeout=5.0):
    got = []
    end = time.monotonic() + timeout
    while len(got) < n and time.monotonic() < end:
        got += w.drain()
        time.sleep(0.01)
    return got


def test_worker_done_and_progress():
    w = Worker()

    def job(emit, cancel):
        emit(1)
        emit(2)
        return "ok"
    w.submit("run", job)
    evs = wait_events(w, 3)
    assert [(e.kind, e.payload) for e in evs] == [("progress", 1), ("progress", 2), ("done", "ok")]


def test_worker_rejects_second_job_while_busy():
    w = Worker()
    gate = threading.Event()
    w.submit("run", lambda emit, cancel: gate.wait(5))
    assert w.busy
    with pytest.raises(RuntimeError):
        w.submit("run", lambda emit, cancel: None)
    gate.set()
    wait_events(w, 1)
    w.join()
    assert not w.busy


def test_worker_cancel_reaches_job_and_maps_to_cancelled():
    w = Worker()

    def job(emit, cancel):
        assert cancel.wait(5)
        raise CancelledError()
    w.submit("run", job)
    w.cancel()
    (ev,) = wait_events(w, 1)
    assert ev.kind == "cancelled"


def test_worker_classifies_errors():
    w = Worker()
    w.submit("run", lambda e, c: (_ for _ in ()).throw(PluginError("E_PARAM_RANGE", "範囲外", "対処")))
    (ev,) = wait_events(w, 1)
    assert ev.kind == "error" and ev.payload.code == "E_PARAM_RANGE" and ev.payload.detail == "対処"
    w.join()

    def runtime(e, c):
        raise PluginRuntimeError("glitch", 12, ValueError("boom"))
    w.submit("run", runtime)
    (ev,) = wait_events(w, 1)
    assert ev.payload.plugin == "glitch" and ev.payload.frame_index == 12
    w.join()

    def internal(e, c):
        raise ZeroDivisionError("x")
    w.submit("run", internal)
    (ev,) = wait_events(w, 1)
    assert ev.payload.code == "E_INTERNAL" and "ZeroDivisionError" in ev.payload.detail


def test_gui_command_without_tkinter(monkeypatch, capsys):
    import sys
    from moviefilter.cli import main
    monkeypatch.setitem(sys.modules, "tkinter", None)  # import tkinter が ImportError になる
    assert main(["gui"]) == 2
    assert "E_GUI_UNAVAILABLE" in capsys.readouterr().err


# ------------------------------------------------------------ ログ受け皿(R3)
def _record(msg, level=20):
    import logging
    return logging.LogRecord("moviefilter.x", level, __file__, 1, msg, None, None)


def test_log_handler_is_bounded_and_reports_dropped():
    from moviefilter.gui import logbuf
    h = logbuf.QueueLogHandler()
    total = logbuf.MAX_QUEUED + 123
    for i in range(total):
        h.emit(_record(f"line {i}"))
    assert h.queue.qsize() == logbuf.MAX_QUEUED and h.dropped == 123
    got, rounds = [], 0
    while True:                                   # 1 回の取り出しは DRAIN_PER_TICK 行まで
        lines = h.drain()
        assert len(lines) <= logbuf.DRAIN_PER_TICK + 1
        if not lines:
            break
        got += lines
        rounds += 1
    assert rounds == -(-logbuf.MAX_QUEUED // logbuf.DRAIN_PER_TICK)
    assert "123 行を省略" in got[-1] and h.dropped == 0
    assert len(got) == logbuf.MAX_QUEUED + 1


def test_log_handler_formats_levels():
    from moviefilter.gui.logbuf import QueueLogHandler
    h = QueueLogHandler()
    h.emit(_record("ふつう"))
    h.emit(_record("注意", 30))
    h.emit(_record("失敗", 40))
    a, b, c = h.drain()
    assert a.endswith(" ふつう") and "警告: 注意" in b and "エラー: 失敗" in c


def test_log_keeper_keeps_only_the_tail():
    from moviefilter.gui.logbuf import LogKeeper
    k = LogKeeper(max_lines=100)
    k.extend([str(i) for i in range(1000)])
    assert len(k.lines) == 100 and k.tail(3) == ["997", "998", "999"]


# ------------------------------------------------------------ キャッシュ(R4)
def _counting_plugin(pdir, name="counted"):
    from conftest import write_plugin
    write_plugin(pdir, name, f"""
        import builtins
        builtins.__mf_exec_count = getattr(builtins, "__mf_exec_count", 0) + 1
        from moviefilter.plugin_api import VideoPlugin
        class P(VideoPlugin):
            name = "{name}"
            description = "x"
            def process(self, frame, ctx):
                return frame
    """)


def test_plugin_module_is_executed_once_per_manager(tmp_path):
    import builtins
    builtins.__mf_exec_count = 0
    pdir = tmp_path / "plugins"
    _counting_plugin(pdir)
    m = PluginManager([pdir])
    m.discover_all()
    for _ in range(3):
        m.instantiate(PluginSpec("counted", {}))     # 実行(run)ごとの解決
    m.resolve("counted")
    m.discover_all()
    assert builtins.__mf_exec_count == 1
    m.clear_cache()                                    # 明示的に読み直す
    m.resolve("counted")
    assert builtins.__mf_exec_count == 2
    del builtins.__mf_exec_count


def test_failed_plugin_is_not_cached(tmp_path):
    from conftest import write_plugin
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "fixme", "def broken(:\n")
    m = PluginManager([pdir])
    _, issues = m.discover_all()
    assert len(issues) == 1
    _counting_plugin(pdir, "fixme")                    # 直したら、次の読み込みで使える
    infos, issues = m.discover_all()
    assert not issues and [i.name for i in infos] == ["fixme"]


def test_resolve_tools_spawns_ffmpeg_version_once(monkeypatch):
    import subprocess
    from moviefilter import video_io
    calls = []
    monkeypatch.setattr(video_io, "_VERIFIED", {})
    monkeypatch.setattr(video_io.shutil, "which", lambda cmd: f"/fake/{cmd}")

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ffmpeg version test\n", stderr="")
    monkeypatch.setattr(video_io.subprocess, "run", fake_run)
    a = video_io.resolve_tools()
    b = video_io.resolve_tools()
    assert a == b and len(calls) == 1
    monkeypatch.setattr(video_io.shutil, "which", lambda cmd: f"/other/{cmd}")   # パスが変われば確認し直す
    video_io.resolve_tools()
    assert len(calls) == 2
    monkeypatch.setattr(video_io.shutil, "which", lambda cmd: None)              # 消えたら検出できる
    with pytest.raises(Exception) as e:
        video_io.resolve_tools()
    assert getattr(e.value, "code", "") == "E_FFMPEG_NOT_FOUND"
