"""从 YouTube 链接解析 channel_id / video_id / @handle。

支持:
  - https://www.youtube.com/watch?v=VIDEO_ID
  - https://youtu.be/VIDEO_ID
  - https://www.youtube.com/channel/UC...
  - https://www.youtube.com/@handle
  - https://www.youtube.com/c/Name / /user/Name
  - 纯 11 位 video id / UC 开头 channel id / @handle
"""
from __future__ import annotations

import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_RE = re.compile(r"^@[\w.-]{2,}$")
_WATCH_RE = re.compile(r"[?&]v=([A-Za-z0-9_-]{11})")
_SHORT_RE = re.compile(r"youtu\.be/([A-Za-z0-9_-]{11})")
_EMBED_RE = re.compile(r"/embed/([A-Za-z0-9_-]{11})")
_SHORTS_RE = re.compile(r"/shorts/([A-Za-z0-9_-]{11})")
_CHANNEL_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")
_HANDLE_PATH_RE = re.compile(r"/(@[\w.-]{2,})")
_USER_PATH_RE = re.compile(r"/(?:c|user)/([\w.-]{2,})")


def looks_like_video(text: str) -> bool:
    text = (text or "").strip()
    if _VIDEO_ID_RE.match(text):
        return True
    return bool(_WATCH_RE.search(text) or _SHORT_RE.search(text)
                or _EMBED_RE.search(text) or _SHORTS_RE.search(text))


def resolve_youtube_video_id(text: str) -> Optional[str]:
    text = (text or "").strip()
    if _VIDEO_ID_RE.match(text):
        return text
    for rx in (_WATCH_RE, _SHORT_RE, _EMBED_RE, _SHORTS_RE):
        m = rx.search(text)
        if m:
            return m.group(1)
    try:
        q = parse_qs(urlparse(text).query)
        v = (q.get("v") or [None])[0]
        if v and _VIDEO_ID_RE.match(v):
            return v
    except Exception:
        pass
    return None


def resolve_youtube_channel_ref(text: str) -> Optional[str]:
    """返回可用于 yt-dlp 的频道标识: UC... / @handle / 规范化频道 URL。"""
    text = (text or "").strip()
    if not text:
        return None
    if _CHANNEL_ID_RE.match(text):
        return text
    if _HANDLE_RE.match(text):
        return text
    m = _CHANNEL_RE.search(text)
    if m:
        return m.group(1)
    m = _HANDLE_PATH_RE.search(text)
    if m:
        return m.group(1)
    m = _USER_PATH_RE.search(text)
    if m:
        # /c/Name /user/Name — 交给 yt-dlp 解析,保留成完整 URL
        return f"https://www.youtube.com/{'c' if '/c/' in text else 'user'}/{m.group(1)}"
    # 已经是 youtube 频道主页
    if "youtube.com/" in text and not looks_like_video(text):
        return text.split("&")[0].rstrip("/")
    return None


def channel_feed_url(channel_ref: str) -> str:
    """把 channel_ref 变成 yt-dlp 可拉列表的 URL(/videos)。"""
    ref = (channel_ref or "").strip()
    if not ref:
        return ""
    if ref.startswith("http"):
        base = ref.rstrip("/")
        if base.endswith("/videos") or base.endswith("/streams") or base.endswith("/shorts"):
            return base
        return base + "/videos"
    if ref.startswith("UC"):
        return f"https://www.youtube.com/channel/{ref}/videos"
    if ref.startswith("@"):
        return f"https://www.youtube.com/{ref}/videos"
    return f"https://www.youtube.com/{ref}/videos"


def video_watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
