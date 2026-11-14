"""プラグインの検出・ロード・パラメータ検証(詳細設計書 §5.1)。"""
from __future__ import annotations

import importlib.util
import inspect
import logging
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Sequence

from .errors import PluginError
from .models import PluginSpec
from .plugin_api import (
    API_VERSION,
    MediaKind,
    ParamSpec,
    ParamType,
    Plugin,
    TemporalWindow,
    VideoPlugin,
)

log = logging.getLogger("moviefilter.plugin_manager")

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]*$")
PARAM_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_INT_RE = re.compile(r"^[+-]?\d+$")
_BOOL_TRUE = {"true", "1", "yes"}
_BOOL_FALSE = {"false", "0", "no"}
MODULE_PREFIX = "moviefilter_plugin_"


@dataclass(frozen=True)
class PluginInfo:
    name: str
    kind: MediaKind
    version: str
    description: str
    params: tuple[ParamSpec, ...]
    cls: type[Plugin]
    path: Path


@dataclass(frozen=True)
class PluginLoadIssue:
    path: Path
    reason: str


def builtin_dir() -> Path:
    return Path(__file__).parent / "builtin_plugins"


def default_search_dirs(extra_dirs: Sequence[Path] = ()) -> list[Path]:
    """同梱 → 環境変数 MOVIEFILTER_PLUGIN_PATH → 設定ファイルの順(先に見つかったものが優先)。"""
    dirs: list[Path] = [builtin_dir()]
    env = os.environ.get("MOVIEFILTER_PLUGIN_PATH", "")
    dirs += [Path(p).expanduser() for p in env.split(os.pathsep) if p]
    dirs += [Path(p).expanduser() for p in extra_dirs]
    return dirs


def stem_of(name: str) -> str:
    return name.replace("-", "_")


# ---------------------------------------------------------------- 検証

def check_param_specs(cls: type[Plugin]) -> str | None:
    """ParamSpec の整合を検証し、問題があれば理由を返す。"""
    seen: set[str] = set()
    for spec in cls.params:
        if not isinstance(spec, ParamSpec):
            return "params に ParamSpec 以外が含まれています"
        n = spec.name
        if not isinstance(n, str) or not PARAM_NAME_RE.match(n):
            return f"ParamSpec の名前が不正です: {n!r}"
        if n in seen:
            return f"ParamSpec の名前が重複しています: {n}"
        seen.add(n)
        t = spec.type
        if not isinstance(t, ParamType):
            return f"ParamSpec の型が不正です: {n}"
        if t not in (ParamType.INT, ParamType.FLOAT) and (spec.min is not None or spec.max is not None):
            return f"min/max は INT・FLOAT のみ指定できます: {n}"
        if t is ParamType.CHOICE:
            if not spec.choices or not all(isinstance(c, str) for c in spec.choices):
                return f"CHOICE には文字列の choices が必要です: {n}"
        elif spec.choices is not None:
            return f"choices は CHOICE のみ指定できます: {n}"
        if spec.min is not None and spec.max is not None and spec.min > spec.max:
            return f"min が max を超えています: {n}"
        try:
            _coerce(spec, spec.default)
        except PluginError as e:
            return f"既定値が不正です: {n} ({e.message})"
    return None


def check_plugin_class(cls: type[Plugin], stem: str) -> str | None:
    """プラグインクラスを検証し、ロードできない理由があれば返す。"""
    name = getattr(cls, "name", None)
    if not isinstance(name, str) or not NAME_RE.match(name):
        return f"name が命名規則に合いません: {name!r}"
    if stem_of(name) != stem:
        return f"ファイル名と name が一致しません: ファイル {stem!r} / name {name!r}"
    if not isinstance(getattr(cls, "kind", None), MediaKind):
        return "kind が定義されていません(VideoPlugin を継承してください)"
    req = getattr(cls, "requires_api", None)
    if not (isinstance(req, tuple) and len(req) == 2 and all(isinstance(v, int) for v in req)):
        return f"requires_api が不正です: {req!r}"
    if req[0] != API_VERSION[0] or req[1] > API_VERSION[1]:
        return (f"API バージョンが非互換です (要求 {req[0]}.{req[1]} / 本体 "
                f"{API_VERSION[0]}.{API_VERSION[1]})")
    if not isinstance(cls.params, tuple):
        return "params はタプルで定義してください"
    return check_param_specs(cls)


# ---------------------------------------------------------------- パラメータ変換

def _type_label(t: ParamType) -> str:
    return {ParamType.INT: "整数", ParamType.FLOAT: "数値", ParamType.BOOL: "真偽値",
            ParamType.STR: "文字列", ParamType.CHOICE: "選択肢"}[t]


