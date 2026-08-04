"""把 yt-dlp 条目转成统一的 Aweme / MediaItem。"""
from __future__ import annotations

from typing import Any, Optional

from ..douyin.extract import Aweme, MediaItem, safe_title  # noqa: F401

from .resolve import video_watch_url


def _to_int(v) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def parse_yt_entry(info: dict, *, quality: str = "highest") -> Optional[Aweme]:
    """从 yt-dlp extract_info(单条或 playlist entry) 构造 Aweme。
    媒体存 watch URL,实际下载走 yt-dlp(直链易过期)。"""
    if not isinstance(info, dict):
        return None
    # playlist flat entry 常用 id/url/title
    vid = (info.get("id") or info.get("url") or "").strip()
    if not vid or len(vid) < 6:
        return None
    # 有些 entry 的 url 是完整链接
    if "watch?v=" in vid or "youtu.be/" in vid:
        from .resolve import resolve_youtube_video_id
        vid = resolve_youtube_video_id(vid) or vid
    title = (info.get("title") or info.get("fulltitle") or "").strip()
    author = (info.get("uploader") or info.get("channel")
              or info.get("creator") or "youtube").strip()
    ts = _to_int(info.get("timestamp") or info.get("release_timestamp"))
    if not ts:
        # upload_date: YYYYMMDD
        ud = str(info.get("upload_date") or "")
        if len(ud) == 8 and ud.isdigit():
            import datetime as _dt
            try:
                ts = int(_dt.datetime(int(ud[:4]), int(ud[4:6]), int(ud[6:8])).timestamp())
            except Exception:
                ts = 0
    cover = (info.get("thumbnail") or "")
    if not cover:
        thumbs = info.get("thumbnails") or []
        if isinstance(thumbs, list) and thumbs:
            cover = (thumbs[-1] or {}).get("url") or ""
    watch = video_watch_url(vid) if len(vid) == 11 else (info.get("webpage_url") or video_watch_url(vid))
    aw = Aweme(
        aweme_id=vid,
        desc=title or vid,
        create_time=ts,
        author_name=author,
        media_type="video",
        medias=[MediaItem(url=watch, kind="video", ext="mp4", index=0)],
        cover=cover or "",
        quality_label=quality or "",
        like_count=_to_int(info.get("like_count")),
        comment_count=_to_int(info.get("comment_count")),
        duration=_to_int(info.get("duration")),
        avatar="",
        platform="youtube",
    )
    return aw


def parse_yt_channel_meta(info: dict) -> dict:
    """从频道页 extract_info 顶层拿昵称/头像等。"""
    if not isinstance(info, dict):
        return {}
    # playlist 顶层
    ch_id = info.get("channel_id") or info.get("id") or ""
    nick = (info.get("channel") or info.get("uploader")
            or info.get("title") or "").strip()
    # 去掉 " - Videos" 之类后缀
    for suf in (" - Videos", " - 视频", " Videos"):
        if nick.endswith(suf):
            nick = nick[: -len(suf)].strip()
    avatar = ""
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        avatar = (thumbs[-1] or {}).get("url") or ""
    return {
        "nickname": nick,
        "sec_uid": ch_id if str(ch_id).startswith("UC") else "",
        "douyin_id": info.get("uploader_id") or info.get("channel_id") or "",
        "avatar": avatar,
        "follower_count": _to_int(info.get("channel_follower_count")),
        "aweme_count": _to_int(info.get("playlist_count")),
    }
