# 抓取与阅读链路 v2

本文是 Journal 的端到端链路说明，对应 2026-11 的改版：**CI 不再超时、正文不再渲染垃圾、
彭博社等付费墙源改用转载版**。代码入口：`scripts/fetch_news.py`。

## 0. 总原则

1. **CI 只做能自动完成的事**：RSS/API → 快照探测 → （无快照时）自动存档 → 正文抓取 →
   清洗 → 写入 `docs/news.json` 与私有仓库 `news-cache`。
2. **人机验证不过就降级，不阻塞**：标记 `challenge`，前端只渲染封面，等用户手动点进文章。
3. **任何阶段到点即停**：已抓到的结果照常落盘，CI 用 `if: always()` 提交。
4. **不绕付费墙**：转载版只读公开发布的页面；读不到就显示封面，不伪造正文。

## 1. 时间预算（解决 CI 超时）

| 变量 | 默认 | 作用 |
|---|---|---|
| `NEWS_BUDGET_SECONDS` | `2400`（40 分钟） | 整轮墙钟上限，到点停止网络任务并写出结果 |
| `MAX_PROBES_PER_RUN` | `140` | 本轮最多探测多少条新链接 |
| `PROBE_WORKERS` | `4` | 快照探测并发（archive.today 限速，别调太高） |
| `PROBE_TOTAL_TIMEOUT` | `30` | 单条探测子进程上限（自动存档时放宽到 75） |
| `AUTO_ARCHIVE_BUDGET` | `24` | 本轮最多自动 “archive this url” 条数 |
| `SYND_BUDGET` | `24` | 本轮最多解析转载版条数 |
| `SYND_WORKERS` | `4` | 转载版检索并发 |
| `DIRECT_TEXT_BUDGET` | `64` | 本轮最多直连正文条数（优先于 archive） |
| `DIRECT_TEXT_WORKERS` | `4` | 直连正文并发 |
| `READ_TEXT_BUDGET` | `80` | 本轮最多抓正文条数 |
| `TEXT_WORKERS` | `4` | 正文抓取并发 |
| `GNEWS_DECODE_PER_FEED` | `100` | 每个 Google News feed 最多解码条数 |
| `OG_IMAGE_BUDGET` | `30` | 本轮最多回填多少张 og:image |

工作流侧：`update` job `timeout-minutes: 60`、抓取 step `timeout-minutes: 50`；提交 step
使用 `if: always()`，即使抓取被 kill 也会提交已完成部分。

## 2. 阶段 A：RSS / 官方 API

`config/sources.json` 里每个 feed 可以带一个可选 `api`：

```json
{"url": "https://rss.nytimes.com/...", "topic": "Top",
 "api": {"kind": "nyt", "section": "home"}}
```

| kind | 需要 | 说明 |
|---|---|---|
| `nyt` | `NYT_API_KEY` | NYT Top Stories API；无 key 自动回落 RSS |
| `guardian` | `GUARDIAN_API_KEY` | Guardian Open Platform；无 key 回落 RSS |
| `wp` | 无 | WordPress REST API（`/wp-json/wp/v2/posts`）；失败回落 RSS |

无 API 的源继续走 RSS；Reuters / WSJ / WaPo / 联合早报经 Google News 检索 + 解码；
TVB 解析 Nuxt `__NUXT_DATA__`。所有源只取**标题、链接、发布时间、缩略图**。

RSS 拿不到图时，`PUT og:image` 回填（限额 15 条/轮，结果缓存 30 天）。

## 3. 阶段 B：直连正文 → 快照探测

**顺序：先直连，后 archive。** 能直接读的源先抓正文写 news-cache，成功即跳过 archive 探测：

- **TVB**：`__NUXT_DATA__` 的 `content/content_hk` + `cover`，不需要 archive/Jina；
- **The Guardian / NYT / TIME**：原文 HTML 的 JSON-LD `articleBody` 或文章容器段落；
- **付费墙源（Bloomberg/FT/WSJ/Economist）**：先解析转载版（The Edge Malaysia、
  Seoul Economic Daily 等），拿到正文就直接缓存；
- 直连失败的条目才进入 archive 探测；探测仍失败才转封面模式。

```
缓存命中（archive 180d / wayback 30d / original 3d / challenge 12h / unarchived 12h）
  └─ 未命中 → 镜像轮换 archive.ph → .md → .li → .is
       ├─ Memento-Datetime 或 /<id>/<url> → status=archive
       ├─ 被人机验证拦下 → status=challenge（Wayback 兜底一次，然后停）
       └─ 确认无快照 → status=original
              └─ 付费墙源且预算允许：自动 “archive this url”
                    ├─ 快照生成 → status=archive
                    └─ 超时/被拦 → status=unarchived
```

- 自动存档只对 `cover_only_when_unarchived` 的源开放，且本轮最多 6 条；单条最多等 40–75s。
- 探测并发 2，提交之间错开 0.4s，降低 archive.today 429。
- 429 视为未命中，暂停 5s 换镜像；不会整轮崩溃。
- **本机走 7890 代理时** archive.today 常见返回 reCAPTCHA 页（proxy IP 被标记），
  探测会判为 `challenge`；正文阶段另有 Jina 快照通道兜底（见下）。
  只给 archive 走代理可设 `ARCHIVE_PROXY=http://127.0.0.1:7890`（其他源仍直连）。