def _range_text(spec: ParamSpec) -> str:
    if spec.min is not None and spec.max is not None:
        return f"{spec.min}〜{spec.max} で指定してください"
    if spec.min is not None:
        return f"{spec.min} 以上で指定してください"
    return f"{spec.max} 以下で指定してください"


def _invalid(spec: ParamSpec, raw: object) -> PluginError:
    detail = f"指定可能: {', '.join(spec.choices)}" if spec.type is ParamType.CHOICE else None
    return PluginError("E_PARAM_INVALID", f"{spec.name} の値が{_type_label(spec.type)}ではありません: {raw}", detail)


def _coerce(spec: ParamSpec, raw: object) -> object:
    t = spec.type
    if isinstance(raw, str):
        if t is ParamType.INT:
            if not _INT_RE.match(raw):
                raise _invalid(spec, raw)
            value: object = int(raw)
        elif t is ParamType.FLOAT:
            try:
                value = float(raw)
            except ValueError:
                raise _invalid(spec, raw) from None
            if not math.isfinite(value):
                raise _invalid(spec, raw)
        elif t is ParamType.BOOL:
            low = raw.lower()
            if low in _BOOL_TRUE:
                value = True
            elif low in _BOOL_FALSE:
                value = False
            else:
                raise _invalid(spec, raw)
        else:  # STR / CHOICE
            value = raw
    else:  # 型付きの値(テスト支援・既定値の検証)
        if t is ParamType.INT and isinstance(raw, int) and not isinstance(raw, bool):
            value = raw
        elif t is ParamType.FLOAT and isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            if not math.isfinite(value):
                raise _invalid(spec, raw)
        elif t is ParamType.BOOL and isinstance(raw, bool):
            value = raw
        elif t in (ParamType.STR, ParamType.CHOICE) and isinstance(raw, str):
            value = raw
        else:
            raise _invalid(spec, raw)

    if t in (ParamType.INT, ParamType.FLOAT):
        if (spec.min is not None and value < spec.min) or (spec.max is not None and value > spec.max):
            raise PluginError("E_PARAM_RANGE", f"{spec.name} は {_range_text(spec)}: {value}")
    if t is ParamType.CHOICE and value not in spec.choices:
        raise _invalid(spec, value)
    return value


def validate_params(cls: type[Plugin], raw_params: dict[str, object]) -> dict[str, object]:
    """未知キー・型変換・範囲を検証し、既定値で補完した辞書を返す。"""
    specs = {s.name: s for s in cls.params}
    unknown = [k for k in raw_params if k not in specs]
    if unknown:
        avail = ", ".join(specs) if specs else "(なし)"
        raise PluginError("E_PARAM_UNKNOWN", f"{cls.name} に不明なパラメータがあります: {', '.join(unknown)}",
                          f"指定可能: {avail}")
    values: dict[str, object] = {}
    for name, spec in specs.items():
        values[name] = _coerce(spec, raw_params[name]) if name in raw_params else spec.default
    return values


def check_supported(cls: type[Plugin]) -> None:
    """第1段階で未対応のプラグインを明示的に拒否する。"""
    if cls.kind is not MediaKind.VIDEO or not issubclass(cls, VideoPlugin):
        raise PluginError("E_PLUGIN_KIND_UNSUPPORTED", f"音声プラグインは現在使用できません: {cls.name}")
    if cls.temporal_window != TemporalWindow(0, 0):
        raise PluginError("E_TEMPORAL_UNSUPPORTED",
                          f"前後フレームを参照するプラグインは現在使用できません: {cls.name}")


def create_plugin(cls: type[Plugin], raw_params: dict[str, object]) -> Plugin:
    """検証済みパラメータでインスタンスを作る(本体・テスト支援で共通)。"""
    check_supported(cls)
    values = validate_params(cls, raw_params)
    try:
        return cls(**values)
    except PluginError:
        raise
    except Exception as e:
        raise PluginError("E_PLUGIN_INIT", f"プラグインの初期化に失敗しました: {cls.name}",
                          f"{type(e).__name__}: {e}") from e


# ---------------------------------------------------------------- ロード

def _load_module(path: Path, stem: str, is_package: bool) -> ModuleType:
    mod_name = MODULE_PREFIX + stem
    if is_package:
        spec = importlib.util.spec_from_file_location(
            mod_name, path / "__init__.py", submodule_search_locations=[str(path)])
    else:
        spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"ロードできません: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        for key in [k for k in sys.modules if k == mod_name or k.startswith(mod_name + ".")]:
            del sys.modules[key]
        raise
    return module


