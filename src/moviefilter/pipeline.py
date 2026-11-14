"""パイプライン(詳細設計書 §5.4)。フレームをプラグインのチェーンに通す。"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .errors import PluginError, PluginRuntimeError
from .plugin_api import FrameContext, VideoInfo, VideoPlugin

log = logging.getLogger("moviefilter.pipeline")


@dataclass
class Frame:
    pixels: np.ndarray
    index: int
    pts_sec: float


def derive_seed(job_seed: int, plugin_index: int, frame_index: int) -> int:
    """フレーム・プラグイン位置ごとの決定的シード。処理順や並列度に依存しない。"""
    ss = np.random.SeedSequence([job_seed, plugin_index, frame_index])
    return int(ss.generate_state(1, dtype=np.uint64)[0])


class VideoStageChain:
    def __init__(self, plugins: Sequence[VideoPlugin], info: VideoInfo, job_seed: int = 0):
        self.plugins = list(plugins)
        self.info = info
        self.job_seed = job_seed
        self._warned_noncontiguous = False

    def setup(self) -> None:
        for p in self.plugins:
            try:
                p.setup(self.info)
            except PluginError:
                raise
            except Exception as e:
                raise PluginError("E_PLUGIN_INIT", f"プラグインの初期化に失敗しました: {p.name}",
                                  f"{type(e).__name__}: {e}") from e

    def run(self, frame: Frame) -> Frame:
        pixels = frame.pixels
        shape = pixels.shape
        for i, plugin in enumerate(self.plugins):
            ctx = FrameContext(frame.index, frame.pts_sec, self.info,
                               derive_seed(self.job_seed, i, frame.index))
            try:
                out = plugin.process(pixels, ctx)
            except Exception as e:
                raise PluginRuntimeError(plugin.name, frame.index, e) from e
            if not isinstance(out, np.ndarray):
                raise PluginError("E_PLUGIN_BAD_OUTPUT",
                                  f"プラグイン {plugin.name} の出力が不正です (フレーム {frame.index}): "
                                  f"ndarray ではなく {type(out).__name__} です")
            if out.shape != shape or out.dtype != np.uint8:
                raise PluginError("E_PLUGIN_BAD_OUTPUT",
                                  f"プラグイン {plugin.name} の出力が不正です (フレーム {frame.index}): "
                                  f"形状 {out.shape}・型 {out.dtype} が期待値 {shape}・uint8 と異なります")
            if not out.flags.c_contiguous:
                if not self._warned_noncontiguous:
                    log.debug("プラグイン %s の出力が C 連続ではないためコピーします", plugin.name)
                    self._warned_noncontiguous = True
                out = np.ascontiguousarray(out)
            pixels = out
        return Frame(pixels, frame.index, frame.pts_sec)

    def teardown(self) -> None:
        """全プラグインの teardown を呼ぶ。失敗は警告のみで、他の後始末や元のエラーを妨げない。"""
        for p in self.plugins:
            try:
                p.teardown()
            except Exception as e:
                log.warning("プラグイン %s の teardown が失敗しました: %s: %s", p.name, type(e).__name__, e)


class AudioStageChain:
    """第1段階はパススルー(元音声のコピー)のみ。音声プラグイン対応時に差し替える。"""

    mode = "passthrough"


class FrameRingBuffer:
    """前後フレーム参照(API 1.2)用の予約。第1段階は past=future=0 のパススルーのみ。"""

    def __init__(self, past: int = 0, future: int = 0):
        if past or future:
            raise NotImplementedError("前後フレームの参照は第1段階では未対応です")
        self.past = past
        self.future = future
