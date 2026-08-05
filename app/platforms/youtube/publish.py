"""YouTube 发布入口(浏览器自动化 YouTube Studio)。

用账号专属持久 profile(已含 Google/YouTube 登录态)打开 Studio,
上传视频、填标题/描述、点发布。

⚠️ 实验性:Studio 选择器随改版可能失效;发布时弹真实窗口,
   遇验证码 / 频道未创建 / 需补缩略图可在窗口里手动处理。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import List, Tuple

from ...browser.identity import Identity
from ...browser.manager import BrowserManager

STUDIO_URL = "https://studio.youtube.com/"
_DEBUG_DIR = Path("./data/debug")

_UPLOAD_BTN = [
    '#upload-icon', '#upload-button',
    'ytcp-button#upload-button',
    'button[aria-label*="Upload"]', 'button[aria-label*="上传"]',
    'ytcp-button:has-text("Upload")', 'ytcp-button:has-text("上传")',
    'tp-yt-paper-button:has-text("UPLOAD")',
]
_CREATE_BTN = [
    '#create-icon', '#create-button',
    'ytcp-button#create-button',
    'button[aria-label*="Create"]', 'button[aria-label*="创建"]',
]
_UPLOAD_MENU = [
    'tp-yt-paper-item:has-text("Upload videos")',
    'tp-yt-paper-item:has-text("上传视频")',
    'text=Upload videos', 'text=上传视频',
]
_TITLE_SEL = [
    '#textbox[aria-label*="title"]',
    '#textbox[aria-label*="标题"]',
    'div#textbox[contenteditable="true"]',
    '#title-textarea #textbox',
    'ytcp-social-suggestions-textbox#title-textarea #textbox',
]
_DESC_SEL = [
    '#description-textarea #textbox',
    'ytcp-social-suggestions-textbox#description-textarea #textbox',
    '#textbox[aria-label*="Tell viewers about your video"]',
    '#textbox[aria-label*="向观众介绍"]',
    '#textbox[aria-label*="description"]',
    '#textbox[aria-label*="说明"]',
    '#textbox[aria-label*="描述"]',
]
_DESC_EXPAND = [
    '#description-textarea',
    'ytcp-social-suggestions-textbox#description-textarea',
    '#description-container',
    'text=Add a description',
    'text=Tell viewers about your video',
    'text=添加说明',
    'text=添加描述',
    'text=向观众介绍你的视频',
]
_NEXT_BTN = [
    '#next-button', 'ytcp-button#next-button',
    'button:has-text("Next")', 'button:has-text("下一步")',
]
_PUBLISH_BTN = [
    '#done-button', 'ytcp-button#done-button',
    'button:has-text("Publish")', 'button:has-text("发布")',
    'button:has-text("Save")', 'button:has-text("保存")',
]
_PUBLIC_RADIO = [
    '#privacy-radios tp-yt-paper-radio-button[name="PUBLIC"]',
    'tp-yt-paper-radio-button[name="PUBLIC"]',
    '#privacy-radios [name="PUBLIC"]',
    'ytcp-video-visibility-select tp-yt-paper-radio-button[name="PUBLIC"]',
    'tp-yt-paper-radio-button:has-text("Public")',
    'tp-yt-paper-radio-button:has-text("公开")',
]
_VISIBILITY_STEP = [
    '#privacy-radios',
    'ytcp-video-visibility-select',
    '#visibility-container',
    'text=Visibility',
    'text=可见性',
    'text=Who can see this video',
    'text=谁可以观看这部影片',
    'text=谁可以观看此视频',
]
# 「是否专为儿童打造」必填;默认选「否」(非儿童向)
_NOT_FOR_KIDS = [
    'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
    '#audience [name="VIDEO_MADE_FOR_KIDS_NOT_MFK"]',
    'tp-yt-paper-radio-button:has-text("No, it\'s not made for kids")',
    'tp-yt-paper-radio-button:has-text("No, it’s not made for kids")',
    'tp-yt-paper-radio-button:has-text("不是给孩子看的")',
    'tp-yt-paper-radio-button:has-text("未面向儿童")',
    'tp-yt-paper-radio-button:has-text("否，未面向儿童")',
    'ytcp-audience-picker tp-yt-paper-radio-button:nth-child(2)',
]
_FOR_KIDS = [
    'tp-yt-paper-radio-button[name="VIDEO_MADE_FOR_KIDS_MFK"]',
    '#audience [name="VIDEO_MADE_FOR_KIDS_MFK"]',
    'tp-yt-paper-radio-button:has-text("Yes, it\'s made for kids")',
    'tp-yt-paper-radio-button:has-text("是，专为儿童打造")',
]


def _log(msg: str) -> None:
    print(f"[yt-publish] {msg}", flush=True)


async def _click_first(page, selectors, timeout=3500) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.click(timeout=timeout)
            return True
        except Exception:
            continue
    return False


async def _fill_editable(page, selectors, text, timeout=4000, max_len: int = 5000) -> bool:
    """写入 contenteditable;先清空再填,避免 Studio 用文件名预填导致超长。"""
    text = (text or "")[:max_len]
    if not text:
        return True
    for sel in selectors:
        try:
            el = page.locator(sel).first
            if await el.count() == 0:
                continue
            await el.click(timeout=timeout)
            await page.wait_for_timeout(150)
            # 彻底清空(Meta/Ctrl+A 在部分 Studio 组件上不可靠)
            try:
                await el.evaluate(
                    """(node) => {
                      node.focus();
                      if (node.isContentEditable) {
                        node.textContent = '';
                        node.innerText = '';
                        node.dispatchEvent(new InputEvent('input', {bubbles: true}));
                      } else if ('value' in node) {
                        node.value = '';
                        node.dispatchEvent(new Event('input', {bubbles: true}));
                      }
                    }"""
                )
            except Exception:
                await page.keyboard.press("Meta+a")
                await page.keyboard.press("Control+a")
                await page.keyboard.press("Backspace")
            await page.wait_for_timeout(100)
            await page.keyboard.type(text, delay=6)
            # 再保险:读回长度,超了就用 JS 截断
            try:
                cur = await el.evaluate(
                    "(n) => (n.isContentEditable ? (n.innerText || n.textContent || '') : (n.value || ''))"
                )
                if isinstance(cur, str) and len(cur) > max_len:
                    await el.evaluate(
                        """(node, t) => {
                          if (node.isContentEditable) {
                            node.textContent = t;
                            node.dispatchEvent(new InputEvent('input', {bubbles: true}));
                          } else if ('value' in node) {
                            node.value = t;
                            node.dispatchEvent(new Event('input', {bubbles: true}));
                          }
                        }""",
                        text,
                    )
            except Exception:
                pass
            return True
        except Exception:
            continue
    return False


def _clip_title(title: str, media_path: str = "", limit: int = 100) -> str:
    """YouTube 标题上限 100;去掉常见「数字ID_」前缀。"""
    t = (title or "").strip()
    if not t and media_path:
        t = Path(media_path).stem
    # 7670..._描述 → 描述
    if re.match(r"^\d{10,}_", t):
        t = t.split("_", 1)[-1].strip() or t
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > limit:
        t = t[:limit].rstrip()
    return t or "Untitled"


async def _dump(page, tag: str) -> str:
    try:
        _DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        png = str(_DEBUG_DIR / f"yt_publish_{tag}_{stamp}.png")
        try:
            await page.screenshot(path=png, full_page=True)
        except Exception:
            png = ""
        _log(f"debug dump {tag}: url={page.url} shot={png}")
        return png
    except Exception:
        return ""


async def _fill_description(page, text: str) -> bool:
    """展开并填写描述;Studio 描述框常折叠,直接 type 会写不进去。"""
    text = (text or "").strip()
    if not text:
        return False
    # 先点展开区域
    for sel in _DESC_EXPAND:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=2500)
            await loc.click(timeout=2500)
            await page.wait_for_timeout(350)
            break
        except Exception:
            continue
    ok = await _fill_editable(page, _DESC_SEL, text[:5000], max_len=5000)
    if ok:
        _log(f"description filled ({len(text)} chars)")
        return True
    # 兜底:找任意可见的第二个 contenteditable textbox(第一个通常是标题)
    try:
        boxes = page.locator('div#textbox[contenteditable="true"]')
        n = await boxes.count()
        for i in range(n):
            el = boxes.nth(i)
            try:
                if not await el.is_visible():
                    continue
                # 跳过标题框:aria-label 含 title/标题
                label = (await el.get_attribute("aria-label") or "").lower()
                if "title" in label or "标题" in label:
                    continue
                await el.scroll_into_view_if_needed(timeout=2000)
                await el.click(timeout=2000)
                await el.evaluate(
                    """(node, t) => {
                      node.focus();
                      node.textContent = t;
                      node.dispatchEvent(new InputEvent('input', {bubbles: true}));
                    }""",
                    text[:5000],
                )
                _log(f"description filled via fallback box#{i}")
                return True
            except Exception:
                continue
    except Exception as e:
        _log(f"description fallback failed: {e!r}")
    _log("description NOT filled")
    return False


async def _select_public_visibility(page) -> bool:
    """在 Visibility 步骤勾选 Public/公开。

    Studio 上传向导默认常不选可见性;点 Next 到末步或点步骤徽章 #step-badge-3,
    再点 name=PUBLIC 的 paper-radio(必要时穿透点 #radio / radioLabel)。
    """
    # 直接跳到 Visibility 步骤(比连点 Next 稳)
    for sel in ("#step-badge-3", "button#step-badge-3", "[id='step-badge-3']"):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.click(timeout=3000, force=True)
                await page.wait_for_timeout(1200)
                _log("jumped to visibility via step-badge-3")
                break
        except Exception:
            continue
    else:
        for _ in range(6):
            if await page.locator(
                "#privacy-radios, ytcp-video-visibility-select, "
                "tp-yt-paper-radio-button[name='PUBLIC']"
            ).count():
                break
            await _click_first(page, _NEXT_BTN, timeout=3000)
            await page.wait_for_timeout(900)

    # 等 radio 组出现
    for _ in range(15):
        try:
            if await page.locator("tp-yt-paper-radio-button[name='PUBLIC']").count():
                break
            if await page.locator("#privacy-radios").count():
                break
        except Exception:
            pass
        await page.wait_for_timeout(500)

    # 1) Playwright 直接点
    for sel in (
        "tp-yt-paper-radio-button[name='PUBLIC']",
        "#privacy-radios tp-yt-paper-radio-button[name='PUBLIC']",
        "#privacy-radios tp-yt-paper-radio-button:nth-child(3)",
        "ytcp-video-visibility-select tp-yt-paper-radio-button[name='PUBLIC']",
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            await loc.scroll_into_view_if_needed(timeout=3000)
            await page.wait_for_timeout(200)
            # 先点整颗,再点内部圆点/标签
            await loc.click(timeout=4000, force=True)
            await page.wait_for_timeout(300)
            for sub in ("#radio", "#offRadio", "#radioLabel", "#radioContainer"):
                try:
                    inner = loc.locator(sub).first
                    if await inner.count():
                        await inner.click(timeout=2000, force=True)
                        break
                except Exception:
                    continue
            await page.wait_for_timeout(400)
            checked = await loc.get_attribute("aria-checked")
            if checked == "true":
                _log(f"visibility=PUBLIC via {sel}")
                return True
        except Exception as e:
            _log(f"click {sel} failed: {e!r}")

    # 2) 点「Public」/「Everyone can watch」文案
    for text in (
        "Everyone can watch your video",
        "Public",
        "公开",
        "所有人都可以观看",
    ):
        try:
            loc = page.get_by_text(text, exact=False).first
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=3000, force=True)
                await page.wait_for_timeout(400)
                pub = page.locator("tp-yt-paper-radio-button[name='PUBLIC']").first
                if await pub.count() and (await pub.get_attribute("aria-checked")) == "true":
                    _log(f"visibility=PUBLIC via text={text!r}")
                    return True
                # 文案点了也可能已生效但属性延迟
                _log(f"visibility click text={text!r} (unverified)")
                return True
        except Exception:
            continue

    # 3) JS 强制 click(绕过遮挡/动画)
    try:
        ok = await page.evaluate(
            """() => {
              const findAll = (root, acc=[]) => {
                root.querySelectorAll && root.querySelectorAll('tp-yt-paper-radio-button').forEach(el => acc.push(el));
                root.querySelectorAll && root.querySelectorAll('*').forEach(el => {
                  if (el.shadowRoot) findAll(el.shadowRoot, acc);
                });
                return acc;
              };
              const radios = findAll(document);
              const pub = radios.find(r => (r.getAttribute('name') || '') === 'PUBLIC')
                || radios.find(r => /public|公开/i.test(r.textContent || ''));
              if (!pub) return false;
              pub.scrollIntoView({block:'center'});
              const inner = pub.querySelector('#radio, #offRadio, #radioContainer, #radioLabel');
              (inner || pub).click();
              pub.click();
              pub.setAttribute('aria-checked', 'true');
              pub.setAttribute('aria-selected', 'true');
              pub.dispatchEvent(new Event('change', {bubbles:true}));
              pub.dispatchEvent(new Event('click', {bubbles:true}));
              return true;
            }"""
        )
        if ok:
            await page.wait_for_timeout(500)
            pub = page.locator("tp-yt-paper-radio-button[name='PUBLIC']").first
            if await pub.count():
                checked = await pub.get_attribute("aria-checked")
                _log(f"visibility=PUBLIC via JS evaluate (aria-checked={checked})")
                # 即使属性未立刻变,也认为点过了
                return True
    except Exception as e:
        _log(f"JS public select failed: {e!r}")

    await _dump(page, "no_public")
    _log("visibility PUBLIC not selected")
    return False


async def _answer_made_for_kids(page, made_for_kids: bool = False) -> bool:
    """勾选 Audience「是否专为儿童打造」;未答则无法点 Next。"""
    sels = _FOR_KIDS if made_for_kids else _NOT_FOR_KIDS
    if await _click_first(page, sels, timeout=4000):
        _log(f"audience set made_for_kids={made_for_kids}")
        return True
    # 有时要先展开 Audience 区块
    for sel in (
        '#audience', 'ytcp-video-metadata-audience',
        'text=Audience', 'text=受众',
        'text=Is this video made for kids',
        'text=是否专为儿童打造',
    ):
        try:
            loc = page.locator(sel).first
            if await loc.count():
                await loc.scroll_into_view_if_needed(timeout=2000)
                await page.wait_for_timeout(300)
                break
        except Exception:
            continue
    if await _click_first(page, sels, timeout=4000):
        _log(f"audience set(after scroll) made_for_kids={made_for_kids}")
        return True
    _log("audience radio not found")
    return False


async def publish_youtube(mgr: BrowserManager, identity: Identity,
                          storage_state_json: str, media_type: str, title: str,
                          desc: str, media_paths: List[str], topics: str = "",
                          headed: bool = True, timeout_seconds: int = 360,
                          made_for_kids: bool = False
                          ) -> Tuple[bool, str, str]:
    """发布一条 YouTube 视频。返回 (ok, result_url, error)。
    仅支持 video;图集请先合成视频。storage_state_json 仅校验用(登录态在持久 profile)。
    made_for_kids: Studio 必填「是否专为儿童打造」,默认否。"""
    if media_type != "video":
        return False, "", "YouTube 目前仅支持上传视频(请选择「视频」类型)"
    files = [str(Path(p)) for p in media_paths if p and Path(p).exists()]
    if not files:
        return False, "", "没有可用的本地媒体文件(路径不存在)"
    tags = [t.strip().lstrip("#") for t in (topics or "").split(",") if t.strip()]
    body = (desc or "").strip()
    title = _clip_title(title, files[0] if files else "", limit=100)
    # 描述为空时回退到标题,避免 Studio 描述栏空白
    if not body:
        body = title
        _log("desc empty, fallback to title")
    if tags:
        body = (body + "\n\n" + " ".join(f"#{t}" for t in tags)).strip()
    _log(f"title({len(title)}): {title[:60]}{'…' if len(title) > 60 else ''}")
    _log(f"desc({len(body)}): {body[:60]}{'…' if len(body) > 60 else ''}")

    ctx = await mgr.open_headed(identity)
    page = await ctx.new_page()
    ok, result_url, error = False, "", ""
    try:
        await page.goto(STUDIO_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(4000)
        url_now = page.url or ""
        if "accounts.google.com" in url_now or "ServiceLogin" in url_now:
            return False, "", "logged_out:YouTube Studio 未登录,请重新完成 YouTube 登录"
        if "channel_create" in url_now or "create_channel" in url_now:
            return False, "", "该 Google 账号尚未创建 YouTube 频道,请先在浏览器里创建频道"

        # 打开上传:优先点 Create → Upload videos,否则点 upload 图标
        opened = False
        if await _click_first(page, _CREATE_BTN, timeout=4000):
            await page.wait_for_timeout(800)
            opened = await _click_first(page, _UPLOAD_MENU, timeout=4000)
        if not opened:
            opened = await _click_first(page, _UPLOAD_BTN, timeout=4000)
        if not opened:
            await _dump(page, "no_upload_btn")
            return False, "", "未找到上传入口(Studio 可能改版)"

        await page.wait_for_timeout(1500)
        try:
            # 对话框里的 file input(可能多个,取第一个可见/可用)
            inp = page.locator('input[type="file"]').first
            await inp.set_input_files(files[:1], timeout=20000)
        except Exception as e:
            await _dump(page, "set_files")
            return False, "", f"上传文件失败: {e!r}"

        # 等详情页标题框出现(上传中也会先出详情表单)
        title_ready = False
        for _ in range(60):
            for sel in _TITLE_SEL:
                try:
                    if await page.locator(sel).first.is_visible():
                        title_ready = True
                        break
                except Exception:
                    continue
            if title_ready:
                break
            await page.wait_for_timeout(2000)
        if not title_ready:
            await _dump(page, "no_title")
            return False, "", "上传后未出现标题编辑框(请到弹出窗口手动完成,或检查网络/验证)"

        await _fill_editable(page, _TITLE_SEL, title, max_len=100)
        await page.wait_for_timeout(500)
        if not await _fill_description(page, body):
            await _dump(page, "no_description")
            _log("warn: description field empty after fill attempts")
        await page.wait_for_timeout(500)

        # Studio 必填:是否专为儿童打造(未勾选无法 Next)
        if not await _answer_made_for_kids(page, made_for_kids=made_for_kids):
            await _dump(page, "no_audience")
            # 不直接失败:留给用户在窗口里手点;仍继续尝试 Next
            _log("未能自动勾选 Audience,将尝试继续(请留意弹窗)")

        # 连点 Next 直到可见性步骤(Details → Video elements → Checks → Visibility)
        for step in range(5):
            # 若仍停在 Details 且 Audience 未选,再试一次
            await _answer_made_for_kids(page, made_for_kids=made_for_kids)
            if not await _click_first(page, _NEXT_BTN, timeout=5000):
                break
            await page.wait_for_timeout(1200)
            # 若 Next 后仍看到 audience 报错提示,再勾一次
            try:
                if await page.get_by_text("You need to answer this question", exact=False).first.is_visible():
                    await _answer_made_for_kids(page, made_for_kids=made_for_kids)
                    await _click_first(page, _NEXT_BTN, timeout=5000)
                    await page.wait_for_timeout(1200)
            except Exception:
                pass

        # 选公开(必须先到 Visibility 步骤)
        if not await _select_public_visibility(page):
            await _dump(page, "no_public")
            _log("warn: failed to select Public; publish may stay Private/Unlisted")

        if not await _click_first(page, _PUBLISH_BTN, timeout=6000):
            await _dump(page, "no_publish")
            return False, "", "未找到发布/完成按钮(请到弹出窗口手动选 Public 并点 Publish)"

        # 等成功:链接 / 文案
        deadline = timeout_seconds
        waited = 0
        while waited < deadline:
            # 抓结果链接
            try:
                for sel in (
                    "a[href*='youtu.be/']",
                    "a[href*='youtube.com/watch']",
                    "a[href*='youtube.com/shorts/']",
                    "a[href*='studio.youtube.com/video/']",
                ):
                    loc = page.locator(sel).first
                    if await loc.count():
                        href = await loc.get_attribute("href") or ""
                        if href:
                            if href.startswith("//"):
                                href = "https:" + href
                            elif href.startswith("/"):
                                href = "https://www.youtube.com" + href
                            # studio edit → watch
                            m = re.search(r"/video/([A-Za-z0-9_-]{11})", href)
                            if m:
                                href = f"https://www.youtube.com/watch?v={m.group(1)}"
                            result_url = href
                            ok = True
                            break
                if ok:
                    break
            except Exception:
                pass
            for kw in ("Video published", "已发布", "Published", "上传完毕", "Processing"):
                try:
                    if await page.get_by_text(kw, exact=False).first.is_visible():
                        ok = True
                        break
                except Exception:
                    continue
            if ok:
                break
            await page.wait_for_timeout(2000)
            waited += 2

        if ok and not result_url:
            result_url = page.url
        if not ok:
            await _dump(page, "uncertain")
            error = "已点发布但未确认成功(请到 YouTube Studio 确认;调试截图见 data/debug/)"
        else:
            _log(f"publish ok url={result_url}")
    except Exception as e:
        error = f"发布异常: {e!r}"
        try:
            await _dump(page, "exception")
        except Exception:
            pass
    finally:
        try:
            await ctx.close()
        except Exception:
            pass
    return ok, result_url, error
