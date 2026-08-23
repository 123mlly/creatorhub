"""TikTok 本账号资料:打开主站解析 @handle / 昵称。"""
from __future__ import annotations

import re
from typing import Any, Dict, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

HOME_URL = "https://www.tiktok.com/"
PROFILE_ME = "https://www.tiktok.com/foryou"
_HANDLE_RE = re.compile(r"/(@[\w.-]{2,24})")
# 访客进站就会带 tt_chain_token / sid_tt / sid_guard / uid_tt,不能当已登录。
_TT_STRONG_LOGIN_COOKIES = {"sessionid", "sessionid_ss"}
# 近年网页登录常只落护照 Cookie,不一定再写 sessionid
_TT_PASSPORT_COOKIES = {"multi_sids", "sid_ucp_v1", "ssid_ucp_v1"}

_READ_WEB_USER_JS = """() => {
  const out = {uid: '', uniqueId: '', nickname: '', loggedIn: false};
  try {
    const el = document.getElementById('__UNIVERSAL_DATA_FOR_REHYDRATION__');
    const raw = el && el.textContent;
    if (raw) {
      const data = JSON.parse(raw);
      const ctx = data && data.__DEFAULT_SCOPE__
        && data.__DEFAULT_SCOPE__['webapp.app-context'];
      const user = ctx && ctx.user;
      if (user && (user.uid || user.uniqueId)) {
        out.uid = String(user.uid || '');
        out.uniqueId = user.uniqueId || '';
        out.nickname = user.nickname || user.nickName || '';
        out.loggedIn = true;
      }
    }
  } catch (e) {}
  if (!out.loggedIn) {
    try {
      const app = window.SIGI_STATE && window.SIGI_STATE.AppContext;
      const user = app && app.appContext && app.appContext.user;
      if (user && (user.uid || user.uniqueId)) {
        out.uid = String(user.uid || '');
        out.uniqueId = user.uniqueId || '';
        out.nickname = user.nickname || '';
        out.loggedIn = true;
      }
    } catch (e) {}
  }
  const loginBtn = document.querySelector('[data-e2e="top-login-button"]');
  const me = document.querySelector(
    '[data-e2e="inbox-icon"], [data-e2e="nav-profile"], [data-e2e="profile-icon"]');
  if (!out.loggedIn && !loginBtn && me) out.loggedIn = true;
  return out;
}"""


def tiktok_session_ready(cookies) -> bool:
    """Cookie 列表里是否有 TikTok 登录 session(排除访客态)。"""
    found: Dict[str, str] = {}
    for c in cookies or []:
        if "tiktok.com" not in (c.get("domain") or ""):
            continue
        name = c.get("name") or ""
        val = (c.get("value") or "").strip()
        if not val or val in ("0", "undefined", "null"):
            continue
        found[name] = val
    if any(found.get(n) for n in _TT_STRONG_LOGIN_COOKIES):
        return True
    multi = (found.get("multi_sids") or "").replace("%3A", ":")
    if multi and ":" in multi:
        return True
    for n in _TT_PASSPORT_COOKIES:
        if n != "multi_sids" and len(found.get(n) or "") >= 24:
            return True
    return False


async def read_tiktok_web_user(page) -> Dict[str, Any]:
    """从当前页读登录用户。未登录返回 loggedIn=False。"""
    try:
        data = await page.evaluate(_READ_WEB_USER_JS)
    except Exception:
        return {"uid": "", "uniqueId": "", "nickname": "", "loggedIn": False}
    if not isinstance(data, dict):
        return {"uid": "", "uniqueId": "", "nickname": "", "loggedIn": False}
    return {
        "uid": str(data.get("uid") or ""),
        "uniqueId": str(data.get("uniqueId") or ""),
        "nickname": str(data.get("nickname") or ""),
        "loggedIn": bool(data.get("loggedIn")),
    }


def _is_login_url(url: str) -> bool:
    u = (url or "").lower()
    return any(k in u for k in ("/login", "login?", "/signup", "oauth"))


async def fetch_tiktok_self_profile(mgr: BrowserManager, identity: Identity,
                                    timeout_ms: int = 45000
                                    ) -> Tuple[dict, str]:
    """返回 (profile_dict, error)。error==logged_out 表示未登录。
    profile 字段对齐其它平台:nickname / sec_uid / douyin_id(@handle) / avatar。
    """
    page = await mgr.new_page(identity, block_media=True)
    try:
        await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        await page.wait_for_timeout(2500)
        url = page.url or ""
        if _is_login_url(url):
            await page.wait_for_timeout(2000)
            try:
                await page.goto(PROFILE_ME, wait_until="domcontentloaded",
                                timeout=timeout_ms)
                await page.wait_for_timeout(1500)
            except Exception:
                pass
            url = page.url or ""
            if _is_login_url(url):
                return {}, "logged_out"

        web_user = await read_tiktok_web_user(page)
        try:
            cookies = await page.context.cookies()
        except Exception:
            cookies = []
        if not web_user.get("loggedIn") and not tiktok_session_ready(cookies):
            return {}, "logged_out"

        handle = ""
        nickname = (web_user.get("nickname") or "").strip()
        avatar = ""
        uid = (web_user.get("uid") or "").strip()
        if web_user.get("uniqueId"):
            handle = "@" + str(web_user["uniqueId"]).lstrip("@")

        try:
            hrefs = await page.eval_on_selector_all(
                'a[href*="/@"]', "els => els.map(e => e.getAttribute('href') || e.href)")
            for h in hrefs or []:
                m = _HANDLE_RE.search(h or "")
                if m and "/video/" not in (h or "") and "/live" not in (h or ""):
                    handle = m.group(1)
                    break
        except Exception:
            pass

        if handle:
            try:
                await page.goto(f"https://www.tiktok.com/{handle}",
                                wait_until="domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(2000)
            except Exception:
                pass

        for sel in (
            '[data-e2e="user-title"]',
            'h1[data-e2e="user-title"]',
            '[data-e2e="user-subtitle"]',
            'h1',
        ):
            try:
                el = page.locator(sel).first
                if await el.count() == 0:
                    continue
                t = (await el.inner_text(timeout=800) or "").strip()
                if t.startswith("@"):
                    handle = handle or t.split()[0]
                    continue
                if t:
                    nickname = t[:60]
                    break
            except Exception:
                continue

        if not handle:
            try:
                txt = await page.inner_text("body")
                hm = re.search(r"(@[\w.-]{2,24})", txt or "")
                if hm:
                    handle = hm.group(1)
            except Exception:
                pass

        try:
            avatar = await page.locator(
                '[data-e2e="user-avatar"] img, img[alt*="profile"], img[alt*="avatar"]'
            ).first.get_attribute("src", timeout=1500) or ""
        except Exception:
            pass

        if not handle and not nickname:
            return {}, "无法解析 TikTok 账号(请确认已登录)"

        return {
            "nickname": nickname or handle or "TikTok",
            "sec_uid": uid or handle.lstrip("@"),
            "douyin_id": handle if handle.startswith("@") else (f"@{handle}" if handle else ""),
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


def parse_tt_self_user(u: dict) -> dict:
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
