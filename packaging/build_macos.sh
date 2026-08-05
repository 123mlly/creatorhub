#!/usr/bin/env bash
# 在 macOS 上构建 CreatorHub.app 与 CreatorHub.dmg
# 用法: ./packaging/build_macos.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

DIST="$ROOT/dist"
BUILD="$ROOT/build"
BUNDLE_BROWSERS="$ROOT/packaging/ms-playwright"
DMG_NAME="CreatorHub"
APP_NAME="CreatorHub.app"

log() { printf '[build] %s\n' "$*"; }

if [[ "$(uname -s)" != "Darwin" ]]; then
  log "仅支持 macOS"
  exit 1
fi

log "同步依赖(+ pyinstaller)…"
uv sync --extra package

log "准备 Playwright Chromium → packaging/ms-playwright"
mkdir -p "$BUNDLE_BROWSERS"

# 只打入当前 playwright 包声明的 revision，避免混入 Cursor/其它版本导致体积膨胀与 codesign 失败
CHROMIUM_REV="$(uv run python -c 'import json; from pathlib import Path; import playwright; d=json.loads((Path(playwright.__file__).parent/"driver/package/browsers.json").read_text()); print(next(b["revision"] for b in d["browsers"] if b["name"]=="chromium"))')"
FFMPEG_REV="$(uv run python -c 'import json; from pathlib import Path; import playwright; d=json.loads((Path(playwright.__file__).parent/"driver/package/browsers.json").read_text()); print(next((b["revision"] for b in d["browsers"] if b["name"]=="ffmpeg"), ""))')"
if [[ -z "${CHROMIUM_REV}" ]]; then
  log "错误: 无法从 playwright browsers.json 读取 chromium revision"
  exit 1
fi
log "目标浏览器: chromium-${CHROMIUM_REV} ffmpeg-${FFMPEG_REV:-?}"

