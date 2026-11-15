"""メインウィンドウのスモークテスト(tkinter と表示環境がなければスキップ)。"""
import pytest

tk = pytest.importorskip("tkinter")

from conftest import write_plugin  # noqa: E402
from moviefilter.config import Config  # noqa: E402
from moviefilter.gui.app import MainWindow
from moviefilter.gui.logbuf import QueueLogHandler  # noqa: E402
from moviefilter.gui.viewmodel import AppState, ViewModel  # noqa: E402
from moviefilter.plugin_manager import PluginManager, default_search_dirs  # noqa: E402
from moviefilter.profiles import default_registry  # noqa: E402
from test_gui_viewmodel import make_media, settle  # noqa: E402


@pytest.fixture
def root():
    try:
        r = tk.Tk()
    except tk.TclError:
        pytest.skip("表示環境がありません")
    yield r
    r.destroy()


def test_window_builds_and_shows_forms_for_all_plugins(root, tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    vm = ViewModel(Config(), PluginManager(default_search_dirs()), default_registry(),
                   probe=lambda p: make_media(p))
    w = MainWindow(root, vm, None, QueueLogHandler())
    assert w.plugin_list.size() == len(vm.infos) >= 5
    assert str(w.run_btn.state()) == "('disabled',)"

    w._load_input(str(src))
    settle(vm, AppState.EDITING)
    root.update()
    assert "160x120" in w.media_label.cget("text")

    for info in vm.infos:                       # 全プラグインのフォームを生成できる
        vm.add_plugin(info.name)
        root.update()
        assert w._form is not None and w._form.info.name == info.name
    assert w.chain_list.size() == len(vm.infos)
    assert str(w.run_btn.state()) == "()"       # 実行可能

    vm.set_param(vm.selected, "nonexistent", "1")          # 無視される(例外にならない)
    glitch = next(i for i, e in enumerate(vm.chain) if e.name == "glitch")
    vm.select(glitch)
    root.update()
    vm.set_param(glitch, "rate", "9")
    root.update()
    assert "⚠" in w.chain_list.get(glitch)
    assert str(w.run_btn.state()) == "('disabled',)"
    assert "rate" in w.status_label.cget("text")


def test_settings_disabled_while_running(root, tmp_path):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "wait", """
        import threading
        from moviefilter.plugin_api import VideoPlugin
        class Wait(VideoPlugin):
            name = "wait"
            description = "x"
            def process(self, frame, ctx):
                return frame
    """)
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")

    class FakeService:
        def __init__(self):
            self.release = __import__("threading").Event()

        def validate_request(self, req):
            return None, req.output_path

        def run(self, req, on_progress=None, cancel=None):
            from moviefilter.errors import CancelledError
            while not cancel.is_set():
                if self.release.wait(0.01):
                    break
            raise CancelledError()

    svc = FakeService()
    vm = ViewModel(Config(), PluginManager(default_search_dirs([pdir])), default_registry(),
                   service=svc, probe=lambda p: make_media(p))
    w = MainWindow(root, vm, None, QueueLogHandler())
    w._load_input(str(src))
    settle(vm, AppState.EDITING)
    vm.add_plugin("wait")
    vm.set_output_text(str(tmp_path / "o.mp4"))
    root.update()
    assert vm.start()
    root.update()
    assert str(w.run_btn.state()) == "('disabled',)" and str(w.cancel_btn.state()) == "()"
    assert "disabled" in w.add_btn.state() and "disabled" in w.del_btn.state()
    assert "disabled" in w.input_entry.state()
    w.cancel_btn.invoke()
    assert vm.state is AppState.CANCELLING
    settle(vm, AppState.EDITING)
    root.update()
    assert str(w.run_btn.state()) == "()" and "中止" in w.status_label.cget("text")


def _tcl_var_count(root):
    return len(root.tk.splitlist(root.tk.call("info", "vars")))


def test_switching_selection_does_not_leak_tcl_variables(root, tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    vm = ViewModel(Config(), PluginManager(default_search_dirs()), default_registry(),
                   probe=lambda p: make_media(p))
    w = MainWindow(root, vm, None, QueueLogHandler())
    w._load_input(str(src))
    settle(vm, AppState.EDITING)
    for name in ("analog-tv", "glitch", "sepia", "old-film", "lens-distortion"):
        vm.add_plugin(name)
    root.update()
    for i in range(10):                      # ウォームアップ
        vm.select(i % 5)
        root.update()
    import gc
    gc.collect()
    before = _tcl_var_count(root)
    for i in range(200):
        vm.select(i % 5)
        root.update()
    gc.collect()
    assert _tcl_var_count(root) - before <= 5, "フォームを作り直すたびに Tcl 変数が増えている"


def test_log_view_is_trimmed(root, tmp_path):
    import logging
    from moviefilter.gui.logbuf import MAX_KEPT
    vm = ViewModel(Config(), PluginManager(default_search_dirs()), default_registry(),
                   probe=lambda p: make_media(p))
    handler = QueueLogHandler()
    w = MainWindow(root, vm, None, handler)
    for i in range(MAX_KEPT * 2 + 300):
        handler.emit(logging.LogRecord("moviefilter.x", 20, __file__, 1, f"line {i}", None, None))
    for _ in range(40):                      # 1 回 500 行ずつ取り込まれる
        w._tick()
    shown = int(w.log_text.index("end-1c").split(".")[0]) - 1
    assert shown <= MAX_KEPT
    assert len(w._log.lines) <= MAX_KEPT
    assert "line 4299" in w.log_text.get("end-3l", "end")     # 末尾(最新)は残っている
