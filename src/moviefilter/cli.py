"""UI層(CLI)(詳細設計書 §7)。"""
from __future__ import annotations

import argparse
import re
import sys
import threading
import time
import traceback
import unicodedata
from pathlib import Path

from . import __version__
from .config import QUALITIES, load_config
from .errors import MovieFilterError, UsageError
from .logging_setup import setup_logging
from .models import PluginSpec, ProcessRequest
from .plugin_manager import PluginManager, default_search_dirs
from .profiles import default_registry
from .service import SEED_LIMIT, Progress, ProcessingService

_SPEC_RE = re.compile(r"^([a-z][a-z0-9_-]*)(?::(.*))?$")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


# ---------------------------------------------------------------- 引数の解析

def parse_plugin_spec(text: str) -> PluginSpec:
    """`name[:key=value[,key=value ...]]` を PluginSpec にする(型変換は PluginManager が行う)。"""
    m = _SPEC_RE.match(text)
    if not m:
        raise MovieFilterError("E_PARAM_INVALID", f"プラグイン指定の書式が不正です: {text}",
                               "書式: 名前[:パラメータ名=値,パラメータ名=値]")
    name, body = m.group(1), m.group(2)
    params: dict[str, str] = {}
    if body is not None:
        if body == "":
            raise MovieFilterError("E_PARAM_INVALID", f"プラグイン指定の書式が不正です: {text}",
                                   "':' の後にパラメータ名=値 を指定してください")
        for part in body.split(","):
            key, sep, value = part.partition("=")
            if not sep or not _KEY_RE.match(key) or "=" in value:
                raise MovieFilterError("E_PARAM_INVALID", f"パラメータの書式が不正です: {part!r} ({text})",
                                       "書式: パラメータ名=値(値に , と = は使えません)")
            if key in params:
                raise MovieFilterError("E_PARAM_INVALID", f"パラメータが重複しています: {key} ({text})")
            params[key] = value
    return PluginSpec(name, params)


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse 既定の英語 usage 出力は使わない
        raise UsageError("E_USAGE", _translate_argparse(message))


def _translate_argparse(msg: str) -> str:
    m = re.match(r"the following arguments are required: (.*)", msg)
    if m:
        if m.group(1) == "command":
            return "サブコマンドを指定してください (list / info / apply / profiles / gui)"
        return f"必須の引数がありません: {m.group(1)}"
    m = re.match(r"unrecognized arguments: (.*)", msg)
    if m:
        return f"不明な引数です: {m.group(1)}"
    m = re.match(r"argument (.*): invalid choice: (.*)", msg)
    if m:
        return f"引数 {m.group(1)} の値が不正です: {m.group(2)}"
    m = re.match(r"argument (.*): expected one argument", msg)
    if m:
        return f"引数 {m.group(1)} には値が必要です"
    m = re.match(r"argument (.*): invalid .* value: (.*)", msg)
    if m:
        return f"引数 {m.group(1)} の値が不正です: {m.group(2)}"
    return f"引数が不正です: {msg}"


def _seed_arg(text: str) -> int:
    try:
        v = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid int value: {text}") from None
    if not 0 <= v < SEED_LIMIT:
        raise MovieFilterError("E_PARAM_INVALID",
                               f"--seed は 0 以上 {SEED_LIMIT - 1} 以下の整数で指定してください: {text}")
    return v


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="moviefilter", description="動画にフィルタプラグインを適用して保存します。")
    p.add_argument("--version", action="version", version=f"moviefilter {__version__}")
    sub = p.add_subparsers(dest="command", required=True, parser_class=_Parser)

    def common(sp):
        sp.add_argument("--config", type=Path, help="設定ファイルを明示指定")
        sp.add_argument("-v", "--verbose", action="store_true", help="詳細ログを表示")

    sp = sub.add_parser("list", help="利用可能なプラグインの一覧")
    common(sp)
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("info", help="プラグインの詳細とパラメータを表示")
    sp.add_argument("plugin")
    common(sp)
    sp.set_defaults(func=cmd_info)

    sp = sub.add_parser("profiles", help="出力プロファイルの一覧")
    common(sp)
    sp.set_defaults(func=cmd_profiles)

    sp = sub.add_parser("apply", help="動画にプラグインを適用して保存")
    sp.add_argument("input", type=Path, help="入力動画ファイル")
    sp.add_argument("-p", "--plugin", action="append", required=True, metavar="SPEC",
                    help="適用するプラグイン(名前[:パラメータ=値,...])。複数指定可、指定順に適用")
    sp.add_argument("-o", "--output", type=Path, help="出力ファイル(既定: <入力名>_<フィルタ名>.mp4)")
    sp.add_argument("--profile", help="出力プロファイル(既定: mp4-h264)")
    sp.add_argument("--quality", choices=QUALITIES, help="画質(既定: medium)")
    sp.add_argument("--no-audio", action="store_true", help="音声を出力しない")
    sp.add_argument("--overwrite", action="store_true", help="既存の出力ファイルを上書きする")
    sp.add_argument("--seed", type=_seed_arg, default=0, help="乱数プラグイン用のシード(既定: 0)")
    sp.add_argument("-q", "--quiet", action="store_true", help="進捗表示を出さない")
    common(sp)
    sp.set_defaults(func=cmd_apply)

    sp = sub.add_parser("gui", help="GUI を起動")
    common(sp)
    sp.set_defaults(func=cmd_gui)
    return p


# ---------------------------------------------------------------- 表示補助

