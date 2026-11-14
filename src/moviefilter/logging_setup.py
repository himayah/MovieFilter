"""ログ設定。ロガー名は moviefilter.<module>(プラグインは moviefilter.plugin.<ファイル名>)。"""
from __future__ import annotations

import logging
import sys

ROOT = "moviefilter"


class _Formatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        msg = record.getMessage()
        if record.levelno >= logging.ERROR:
            return f"エラー: {msg}"
        if record.levelno >= logging.WARNING:
            return f"警告: {msg}"
        return f"[{record.levelname}] {record.name}: {msg}"


def setup_logging(verbose: bool = False) -> None:
    logger = logging.getLogger(ROOT)
    for h in list(logger.handlers):
        logger.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(_Formatter())
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.WARNING)
    logger.propagate = False
