"""出力プロファイル(詳細設計書 §5.3)。形式の追加は OutputProfile の実装と登録だけで行う。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Literal

from .errors import OutputError
from .models import MediaInfo

AudioMode = Literal["copy", "reencode", "none"]


@dataclass(frozen=True)
class AudioPolicy:
    copy_codecs: frozenset[str]
    fallback_codec: str
    fallback_bitrate: str


class OutputProfile(ABC):
    name: ClassVar[str]
    description: ClassVar[str] = ""
    extension: ClassVar[str]
    audio_policy: ClassVar[AudioPolicy]
    quality_levels: ClassVar[dict[str, object]]

    @abstractmethod
    def build_encode_args(self, media: MediaInfo, quality: str, audio_mode: AudioMode) -> list[str]:
        """出力側のオプション(入力指定と出力パスは含まない)を返す。

        入力 0 は rawvideo のパイプ(加工後の映像)、入力 1 は元の動画ファイル。
        """

    def decide_audio_mode(self, media: MediaInfo, keep_audio: bool) -> AudioMode:
        if not keep_audio or media.audio is None:
            return "none"
        if media.audio.codec in self.audio_policy.copy_codecs:
            return "copy"
        return "reencode"


class Mp4H264Profile(OutputProfile):
    name = "mp4-h264"
    description = "MP4 コンテナ / H.264 映像"
    extension = ".mp4"
    audio_policy = AudioPolicy(
        copy_codecs=frozenset({"aac", "mp3", "ac3", "eac3", "alac"}),
        fallback_codec="aac",
        fallback_bitrate="192k",
    )
    quality_levels = {"low": 28, "medium": 23, "high": 18}

    def build_encode_args(self, media: MediaInfo, quality: str, audio_mode: AudioMode) -> list[str]:
        crf = self.quality_levels[quality]
        args = ["-map", "0:v:0"]
        if audio_mode != "none":
            args += ["-map", f"1:{media.audio_stream_index}"]
        args += ["-map_metadata:g", "1", "-map_chapters", "-1"]

        # RGB→YUV の変換行列と色情報を BT.709 で明示する(指定しないと色味がずれる)
        vf = []
        if media.video.width % 2 or media.video.height % 2:
            vf.append("pad=ceil(iw/2)*2:ceil(ih/2)*2")
        vf.append("scale=out_color_matrix=bt709:out_range=tv")
        args += ["-vf", ",".join(vf)]
        args += ["-c:v", "libx264", "-crf", str(crf), "-preset", "medium", "-pix_fmt", "yuv420p"]
        args += ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
                 "-color_range", "tv"]
        if audio_mode == "copy":
            args += ["-c:a", "copy"]
        elif audio_mode == "reencode":
            p = self.audio_policy
            args += ["-c:a", p.fallback_codec, "-b:a", p.fallback_bitrate]
        else:
            args += ["-an"]
        args += ["-movflags", "+faststart"]
        return args


class ProfileRegistry:
    def __init__(self) -> None:
        self._profiles: dict[str, OutputProfile] = {}

    def register(self, profile: OutputProfile) -> None:
        self._profiles[profile.name] = profile

    def get(self, name: str) -> OutputProfile:
        try:
            return self._profiles[name]
        except KeyError:
            raise OutputError("E_PROFILE_UNKNOWN", f"不明な出力形式です: {name}",
                              f"指定可能: {', '.join(self.names())}") from None

    def names(self) -> list[str]:
        return sorted(self._profiles)

    def all(self) -> list[OutputProfile]:
        return [self._profiles[n] for n in self.names()]


def default_registry() -> ProfileRegistry:
    reg = ProfileRegistry()
    reg.register(Mp4H264Profile())
    return reg
