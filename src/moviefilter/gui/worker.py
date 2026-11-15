"""ワーカースレッドとイベントキュー(GUI 設計書 §7.2、tkinter 非依存)。

ワーカーから UI へは queue.Queue でのみ伝える。Tk のウィジェットには触れない。
"""
from __future__ import annotations

import logging
import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Any, Callable

from ..errors import MovieFilterError, PluginRuntimeError

log = logging.getLogger("moviefilter.gui")


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    message: str
    detail: str | None = None
    plugin: str | None = None
    frame_index: int | None = None


@dataclass(frozen=True)
class Event:
    job: str    # "probe" / "run"
    kind: str   # "progress" / "done" / "error" / "cancelled"
    payload: Any = None


def classify(exc: BaseException) -> ErrorInfo:
    if isinstance(exc, PluginRuntimeError):
        return ErrorInfo(exc.code, exc.message, exc.detail, exc.plugin, exc.frame_index)
    if isinstance(exc, MovieFilterError):
        return ErrorInfo(exc.code, exc.message, exc.detail)
    return ErrorInfo("E_INTERNAL", "予期しないエラーが発生しました",
                     "".join(traceback.format_exception(exc)))


class Worker:
    """同時に 1 ジョブだけを実行する。"""

    def __init__(self) -> None:
        self.events: "queue.Queue[Event]" = queue.Queue()
        self.cancel_event = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def submit(self, job: str, fn: Callable[[Callable[[Any], None], threading.Event], Any]) -> None:
        """fn(emit_progress, cancel_event) をワーカーで実行する。"""
        if self.busy:
            raise RuntimeError("ジョブを実行中です")
        self.cancel_event = threading.Event()
        cancel = self.cancel_event

        def emit(progress: Any) -> None:
            self.events.put(Event(job, "progress", progress))

        def target() -> None:
            try:
                result = fn(emit, cancel)
            except MovieFilterError as e:
                if e.code == "E_CANCELLED":
                    self.events.put(Event(job, "cancelled"))
                else:
                    self.events.put(Event(job, "error", classify(e)))
            except Exception as e:  # 想定外の例外も UI に伝える
                log.exception("予期しないエラー")
                self.events.put(Event(job, "error", classify(e)))
            else:
                self.events.put(Event(job, "done", result))

        self._thread = threading.Thread(target=target, name=f"moviefilter-{job}", daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self.cancel_event.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def drain(self) -> list[Event]:
        out: list[Event] = []
        while True:
            try:
                out.append(self.events.get_nowait())
            except queue.Empty:
                return out
