import sys

# 起動用スクリプト(./moviefilter)や python -m moviefilter から実行されたとき、
# 前提が満たされていなければ、分かりやすいメッセージで終了する(import エラーの羅列を避ける)。
if sys.version_info < (3, 11):
    sys.exit("エラー: Python 3.11 以上が必要です (現在 %d.%d.%d)。" % sys.version_info[:3])
try:
    import numpy  # noqa: F401
except ImportError:
    sys.exit("エラー: NumPy がインストールされていません。`pip install numpy` を実行してください。")

try:
    from .cli import main
except KeyboardInterrupt:  # 起動中の Ctrl+C はトレースバックを出さずに終了する
    sys.exit(130)

raise SystemExit(main())
