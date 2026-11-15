"""ViewModel の単体・結合テスト(tkinter は使わない)。"""
import subprocess
import time
from pathlib import Path

import pytest

from conftest import probe, run_cli, write_plugin
from moviefilter.config import Config
from moviefilter.errors import InputError
from moviefilter.gui.viewmodel import AppState, ViewModel, format_media, format_progress, hms
from moviefilter.models import AudioInfo, MediaInfo
from moviefilter.plugin_api import VideoInfo
from moviefilter.plugin_manager import PluginManager, default_search_dirs
from moviefilter.profiles import default_registry
from moviefilter.service import Progress, default_output_path


def make_media(path, audio=True, vfr=False):
    v = VideoInfo(160, 120, 25, 1, 50, 2.0)
    return MediaInfo(path, "mov,mp4", v, "h264", "vfr" if vfr else "cfr", 0.0,
                     AudioInfo("aac", 44100, 1, 0.0) if audio else None)


def settle(vm, state=None, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        vm.worker.join(0.05)
        vm.pump()
        if not vm.worker.busy and (state is None or vm.state is state):
            return
    raise AssertionError(f"状態が {state} になりません: {vm.state}")


@pytest.fixture
def vm(tmp_path):
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    v = ViewModel(Config(), PluginManager(default_search_dirs()), default_registry(),
                  probe=lambda p: make_media(p))
    v.src = src
    return v


def loaded(vm):
    vm.set_input(vm.src)
    settle(vm, AppState.EDITING)
    return vm


# ------------------------------------------------------------ 状態遷移・入力
def test_initial_state_and_plugin_list(vm):
    assert vm.state is AppState.EMPTY and not vm.check().ok
    names = [i.name for i in vm.infos]
    assert {"sepia", "glitch", "analog-tv", "old-film", "lens-distortion"} <= set(names)


def test_probe_success_sets_default_output(vm):
    events = []
    vm.listeners.append(lambda e, p: events.append(e))
    vm.set_input(vm.src)
    assert vm.state is AppState.PROBING and not vm.editable
    settle(vm, AppState.EDITING)
    assert vm.media is not None and "probe" in events
    assert vm.output_text == str(default_output_path(vm.src, [], ".mp4"))
    assert vm.output_text.endswith("in_filtered.mp4")   # 適用リストが空のあいだは "_filtered"


def test_probe_failure_returns_to_empty(tmp_path):
    def bad(path):
        raise InputError("E_PROBE_FAILED", "解析できません")
    v = ViewModel(Config(), PluginManager(default_search_dirs()), default_registry(), probe=bad)
    v.set_input(tmp_path / "x.mp4")
    settle(v, AppState.EMPTY)
    assert v.media is None and v.probe_error.code == "E_PROBE_FAILED"


def test_default_output_follows_applied_filters(vm):
    loaded(vm)
    vm.add_plugin("sepia")
    assert vm.output_text.endswith("in_sepia.mp4")
    vm.add_plugin("glitch")
    assert vm.output_text.endswith("in_sepia_glitch.mp4")
    vm.move_plugin(1, -1)                      # 適用順が入れ替わると名前も変わる
    assert vm.output_text.endswith("in_glitch_sepia.mp4")
    vm.remove_plugin(0)
    assert vm.output_text.endswith("in_sepia.mp4")
    vm.set_output_text(str(vm.src.with_name("mine.mp4")))   # 手で編集したら、以後は変えない
    vm.add_plugin("glitch")
    assert vm.output_text.endswith("mine.mp4")


def test_default_output_path_rules(tmp_path):
    p = tmp_path / "a.mov"
    assert default_output_path(p, ["sepia"], ".mp4") == tmp_path / "a_sepia.mp4"
    assert default_output_path(p, ["old-film", "sepia"], ".mp4") == tmp_path / "a_old-film_sepia.mp4"
    assert default_output_path(p, [], ".mp4") == tmp_path / "a_filtered.mp4"
    long_names = ["analog-tv"] * 20                         # 長すぎるときは "_filtered"
    assert default_output_path(p, long_names, ".mp4") == tmp_path / "a_filtered.mp4"


def test_manual_output_is_kept_when_input_changes(vm, tmp_path):
    loaded(vm)
    vm.set_output_text(str(tmp_path / "mine.mp4"))
    other = tmp_path / "other.mp4"
    other.write_bytes(b"x")
    vm.set_input(other)
    settle(vm, AppState.EDITING)
    assert vm.output_text.endswith("mine.mp4")


def test_profile_change_resyncs_untouched_output(vm):
    loaded(vm)
    before = vm.output_text
    vm.set_profile("mp4-h264")
    assert vm.output_text == before


# ------------------------------------------------------------ 適用リスト
def test_chain_add_move_remove_and_duplicates(vm):
    loaded(vm)
    vm.add_plugin("sepia")
    vm.add_plugin("glitch")
    vm.add_plugin("sepia")  # 同じプラグインを複数回
    assert [e.name for e in vm.chain] == ["sepia", "glitch", "sepia"] and vm.selected == 2
    vm.move_plugin(2, -1)
    assert [e.name for e in vm.chain] == ["sepia", "sepia", "glitch"] and vm.selected == 1
    vm.move_plugin(0, -1)  # 範囲外は何もしない
    assert [e.name for e in vm.chain] == ["sepia", "sepia", "glitch"]
    vm.remove_plugin(1)
    assert [e.name for e in vm.chain] == ["sepia", "glitch"] and vm.selected == 1
    vm.remove_plugin(0)
    vm.remove_plugin(0)
    assert vm.chain == [] and vm.selected is None


def test_unknown_plugin_is_ignored(vm):
    vm.add_plugin("no-such-plugin")
    assert vm.chain == []


def test_audio_plugin_cannot_be_added(tmp_path):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "echo", """
        from moviefilter.plugin_api import AudioPlugin
        class Echo(AudioPlugin):
            name = "echo"
            description = "audio"
    """)
    v = ViewModel(Config(), PluginManager(default_search_dirs([pdir])), default_registry(),
                  probe=lambda p: make_media(p))
    info = v.info_of("echo")
    assert info is not None and not v.is_addable(info)
    v.add_plugin("echo")
    assert v.chain == []


