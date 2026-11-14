"""動画全体を一様にセピアカラーにするプラグイン(詳細設計書 §9)。"""
import numpy as np

from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin

SEPIA = np.array(
    [[0.393, 0.769, 0.189],
     [0.349, 0.686, 0.168],
     [0.272, 0.534, 0.131]],
    dtype=np.float32,
)


class SepiaPlugin(VideoPlugin):
    name = "sepia"
    description = "動画全体を一様にセピアカラーにします"
    version = "1.0.0"
    requires_api = (1, 0)
    params = (
        ParamSpec("intensity", ParamType.FLOAT, 1.0,
                  "効果の強さ (0.0=元画像, 1.0=完全なセピア)", min=0.0, max=1.0),
    )

    def setup(self, info):
        a = self.params_values["intensity"]
        m = (1.0 - a) * np.eye(3, dtype=np.float32) + a * SEPIA
        self._mt = np.ascontiguousarray(m.T.astype(np.float32))
        n = info.width * info.height
        self._f32 = np.empty((n, 3), np.float32)
        self._acc = np.empty((n, 3), np.float32)
        self._out = np.empty((info.height, info.width, 3), np.uint8)

    def process(self, frame, ctx):
        np.copyto(self._f32, frame.reshape(-1, 3), casting="unsafe")
        np.matmul(self._f32, self._mt, out=self._acc)
        np.clip(self._acc, 0, 255, out=self._acc)
        np.add(self._acc, 0.5, out=self._acc)  # 四捨五入(クリップ後に +0.5 して切り捨て)
        np.copyto(self._out, self._acc.reshape(self._out.shape), casting="unsafe")
        return self._out
