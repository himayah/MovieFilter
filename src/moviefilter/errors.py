"""例外とエラーコード。エラーコードと終了コードの対応は _EXIT_CODES が唯一の正(詳細設計書 §7.6)。"""
from __future__ import annotations

_EXIT_CODES: dict[str, int] = {
    # 2: 引数・設定・入出力・プラグイン指定の誤り(処理開始前に検出)
    **dict.fromkeys(
        [
            "E_USAGE",
            "E_INPUT_NOT_FOUND",
            "E_NO_VIDEO_STREAM",
            "E_PROBE_FAILED",
            "E_OUTPUT_EXISTS",
            "E_OUTPUT_SAME_AS_INPUT",
            "E_OUTPUT_EXT_MISMATCH",
            "E_OUTPUT_DIR_INVALID",
            "E_PROFILE_UNKNOWN",
            "E_PLUGIN_NOT_FOUND",
            "E_PARAM_UNKNOWN",
            "E_PARAM_INVALID",
            "E_PARAM_RANGE",
            "E_PLUGIN_INIT",
            "E_PLUGIN_KIND_UNSUPPORTED",
            "E_TEMPORAL_UNSUPPORTED",
            "E_CONFIG_INVALID",
            "E_FFMPEG_NOT_FOUND",
        ],
        2,
    ),
    # 3: 処理中のエラー
    **dict.fromkeys(
        [
            "E_DECODE_FAILED",
            "E_ENCODE_FAILED",
            "E_NO_FRAMES",
            "E_PLUGIN_BAD_OUTPUT",
            "E_PLUGIN_RUNTIME",
        ],
        3,
    ),
    "E_CANCELLED": 130,
}


class MovieFilterError(Exception):
    """全エラーの基底。`str(e)` は "[E_XXXX] メッセージ" の形式。"""

    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail

    @property
    def exit_code(self) -> int:
        return _EXIT_CODES.get(self.code, 1)

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class UsageError(MovieFilterError):
    pass


class InputError(MovieFilterError):
    pass


class OutputError(MovieFilterError):
    pass


class PluginError(MovieFilterError):
    pass


class ToolError(MovieFilterError):
    pass


class ConfigError(MovieFilterError):
    pass


class CancelledError(MovieFilterError):
    def __init__(self, message: str = "中断しました"):
        super().__init__("E_CANCELLED", message)


class PluginRuntimeError(PluginError):
    """プラグインの process が送出した例外を、プラグイン名・フレーム番号つきで包む。"""

    def __init__(self, plugin: str, frame_index: int, cause: BaseException):
        super().__init__(
            "E_PLUGIN_RUNTIME",
            f"プラグイン {plugin} がフレーム {frame_index} で失敗しました",
            f"{type(cause).__name__}: {cause}",
        )
        self.plugin = plugin
        self.frame_index = frame_index
        self.cause = cause