# ------------------------------------------------------------ パラメータ
def test_param_default_is_not_stored_and_invalid_is_reported(vm):
    loaded(vm)
    vm.add_plugin("glitch")
    vm.set_param(0, "rate", "0.3")
    assert vm.chain[0].values == {"rate": "0.3"}
    vm.set_param(0, "rate", "0.10")       # 既定値(0.1)と同じ値 → 保持から外れる
    assert vm.chain[0].values == {}
    vm.set_param(0, "rate", "5")          # 範囲外
    assert "rate" in vm.param_errors(0)
    chk = vm.check()
    assert not chk.ok and "glitch" in chk.reason and "rate" in chk.reason
    vm.set_param(0, "rate", "abc")        # 型不正
    assert "rate" in vm.param_errors(0)
    vm.reset_param(0, "rate")
    assert vm.param_errors(0) == {} and vm.check().ok


def test_bool_param_validation(vm):
    loaded(vm)
    vm.add_plugin("glitch")
    vm.set_param(0, "blocks", "false")
    assert vm.chain[0].values == {"blocks": "false"} and not vm.param_errors(0)
    vm.set_param(0, "blocks", "maybe")
    assert "blocks" in vm.param_errors(0)
    vm.reset_all_params(0)
    assert vm.chain[0].values == {}


# ------------------------------------------------------------ 実行可否
def test_check_reasons_in_order(vm, tmp_path):
    assert vm.check().reason == "入力動画を選択してください"
    loaded(vm)
    assert "プラグイン" in vm.check().reason
    vm.add_plugin("sepia")
    assert vm.check().ok
    vm.set_seed_text("-1")
    assert "シード" in vm.check().reason
    vm.set_seed_text("12")
    assert vm.check().ok and vm.build_request().seed == 12
    vm.set_output_text(str(tmp_path / "nodir" / "o.mp4"))
    assert "出力先フォルダ" in vm.check().reason
    vm.set_output_text(str(tmp_path / "o.txt"))
    assert "拡張子" in vm.check().reason
    vm.set_output_text(str(vm.src))
    assert "同じファイル" in vm.check().reason


