"""用已登录 TikTok 账号的持久 Chromium 打开作品/短剧页,拦截播放直链。

yt-dlp 走公开 API 时经常被地区/IP 风控拦住;浏览器带登录态和账号代理打开原页,
从 itemStruct / 接口 JSON / 播放器请求里取出 playAddr。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

_READ_PAGE_STATE_JS = """() => {
  const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
  if (el && el.textContent) {
    try { return JSON.parse(el.textContent); } catch (e) {}
  }
  if (window.SIGI_STATE) return window.SIGI_STATE;
  if (window.__NEXT_DATA__) return window.__NEXT_DATA__;
  return null;
}"""

_ITEM_ID_RE = re.compile(r"^\d{8,25}$")
_HREF_VIDEO_RE = re.compile(r"(?:/@([^/]+))?/(?:video|photo)/(\d{8,25})")
_HREF_DRAMA_RE = re.compile(r"/shortdrama/episode/(\d{8,25})", re.I)
_NOISE_FEED_TOKS = (
    "/api/recommend/", "/api/related/", "/api/challenge/",
    "/api/comment/", "/api/search/", "/api/inbox", "/api/notice",
    "/share/item_list", "/api/foryou", "/api/story/",
    "/api/ad/", "/api/promote",
)
_PROFILE_FEED_TOKS = (
    "/api/post/item_list", "/api/repost/item_list",
    "/api/mix/item_list", "/api/playlist", "/api/user/playlist",
    "/api/creator/", "/api/drama/", "shortdrama", "drama_list",
)
_FEED_LIST_KEYS = (
    "itemList", "item_list", "aweme_list", "awemeList",
    "dramaList", "drama_list", "itemStructList",
)

_HARVEST_PROFILE_JS = """(want) => {
  const handle = String(want || '').replace(/^@/, '').toLowerCase();
  const esc = handle.replace(/[.*+?^${}()|[\\]\\\\]/g, '\\\\$&');
  const mine = handle ? new RegExp('/@' + esc + '/(?:video|photo)/\\\\d{8,}', 'i') : null;
  const drama = /\\/shortdrama\\/episode\\/\\d{8,}/i;
  const anyVid = /\\/(?:video|photo)\\/\\d{8,}/;
  const grid = document.querySelector('[data-e2e="user-post-item-list"]')
    || document.querySelector('[data-e2e="user-page"]');
  const hrefs = [];
  const seen = new Set();
  const add = (raw, fromGrid) => {
    const h = String(raw || '').split('?')[0];
    if (!h || seen.has(h)) return;
    const own = !!(mine && mine.test(h));
    if (own || (fromGrid && (drama.test(h) || anyVid.test(h)))) {
      seen.add(h);
      hrefs.push(h);
    }
  };
  const scan = (root, fromGrid) => {
    if (!root || !root.querySelectorAll) return;
    for (const a of root.querySelectorAll('a[href], [href]')) add(a.href || a.getAttribute('href'), fromGrid);
    for (const el of root.querySelectorAll('[data-e2e="user-post-item"], [class*="DivItemContainer"]')) {
      const a = el.querySelector('a[href], [href]');
      if (a) add(a.href || a.getAttribute('href'), true);
    }
  };
  scan(grid, true);
  if (!grid) scan(document.querySelector('main') || document.body, true);
  scan(document, false);
  const tabs = [];
  for (const el of document.querySelectorAll('[data-e2e],[role="tab"]')) {
    const key = el.getAttribute('data-e2e') || el.getAttribute('role') || '';
    const text = (el.textContent || '').trim().slice(0, 40);
    if (key || text) tabs.push((key + ' ' + text).trim());
  }
  return {
    hrefs: hrefs.slice(0, 200),
    tabs: tabs.slice(0, 40),
    grid: !!grid,
    n: hrefs.length,
  };
}"""

_CLICK_PROFILE_TABS_JS = """(label) => {
  const want = String(label || '').trim().toLowerCase();
  if (!want) return '';
  const root = document.querySelector('[data-e2e="user-page"]')
    || document.querySelector('[data-e2e="user-detail"]')
    || document.querySelector('main')
    || document;
  for (const el of root.querySelectorAll(
    'p, span, button, a, div[role="tab"], [data-e2e*="tab"]'
  )) {
    const t = (el.textContent || '').trim();
    if (!t || t.length > 24) continue;
    const low = t.toLowerCase();
    if (low !== want && !low.startsWith(want)) continue;
    if (!el.offsetParent) continue;
    const href = String((el.closest('a') || el).href || '');
    if (/\\/shortdrama\\/?$/.test(href) || /\\/explore/.test(href)) continue;
    try { el.click(); return t; } catch (e) {}
  }
  return '';
}"""

_CLICK_VIDEOS_TAB_JS = """() => {
  const sels = [
    '[data-e2e="videos-tab"]',
    '[data-e2e="user-tab-item"][href*="?tab"]',
    'a[href$="/"][data-e2e*="video"]',
  ];
  for (const s of sels) {
    const el = document.querySelector(s);
    if (!el) continue;
    try { el.click(); return s; } catch (e) {}
  }
  return '';
}"""

_SCROLL_PROFILE_JS = """() => {
  const el = document.querySelector('[data-e2e="user-post-item-list"]')
    || document.querySelector('main')
    || document.scrollingElement;
  if (el && el.scrollBy) el.scrollBy(0, 4000);
  window.scrollBy(0, 4000);
  return (el && (el.getAttribute('data-e2e') || el.tagName)) || 'none';
}"""

_PROFILE_DEBUG_JS = """() => {
  const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
  let detail = {};
  try {
    const data = el && el.textContent ? JSON.parse(el.textContent) : (window.SIGI_STATE || {});
    const scope = (data && data.__DEFAULT_SCOPE__) || data || {};
    detail = scope['webapp.user-detail'] || {};
  } catch (e) {}
  const info = detail.userInfo || {};
  const user = info.user || {};
  const stats = info.stats || {};
  return {
    uniqueId: user.uniqueId || '',
    videoCount: stats.videoCount || 0,
    itemList: Array.isArray(detail.itemList) ? detail.itemList.length : 0,
    posts: document.querySelectorAll('[data-e2e="user-post-item"]').length,
    videoLinks: [...document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]')]
      .map(a => (a.href || '').split('?')[0]).filter(Boolean).slice(0, 8),
  };
}"""

_CLOSE_OVERLAY_JS = """() => {
  try {
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', keyCode: 27, bubbles: true }));
  } catch (e) {}
  const inbox = document.querySelector('[data-e2e="inbox-icon"]');
  const panel = document.querySelector('[data-e2e="inbox-notifications"]');
  if (inbox && panel) { try { inbox.click(); } catch (e) {} }
  return !!(inbox && panel);
}"""

_KICK_PLAYER_JS = """() => {
  const v = document.querySelector('video');
  if (v) {
    v.muted = true;
    try { v.play(); } catch (e) {}
  }
  const btn = document.querySelector(
    '[data-e2e="player-play"], button[aria-label*="Play"], button[aria-label*="播放"]'
  );
  if (btn) { try { btn.click(); } catch (e) {} }
}"""

_EPISODE_STRIP_JS = """() => {
  let max = 0;
  const groups = document.querySelectorAll('div, ul, ol');
  for (const g of groups) {
    const nums = [];
    for (const el of g.children) {
      const s = ((el.innerText || '').trim());
      if (/^\\d{1,3}$/.test(s)) nums.push(parseInt(s, 10));
    }
    if (nums.length >= 4) max = Math.max(max, ...nums);
  }
  return max;
}"""

_READ_VIDEO_SRC_JS = """() => {
  const urls = [];
  for (const v of document.querySelectorAll('video')) {
    for (const s of [v.currentSrc, v.src]) {
      if (s && String(s).startsWith('http')) urls.push(s);
    }
    for (const src of v.querySelectorAll('source')) {
      const u = src.src || '';
      if (u && u.startsWith('http')) urls.push(u);
    }
  }
  return urls;
}"""


def _as_urls(val: Any) -> List[str]:
    out: List[str] = []
    if isinstance(val, str):
        if val.startswith("http"):
            out.append(val)
        return out
    if isinstance(val, list):
        for item in val[:80]:
            out.extend(_as_urls(item))
        return out
    if isinstance(val, dict):
        for key in ("UrlList", "url_list", "urlList", "urls", "url", "Url",
                    "playAddr", "PlayAddr", "downloadAddr", "DownloadAddr",
                    "play_addr", "download_addr"):
            if key in val:
                out.extend(_as_urls(val[key]))
    return out


def _is_play_url(url: str) -> bool:
    u = (url or "").lower()
    if not u.startswith("http"):
        return False
    if any(tok in u for tok in (
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".ico",
        "mime_type=image", "ibyteimg", "~tplv-", "/avatar", "cover",
        ".m3u8", "mime_type=audio",
    )):
        return False
    # v16-/tiktokcdn 太宽,会把接口 protobuf、封面、字幕当成播放地址
    return (
        "/video/tos/" in u
        or "mime_type=video" in u
        or ".mp4" in u.split("?")[0]
    )


def _uniq_play_urls(*groups: List[str]) -> List[str]:
    seen: set[str] = set()
    out: List[str] = []
    for group in groups:
        for url in group or []:
            u = str(url or "").strip()
            if not u or u in seen or not _is_play_url(u):
                continue
            seen.add(u)
            out.append(u)
    return out


def looks_like_video_bytes(body: bytes) -> bool:
    """浏览器 <video> 能认的容器: MP4/MOV(ftyp) 或 WebM。"""
    if not body or len(body) < 12:
        return False
    head = body[:64]
    stripped = head.lstrip()[:15].lower()
    if stripped.startswith(b"<!doctype") or stripped.startswith(b"<html"):
        return False
    if head[4:8] == b"ftyp" or b"ftyp" in head[:32]:
        return True
    return head.startswith(b"\x1aE\xdf\xa3")


def play_url_candidates(hit: Optional[dict]) -> List[str]:
    if not isinstance(hit, dict):
        return []
    extras = hit.get("play_urls") if isinstance(hit.get("play_urls"), list) else []
    return _uniq_play_urls([str(hit.get("play_url") or "")], [str(u) for u in extras])


def pick_tt_play_url(urls: List[str]) -> str:
    uniq = _uniq_play_urls(urls)
    if not uniq:
        return ""
    mp4 = [u for u in uniq if ".mp4" in u.lower() or "/video/tos/" in u.lower()]
    pool = mp4 or uniq
    no_m3u8 = [u for u in pool if ".m3u8" not in u.lower()]
    pool = no_m3u8 or pool
    clean = [u for u in pool if "unwatermarked" in u.lower() or "watermark=0" in u.lower()]
    wm = [u for u in pool if "watermark=1" in u.lower() or "playwm" in u.lower()]
    prefer = clean or [u for u in pool if u not in wm] or pool
    return prefer[-1]


def _url_play_score(url: str, base: int) -> int:
    u = (url or "").lower()
    if "unwatermarked" in u or "watermark=0" in u:
        base += 10
    if any(tok in u for tok in ("watermark=1", "playwm", "/aweme/v1/playwm")):
        base -= 30
    return base


def _collect_item_play_urls(obj: dict) -> List[str]:
    """无水印 playAddr / H.264 优先;downloadAddr 带水印,只作备用。"""
    video = obj.get("video") if isinstance(obj.get("video"), dict) else {}
    ranked: List[Tuple[int, str]] = []

    def add(urls: List[str], score: int) -> None:
        for url in urls:
            if url and _is_play_url(url):
                ranked.append((_url_play_score(url, score), url))

    bitrate = video.get("bitrateInfo") or video.get("bitrate_info") or []
    if isinstance(bitrate, list):
        for br in bitrate:
            if not isinstance(br, dict):
                continue
            codec = str(
                br.get("CodecType") or br.get("codecType")
                or br.get("codec_type") or br.get("Codec") or ""
            ).lower()
            if "h264" in codec or "avc" in codec:
                score = 95
            elif "h265" in codec or "hevc" in codec or "hvc" in codec:
                score = 25
            else:
                score = 70
            add(_as_urls(br), score)
    add(_as_urls(video.get("playAddr") or video.get("play_addr")), 90)
    add(_as_urls(obj.get("playAddr") or obj.get("play_addr")), 85)
    add(_as_urls(video.get("downloadAddr") or video.get("download_addr")), 40)
    ranked.sort(key=lambda item: -item[0])
    return _uniq_play_urls([url for _, url in ranked])


async def _response_json(resp) -> Any:
    try:
        return await resp.json()
    except Exception:
        pass
    try:
        text = await resp.text()
        if text and text.lstrip()[:1] in "{[":
            return json.loads(text)
    except Exception:
        pass
    return None


def is_tt_noise_feed_url(url: str) -> bool:
    u = (url or "").lower()
    return any(tok in u for tok in _NOISE_FEED_TOKS)


def is_tt_profile_feed_url(url: str) -> bool:
    u = (url or "").lower()
    return any(tok in u for tok in _PROFILE_FEED_TOKS)


def user_detail_from_payload(data: Any) -> Any:
    """只取 webapp.user-detail,避免整页 SIGI 里推荐流的 ItemModule。"""
    found: Any = None

    def walk(node: Any, depth: int = 0) -> None:
        nonlocal found
        if found is not None or depth > 10 or node is None:
            return
        if isinstance(node, list):
            for item in node[:40]:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if "webapp.user-detail" in node:
            found = node.get("webapp.user-detail")
            return
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    walk(data)
    return found


def _to_int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def _item_id(node: dict) -> str:
    video = node.get("video") if isinstance(node.get("video"), dict) else {}
    for key in (
        "id", "aweme_id", "itemId", "videoId", "episodeId", "episode_id",
        "dramaID", "dramaId", "drama_id", "seriesId", "series_id",
    ):
        vid = str(node.get(key) or "").strip()
        if _ITEM_ID_RE.match(vid):
            return vid
    vid = str(video.get("id") or "").strip()
    return vid if _ITEM_ID_RE.match(vid) else ""


def _first_http(val: Any, depth: int = 0) -> str:
    if depth > 4 or val is None:
        return ""
    if isinstance(val, str):
        return val if val.startswith("http") else ""
    if isinstance(val, list):
        for item in val[:12]:
            found = _first_http(item, depth + 1)
            if found:
                return found
        return ""
    if isinstance(val, dict):
        for item in val.values():
            found = _first_http(item, depth + 1)
            if found:
                return found
    return ""


def _meta_from_item(obj: dict) -> dict:
    video = obj.get("video") if isinstance(obj.get("video"), dict) else {}
    author = obj.get("author") if isinstance(obj.get("author"), dict) else {}
    if not author and isinstance(obj.get("creator"), dict):
        author = obj["creator"]
    stats = obj.get("stats") if isinstance(obj.get("stats"), dict) else {}
    vid = _item_id(obj)
    urls = _collect_item_play_urls(obj)
    cover = ""
    for key in ("originCover", "cover", "dynamicCover", "thumbnail"):
        for cand in _as_urls(video.get(key)):
            if cand.startswith("http"):
                cover = cand
                break
        if cover:
            break
    handle = str(author.get("uniqueId") or author.get("unique_id") or "").strip()
    nick = str(author.get("nickname") or author.get("nickName") or handle or "").strip()
    duration = _to_int(video.get("duration") or obj.get("duration") or obj.get("totalDuration") or 0)
    if duration > 10_000:
        duration //= 1000
    drama = obj.get("shortDramaSeriesInfo") or obj.get("short_drama_series_info") or {}
    is_drama = bool(drama) or bool(
        obj.get("seriesId") or obj.get("episodeNo") or obj.get("episode_no")
        or obj.get("dramaID") or obj.get("dramaId") or obj.get("dramaName")
        or obj.get("numVideos")
    )
    if not cover:
        cover = (
            _first_http(obj.get("cover"))
            or _first_http(obj.get("coverUrl"))
            or _first_http(obj.get("poster"))
            or _first_http(obj.get("zoomCover"))
        )
    info = drama if isinstance(drama, dict) else {}
    series_id = str(info.get("seriesId") or info.get("series_id") or "").strip()
    episode = _to_int(
        info.get("episodeNumber") or info.get("episode_number")
        or info.get("episodeNo") or info.get("episode_no")
        or obj.get("episodeNo") or obj.get("episode_no") or 0
    )
    episode_count = _to_int(
        info.get("episodeCount") or info.get("episode_count")
        or info.get("totalEpisode") or info.get("totalEpisodes") or 0
    )
    kind = "shortdrama" if is_drama else "video"
    title = str(
        obj.get("desc") or obj.get("title") or obj.get("name")
        or obj.get("dramaName") or obj.get("drama_name")
        or (drama.get("title") if isinstance(drama, dict) else "")
        or vid
    ).strip()
    return {
        "id": vid,
        "play_url": urls[0] if urls else "",
        "play_urls": urls,
        "title": title,
        "author": nick or "tiktok",
        "handle": handle,
        "cover": cover if cover.startswith("http") else "",
        "duration": duration,
        "like_count": _to_int(stats.get("diggCount") or obj.get("diggCount")),
        "comment_count": _to_int(stats.get("commentCount") or obj.get("commentCount")),
        "create_time": _to_int(obj.get("createTime") or obj.get("create_time")),
        "avatar": str(author.get("avatarLarger") or author.get("avatarThumb") or ""),
        "kind": kind,
        "series_id": series_id,
        "series_title": str(
            info.get("seriesTitle") or info.get("series_title") or info.get("title") or ""
        ).strip(),
        "episode": episode if 1 <= episode <= 500 else 0,
        "episode_count": episode_count if 1 <= episode_count <= 500 else 0,
    }


def _remember_episode(episodes: Dict[int, dict], meta: dict) -> None:
    ep = int(meta.get("episode") or 0)
    if ep < 1 or ep > 500:
        return
    prev = episodes.get(ep)
    if not prev:
        episodes[ep] = dict(meta)
        return
    merged = dict(prev)
    for key, val in meta.items():
        if val in ("", 0, None, [], {}):
            continue
        if key == "play_urls" and isinstance(val, list):
            merged["play_urls"] = _uniq_play_urls(prev.get("play_urls") or [], val)
            continue
        if key == "play_url" and prev.get("play_url"):
            if len(str(val)) > len(str(prev.get("play_url") or "")):
                merged["play_url"] = val
            continue
        merged[key] = val
    if not merged.get("play_url") and merged.get("play_urls"):
        merged["play_url"] = merged["play_urls"][0]
    episodes[ep] = merged


def drama_episodes_from_payload(data: Any, series_id: str = "") -> dict:
    """从短剧页 JSON 取出同一 seriesId 的分集。

    URL 里的长数字是系列 id,每一集另有自己的视频 id 和 episodeNumber。
    其它剧的 shortDramaSeriesInfo 不收。没有系列 id 的分集行,只在已经走进
    目标剧对象时才收。
    """
    want = str(series_id or "").strip()
    episodes: Dict[int, dict] = {}
    found_series = want
    title = ""
    count = 0

    def take_count(n: int) -> None:
        nonlocal count
        if 1 <= n <= 500 and n > count:
            count = n

    def walk(node: Any, depth: int, ctx_sid: str) -> None:
        nonlocal title, found_series
        if depth > 16 or node is None:
            return
        if isinstance(node, list):
            for item in node[:500]:
                walk(item, depth + 1, ctx_sid)
            return
        if not isinstance(node, dict):
            return
        info = node.get("shortDramaSeriesInfo") or node.get("short_drama_series_info") or {}
        if not isinstance(info, dict):
            info = {}
        sid = str(
            info.get("seriesId") or info.get("series_id")
            or node.get("seriesId") or node.get("series_id")
            or node.get("dramaID") or node.get("dramaId") or node.get("drama_id")
            or ""
        ).strip()
        if sid and not _ITEM_ID_RE.match(sid):
            sid = ""
        if want and sid and sid != want:
            for value in node.values():
                walk(value, depth + 1, "")
            return
        child_ctx = ctx_sid
        if sid and (not want or sid == want):
            found_series = sid
            child_ctx = sid
            take_count(_to_int(
                info.get("episodeCount") or info.get("episode_count")
                or info.get("totalEpisode") or info.get("totalEpisodes") or 0
            ))
            if sid == (want or sid):
                take_count(_to_int(node.get("numVideos") or info.get("numVideos") or 0))
            name = str(
                info.get("seriesTitle") or info.get("series_title")
                or info.get("title") or node.get("dramaName") or node.get("drama_name")
                or ""
            ).strip()
            if name:
                title = title or name
        active = child_ctx if not want else (want if (sid == want or (not sid and ctx_sid == want) or child_ctx == want) else "")
        if active == (want or found_series or active) and (not want or active == want):
            meta = _meta_from_item(node) if (_looks_like_item(node) or _item_id(node)) else {}
            ep = int(meta.get("episode") or 0) if meta else 0
            if not ep:
                ep = _to_int(
                    info.get("episodeNumber") or info.get("episode_number")
                    or node.get("episodeNumber") or node.get("episodeNo") or 0
                )
            vid = str((meta or {}).get("id") or _item_id(node) or "")
            series_key = want or found_series
            if ep and vid and vid != series_key and (not want or active == want or sid == want):
                packed = dict(meta or {})
                packed["id"] = vid
                packed["episode"] = ep
                packed["series_id"] = want or sid or found_series
                if title:
                    packed["series_title"] = packed.get("series_title") or title
                _remember_episode(episodes, packed)
        for value in node.values():
            walk(value, depth + 1, child_ctx if not (want and sid and sid != want) else "")

    walk(data, 0, want)
    if not count and episodes:
        count = max(episodes)
    ordered = [episodes[n] for n in sorted(episodes)]
    return {
        "series_id": found_series,
        "title": title,
        "episode_count": count,
        "episodes": ordered,
    }


def _hit_from_media_urls(urls: List[str], vid: str = "") -> Optional[dict]:
    cands = _uniq_play_urls(urls)
    play = pick_tt_play_url(cands)
    if not play:
        return None
    return {
        "id": str(vid or ""),
        "play_url": play,
        "play_urls": cands,
        "title": "",
        "author": "tiktok",
        "handle": "",
        "cover": "",
        "duration": 0,
        "like_count": 0,
        "comment_count": 0,
        "create_time": 0,
        "avatar": "",
    }


def hit_from_payload(data: Any, want_id: str = "") -> Optional[dict]:
    """在任意 JSON 里找出带播放地址的作品/短剧条目。"""
    best: Optional[dict] = None
    matched: Optional[dict] = None
    urls: List[str] = []
    want = str(want_id or "").strip()

    def walk(node: Any, depth: int = 0) -> None:
        nonlocal best, matched
        if depth > 14 or node is None:
            return
        if isinstance(node, list):
            for item in node[:120]:
                walk(item, depth + 1)
            return
        if isinstance(node, str):
            if _is_play_url(node):
                urls.append(node)
            return
        if not isinstance(node, dict):
            return
        vid_hint = node.get("id") or node.get("aweme_id") or node.get("itemId")
        video = node.get("video")
        if vid_hint and (
            isinstance(video, dict) or node.get("playAddr") or node.get("play_addr")
        ):
            meta = _meta_from_item(node)
            item_id = str(meta.get("id") or "")
            # 店铺作品常带 isAd;只有 ID 对得上时才收下,避免把前贴广告当成目标
            skip_ad = _is_tt_ad_item(node) and (not want or item_id != want)
            if not skip_ad and meta.get("play_url"):
                if want and item_id == want:
                    matched = meta
                if best is None or len(meta["play_url"]) >= len(best.get("play_url") or ""):
                    best = meta
        for value in node.values():
            walk(value, depth + 1)

    walk(data)
    chosen = matched or (None if want else best)
    if chosen and chosen.get("play_url"):
        return chosen
    if want:
        return None
    return _hit_from_media_urls(urls)


def _is_tt_ad_item(node: dict) -> bool:
    if not isinstance(node, dict):
        return False
    for key in ("isAd", "isSponsored", "is_ad", "is_sponsored"):
        val = node.get(key)
        if val is True or val in (1, "1", "true", "True"):
            return True
    ad = node.get("ad") or node.get("adInfo") or node.get("advertisement_info")
    return bool(isinstance(ad, dict) and ad)


def _looks_like_item(node: dict) -> bool:
    vid = _item_id(node)
    if not vid:
        return False
    if isinstance(node.get("video"), dict):
        return True
    if node.get("author") or node.get("desc") or node.get("createTime") or node.get("create_time"):
        return True
    if (node.get("shortDramaSeriesInfo") or node.get("seriesId")
            or node.get("episodeNo") or node.get("episode_no")
            or node.get("dramaID") or node.get("dramaId") or node.get("dramaName")
            or node.get("numVideos")):
        return True
    return False


def items_from_payload(data: Any, *, list_keys_only: bool = False) -> List[dict]:
    """从主页/item_list/短剧 JSON 收集作品(带 _hit 归一字段)。

    list_keys_only: 只收 itemList/dramaList 等数组,避免空列表响应里夹带的广告 itemStruct
    被当成该账号作品。
    """
    found: Dict[str, dict] = {}

    def take_item(node: dict) -> None:
        if not _looks_like_item(node):
            return
        meta = _meta_from_item(node)
        vid = str(meta.get("id") or "")
        if vid:
            packed = dict(node)
            packed["_hit"] = meta
            found[vid] = packed

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 14 or node is None:
            return
        if isinstance(node, list):
            for item in node[:200]:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        if list_keys_only:
            for key in _FEED_LIST_KEYS:
                val = node.get(key)
                if isinstance(val, list):
                    for item in val[:200]:
                        if isinstance(item, dict):
                            take_item(item)
            for value in node.values():
                if isinstance(value, (dict, list)):
                    walk(value, depth + 1)
            return
        if _looks_like_item(node):
            take_item(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    walk(data)
    return list(found.values())


def items_from_hrefs(hrefs: List[str], *, handle: str = "") -> List[dict]:
    """从主页 DOM 链接收 /video/ 与 /shortdrama/episode/。"""
    found: Dict[str, dict] = {}
    hid = (handle or "").lstrip("@")
    for href in hrefs or []:
        h = str(href or "").split("?")[0]
        drama = _HREF_DRAMA_RE.search(h)
        video = _HREF_VIDEO_RE.search(h)
        if drama:
            vid, kind = drama.group(1), "shortdrama"
            item_handle = hid
        elif video:
            item_handle = (video.group(1) or hid or "").lstrip("@")
            vid, kind = video.group(2), "video"
        else:
            continue
        found[vid] = {"_hit": {
            "id": vid,
            "play_url": "",
            "title": vid,
            "author": item_handle or hid or "tiktok",
            "handle": item_handle,
            "cover": "",
            "duration": 0,
            "like_count": 0,
            "comment_count": 0,
            "create_time": 0,
            "avatar": "",
            "kind": kind,
        }}
    return list(found.values())


def _user_meta_dict(user: dict) -> dict:
    if not isinstance(user, dict):
        return {}
    handle = str(user.get("uniqueId") or user.get("unique_id") or "").strip()
    nick = str(user.get("nickname") or user.get("nickName") or handle).strip()
    uid = str(user.get("id") or user.get("uid") or user.get("secUid") or "").strip()
    avatar = user.get("avatarLarger") or user.get("avatarThumb") or user.get("avatar") or ""
    if isinstance(avatar, dict):
        urls = _as_urls(avatar)
        avatar = urls[0] if urls else ""
    if not handle and not nick:
        return {}
    return {
        "nickname": nick or handle,
        "sec_uid": uid or handle,
        "douyin_id": f"@{handle.lstrip('@')}" if handle else "",
        "avatar": str(avatar or ""),
    }


def _handle_key(meta: dict) -> str:
    return (meta.get("douyin_id") or meta.get("sec_uid") or "").lstrip("@").lower()


def user_meta_from_payload(data: Any, want_handle: str = "") -> dict:
    """从 user-detail / userInfo 抽出被打开主页的资料,不要登录账号自己。"""
    want = (want_handle or "").lstrip("@").lower()
    matched: dict = {}
    profile: dict = {}

    def consider(user: dict) -> None:
        nonlocal matched, profile
        meta = _user_meta_dict(user)
        if not meta:
            return
        if want and _handle_key(meta) == want:
            matched = meta
        elif not profile:
            profile = meta

    def walk(node: Any, depth: int = 0) -> None:
        if matched or depth > 12 or node is None:
            return
        if isinstance(node, list):
            for item in node[:80]:
                walk(item, depth + 1)
            return
        if not isinstance(node, dict):
            return
        # 登录态在 webapp.app-context.user,那是自己,不是监控目标
        if "webapp.app-context" in node:
            for key, value in node.items():
                if key == "webapp.app-context":
                    continue
                walk(value, depth + 1)
            return
        detail = node.get("webapp.user-detail")
        if isinstance(detail, dict):
            info = detail.get("userInfo") if isinstance(detail.get("userInfo"), dict) else detail
            if isinstance(info, dict) and isinstance(info.get("user"), dict):
                consider(info["user"])
        if isinstance(node.get("userInfo"), dict) and isinstance(node["userInfo"].get("user"), dict):
            consider(node["userInfo"]["user"])
        if isinstance(node.get("user"), dict):
            consider(node["user"])
        if node.get("uniqueId") or node.get("unique_id"):
            consider(node)
        for value in node.values():
            if isinstance(value, (dict, list)):
                walk(value, depth + 1)

    walk(data)
    return matched or ({} if want else profile)


async def fetch_tt_user_videos(
    mgr: BrowserManager,
    identity: Identity,
    user_ref: str,
    known_ids: Optional[Set[str]] = None,
    max_scrolls: int = 12,
    settle_ms: int = 1800,
    block_media: bool = True,
) -> Tuple[List[dict], dict, str]:
    """打开用户主页并下滑,拦截 item_list / UNIVERSAL_DATA 收集作品。"""
    from .resolve import handle_from_user_ref, user_feed_url

    url = user_feed_url(user_ref)
    if not url:
        return [], {}, "无效的 TikTok 主页链接"
    want = handle_from_user_ref(user_ref).lower()
    known = known_ids or set()
    collected: Dict[str, dict] = {}
    author: dict = {}
    error = ""
    api_seen: list = []
    # 短剧号 Videos 列表常为空,需要 DOM 链接和合集 Tab,不能挡图片
    try:
        page = await mgr.new_page(identity, False)
    except Exception as exc:
        return [], {}, f"打开主页失败: {exc!r}"

    def ingest(data: Any, *, trust_profile: bool = False) -> None:
        nonlocal author
        if not data:
            return
        meta = user_meta_from_payload(data, want_handle=want)
        if meta:
            if not author or (want and _handle_key(author) != want and _handle_key(meta) == want):
                author = meta
        for item in items_from_payload(data, list_keys_only=True):
            hit = item.get("_hit") or {}
            vid = str(hit.get("id") or "")
            if not vid:
                continue
            item_handle = str(hit.get("handle") or "").lstrip("@").lower()
            if want and item_handle and item_handle != want:
                continue
            if want and not item_handle:
                if trust_profile or str(hit.get("kind") or "") == "shortdrama":
                    hit["handle"] = want
                else:
                    continue
            collected[vid] = item

    async def harvest_dom() -> None:
        try:
            snap = await page.evaluate(_HARVEST_PROFILE_JS, want)
        except Exception:
            return
        hrefs = list((snap or {}).get("hrefs") or [])
        for item in items_from_hrefs(hrefs, handle=want):
            hit = item.get("_hit") or {}
            vid = str(hit.get("id") or "")
            item_handle = str(hit.get("handle") or "").lstrip("@").lower()
            if want and item_handle and item_handle != want:
                continue
            if vid:
                collected.setdefault(vid, item)
        if not collected:
            print(
                f"[tt_videos] dom hrefs={len(hrefs)} grid={(snap or {}).get('grid')} "
                f"tabs={(snap or {}).get('tabs')[:16]}",
                flush=True,
            )

    async def on_response(resp):
        rurl = resp.url or ""
        rt = resp.request.resource_type
        if "tiktok.com" not in rurl:
            return
        path = rurl.split("?")[0]
        if rt in ("xhr", "fetch") or "/api/" in path:
            if len(api_seen) < 50:
                api_seen.append(f"{resp.status} {rt} {path.split('tiktok.com')[-1][:90]}")
        if is_tt_noise_feed_url(rurl):
            return
        if rt not in ("xhr", "fetch") and "/api/" not in path:
            return
        if not (is_tt_profile_feed_url(rurl) or "item_list" in path.lower()):
            return
        data = await _response_json(resp)
        if data is None:
            print(f"[tt_videos] json-fail {path.split('tiktok.com')[-1][:80]}", flush=True)
            return
        if "/drama/" in path.lower() and isinstance(data, dict):
            sample = data.get("dramaList") or data.get("drama_list") or []
            first = sample[0] if isinstance(sample, list) and sample else {}
            print(
                f"[tt_videos] drama_list n="
                f"{len(sample) if isinstance(sample, list) else type(sample).__name__} "
                f"keys={list(data.keys())[:12]} "
                f"item_keys={list(first.keys())[:14] if isinstance(first, dict) else []}",
                flush=True,
            )
        if "/api/post/item_list" in path.lower() and isinstance(data, dict):
            sample = data.get("itemList") or data.get("item_list") or data.get("aweme_list") or []
            first = sample[0] if isinstance(sample, list) and sample else {}
            ah = ""
            if isinstance(first, dict):
                au = first.get("author") if isinstance(first.get("author"), dict) else {}
                ah = str(au.get("uniqueId") or "")
            print(
                f"[tt_videos] post_item_list n="
                f"{len(sample) if isinstance(sample, list) else type(sample).__name__} "
                f"status={data.get('statusCode') or data.get('status_code')} "
                f"keys={list(data.keys())[:12]} first_handle={ah!r} "
                f"isAd={first.get('isAd') if isinstance(first, dict) else None}",
                flush=True,
            )
        ingest(data, trust_profile=is_tt_profile_feed_url(rurl) or "/api/post/item_list" in path.lower())

    page.on("response", on_response)
    final_url = ""
    dbg = {}
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.evaluate(_CLOSE_OVERLAY_JS)
        except Exception:
            pass
        try:
            tab = await page.evaluate(_CLICK_VIDEOS_TAB_JS)
            if tab:
                print(f"[tt_videos] videos-tab {tab}", flush=True)
                await page.wait_for_timeout(600)
        except Exception:
            pass
        try:
            await page.wait_for_selector(
                f'[data-e2e="user-post-item"], a[href*="/@{want}/video/"], a[href*="/video/"]',
                timeout=20000,
            )
        except Exception:
            pass
        await page.wait_for_timeout(max(settle_ms, 2500))
        try:
            scoped = user_detail_from_payload(await page.evaluate(_READ_PAGE_STATE_JS))
            if scoped is not None:
                ingest(scoped, trust_profile=True)
        except Exception:
            pass
        try:
            dbg = await page.evaluate(_PROFILE_DEBUG_JS) or {}
        except Exception:
            dbg = {}
        await harvest_dom()
        clicked = []
        for label in ("Short dramas", "短剧", "Series", "Playlist", "Playlists", "播放列表", "Videos", "视频"):
            try:
                hit = await page.evaluate(_CLICK_PROFILE_TABS_JS, label)
            except Exception:
                hit = ""
            if not hit or hit in clicked:
                continue
            await page.wait_for_timeout(settle_ms)
            here = (page.url or "").lower()
            if want and f"/@{want}" not in here:
                print(f"[tt_videos] tab {hit!r} left profile -> {page.url}", flush=True)
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(settle_ms)
                except Exception:
                    pass
                continue
            clicked.append(hit)
            await harvest_dom()
        if clicked:
            print(f"[tt_videos] clicked tabs={clicked}", flush=True)
        stagnant = 0
        for i in range(max(0, int(max_scrolls or 0))):
            before = len(collected)
            try:
                await page.evaluate(_SCROLL_PROFILE_JS)
            except Exception:
                await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(settle_ms)
            await harvest_dom()
            if (known & set(collected.keys())) and i >= 1 and len(collected) >= 8:
                break
            if len(collected) == before:
                stagnant += 1
                if stagnant >= 3:
                    break
            else:
                stagnant = 0
        final_url = page.url or ""
        if not collected:
            low = final_url.lower()
            if "login" in low or "passport" in low:
                error = "logged_out:登录态失效,请重新登录"
            else:
                error = (
                    "未拦截到作品或短剧数据；该账号可能没有公开 Videos,"
                    "或短剧只在合集/剧集入口里。请确认登录地区能打开该主页"
                )
    except Exception as exc:
        error = f"打开主页失败: {exc!r}"
    finally:
        try:
            await page.close()
        except Exception:
            pass

    if want and (not author or _handle_key(author) != want):
        for item in collected.values():
            hit = item.get("_hit") or {}
            if str(hit.get("handle") or "").lstrip("@").lower() == want:
                author = {
                    "nickname": hit.get("author") or "",
                    "sec_uid": hit.get("handle") or "",
                    "douyin_id": "@" + str(hit.get("handle") or "").lstrip("@"),
                    "avatar": hit.get("avatar") or "",
                }
                break

    stats_n = 0
    try:
        stats_n = int((dbg or {}).get("videoCount") or 0)
    except (TypeError, ValueError):
        stats_n = 0
    if stats_n and isinstance(author, dict):
        author = dict(author)
        author["aweme_count"] = stats_n

    ids = list(collected.keys())[:12]
    print(
        f"[tt_videos] user_ref={user_ref!r} items={len(collected)} ids={ids} "
        f"author={author.get('douyin_id')} dbg={dbg} err={error} "
        f"api_seen={api_seen[:24]}",
        flush=True,
    )
    return list(collected.values()), author, error


async def fetch_tt_video_detail(
    mgr: BrowserManager,
    identity: Identity,
    source_url: str,
    settle_ms: int = 2800,
) -> Optional[dict]:
    """打开原分享页(短剧页不要改写成 /video/),拦截播放直链。"""
    url = (source_url or "").strip()
    if not url or "tiktok.com" not in url:
        return None
    collected: Dict[str, dict] = {}
    media_urls: List[str] = []
    from .resolve import resolve_tiktok_video_id
    expect_id = resolve_tiktok_video_id(url) or ""
    page = await mgr.new_page(identity, block_media=False)

    async def on_response(resp):
        rurl = resp.url or ""
        rt = resp.request.resource_type
        if _is_play_url(rurl):
            media_urls.append(rurl)
        if rt not in ("xhr", "fetch") or "tiktok.com" not in rurl:
            return
        data = await _response_json(resp)
        if data is None:
            return
        hit = hit_from_payload(data, want_id=expect_id)
        if hit and hit.get("play_url"):
            hid = str(hit.get("id") or "")
            if expect_id and hid == expect_id:
                collected["hit"] = hit
            elif "hit" not in collected and not expect_id:
                collected["hit"] = hit

    page.on("response", on_response)
    final_url = ""
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.evaluate(_KICK_PLAYER_JS)
        except Exception:
            pass
        waited = 0
        limit = max(settle_ms, 800) + 8000
        while waited < limit:
            if collected.get("hit"):
                break
            await page.wait_for_timeout(400)
            waited += 400
            if waited in (1200, 2800, 5200):
                try:
                    await page.evaluate(_KICK_PLAYER_JS)
                except Exception:
                    pass
        if "hit" not in collected:
            try:
                rendered = await page.evaluate(_READ_PAGE_STATE_JS)
                hit = hit_from_payload(rendered, want_id=expect_id)
                if hit and hit.get("play_url"):
                    collected["hit"] = hit
            except Exception:
                pass
        if "hit" not in collected:
            try:
                srcs = await page.evaluate(_READ_VIDEO_SRC_JS)
                if isinstance(srcs, list):
                    media_urls.extend(str(u) for u in srcs)
            except Exception:
                pass
        if "hit" not in collected:
            # MSE 播放器 currentSrc 是 blob:,真正的 tos CDN 只出现在 media 请求里
            hit = _hit_from_media_urls(media_urls, expect_id)
            if hit:
                collected["hit"] = hit
        final_url = page.url or ""
    except Exception as exc:
        print(f"[share] tt browser detail goto failed: {exc!r}", flush=True)
    finally:
        try:
            await page.close()
        except Exception:
            pass

    hit = collected.get("hit")
    if hit:
        if not hit.get("id"):
            from .resolve import resolve_tiktok_video_id
            hit["id"] = resolve_tiktok_video_id(url) or resolve_tiktok_video_id(final_url) or "tiktok"
        extras = hit.get("play_urls") if isinstance(hit.get("play_urls"), list) else []
        hit["play_urls"] = _uniq_play_urls(
            [str(hit.get("play_url") or "")], [str(u) for u in extras], media_urls,
        )
        if not hit.get("play_url") and hit["play_urls"]:
            hit["play_url"] = hit["play_urls"][0]
        print("[share] tt browser detail hit", flush=True)
        return hit
    print(
        f"[share] tt browser detail miss url={url} final={final_url} "
        f"expect={expect_id} media={len(media_urls)}",
        flush=True,
    )
    return None


def _absorb_drama(bucket: dict, parsed: dict) -> None:
    if not parsed:
        return
    if parsed.get("series_id") and not bucket.get("series_id"):
        bucket["series_id"] = parsed["series_id"]
    if parsed.get("title") and not bucket.get("title"):
        bucket["title"] = parsed["title"]
    count = int(parsed.get("episode_count") or 0)
    if count > int(bucket.get("episode_count") or 0):
        bucket["episode_count"] = count
    for meta in parsed.get("episodes") or []:
        _remember_episode(bucket["episodes"], meta)


async def fetch_tt_drama_series(
    mgr: BrowserManager,
    identity: Identity,
    source_url: str,
    on_progress=None,
) -> dict:
    """打开短剧某一集,读出总集数,缺播放地址的集再逐集打开。"""
    from .resolve import shortdrama_episode_url, shortdrama_parts

    series_id, start_ep = shortdrama_parts(source_url)
    url = (source_url or "").strip()

    def report(current: int, total: int) -> None:
        if not on_progress:
            return
        try:
            on_progress(int(current or 0), int(total or 0), bucket.get("title") or "")
        except Exception:
            pass
    if not series_id or "tiktok.com" not in url:
        return {"series_id": "", "title": "", "episode_count": 0, "episodes": [], "error": "不是短剧链接"}
    bucket: dict = {
        "series_id": series_id,
        "title": "",
        "episode_count": 0,
        "episodes": {},
        "error": "",
    }
    pending = {"n": 0}
    media_urls: List[str] = []
    page = await mgr.new_page(identity, block_media=False)

    def _take_hit(hit: Optional[dict], episode: int) -> None:
        if not hit or not hit.get("play_url") or episode < 1:
            return
        sid = str(hit.get("series_id") or "")
        if sid and sid != series_id:
            return
        packed = dict(hit)
        packed["episode"] = int(hit.get("episode") or 0) or episode
        if packed["episode"] != episode and int(hit.get("episode") or 0):
            packed["episode"] = int(hit["episode"])
        packed["series_id"] = series_id
        if bucket.get("title"):
            packed["series_title"] = packed.get("series_title") or bucket["title"]
        _remember_episode(bucket["episodes"], packed)

    async def on_response(resp):
        rurl = resp.url or ""
        rt = resp.request.resource_type
        if _is_play_url(rurl):
            media_urls.append(rurl)
        if rt not in ("xhr", "fetch") or "tiktok.com" not in rurl:
            return
        data = await _response_json(resp)
        if data is None:
            return
        _absorb_drama(bucket, drama_episodes_from_payload(data, series_id))

    async def _read_state() -> None:
        try:
            rendered = await page.evaluate(_READ_PAGE_STATE_JS)
        except Exception:
            rendered = None
        if rendered is not None:
            _absorb_drama(bucket, drama_episodes_from_payload(rendered, series_id))
            if pending["n"]:
                hit = hit_from_payload(rendered, "")
                sid = str((hit or {}).get("series_id") or "")
                ep = int((hit or {}).get("episode") or 0)
                if hit and (sid == series_id or ep == pending["n"]):
                    _take_hit(hit, pending["n"])
        if pending["n"] and not (bucket["episodes"].get(pending["n"]) or {}).get("play_url"):
            try:
                srcs = await page.evaluate(_READ_VIDEO_SRC_JS)
                if isinstance(srcs, list):
                    media_urls.extend(str(u) for u in srcs)
            except Exception:
                pass
            _take_hit(_hit_from_media_urls(media_urls, ""), pending["n"])

    async def _open(episode: int) -> None:
        pending["n"] = episode
        media_urls.clear()
        target = shortdrama_episode_url(series_id, episode)
        report(episode, int(bucket.get("episode_count") or 0))
        print(f"[tt-drama] open {episode} {target}", flush=True)
        await page.goto(target, wait_until="domcontentloaded", timeout=45000)
        try:
            await page.evaluate(_KICK_PLAYER_JS)
        except Exception:
            pass
        waited = 0
        while waited < 12000:
            cur = bucket["episodes"].get(episode) or {}
            if cur.get("play_url"):
                break
            await page.wait_for_timeout(400)
            waited += 400
            if waited in (1600, 4000, 8000):
                try:
                    await page.evaluate(_KICK_PLAYER_JS)
                except Exception:
                    pass
        await _read_state()
        low = (page.url or "").lower()
        if "login" in low or "passport" in low:
            bucket["error"] = "logged_out:登录态失效,请重新登录"

    page.on("response", on_response)
    try:
        await _open(start_ep or 1)
        if not bucket["episode_count"]:
            try:
                strip = int(await page.evaluate(_EPISODE_STRIP_JS) or 0)
            except Exception:
                strip = 0
            if strip > int(bucket["episode_count"] or 0):
                bucket["episode_count"] = strip
        count = int(bucket["episode_count"] or 0)
        if bucket["episodes"]:
            count = max(count, max(bucket["episodes"]))
        count = min(max(count, start_ep or 1), 200)
        bucket["episode_count"] = count
        print(
            f"[tt-drama] series={series_id} title={bucket.get('title')!r} "
            f"count={count} have={sorted(bucket['episodes'])}",
            flush=True,
        )
        for n in range(1, count + 1):
            if bucket.get("error", "").startswith("logged_out"):
                break
            cur = bucket["episodes"].get(n) or {}
            if cur.get("play_url"):
                continue
            try:
                await _open(n)
            except Exception as exc:
                print(f"[tt-drama] episode {n} failed: {exc!r}", flush=True)
    except Exception as exc:
        bucket["error"] = bucket.get("error") or f"打开短剧失败: {exc!r}"
        print(f"[tt-drama] {bucket['error']}", flush=True)
    finally:
        try:
            await page.close()
        except Exception:
            pass

    ordered = [bucket["episodes"][n] for n in sorted(bucket["episodes"])]
    print(
        f"[tt-drama] done series={series_id} episodes={len(ordered)} "
        f"with_play={sum(1 for e in ordered if e.get('play_url'))} "
        f"err={bucket.get('error') or ''}",
        flush=True,
    )
    return {
        "series_id": series_id,
        "title": bucket.get("title") or "",
        "episode_count": int(bucket.get("episode_count") or len(ordered)),
        "episodes": ordered,
        "error": bucket.get("error") or "",
    }


async def download_tt_url(
    mgr: BrowserManager,
    identity: Identity,
    url: str,
    dest: Path,
) -> str:
    """用账号 context 的 Cookie/代理拉 CDN。成功返回空串。"""
    ctx = await mgr.context_for(identity)
    try:
        resp = await ctx.request.get(
            url,
            headers={
                "Referer": "https://www.tiktok.com/",
                "Origin": "https://www.tiktok.com",
            },
            timeout=180_000,
        )
    except Exception as exc:
        return str(exc)
    if resp.status not in (200, 206):
        return f"HTTP {resp.status}"
    ctype = str(resp.headers.get("content-type") or "").lower()
    if any(tok in ctype for tok in ("json", "html", "javascript", "xml", "text/plain")):
        return f"not video content-type ({ctype or 'empty'})"
    body = await resp.body()
    head = body[:80].lstrip()
    if len(body) < 4096 or head.startswith(b"<!DOCTYPE") or head.startswith(b"<html"):
        return "empty or html body"
    if not looks_like_video_bytes(body):
        return "CDN 返回的不是可播放视频(常见于接口 protobuf 或 HEVC 初始化段)"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(body)
    return ""
