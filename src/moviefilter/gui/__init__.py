"""GUI(GUI 設計書)。tkinter が無い環境でも CLI を使えるよう、起動時に初めて tkinter を import する。"""
from __future__ import annotations

import sys
from pathlib import Path

from ..config import Config, load_config
from ..errors import ConfigError, MovieFilterError


def main(config_path: Path | None = None, verbose: bool = False) -> int:
    try:
        import tkinter  # noqa: F401
    except ImportError as e:
        raise MovieFilterError(
            "E_GUI_UNAVAILABLE", "GUI には tkinter が必要ですが、利用できません",
            "Windows は Python の再インストール時に tcl/tk を含めてください。"
            "Debian/Ubuntu では `sudo apt install python3-tk` を実行してください") from e
    config_error = None
    try:
        config = load_config(config_path)
    except ConfigError as e:
        config, config_error = Config(), e.message
    from .app import run_gui
    return run_gui(config, config_error, verbose)


def gui_main() -> int:
    """`moviefilter-gui`(コンソールなしの起動)用。エラーはダイアログで通知する。"""
    try:
        return main()
    except MovieFilterError as e:
        text = f"{e.message}\n{e.detail or ''}".strip()
        try:
            import tkinter
            from tkinter import messagebox
            root = tkinter.Tk()
            root.withdraw()
            messagebox.showerror("MovieFilter", text)
        except Exception:
            print(f"エラー [{e.code}]: {text}", file=sys.stderr)
        return e.exit_code
