"""GUI の設定から、等価な `moviefilter apply ...` コマンド文字列を作る(GUI-10、tkinter 非依存)。"""
from __future__ import annotations

import shlex
import subprocess
import sys

from ..models import PluginSpec, ProcessRequest


def format_spec(spec: PluginSpec) -> str | None:
    """`name[:k=v,...]` を作る。値に , または = を含み CLI で表せないときは None。"""
    if not spec.raw_params:
        return spec.name
    parts = []
    for key, value in spec.raw_params.items():
        if "," in value or "=" in value:
            return None
        parts.append(f"{key}={value}")
    return f"{spec.name}:{','.join(parts)}"


def _quote(arg: str, windows: bool) -> str:
    if windows:
        return subprocess.list2cmdline([arg])
    return shlex.quote(arg)


def build_cli_command(req: ProcessRequest, default_profile: str = "mp4-h264",
                      default_quality: str = "medium", windows: bool | None = None) -> str | None:
    """既定値と同じ項目は省略する。CLI で表現できない設定のときは None を返す。"""
    windows = sys.platform == "win32" if windows is None else windows
    args: list[str] = ["moviefilter", "apply", str(req.input_path)]
    for spec in req.plugins:
        text = format_spec(spec)
        if text is None:
            return None
        args += ["-p", text]
    if req.output_path is not None:
        args += ["-o", str(req.output_path)]
    if req.profile != default_profile:
        args += ["--profile", req.profile]
    if req.quality != default_quality:
        args += ["--quality", req.quality]
    if not req.keep_audio:
        args.append("--no-audio")
    if req.overwrite:
        args.append("--overwrite")
    if req.seed != 0:
        args += ["--seed", str(req.seed)]
    return " ".join(_quote(a, windows) if i else a for i, a in enumerate(args))
