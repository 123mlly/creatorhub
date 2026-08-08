# CreatorHub

本地运行的多平台内容管理面板，支持 **抖音 / 小红书 / 快手 / 视频号 / YouTube**。

基于 Python + FastAPI 提供 Web 界面，也可通过桌面壳（pywebview）或 Docker 运行。账号登录态、数据库与媒体文件保存在本机，不上传云端。

## 平台能力

| 功能 | 抖音 | 小红书 | 快手 | 视频号 | YouTube |
|---|:---:|:---:|:---:|:---:|:---:|
| 登录 | 扫码 / 创作者 / Cookie | 扫码 / Cookie | 扫码 / 创作者 / Cookie | 扫码 / Cookie | 扫码 / Cookie |
| 作品监控与下载 | ✅ 可选画质 | ✅ 创作者 / 关键词 | ✅ | 仅本账号 | ✅ 需登录账号 |
| 直播监控与录制 | ✅ 开播提醒 + 录 mp4 | — | — | — | ✅ 开播提醒 + 录 mp4 |
| 评论监控 | ✅ | ✅ | ✅ | 仅本账号 | — |
| 发布 | ✅ | ✅ | ✅ | ✅ | ✅ Studio |
| 自动评论 | ✅ | ✅ | ✅ | — | — |
| 本账号 | 作品 / 关注 / 粉丝 / 私信 | 作品 / 关注 / 粉丝 / 私信 | 作品 / 关注 / 粉丝 | 作品 / 数据 | 作品 / 数据 |
| 通知 | Bark / 钉钉 / Telegram | 同左 | 同左 | 同左 | 同左 |

说明：

- **视频号**：仅创作者助手中的本账号数据，不支持监控或下载他人作品。
- **YouTube**：监控与下载依赖已登录账号（yt-dlp + Cookie）；暂无评论监控、关注/粉丝/私信；Studio 发布需本机弹出浏览器，Docker 中受限。
- **直播监控**：仅抖音 / YouTube。同一博主可同时加「作品监控」与「直播监控」；录制结束后统一为 mp4（抖音 flv 会自动转封装）。

## 运行方式怎么选

| 方式 | 适合 | 扫码登录 | 发布弹窗 |
|---|---|---|---|
| 一键脚本 / 手动 uv | 开发与日常本机使用 | ✅ | ✅ |
| 桌面窗口 `desktop.sh` | 不想开浏览器看面板 | ✅ | ✅ |
| macOS / Windows 安装包 | 分发给同事、免装 Python | ✅ | ✅ |
| Docker | 服务器常驻、无桌面 | ❌ 用 Cookie | 受限 |

## 环境要求