def _find_classes(module: ModuleType) -> list[type[Plugin]]:
    prefix = module.__name__
    found = []
    for obj in vars(module).values():
        if not (inspect.isclass(obj) and issubclass(obj, Plugin)) or inspect.isabstract(obj):
            continue
        if obj.__module__ == prefix or obj.__module__.startswith(prefix + "."):
            found.append(obj)
    return found


class PluginManager:
    def __init__(self, search_dirs: Sequence[Path]):
        self.search_dirs = [Path(d) for d in search_dirs]

    # ------------------------------------------------------------ 走査
    def _entries(self) -> list[tuple[str, Path, bool]]:
        """(ステム, パス, パッケージか) を検索順に列挙する。"""
        out = []
        for d in self.search_dirs:
            if not d.is_dir():
                log.debug("プラグインの置き場が存在しません: %s", d)
                continue
            for p in sorted(d.iterdir()):
                if p.name.startswith(("_", ".")):
                    continue
                if p.is_file() and p.suffix == ".py":
                    out.append((p.stem, p, False))
                elif p.is_dir() and (p / "__init__.py").is_file():
                    out.append((p.name, p, True))
        return out

    def _load(self, stem: str, path: Path, is_package: bool) -> PluginInfo:
        """1つのプラグインをロードして検証する。失敗時は _LoadFailure を送出する。"""
        try:
            module = _load_module(path, stem, is_package)
        except SyntaxError as e:
            raise _LoadFailure(f"構文エラー ({Path(e.filename or path).name}:{e.lineno}): {e.msg}") from e
        except KeyboardInterrupt:
            raise
        except BaseException as e:  # プラグインが import 時に sys.exit() しても本体は落とさない
            raise _LoadFailure(f"import に失敗: {type(e).__name__}: {e}") from e
        classes = _find_classes(module)
        if not classes:
            raise _LoadFailure("プラグインクラスが見つかりません")
        if len(classes) > 1:
            raise _LoadFailure("プラグインクラスが複数あります: " + ", ".join(c.__name__ for c in classes))
        cls = classes[0]
        reason = check_plugin_class(cls, stem)
        if reason:
            raise _LoadFailure(reason)
        return PluginInfo(cls.name, cls.kind, cls.version, cls.description, cls.params, cls, path)

    def discover_all(self) -> tuple[list[PluginInfo], list[PluginLoadIssue]]:
        """全プラグインをロードする(`list` 用)。失敗は他に波及させない。"""
        infos: list[PluginInfo] = []
        issues: list[PluginLoadIssue] = []
        seen: set[str] = set()
        for stem, path, is_pkg in self._entries():
            if stem in seen:
                log.warning("名前が重複するプラグインを無視しました: %s (%s)", stem, path)
                continue
            seen.add(stem)
            try:
                infos.append(self._load(stem, path, is_pkg))
            except _LoadFailure as e:
                log.warning("プラグインの読み込みに失敗しました: %s (%s)", path.name, e.reason)
                issues.append(PluginLoadIssue(path, e.reason))
        infos.sort(key=lambda i: i.name)
        return infos, issues

    def available_names(self) -> list[str]:
        """import せずに、ファイル名から利用可能な名前の候補を列挙する。"""
        return sorted({stem for stem, _, _ in self._entries()})

    # ------------------------------------------------------------ 解決
    def resolve(self, name: str) -> PluginInfo:
        """指定名のプラグインだけをロードする(`apply` / `info` 用)。"""
        if not NAME_RE.match(name):
            raise PluginError("E_PARAM_INVALID", f"プラグイン名が不正です: {name}",
                              "小文字英字で始まり、小文字英数字・_・- が使えます")
        want = stem_of(name)
        for stem, path, is_pkg in self._entries():
            if stem != want:
                continue
            try:
                info = self._load(stem, path, is_pkg)
            except _LoadFailure as e:
                raise PluginError("E_PLUGIN_NOT_FOUND", f"プラグインが見つかりません: {name}",
                                  f"{path.name} の読み込みに失敗しました: {e.reason}") from e
            if info.name != name:
                raise PluginError("E_PLUGIN_NOT_FOUND", f"プラグインが見つかりません: {name}",
                                  f"このファイルのプラグイン名は {info.name} です")
            return info
        names = self.available_names()
        raise PluginError("E_PLUGIN_NOT_FOUND", f"プラグインが見つかりません: {name}",
                          "利用可能: " + (", ".join(names) if names else "(なし)"))

    def instantiate(self, spec: PluginSpec) -> Plugin:
        info = self.resolve(spec.name)
        return create_plugin(info.cls, dict(spec.raw_params))


class _LoadFailure(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
