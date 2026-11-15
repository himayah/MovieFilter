"""メインウィンドウ(GUI 設計書 §4)。ロジックは ViewModel に置き、ここは画面の組み立てと反映だけを行う。"""
from __future__ import annotations

import logging
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..config import QUALITIES, Config
from ..errors import MovieFilterError
from ..plugin_manager import PluginManager, default_search_dirs
from ..profiles import default_registry
from . import dialogs, strings
from .logbuf import MAX_KEPT, LogKeeper, QueueLogHandler
from .param_form import ParamForm
from .state_store import load_state, save_state
from .viewmodel import AppState, ViewModel, format_media, format_progress

POLL_MS = 100


class MainWindow:
    def __init__(self, root: tk.Tk, vm: ViewModel, config_error: str | None, log_handler: QueueLogHandler):
        self.root = root
        self.vm = vm
        self.log_handler = log_handler
        self._building = False
        self._form: ParamForm | None = None
        self._form_key: tuple | None = None
        self._chain_texts: list[str] = []
        self._visible_infos: list = []
        self._log = LogKeeper()
        self._state = load_state()
        self._last_dir = self._state.get("last_dir") or str(Path.home())

        root.title(strings.APP_TITLE)
        root.geometry(self._state.get("geometry") or "960x820")
        root.minsize(820, 640)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        self._build_banner(config_error)
        self._build_input()
        self._build_middle()
        self._build_output()
        self._build_run()
        self._build_log()

        vm.listeners.append(self._on_vm)
        self._refresh_plugin_list()
        self.refresh()
        root.after(POLL_MS, self._tick)

    # ============================================================ 構築
    def _build_banner(self, config_error: str | None) -> None:
        self.banner = ttk.Label(self.root, foreground="#7a4a00", background="#fff4e0", padding=6,
                                wraplength=900, justify="left")
        self.banner.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 0))
        self._config_error = config_error

    def _build_input(self) -> None:
        f = ttk.LabelFrame(self.root, text=strings.SEC_INPUT, padding=8)
        f.grid(row=1, column=0, sticky="ew", padx=10, pady=(8, 0))
        f.columnconfigure(0, weight=1)
        self.input_var = tk.StringVar()
        self.input_entry = ttk.Entry(f, textvariable=self.input_var)
        self.input_entry.grid(row=0, column=0, sticky="ew")
        self.input_entry.bind("<Return>", lambda e: self._load_input(self.input_var.get()))
        ttk.Button(f, text=strings.BTN_BROWSE, command=self._browse_input).grid(row=0, column=1, padx=(6, 0))
        self.media_label = ttk.Label(f, text=strings.HINT_NO_INPUT_INFO, foreground="#667085")
        self.media_label.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.vfr_label = ttk.Label(f, text=strings.HINT_VFR, foreground="#b45309")
        self.vfr_label.grid(row=2, column=0, columnspan=2, sticky="w")
        self.input_frame = f

    def _build_middle(self) -> None:
        mid = ttk.Frame(self.root)
        mid.grid(row=2, column=0, sticky="nsew", padx=10, pady=(8, 0))
        mid.columnconfigure(1, weight=1)
        mid.rowconfigure(0, weight=1)

        # ② プラグイン一覧
        left = ttk.LabelFrame(mid, text=strings.SEC_PLUGINS, padding=8)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        left.rowconfigure(1, weight=1)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh_plugin_list())
        ttk.Entry(left, textvariable=self.search_var, width=30).grid(row=0, column=0, sticky="ew")
        self.plugin_list = tk.Listbox(left, exportselection=False, width=30, height=8, activestyle="none")
        self.plugin_list.grid(row=1, column=0, sticky="nsew", pady=6)
        self.plugin_list.bind("<<ListboxSelect>>", lambda e: self._on_plugin_select())
        self.plugin_list.bind("<Double-Button-1>", lambda e: self._add_selected())
        self.plugin_desc = ttk.Label(left, text=strings.HINT_NO_PLUGIN_SELECTED, foreground="#667085",
                                     wraplength=230, justify="left")
        self.plugin_desc.grid(row=2, column=0, sticky="w")
        self.add_btn = ttk.Button(left, text=strings.BTN_ADD, command=self._add_selected)
        self.add_btn.grid(row=3, column=0, sticky="ew", pady=(6, 0))
        self.issue_btn = ttk.Button(left, text=strings.HINT_LOAD_ISSUE,
                                    command=lambda: dialogs.show_issues(self.root, self.vm.issues))
        self.issue_btn.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.left_frame = left

        # ③ 適用リスト + ④ パラメータ
        right = ttk.Frame(mid)
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        chain = ttk.LabelFrame(right, text=strings.SEC_CHAIN, padding=8)
        chain.grid(row=0, column=0, sticky="ew")
        chain.columnconfigure(0, weight=1)
        self.chain_list = tk.Listbox(chain, exportselection=False, height=4, activestyle="none")
        self.chain_list.grid(row=0, column=0, rowspan=3, sticky="ew")
        self.chain_list.bind("<<ListboxSelect>>", lambda e: self._on_chain_select())
        self.up_btn = ttk.Button(chain, text=strings.BTN_UP, width=3, command=lambda: self._move(-1))
        self.down_btn = ttk.Button(chain, text=strings.BTN_DOWN, width=3, command=lambda: self._move(1))
        self.del_btn = ttk.Button(chain, text=strings.BTN_REMOVE, width=3, command=self._remove)
        for i, b in enumerate((self.up_btn, self.down_btn, self.del_btn)):
            b.grid(row=i, column=1, padx=(6, 0), pady=1)
        self.chain_frame = chain

        self.param_frame = ttk.LabelFrame(right, text=strings.SEC_PARAMS, padding=8)
        self.param_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
        self.param_frame.columnconfigure(0, weight=1)
        self.param_hint = ttk.Label(self.param_frame, text=strings.HINT_NO_CHAIN_SELECTED, foreground="#667085")
        self.param_hint.grid(row=0, column=0, columnspan=2, sticky="w")
        # パラメータが多いプラグインでも収まるよう、縦スクロールできる領域に置く
        self.param_frame.rowconfigure(1, weight=1)
        canvas = tk.Canvas(self.param_frame, highlightthickness=0, height=160)
        sb = ttk.Scrollbar(self.param_frame, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=sb.set)
        canvas.grid(row=1, column=0, sticky="nsew")
        sb.grid(row=1, column=1, sticky="ns")
        self.param_holder = ttk.Frame(canvas)
        self.param_holder.columnconfigure(0, weight=1)
        win = canvas.create_window((0, 0), window=self.param_holder, anchor="nw")
        self.param_holder.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(win, width=e.width))

        def wheel(e):
            if e.num == 4 or e.delta > 0:
                canvas.yview_scroll(-2, "units")
            else:
                canvas.yview_scroll(2, "units")
        for ev in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            canvas.bind(ev, wheel)
            self.param_holder.bind(ev, wheel)
        self.reset_all_btn = ttk.Button(self.param_frame, text=strings.BTN_RESET_ALL, command=self._reset_all)
        self.reset_all_btn.grid(row=2, column=0, columnspan=2, sticky="e", pady=(6, 0))

    def _build_output(self) -> None:
        f = ttk.LabelFrame(self.root, text=strings.SEC_OUTPUT, padding=8)
        f.grid(row=3, column=0, sticky="ew", padx=10, pady=(8, 0))
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text=strings.LBL_OUTPUT).grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.output_var = tk.StringVar()
        self.output_var.trace_add("write", lambda *_: (not self._building) and self.vm.set_output_text(self.output_var.get()))
        ttk.Entry(f, textvariable=self.output_var).grid(row=0, column=1, sticky="ew")
        ttk.Button(f, text=strings.BTN_BROWSE, command=self._browse_output).grid(row=0, column=2, padx=(6, 0))

        opts = ttk.Frame(f)
        opts.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Label(opts, text=strings.LBL_PROFILE).grid(row=0, column=0, padx=(0, 4))
        self.profile_var = tk.StringVar()
        cb = ttk.Combobox(opts, textvariable=self.profile_var, values=self.vm.profiles.names(),
                          state="readonly", width=12)
        cb.grid(row=0, column=1, padx=(0, 12))
        cb.bind("<<ComboboxSelected>>", lambda e: self.vm.set_profile(self.profile_var.get()))
        ttk.Label(opts, text=strings.LBL_QUALITY).grid(row=0, column=2, padx=(0, 4))
        self.quality_var = tk.StringVar()
        for i, q in enumerate(QUALITIES):
            ttk.Radiobutton(opts, text=strings.QUALITY_LABELS.get(q, q), value=q, variable=self.quality_var,
                            command=lambda: self.vm.set_quality(self.quality_var.get())).grid(row=0, column=3 + i)
        self.audio_var = tk.BooleanVar()
        ttk.Checkbutton(opts, text=strings.LBL_KEEP_AUDIO, variable=self.audio_var,
                        command=lambda: self.vm.set_keep_audio(self.audio_var.get())).grid(row=0, column=7, padx=(12, 0))
        ttk.Label(opts, text=strings.LBL_SEED).grid(row=0, column=8, padx=(12, 4))
        self.seed_var = tk.StringVar()
        self.seed_var.trace_add("write", lambda *_: (not self._building) and self.vm.set_seed_text(self.seed_var.get()))
        ttk.Entry(opts, textvariable=self.seed_var, width=10).grid(row=0, column=9)

        row2 = ttk.Frame(f)
        row2.grid(row=2, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        row2.columnconfigure(0, weight=1)
        self.overwrite_var = tk.BooleanVar()
        ttk.Checkbutton(row2, text=strings.LBL_OVERWRITE, variable=self.overwrite_var,
                        command=lambda: self.vm.set_overwrite(self.overwrite_var.get())).grid(row=0, column=0, sticky="w")
        ttk.Button(row2, text=strings.BTN_SHOW_CLI,
                   command=lambda: dialogs.show_cli(self.root, self.vm.cli_command())).grid(row=0, column=1)
        self.output_frame = f

    def _build_run(self) -> None:
        f = ttk.LabelFrame(self.root, text=strings.SEC_RUN, padding=8)
        f.grid(row=4, column=0, sticky="ew", padx=10, pady=(8, 0))
        f.columnconfigure(2, weight=1)
        self.run_btn = ttk.Button(f, text=strings.BTN_RUN, command=self._run)
        self.run_btn.grid(row=0, column=0)
        self.cancel_btn = ttk.Button(f, text=strings.BTN_CANCEL, command=self.vm.cancel)
        self.cancel_btn.grid(row=0, column=1, padx=6)
        self.progress = ttk.Progressbar(f, maximum=100, mode="determinate")
        self.progress.grid(row=0, column=2, sticky="ew", padx=6)
        self.progress_label = ttk.Label(f, text="", width=34)
        self.progress_label.grid(row=0, column=3)
        self.status_label = ttk.Label(f, text="", foreground="#667085")
        self.status_label.grid(row=1, column=0, columnspan=4, sticky="w", pady=(6, 0))
        self._pb_indeterminate = False

    def _build_log(self) -> None:
        f = ttk.LabelFrame(self.root, text=strings.SEC_LOG, padding=8)
        f.grid(row=5, column=0, sticky="ew", padx=10, pady=8)
        f.columnconfigure(0, weight=1)
        self.log_toggle = ttk.Button(f, text=strings.BTN_SHOW_LOG, command=self._toggle_log)
        self.log_toggle.grid(row=0, column=0, sticky="w")
        self.log_box = ttk.Frame(f)
        self.log_text = tk.Text(self.log_box, height=7, wrap="word", state="disabled")
        sb = ttk.Scrollbar(self.log_box, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=sb.set)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.log_frame = f
        self._log_open = False

    # ============================================================ 入力
    def _browse_input(self) -> None:
        path = filedialog.askopenfilename(
            parent=self.root, title=strings.SEC_INPUT, initialdir=self._last_dir,
            filetypes=[("動画ファイル", "*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.flv"), ("すべてのファイル", "*.*")])
        if path:
            self._load_input(path)

    def _load_input(self, path: str) -> None:
        path = path.strip().strip('"')
        if not path or not self.vm.editable:
            return
        self._last_dir = str(Path(path).parent)
        self.input_var.set(path)
        self.vm.set_input(path)

    def _browse_output(self) -> None:
        ext = self.vm.profiles.get(self.vm.profile).extension
        cur = Path(self.vm.output_text) if self.vm.output_text else None
        path = filedialog.asksaveasfilename(
            parent=self.root, initialdir=str(cur.parent) if cur else self._last_dir,
            initialfile=cur.name if cur else "", defaultextension=ext, confirmoverwrite=False,
            filetypes=[(self.vm.profile, f"*{ext}"), ("すべてのファイル", "*.*")])
        if path:
            self.vm.set_output_text(path)
            self.refresh()

    # ============================================================ プラグイン / 適用リスト
    def _refresh_plugin_list(self) -> None:
        q = self.search_var.get().strip().lower()
        self._visible_infos = [i for i in self.vm.infos
                               if not q or q in i.name.lower() or q in i.description.lower()]
        self.plugin_list.delete(0, "end")
        for idx, info in enumerate(self._visible_infos):
            addable = self.vm.is_addable(info)
            self.plugin_list.insert("end", info.name if addable else f"{info.name}(音声・未対応)")
            if not addable:
                self.plugin_list.itemconfig(idx, foreground="#9aa3b2")
        self._on_plugin_select()

    def _selected_info(self):
        sel = self.plugin_list.curselection()
        return self._visible_infos[sel[0]] if sel else None

    def _on_plugin_select(self) -> None:
        info = self._selected_info()
        if info is None:
            self.plugin_desc.configure(text=strings.HINT_NO_PLUGIN_SELECTED)
        else:
            text = f"{info.name} v{info.version}\n{info.description}"
            if not self.vm.is_addable(info):
                text += f"\n{strings.HINT_AUDIO_PLUGIN}"
            self.plugin_desc.configure(text=text)
        self._update_enabled()

    def _add_selected(self) -> None:
        info = self._selected_info()
        if info is not None:
            self.vm.add_plugin(info.name)

    def _on_chain_select(self) -> None:
        if self._building:
            return
        sel = self.chain_list.curselection()
        self.vm.select(sel[0] if sel else None)

    def _move(self, delta: int) -> None:
        if self.vm.selected is not None:
            self.vm.move_plugin(self.vm.selected, delta)

    def _remove(self) -> None:
        if self.vm.selected is not None:
            self.vm.remove_plugin(self.vm.selected)

    def _reset_all(self) -> None:
        i = self.vm.selected
        if i is not None:
            self.vm.reset_all_params(i)
            if self._form is not None:
                self._form.reset_all()

    # ============================================================ 実行
    def _run(self) -> None:
        chk = self.vm.check(fresh=True)
        if not chk.ok:
            return
        confirmed = False
        if chk.needs_overwrite_confirm:
            if not messagebox.askyesno(strings.DLG_OVERWRITE_TITLE,
                                       strings.DLG_OVERWRITE_BODY.format(path=self.vm.output_text),
                                       parent=self.root):
                return
            confirmed = True
        self.vm.start(overwrite_confirmed=confirmed)

    def _on_close(self) -> None:
        if self.vm.state in (AppState.RUNNING, AppState.CANCELLING):
            if not messagebox.askyesno(strings.DLG_QUIT_TITLE, strings.DLG_QUIT_BODY, parent=self.root):
                return
            self.vm.cancel()
            self._wait_then_close()
            return
        self._finish_close()

    def _wait_then_close(self) -> None:
        # 後始末(一時ファイル削除)の完了を待ってから終了する
        self.vm.pump()
        if self.vm.worker.busy:
            self.root.after(POLL_MS, self._wait_then_close)
        else:
            self._finish_close()

    def _finish_close(self) -> None:
        self._state.update(geometry=self.root.winfo_geometry(), last_dir=self._last_dir)
        save_state(self._state)
        self.root.destroy()

    # ============================================================ 定期処理・イベント
    def _tick(self) -> None:
        self.root.after(POLL_MS, self._tick)
        self.vm.pump()
        lines = self.log_handler.drain()
        if lines:
            self._log.extend(lines)
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "\n".join(lines) + "\n")
            over = int(self.log_text.index("end-1c").split(".")[0]) - 1 - MAX_KEPT
            if over > 0:                                  # 古い行から捨て、画面の負荷を一定に保つ
                self.log_text.delete("1.0", f"{over + 1}.0")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")

    def _on_vm(self, event: str, payload: object) -> None:
        if event == "progress":
            self._update_progress()
        elif event == "param":
            self._update_param_errors()
            self._update_run_state()
        elif event in ("selection",):
            self.refresh()
        else:
            self.refresh()
        if event == "done" and payload is not None and self.vm.last_result is payload:
            self.root.after(0, lambda: dialogs.show_done(self.root, payload))
        elif event == "error" and self.vm.last_error is payload:
            def show():
                self._open_log()
                dialogs.show_error(self.root, payload, "\n".join(self._log.tail(50)))
            self.root.after(0, show)

    def _toggle_log(self) -> None:
        self._open_log(not self._log_open)

    def _open_log(self, open_: bool = True) -> None:
        self._log_open = open_
        if open_:
            self.log_box.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        else:
            self.log_box.grid_remove()
        self.log_toggle.configure(text=strings.BTN_HIDE_LOG if open_ else strings.BTN_SHOW_LOG)

    # ============================================================ 反映
    def refresh(self) -> None:
        vm = self.vm
        self._building = True
        try:
            self._refresh_banner()
            self._refresh_media()
            self._refresh_chain()
            self._refresh_form()
            self._refresh_output()
        finally:
            self._building = False
        self._update_progress()
        self._update_enabled()
        self._update_run_state()

    def _refresh_banner(self) -> None:
        msgs = []
        if self.vm.tool_error:
            msgs.append(strings.BANNER_TOOL.format(message=self.vm.tool_error))
        if self._config_error:
            msgs.append(strings.BANNER_CONFIG.format(message=self._config_error))
        if msgs:
            self.banner.configure(text="\n".join(msgs))
            self.banner.grid()
        else:
            self.banner.grid_remove()

    def _refresh_media(self) -> None:
        vm = self.vm
        if vm.state is AppState.PROBING:
            self.media_label.configure(text=strings.HINT_PROBING, foreground="#667085")
        elif vm.media is not None:
            self.media_label.configure(text=format_media(vm.media), foreground="#1f2733")
        elif vm.probe_error is not None:
            e = vm.probe_error
            self.media_label.configure(text=f"[{e.code}] {e.message}", foreground="#c62828")
        else:
            self.media_label.configure(text=strings.HINT_NO_INPUT_INFO, foreground="#667085")
        if vm.media is not None and vm.media.source_fps_mode == "vfr":
            self.vfr_label.grid()
        else:
            self.vfr_label.grid_remove()

    def _chain_texts_now(self) -> list[str]:
        out = []
        for i, e in enumerate(self.vm.chain):
            out.append(f"{i + 1}. {e.name}" + ("   ⚠" if self.vm.param_errors(i) else ""))
        return out

    def _refresh_chain(self) -> None:
        texts = self._chain_texts_now()
        if texts != self._chain_texts:
            self.chain_list.delete(0, "end")
            for t in texts:
                self.chain_list.insert("end", t)
            self._chain_texts = texts
        self.chain_list.selection_clear(0, "end")
        if self.vm.selected is not None:
            self.chain_list.selection_set(self.vm.selected)
            self.chain_list.see(self.vm.selected)

    def _refresh_form(self) -> None:
        vm = self.vm
        i = vm.selected
        key = None
        if i is not None:
            key = (i, vm.chain[i].name, id(vm.chain[i]))
        if key == self._form_key:
            return
        self._form_key = key
        if self._form is not None:
            self._form.destroy()
            self._form = None
        if key is None:
            self.param_hint.grid()
            self.reset_all_btn.grid_remove()
            return
        self.param_hint.grid_remove()
        info = vm.info_of(vm.chain[i].name)
        if info is None:
            return
        entry = vm.chain[i]
        self._form = ParamForm(
            self.param_holder, info, entry,
            on_change=lambda name, text, idx=i: vm.set_param(idx, name, text),
            on_reset=lambda name, idx=i: vm.reset_param(idx, name))
        self._form.grid(row=0, column=0, sticky="nsew")
        self._form.show_errors(vm.param_errors(i))
        if info.params:
            self.reset_all_btn.grid()
        else:
            self.reset_all_btn.grid_remove()

    def _refresh_output(self) -> None:
        vm = self.vm
        if self.output_var.get() != vm.output_text:
            self.output_var.set(vm.output_text)
        self.profile_var.set(vm.profile)
        self.quality_var.set(vm.quality)
        self.audio_var.set(vm.keep_audio)
        if self.seed_var.get() != vm.seed_text:
            self.seed_var.set(vm.seed_text)
        self.overwrite_var.set(vm.overwrite)

    def _update_param_errors(self) -> None:
        i = self.vm.selected
        if self._form is not None and i is not None:
            self._form.show_errors(self.vm.param_errors(i))
        texts = self._chain_texts_now()
        if texts != self._chain_texts:
            self._building = True
            try:
                self._refresh_chain()
            finally:
                self._building = False

    def _update_progress(self) -> None:
        vm = self.vm
        p = vm.progress
        running = vm.state in (AppState.RUNNING, AppState.CANCELLING)
        if p is not None and p.total_frames:
            if self._pb_indeterminate:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._pb_indeterminate = False
            self.progress.configure(value=min(100.0, p.frames_done / p.total_frames * 100))
        elif running and p is not None:  # フレーム総数が不明
            if not self._pb_indeterminate:
                self.progress.configure(mode="indeterminate")
                self.progress.start(20)
                self._pb_indeterminate = True
        else:
            if self._pb_indeterminate:
                self.progress.stop()
                self.progress.configure(mode="determinate")
                self._pb_indeterminate = False
            self.progress.configure(value=0)
        text = format_progress(p) if (p is not None and (running or vm.last_result is not None)) else ""
        self.progress_label.configure(text=text)
        self.status_label.configure(text=vm.status)

    def _update_run_state(self) -> None:
        chk = self.vm.check()
        self.run_btn.state(["!disabled"] if chk.ok else ["disabled"])
        busy = self.vm.state in (AppState.RUNNING, AppState.CANCELLING)
        if not chk.ok and not busy and chk.reason:
            self.status_label.configure(text=chk.reason)
        else:
            self.status_label.configure(text=self.vm.status)
        self.cancel_btn.state(["!disabled"] if self.vm.state is AppState.RUNNING else ["disabled"])

    def _update_enabled(self) -> None:
        editable = self.vm.editable
        info = self._selected_info()
        self.add_btn.state(["!disabled"] if (editable and info is not None and self.vm.is_addable(info))
                           else ["disabled"])
        if self.vm.issues:
            self.issue_btn.grid()
        else:
            self.issue_btn.grid_remove()
        for w in (self.input_frame, self.output_frame, self.chain_frame, self.param_frame):
            _enable_tree(w, editable)
        if self._form is not None:
            _enable_tree(self._form, editable)
        # 入力欄・参照は PROBING 中も無効、EMPTY/EDITING で有効。ツール不在時は入力を無効にする
        if self.vm.tool_error:
            _enable_tree(self.input_frame, False)
        sel = self.vm.selected
        n = len(self.vm.chain)
        if editable and sel is not None:
            self.up_btn.state(["!disabled"] if sel > 0 else ["disabled"])
            self.down_btn.state(["!disabled"] if sel < n - 1 else ["disabled"])
            self.del_btn.state(["!disabled"])
        else:
            for b in (self.up_btn, self.down_btn, self.del_btn):
                b.state(["disabled"])


def _enable_tree(widget: tk.Misc, enabled: bool) -> None:
    for w in widget.winfo_children():
        _enable_tree(w, enabled)
    if isinstance(widget, ttk.Combobox):
        widget.configure(state="readonly" if enabled else "disabled")
    elif isinstance(widget, tk.Listbox):
        widget.configure(state="normal" if enabled else "disabled")
    elif isinstance(widget, (ttk.Button, ttk.Entry, ttk.Checkbutton, ttk.Radiobutton, ttk.Scale, ttk.Spinbox)):
        widget.state(["!disabled"] if enabled else ["disabled"])


def run_gui(config: Config, config_error: str | None = None, verbose: bool = False) -> int:
    handler = QueueLogHandler(logging.DEBUG if verbose else logging.INFO)
    logger = logging.getLogger("moviefilter")
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False

    try:
        root = tk.Tk()
    except tk.TclError as e:
        raise MovieFilterError("E_GUI_UNAVAILABLE", "画面を表示できません",
                               f"表示環境(ディスプレイ)を確認してください: {e}") from e
    manager = PluginManager(default_search_dirs(config.extra_dirs))
    vm = ViewModel(config, manager, default_registry())
    MainWindow(root, vm, config_error, handler)
    root.mainloop()
    return 0
