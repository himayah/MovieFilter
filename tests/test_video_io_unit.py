from pathlib import Path

import pytest

from moviefilter.errors import InputError
from moviefilter.models import AudioInfo, MediaInfo
from moviefilter.plugin_api import VideoInfo
from moviefilter.profiles import Mp4H264Profile
from moviefilter.video_io import audio_offset, build_decode_args, build_encode_args, parse_probe


def stream(**kw):
    s = {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 160, "height": 120,
         "avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001", "nb_frames": "90", "start_time": "0.000000",
         "duration": "3.003000", "disposition": {"attached_pic": 0}}
    s.update(kw)
    return s


AUDIO = {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "44100", "channels": 2,
         "start_time": "0.000000"}


def probe(*streams, fmt=None):
    return parse_probe({"streams": list(streams), "format": fmt or {"format_name": "mov,mp4", "duration": "3.0"}},
                       Path("in.mp4"))


def test_basic_cfr():
    m = probe(stream(), AUDIO)
    v = m.video
    assert (v.fps_num, v.fps_den, v.total_frames) == (30000, 1001, 90) and m.source_fps_mode == "cfr"
    assert m.audio.codec == "aac" and m.audio_stream_index == 1 and m.video_stream_index == 0


def test_attached_pic_is_not_video():
    with pytest.raises(InputError) as e:
        probe(stream(disposition={"attached_pic": 1}), {**AUDIO, "index": 0})
    assert e.value.code == "E_NO_VIDEO_STREAM"
    m = probe({**AUDIO, "index": 0}, stream(index=1, disposition={"attached_pic": 0}))
    assert m.video_stream_index == 1


def test_no_video():
    with pytest.raises(InputError) as e:
        probe(AUDIO)
    assert e.value.code == "E_NO_VIDEO_STREAM"


@pytest.mark.parametrize("sd,tags,swapped", [
    ([{"rotation": 90}], None, True), ([{"rotation": -90}], None, True), ([{"rotation": 270}], None, True),
    ([{"rotation": 180}], None, False), (None, {"rotate": "90"}, True), (None, None, False),
])
def test_rotation_swaps_dimensions(sd, tags, swapped):
    s = stream()
    if sd:
        s["side_data_list"] = sd
    if tags:
        s["tags"] = tags
    v = probe(s).video
    assert (v.width, v.height) == ((120, 160) if swapped else (160, 120))


def test_vfr_detection_and_frame_estimate():
    m = probe(stream(avg_frame_rate="300/29", r_frame_rate="10/1", nb_frames="30", duration="3.0"))
    assert m.source_fps_mode == "vfr"
    assert m.video.total_frames == round(3.0 * 300 / 29)   # nb_frames は VFR では使わない


def test_unknown_frame_count_and_duration():
    m = probe(stream(nb_frames="N/A", duration="N/A"), fmt={"format_name": "x"})
    assert m.video.total_frames is None


def test_frame_rate_missing():
    with pytest.raises(InputError):
        probe(stream(avg_frame_rate="0/0", r_frame_rate="0/0"))


def test_start_times():
    m = probe(stream(start_time="0.1"), {**AUDIO, "start_time": "0.6"})
    assert m.video_start_sec == 0.1 and m.audio.start_sec == 0.6


def media(audio_start=0.0, video_start=0.0, fps=(25, 1)):
    return MediaInfo(Path("in.mp4"), "mp4", VideoInfo(160, 120, fps[0], fps[1], 50, 2.0), "h264", "cfr",
                     video_start, AudioInfo("aac", 44100, 2, audio_start), 0, 1)


def test_audio_offset_only_when_video_start_is_beyond_one_frame():
    # 音声だけが遅れて始まる入力は、ファイル内のタイムスタンプがそのまま保たれるので補正しない
    assert audio_offset(media(audio_start=0.5), "copy") is None
    assert audio_offset(media(), "copy") is None
    assert audio_offset(media(video_start=0.03), "copy") is None        # 1 フレーム(0.04s)以内は補正しない
    # 映像が遅れて始まる入力は、パイプ経由で 0 から始まる映像に合わせ、音声を映像の開始時刻ぶん戻す
    assert audio_offset(media(video_start=0.5), "copy") == pytest.approx(-0.5)
    assert audio_offset(media(video_start=0.5), "none") is None


def test_encode_args_structure():
    cmd = build_encode_args("ffmpeg", media(video_start=0.5), Mp4H264Profile(), "medium", "copy", Path("t.mp4"))
    assert cmd[cmd.index("-framerate") + 1] == "25/1"
    assert cmd.index("-itsoffset") < cmd.index("-i", cmd.index("-itsoffset"))     # 音声入力の直前
    assert cmd.count("-i") == 2 and cmd[-1] == "t.mp4" and "-shortest" not in cmd
    assert cmd[cmd.index("-s") + 1] == "160x120"


def test_encode_args_use_exact_rational_fps():
    cmd = build_encode_args("ffmpeg", media(fps=(30000, 1001)), Mp4H264Profile(), "medium", "none", Path("t.mp4"))
    assert cmd[cmd.index("-framerate") + 1] == "30000/1001" and "-itsoffset" not in cmd


def test_decode_args():
    m = media()
    assert "-vf" not in build_decode_args("ffmpeg", m)
    vfr = MediaInfo(m.path, m.container, m.video, "h264", "vfr", 0.0, None, 2, None)
    a = build_decode_args("ffmpeg", vfr)
    assert a[a.index("-vf") + 1] == "fps=25/1" and a[a.index("-map") + 1] == "0:2"
