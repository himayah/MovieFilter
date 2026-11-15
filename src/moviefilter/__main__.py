import sys

# 起動用スクリプト(./moviefilter)や python -m moviefilter から実行されたとき、
# 前提が満たされていなければ、分かりやすいメッセージで終了する(import エラーの羅列を避ける)。


def _die(message: str):
    """前提を満たさないときの終了。コンソールのない起動(pythonw / moviefilter-gui.cmd)では、
    標準エラーが存在せずメッセージが誰にも見えないため、ダイアログで知らせる。"""
    if sys.stderr is None:
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("MovieFilter", message)
        except Exception:
            pass
        sys.exit(1)
    sys.exit(message)


if sys.version_info < (3, 11):
    _die("エラー: Python 3.11 以上が必要です (現在 %d.%d.%d)。" % sys.version_info[:3])
try:
    import numpy  # noqa: F401
except ImportError:
    _die("エラー: NumPy がインストールされていません。`pip install numpy` を実行してください。")

try:
    from .cli import main
except KeyboardInterrupt:  # 起動中の Ctrl+C はトレースバックを出さずに終了する
    sys.exit(130)

raise SystemExit(main())
