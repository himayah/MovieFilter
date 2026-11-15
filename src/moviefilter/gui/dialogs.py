"""ダイアログ群(GUI 設計書 §4.3)。"""
from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk
from typing import Callable

from ..models import ProcessResult
from ..plugin_manager import PluginLoadIssue
from . import strings
from .platform_utils import open_folder_of, open_path
from .viewmodel import hms
from .worker import ErrorInfo


class _Dialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, title: str):
        super().__init__(parent)
        self.title(title)
        self.transient(parent.winfo_toplevel())
        self.resizable(True, True)
        self.body = ttk.Frame(self, padding=12)
        self.body.pack(fill="both", expand=True)
        self.buttons = ttk.Frame(self, padding=(12, 0, 12, 12))
        self.buttons.pack(fill="x")
        self.bind("<Escape>", lambda e: self.destroy())

    def add_button(self, text: str, command: Callable[[], None]) -> ttk.Button:
        b = ttk.Button(self.buttons, text=text, command=command)
        b.pack(side="right", padx=(6, 0))
        return b

    def show(self) -> None:
        self.update_idletasks()
        self.grab_set()
        self.focus_set()


def _copy(widget: tk.Misc, text: str) -> None:
    widget.clipboard_clear()
    widget.clipboard_append(text)


def _readonly_text(parent, text: str, height: int) -> tk.Text:
    frame = ttk.Frame(parent)
    t = tk.Text(frame, height=height, width=72, wrap="word")
    sb = ttk.Scrollbar(frame, command=t.yview)
    t.configure(yscrollcommand=sb.set)
    t.insert("1.0", text)
    t.configure(state="disabled")
    t.pack(side="left", fill="both", expand=True)
    sb.pack(side="right", fill="y")
    frame.pack(fill="both", expand=True, pady=(8, 0))
    return t


def error_text(err: ErrorInfo) -> str:
    lines = [f"[{err.code}] {err.message}"]
    if err.plugin is not None:
        lines.append(f"プラグイン: {err.plugin} / フレーム番号: {err.frame_index}")
    if err.detail:
        lines.append(err.detail)
    return "\n".join(lines)


def show_error(parent: tk.Misc, err: ErrorInfo, log_text: str = "") -> None:
    d = _Dialog(parent, strings.DLG_ERROR_TITLE)
    ttk.Label(d.body, text=err.message, wraplength=520, justify="left",
              font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
    if err.detail and err.code != "E_INTERNAL":
        ttk.Label(d.body, text=err.detail, wraplength=520, justify="left").pack(anchor="w", pady=(6, 0))
    shown = {"on": False}

    def toggle():
        if not shown["on"]:
            shown["on"] = True
            detail_btn.configure(state="disabled")
            _readonly_text(d.body, error_text(err), 8)
    detail_btn = d.add_button(strings.DLG_DETAIL_SHOW, toggle)
    d.add_button(strings.DLG_COPY_LOG, lambda: _copy(d, error_text(err) + ("\n\n" + log_text if log_text else "")))
    d.add_button(strings.BTN_CLOSE, d.destroy)
    d.show()


def show_done(parent: tk.Misc, result: ProcessResult) -> None:
    d = _Dialog(parent, strings.DLG_DONE_TITLE)
    ttk.Label(d.body, justify="left", wraplength=520,
              text=strings.DLG_DONE_BODY.format(path=result.output_path, frames=result.frames_processed,
                                                elapsed=hms(result.elapsed_sec))).pack(anchor="w")

    def guarded(fn, path: Path):
        try:
            fn(path)
        except OSError as e:
            ttk.Label(d.body, text=f"開けませんでした: {e}", foreground="#c62828").pack(anchor="w")
    d.add_button(strings.BTN_CLOSE, d.destroy)
    d.add_button(strings.BTN_OPEN_FILE, lambda: guarded(open_path, result.output_path))
    d.add_button(strings.BTN_OPEN_FOLDER, lambda: guarded(open_folder_of, result.output_path))
    d.show()


def show_cli(parent: tk.Misc, command: str | None) -> None:
    d = _Dialog(parent, strings.DLG_CLI_TITLE)
    if command is None:
        ttk.Label(d.body, text=strings.DLG_CLI_UNREPRESENTABLE, wraplength=520, justify="left").pack(anchor="w")
    else:
        ttk.Label(d.body, text=strings.DLG_CLI_NOTE).pack(anchor="w")
        _readonly_text(d.body, command, 5)
        d.add_button(strings.BTN_COPY, lambda: _copy(d, command))
    d.add_button(strings.BTN_CLOSE, d.destroy)
    d.show()


def show_issues(parent: tk.Misc, issues: list[PluginLoadIssue]) -> None:
    d = _Dialog(parent, strings.DLG_ISSUES_TITLE)
    _readonly_text(d.body, "\n\n".join(f"{i.path}\n  {i.reason}" for i in issues), 10)
    d.add_button(strings.BTN_CLOSE, d.destroy)
    d.show()
