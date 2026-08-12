"""YouTube 本账号资料:打开 Studio 解析频道 ID / 昵称。"""
from __future__ import annotations

import re
from typing import Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

STUDIO_URL = "https://studio.youtube.com/"
HOME_URL = "https://www.youtube.com/"
_CHANNEL_RE = re.compile(r"/channel/(UC[A-Za-z0-9_-]{22})")


def _is_login_url(url: str) -> bool:
    u = url or ""
    return "accounts.google.com" in u or "ServiceLogin" in u


async def fetch_youtube_self_profile(mgr: BrowserManager, identity: Identity,
                                     timeout_ms: int = 45000
                                     ) -> Tuple[dict, str]:
    """返回 (profile_dict, error)。error==logged_out 表示未登录。
    profile 字段对齐其它平台:nickname / sec_uid(UC…) / douyin_id(@handle) / avatar / follower_count / aweme_count。

    保活策略:先轻量访问 youtube.com 刷新 Cookie,再进 Studio;若偶发跳登录页会重试一次,
    降低误判失效。
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

        channel_id = ""
        m = _CHANNEL_RE.search(url)
        if m:
            channel_id = m.group(1)
        if not channel_id:
            # 侧栏 / 链接里再找
            try:
                hrefs = await page.eval_on_selector_all(
                    "a[href*='/channel/']", "els => els.map(e => e.href)")
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
            "#channel-name",
            "ytcp-channel-name #text",
            "#avatar-btn img",
        ):
            try:
                el = page.locator(sel).first
                if await el.count() == 0:
                    continue
                for attr in ("title", "alt", "aria-label"):
                    v = await el.get_attribute(attr)
                    if v and v.strip():
                        nickname = v.strip()[:60]
                        break
                if not nickname:
                    t = (await el.inner_text(timeout=800) or "").strip()
                    if t:
                        nickname = t[:60]
                if nickname:
                    break
            except Exception:
                continue

        handle = ""
        try:
            # Studio 有时展示 @handle
            txt = await page.inner_text("body")
            hm = re.search(r"(@[\w.-]{2,})", txt or "")
            if hm:
                handle = hm.group(1)
        except Exception:
            pass

        avatar = ""
        try:
            avatar = await page.locator("#avatar-btn img, ytcp-account-item img").first.get_attribute(
                "src", timeout=1500) or ""
        except Exception:
            pass

        if not channel_id and not nickname:
            # Cookie 在但进不了 Studio
            try:
                cookies = await page.context.cookies()
                names = {c["name"] for c in cookies}
                if "LOGIN_INFO" not in names and "SID" not in names:
                    return {}, "logged_out"
            except Exception:
                pass
            return {}, "无法解析 YouTube 频道(请确认已创建频道)"

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
