# Journal — 个人新闻聚合阅读

个人自用：把彭博社、纽约时报、经济学人的 RSS 标题聚合成一个手机友好的静态页面，点开优先走 [archive.today](https://archive.today) 快照；没有现成快照时打开 archive.today 的人机验证页，人工通过验证后可直接看已有快照或顺手存档（不再直接跳原文，原文走"打开原文"按钮）。

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

## APK 封装（可选）

页面是纯静态站，用 WebView 封装服务即可：

1. 打开 [webtoapp.design](https://webtoapp.design)，粘贴 Pages 网址，按向导生成 APK（自动启用 JavaScript），下载后传到安卓手机安装（允许"安装未知应用"）。
2. 备选：[Median](https://median.co)（原 GoNative）或 PWABuilder（页面已带 `manifest.webmanifest`）。

## 隐私与合规

- 只处理 RSS 公开提供的标题与链接元数据；不抓取、不存储正文。
- 不绕过任何付费墙；快照链接指向 archive.today / Wayback 已有页面。
- 仅供个人使用，不分发、不上架。
