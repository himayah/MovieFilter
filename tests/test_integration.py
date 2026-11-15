"""FFmpeg を使う結合テスト(FFmpeg がなければスキップ)。"""
import os
import signal
import subprocess
import sys
import time
from fractions import Fraction

import numpy as np
import pytest

from conftest import probe, run_cli, write_plugin

pytestmark = pytest.mark.ffmpeg


def frames_of(path, size):
    """動画を RGB フレームの配列にデコードする。"""
    w, h = size
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, np.uint8).reshape(-1, h, w, 3)


def duration(stream):
    return float(stream["duration"])


def test_basic_with_audio(videos, tmp_path):
    out = tmp_path / "out.mp4"
    code, stdout, stderr = run_cli("apply", videos["basic"], "-p", "sepia", "-o", out)
    assert code == 0, stderr
    assert "完了" in stdout and str(out) in stdout and "50 フレーム" in stdout
    v_in, a_in, _ = probe(videos["basic"])
    v, a, d = probe(out)
    assert (v["width"], v["height"]) == (160, 120) and v["codec_name"] == "h264"
    assert Fraction(v["avg_frame_rate"]) == 25 and int(v["nb_frames"]) == 50
    assert v["color_space"] == "bt709" and v["pix_fmt"] == "yuv420p"
    assert a["codec_name"] == "aac" and abs(duration(a) - duration(v)) < 0.1
    assert not list(tmp_path.glob(".*.tmp*"))                         # 一時ファイルが残らない


def test_default_output_name(videos, tmp_path):
    import shutil
    src = tmp_path / "holiday.mp4"
    shutil.copy(videos["silent"], src)
    code, stdout, stderr = run_cli("apply", src, "-p", "sepia", "-q")
    assert code == 0, stderr
    assert (tmp_path / "holiday_sepia.mp4").is_file()
    code, _, stderr = run_cli("apply", src, "-p", "sepia", "-q")
    assert code == 2 and "E_OUTPUT_EXISTS" in stderr
    assert run_cli("apply", src, "-p", "sepia", "-q", "--overwrite")[0] == 0
    # 複数のフィルタは適用順に連結される
    code, _, stderr = run_cli("apply", src, "-p", "sepia", "-p", "glitch", "-q")
    assert code == 0, stderr
    assert (tmp_path / "holiday_sepia_glitch.mp4").is_file()


def test_ntsc_rate_and_sync_and_metadata(videos, tmp_path):
    out = tmp_path / "o.mp4"
    assert run_cli("apply", videos["ntsc"], "-p", "sepia", "-o", out, "-q")[0] == 0
    v, a, d = probe(out)
    assert Fraction(v["avg_frame_rate"]) == Fraction(30000, 1001) and int(v["nb_frames"]) == 90
    assert abs(duration(v) - duration(a)) < 1001 / 30000 + 0.02       # 映像と音声の長さの差は 1 フレーム以内
    assert d["format"]["tags"]["title"] == "TestTitle"                  # メタデータを引き継ぐ


def test_silent_input_and_no_audio_option(videos, tmp_path):
    for src, extra in ((videos["silent"], []), (videos["basic"], ["--no-audio"])):
        out = tmp_path / f"{src.stem}{len(extra)}.mp4"
        assert run_cli("apply", src, "-p", "sepia", "-o", out, "-q", *extra)[0] == 0
        v, a, _ = probe(out)
        assert a is None and int(v["nb_frames"]) > 0


def test_opus_is_reencoded_to_aac(videos, tmp_path):
    out = tmp_path / "o.mp4"
    code, _, err = run_cli("apply", videos["mkv_opus"], "-p", "sepia", "-o", out, "-q")
    assert code == 0, err
    assert probe(out)[1]["codec_name"] == "aac"


def test_odd_size_is_padded(videos, tmp_path):
    out = tmp_path / "o.mp4"
    code, _, err = run_cli("apply", videos["odd"], "-p", "sepia", "-o", out, "-q")
    assert code == 0, err
    v = probe(out)[0]
    assert (v["width"], v["height"]) == (322, 242)


@pytest.mark.parametrize("key", ["offset", "vdelay"])
def test_av_relative_offset_is_preserved(videos, tmp_path, key):
    """音声が遅れて始まる入力・映像が遅れて始まる入力の両方で、映像と音声の相対位置が保たれる。"""
    out = tmp_path / "o.mp4"
    assert run_cli("apply", videos[key], "-p", "sepia", "-o", out, "-q")[0] == 0
    v_in, a_in, _ = probe(videos[key])
    v, a, _ = probe(out)
    before = float(a_in["start_time"]) - float(v_in["start_time"])
    after = float(a["start_time"]) - float(v["start_time"])
    assert after == pytest.approx(before, abs=0.06), (before, after)