def _width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _table(rows: list[list[str]]) -> str:
    widths = [max(_width(r[i]) for r in rows) for i in range(len(rows[0]))]
    lines = []
    for r in rows:
        lines.append("  ".join(_pad(c, w) for c, w in zip(r[:-1], widths)) + "  " + r[-1])
    return "\n".join(lines)


def _hms(sec: float | None) -> str:
    if sec is None:
        return "--:--:--"
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{sec % 3600 // 60:02d}:{sec % 60:02d}"


class ProgressPrinter:
    """進捗を stderr に表示する。TTY では行更新、そうでなければ 10 秒ごとに 1 行。"""

    def __init__(self) -> None:
        self.tty = sys.stderr.isatty()
        self._last_line = 0.0
        self._shown = False

    def __call__(self, p: Progress) -> None:
        if p.total_frames:
            ratio = min(1.0, p.frames_done / p.total_frames)
            bar = "#" * int(ratio * 10) + "-" * (10 - int(ratio * 10))
            line = (f"[{bar}] {int(ratio * 100):3d}%  {p.frames_done}/{p.total_frames} frames  "
                    f"ETA {_hms(p.eta_sec)}")
        else:
            line = f"frames {p.frames_done}  elapsed {_hms(p.elapsed_sec)}"
        if self.tty:
            sys.stderr.write("\r" + line + "\033[K")
            sys.stderr.flush()
            self._shown = True
        elif p.done or time.monotonic() - self._last_line >= 10:
            print(line, file=sys.stderr, flush=True)
            self._last_line = time.monotonic()

    def finish(self) -> None:
        if self.tty and self._shown:
            sys.stderr.write("\n")
            sys.stderr.flush()


def _make_manager(args) -> tuple[object, PluginManager]:
    config = load_config(getattr(args, "config", None))
    return config, PluginManager(default_search_dirs(config.extra_dirs))


# ---------------------------------------------------------------- コマンド

def cmd_list(args) -> int:
    _, manager = _make_manager(args)
    infos, issues = manager.discover_all()
    if infos:
        rows = [["名前", "種別", "バージョン", "説明"]]
        rows += [[i.name, i.kind.value, i.version, i.description] for i in infos]
        print(_table(rows))
    else:
        print("利用可能なプラグインはありません")
    if issues:
        print("\nロード失敗:")
        for i in issues:
            print(f"  {i.path}: {i.reason}")
    return 0


def cmd_info(args) -> int:
    _, manager = _make_manager(args)
    i = manager.resolve(args.plugin)
    print(f"{i.name} ({i.kind.value}) v{i.version}")
    if i.description:
        print(f"  {i.description}")
    if not i.params:
        print("パラメータ: なし")
        return 0
    print("パラメータ:")
    for s in i.params:
        rng = ""
        if s.min is not None or s.max is not None:
            rng = f"  範囲 {s.min if s.min is not None else ''}〜{s.max if s.max is not None else ''}"
        if s.choices:
            rng = f"  選択肢 {', '.join(s.choices)}"
        print(f"  {s.name}  {s.type.value}  既定値 {s.default}{rng}")
        if s.description:
            print(f"      {s.description}")
    return 0


def cmd_profiles(args) -> int:
    rows = [["名前", "拡張子", "説明"]]
    rows += [[p.name, p.extension, p.description] for p in default_registry().all()]
    print(_table(rows))
    return 0


def cmd_apply(args) -> int:
    config, manager = _make_manager(args)
    service = ProcessingService(config, manager, default_registry())
    specs = tuple(parse_plugin_spec(t) for t in args.plugin)
    req = ProcessRequest(
        input_path=args.input,
        plugins=specs,
        output_path=args.output,
        profile=args.profile or config.profile,
        quality=args.quality or config.quality,
        keep_audio=not args.no_audio,
        overwrite=args.overwrite,
        seed=args.seed,
    )
    printer = None if args.quiet else ProgressPrinter()
    cancel = threading.Event()
    try:
        result = service.run(req, on_progress=printer, cancel=cancel)
    finally:
        if printer:
            printer.finish()
    print(f"完了: {result.output_path} ({result.frames_processed} フレーム, {_hms(result.elapsed_sec)})")
    return 0


def cmd_gui(args) -> int:
    from .gui import main as gui_main  # tkinter が無い環境でも CLI を使えるよう、ここで初めて import
    return gui_main(args.config, args.verbose)


# ---------------------------------------------------------------- エントリポイント

def _report(e: MovieFilterError, verbose: bool) -> None:
    print(f"エラー [{e.code}]: {e.message}", file=sys.stderr)
    if e.detail:
        print(f"詳細: {e.detail}", file=sys.stderr)
    if verbose:
        traceback.print_exception(e, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    argv = sys.argv[1:] if argv is None else argv
    verbose = "-v" in argv or "--verbose" in argv
    try:
        args = build_parser().parse_args(argv)
        setup_logging(args.verbose)
        return args.func(args)
    except SystemExit as e:  # --help / --version
        return e.code if isinstance(e.code, int) else 0
    except MovieFilterError as e:
        _report(e, verbose)
        return e.exit_code
    except KeyboardInterrupt:
        print("エラー [E_CANCELLED]: 中断しました", file=sys.stderr)
        return 130
    except Exception as e:  # 想定外の例外
        print(f"エラー [E_INTERNAL]: 予期しないエラーが発生しました: {type(e).__name__}: {e}", file=sys.stderr)
        if verbose:
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
