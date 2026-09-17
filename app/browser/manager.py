"""Playwright 浏览器管理器(多账号隔离版)。

每个账号一套**独立持久化 context**(launch_persistent_context):
  - 独立 user-data-dir(cookie/localStorage 天然隔离)
  - 独立代理 / UA / 视口 / 时区 / 指纹
常驻这些 context 并按 LRU 控制同时存活数量(省内存)。
登录/发布用同一 profile 的**有头** context(headless=False)。
对应原项目用 chromedp 的角色。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from playwright.async_api import BrowserContext, async_playwright

from ..windowing import (CHROMIUM_WINDOW_CLASSES, bring_window_to_front,
                         capture_window_snapshot)
from .identity import Identity, fingerprint_script


def _default_ms_playwright_dir() -> Optional[Path]:
    home = Path.home()
    for c in (
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
        Path(os.environ.get("LOCALAPPDATA", "") or "") / "ms-playwright",
    ):
        if c and c.is_dir() and any(c.glob("chromium-*")):
            return c
    return None


def sanitize_playwright_browsers_path() -> None:
    """Cursor 终端常注入 PLAYWRIGHT_BROWSERS_PATH=.../cursor-sandbox-cache/...
    该 Chromium 有头启动会立刻退出(TargetClosedError)。强制切回本机缓存。"""
    raw = (os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "").strip()
    bad_markers = ("cursor-sandbox-cache", "/.cursor-sandbox/")
    bad = (not raw) or any(m in raw for m in bad_markers) or not Path(raw).is_dir()
    if not bad and not any(Path(raw).glob("chromium-*")):
        bad = True
    if not bad:
        return
    fallback = _default_ms_playwright_dir()
    if fallback is not None:
        print(f"[browser] rewrite PLAYWRIGHT_BROWSERS_PATH -> {fallback}", flush=True)
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(fallback)
    elif raw:
        print(f"[browser] drop bad PLAYWRIGHT_BROWSERS_PATH={raw!r}", flush=True)
        os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)


sanitize_playwright_browsers_path()

_STEALTH = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
    # 关键:禁止 WebRTC 走非代理 UDP。否则真实 Chromium 会通过 STUN 直接暴露宿主
    # 公网/内网 IP,绕过我们在 HTTP 层设的账号代理 —— 所有号在 WebRTC 上露同一真实
    # 出口 IP,一号一代理的防关联就白做了。这个 flag 让 WebRTC 只认代理路径。
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
]


def _detect_screen_size() -> Optional[Tuple[int, int]]:
    """探测当前主屏逻辑分辨率(CSS 像素; macOS Retina 用点而非物理像素)。"""
    if sys.platform == "darwin":
        # AppKit NSScreen.frame → 逻辑点(与 Playwright viewport 一致)
        try:
            out = subprocess.check_output(
                ["osascript", "-l", "JavaScript", "-e",
                 'ObjC.import("AppKit");'
                 "var f=$.NSScreen.mainScreen.frame;"
                 "f.size.width + \" \" + f.size.height"],
                text=True, timeout=4,
            )
            parts = [int(float(x)) for x in out.split()]
            if len(parts) >= 2 and parts[0] >= 800 and parts[1] >= 600:
                return parts[0], parts[1]
        except Exception:
            pass
        try:
            out = subprocess.check_output(
                ["osascript", "-e",
                 'tell application "Finder" to get bounds of window of desktop'],
                text=True, timeout=4,
            )
            parts = [int(x) for x in re.findall(r"-?\d+", out)]
            if len(parts) >= 4:
                w, h = parts[2] - parts[0], parts[3] - parts[1]
                if w >= 800 and h >= 600:
                    return w, h
        except Exception:
            pass
        return None
    try:
        if sys.platform == "win32":
            import ctypes
            user32 = ctypes.windll.user32  # type: ignore[attr-defined]
            w, h = int(user32.GetSystemMetrics(0)), int(user32.GetSystemMetrics(1))
            if w >= 800 and h >= 600:
                return w, h
        else:
            out = subprocess.check_output(["xdpyinfo"], text=True, timeout=4)
            m = re.search(r"dimensions:\s*(\d+)x(\d+)", out)
            if m:
                w, h = int(m.group(1)), int(m.group(2))
                if w >= 800 and h >= 600:
                    return w, h
    except Exception:
        pass
    return None


def _headed_viewport() -> Tuple[Optional[Dict[str, int]], List[str]]:
    """有头窗口:尽量铺满当前屏幕;探测失败则最大化并禁用固定 viewport。"""
    extra = ["--start-maximized"]
    size = _detect_screen_size()
    if not size:
        return None, extra
    sw, sh = size
    # 预留菜单栏 / Dock / 窗口边框,避免内容区被裁切
    vw = max(1024, min(sw - 40, 3840))
    vh = max(720, min(sh - 100, 2160))
    extra.append(f"--window-size={vw},{vh}")
    print(f"[browser] headed viewport={vw}x{vh} (screen={sw}x{sh})", flush=True)
    return {"width": vw, "height": vh}, extra

# storage_state 里允许注入的 Cookie 字段(playwright add_cookies 接受的键)
_COOKIE_KEYS = ("name", "value", "domain", "path", "expires", "httpOnly", "secure", "sameSite")


def _parse_proxy(s: str) -> Optional[Dict[str, str]]:
    """把 http://user:pass@host:port / socks5://host:port 解析成 Playwright proxy 配置。"""
    s = (s or "").strip()
    if not s:
        return None
    u = urlparse(s if "://" in s else "http://" + s)
    if not u.hostname:
        return None
    scheme = u.scheme or "http"
    port = f":{u.port}" if u.port else ""
    pr: Dict[str, str] = {"server": f"{scheme}://{u.hostname}{port}"}
    if u.username:
        pr["username"] = u.username
    if u.password:
        pr["password"] = u.password
    return pr


