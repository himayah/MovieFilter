from pathlib import Path

import pytest

from moviefilter.errors import OutputError
from moviefilter.models import AudioInfo, MediaInfo
from moviefilter.plugin_api import VideoInfo
from moviefilter.profiles import Mp4H264Profile, default_registry


def media(w=160, h=120, codec="aac"):
    audio = AudioInfo(codec, 44100, 2, 0.0) if codec else None
    return MediaInfo(Path("in.mkv"), "matroska", VideoInfo(w, h, 25, 1, 50, 2.0), "h264", "cfr", 0.0, audio,
                     video_stream_index=0, audio_stream_index=1 if codec else None)


P = Mp4H264Profile()


@pytest.mark.parametrize("codec,keep,mode", [
    ("aac", True, "copy"), ("mp3", True, "copy"), ("ac3", True, "copy"),
    ("opus", True, "reencode"), ("vorbis", True, "reencode"), ("flac", True, "reencode"),
    ("aac", False, "none"), (None, True, "none"),
])
def test_audio_mode(codec, keep, mode):
    assert P.decide_audio_mode(media(codec=codec), keep) == mode


def test_args_audio_copy():
    a = P.build_encode_args(media(), "medium", "copy")
    assert a[a.index("-c:a") + 1] == "copy" and "1:1" in a
    assert a[a.index("-crf") + 1] == "23"
    assert "-map_chapters" in a and a[a.index("-map_chapters") + 1] == "-1"
    assert "-shortest" not in a
    vf = a[a.index("-vf") + 1]
    assert "pad" not in vf and "out_color_matrix=bt709" in vf
    assert a[a.index("-colorspace") + 1] == "bt709"


def test_args_reencode_and_none():
    a = P.build_encode_args(media(codec="opus"), "high", "reencode")
    assert a[a.index("-c:a") + 1] == "aac" and a[a.index("-b:a") + 1] == "192k"
    assert a[a.index("-crf") + 1] == "18"
    n = P.build_encode_args(media(codec=None), "low", "none")
    assert "-an" in n and "-c:a" not in n and not any(x.startswith("1:") for x in n)
    assert n[n.index("-crf") + 1] == "28"
    assert "-map_metadata:g" in n          # 音声なしでもメタデータは引き継ぐ(入力 1 は常に存在する)


@pytest.mark.parametrize("w,h,pad", [(160, 120, False), (161, 120, True), (160, 121, True), (321, 241, True)])
def test_pad_only_for_odd_sizes(w, h, pad):
    a = P.build_encode_args(media(w, h), "medium", "copy")
    assert ("pad=ceil(iw/2)*2:ceil(ih/2)*2" in a[a.index("-vf") + 1]) is pad


def test_registry():
    reg = default_registry()
    assert reg.names() == ["mp4-h264"] and reg.get("mp4-h264").extension == ".mp4"
    with pytest.raises(OutputError) as e:
        reg.get("webm")
    assert e.value.code == "E_PROFILE_UNKNOWN"
