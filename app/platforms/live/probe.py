"""开播探测：抖音(浏览器主页) / YouTube(yt-dlp /live)。"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

log = logging.getLogger("creatorhub.live")

_LIVE_HREF_RE = re.compile(r"https?://live\.douyin\.com/([A-Za-z0-9_.-]+)")
_ROOM_ID_RE = re.compile(r'"(?:room_id|roomId|web_rid)"\s*:\s*"?(\d{6,})"?')


@dataclass
class LiveInfo:
    is_live: bool
    platform: str
    session_id: str = ""       # 本场唯一 id(抖音 room / YouTube video)
    title: str = ""
    author: str = ""
    cover: str = ""
    watch_url: str = ""        # 直播间页面 / YouTube 观看地址
    stream_url: str = ""       # 抖音直链(flv/hls),有则优先 ffmpeg 录
    error: str = ""


def youtube_live_url(channel_ref: str) -> str:
    """频道 → /live 入口(开播时跳到当前直播间)。"""
    ref = (channel_ref or "").strip().rstrip("/")
    if not ref:
        return ""
    if ref.startswith("http"):
        base = ref.split("?")[0].rstrip("/")
        for suf in ("/videos", "/streams", "/shorts", "/live", "/featured"):
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        return base + "/live"
    if ref.startswith("UC"):
        return f"https://www.youtube.com/channel/{ref}/live"
    if ref.startswith("@"):
        return f"https://www.youtube.com/{ref}/live"
    return f"https://www.youtube.com/{ref}/live"


def probe_youtube_live_sync(
    channel_ref: str,
    *,
    proxy: str = "",
    cookie_file: str = "",
    user_agent: str = "",
) -> LiveInfo:
    try:
        import yt_dlp  # type: ignore
    except ImportError:
        return LiveInfo(False, "youtube", error="缺少 yt-dlp 依赖")

    from ..youtube.fetcher import apply_yt_dlp_youtube_opts

    url = youtube_live_url(channel_ref)
    if not url:
        return LiveInfo(False, "youtube", error="无效频道")

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
        "socket_timeout": 30,
        "retries": 2,
        "http_headers": {
            "User-Agent": user_agent or (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        },
    }
    apply_yt_dlp_youtube_opts(opts)
    if proxy:
        opts["proxy"] = proxy
    if cookie_file:
        opts["cookiefile"] = cookie_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        msg = str(exc)
        # 未开播时 yt-dlp 常抛 "is offline" / "not currently live"
        low = msg.lower()
        if any(k in low for k in ("offline", "not currently live", "is not live",
                                   "premier", "upcoming", "no video")):
            return LiveInfo(False, "youtube")
        log.info("youtube live probe: %s", msg[:200])
        return LiveInfo(False, "youtube", error=msg[:300])

    if not info:
        return LiveInfo(False, "youtube")

    live_status = (info.get("live_status") or "").lower()
    is_live = bool(info.get("is_live")) or live_status == "is_live"
    if not is_live:
        return LiveInfo(False, "youtube", author=info.get("channel") or "",
                        title=info.get("title") or "")

    vid = (info.get("id") or "").strip()
    watch = info.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid}" if vid else url)
    return LiveInfo(
        is_live=True,
        platform="youtube",
        session_id=f"yt_{vid}" if vid else f"yt_{hash(watch) & 0xffffffff:x}",
        title=(info.get("title") or "").strip(),
        author=(info.get("channel") or info.get("uploader") or "").strip(),
        cover=(info.get("thumbnail") or ""),
        watch_url=watch,
    )


async def probe_youtube_live(
    channel_ref: str,
    *,
    proxy: str = "",
    state_json: str = "",
    user_agent: str = "",
) -> LiveInfo:
    from ..youtube.fetcher import storage_state_to_cookiefile
    from pathlib import Path

    cookie_file = storage_state_to_cookiefile(state_json) if state_json else ""
    try:
        return await asyncio.to_thread(
            probe_youtube_live_sync,
            channel_ref,
            proxy=proxy,
            cookie_file=cookie_file,
            user_agent=user_agent,
        )
    finally:
        if cookie_file:
            try:
                Path(cookie_file).unlink(missing_ok=True)
            except Exception:
                pass


_DOUYIN_LIVE_PROBE_JS = """() => {
  const hrefs = [...document.querySelectorAll('a[href*="live.douyin.com"]')]
    .map(a => a.href).filter(Boolean);
  const html = document.documentElement ? document.documentElement.innerHTML : '';
  const txt = (document.body && document.body.innerText || '').replace(/\\s+/g, ' ');
  const liveText = /直播中/.test(txt);
  let roomId = '';
  let webRid = '';
  let cover = '';
  const mRoom = html.match(/"(?:room_id|roomId)"\\s*:\\s*"?(\\d{6,})"?/);
  if (mRoom) roomId = mRoom[1];
  const mRid = html.match(/"(?:web_rid|webRid)"\\s*:\\s*"([^"]+)"/);
  if (mRid) webRid = mRid[1];
  // RENDER_DATA / 内嵌 JSON
  try {
    const rd = document.getElementById('RENDER_DATA');
    if (rd && rd.textContent) {
      const decoded = decodeURIComponent(rd.textContent);
      const m1 = decoded.match(/"(?:room_id|roomId)"\\s*:\\s*"?(\\d{6,})"?/);
      if (m1) roomId = roomId || m1[1];
      const m2 = decoded.match(/"(?:web_rid|webRid)"\\s*:\\s*"([^"]+)"/);
      if (m2) webRid = webRid || m2[1];
      if (/\"live_status\"\\s*:\\s*2/.test(decoded) || /\"status\"\\s*:\\s*2/.test(decoded)) {
        // status 2 常见为直播中
      }
      const coverKeys = [
        /"cover"\\s*:\\s*\\{\\s*"url_list"\\s*:\\s*\\[\\s*"(https?:[^"]+)"/,
        /"(?:origin_cover|dynamic_cover|cover_url)"\\s*:\\s*\\{\\s*"url_list"\\s*:\\s*\\[\\s*"(https?:[^"]+)"/,
        /"(?:cover|coverUrl|cover_url|room_cover)"\\s*:\\s*"(https?:[^"]+)"/,
      ];
      for (const re of coverKeys) {
        const m = decoded.match(re);
        if (m && m[1]) { cover = m[1].replace(/\\\\u002F/g, '/'); break; }
      }
    }
  } catch (e) {}
  if (!cover) {
    const og = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
    if (og && og.content) cover = og.content.trim();
  }
  if (!cover) {
    const liveImg = document.querySelector(
      'a[href*="live.douyin.com"] img, [class*="live"] img, [data-e2e*="live"] img');
    if (liveImg && (liveImg.currentSrc || liveImg.src))
      cover = (liveImg.currentSrc || liveImg.src).trim();
  }
  let nick = '';
  const nickEl = document.querySelector('h1, [data-e2e="user-info"] h1, [class*="nickname"]');
  if (nickEl) nick = (nickEl.textContent || '').trim().slice(0, 80);
  return { hrefs, liveText, roomId, webRid, nick, cover, bodyLen: txt.length };
}"""


def _normalize_douyin_live_url(href: str, web_rid: str = "", room_id: str = "") -> str:
    href = (href or "").strip()
    if href and "live.douyin.com" in href:
        return href.split("?")[0].rstrip("/")
    if web_rid:
        return f"https://live.douyin.com/{web_rid}"
    if room_id:
        return f"https://live.douyin.com/{room_id}"
    return ""


async def probe_douyin_live(
    browser,
    identity,
    sec_uid: str,
    *,
    block_media: bool = True,
) -> LiveInfo:
    """打开抖音主页,从直播入口 / 页面数据判断是否开播。"""
    if not sec_uid:
        return LiveInfo(False, "douyin", error="缺少 sec_uid")
    url = f"https://www.douyin.com/user/{sec_uid}"
    page = None
    try:
        page = await browser.new_page(identity, block_media=block_media)
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(2500)
        data = await page.evaluate(_DOUYIN_LIVE_PROBE_JS)
        hrefs = data.get("hrefs") or []
        web_rid = (data.get("webRid") or "").strip()
        room_id = (data.get("roomId") or "").strip()
        live_text = bool(data.get("liveText"))
        watch = ""
        for h in hrefs:
            m = _LIVE_HREF_RE.search(h or "")
            if m:
                watch = _normalize_douyin_live_url(h, m.group(1), room_id)
                web_rid = web_rid or m.group(1)
                break
        if not watch:
            watch = _normalize_douyin_live_url("", web_rid, room_id)

        # 必须有直播入口链接,或明确「直播中」文案 + 可拼出的 live URL
        is_live = bool(hrefs) or (live_text and bool(watch))
        if not is_live:
            try:
                html = await page.content()
                m = _LIVE_HREF_RE.search(html or "")
                if m and live_text:
                    watch = f"https://live.douyin.com/{m.group(1)}"
                    web_rid = m.group(1)
                    is_live = True
            except Exception:
                pass

        if not is_live or not watch:
            return LiveInfo(
                False, "douyin",
                author=(data.get("nick") or "").strip(),
            )

        cover = (data.get("cover") or "").strip()
        # 主页没封面时进直播间补一枪 og:image / 房间封面
        if not cover and watch:
            try:
                await page.goto(watch, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(1200)
                cover = await page.evaluate("""() => {
                  const og = document.querySelector('meta[property="og:image"], meta[name="og:image"]');
                  if (og && og.content) return og.content.trim();
                  const html = document.documentElement ? document.documentElement.innerHTML : '';
                  const m = html.match(/"(?:cover|origin_cover)"\\s*:\\s*\\{\\s*"url_list"\\s*:\\s*\\[\\s*"(https?:[^"]+)"/);
                  if (m) return m[1].replace(/\\\\u002F/g, '/');
                  const img = document.querySelector('video[poster]');
                  if (img && img.getAttribute('poster')) return img.getAttribute('poster');
                  return '';
                }""") or ""
            except Exception as exc:
                log.debug("douyin live cover fetch: %s", exc)

        sid = web_rid or room_id or urlparse(watch).path.strip("/")
        return LiveInfo(
            is_live=True,
            platform="douyin",
            session_id=f"dy_{sid}",
            title="直播中",
            author=(data.get("nick") or "").strip(),
            cover=cover.strip(),
            watch_url=watch,
        )
    except Exception as exc:
        log.warning("douyin live probe failed: %s", exc)
        return LiveInfo(False, "douyin", error=str(exc)[:300])
    finally:
        try:
            if page:
                await page.close()
        except Exception:
            pass
