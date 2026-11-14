import json
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def pytest_collection_modifyitems(config, items):
    if HAS_FFMPEG:
        return
    skip = pytest.mark.skip(reason="FFmpeg がないためスキップ")
    for item in items:
        if "ffmpeg" in item.keywords:
            item.add_marker(skip)


def write_plugin(directory: Path, stem: str, source: str, package: bool = False) -> Path:
    """テスト用プラグインを書き出す。package=True なら <stem>/__init__.py 形式。"""
    directory.mkdir(parents=True, exist_ok=True)
    body = textwrap.dedent(source)
    if package:
        (directory / stem).mkdir(exist_ok=True)
        path = directory / stem / "__init__.py"
    else:
        path = directory / f"{stem}.py"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def plugin_dir(tmp_path):
    return tmp_path / "plugins"


def ffmpeg(*args):
    r = subprocess.run(["ffmpeg", "-v", "error", "-y", *map(str, args)], capture_output=True, text=True)
    if r.returncode != 0:
        pytest.skip(f"テスト動画を作れません: {r.stderr.strip()[:200]}")


def probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format",
                        str(path)], capture_output=True, text=True, check=True)
    d = json.loads(r.stdout)
    video = next((s for s in d["streams"] if s["codec_type"] == "video"), None)
    audio = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    return video, audio, d


def run_cli(*args, env=None, cwd=None):
    import os
    e = dict(os.environ)
    e["PYTHONIOENCODING"] = "utf-8"
    e.update(env or {})
    r = subprocess.run([sys.executable, "-m", "moviefilter", *map(str, args)], capture_output=True,
                       text=True, encoding="utf-8", env=e, cwd=cwd)
    return r.returncode, r.stdout, r.stderr


@pytest.fixture(scope="session")
def videos(tmp_path_factory):
    """結合テスト用の短い動画(都度生成する)。"""
    if not HAS_FFMPEG:
        return {}
    d = tmp_path_factory.mktemp("videos")
    v = {}
    v["basic"] = d / "basic.mp4"            # 25fps / 音声あり(AAC)
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=25", "-f", "lavfi",
           "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", v["basic"])
    v["ntsc"] = d / "ntsc.mp4"              # 29.97fps
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=30000/1001", "-f", "lavfi",
           "-i", "sine=duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-metadata",
           "title=TestTitle", v["ntsc"])
    v["silent"] = d / "silent.mp4"          # 音声なし
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=25", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", v["silent"])
    v["odd"] = d / "odd.mp4"                # 奇数サイズ(yuv444p なら奇数も可能)
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=1:size=321x241:rate=25", "-c:v", "libx264",
           "-pix_fmt", "yuv444p", v["odd"])
    v["offset"] = d / "offset.mp4"          # 音声の開始が 0.5 秒遅れる
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=25", "-itsoffset", "0.5", "-f", "lavfi",
           "-i", "sine=duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", v["offset"])
    v["vdelay"] = d / "vdelay.mp4"          # 映像の開始が 0.5 秒遅れる(音声は 0 秒から)
    ffmpeg("-itsoffset", "0.5", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=25", "-f", "lavfi",
           "-i", "sine=duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", v["vdelay"])
    v["rotated"] = d / "rotated.mp4"        # 回転メタデータ(90度)付き
    ffmpeg("-i", v["basic"], "-c", "copy", "-metadata:s:v:0", "rotate=90", v["rotated"])
    v["vfr"] = d / "vfr.mp4"                # 可変フレームレート
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=30", "-vf", "select='lt(mod(n,5),3)'",
           "-vsync", "vfr", "-c:v", "libx264", "-pix_fmt", "yuv420p", v["vfr"])
    v["mkv_opus"] = d / "opus.mkv"          # MP4 にコピーできない音声(Opus)
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=25", "-f", "lavfi", "-i", "sine=duration=1",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libopus", v["mkv_opus"])
    v["long"] = d / "long.mp4"              # 中断テスト用(処理を遅くするプラグインと組み合わせる)
    ffmpeg("-f", "lavfi", "-i", "testsrc=duration=20:size=64x48:rate=25", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", v["long"])
    v["broken"] = d / "broken.mp4"
    v["broken"].write_bytes(b"this is not a video" * 100)
    v["cover"] = d / "cover.mp3"            # カバー画像付き音声
    ffmpeg("-f", "lavfi", "-i", "color=c=red:s=64x64:d=1", "-frames:v", "1", d / "cover.png")
    ffmpeg("-f", "lavfi", "-i", "sine=duration=1", "-i", d / "cover.png", "-map", "0:a", "-map", "1:v",
           "-c:a", "libmp3lame", "-c:v", "mjpeg", "-disposition:v", "attached_pic", v["cover"])
    return v
