"""直播监控：开播探测 + 流录制（抖音 / YouTube）。"""
from .probe import LiveInfo, probe_douyin_live, probe_youtube_live
from .record import (
    extract_cover_frame_sync,
    live_cover_path_for,
    record_douyin_live,
    record_live_stream,
)

__all__ = [
    "LiveInfo",
    "probe_douyin_live",
    "probe_youtube_live",
    "record_douyin_live",
    "record_live_stream",
    "extract_cover_frame_sync",
    "live_cover_path_for",
]
