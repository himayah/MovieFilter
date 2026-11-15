"""OS 依存の処理(フォルダ/ファイルを開く等)をここに隔離する(GUI-N5)。"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def open_path(path: Path) -> None:
    """ファイルまたはフォルダを、OS の既定のアプリで開く。"""
    if sys.platform == "win32":
        os.startfile(str(path))  # noqa: S606
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_folder_of(path: Path) -> None:
    open_path(path.parent)