def normalize_proxy(s: str) -> str:
    """把用户输入规范成带协议头的代理 URL(httpx 必须带 scheme)。
    裸 host:port -> http://host:port;保留账号密码;无法解析则原样返回。
    例:'1.2.3.4:8080' -> 'http://1.2.3.4:8080'。"""
    s = (s or "").strip()
    if not s or not _parse_proxy(s):
        return s
    u = urlparse(s if "://" in s else "http://" + s)
    scheme = u.scheme or "http"
    auth = ""
    if u.username:
        auth = u.username + (":" + u.password if u.password else "") + "@"
    port = f":{u.port}" if u.port else ""
    return f"{scheme}://{auth}{u.hostname}{port}"


def _sanitize_cookies(cookies: List[dict]) -> List[dict]:
    out = []
    for c in cookies:
        if not c.get("name"):
            continue
        ck = {k: c[k] for k in _COOKIE_KEYS if k in c}
        if ck.get("sameSite") not in ("Strict", "Lax", "None"):
            ck.pop("sameSite", None)
        out.append(ck)
    return out


def _cookie_key(cookie: dict) -> tuple[str, str, str]:
    """Cookie identity used when merging DB storage_state into a profile."""
    return (
        str(cookie.get("name") or ""),
        str(cookie.get("domain") or "").lstrip(".").lower(),
        str(cookie.get("path") or "/"),
    )


def _bridge_cookies(states: tuple, existing: List[dict] | None = None) -> List[dict]:
    """Return usable storage_state cookies missing from the live profile.

    Chromium removes session cookies when a persistent context is closed.  A
    profile can therefore be non-empty while an auth cookie captured in
    ``storage_state`` is already absent.  This is common for Channels'
    ``_finder_auth``/``sessionid`` hand-off from the headed login context to
    the background context.

    Existing profile cookies win so an older DB snapshot never overwrites a
    cookie Chromium has refreshed since the last snapshot.
    """
    existing_keys = {_cookie_key(c) for c in (existing or [])}
    candidates: Dict[tuple[str, str, str], dict] = {}
    now = time.time()
    for raw_state in states or ():
        try:
            cookies = json.loads(raw_state or "{}").get("cookies") or []
        except Exception:
            continue
        for cookie in _sanitize_cookies(cookies):
            key = _cookie_key(cookie)
            if not key[0] or key in existing_keys:
                continue
            expires = cookie.get("expires")
            try:
                if expires is not None and float(expires) > 0 and float(expires) <= now:
                    continue
            except (TypeError, ValueError):
                pass
            candidates[key] = cookie
    return list(candidates.values())