- cf_clearance 绑定 **IP + UA**：cookie 里的 IP 是代理出口，GitHub runner 出口不同，
  CI 里通常无法复用；CI 以 Jina 快照 + 转载版为主，本机才可能直接读 archive.today。

## 4. 阶段 C：正文（转载版优先）

付费墙源（Bloomberg / FT / WSJ / Economist 等）在 CI 里通常只能拿到导语，因此正文链是：

```
1. 转载/联合发布版（syndication.py）
   通用检索 + 精确标题/站点检索 → 解码跳转 → 抓 HTML → JSON-LD/文章段落
   优先：The Edge Malaysia → The Edge Singapore → Yahoo Finance → BNN Bloomberg
        → Financial Post → Business Standard → Moneyweb → BusinessToday Malaysia
        → Seoul Economic Daily → The Economic Times → BusinessWorld → Investing.com
2. 发布者原文 HTML（curl_cffi 浏览器指纹）→ JSON-LD articleBody → 文章容器段落
   - TVB：正文在 Nuxt `__NUXT_DATA__` 的 `content/content_hk` 字段，直接解析，
     不需要 archive/Jina；中文短讯 2 段、约 200 字也算完整正文
3. Jina Reader（r.jina.ai）原文
4. Jina Reader 读 archive.today 快照（r.jina.ai 的出口 IP 常能过 reCAPTCHA）
5. 全失败 → None → 前端“封面 + 开始阅读”
```

转载版只取公开页面，标题/图片/正文一起拿：这解决了“彭博原文付费墙绕不开”和
“封面图缺失”两个问题。结果缓存 30 天（未命中 12h 后重试），命中后不再重复检索。

## 5. 阶段 D：清洗与质量关

识别并拒绝：

- **付费墙/登录/同意墙**：`$1`、`Continue with Apple/Google`、`Sign In`、
  `Unlock election coverage`、`Do Not Sell or Share My Personal Information`、
  `By continuing, I agree to the $1 and $1`；
- **人机验证页**：Cloudflare / CAPTCHA / `security check`；
- **archive.today 索引页**：`newest` 页会把 NYT/FT/WaPo 的标题列在一起，绝不能当正文；
- **页脚/推荐区/募捐区/评论摘要**、短行、URL 行、与标题重复的行。

正文区间：标题（或首个长句）→ 结束标记（Most Read / More From / © / Subscribe…）。
质量门槛：≥3 个长段、≥400 字符（中文 ≥200 字）、多数段落成句；付费墙源要求
≥4 段且 ≥900 字符，否则视为“只有导语”，交给转载版。

只把清洗后的 blocks 写入私有仓库 `news-cache`（GitHub Contents API），不写公开仓库。

## 6. 阶段 E：渲染与人工流程

`news.json` 每条带 `status` / `archive_url` / `wayback_url` / `syndicated_url` / `cover_only`。

```
有正文缓存（news-cache）        → 直接渲染阅读模式
有快照（archive/wayback）      → 客户端按链抓取 → 渲染
付费墙源且无快照/无正文         → 只渲染封面（cover_only=true）
                                   └─ 用户点卡片 → 阅读器封面 + “开始阅读”
                                        ├─ 有快照：开始阅读 → 页面抓取 → 清洗 → 渲染
                                        └─ 无快照：开始阅读 → 归档页
                                             ├─ APK：自动点 archive this url（无快照时）
                                             │   等快照生成 → “完成验证 · 提取正文”
                                             └─ 浏览器：手动过验证后回来重试
```

APK 的 `Android.captureFor` 进入人工验证捕获流程；`onPageFinished` 在 archive 域上注入
`SAVE_JS`，检测到 “No results / archive this url” 时自动点一次存档链接（只点一次，不循环）。
提取时校验：必须是 archive 域、正文 ≥200 字符、≥3 行长文本、不含验证页特征。

## 7. 状态机

| status | 含义 | 前端 |
|---|---|---|
| `archive` | archive.today 快照 | 正常阅读 |
| `wayback` | Wayback 快照 | 正常阅读 |
| `challenge` | 人机验证未过 | 付费墙源只显示封面；其余显示卡片 |
| `unarchived` | 自动存档失败/超时 | 付费墙源只显示封面 |
| `original` | 无快照 | 付费墙源只显示封面；其余尝试原文 |

`hide_unarchived` 的源（NYT/WSJ/WaPo/Reuters）在确认无快照时直接从列表隐藏，
避免“点进去什么都没有”；快照出现后自动回归。

## 8. 源策略一览

| 源 | 策略 | 备注 |
|---|---|---|
| Bloomberg | RSS + 转载版 | The Edge Malaysia/Singapore 等转载 Bloomberg |
| NYT | RSS / Top Stories API | 有 `NYT_API_KEY` 用 API |
| Guardian | RSS / Open Platform | 有 `GUARDIAN_API_KEY` 用 API |
| Economist / FT / WSJ | RSS + 转载版 | 付费墙源，封面兜底 |
| TIME | RSS / WordPress REST | WP 失败自动回落 RSS |
| Reuters / WaPo | Google News 检索 | 无官方 RSS |
| SCMP / TVB | RSS / Nuxt 解析 | 只取元数据 |

## 9. 失败与回滚

- 单个源失败：记日志、跳过，不影响其他源。
- 单条探测/正文失败：写失败标记（3 天/12 小时后重试），流程继续。
- 整轮异常：`main` 的 `finally` 仍会写出已完成部分；CI 的 `if: always()` 提交。
- 正文写私有仓库失败：只影响该条，不阻断 `news.json` 发布。
