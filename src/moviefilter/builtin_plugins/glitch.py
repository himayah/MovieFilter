"""デジタルグリッチ(RGBずれ・ブロックの位置ずれ・横ずれ)を時々入れるプラグイン。"""
import numpy as np

from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin


class GlitchPlugin(VideoPlugin):
    name = "glitch"
    description = "デジタルグリッチ(RGBずれ・ブロック崩れ・横ずれ)を時々入れます"
    version = "1.0.0"
    requires_api = (1, 0)
    params = (
        ParamSpec("rate", ParamType.FLOAT, 0.1,
                  "グリッチが発生するフレームの割合 (0.0=発生しない, 1.0=毎フレーム)", min=0.0, max=1.0),
        ParamSpec("strength", ParamType.FLOAT, 0.5,
                  "グリッチの強さ (0.0〜1.0)", min=0.0, max=1.0),
        ParamSpec("rgb_shift", ParamType.BOOL, True, "グリッチ時に R/B チャンネルを左右にずらす"),
        ParamSpec("blocks", ParamType.BOOL, True, "グリッチ時に矩形ブロックを別の位置の内容で置き換える"),
        ParamSpec("slices", ParamType.BOOL, True, "グリッチ時に横帯を左右にずらす"),
    )

    def setup(self, info):
        v = self.params_values
        self._rate = float(v["rate"])
        self._s = float(v["strength"])
        self._rgb = bool(v["rgb_shift"])
        self._blocks = bool(v["blocks"])
        self._slices = bool(v["slices"])
        self._out = np.empty((info.height, info.width, 3), np.uint8)

    def process(self, frame, ctx):
        out = self._out
        np.copyto(out, frame)
        if self._rate <= 0 or self._s <= 0:
            return out
        rng = np.random.default_rng(ctx.seed)
        if rng.random() >= self._rate:
            return out
        h, w = out.shape[:2]
        s = self._s
        if self._slices:
            for _ in range(int(rng.integers(1, 2 + int(4 * s)))):
                bh = int(max(1, h * rng.uniform(0.01, 0.1)))
                y0 = int(rng.integers(0, max(1, h - bh + 1)))
                dx = int(rng.integers(-1, 2) * w * rng.uniform(0.02, 0.25) * s)
                if dx:
                    out[y0:y0 + bh] = np.roll(out[y0:y0 + bh], dx, axis=1)
        if self._blocks:
            for _ in range(int(rng.integers(1, 2 + int(6 * s)))):
                bw = int(max(1, w * rng.uniform(0.03, 0.3) * s))
                bh = int(max(1, h * rng.uniform(0.02, 0.2) * s))
                bw, bh = min(bw, w), min(bh, h)
                x0, y0 = int(rng.integers(0, w - bw + 1)), int(rng.integers(0, h - bh + 1))
                sx, sy = int(rng.integers(0, w - bw + 1)), int(rng.integers(0, h - bh + 1))
                out[y0:y0 + bh, x0:x0 + bw] = frame[sy:sy + bh, sx:sx + bw]
        if self._rgb:
            d = int(max(1, w * 0.03 * s * rng.uniform(0.3, 1.0)))
            out[..., 0] = np.roll(out[..., 0], d, axis=1)
            out[..., 2] = np.roll(out[..., 2], -d, axis=1)
        return out