class BrowserManager:
    def __init__(self, default_ua: str, profiles_root: str = "./data/profiles",
                 max_live: int = 6):
        self.default_ua = default_ua
        self.profiles_root = profiles_root
        self.max_live = max(1, max_live)
        self._pw = None
        self._contexts: Dict[Any, BrowserContext] = {}   # key -> 持久化 context
        self._last_used: Dict[Any, float] = {}
        self._locks: Dict[Any, asyncio.Lock] = {}
        self._cv_lock = asyncio.Lock()                   # 保护 context 字典的创建/驱逐
        self._chrome_major: Optional[int] = None         # 实际 Chromium 大版本(启动时探测)

    async def start(self):
        sanitize_playwright_browsers_path()
        if sys.platform == "win32":
            loop = asyncio.get_running_loop()
            if isinstance(loop, asyncio.SelectorEventLoop):
                raise RuntimeError(
                    "Windows 上当前事件循环无法启动 Chromium（uvicorn --reload 会切到 "
                    "SelectorEventLoop）。请改用: uv run python -m app.serve --reload"
                )
        self._pw = await async_playwright().start()
        self._chrome_major = await self._detect_chrome_major()

    async def _detect_chrome_major(self) -> Optional[int]:
        """探测 Playwright 实际内置的 Chromium 大版本。
        账号 UA 池写死了 Chrome 版本,但真实内核可能是另一版本 —— 二者不一致时,
        Sec-CH-UA 请求头 / navigator.userAgentData 由真实内核发出,会和 UA 字符串对不上,
        成为自动化特征。这里读一次真实 UA,后续把账号 UA 的版本号归一到它。"""
        try:
            b = await self._pw.chromium.launch(headless=True, args=_STEALTH)
            try:
                pg = await b.new_page()
                ua = await pg.evaluate("navigator.userAgent")
            finally:
                await b.close()
            m = re.search(r"Chrome/(\d+)", ua or "")
            return int(m.group(1)) if m else None
        except Exception:
            return None

    def _normalize_ua(self, ua: str) -> str:
        """把账号 UA 的 Chrome/Edg 大版本对齐到真实内核版本(未探测到则原样返回)。"""
        if not self._chrome_major or not ua:
            return ua
        v = self._chrome_major
        ua = re.sub(r"Chrome/\d+", f"Chrome/{v}", ua)
        ua = re.sub(r"Edg/\d+", f"Edg/{v}", ua)
        return ua

    def _sec_ch_ua_headers(self, ua: str) -> Optional[Dict[str, str]]:
        """按归一后的 UA 生成一致的 Client Hints 头,覆盖真实内核默认发出的值。"""
        v = self._chrome_major
        if not v:
            return None
        if "Edg/" in ua:
            brands = (f'"Chromium";v="{v}", "Microsoft Edge";v="{v}", '
                      f'"Not?A_Brand";v="99"')
        else:
            brands = (f'"Chromium";v="{v}", "Google Chrome";v="{v}", '
                      f'"Not?A_Brand";v="99"')
        platform = ('"macOS"' if "Mac OS" in ua
                    else '"Linux"' if "Linux" in ua and "Android" not in ua
                    else '"Windows"')
        return {"sec-ch-ua": brands, "sec-ch-ua-mobile": "?0",
                "sec-ch-ua-platform": platform}

    async def stop(self):
        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        if self._pw:
            await self._pw.stop()

    # ── 画像 ──
    def identity_for(self, acc) -> Identity:
        return Identity.from_account(acc, self.profiles_root, self.default_ua)

    def anon_identity(self) -> Identity:
        return Identity(account_id=None,
                        profile_dir=str(Path(self.profiles_root) / "_anon"),
                        ua=self.default_ua)

    def lock_for(self, key) -> asyncio.Lock:
        """每账号串行锁:同一账号同一时刻只允许一个浏览器动作。"""
        return self._locks.setdefault(key, asyncio.Lock())

    # ── 持久化 context ──
    async def _launch_persistent(self, identity: Identity, headless: bool = True
                                 ) -> BrowserContext:
        pdir = Path(identity.profile_dir)
        pdir.mkdir(parents=True, exist_ok=True)
        was_empty = not any(pdir.iterdir())
        ua = self._normalize_ua(identity.ua or self.default_ua)
        # 无头:继续用账号固定视口(指纹隔离);有头:适配当前屏幕,方便登录/Studio 操作
        args = list(_STEALTH)
        if headless:
            viewport: Optional[Dict[str, int]] = {
                "width": identity.viewport_w or 1280,
                "height": identity.viewport_h or 800,
            }
        else:
            viewport, extra = _headed_viewport()
            args.extend(extra)
        kwargs: Dict[str, Any] = dict(
            user_data_dir=str(pdir), headless=headless, args=args,
            user_agent=ua,
            locale=identity.locale or "zh-CN",
            timezone_id=identity.timezone_id or "Asia/Shanghai",
            # geolocation 伪造:坐标与代理 IP 归属地/时区对齐,并预授权定位权限
            # (模拟"用户已允许定位"的真实浏览器),避免 navigator.geolocation 暴露真实位置
            # 或与代理 IP 地区冲突 —— 抖音/视频号 POI 等功能会读它。
            geolocation=identity.geolocation,
            permissions=["geolocation"],
        )
        if viewport is None:
            # 跟随真实窗口大小(配合 --start-maximized)
            kwargs["no_viewport"] = True
        else:
            kwargs["viewport"] = viewport
        proxy = _parse_proxy(identity.proxy)
        if proxy:
            kwargs["proxy"] = proxy
        # 每次启动前纠正(父进程/Cursor 可能重新注入沙箱路径)
        sanitize_playwright_browsers_path()
        try:
            ctx = await self._pw.chromium.launch_persistent_context(**kwargs)
        except Exception as exc:
            name = type(exc).__name__
            if "TargetClosed" in name or "has been closed" in str(exc):
                pw = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
                raise RuntimeError(
                    "Chromium 启动后立即退出(常见于 Cursor 沙箱浏览器路径)。"
                    f" 当前 PLAYWRIGHT_BROWSERS_PATH={pw!r}。"
                    "请在系统终端执行: "
                    "unset PLAYWRIGHT_BROWSERS_PATH; "
                    "export PLAYWRIGHT_BROWSERS_PATH=\"$HOME/Library/Caches/ms-playwright\"; "
                    "uv run playwright install chromium; "
                    "然后重启服务。"
                    f" 原始错误: {exc!r}"
                ) from exc
            raise
        # Client Hints 与归一后的 UA 保持一致(否则内核按真实版本发 Sec-CH-UA,和 UA 打架)
        sec = self._sec_ch_ua_headers(ua)
        if sec:
            try:
                await ctx.set_extra_http_headers(sec)
            except Exception:
                pass
        if identity.fp_seed:
            try:
                await ctx.add_init_script(fingerprint_script(identity.fp_seed, ua))
            except Exception:
                pass
        # 登录态桥接:
        # 1) 全新 profile 注入 DB storage_state，兼容旧账号迁移；
        # 2) 非空 profile 也补回“磁盘中缺失”的 Cookie。Chromium 关闭 context
        #    时会丢弃 session cookie，视频号刚扫码成功后从有头切到无头 context
        #    正好会经过这条路径。只补缺失项，不覆盖 profile 中更新过的 Cookie。
        if identity.bridge_states:
            try:
                existing = [] if was_empty else await ctx.cookies()
                cookies = _bridge_cookies(identity.bridge_states, existing)
                if cookies:
                    await ctx.add_cookies(cookies)
            except Exception:
                pass
        return ctx

    async def _evict_if_needed(self):
        """常驻 context 超过上限时,关掉最久未用且当前未被锁占用的那个。"""
        while len(self._contexts) >= self.max_live:
            cands = [k for k in self._contexts
                     if not (k in self._locks and self._locks[k].locked())]
            if not cands:
                break
            victim = min(cands, key=lambda k: self._last_used.get(k, 0))
            ctx = self._contexts.pop(victim, None)
            self._last_used.pop(victim, None)
            if ctx:
                try:
                    await ctx.close()
                except Exception:
                    pass

    async def context_for(self, identity: Identity) -> BrowserContext:
        """取(或惰性创建)账号专属常驻 context。"""
        key = identity.key
        async with self._cv_lock:
            ctx = self._contexts.get(key)
            if ctx is None:
                await self._evict_if_needed()
                ctx = await self._launch_persistent(identity, headless=True)
                self._contexts[key] = ctx
            self._last_used[key] = time.time()
            return ctx

    async def new_page(self, identity: Identity, block_media: bool = False):
        """从账号常驻 context 开一个新 page(可屏蔽图片/视频/字体)。用完请 page.close()。"""
        ctx = await self.context_for(identity)
        page = await ctx.new_page()
        if block_media:
            async def _route(route):
                if route.request.resource_type in ("image", "media", "font"):
                    await route.abort()
                else:
                    await route.continue_()
            await page.route("**/*", _route)
        return page

    async def close_context(self, key):
        async with self._cv_lock:
            ctx = self._contexts.pop(key, None)
            self._last_used.pop(key, None)
        if ctx:
            try:
                await ctx.close()
            except Exception:
                pass

    async def open_headed(self, identity: Identity) -> BrowserContext:
        """登录/发布:先关掉该账号常驻无头 context(同一 profile 不能并存),
        再开同 profile 的有头 context。调用方用完务必 await ctx.close()(关闭即落盘 Cookie)。"""
        snapshot = capture_window_snapshot(CHROMIUM_WINDOW_CLASSES)
        await self.close_context(identity.key)
        ctx = await self._launch_persistent(identity, headless=False)
        await asyncio.to_thread(bring_window_to_front, snapshot,
                                CHROMIUM_WINDOW_CLASSES, "", 1.5)
        return ctx