def test_existing_output_requires_confirmation(vm, tmp_path):
    loaded(vm)
    vm.add_plugin("sepia")
    out = tmp_path / "exists.mp4"
    out.write_bytes(b"old")
    vm.set_output_text(str(out))
    chk = vm.check()
    assert chk.ok and chk.needs_overwrite_confirm
    assert vm.start() is False and vm.state is AppState.EDITING   # 確認なしでは開始しない
    vm.set_overwrite(True)
    assert vm.check() == vm.check() and not vm.check().needs_overwrite_confirm


def test_tool_error_blocks_run(tmp_path):
    cfg = Config(ffmpeg="no-such-ffmpeg-binary", ffprobe="no-such-ffprobe-binary")
    v = ViewModel(cfg, PluginManager(default_search_dirs()), default_registry())
    assert v.tool_error and "ffmpeg" in v.tool_error
    v.set_input(tmp_path / "a.mp4")
    settle(v, AppState.EMPTY)
    assert v.probe_error.code == "E_FFMPEG_NOT_FOUND"
    assert not v.check().ok


def test_cli_command_reflects_settings(vm):
    loaded(vm)
    assert vm.cli_command() is None   # 適用リストが空
    vm.add_plugin("sepia")
    vm.set_param(0, "intensity", "0.5")
    vm.set_quality("high")
    vm.set_keep_audio(False)
    text = vm.cli_command()
    assert "-p sepia:intensity=0.5" in text and "--quality high" in text and "--no-audio" in text


# ------------------------------------------------------------ 表示用の整形
def test_formatters():
    assert hms(83) == "0:01:23" and hms(None) == "--:--:--"
    assert format_progress(None) == ""
    assert format_progress(Progress(25, 100, 1.0, 3.0)) == "25%  25/100  残り約0:00:03"
    assert format_progress(Progress(100, 100, 1.0, 0.0, True)) == "100%  100/100"
    assert format_progress(Progress(7, None, 62.0, None)) == "7 フレーム  経過 0:01:02"
    m = format_media(make_media(Path("a.mp4")))
    assert "160x120" in m and "25fps" in m and "aac 1ch" in m and "50フレーム" in m
    assert "音声: なし" in format_media(make_media(Path("a.mp4"), audio=False))


# ------------------------------------------------------------ 実処理(FFmpeg が必要)
def framemd5(path):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", "-f", "framemd5", "-"],
                       capture_output=True, text=True, check=True)
    return [ln.split(",")[-1].strip() for ln in r.stdout.splitlines() if ln and not ln.startswith("#")]


@pytest.mark.ffmpeg
def test_gui_run_matches_cli_output(videos, tmp_path):
    v = ViewModel(Config(), PluginManager(default_search_dirs()), default_registry())
    v.set_input(videos["basic"])
    settle(v, AppState.EDITING)
    assert v.media.video.width == 160
    v.add_plugin("sepia")
    v.set_param(0, "intensity", "0.7")
    v.add_plugin("glitch")
    v.set_param(1, "rate", "0.5")
    v.set_seed_text("3")
    v.set_output_text(str(tmp_path / "gui.mp4"))
    progress = []
    v.listeners.append(lambda e, p: progress.append(p) if e == "progress" else None)
    assert v.start() and v.state is AppState.RUNNING and not v.editable
    settle(v, AppState.EDITING)
    assert v.last_result is not None and v.last_result.frames_processed == 50
    assert progress and progress[-1].done

    cli_out = tmp_path / "cli.mp4"
    code, _, err = run_cli("apply", videos["basic"], "-p", "sepia:intensity=0.7", "-p", "glitch:rate=0.5",
                           "--seed", "3", "-o", cli_out)
    assert code == 0, err
    assert framemd5(tmp_path / "gui.mp4") == framemd5(cli_out)
    # 生成した CLI コマンドの引数も同じ設定になっている
    assert "glitch:rate=0.5" in v.cli_command() and "--seed 3" in v.cli_command()


