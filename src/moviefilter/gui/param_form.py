"""ParamSpec から入力部品を自動生成するフォーム(GUI 設計書 §5)。

値は文字列のまま ViewModel へ渡す。型変換・範囲検証は PluginManager の既存ロジックに任せる。
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from ..plugin_api import ParamSpec
from ..plugin_manager import PluginInfo
from . import strings
from .param_kind import CHECK, CHOICE, NUMBER, SLIDER, SPINBOX, range_text, widget_kind
from .viewmodel import PluginEntry

_TRUE = {"true", "1", "yes"}


def _fmt(v: float) -> str:
    return f"{round(v, 3):g}"


class ParamForm(ttk.Frame):
    def __init__(self, parent, info: PluginInfo, entry: PluginEntry,
                 on_change: Callable[[str, str], None], on_reset: Callable[[str], None]):
        super().__init__(parent)
        self.info = info
        self._entry = entry
        self._on_change = on_change
        self._on_reset = on_reset
        self._setters: dict[str, Callable[[str], None]] = {}
        self._error_labels: dict[str, ttk.Label] = {}
        self._sync = False
        # trace_add は Tcl 側にコマンドを登録し、コールバックと変数を掴み続ける。
        # destroy() で解除しないと、作り直すたびに Tcl 変数が残り続ける(GC でも回収されない)。
        self._traces: list[tuple[tk.Variable, str]] = []
        self.columnconfigure(1, weight=1)
        if not info.params:
            ttk.Label(self, text=strings.HINT_NO_PARAMS, foreground="#667085").grid(row=0, column=0, sticky="w")
            return
        row = 0
        for spec in info.params:
            self._build_row(row, spec)
            row += 3

    # ------------------------------------------------------------ 構築
    def _initial_text(self, spec: ParamSpec) -> str:
        text = self._entry.values.get(spec.name)
        if text is not None:
            return text
        d = spec.default
        if isinstance(d, bool):
            return "true" if d else "false"
        return str(d)

    def _build_row(self, row: int, spec: ParamSpec) -> None:
        kind = widget_kind(spec)
        ttk.Label(self, text=spec.name).grid(row=row, column=0, sticky="w", padx=(0, 8))
        holder = ttk.Frame(self)
        holder.grid(row=row, column=1, sticky="ew")
        holder.columnconfigure(0, weight=1)
        reset = ttk.Button(self, text=strings.BTN_RESET_ONE, width=3,
                           command=lambda n=spec.name: self._reset(n))
        reset.grid(row=row, column=2, padx=(6, 0))

        if kind == CHECK:
            var = tk.BooleanVar(value=self._initial_text(spec).lower() in _TRUE)

            def on_check(*_a, n=spec.name, v=var):
                if not self._sync:
                    self._on_change(n, "true" if v.get() else "false")
            self._traces.append((var, var.trace_add("write", on_check)))
            ttk.Checkbutton(holder, variable=var).grid(row=0, column=0, sticky="w")
            self._setters[spec.name] = lambda t, v=var: self._set(lambda: v.set(t.lower() in _TRUE))
        else:
            var = tk.StringVar(value=self._initial_text(spec))
            scale = None

            def on_text(*_a, n=spec.name, v=var):
                if self._sync:
                    return
                self._on_change(n, v.get())
                if scale is not None:
                    try:
                        self._set(lambda: scale.set(float(v.get())))
                    except ValueError:
                        pass
            self._traces.append((var, var.trace_add("write", on_text)))

            if kind == SLIDER:
                def on_scale(val, v=var, n=spec.name):
                    if self._sync:
                        return
                    text = _fmt(float(val))
                    self._set(lambda: v.set(text))
                    self._on_change(n, text)
                scale = ttk.Scale(holder, from_=spec.min, to=spec.max, command=on_scale)
                try:
                    scale.set(float(var.get()))
                except ValueError:
                    pass
                scale.grid(row=0, column=0, sticky="ew", padx=(0, 8))
                ttk.Entry(holder, textvariable=var, width=8).grid(row=0, column=1)
            elif kind == SPINBOX:
                ttk.Spinbox(holder, from_=spec.min, to=spec.max, textvariable=var, width=10) \
                    .grid(row=0, column=0, sticky="w")
            elif kind == CHOICE:
                ttk.Combobox(holder, textvariable=var, values=list(spec.choices), state="readonly", width=20) \
                    .grid(row=0, column=0, sticky="w")
            else:  # NUMBER / TEXT
                ttk.Entry(holder, textvariable=var, width=12 if kind == NUMBER else 30) \
                    .grid(row=0, column=0, sticky="w")

            def setter(t, v=var, sc=scale):
                def apply():
                    v.set(t)
                    if sc is not None:
                        try:
                            sc.set(float(t))
                        except ValueError:
                            pass
                self._set(apply)
            self._setters[spec.name] = setter

        desc = spec.description
        rng = range_text(spec)
        if rng:
            desc = f"{desc} ({rng})" if desc else rng
        err = ttk.Label(self, text="", foreground="#c62828", wraplength=420, justify="left")
        err.grid(row=row + 1, column=1, columnspan=2, sticky="w")
        self._error_labels[spec.name] = err
        if desc:
            ttk.Label(self, text=desc, foreground="#667085", wraplength=420, justify="left") \
                .grid(row=row + 2, column=1, columnspan=2, sticky="w", pady=(0, 6))

    def _set(self, fn: Callable[[], None]) -> None:
        was, self._sync = self._sync, True
        try:
            fn()
        finally:
            self._sync = was

    def _reset(self, name: str) -> None:
        spec = next(s for s in self.info.params if s.name == name)
        self._on_reset(name)
        d = spec.default
        text = ("true" if d else "false") if isinstance(d, bool) else str(d)
        self._setters[name](text)

    def destroy(self) -> None:
        for var, cb in self._traces:
            try:
                var.trace_remove("write", cb)
            except tk.TclError:
                pass
        self._traces.clear()
        self._setters.clear()
        super().destroy()

    # ------------------------------------------------------------ 外部から
    def show_errors(self, errors: dict[str, str]) -> None:
        for name, label in self._error_labels.items():
            label.configure(text=errors.get(name, ""))

    def reset_all(self) -> None:
        for spec in self.info.params:
            d = spec.default
            self._setters[spec.name]("true" if d is True else "false" if d is False else str(d))
