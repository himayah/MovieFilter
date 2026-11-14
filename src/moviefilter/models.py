"""処理全体で受け渡すデータクラス(詳細設計書 §4)。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .plugin_api import VideoInfo


@dataclass(frozen=True)
class AudioInfo:
    codec: str
    sample_rate: int
    channels: int
    start_sec: float


@dataclass(frozen=True)
class MediaInfo:
    path: Path
    container: str
    video: VideoInfo
    source_video_codec: str
    source_fps_mode: Literal["cfr", "vfr"]
    video_start_sec: float
    audio: AudioInfo | None
    video_stream_index: int = 0
    audio_stream_index: int | None = None


@dataclass(frozen=True)
class PluginSpec:
    name: str
    raw_params: dict[str, str]


@dataclass(frozen=True)
class ProcessRequest:
    input_path: Path
    plugins: tuple[PluginSpec, ...]
    output_path: Path | None = None
    profile: str = "mp4-h264"
    quality: str = "medium"
    keep_audio: bool = True
    overwrite: bool = False
    seed: int = 0


@dataclass(frozen=True)
class ProcessResult:
    output_path: Path
    frames_processed: int
    elapsed_sec: float
