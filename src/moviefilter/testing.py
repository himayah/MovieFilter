"""プラグイン作者向けテスト支援(詳細設計書 §5.8)。動画・FFmpeg なしでプラグインをテストできる。"""
from __future__ import annotations

from typing import Iterable

import numpy as np

from .pipeline import Frame, VideoStageChain
from .plugin_api import FrameContext, Plugin, VideoInfo
from .plugin_manager import create_plugin
from .pipeline import derive_seed

__all__ = ["make_video_info", "make_frame_context", "make_test_frames", "run_plugin"]


def make_video_info(width: int = 64, height: int = 48, fps_num: int = 30, fps_den: int = 1,
                    total_frames: int | None = None) -> VideoInfo:
    duration = total_frames * fps_den / fps_num if total_frames else None
    return VideoInfo(width, height, fps_num, fps_den, total_frames, duration)


def make_frame_context(index: int = 0, info: VideoInfo | None = None, seed: int = 0) -> FrameContext:
    info = info or make_video_info()
    return FrameContext(index, index * info.fps_den / info.fps_num, info, seed)


def make_test_frames(count: int = 5, width: int = 64, height: int = 48,
                     pattern: str = "gradient") -> list[np.ndarray]:
    """テスト用フレーム(各 (height, width, 3) の uint8)。pattern: gradient / noise / black / white。"""
    frames = []
    if pattern == "gradient":
        x = np.linspace(0, 255, width, dtype=np.float32)[None, :]
        y = np.linspace(0, 255, height, dtype=np.float32)[:, None]
        for i in range(count):
            f = np.empty((height, width, 3), np.uint8)
            f[:, :, 0] = x
            f[:, :, 1] = y
            f[:, :, 2] = (i * 255 // max(1, count - 1)) if count > 1 else 128
            frames.append(f)
    elif pattern == "noise":
        rng = np.random.default_rng(12345)
        frames = [rng.integers(0, 256, (height, width, 3), dtype=np.uint8) for _ in range(count)]
    elif pattern in ("black", "white"):
        v = 0 if pattern == "black" else 255
        frames = [np.full((height, width, 3), v, np.uint8) for _ in range(count)]
    else:
        raise ValueError(f"不明な pattern です: {pattern} (gradient / noise / black / white)")
    return frames


def run_plugin(plugin_cls: type[Plugin], frames: Iterable[np.ndarray], params: dict | None = None,
               info: VideoInfo | None = None, job_seed: int = 0) -> list[np.ndarray]:
    """本番と同じ手順(パラメータ検証 → setup → process → teardown)でプラグインを実行する。

    戻り値は加工後の各フレームの**コピー**のリスト。入力は読み取り専用で渡される。
    """
    frames = list(frames)
    if not frames:
        return []
    h, w = frames[0].shape[:2]
    info = info or make_video_info(w, h, total_frames=len(frames))
    plugin = create_plugin(plugin_cls, dict(params or {}))
    chain = VideoStageChain([plugin], info, job_seed)
    outputs: list[np.ndarray] = []
    try:
        chain.setup()
        for i, f in enumerate(frames):
            src = np.ascontiguousarray(f).copy()
            src.flags.writeable = False
            out = chain.run(Frame(src, i, i * info.fps_den / info.fps_num))
            outputs.append(out.pixels.copy())
    finally:
        chain.teardown()
    return outputs