# 各平台 Cookie 顶域(子域如 creator./edith. 都吃顶域 cookie,一个就够)
_COOKIE_DOMAIN = {
    "douyin": ".douyin.com",
    "xhs": ".xiaohongshu.com",
    "kuaishou": ".kuaishou.com",
    "shipinhao": ".weixin.qq.com",   # 视频号:finder 登录态(_finder_auth/sessionid)挂在 .weixin.qq.com
    "youtube": ".youtube.com",
    "tiktok": ".tiktok.com",
}

# Google 账号 Cookie 挂在 .google.com,YouTube 业务 Cookie 挂在 .youtube.com
_GOOGLE_AUTH_COOKIES = {
    "SID", "HSID", "SSID", "APISID", "SAPISID",
    "__Secure-1PSID", "__Secure-3PSID", "__Secure-1PAPISID", "__Secure-3PAPISID",
    "__Secure-1PSIDTS", "__Secure-3PSIDTS", "SIDCC",
}


def cookie_string_to_state(cookie_str: str, platform: str = "douyin") -> str:
    """把粘贴的 Cookie 串转成 Playwright storage_state JSON(兜底登录用)。"""
    default_domain = _COOKIE_DOMAIN.get(platform, ".douyin.com")
    cookies: List[Dict[str, Any]] = []
    for part in cookie_str.strip().split(";"):
        if "=" not in part:
            continue
        k, v = part.strip().split("=", 1)
        if not k:
            continue
        name = k.strip()
        value = v.strip()
        # YouTube:Google 认证 Cookie 需同时挂 .google.com 与 .youtube.com,
        # 否则 yt-dlp 请求 youtube.com 时带不上 SID/PSID,会被当成未登录打 bot。
        if platform == "youtube" and name in _GOOGLE_AUTH_COOKIES:
            for domain in (".google.com", ".youtube.com"):
                cookies.append({
                    "name": name, "value": value,
                    "domain": domain, "path": "/",
                })
            continue
        cookies.append({
            "name": name, "value": value,
            "domain": default_domain, "path": "/",
        })
    return json.dumps({"cookies": cookies, "origins": []})
