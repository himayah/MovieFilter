"""アナログ放送時代のテレビのような画質にし、時々ノイズを入れるプラグイン。"""
import numpy as np

from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin

_LUMA = np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _hblur(a, radius):
    """水平方向のボックスぼかし(端は端の画素で延長)。a: (h, w, c) float32。"""
    if radius <= 0:
        return a
    w = a.shape[1]
    p = np.pad(a, ((0, 0), (radius, radius), (0, 0)), mode="edge")
    acc = np.zeros_like(a)
    for k in range(2 * radius + 1):
        acc += p[:, k:k + w]
    acc /= 2 * radius + 1
    return acc


class AnalogTvPlugin(VideoPlugin):
    name = "analog-tv"
    description = "アナログ放送時代のテレビ風の画質にし、時々ノイズを入れます"
    version = "1.0.0"
    requires_api = (1, 0)
    params = (
        ParamSpec("softness", ParamType.FLOAT, 0.5,
                  "解像感の低下と色にじみの強さ (0.0=なし, 1.0=最大)", min=0.0, max=1.0),
        ParamSpec("scanlines", ParamType.FLOAT, 0.25,
                  "走査線(横縞)の濃さ (0.0=なし, 1.0=最大)", min=0.0, max=1.0),
        ParamSpec("grain", ParamType.FLOAT, 0.3,
                  "常時かかる細かいざらつきの強さ (0.0=なし, 1.0=最大)", min=0.0, max=1.0),
        ParamSpec("noise_rate", ParamType.FLOAT, 0.05,
                  "ノイズが発生するフレームの割合 (0.0=発生しない, 1.0=毎フレーム)", min=0.0, max=1.0),
        ParamSpec("noise_strength", ParamType.FLOAT, 0.7,
                  "ノイズ発生時の強さ (0.0〜1.0)", min=0.0, max=1.0),
    )

    def setup(self, info):
        v = self.params_values
        h, w = info.height, info.width
        self._soft = float(v["softness"])
        self._grain = float(v["grain"])
        self._rate = float(v["noise_rate"])
        self._strength = float(v["noise_strength"])
        # 走査線: 偶数行はそのまま、奇数行を暗くする
        gain = np.ones(h, np.float32)
        gain[1::2] = 1.0 - 0.5 * float(v["scanlines"])
        self._gain = gain[:, None, None]
        self._radius = int(round(1 + 3 * self._soft)) if self._soft > 0 else 0
        self._out = np.empty((h, w, 3), np.uint8)

    def process(self, frame, ctx):
        rng = np.random.default_rng(ctx.seed)
        f = frame.astype(np.float32)
        h, w = f.shape[:2]

        # 輝度は少しぼかし、色は大きくにじませて右へずらす(色にじみ)
        if self._soft > 0:
            y = f @ _LUMA
            chroma = f - y[..., None]
            y_soft = _hblur(y[..., None], 1)[..., 0]
            y = (1.0 - self._soft) * y + self._soft * y_soft
            chroma = _hblur(chroma, self._radius)
            shift = min(w - 1, int(round(2 * self._soft)))
            if shift > 0:
                chroma = np.concatenate([np.repeat(chroma[:, :1], shift, axis=1),
                                         chroma[:, :-shift]], axis=1)
            chroma *= 1.0 - 0.25 * self._soft  # 彩度もやや落ちる
            f = chroma + y[..., None]

        f *= self._gain

        if self._grain > 0:
            f += rng.standard_normal((h, w), dtype=np.float32)[..., None] * (self._grain * 18.0)

        if self._rate > 0 and self._strength > 0 and rng.random() < self._rate:
            self._noise_burst(f, rng)

        np.clip(f, 0, 255, out=f)
        np.add(f, 0.5, out=f)
        np.copyto(self._out, f, casting="unsafe")
        return self._out

    def _noise_burst(self, f, rng):
        """1フレームぶんのノイズ: 明るさのちらつき・横ずれ・砂嵐の帯・まれに全面の砂嵐。"""
        h, w = f.shape[:2]
        s = self._strength
        f *= 1.0 + rng.uniform(-0.15, 0.15) * s
        for _ in range(int(rng.integers(1, 4))):
            bh = int(max(1, h * rng.uniform(0.02, 0.12)))
            y0 = int(rng.integers(0, max(1, h - bh + 1)))
            band = slice(y0, y0 + bh)
            dx = int(rng.integers(-w // 8, w // 8 + 1) * s)
            if dx:
                f[band] = np.roll(f[band], dx, axis=1)
            snow = rng.uniform(0, 255, (bh, w)).astype(np.float32)[..., None]
            a = rng.uniform(0.4, 1.0) * s
            f[band] = (1.0 - a) * f[band] + a * snow
        if rng.random() < 0.15:
            snow = rng.uniform(0, 255, (h, w)).astype(np.float32)[..., None]
            a = 0.6 * s
            f[:] = (1.0 - a) * f + a * snow