def test_rotation_metadata_is_applied_once(videos, tmp_path):
    out = tmp_path / "o.mp4"
    code, _, err = run_cli("apply", videos["rotated"], "-p", "sepia", "-o", out, "-q")
    assert code == 0, err
    v = probe(out)[0]
    assert (v["width"], v["height"]) == (120, 160)          # 回転後の寸法で出力され、回転メタデータは残らない
    assert not v.get("side_data_list")


def test_vfr_becomes_cfr_with_warning(videos, tmp_path):
    out = tmp_path / "o.mp4"
    code, _, err = run_cli("apply", videos["vfr"], "-p", "sepia", "-o", out, "-q")
    assert code == 0, err
    assert "可変フレームレート" in err
    v = probe(out)[0]
    assert v["avg_frame_rate"] == v["r_frame_rate"] and int(v["nb_frames"]) > 10


def test_sepia_colors_and_chain_order(videos, tmp_path):
    out = tmp_path / "o.mp4"
    assert run_cli("apply", videos["basic"], "-p", "sepia", "-p", "sepia:intensity=0.5", "-o", out, "-q")[0] == 0
    fr = frames_of(out, (160, 120)).astype(float)
    mean = fr.mean(axis=(0, 1, 2))
    assert mean[0] >= mean[1] >= mean[2]                    # セピアは R ≥ G ≥ B(圧縮誤差はフレーム平均で吸収)


def test_color_is_preserved_through_pipeline(tmp_path):
    """プラグインなし相当(intensity=0)で、原色パッチの色が往復で保たれる(BT.709 の指定が効いている)。"""
    src = tmp_path / "patch.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0xff0000:s=64x48:d=1:r=10",
                    "-f", "lavfi", "-i", "color=c=0x00ff00:s=64x48:d=1:r=10", "-f", "lavfi",
                    "-i", "color=c=0x808080:s=64x48:d=1:r=10", "-filter_complex",
                    "[0][1][2]concat=n=3:v=1:a=0,format=rgb24,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p",
                    "-c:v", "libx264", "-crf", "12", "-colorspace", "bt709", "-color_primaries", "bt709",
                    "-color_trc", "bt709", "-color_range", "tv", src], check=True)
    out = tmp_path / "o.mp4"
    assert run_cli("apply", src, "-p", "sepia:intensity=0", "-o", out, "-q", "--quality", "high")[0] == 0
    a, b = frames_of(src, (64, 48)), frames_of(out, (64, 48))
    assert len(a) == len(b)
    for i in (5, 15, 25):
        assert np.abs(a[i, 24, 32].astype(int) - b[i, 24, 32].astype(int)).max() <= 4


# ---------------------------------------------------------------- 異常系

def test_broken_and_missing_and_cover_inputs(videos, tmp_path):
    out = tmp_path / "o.mp4"
    code, _, err = run_cli("apply", videos["broken"], "-p", "sepia", "-o", out)
    assert code == 2 and "E_PROBE_FAILED" in err and not out.exists()
    code, _, err = run_cli("apply", tmp_path / "nothing.mp4", "-p", "sepia", "-o", out)
    assert code == 2 and "E_INPUT_NOT_FOUND" in err
    code, _, err = run_cli("apply", videos["cover"], "-p", "sepia", "-o", out)
    assert code == 2 and "E_NO_VIDEO_STREAM" in err and not out.exists()


def test_input_equals_output(videos, tmp_path):
    import shutil
    src = tmp_path / "a.mp4"
    shutil.copy(videos["silent"], src)
    link = tmp_path / "link.mp4"
    link.symlink_to(src)
    for target in (src, link):
        code, _, err = run_cli("apply", src, "-p", "sepia", "-o", target, "--overwrite")
        assert code == 2 and "E_OUTPUT_SAME_AS_INPUT" in err
    assert src.stat().st_size > 0