# 清掉无关版本，避免误打进包
shopt -s nullglob
for d in "$BUNDLE_BROWSERS"/*; do
  [[ -d "$d" ]] || continue
  name="$(basename "$d")"
  case "$name" in
    "chromium-${CHROMIUM_REV}"|"ffmpeg-${FFMPEG_REV}") ;;
    chromium_headless_shell-*) rm -rf "$d"; log "移除 $name（不打包 headless_shell）" ;;
    chromium-*|ffmpeg-*) rm -rf "$d"; log "移除多余 $name" ;;
  esac
done
shopt -u nullglob

# 优先复用本机缓存，避免重复下载
CACHE_CANDIDATES=(
  "${PLAYWRIGHT_BROWSERS_PATH:-}"
  "$HOME/Library/Caches/ms-playwright"
  "$HOME/.cache/ms-playwright"
)

ensure_browser_dir() {
  local name="$1"
  [[ -n "$name" ]] || return 0
  [[ -d "$BUNDLE_BROWSERS/$name" ]] && return 0
  local c
  for c in "${CACHE_CANDIDATES[@]}"; do
    [[ -n "$c" && -d "$c/$name" ]] || continue
    log "复制 $name ← $c"
    cp -R "$c/$name" "$BUNDLE_BROWSERS/$name"
    return 0
  done
  return 1
}

need_install=0
ensure_browser_dir "chromium-${CHROMIUM_REV}" || need_install=1
if [[ -n "${FFMPEG_REV}" ]]; then
  ensure_browser_dir "ffmpeg-${FFMPEG_REV}" || need_install=1
fi

if [[ "$need_install" -eq 1 ]]; then
  log "缓存中缺 Chromium/ffmpeg，执行 playwright install chromium…"
  export PLAYWRIGHT_BROWSERS_PATH="$BUNDLE_BROWSERS"
  uv run playwright install chromium
fi

if [[ ! -d "$BUNDLE_BROWSERS/chromium-${CHROMIUM_REV}" ]]; then
  log "错误: 未能准备 chromium-${CHROMIUM_REV}，请先 uv run playwright install chromium"
  exit 1
fi

log "Chromium 就绪:"
du -sh "$BUNDLE_BROWSERS"/* 2>/dev/null || true

log "PyInstaller 打包（Chromium 稍后拷入，避免 codesign 失败）…"
rm -rf "$DIST/$APP_NAME" "$DIST/CreatorHub" "$BUILD"
# 清掉可能损坏的 Chromium bincache，避免上次失败残留干扰
find "${HOME}/Library/Application Support/pyinstaller" \
  -type d -name 'ms-playwright' -path '*bincache*' \
  -prune -exec rm -rf {} + 2>/dev/null || true
uv run pyinstaller \
  --noconfirm \
  --clean \
  --distpath "$DIST" \
  --workpath "$BUILD" \
  "$ROOT/packaging/creatorhub.spec"

if [[ ! -d "$DIST/$APP_NAME" ]]; then
  log "错误: 未生成 $DIST/$APP_NAME"
  exit 1
fi

# 把浏览器拷进 .app：PyInstaller 6 把 datas 放在 Contents/Resources，
# Contents/Frameworks（_MEIPASS）里是指向 Resources 的符号链接。
RESOURCES_DIR="$DIST/$APP_NAME/Contents/Resources"
FRAMEWORKS_DIR="$DIST/$APP_NAME/Contents/Frameworks"
if [[ ! -d "$RESOURCES_DIR" || ! -f "$RESOURCES_DIR/base_library.zip" ]]; then
  log "错误: 找不到 $RESOURCES_DIR/base_library.zip，无法拷入 Chromium"
  exit 1
fi
log "拷入 Chromium → $RESOURCES_DIR/ms-playwright"
rm -rf "$RESOURCES_DIR/ms-playwright"
mkdir -p "$RESOURCES_DIR/ms-playwright"
cp -R "$BUNDLE_BROWSERS/chromium-${CHROMIUM_REV}" "$RESOURCES_DIR/ms-playwright/"
if [[ -n "${FFMPEG_REV}" && -d "$BUNDLE_BROWSERS/ffmpeg-${FFMPEG_REV}" ]]; then
  cp -R "$BUNDLE_BROWSERS/ffmpeg-${FFMPEG_REV}" "$RESOURCES_DIR/ms-playwright/"
fi
if [[ -d "$FRAMEWORKS_DIR" ]]; then
  rm -rf "$FRAMEWORKS_DIR/ms-playwright"
  ln -sfn ../Resources/ms-playwright "$FRAMEWORKS_DIR/ms-playwright"
fi
# 同步 onedir 产物（未包进 .app 时也可直接跑 dist/CreatorHub）
if [[ -d "$DIST/CreatorHub/_internal" ]]; then
  rm -rf "$DIST/CreatorHub/_internal/ms-playwright"
  mkdir -p "$DIST/CreatorHub/_internal/ms-playwright"
  cp -R "$BUNDLE_BROWSERS/chromium-${CHROMIUM_REV}" "$DIST/CreatorHub/_internal/ms-playwright/"
  if [[ -n "${FFMPEG_REV}" && -d "$BUNDLE_BROWSERS/ffmpeg-${FFMPEG_REV}" ]]; then
    cp -R "$BUNDLE_BROWSERS/ffmpeg-${FFMPEG_REV}" "$DIST/CreatorHub/_internal/ms-playwright/"
  fi
fi

# 去掉隔离属性，便于本机试跑（未签名仍可能被 Gatekeeper 拦）
xattr -cr "$DIST/$APP_NAME" 2>/dev/null || true

log "制作 DMG…"
STAGE="$DIST/dmg-stage"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$DIST/$APP_NAME" "$STAGE/"
ln -sf /Applications "$STAGE/Applications"

DMG_PATH="$DIST/${DMG_NAME}.dmg"
rm -f "$DMG_PATH"
hdiutil create \
  -volname "$DMG_NAME" \
  -srcfolder "$STAGE" \
  -ov \
  -format UDZO \
  "$DMG_PATH"
rm -rf "$STAGE"

log "完成:"
log "  App: $DIST/$APP_NAME"
log "  DMG: $DMG_PATH"
du -sh "$DIST/$APP_NAME" "$DMG_PATH"
log "首次打开若提示无法验证开发者: 右键 → 打开，或在「隐私与安全性」里允许。"
log "用户数据目录: ~/Library/Application Support/CreatorHub/"
