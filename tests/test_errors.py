from moviefilter.errors import (CancelledError, MovieFilterError, PluginRuntimeError, _EXIT_CODES)

# 詳細設計書 §7.6 の対応表
EXIT_2 = ["E_USAGE", "E_INPUT_NOT_FOUND", "E_NO_VIDEO_STREAM", "E_PROBE_FAILED", "E_OUTPUT_EXISTS",
          "E_OUTPUT_SAME_AS_INPUT", "E_OUTPUT_EXT_MISMATCH", "E_OUTPUT_DIR_INVALID", "E_PROFILE_UNKNOWN",
          "E_PLUGIN_NOT_FOUND", "E_PARAM_UNKNOWN", "E_PARAM_INVALID", "E_PARAM_RANGE", "E_PLUGIN_INIT",
          "E_PLUGIN_KIND_UNSUPPORTED", "E_TEMPORAL_UNSUPPORTED", "E_CONFIG_INVALID", "E_FFMPEG_NOT_FOUND"]
EXIT_3 = ["E_DECODE_FAILED", "E_ENCODE_FAILED", "E_NO_FRAMES", "E_PLUGIN_BAD_OUTPUT", "E_PLUGIN_RUNTIME"]


def test_exit_code_table():
    for code in EXIT_2:
        assert MovieFilterError(code, "x").exit_code == 2, code
    for code in EXIT_3:
        assert MovieFilterError(code, "x").exit_code == 3, code
    assert MovieFilterError("E_CANCELLED", "x").exit_code == 130
    assert MovieFilterError("E_SOMETHING_ELSE", "x").exit_code == 1
    assert set(_EXIT_CODES) == set(EXIT_2 + EXIT_3 + ["E_CANCELLED"])


def test_str_contains_code():
    assert str(MovieFilterError("E_PARAM_RANGE", "範囲外")) == "[E_PARAM_RANGE] 範囲外"
    assert CancelledError().exit_code == 130


def test_plugin_runtime_error():
    e = PluginRuntimeError("brighten", 342, ValueError("boom"))
    assert e.code == "E_PLUGIN_RUNTIME" and "342" in e.message and "brighten" in e.message
    assert "boom" in e.detail and e.exit_code == 3