def test_ffmpeg_not_found(videos, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[tools]\nffmpeg = "/no/such/ffmpeg"\n', encoding="utf-8")
    code, _, err = run_cli("apply", videos["silent"], "-p", "sepia", "-o", tmp_path / "o.mp4", "--config", cfg)
    assert code == 2 and "E_FFMPEG_NOT_FOUND" in err
    assert run_cli("list", "--config", cfg)[0] == 0           # list は FFmpeg を必要としない


def test_config_quality_default_and_cli_override(videos, tmp_path):
    cfg = tmp_path / "c.toml"
    cfg.write_text('[defaults]\nquality = "low"\n', encoding="utf-8")
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    assert run_cli("apply", videos["basic"], "-p", "sepia", "-o", a, "--config", cfg, "-q")[0] == 0
    assert run_cli("apply", videos["basic"], "-p", "sepia", "-o", b, "--config", cfg, "--quality", "high", "-q")[0] == 0
    assert a.stat().st_size < b.stat().st_size


FAILING = '''
import numpy as np
from moviefilter.plugin_api import VideoPlugin
class P(VideoPlugin):
    name = "{name}"
    def process(self, frame, ctx):
{body}
'''


@pytest.mark.parametrize("name,body,code,expect", [
    ("boom", "        if ctx.index == 5: raise ValueError('kaboom')\n        return frame", 3, "E_PLUGIN_RUNTIME"),
    ("badshape", "        return frame[:-2]", 3, "E_PLUGIN_BAD_OUTPUT"),
    ("badtype", "        return frame.astype(np.float32)", 3, "E_PLUGIN_BAD_OUTPUT"),
])
def test_plugin_failure_cleans_up(videos, tmp_path, name, body, code, expect):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, name, FAILING.format(name=name, body=body))
    out = tmp_path / "o.mp4"
    rc, _, err = run_cli("apply", videos["basic"], "-p", name, "-o", out, env={"MOVIEFILTER_PLUGIN_PATH": str(pdir)})
    assert rc == code and expect in err
    if name == "boom":
        assert "フレーム 5" in err and "kaboom" in err and name in err
    assert not out.exists() and not list(tmp_path.glob(".*.tmp*"))


def test_user_plugin_with_params_and_seed(videos, tmp_path):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "noisy", '''
import numpy as np
from moviefilter.plugin_api import *
class N(VideoPlugin):
    name = "noisy"
    params = (ParamSpec("sigma", ParamType.FLOAT, 20.0, min=0.0, max=100.0),)
    def process(self, frame, ctx):
        rng = np.random.default_rng(ctx.seed)
        n = rng.normal(0, self.params_values["sigma"], frame.shape[:2])
        return np.clip(frame + n[:, :, None], 0, 255).astype(np.uint8)
''')
    env = {"MOVIEFILTER_PLUGIN_PATH": str(pdir)}
    outs = {}
    for label, seed in (("a", 1), ("b", 1), ("c", 2)):
        o = tmp_path / f"{label}.mp4"
        assert run_cli("apply", videos["silent"], "-p", "noisy:sigma=30", "--seed", seed, "-o", o, "-q", env=env)[0] == 0
        outs[label] = frames_of(o, (160, 120))
    assert (outs["a"] == outs["b"]).all()                    # 同じシードなら出力フレームが一致
    assert not (outs["a"] == outs["c"]).all()
    code, _, err = run_cli("apply", videos["silent"], "-p", "noisy:sigma=500", "-o", tmp_path / "x.mp4", env=env)
    assert code == 2 and "E_PARAM_RANGE" in err


SLOW = '''
import time
from moviefilter.plugin_api import VideoPlugin
class S(VideoPlugin):
    name = "slow"
    def process(self, frame, ctx):
        time.sleep(0.05)
        return frame
'''


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX のシグナルを使用")
def test_ctrl_c_cleans_up_and_leaves_no_ffmpeg(videos, tmp_path):
    pdir = tmp_path / "plugins"
    write_plugin(pdir, "slow", SLOW)
    out = tmp_path / "o.mp4"
    env = dict(os.environ, MOVIEFILTER_PLUGIN_PATH=str(pdir), PYTHONIOENCODING="utf-8")
    proc = subprocess.Popen([sys.executable, "-m", "moviefilter", "apply", str(videos["long"]), "-p", "slow",
                             "-o", str(out)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8")
    # 実行環境によっては、Python の起動が数十秒止まることがある(WSL 上の Python 3.11.0rc1 で確認)。待ち時間は長めに取る
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and proc.poll() is None and not list(tmp_path.glob(".*.tmp*")):
        time.sleep(0.05)                                                    # 一時ファイルができる(=処理中)まで待つ
    if proc.poll() is not None:
        out, err = proc.communicate()
        pytest.fail(f"処理が始まる前に終了した (終了コード {proc.returncode})\nstdout: {out}\nstderr: {err}")
    if not list(tmp_path.glob(".*.tmp*")):
        proc.kill()
        out, err = proc.communicate()
        pytest.fail(f"処理が始まらなかった\nstdout: {out}\nstderr: {err}")
    time.sleep(0.3)
    proc.send_signal(signal.SIGINT)
    _, err = proc.communicate(timeout=120)
    assert proc.returncode == 130 and "中断" in err
    assert not out.exists() and not list(tmp_path.glob(".*.tmp*"))
    time.sleep(0.3)
    left = subprocess.run(["pgrep", "-f", str(videos["long"])], capture_output=True, text=True).stdout.strip()
    assert left == "", f"FFmpeg の子プロセスが残っている: {left}"


def test_python_m_entry_and_exit_codes(videos, tmp_path):
    assert run_cli("--version")[0] == 0
    code, _, err = run_cli("apply", videos["basic"])
    assert code == 2 and "E_USAGE" in err