@pytest.mark.ffmpeg
def test_gui_cancel_leaves_no_files(videos, tmp_path):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "slow", """
        import time
        from moviefilter.plugin_api import VideoPlugin
        class Slow(VideoPlugin):
            name = "slow"
            description = "slow"
            def process(self, frame, ctx):
                time.sleep(0.02)
                return frame
    """)
    v = ViewModel(Config(), PluginManager(default_search_dirs([pdir])), default_registry())
    v.set_input(videos["long"])
    settle(v, AppState.EDITING)
    v.add_plugin("slow")
    out = tmp_path / "out.mp4"
    v.set_output_text(str(out))
    assert v.start()
    end = time.monotonic() + 10
    while v.progress is None and time.monotonic() < end:   # 処理が始まるまで待つ
        v.worker.join(0.02)
        v.pump()
    v.cancel()
    assert v.state is AppState.CANCELLING and not v.check().ok
    settle(v, AppState.EDITING)
    assert v.last_result is None and v.last_error is None and "中止" in v.status
    assert not out.exists() and not list(tmp_path.glob(".*.tmp*"))


@pytest.mark.ffmpeg
def test_gui_error_is_reported_with_plugin_and_frame(videos, tmp_path):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "boom", """
        from moviefilter.plugin_api import VideoPlugin
        class Boom(VideoPlugin):
            name = "boom"
            description = "fails"
            def process(self, frame, ctx):
                if ctx.index == 3:
                    raise ValueError("kaboom")
                return frame
    """)
    v = ViewModel(Config(), PluginManager(default_search_dirs([pdir])), default_registry())
    v.set_input(videos["basic"])
    settle(v, AppState.EDITING)
    v.add_plugin("boom")
    out = tmp_path / "o.mp4"
    v.set_output_text(str(out))
    events = []
    v.listeners.append(lambda e, p: events.append(e))
    assert v.start()
    settle(v, AppState.EDITING)
    assert v.last_error.code == "E_PLUGIN_RUNTIME" and v.last_error.plugin == "boom"
    assert v.last_error.frame_index == 3 and "error" in events and not out.exists()


# ------------------------------------------------------------ ファイルシステム検査の使い回し(R2)
class _CountingService:
    def __init__(self, real):
        self.real, self.calls = real, 0

    def validate_request(self, req):
        self.calls += 1
        return self.real.validate_request(req)

    def run(self, *a, **kw):
        return self.real.run(*a, **kw)


def test_filesystem_check_is_reused_while_dragging_sliders(tmp_path):
    from moviefilter.service import ProcessingService
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    manager = PluginManager(default_search_dirs())
    svc = _CountingService(ProcessingService(Config(), manager, default_registry()))
    vm = ViewModel(Config(), manager, default_registry(), service=svc, probe=lambda p: make_media(p))
    vm.set_input(src)
    settle(vm, AppState.EDITING)
    vm.add_plugin("glitch")
    assert vm.check().ok
    base = svc.calls
    for i in range(50):                                   # スライダーを動かす
        vm.set_param(0, "rate", "0.3" if i % 2 else "0.4")
        assert vm.check().ok
    assert svc.calls == base                              # ディスクには問い合わせていない
    vm.set_param(0, "rate", "9")                          # メモリ上の検査は毎回効く
    assert not vm.check().ok and svc.calls == base
    vm.set_param(0, "rate", "0.3")
    vm.set_output_text(str(tmp_path / "other.mp4"))       # 出力先が変われば確かめ直す
    assert vm.check().ok and svc.calls == base + 1
    assert vm.check(fresh=True).ok and svc.calls == base + 2   # 実行直前は必ず確かめ直す


def test_filesystem_check_notices_output_created_by_a_run(tmp_path):
    from moviefilter.service import ProcessingService
    src = tmp_path / "in.mp4"
    src.write_bytes(b"x")
    out = tmp_path / "o.mp4"
    manager = PluginManager(default_search_dirs())
    vm = ViewModel(Config(), manager, default_registry(),
                   service=ProcessingService(Config(), manager, default_registry()),
                   probe=lambda p: make_media(p))
    vm.set_input(src)
    settle(vm, AppState.EDITING)
    vm.add_plugin("sepia")
    vm.set_output_text(str(out))
    assert not vm.check().needs_overwrite_confirm
    out.write_bytes(b"made by someone else")              # 外部でファイルができても…
    assert not vm.check().needs_overwrite_confirm         # …キャッシュのあいだは古い結果(許容)
    assert vm.check(fresh=True).needs_overwrite_confirm   # 実行時の確認は必ず最新
    vm.worker.events.put(__import__("moviefilter.gui.worker", fromlist=["Event"]).Event("run", "done", None))
    vm.pump()                                             # 処理の完了通知でキャッシュは捨てられる
    assert vm.check().needs_overwrite_confirm