- Python **3.11+**（推荐 3.12）
- [uv](https://docs.astral.sh/uv/)（一键启动会自动检测）
- 桌面环境（扫码 / 发布弹窗需要；Docker 除外）
- Node.js 18+（仅小红书发布签名需要）
- ffmpeg 可选（未安装时使用依赖自带的 ffmpeg）
- Docker / Compose（可选）

安装 uv：

```bash
# macOS / Linux
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

## 快速开始

```bash
git clone https://github.com/3441293738/creatorhub.git
cd creatorhub
```

### macOS / Linux

```bash
chmod +x start.sh
./start.sh
```

### Windows

```bat
.\start.cmd
```

首次运行会：同步依赖到 `.venv`、安装 Playwright Chromium、生成 `config.yaml`，并打开：

```text
http://127.0.0.1:8000
```

常用参数（Windows 将 `./start.sh` 换成 `.\start.cmd`）：

```bash
./start.sh install      # 重装 / 更新依赖
./start.sh check        # 环境自检
./start.sh --no-open    # 不自动打开浏览器
./start.sh --port 8080  # 指定端口
./start.sh --reload     # 开发热重载
```

### 桌面窗口

内置启动后端，关窗即退出：

```bash
./desktop.sh
# 或
python creatorhub.py desktop
# macOS 也可双击 CreatorHub.command
```

扫码登录与发布仍会另弹 Playwright Chromium 窗口。

### Docker（Cookie 登录）

容器内默认关闭扫码，请在面板使用「Cookie 粘贴」。无头 Chromium 仍可用于监控与抓取。

```bash
docker compose up -d --build
```

打开 `http://127.0.0.1:8000`。数据落在 `./data/`。

```bash
docker compose logs -f
docker compose down
```

国内构建若 Chromium 下载失败，可在 `docker-compose.yml` 增加：

```yaml
environment:
  PLAYWRIGHT_DOWNLOAD_HOST: "https://npmmirror.com/mirrors/playwright"
```

> 依赖弹出浏览器的发布 / 创作者登录在 Docker 中受限；监控、评论抓取、链接下载等无头流程可用。

<details>
<summary>手动安装（uv）</summary>

```bash
uv sync
uv run playwright install chromium
cp config.example.yaml config.yaml   # 若尚无 config.yaml

uv run python selftest.py
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# 开发
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

小红书发布还需：

```bash
npm install
```

</details>

## 桌面安装包

### macOS

在本机执行：

```bash
./packaging/build_macos.sh
```

产物：

| 文件 | 说明 |
|---|---|
| `dist/CreatorHub.app` | 可拖进「应用程序」 |
| `dist/CreatorHub.dmg` | 分发用安装镜像 |

- 用户数据：`~/Library/Application Support/CreatorHub/`
- 未签名：首次打开用右键 → 打开，或在「隐私与安全性」中允许
- 体积约数百 MB～1GB（含 Chromium）

### Windows

须在 **Windows** 上构建（不能在 macOS 交叉编译）：

```powershell
.\packaging\build_windows.ps1
```

产物：

| 文件 | 说明 |
|---|---|
| `dist\CreatorHub\CreatorHub.exe` | 目录内直接运行 |
| `dist\CreatorHub-windows.zip` | 分发压缩包 |

- 用户数据：`%APPDATA%\CreatorHub\`
- 需要 [WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)（Win10/11 多数已带）
- 未签名时 SmartScreen 可能提示「仍要运行」

可选 **Authenticode 代码签名**（需先购买代码签名证书，并安装含 `signtool` 的 Windows SDK）。在打包前设置环境变量，脚本会在打 zip 前签名 `CreatorHub.exe`：

```powershell
# 方式一：PFX 文件
$env:CODESIGN_PFX = "C:\path\to\cert.pfx"
$env:CODESIGN_PASSWORD = "证书密码"   # 无密码可省略

# 方式二：本机证书存储 / EV 令牌（按证书主题名）
$env:CODESIGN_SUBJECT = "Your Company Name"

# 可选：时间戳服务（默认 DigiCert）
# $env:CODESIGN_TIMESTAMP_URL = "http://timestamp.digicert.com"

.\packaging\build_windows.ps1
```

未设置上述变量时跳过签名，行为与以前相同。

## 基本使用

### 添加账号

1. 顶部切换平台。
2. 左侧进入「账号」。
3. 使用扫码、创作者登录或 Cookie 粘贴。
4. 登录后可刷新资料、检测状态或重新登录。

小红书扫码会依次获取读取态与创作态；需要发布时，请在跳转创作平台后完成登录再关窗。添加笔记 / 创作者监控时，建议使用含 `xsec_token` 的完整链接。

YouTube 建议用 Cookie 或扫码登录 Google / YouTube；监控目标需要绑定已登录的 YouTube 账号。

### 监控与下载

- **作品监控**：创作者主页、作品链接、短链或平台 ID；发现新作自动入库。
- **评论监控**：单条作品或账号近期作品（YouTube / 视频号他人作品不适用）。
- **链接下载**：粘贴分享文案或链接即可解析下载。
- **历史回填**：默认只盯订阅后的新内容，也可回填最近 N 条。

抖音 / YouTube 可选画质；小红书支持图集与视频；支持断点续传与失败重试。

### 发布与转发

- 各平台走对应创作中心 / Studio（有头浏览器）。
- 小红书支持图集、视频与定时发布。
- 已下载内容可在支持的平台间转发，发布前可改标题、正文与话题。

### 本账号与通知

- 「本账号」可同步作品与互动数据（能力因平台而异）。
- 可开启作品健康监控（零播、违规等）并推送通知。
- 通知渠道：Bark、钉钉、Telegram。

> 自动评论、回复、私信、关注等写操作受平台风控影响，请低频使用。

## 配置

首次启动会从 `config.example.yaml` 生成 `config.yaml`。多数选项也可在面板「设置」中修改。

```yaml
server:
  host: 0.0.0.0
  port: 8000

engine:
  scan_interval_seconds: 300
  monitor_initial_backfill_count: 0   # 0=仅新作品；-1=尽量全量
  worker_pool_size: 2
  scan_concurrency: 2
  media_dir: ./data/media
  profiles_dir: ./data/profiles

storage:
  db_path: ./data/creatorhub.db

proxies: []
  # - http://user:pass@host:port
  # - socks5://user:pass@host:port
```

完整说明见 [`config.example.yaml`](config.example.yaml)。

- `config.yaml`、数据库、登录态与媒体默认不进 Git。
- 每账号独立浏览器 profile；多账号建议一号一代理。
- 启动时自动迁移数据库字段，一般无需删库升级。

打包版数据目录见上文「桌面安装包」，不写在 `.app` / 安装目录内。

## 命令行链接下载

只解析、不下载：

```bash
python -m app.engine.share_downloader --links-only "完整分享文案或链接"
```

下载：

```bash
python -m app.engine.share_downloader "完整分享文案或链接" -o ./data/media/share -q 1080
```

面板「链接下载」同样可用。

## 数据目录

开发 / 脚本运行时默认：

```text
data/
├─ creatorhub.db   # SQLite
├─ media/          # 下载内容
└─ profiles/       # 账号浏览器配置与登录态
```

备份时请同时保留 `config.yaml` 与 `data/`（或打包版的 Application Support / `%APPDATA%`）。

## 常见问题

| 问题 | 处理 |
|---|---|
| Playwright 找不到浏览器 | `uv run playwright install chromium` |
| 扫码不弹窗 | 确认有桌面环境；也可用 Cookie；Docker 请用 Cookie |
| Windows Playwright 子进程报错 | 不要用多 worker，保持单进程启动 |
| 抓不到作品 / 评论 | 检查登录态、链接与网络，重新登录并降低频率 |
| 小红书链接失败 | 重新复制含有效 `xsec_token` 的完整链接 |
| YouTube 监控失败 | 确认已添加并登录 YouTube 账号，Cookie 需含 Google / YouTube 域 |
| 视频无声或画质异常 | 更新依赖；或安装系统 ffmpeg 并加入 `PATH` |
| macOS 安装包无法打开 | 右键 → 打开；未签名属正常 |
| 桌面安装包图标未更新 | 删除旧 `.app` 后重装，必要时注销刷新 Dock |


## 友链

- [LINUX DO](https://linux.do/) — 感谢社区提供的帮助与支持。

## 使用说明

本项目用于技术学习与个人内容管理，不提供账号、Cookie、代理或平台数据。请遵守目标平台规则及所在地法律法规，并尊重内容版权与个人隐私。
