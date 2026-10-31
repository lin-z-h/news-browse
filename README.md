# Journal — 个人新闻聚合阅读

个人自用：把彭博社、纽约时报、经济学人的 RSS 标题聚合成一个手机友好的静态页面，点开优先走 [archive.today](https://archive.today) 快照；没有现成快照时打开 archive.today 的人机验证页（`/newest/` 直达最新快照），人工通过验证后可直接看已有快照或顺手存档（不再直接跳原文，原文走"打开原文"按钮）。

**功能**：搜索；来源 × 话题双维度筛选（chips 双行循环滚动，悬停/触按/选中时暂停）；亮色 / 暗色 / 系统主题切换；收藏/稍后读（localStorage 持久化，掉出列表仍可看）；紧凑/舒适视图切换；RSS 缩略图 + og:image 回填（无图时用来源首字母色块）；结果计数 + 清除筛选；相对时间每分钟自动刷新；**阅读模式**：无快照的文章点卡片直接在应用内读全文（经开源 [Jina Reader](https://github.com/jina-ai/reader) 按需抓取，本地缓存，付费源只能看到公开部分，不绕付费墙、不批量抓取、不在仓库存储正文）。

**源（11 个）**：Bloomberg、NYT、Economist、Guardian、WSJ、Financial Times、TIME、SCMP、Washington Post、Reuters、联合早报英文版。Reuters 与联合早报无官方 RSS，经 Google News 检索 + [googlenewsdecoder](https://pypi.org/project/googlenewsdecoder/) 解码还原原文链接；TVB 暂缺（无可用的公开 RSS/索引，后续可换方案补）。

新闻源会随时间陆续死亡（停更/清空/改版，如 WSJ 官方 feed 冻结在 2025-01、NYT 体育部裁撤后源变空）。定期跑一次体检脚本可揪出"活着但冻结/为空"的源：

```bash
.venv\Scripts\python scripts\feed_checkup.py   # 走代理时先 set HTTPS_PROXY=http://127.0.0.1:7890
```

输出每个源的条数与最新文章距今年龄，发现异常后编辑 `config/sources.json` 换源即可。

## 页面交互约定

- 点卡片 / 回车 = 打开存档（快照 → Wayback → archive.today 验证页）
- 卡片右侧图标：书签=收藏 · 箭头=打开原文 · 方框=复制链接 · 红色方框=存档版（与点卡片等效）

> **边界声明**：仅抓取并存储标题、来源、发布时间、链接等元数据，不抓取、不存储任何新闻正文；不提供绕过付费墙的功能；仅供个人使用，不分发。

## 架构

```
config/sources.json ──► scripts/fetch_news.py ──► docs/news.json ──► GitHub Pages (docs/)
   (源列表可配置)            │                        ▲
                            │ 子进程调用               │
                            ▼                        │
                     scripts/archive_probe.py        │
                     (archive.ph/.md/.li/.is 轮换     │
                      → Wayback 兜底 → 原文兜底)      │
                            │                        │
                     data/cache.json ────────────────┘ (跨运行复用，随仓库提交)
```

- GitHub Actions 每 2 小时（UTC）跑一次，也支持手动触发；有变更时提交 `docs/news.json` 与 `data/cache.json`，push 自动触发 Pages 部署。
- 前端为纯静态 HTML/CSS/JS（`docs/`），不依赖后端。

## 目录结构

```
├── config/sources.json        RSS 源列表与数量上限（可编辑）
├── scripts/
│   ├── fetch_news.py          主流程：抓 RSS → 清洗 → 探测 → 生成 news.json
│   ├── clean_url.py           链接清洗：移除 utm_*、fbclid 等跟踪参数（纯函数，可测试）
│   ├── verify_guard.py        【机器验证处理 · 独立环节】验证页检测 / nopecha / 退出码 75
│   ├── archive_probe.py       快照探测（镜像轮换 + Memento-Datetime 判定 + Wayback 兜底）
│   └── cache_store.py         探测结果缓存（按状态区分 TTL）
├── tests/                     单元测试（pytest）
├── docs/                      Pages 站点（index.html / style.css / app.js / news.json）
├── data/cache.json            缓存（随仓库提交，Actions 之间复用）
└── .github/workflows/update-news.yml
```

## 本地运行

```bash
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt      # Windows
.venv\Scripts\python scripts\fetch_news.py
.venv\Scripts\python -m http.server 8000 -d docs   # 浏览器打开 http://localhost:8000
```

国内网络需要代理时，直接设环境变量即可（requests / curl_cffi 都会自动识别）：

```bash
set HTTPS_PROXY=http://127.0.0.1:7890
```

跑测试：

```bash
.venv\Scripts\python -m pytest -q
```

## 配置 RSS 源

编辑 `config/sources.json`：`sources` 里每项是 `{name, feeds[]}`，增删改源只动这个文件；`max_per_source` / `max_total` 控制数量上限，也可用环境变量 `MAX_PER_SOURCE`、`MAX_TOTAL`、`MAX_PROBES_PER_RUN` 覆盖。单个源失效只记日志、不影响整体。

经济学人栏目源格式为 `https://www.economist.com/<栏目>/rss.xml`（如 `leaders`、`business`、`china`）。

## 机器验证处理（独立环节）

archive.today 的 Cloudflare 挑战 / CAPTCHA 是**常态而非异常**，由 `verify_guard.py` + `archive_probe.py` 独立处理：

1. **验证页检测**：模式匹配 `security check` / `captcha` / `recaptcha` / `cloudflare…challenge` 等关键词；同时校验行数、字符数是否达到"实质内容"阈值。弱关键词只在内容单薄时判定，防止把"正文讨论 CAPTCHA 的正常归档页"误判；而 `recaptcha/api.js`、`data-sitekey=` 等真实验证组件标记则为强特征（archive.today 限速页内容很厚，必须靠组件标记识别）。
2. **人工介入信号**：先识别类型（cloudflare / captcha），再用 nopecha 小规模尝试（需 `NOPECHA_API_KEY`，每个链接最多 1 个任务）；失败则向 stderr 输出触发验证的 URL 和"需要人工验证"提示，并以**退出码 75**（EX_TEMPFAIL）终止当前链接的探测。上层 `fetch_news.py` 以子进程调用探测环节，看到 75 就把该条新闻标记为"验证待处理"（`status=challenge`），整体流程继续，不会崩溃；标记前仍会尝试 Wayback 兜底，尽量给出可用的快照链接。单个链接也可手动验证：`python scripts/archive_probe.py <URL>`。
3. **cf_clearance 复用**：设置 secret/环境变量 `ARCHIVE_COOKIE`（支持 `cf_clearance=值` 或完整 Cookie 串），探测请求会自动携带。注意：cf_clearance 通常**绑定 IP 和 User-Agent**，过期后需要重新获取，因此这是"可改善但非永久"的方案——如同时配置 `ARCHIVE_UA`，务必与取 cookie 时浏览器的 UA 一致。
4. **429 限速**：视为未命中，暂停后轮换下一个镜像，不崩溃。

探测规则：镜像按 `archive.ph → archive.md → archive.li → archive.is` 轮换（可用 `ARCHIVE_MIRRORS` 覆盖）；命中判定以 **Memento-Datetime 响应头**为可靠信号，最终 URL 形如 `/<快照id>/<原文>` 为兜底信号；默认超时 10s/25s、重试 2 次、请求间隔 1.5s（`PROBE_*` 环境变量可调）。

缓存 TTL：`archive` 180 天、`wayback` 30 天、未命中 3 天、验证待处理 12 小时后自动重试。

## 部署（已完成的事 + 你可能需要的操作）

仓库已创建并推送；在 GitHub 上确认一次：

1. **Settings → Pages**：Source 选 `Deploy from a branch`，Branch 选 `main`、目录 `/docs`，保存（若创建仓库时已通过 API 启用则无需再动）。
2. **Actions**：`update-news` 工作流每 2 小时自动运行；也可在 Actions 页面手动 `Run workflow`。
3. 可选 secrets（Settings → Secrets and variables → Actions）：
   - `ARCHIVE_COOKIE` / `ARCHIVE_UA`：见上节；
   - `NOPECHA_API_KEY`：打码服务 key（不配则跳过自动解题）。

手机访问 `https://<你的用户名>.github.io/news-browse/` 即可使用。

## APK 封装（已自动化）

仓库内置 WebView 壳工程（`apk/`，纯 Java、无第三方依赖），GitHub Actions 在每次 update-news 成功后自动构建签名 APK 并发布到 Releases：

- 下载：<https://github.com/lin-z-h/news-browse/releases/download/apk-latest/Journal.apk>
- 安装：传到安卓手机直接安装（允许"安装未知应用"）；更新版本可直接覆盖安装（签名一致，versionCode 按构建时间自增）
- 签名 keystore 首次构建时由 CI 自动生成并提交回仓库（`apk/signing/release.jks`，个人自用场景，仅用于保证覆盖安装签名一致，不含敏感内容）
- 修改 `docs/**` 或 `apk/**` 后 push 会自动重建 APK；也可在 Actions 页手动 Run workflow
- 备选：webtoapp.design / Median 等在线封装服务（粘贴 Pages 网址即可），或 PWABuilder

## 隐私与合规

- 只处理 RSS 公开提供的标题与链接元数据；不抓取、不存储正文。
- 不绕过任何付费墙；快照链接指向 archive.today / Wayback 已有页面。
- 仅供个人使用，不分发、不上架。
