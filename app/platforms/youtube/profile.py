"""YouTube 本账号资料:打开 Studio 解析频道 ID / 昵称。"""
from __future__ import annotations

import re
from typing import Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

STUDIO_URL = "https://studio.youtube.com/"
HOME_URL = "https://www.youtube.com/"
_CHANNEL_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")
# Studio 自有节点里的 @handle(不要扫整页 body,会误抓推荐区别人的号)
_HANDLE_RE = re.compile(r"^@[\w.-]{2,40}$")


def _is_login_url(url: str) -> bool:
    u = url or ""
    return "accounts.google.com" in u or "ServiceLogin" in u


def yt_profile_conflicts(existing_sec_uid: str, parsed: dict) -> bool:
    """已有 UC 频道 id 时,新抓到的 id 不一致 → 半掉线误抓,禁止覆盖。"""
    old = (existing_sec_uid or "").strip()
    new = ((parsed or {}).get("sec_uid") or "").strip()
    return bool(old.startswith("UC") and new.startswith("UC") and old != new)


async def fetch_youtube_self_profile(mgr: BrowserManager, identity: Identity,
                                     timeout_ms: int = 45000
                                     ) -> Tuple[dict, str]:
    """返回 (profile_dict, error)。error==logged_out 表示未登录。
    profile 字段对齐其它平台:nickname / sec_uid(UC…) / douyin_id(@handle) / avatar / follower_count / aweme_count。

    保活策略:先轻量访问 youtube.com 刷新 Cookie,再进 Studio;若偶发跳登录页会重试一次,
    降低误判失效。

    只认 Studio URL / 实体栏上的频道,绝不扫整页第一个 @ 或任意 /channel/ 链接
    (半掉线时推荐区会把多个账号写成同一个频道)。
    """
    page = await mgr.new_page(identity, block_media=True)
    try:
        # 1) 主站轻摸:续 Cookie / 降低直接撞 Studio 登录墙的概率
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(2000)
        except Exception:
            pass

        # 2) Studio 判活 + 读频道
        await page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(3000)
        url = page.url or ""

        if _is_login_url(url):
            # 偶发重定向 / 会话传播延迟:再摸一次主站后重进 Studio
            await page.wait_for_timeout(2500)
            try:
                await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(1500)
            except Exception:
                pass
            await page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=timeout_ms)
            await page.wait_for_timeout(3000)
            url = page.url or ""
            if _is_login_url(url):
                return {}, "logged_out"

        if "channel_create" in url or "create_channel" in url:
            return {}, "logged_out"

        # 频道 ID:优先 Studio 地址栏(…/channel/UC…/…),其次仅实体栏/导航里的自有链接
        channel_id = ""
        m = _CHANNEL_RE.search(url)
        if m:
            channel_id = m.group(1)
        if not channel_id:
            try:
                hrefs = await page.evaluate("""() => {
                  const sels = [
                    'ytcp-entity-name a[href*="/channel/"]',
                    '#entity-name a[href*="/channel/"]',
                    'a#channel-url[href*="/channel/"]',
                    'ytcp-navigation-drawer a[href*="/channel/UC"]',
                    'ytcp-topbar-renderer a[href*="/channel/UC"]',
                  ];
                  const out = [];
                  for (const s of sels) {
                    for (const el of document.querySelectorAll(s)) {
                      if (el.href) out.push(el.href);
                    }
                  }
                  return out;
                }""")
                for h in hrefs or []:
                    m2 = _CHANNEL_RE.search(h or "")
                    if m2:
                        channel_id = m2.group(1)
                        break
            except Exception:
                pass

        nickname = ""
        for sel in (
            "#entity-name",
            "ytcp-entity-name #entity-name",
            "ytcp-channel-name #text",
            "#channel-title",
        ):
            try:
                el = page.locator(sel).first
                if await el.count() == 0:
                    continue
                for attr in ("title", "aria-label"):
                    v = await el.get_attribute(attr)
                    if v and v.strip() and not v.strip().startswith("@"):
                        nickname = v.strip()[:60]
                        break
                if not nickname:
                    t = (await el.inner_text(timeout=800) or "").strip()
                    if t and not t.startswith("@"):
                        nickname = t.split("\n")[0].strip()[:60]
                if nickname:
                    break
            except Exception:
                continue

        handle = ""
        for sel in (
            "#channel-handle",
            "ytcp-channel-name #handle",
            "ytcp-entity-name #entity-handle",
            "#entity-handle",
        ):
            try:
                el = page.locator(sel).first
                if await el.count() == 0:
                    continue
                t = (await el.inner_text(timeout=800) or "").strip()
                if not t:
                    t = (await el.get_attribute("title") or "").strip()
                t = t.split()[0] if t else ""
                if _HANDLE_RE.match(t):
                    handle = t
                    break
            except Exception:
                continue

        avatar = ""
        try:
            avatar = await page.locator(
                "#avatar-btn img, ytcp-entity-name img, ytcp-topbar-renderer img"
            ).first.get_attribute("src", timeout=1500) or ""
        except Exception:
            pass

        # 必须拿到 UC 频道 id。只有昵称/无 id 时宁可失败,也不要拿推荐区脏数据写库。
        if not channel_id:
            try:
                cookies = await page.context.cookies()
                names = {c["name"] for c in cookies}
                if "LOGIN_INFO" not in names and "SID" not in names:
                    return {}, "logged_out"
            except Exception:
                pass
            return {}, "无法解析 YouTube 频道 ID(请确认已创建频道且登录有效)"

        return {
            "nickname": nickname or handle or channel_id or "YouTube",
            "sec_uid": channel_id,
            "douyin_id": handle or channel_id,
            "avatar": avatar,
            "follower_count": 0,
            "aweme_count": 0,
        }, ""
    except Exception as e:
        return {}, f"抓取资料异常: {e!r}"
    finally:
        try:
            await page.close()
        except Exception:
            pass


def parse_yt_self_user(u: dict) -> dict:
    """与其它平台 parse_*_self_user 对齐的薄封装。"""
    if not isinstance(u, dict):
        return {}
    return {
        "nickname": (u.get("nickname") or "").strip(),
        "sec_uid": (u.get("sec_uid") or "").strip(),
        "douyin_id": (u.get("douyin_id") or "").strip(),
        "avatar": (u.get("avatar") or "").strip(),
        "follower_count": int(u.get("follower_count") or 0),
        "aweme_count": int(u.get("aweme_count") or 0),
    }
