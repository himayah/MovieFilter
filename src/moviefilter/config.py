"""設定ファイル(TOML)の読み込み(詳細設計書 §5.6)。"""
from __future__ import annotations

import logging
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

log = logging.getLogger("moviefilter.config")

QUALITIES = ("low", "medium", "high")


@dataclass(frozen=True)
class Config:
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    extra_dirs: tuple[Path, ...] = ()
    profile: str = "mp4-h264"
    quality: str = "medium"
    source: Path | None = None


_KNOWN = {
    "tools": {"ffmpeg": str, "ffprobe": str},
    "plugins": {"extra_dirs": list},
    "defaults": {"profile": str, "quality": str},
}


def _default_user_config() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home()))
        return base / "moviefilter" / "config.toml"
    return Path.home() / ".config" / "moviefilter" / "config.toml"


def find_config_file(explicit: Path | None) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError("E_CONFIG_INVALID", f"設定ファイルが見つかりません: {explicit}")
        return explicit
    for cand in (Path.cwd() / "moviefilter.toml", _default_user_config()):
        if cand.is_file():
            return cand
    return None


def load_config(explicit: Path | None = None) -> Config:
    path = find_config_file(explicit)
    if path is None:
        return Config()
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except (tomllib.TOMLDecodeError, OSError) as e:
        raise ConfigError("E_CONFIG_INVALID", f"設定ファイルを読み込めません: {path}", str(e)) from e

    values: dict[str, object] = {}
    for section, body in data.items():
        if section not in _KNOWN:
            log.warning("設定ファイルに不明な項目があります: %s", section)
            continue
        if not isinstance(body, dict):
            raise ConfigError("E_CONFIG_INVALID", f"設定ファイルの値が不正です: {section}")
        for key, value in body.items():
            expected = _KNOWN[section].get(key)
            if expected is None:
                log.warning("設定ファイルに不明な項目があります: %s.%s", section, key)
                continue
            if not isinstance(value, expected) or (
                expected is list and not all(isinstance(v, str) for v in value)
            ):
                raise ConfigError("E_CONFIG_INVALID", f"設定ファイルの値が不正です: {section}.{key}")
            values[f"{section}.{key}"] = value

    quality = values.get("defaults.quality", "medium")
    if quality not in QUALITIES:
        raise ConfigError("E_CONFIG_INVALID", "設定ファイルの値が不正です: defaults.quality",
                          f"指定可能: {', '.join(QUALITIES)}")
    return Config(
        ffmpeg=values.get("tools.ffmpeg", "ffmpeg"),
        ffprobe=values.get("tools.ffprobe", "ffprobe"),
        extra_dirs=tuple(Path(p).expanduser() for p in values.get("plugins.extra_dirs", [])),
        profile=values.get("defaults.profile", "mp4-h264"),
        quality=quality,
        source=path,
    )
