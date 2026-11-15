"""GUI のログ受け皿(tkinter 非依存)。

ワーカースレッドから呼ばれるログを上限つきのキューに積み、UI スレッドが少しずつ取り出す。
プラグインがフレームごとにログを出しても、メモリと画面の更新負荷が際限なく増えないようにする。
"""
from __future__ import annotations

import logging
import queue
import time
from collections import deque

MAX_QUEUED = 5000       # 取り出しが追いつかないときに溜めておく最大行数(超えた分は捨てる)
MAX_KEPT = 2000         # 画面・エラー報告用に保持する最大行数
DRAIN_PER_TICK = 500    # 1 回の取り出しで UI に渡す最大行数

_LABELS = {logging.WARNING: "警告", logging.ERROR: "エラー", logging.CRITICAL: "エラー"}


class QueueLogHandler(logging.Handler):
    def __init__(self, level: int = logging.INFO) -> None:
        super().__init__(level)
        self.queue: "queue.Queue[str]" = queue.Queue(MAX_QUEUED)
        self.dropped = 0

    def emit(self, record: logging.LogRecord) -> None:
        label = _LABELS.get(record.levelno)
        line = f"{time.strftime('%H:%M:%S')} {label + ': ' if label else ''}{record.getMessage()}"
        try:
            self.queue.put_nowait(line)
        except queue.Full:
            self.dropped += 1

    def drain(self, limit: int = DRAIN_PER_TICK) -> list[str]:
        """最大 limit 行を取り出す。捨てた行があれば、その旨の 1 行を末尾に添える。"""
        lines: list[str] = []
        while len(lines) < limit:
            try:
                lines.append(self.queue.get_nowait())
            except queue.Empty:
                break
        if self.dropped and self.queue.empty():
            lines.append(f"(ログが多いため {self.dropped} 行を省略しました)")
            self.dropped = 0
        return lines


class LogKeeper:
    """直近 MAX_KEPT 行だけを保持する(エラー報告の「ログをコピー」用)。"""

    def __init__(self, max_lines: int = MAX_KEPT) -> None:
        self.lines: deque[str] = deque(maxlen=max_lines)

    def extend(self, lines: list[str]) -> None:
        self.lines.extend(lines)

    def tail(self, n: int) -> list[str]:
        return list(self.lines)[-n:]
