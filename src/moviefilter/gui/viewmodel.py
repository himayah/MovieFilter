"""GUI の状態・検証・ProcessRequest の組み立て(GUI 設計書 §7・§8.3、tkinter 非依存)。"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import QUALITIES, Config
from ..errors import MovieFilterError
from ..models import MediaInfo, PluginSpec, ProcessRequest, ProcessResult
from ..plugin_api import MediaKind
from ..plugin_manager import PluginInfo, PluginLoadIssue, PluginManager, validate_params
from ..profiles import ProfileRegistry
from ..service import SEED_LIMIT, Progress, ProcessingService, default_output_path
from ..video_io import Prober, resolve_tools
from . import strings
from .cli_text import build_cli_command
from .worker import ErrorInfo, Event, Worker


class AppState(enum.Enum):
    EMPTY = "empty"
    PROBING = "probing"
    EDITING = "editing"
    RUNNING = "running"
    CANCELLING = "cancelling"


@dataclass
class PluginEntry:
    """適用リストの 1 行。values には「既定値と異なる、または未検証の入力」だけを文字列で保持する。"""
    name: str
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RunCheck:
    ok: bool
    reason: str | None = None
    needs_overwrite_confirm: bool = False


def hms(sec: float | None) -> str:
    if sec is None:
        return "--:--:--"
    sec = int(round(sec))
    return f"{sec // 3600:d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


def format_progress(p: Progress | None) -> str:
    if p is None:
        return ""
    if p.total_frames:
        pct = int(min(1.0, p.frames_done / p.total_frames) * 100)
        eta = "" if p.done else f"  残り約{hms(p.eta_sec)}"
        return f"{pct}%  {p.frames_done}/{p.total_frames}{eta}"
    return f"{p.frames_done} フレーム  経過 {hms(p.elapsed_sec)}"


def format_media(media: MediaInfo) -> str:
    v = media.video
    parts = [f"{v.width}x{v.height}", f"{v.fps:.2f}fps".replace(".00fps", "fps")]
    if v.duration_sec is not None:
        parts.append(hms(v.duration_sec) + (f" ({v.total_frames}フレーム)" if v.total_frames else ""))
    parts.append(f"音声: {media.audio.codec} {media.audio.channels}ch" if media.audio else "音声: なし")
    return " / ".join(parts)


class ViewModel:
    """View が購読する状態の持ち主。listener(event_name, payload) で変化を通知する。"""

    def __init__(self, config: Config, manager: PluginManager, profiles: ProfileRegistry,
                 service: ProcessingService | None = None,
                 probe: Callable[[Path], MediaInfo] | None = None,
                 worker: Worker | None = None):
        self.config = config
        self.manager = manager
        self.profiles = profiles
        self.service = service or ProcessingService(config, manager, profiles)
        self.worker = worker or Worker()
        self.listeners: list[Callable[[str, object], None]] = []

        self.tool_error: str | None = None
        if probe is None:
            try:
                tools = resolve_tools(config.ffmpeg, config.ffprobe)
                prober = Prober(tools.ffprobe)
                probe = prober.probe
            except MovieFilterError as e:
                self.tool_error = e.message + (f"({e.detail})" if e.detail else "")
                probe = _unavailable_probe(e)
        self._probe = probe

        self.infos: list[PluginInfo] = []
        self.issues: list[PluginLoadIssue] = []
        self.reload_plugins()

        self.state = AppState.EMPTY
        self.input_path: Path | None = None
        self.media: MediaInfo | None = None
        self.probe_error: ErrorInfo | None = None
        self.chain: list[PluginEntry] = []
        self.selected: int | None = None

        self.profile = config.profile if config.profile in profiles.names() else profiles.names()[0]
        self.quality = config.quality
        self.keep_audio = True
        self.seed_text = "0"
        self.overwrite = False
        self.output_text = ""
        self.output_touched = False

        # ファイルシステムを見る事前検証の結果。入力・出力・形式などが同じなら使い回す
        # (スライダー操作のたびに、遅いドライブへ問い合わせないため)。
        self._fs_cache: tuple[tuple, RunCheck] | None = None

        self.progress: Progress | None = None
        self.status = strings.STATE_IDLE
        self.last_result: ProcessResult | None = None
        self.last_error: ErrorInfo | None = None

    # ------------------------------------------------------------ 通知
    def notify(self, event: str = "changed", payload: object = None) -> None:
        for fn in list(self.listeners):
            fn(event, payload)

    # ------------------------------------------------------------ プラグイン
    def reload_plugins(self) -> None:
        """プラグインを読み直す(ファイルの追加・書き換えを反映する)。"""
        self.manager.clear_cache()
        self.infos, self.issues = self.manager.discover_all()

    def info_of(self, name: str) -> PluginInfo | None:
        return next((i for i in self.infos if i.name == name), None)

    @staticmethod
    def is_addable(info: PluginInfo) -> bool:
        return info.kind is MediaKind.VIDEO

    # ------------------------------------------------------------ 入力
    def set_input(self, path: Path | str) -> None:
        if self.state not in (AppState.EMPTY, AppState.EDITING):
            return
        path = Path(path)
        self.input_path = path
        self.media = None
        self.probe_error = None
        self.state = AppState.PROBING
        self.status = strings.STATE_PROBING
        self.worker.submit("probe", lambda emit, cancel: self._probe(path))
        self.notify()

    def _sync_default_output(self) -> None:
        if self.output_touched or self.input_path is None:
            return
        ext = self.profiles.get(self.profile).extension
        names = [e.name for e in self.chain]
        self.output_text = str(default_output_path(self.input_path, names, ext))

    # ------------------------------------------------------------ 適用リスト
    def add_plugin(self, name: str) -> None:
        info = self.info_of(name)
        if info is None or not self.is_addable(info) or not self.editable:
            return
        self.chain.append(PluginEntry(name))
        self.selected = len(self.chain) - 1
        self._sync_default_output()
        self.notify()

    def remove_plugin(self, index: int) -> None:
        if not self.editable or not 0 <= index < len(self.chain):
            return
        del self.chain[index]
        if not self.chain:
            self.selected = None
        else:
            self.selected = min(index, len(self.chain) - 1)
        self._sync_default_output()
        self.notify()

    def move_plugin(self, index: int, delta: int) -> None:
        j = index + delta
        if not self.editable or not (0 <= index < len(self.chain) and 0 <= j < len(self.chain)):
            return
        self.chain[index], self.chain[j] = self.chain[j], self.chain[index]
        self.selected = j
        self._sync_default_output()
        self.notify()

    def select(self, index: int | None) -> None:
        if index is not None and not 0 <= index < len(self.chain):
            index = None
        if index != self.selected:
            self.selected = index
            self.notify("selection")

    # ------------------------------------------------------------ パラメータ
    def set_param(self, index: int, key: str, text: str) -> None:
        """入力値を設定する。既定値と同じ値になったら保持から外す(CLI コマンドを短く保つ)。"""
        entry = self.chain[index]
        info = self.info_of(entry.name)
        if info is None:
            return
        spec = next((s for s in info.params if s.name == key), None)
        if spec is None:
            return
        try:
            same = validate_params(info.cls, {key: text})[key] == spec.default
        except MovieFilterError:
            same = False
        if same:
            entry.values.pop(key, None)
        else:
            entry.values[key] = text
        self.notify("param")

    def reset_param(self, index: int, key: str) -> None:
        self.chain[index].values.pop(key, None)
        self.notify("param")

    def reset_all_params(self, index: int) -> None:
        self.chain[index].values.clear()
        self.notify("param")

    def param_errors(self, index: int) -> dict[str, str]:
        entry = self.chain[index]
        info = self.info_of(entry.name)
        errors: dict[str, str] = {}
        if info is None:
            return {"": f"プラグインが見つかりません: {entry.name}"}
        for key, text in entry.values.items():
            try:
                validate_params(info.cls, {key: text})
            except MovieFilterError as e:
                errors[key] = e.message
        return errors

    # ------------------------------------------------------------ 出力設定
    def set_output_text(self, text: str) -> None:
        if text != self.output_text:
            self.output_text = text
            self.output_touched = True
            self.notify("output")

    def set_profile(self, name: str) -> None:
        self.profile = name
        self._sync_default_output()
        self.notify()

    def set_quality(self, q: str) -> None:
        if q in QUALITIES:
            self.quality = q
            self.notify("output")

    def set_keep_audio(self, v: bool) -> None:
        self.keep_audio = bool(v)
        self.notify("output")

    def set_seed_text(self, text: str) -> None:
        self.seed_text = text
        self.notify("output")

    def set_overwrite(self, v: bool) -> None:
        self.overwrite = bool(v)
        self.notify("output")

    # ------------------------------------------------------------ 実行可否と要求の組み立て
    @property
    def editable(self) -> bool:
        return self.state in (AppState.EMPTY, AppState.EDITING)

    def parse_seed(self) -> int | None:
        t = self.seed_text.strip()
        if not t.isdigit() and not (t[:1] == "+" and t[1:].isdigit()):
            return None
        v = int(t)
        return v if 0 <= v < SEED_LIMIT else None

    def build_request(self, overwrite: bool | None = None) -> ProcessRequest:
        seed = self.parse_seed()
        out = self.output_text.strip()
        return ProcessRequest(
            input_path=self.input_path,
            plugins=tuple(PluginSpec(e.name, dict(e.values)) for e in self.chain),
            output_path=Path(out) if out else None,
            profile=self.profile,
            quality=self.quality,
            keep_audio=self.keep_audio,
            overwrite=self.overwrite if overwrite is None else overwrite,
            seed=0 if seed is None else seed,
        )

    def check(self, fresh: bool = False) -> RunCheck:
        """実行できるかと、できない理由。

        パラメータ・シード・適用リストなど、メモリ上で決まる検査は毎回行う。ファイルシステムを見る検査
        (入力の存在、出力先フォルダ、既存ファイルなど)は、関係する設定が変わらない限り前回の結果を使い回す。
        実際に実行する直前は fresh=True で必ず確かめ直す。
        """
        if self.state in (AppState.RUNNING, AppState.CANCELLING):
            return RunCheck(False, strings.REASON_BUSY)
        if self.tool_error:
            return RunCheck(False, strings.BANNER_TOOL.format(message=self.tool_error))
        if self.state is AppState.PROBING:
            return RunCheck(False, strings.STATE_PROBING)
        if self.media is None or self.input_path is None:
            return RunCheck(False, strings.REASON_NO_INPUT)
        if not self.chain:
            return RunCheck(False, strings.REASON_NO_CHAIN)
        for i, entry in enumerate(self.chain):
            errs = self.param_errors(i)
            if errs:
                key = next(iter(errs))
                where = f"{i + 1}. {entry.name}" + (f" の {key}" if key else "")
                return RunCheck(False, strings.REASON_BAD_PARAM.format(where=where))
        if self.parse_seed() is None:
            return RunCheck(False, strings.REASON_BAD_SEED.format(limit=SEED_LIMIT - 1))
        key = (self.input_path, self.output_text.strip(), self.profile, self.quality, self.overwrite)
        if not fresh and self._fs_cache is not None and self._fs_cache[0] == key:
            return self._fs_cache[1]
        result = self._check_filesystem()
        self._fs_cache = (key, result)
        return result

    def _check_filesystem(self) -> RunCheck:
        try:
            self.service.validate_request(self.build_request())
        except MovieFilterError as e:
            if e.code == "E_OUTPUT_EXISTS":
                return RunCheck(True, None, needs_overwrite_confirm=True)
            return RunCheck(False, e.message)
        return RunCheck(True)

    def cli_command(self) -> str | None:
        if self.input_path is None or not self.chain:
            return None
        return build_cli_command(self.build_request(), self.config.profile, self.config.quality)

    # ------------------------------------------------------------ 実行・中止
    def start(self, overwrite_confirmed: bool = False) -> bool:
        """設定をスナップショットして処理を開始する。開始できたら True。"""
        chk = self.check(fresh=True)
        if not chk.ok or (chk.needs_overwrite_confirm and not overwrite_confirmed):
            return False
        req = self.build_request(overwrite=self.overwrite or overwrite_confirmed)
        self.state = AppState.RUNNING
        self.status = strings.STATE_RUNNING
        self.progress = None
        self.last_result = None
        self.last_error = None
        service = self.service
        self.worker.submit("run", lambda emit, cancel: service.run(req, on_progress=emit, cancel=cancel))
        self.notify()
        return True

    def cancel(self) -> None:
        if self.state is AppState.RUNNING:
            self.state = AppState.CANCELLING
            self.status = strings.STATE_CANCELLING
            self.worker.cancel()
            self.notify()

    # ------------------------------------------------------------ イベント処理(UI スレッドから定期的に呼ぶ)
    def pump(self) -> None:
        for ev in self.worker.drain():
            self._handle(ev)

    def _handle(self, ev: Event) -> None:
        if ev.kind != "progress":
            self._fs_cache = None  # 処理の完了・失敗などで、ディスクの状態(出力ファイルの有無など)が変わりうる
        if ev.job == "probe":
            if ev.kind == "done":
                self.media = ev.payload
                self.state = AppState.EDITING
                self.status = strings.STATE_IDLE
                self._sync_default_output()
            elif ev.kind == "error":
                self.media = None
                self.probe_error = ev.payload
                self.state = AppState.EMPTY
                self.status = strings.STATE_IDLE
            self.notify("probe", ev)
        elif ev.job == "run":
            if ev.kind == "progress":
                self.progress = ev.payload
                self.notify("progress", ev.payload)
                return
            self.state = AppState.EDITING
            if ev.kind == "done":
                self.last_result = ev.payload
                self.status = strings.STATE_DONE
            elif ev.kind == "cancelled":
                self.progress = None
                self.status = strings.STATE_CANCELLED
            else:
                self.last_error = ev.payload
                self.status = strings.STATE_FAILED
            self.notify(ev.kind, ev.payload)


def _unavailable_probe(err: MovieFilterError) -> Callable[[Path], MediaInfo]:
    def probe(path: Path) -> MediaInfo:
        raise err
    return probe
