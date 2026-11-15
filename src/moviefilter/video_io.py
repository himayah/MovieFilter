"""FFprobe / FFmpeg との連携(詳細設計書 §5.2)。"""
from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Iterator

import numpy as np

from .errors import InputError, ToolError
from .models import AudioInfo, MediaInfo
from .plugin_api import VideoInfo
from .profiles import AudioMode, OutputProfile

log = logging.getLogger("moviefilter.video_io")

STDERR_TAIL_BYTES = 16 * 1024


@dataclass(frozen=True)
class ToolPaths:
    ffmpeg: str
    ffprobe: str


_VERIFIED: dict[tuple[str, str], ToolPaths] = {}  # (ffmpeg, ffprobe の解決済みパス) → 動作確認済み


def resolve_tools(ffmpeg: str = "ffmpeg", ffprobe: str = "ffprobe") -> ToolPaths:
    """FFmpeg / FFprobe を解決し、動作確認とバージョンのログ出力を行う。

    `ffmpeg -version` の実行(子プロセスの起動)は、同じパスについて 1 プロセスにつき 1 回だけ行う。
    パスの解決(`shutil.which`)は毎回行うので、実行ファイルが消えた場合は検出できる。
    """
    found = {}
    for label, cmd in (("ffmpeg", ffmpeg), ("ffprobe", ffprobe)):
        path = shutil.which(cmd)
        if path is None:
            raise ToolError("E_FFMPEG_NOT_FOUND", f"{label} が見つかりません: {cmd}",
                            "FFmpeg をインストールして PATH を通すか、設定ファイルの [tools] でパスを指定してください")
        found[label] = path
    key = (found["ffmpeg"], found["ffprobe"])
    if key in _VERIFIED:
        return _VERIFIED[key]
    try:
        r = subprocess.run([found["ffmpeg"], "-version"], capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            raise OSError(r.stderr.strip())
        log.info("FFmpeg: %s", r.stdout.splitlines()[0] if r.stdout else "?")
    except (OSError, subprocess.SubprocessError) as e:
        raise ToolError("E_FFMPEG_NOT_FOUND", f"ffmpeg を実行できません: {found['ffmpeg']}", str(e)) from e
    tools = _VERIFIED[key] = ToolPaths(found["ffmpeg"], found["ffprobe"])
    return tools


def _popen_kwargs() -> dict:
    """子プロセスが Ctrl+C を直接受けないよう、別プロセスグループで起動する。"""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


class _StderrTail:
    """子プロセスの stderr を常時読み出し(デッドロック防止)、末尾だけを保持する。"""

    def __init__(self, stream):
        self._chunks: deque[bytes] = deque()
        self._size = 0
        self._thread = threading.Thread(target=self._run, args=(stream,), daemon=True)
        self._thread.start()

    def _run(self, stream) -> None:
        try:
            for chunk in iter(lambda: stream.read(4096), b""):
                self._chunks.append(chunk)
                self._size += len(chunk)
                while self._size > STDERR_TAIL_BYTES and len(self._chunks) > 1:
                    self._size -= len(self._chunks.popleft())
        except (OSError, ValueError):
            pass

    def text(self) -> str:
        self._thread.join(timeout=2)
        return b"".join(self._chunks).decode("utf-8", "replace").strip()[-STDERR_TAIL_BYTES:]


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------- Prober

def _frac(text: str | None) -> Fraction | None:
    if not text:
        return None
    try:
        f = Fraction(text)
    except (ValueError, ZeroDivisionError):
        return None
    return f if f > 0 else None


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rotation(stream: dict) -> int:
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            return int(round(float(sd["rotation"])))
    tag = (stream.get("tags") or {}).get("rotate")
    return int(round(float(tag))) if tag not in (None, "") else 0


def parse_probe(data: dict, path: Path) -> MediaInfo:
    """ffprobe の JSON から MediaInfo を作る(単体テスト可能な純粋関数)。"""
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")), None)
    if video is None:
        raise InputError("E_NO_VIDEO_STREAM", f"映像ストリームが見つかりません: {path}")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format", {})

    avg, r = _frac(video.get("avg_frame_rate")), _frac(video.get("r_frame_rate"))
    fps = avg or r
    if fps is None:
        raise InputError("E_PROBE_FAILED", f"フレームレートを取得できません: {path}")
    is_vfr = bool(avg and r and abs(avg - r) / r > Fraction(5, 1000))

    width, height = int(video["width"]), int(video["height"])
    if abs(_rotation(video)) % 180 == 90:  # FFmpeg は自動回転するため、デコード後の寸法に合わせる
        width, height = height, width

    duration = _float(video.get("duration")) or _float(fmt.get("duration"))
    nb = video.get("nb_frames")
    if not is_vfr and nb and str(nb).isdigit() and int(nb) > 0:
        total = int(nb)
    elif duration:
        total = max(1, int(round(duration * float(fps))))
    else:
        total = None

    info = VideoInfo(width, height, fps.numerator, fps.denominator, total, duration)
    audio_info = None
    if audio is not None:
        audio_info = AudioInfo(audio.get("codec_name", "?"), int(audio.get("sample_rate", 0) or 0),
                               int(audio.get("channels", 0) or 0), _float(audio.get("start_time")) or 0.0)
    return MediaInfo(
        path=path,
        container=fmt.get("format_name", "?"),
        video=info,
        source_video_codec=video.get("codec_name", "?"),
        source_fps_mode="vfr" if is_vfr else "cfr",
        video_start_sec=_float(video.get("start_time")) or 0.0,
        audio=audio_info,
        video_stream_index=int(video["index"]),
        audio_stream_index=int(audio["index"]) if audio is not None else None,
    )


class Prober:
    def __init__(self, ffprobe: str):
        self.ffprobe = ffprobe

    def probe(self, path: Path) -> MediaInfo:
        cmd = [self.ffprobe, "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(path)]
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as e:
            raise InputError("E_PROBE_FAILED", f"動画情報を取得できません: {path}", str(e)) from e
        if r.returncode != 0:
            raise InputError("E_PROBE_FAILED", f"動画情報を取得できません: {path}",
                             r.stderr.decode("utf-8", "replace").strip()[-STDERR_TAIL_BYTES:])
        try:
            data = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            raise InputError("E_PROBE_FAILED", f"動画情報を取得できません: {path}", str(e)) from e
        return parse_probe(data, path)


# ---------------------------------------------------------------- Decoder

def build_decode_args(ffmpeg: str, media: MediaInfo) -> list[str]:
    args = [ffmpeg, "-v", "error", "-nostdin", "-i", str(media.path),
            "-map", f"0:{media.video_stream_index}", "-an", "-sn", "-dn"]
    if media.source_fps_mode == "vfr":  # 可変フレームレートは固定フレームレートへ正規化する
        args += ["-vf", f"fps={media.video.fps_num}/{media.video.fps_den}"]
    return args + ["-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]


class Decoder:
    def __init__(self, ffmpeg: str, media: MediaInfo):
        self._ffmpeg = ffmpeg
        self._media = media
        self._proc: subprocess.Popen | None = None
        self._stderr: _StderrTail | None = None

    def __enter__(self) -> "Decoder":
        cmd = build_decode_args(self._ffmpeg, self._media)
        log.debug("decode: %s", " ".join(cmd))
        try:
            self._proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                          stdin=subprocess.DEVNULL, **_popen_kwargs())
        except OSError as e:
            raise ToolError("E_DECODE_FAILED", "動画の読み込みを開始できません", str(e)) from e
        self._stderr = _StderrTail(self._proc.stderr)
        return self

    def __exit__(self, *exc) -> None:
        if self._proc is not None:
            _stop(self._proc)
            for s in (self._proc.stdout, self._proc.stderr):
                if s:
                    s.close()

    def frames(self) -> Iterator[np.ndarray]:
        """単一の再利用バッファへ読み込み、読み取り専用のビューを yield する(次の反復まで有効)。"""
        v = self._media.video
        buf = bytearray(v.width * v.height * 3)
        mv = memoryview(buf)
        view = np.frombuffer(buf, np.uint8).reshape(v.height, v.width, 3)
        view.flags.writeable = False
        out = self._proc.stdout
        while True:
            got = 0
            while got < len(buf):
                n = out.readinto(mv[got:])
                if not n:
                    break
                got += n
            if got < len(buf):
                if got:
                    log.warning("動画の末尾に端数のデータがあったため破棄しました (%d バイト)", got)
                break
            yield view
        rc = self._proc.wait()
        if rc != 0:
            raise ToolError("E_DECODE_FAILED", "動画の読み込みに失敗しました", self._stderr.text())


# ---------------------------------------------------------------- Encoder

def audio_offset(media: MediaInfo, audio_mode: AudioMode) -> float | None:
    """映像の開始時刻が 1 フレームを超えてずれている場合のみ、音声入力に与える補正値(秒)を返す。

    入力ファイルの音声は元の開始時刻を保ったまま読み込まれる一方、パイプ経由の映像は必ず 0 から始まる。
    映像と音声の相対位置を保つため、音声側を「映像の開始時刻ぶん」戻す。
    """
    if audio_mode == "none" or media.audio is None:
        return None
    frame_time = media.video.fps_den / media.video.fps_num
    return -media.video_start_sec if abs(media.video_start_sec) > frame_time else None


def build_encode_args(ffmpeg: str, media: MediaInfo, profile: OutputProfile, quality: str,
                      audio_mode: AudioMode, tmp_output: Path) -> list[str]:
    v = media.video
    args = [ffmpeg, "-v", "error", "-nostdin", "-y",
            "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{v.width}x{v.height}",
            "-framerate", f"{v.fps_num}/{v.fps_den}", "-i", "pipe:0"]
    off = audio_offset(media, audio_mode)
    if off is not None:
        args += ["-itsoffset", f"{off:.6f}"]
    # 入力 1 は常に追加する(音声・メタデータの取得元)。-shortest は付けない
    # (ファイル入力が先に終端に達して映像が途中で打ち切られるため)。
    args += ["-i", str(media.path)]
    args += profile.build_encode_args(media, quality, audio_mode)
    return args + [str(tmp_output)]


class Encoder:
    def __init__(self, ffmpeg: str, media: MediaInfo, profile: OutputProfile, quality: str,
                 audio_mode: AudioMode, tmp_output: Path):
        self._cmd = build_encode_args(ffmpeg, media, profile, quality, audio_mode, tmp_output)
        self._proc: subprocess.Popen | None = None
        self._stderr: _StderrTail | None = None
        self.frames_written = 0

    def __enter__(self) -> "Encoder":
        log.debug("encode: %s", " ".join(self._cmd))
        try:
            self._proc = subprocess.Popen(self._cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.PIPE, **_popen_kwargs())
        except OSError as e:
            raise ToolError("E_ENCODE_FAILED", "動画の書き出しを開始できません", str(e)) from e
        self._stderr = _StderrTail(self._proc.stderr)
        return self

    def __exit__(self, *exc) -> None:
        self.abort()

    def write(self, frame: np.ndarray) -> None:
        try:
            self._proc.stdin.write(memoryview(frame).cast("B"))
        except (BrokenPipeError, OSError, ValueError) as e:
            self._proc.poll()
            detail = self._stderr.text() or str(e)
            raise ToolError("E_ENCODE_FAILED", "動画の書き出しに失敗しました", detail) from e
        self.frames_written += 1

    def finish(self) -> None:
        if self.frames_written == 0:
            raise ToolError("E_NO_FRAMES", "処理できるフレームがありませんでした")
        try:
            self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        rc = self._proc.wait()
        if rc != 0:
            raise ToolError("E_ENCODE_FAILED", "動画の書き出しに失敗しました", self._stderr.text())

    def abort(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin and not self._proc.stdin.closed:
                self._proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        _stop(self._proc)
        if self._proc.stderr:
            self._proc.stderr.close()
