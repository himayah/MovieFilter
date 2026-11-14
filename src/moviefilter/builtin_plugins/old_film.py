"""古い映画フィルム風(色あせ・ちらつき・ゆれ・縦傷・ゴミ・粒子・周辺減光)にするプラグイン。"""
import numpy as np

from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)
_TINT = np.array([1.0, 0.86, 0.66], dtype=np.float32)


class OldFilmPlugin(VideoPlugin):
    name = "old-film"
    description = "古い映画フィルム風(色あせ・ちらつき・縦傷・ゴミ・粒子)にします"
    version = "1.0.0"
    requires_api = (1, 0)
    params = (
        ParamSpec("tint", ParamType.FLOAT, 0.7,
                  "モノクロ+セピア調への変換の強さ (0.0=元の色, 1.0=完全に単色)", min=0.0, max=1.0),
        ParamSpec("flicker", ParamType.FLOAT, 0.3, "明るさのちらつき (0.0=なし)", min=0.0, max=1.0),
        ParamSpec("jitter", ParamType.FLOAT, 0.3, "画面のゆれ (0.0=なし)", min=0.0, max=1.0),
        ParamSpec("scratches", ParamType.FLOAT, 0.3, "縦傷の量 (0.0=なし)", min=0.0, max=1.0),
        ParamSpec("dust", ParamType.FLOAT, 0.3, "ゴミ・汚れの量 (0.0=なし)", min=0.0, max=1.0),
        ParamSpec("grain", ParamType.FLOAT, 0.3, "フィルム粒子の強さ (0.0=なし)", min=0.0, max=1.0),
    )

    def setup(self, info):
        v = self.params_values
        self._p = {k: float(v[k]) for k in ("tint", "flicker", "jitter", "scratches", "dust", "grain")}
        h, w = info.height, info.width
        ys = (np.arange(h, dtype=np.float32) + 0.5) / h * 2 - 1
        xs = (np.arange(w, dtype=np.float32) + 0.5) / w * 2 - 1
        r2 = ys[:, None] ** 2 + xs[None, :] ** 2
        self._vig = (1.0 - 0.3 * r2 / 2.0)[..., None].astype(np.float32)
        self._out = np.empty((h, w, 3), np.uint8)

    def process(self, frame, ctx):
        p = self._p
        rng = np.random.default_rng(ctx.seed)
        h, w = frame.shape[:2]
        f = frame.astype(np.float32)

        if p["tint"] > 0:
            tinted = (f @ _LUMA)[..., None] * _TINT
            f = (1.0 - p["tint"]) * f + p["tint"] * tinted

        if p["jitter"] > 0:
            dx = int(round(rng.uniform(-1, 1) * p["jitter"] * w * 0.012))
            dy = int(round(rng.uniform(-1, 1) * p["jitter"] * h * 0.012))
            if dx or dy:
                m = max(abs(dx), abs(dy))
                padded = np.pad(f, ((m, m), (m, m), (0, 0)), mode="edge")
                f = padded[m - dy:m - dy + h, m - dx:m - dx + w]

        f = f * self._vig
        if p["flicker"] > 0:
            f *= 1.0 + rng.uniform(-0.2, 0.2) * p["flicker"]
        if p["grain"] > 0:
            f += rng.standard_normal((h, w), dtype=np.float32)[..., None] * (p["grain"] * 14.0)

        for _ in range(int(rng.poisson(p["scratches"] * 1.5))):
            x = int(rng.integers(0, w))
            sw = int(rng.integers(1, 3))
            y0 = 0 if rng.random() < 0.6 else int(rng.integers(0, h))
            y1 = h if y0 == 0 and rng.random() < 0.6 else int(rng.integers(y0, h + 1))
            a = rng.uniform(0.3, 0.8)
            target = 255.0 if rng.random() < 0.7 else 0.0
            f[y0:y1, x:x + sw] = (1 - a) * f[y0:y1, x:x + sw] + a * target

        for _ in range(int(rng.poisson(p["dust"] * 20 * h * w / (640 * 480)))):
            r = max(1, int(h * rng.uniform(0.002, 0.008)))
            y, x = int(rng.integers(0, h)), int(rng.integers(0, w))
            a = rng.uniform(0.4, 0.9)
            target = 0.0 if rng.random() < 0.6 else 255.0
            f[y:y + r, x:x + r] = (1 - a) * f[y:y + r, x:x + r] + a * target

        np.clip(f, 0, 255, out=f)
        f += 0.5
        np.copyto(self._out, f, casting="unsafe")
        return self._out
