"""MovieFilter プラグインのひな形

使い方:
  1. このファイルを「プラグイン名.py」にコピーする
     (ファイル名は、下の `name` の `-` を `_` に置き換えたものと一致させること。
      例: name = "film-grain" なら film_grain.py)
  2. 「★ 書き換える」の印がある箇所(クラス名・name・description・params)を編集する
  3. 「★ エフェクト処理」の箇所にエフェクトを書く
  4. MOVIEFILTER_PLUGIN_PATH で指定したフォルダ(または設定ファイルの extra_dirs)に置く
  5. `moviefilter list` に表示されれば完成。`moviefilter apply 入力.mp4 -p プラグイン名` で実行できる

このままの状態でも動作する(何も加工せず、そのまま出力する)。
詳細は docs/plugin_author_guide.md を参照。
"""
import numpy as np

from moviefilter.plugin_api import ParamSpec, ParamType, VideoPlugin


class MyEffectPlugin(VideoPlugin):  # ★ 書き換える: クラス名は自由(1ファイルに1プラグインのみ)
    # ---------------------------------------------------------------
    # ★ 書き換える: プラグインの情報
    # ---------------------------------------------------------------
    name = "my_effect"  # CLI で指定する名前。[a-z][a-z0-9_-]*。ファイル名(my_effect.py)と一致させる
    description = "何も加工しないひな形プラグイン"  # `moviefilter list` に表示される説明
    version = "1.0.0"
    requires_api = (1, 0)  # 変更しない(プラグインAPIのバージョン)

    # ★ 書き換える: パラメータ定義(CLI で -p my_effect:strength=0.5 のように指定できる)
    # 不要なら params = () にする。型は INT / FLOAT / BOOL / STR / CHOICE。
    params = (
        ParamSpec(
            "strength",            # 名前: [a-z][a-z0-9_]*
            ParamType.FLOAT,       # 型
            1.0,                   # 既定値(型と範囲に合っていること)
            "効果の強さ (0.0〜1.0)",  # `moviefilter info` に表示される説明
            min=0.0,
            max=1.0,
        ),
    )

    # ---------------------------------------------------------------
    # 準備: 処理を始める前に1回だけ呼ばれる(通常は編集不要)
    # ---------------------------------------------------------------
    def setup(self, info):
        # info.width / info.height / info.fps / info.total_frames が使える
        # 出力用バッファを一度だけ確保して使い回す(フレームごとのメモリ確保を避けるため)
        self._out = np.empty((info.height, info.width, 3), dtype=np.uint8)

        # ★ 必要ならここで、処理で使う値・テーブル・状態の初期化を行う
        # self._strength = self.params_values["strength"]

    # ---------------------------------------------------------------
    # 1フレームごとの処理
    # ---------------------------------------------------------------
    def process(self, frame, ctx):
        """frame: (高さ, 幅, 3) の uint8 RGB 配列。読み取り専用。
        ctx.index: フレーム番号(0始まり) / ctx.pts_sec: 時刻(秒)
        ctx.seed:  乱数用のシード(np.random.default_rng(ctx.seed) で使う)
        戻り値: frame と同じ形・型(uint8)の配列
        """
        out = self._out
        np.copyto(out, frame)  # 入力は書き換えられないので、出力バッファへコピーしてから加工する

        # ===============================================================
        # ★ エフェクト処理: ここから
        #    out(高さ×幅×3 の uint8 配列)を加工する。入力 frame は読み取るだけ。
        #
        #    strength = self.params_values["strength"]
        #    例) 赤チャンネルを半分にする:
        #        out[:, :, 0] = out[:, :, 0] // 2
        #    例) 全体を暗くする (float を経由して 0〜255 に収める):
        #        np.multiply(frame, 0.5, out=out, casting="unsafe")
        # ★ エフェクト処理: ここまで
        # ===============================================================

        return out

    # ---------------------------------------------------------------
    # 後始末: 処理の終了時(失敗・中断時も)に1回呼ばれる。通常は不要
    # ---------------------------------------------------------------
    # def teardown(self):
    #     pass
