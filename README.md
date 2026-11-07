# Journal — 个人新闻聚合阅读

个人自用：把彭博社、纽约时报、经济学人等 12 个来源的 RSS 标题聚合成一个手机友好的静态页面。点开优先走 [archive.today](https://archive.today) 快照；没有现成快照时，CI 会 best-effort 自动 “archive this url”，仍然失败就只渲染封面，等用户点进文章手动过人机验证（详见 [抓取与阅读链路 v2](docs/PIPELINE.md)）。

**功能**：搜索；来源 × 话题双维度筛选（chips 双行循环滚动，悬停/触按/选中时暂停）；亮色 / 暗色 / 系统主题切换；收藏/稍后读（localStorage 持久化，掉出列表仍可看）；紧凑/舒适视图切换；RSS 缩略图 + og:image 回填（无图时用来源首字母色块）；结果计数 + 清除筛选；相对时间每分钟自动刷新；**阅读模式**：点任意卡片在应用内读全文。正文按“转载版/直连 → 发布者原文 → Jina Reader → archive 兜底 → 封面/手动”链取首个质量达标来源，直连成功即写 news-cache 并跳过 archive；付费墙源（Bloomberg 等）优先用 The Edge Malaysia / The Edge Singapore / Yahoo Finance 等**公开转载版**拿标题、图片、正文；只缓存清洗后的段落，不绕付费墙、不批量抓取、不在公开仓库存储正文。

**源（12 个）**：Bloomberg、NYT、Economist、Guardian、WSJ、Financial Times、TIME、SCMP、Washington Post、Reuters、联合早报英文版、TVB。Reuters 与联合早报无官方 RSS，经 Google News 检索 + [googlenewsdecoder](https://pypi.org/project/googlenewsdecoder/) 解码还原原文链接；TVB 列表解析 Nuxt 内嵌数据，**文章正文也直接解析 `__NUXT_DATA__`（不需要 archive）**；NYT/Guardian 可选官方 API（配置 `NYT_API_KEY` / `GUARDIAN_API_KEY` 后自动启用，否则回落 RSS）。

新闻源会随时间陆续死亡（停更/清空/改版，如 WSJ 官方 feed 冻结在 2025-01、NYT 体育部裁撤后源变空）。定期跑一次体检脚本可揪出"活着但冻结/为空"的源：

```bash
.venv\Scripts\python scripts\feed_checkup.py   # 走代理时先 set HTTPS_PROXY=http://127.0.0.1:7890
```

输出每个源的条数与最新文章距今年龄，发现异常后编辑 `config/sources.json` 换源即可。

## 页面交互约定

- 点卡片 / 回车 = 打开阅读模式；有正文缓存/快照直接渲染，没有则按链路抓取
- 付费墙源且没有快照/正文时，阅读器只显示**封面 + “开始阅读”**：有快照直接抓取；没有快照则跳归档页，手动过人机验证后回来提取
- 卡片右侧图标：书签=收藏 · 箭头=打开原文 · 方框=复制链接 · 红色方框=打开存档页（与点卡片等效）

> **边界声明**：仅抓取并存储标题、来源、发布时间、链接等元数据；正文只存私有仓库，不写入公开仓库；转载版只读公开发布的页面，不绕过付费墙；仅供个人使用，不分发。

## 架构

```
config/sources.json ──► scripts/fetch_news.py ──► docs/news.json ──► GitHub Pages (docs/)
   (源/策略/预算)            │                        ▲
                            │ 子进程调用               │
                            ▼                        │
                     scripts/archive_probe.py        │
                     (archive 镜像轮换 → 自动存档     │
                      → Wayback 兜底 → 原文)          │
                            │                        │
                            ▼                        │
                     scripts/readability.py          │
                     (转载版 → 原文 → Jina → 清洗)    │
                            │                        │
                     data/cache.json ────────────────┘ (跨运行复用，随仓库提交)
                            │
                            ▼
                     news-cache 私有仓库（仅正文，不进公开仓库）
```

- GitHub Actions 每 2 小时（UTC）跑一次，也支持手动触发；有变更时提交 `docs/news.json` 与 `data/cache.json`，push 自动触发 Pages 部署。
- 前端为纯静态 HTML/CSS/JS（`docs/`），不依赖后端。
- CI 有整轮墙钟预算（默认 40 分钟），到点停止并提交已完成部分；抓取 step 超时也会由 `if: always()` 提交。

## 目录结构

```
├── config/sources.json        RSS 源、可选官方 API、转载版优先级
├── docs/
│   ├── PIPELINE.md            抓取与阅读链路 v2（时间预算/状态机/人工流程）
│   └── index.html …           前端静态站点
├── scripts/
│   ├── fetch_news.py          主流程：RSS/API → 探测 → 转载版 → 正文 → news.json
│   ├── feed_api.py            NYT / Guardian / WordPress 官方 API 适配
│   ├── syndication.py         转载版检索与解析（The Edge、Yahoo 等）
│   ├── readability.py         正文提取、去噪、质量关、私有缓存
│   ├── clean_url.py           链接清洗：移除 utm_*、fbclid 等跟踪参数
│   ├── verify_guard.py        验证页检测 / nopecha / 退出码 75
│   ├── archive_probe.py       快照探测 + 自动存档 + Wayback 兜底
│   └── cache_store.py         探测结果缓存（按状态区分 TTL）
├── tests/                     单元测试（pytest）
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

只想让 archive.today 走代理（本机 7890）、其他源直连时，用专用变量：

```bash
set ARCHIVE_PROXY=http://127.0.0.1:7890
```

跑测试：

```bash
.venv\Scripts\python -m pytest -q
```

## 配置 RSS 源 / 官方 API

编辑 `config/sources.json`：`sources` 里每项是 `{name, feeds[]}`，增删改源只动这个文件；`max_per_source` / `max_total` 控制数量上限，也可用环境变量 `MAX_PER_SOURCE`、`MAX_TOTAL`、`MAX_PROBES_PER_RUN` 覆盖。单个源失效只记日志、不影响整体。

每个 feed 可带可选 `api`，有 key/接口可用时优先，失败自动回落 RSS：

```json
{"url": "https://rss.nytimes.com/...", "topic": "Top",
 "api": {"kind": "nyt", "section": "home"}}
```

- `kind: "nyt"` → NYT Top Stories API（需 `NYT_API_KEY`）
- `kind: "guardian"` → Guardian Open Platform（需 `GUARDIAN_API_KEY`）
- `kind: "wp"` → WordPress REST API（TIME 已配置，失败自动回落 RSS）

付费墙源可加 `syndication` 列表指定转载版优先级（如 Bloomberg → `edge_my` → `edge_sg` → `yahoo`）；
`cover_only_when_unarchived: true` 表示无快照时前端只渲染封面，等用户手动阅读。

经济学人栏目源格式为 `https://www.economist.com/<栏目>/rss.xml`（如 `leaders`、`business`、`china`）。

## CI 时间预算（防超时）

`fetch_news.py` 有整轮墙钟预算，到点停止网络任务、照常写出已完成部分；`update-news.yml`
的抓取 step 限 50 分钟、job 限 60 分钟，提交 step 使用 `if: always()`，超时也会提交进度。

| 变量 | 默认 | 作用 |
|---|---|---|
| `NEWS_BUDGET_SECONDS` | 2400 | 整轮上限（40 分钟） |
| `MAX_PROBES_PER_RUN` | 35 | 本轮最多探测条数 |
| `PROBE_WORKERS` | 2 | 探测并发 |
| `PROBE_TOTAL_TIMEOUT` | 45 | 单条探测上限（自动存档 75） |
| `AUTO_ARCHIVE_BUDGET` | 6 | 本轮自动存档条数 |
| `SYND_BUDGET` / `DIRECT_TEXT_BUDGET` | 6 / 16 | 转载版、直连正文条数（直连优先于 archive） |
| `READ_TEXT_BUDGET` | 20 | 兜底正文抓取条数 |
| `TEXT_WORKERS` | 2 | 正文抓取并发 |

## 机器验证处理（独立环节）

archive.today 的 Cloudflare 挑战 / CAPTCHA 是**常态而非异常**，由 `verify_guard.py` + `archive_probe.py` 独立处理：

1. **验证页检测**：模式匹配 `security check` / `captcha` / `recaptcha` / `cloudflare…challenge` 等关键词；同时校验行数、字符数是否达到"实质内容"阈值。弱关键词只在内容单薄时判定，防止把"正文讨论 CAPTCHA 的正常归档页"误判；而 `recaptcha/api.js`、`data-sitekey=` 等真实验证组件标记则为强特征（archive.today 限速页内容很厚，必须靠组件标记识别）。
2. **人工介入信号**：先识别类型（cloudflare / captcha），再用 nopecha 小规模尝试（需 `NOPECHA_API_KEY`，每个链接最多 1 个任务）；失败则向 stderr 输出触发验证的 URL 和"需要人工验证"提示，并以**退出码 75**（EX_TEMPFAIL）终止当前链接的探测。上层 `fetch_news.py` 以子进程调用探测环节，看到 75 就把该条新闻标记为"验证待处理"（`status=challenge`），整体流程继续，不会崩溃；标记前仍会尝试 Wayback 兜底。
3. **无快照 → 自动存档**：付费墙源在预算内自动提交一次 “archive this url” 并轮询 40–75s；成功则 `status=archive`，失败标记 `unarchived`（12h 后重试）。自动存档被人机验证拦下时仍按 `challenge` 处理，不重试。
4. **前端降级**：`challenge` / `unarchived` 且没有正文缓存的付费墙文章只渲染封面；用户点“开始阅读”后，APK 会打开归档页并（无快照时）自动点一次存档链接，等快照生成后点“完成验证 · 提取正文”；浏览器则手动打开归档页。
5. **cf_clearance 复用**：设置 secret/环境变量 `ARCHIVE_COOKIE`（支持 `cf_clearance=值` 或完整 Cookie 串），探测请求会自动携带。注意：cf_clearance 绑定 **IP + User-Agent**；cookie 里的 IP 是本机代理出口（如 `143.244.189.0`），GitHub Actions runner 的出口 IP 不同，**CI 里通常无法复用**，请以 Jina 快照 + 转载版为主。若同时配置 `ARCHIVE_UA`，务必与取 cookie 时浏览器的 UA 一致。
6. **429 限速**：视为未命中，暂停 5s 后轮换下一个镜像，不崩溃。
7. **本机 7890 代理**：archive.today 常返回 reCAPTCHA 页（代理 IP 被标记），探测判为 `challenge`；正文阶段会再用 Jina Reader 的出口 IP 读 `archive.ph/newest/<url>` 快照兜底，拿到全文则正常缓存。

探测规则：镜像按 `archive.ph → archive.md → archive.li → archive.is` 轮换（可用 `ARCHIVE_MIRRORS` 覆盖）；命中判定以 **Memento-Datetime 响应头**为可靠信号，最终 URL 形如 `/<快照id>/<原文>` 为兜底信号；默认超时 8s/15s、重试 1 次、请求间隔 1s（`PROBE_*` 环境变量可调）。

缓存 TTL：`archive` 180 天、`wayback` 30 天、未命中 3 天、`challenge` / `unarchived` 12 小时后自动重试。

## 部署（已完成的事 + 你可能需要的操作）

仓库已创建并推送；在 GitHub 上确认一次：

1. **Settings → Pages**：Source 选 `Deploy from a branch`，Branch 选 `main`、目录 `/docs`，保存（若创建仓库时已通过 API 启用则无需再动）。
2. **Actions**：`update-news` 工作流每 2 小时自动运行；也可在 Actions 页面手动 `Run workflow`。
3. 可选 secrets（Settings → Secrets and variables → Actions）：
   - `ARCHIVE_COOKIE` / `ARCHIVE_UA`：见上节；
   - `NOPECHA_API_KEY`：打码服务 key（不配则跳过自动解题）；
   - `NYT_API_KEY` / `GUARDIAN_API_KEY`：启用官方 API（不配走 RSS）；
   - `JINA_KEY` / `NEWS_CACHE_TOKEN`：正文抓取配额与私有正文缓存（不配则不缓存正文，前端按需抓）。

手机访问 `https://<你的用户名>.github.io/news-browse/` 即可使用。

## APK 封装（已自动化）

仓库内置 WebView 壳工程（`apk/`，纯 Java、无第三方依赖），GitHub Actions 在每次 update-news 成功后自动构建签名 APK 并发布到 Releases：

- 下载：<https://github.com/lin-z-h/news-browse/releases/download/apk-latest/Journal.apk>
- 安装：传到安卓手机直接安装（允许"安装未知应用"）；更新版本可直接覆盖安装（签名一致，versionCode 按构建时间自增）
- 签名 keystore 首次构建时由 CI 自动生成并提交回仓库（`apk/signing/release.jks`，个人自用场景，仅用于保证覆盖安装签名一致，不含敏感内容）
- 修改 `docs/**` 或 `apk/**` 后 push 会自动重建 APK；也可在 Actions 页手动 Run workflow
- 备选：webtoapp.design / Median 等在线封装服务（粘贴 Pages 网址即可），或 PWABuilder

## 隐私与合规

- 只处理 RSS / 官方 API 公开提供的标题、链接、时间、缩略图等元数据。
- 正文只抓取公开页面（含公开转载版），清洗后存入私有仓库；不写入公开仓库、不批量分发。
- 不绕过任何付费墙；转载版只读公开发布的内容，拿不到就显示封面让用户手动阅读。
- 仅供个人使用，不分发、不上架。
