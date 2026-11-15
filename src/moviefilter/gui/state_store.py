"""GUI 専用の状態ファイル(ウィンドウサイズ・最後に開いたフォルダ)。失敗しても動作に影響させない。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def state_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
        return base / "moviefilter" / "gui_state.json"
    return Path.home() / ".config" / "moviefilter" / "gui_state.json"


def load_state(path: Path | None = None) -> dict:
    try:
        data = json.loads((path or state_path()).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: Path | None = None) -> None:
    p = path or state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass
