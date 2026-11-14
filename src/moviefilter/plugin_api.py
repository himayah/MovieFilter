"""プラグイン作者が使う公開API(詳細設計書 §3)。

このモジュールは本体の他モジュールに依存しない。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar, Sequence

import numpy as np

__all__ = [
    "API_VERSION",
    "MediaKind",
    "TemporalWindow",
    "ParamType",
    "ParamSpec",
    "VideoInfo",
    "FrameContext",
    "Plugin",
    "VideoPlugin",
    "AudioPlugin",
]

API_VERSION: tuple[int, int] = (1, 0)


class MediaKind(Enum):
    VIDEO = "video"
    AUDIO = "audio"  # 予約(第1段階は適用を拒否する)


@dataclass(frozen=True)
class TemporalWindow:
    past: int = 0
    future: int = 0


class ParamType(Enum):
    INT = "int"
    FLOAT = "float"
    BOOL = "bool"
    STR = "str"
    CHOICE = "choice"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: ParamType
    default: object
    description: str = ""
    min: float | None = None
    max: float | None = None
    choices: tuple | None = None


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    fps_num: int
    fps_den: int
    total_frames: int | None = None
    duration_sec: float | None = None

    @property
    def fps(self) -> float:
        return self.fps_num / self.fps_den


@dataclass(frozen=True)
class FrameContext:
    index: int
    pts_sec: float
    info: VideoInfo
    seed: int


class Plugin(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    version: ClassVar[str] = "0.0.0"
    requires_api: ClassVar[tuple[int, int]] = (1, 0)
    kind: ClassVar[MediaKind]
    params: ClassVar[tuple[ParamSpec, ...]] = ()

    def __init__(self, **params):
        self.params_values = params

    def teardown(self) -> None:
        pass


class VideoPlugin(Plugin):
    kind = MediaKind.VIDEO
    temporal_window: ClassVar[TemporalWindow] = TemporalWindow(0, 0)

    def setup(self, info: VideoInfo) -> None:
        pass

    @abstractmethod
    def process(self, frame: np.ndarray, ctx: FrameContext) -> np.ndarray: ...

    # 予約(API 1.2 で有効化予定)。既定実装は process に委譲する。
    def process_window(self, frames: Sequence[np.ndarray], ctx: FrameContext) -> np.ndarray:
        return self.process(frames[self.temporal_window.past], ctx)


class AudioPlugin(Plugin):
    """予約(API 1.1 で定義予定)。第1段階では一覧に表示されるが適用はできない。"""

    kind = MediaKind.AUDIO
