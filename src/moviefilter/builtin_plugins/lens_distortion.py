"""レンズ歪み(樽型・糸巻き型)と色収差を加えるプラグイン。"""
import numpy as np

from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin


def _bilinear_table(sx, sy, w, h):
    """元画像の座標 (sx, sy) [画素単位] から、左上の添字と小数部の表を作る。"""
    sx = np.clip(sx, 0, w - 1)
    sy = np.clip(sy, 0, h - 1)
    x0 = np.minimum(np.floor(sx), w - 2).astype(np.int32)
    y0 = np.minimum(np.floor(sy), h - 2).astype(np.int32)
    fx = (sx - x0).astype(np.float32)
    fy = (sy - y0).astype(np.float32)
    return (y0 * w + x0).ravel(), fx.ravel(), fy.ravel()


class LensDistortionPlugin(VideoPlugin):
    name = "lens-distortion"
    description = "レンズ歪み(樽型/糸巻き型)と色収差を加えます"
    version = "1.0.0"
    requires_api = (1, 0)
    params = (
        ParamSpec("curvature", ParamType.FLOAT, 0.2,
                  "歪みの量 (正=樽型に膨らむ, 負=糸巻き型, 0=なし)", min=-0.4, max=1.0),
        ParamSpec("aberration", ParamType.FLOAT, 0.3,
                  "色収差の強さ (0.0=なし, 1.0=最大)", min=0.0, max=1.0),
    )

    def setup(self, info):
        k = float(self.params_values["curvature"])
        ab = float(self.params_values["aberration"])
        h, w = info.height, info.width
        self._h, self._w = h, w
        self._out = np.empty((h, w, 3), np.uint8)
        self._identity = (k == 0 and ab == 0) or w < 2 or h < 2
        if self._identity:
            return
        xs = (np.arange(w, dtype=np.float32) + 0.5) / w * 2 - 1
        ys = (np.arange(h, dtype=np.float32) + 0.5) / h * 2 - 1
        x, y = np.meshgrid(xs, ys)
        r2 = x * x + y * y
        fit = 1.0 / (1.0 + 2.0 * k) if k > 0 else 1.0   # 樽型は四隅が元画像の外に出ないよう縮める
        base = fit * (1.0 + k * r2)
        tables = []
        for scale in (1.0 + 0.012 * ab, 1.0, 1.0 - 0.012 * ab):   # R, G, B
            sx = ((x * base * scale) + 1) / 2 * w - 0.5
            sy = ((y * base * scale) + 1) / 2 * h - 0.5
            tables.append(_bilinear_table(sx, sy, w, h))
        if ab == 0:
            tables = [tables[1]] * 3
        self._tables = tables
        self._acc = np.empty(h * w, np.float32)

    def process(self, frame, ctx):
        if self._identity:
            np.copyto(self._out, frame)
            return self._out
        h, w = self._h, self._w
        flat = frame.reshape(-1, 3)
        for c, (i0, fx, fy) in enumerate(self._tables):
            p = np.ascontiguousarray(flat[:, c]).astype(np.float32)
            top = p[i0] * (1 - fx) + p[i0 + 1] * fx
            bot = p[i0 + w] * (1 - fx) + p[i0 + w + 1] * fx
            acc = top * (1 - fy) + bot * fy
            acc += 0.5
            self._out[..., c] = acc.reshape(h, w).astype(np.uint8)
        return self._out
