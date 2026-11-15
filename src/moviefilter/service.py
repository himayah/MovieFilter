"""処理サービス層(詳細設計書 §5.5)。UI(CLI/将来のGUI)はここだけを呼ぶ。"""
from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .config import QUALITIES, Config
from .errors import CancelledError, InputError, OutputError, PluginError, UsageError
from .models import MediaInfo, ProcessRequest, ProcessResult
from .pipeline import Frame, VideoStageChain
from .plugin_manager import PluginManager
from .profiles import ProfileRegistry
from .video_io import Decoder, Encoder, Prober, audio_offset, resolve_tools

log = logging.getLogger("moviefilter.service")

PROGRESS_INTERVAL_SEC = 0.2
SEED_LIMIT = 2**32


MAX_DEFAULT_STEM = 120  # 既定の出力名がこの長さを超えるときは、フィルタ名を並べず "_filtered" にする


def default_output_path(input_path: Path, plugin_names: Sequence[str], ext: str) -> Path:
    """既定の出力パス: 入力と同じフォルダの `<入力名>_<フィルタ名>[_<フィルタ名>...]<拡張子>`(適用順)。

    フィルタ名が無い、または名前が長くなりすぎるときは `<入力名>_filtered<拡張子>` にする。
    CLI と GUI が同じ規則を使う(GUI は入力・適用リストが変わるたびにこれで既定名を更新する)。
    """
    suffix = "_".join(plugin_names)
    if not suffix or len(input_path.stem) + len(suffix) + 1 > MAX_DEFAULT_STEM:
        suffix = "filtered"
    return input_path.with_name(f"{input_path.stem}_{suffix}{ext}")


@dataclass(frozen=True)
class Progress:
    frames_done: int
    total_frames: int | None
    elapsed_sec: float
    eta_sec: float | None
    done: bool = False  # 全フレームの処理が完了したときの最後の通知


class ProcessingService:
    def __init__(self, config: Config, plugin_manager: PluginManager, profiles: ProfileRegistry):
        self.config = config
        self.plugins = plugin_manager
        self.profiles = profiles

    # ------------------------------------------------------------ 事前検証
    def _resolve_output(self, req: ProcessRequest, ext: str) -> Path:
        inp = req.input_path
        if req.output_path is not None:
            out = req.output_path
            if out.suffix.lower() != ext:
                raise OutputError("E_OUTPUT_EXT_MISMATCH",
                                  f"出力の拡張子が形式と一致しません ({ext} が必要): {out}")
        else:
            out = default_output_path(inp, [p.name for p in req.plugins], ext)

        parent = out.resolve().parent
        if not parent.is_dir() or not os.access(parent, os.W_OK):
            raise OutputError("E_OUTPUT_DIR_INVALID", f"出力先フォルダが存在しないか、書き込めません: {parent}")
        if out.exists() and os.path.samefile(out, inp) or out.resolve() == inp.resolve():
            raise OutputError("E_OUTPUT_SAME_AS_INPUT", f"入力と出力が同じファイルです: {out}")
        if out.exists() and not req.overwrite:
            raise OutputError("E_OUTPUT_EXISTS", f"出力ファイルが既に存在します: {out}",
                              "上書きするには --overwrite を指定してください")
        return out

    def validate_request(self, req: ProcessRequest):
        """処理を始めずに、入力・出力・品質・シード・プラグイン指定の事前検証だけを行う(GUI も利用する)。

        戻り値は (出力プロファイル, 確定した出力パス)。
        """
        inp = req.input_path
        if not inp.is_file() or not os.access(inp, os.R_OK):
            raise InputError("E_INPUT_NOT_FOUND", f"入力ファイルが見つかりません: {inp}")
        if req.quality not in QUALITIES:
            raise OutputError("E_PARAM_INVALID", f"品質の指定が不正です: {req.quality}",
                              f"指定可能: {', '.join(QUALITIES)}")
        if not (isinstance(req.seed, int) and 0 <= req.seed < SEED_LIMIT):
            raise PluginError("E_PARAM_INVALID", f"seed は 0 以上 {SEED_LIMIT - 1} 以下の整数で指定してください: {req.seed}")
        if not req.plugins:
            raise UsageError("E_USAGE", "プラグインを1つ以上指定してください")
        profile = self.profiles.get(req.profile)
        out = self._resolve_output(req, profile.extension)
        return profile, out

    # ------------------------------------------------------------ 実行
    def run(self, req: ProcessRequest,
            on_progress: Callable[[Progress], None] | None = None,
            cancel: threading.Event | None = None) -> ProcessResult:
        profile, output_path = self.validate_request(req)
        tools = resolve_tools(self.config.ffmpeg, self.config.ffprobe)
        plugins = [self.plugins.instantiate(spec) for spec in req.plugins]
        media: MediaInfo = Prober(tools.ffprobe).probe(req.input_path)

        audio_mode = profile.decide_audio_mode(media, req.keep_audio)
        log.info("入力: %dx%d %d/%d fps %s フレーム, 音声: %s, 適用順: %s, seed: %d",
                 media.video.width, media.video.height, media.video.fps_num, media.video.fps_den,
                 media.video.total_frames, audio_mode, " → ".join(p.name for p in plugins), req.seed)
        offset = audio_offset(media, audio_mode)
        if offset is not None:
            log.info("映像・音声の開始時刻のずれを補正しました (%.3f 秒)", offset)
        if media.source_fps_mode == "vfr":
            log.warning("可変フレームレートの動画です。固定フレームレートに変換して出力します")

        tmp = output_path.with_name(f".{output_path.stem}.{os.getpid()}.tmp{output_path.suffix}")
        chain = VideoStageChain(plugins, media.video, req.seed)
        start = time.monotonic()
        last_report = 0.0
        done = 0
        ok = False
        try:
            chain.setup()
            with Decoder(tools.ffmpeg, media) as dec, \
                    Encoder(tools.ffmpeg, media, profile, req.quality, audio_mode, tmp) as enc:
                fps_num, fps_den = media.video.fps_num, media.video.fps_den
                for i, pixels in enumerate(dec.frames()):
                    if cancel is not None and cancel.is_set():
                        raise CancelledError()
                    out = chain.run(Frame(pixels, i, i * fps_den / fps_num))
                    enc.write(out.pixels)
                    done = i + 1
                    now = time.monotonic()
                    if on_progress and now - last_report >= PROGRESS_INTERVAL_SEC:
                        last_report = now
                        on_progress(self._progress(done, media, now - start))
                enc.finish()
            os.replace(tmp, output_path)
            ok = True
        finally:
            chain.teardown()
            if not ok and tmp.exists():
                try:
                    tmp.unlink()
                except OSError as e:
                    log.warning("一時ファイルを削除できません: %s (%s)", tmp, e)
        elapsed = time.monotonic() - start
        if on_progress:
            on_progress(Progress(done, done, elapsed, 0.0, True))
        return ProcessResult(output_path, done, elapsed)

    @staticmethod
    def _progress(done: int, media: MediaInfo, elapsed: float) -> Progress:
        total = media.video.total_frames
        eta = None
        if total and done:
            eta = max(0.0, elapsed / done * (total - done))
        return Progress(done, total, elapsed, eta)
