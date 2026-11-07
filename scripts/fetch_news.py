"""主流程：抓 RSS/API → 清洗链接 → 探测快照（含自动存档）→ 转载版兜底
→ 正文缓存 → 生成 docs/news.json + data/cache.json。

只保留标题、来源、发布时间、链接、快照链接等元数据；正文只存私有仓库
news-cache（仅 token 持有者可见）。

探测以子进程方式调用 archive_probe.py（独立环节）：
* 退出码 0   → stdout 最后一行为 JSON 结果；
* 退出码 75  → 该条新闻标记为"验证待处理"(challenge)，流程继续；
* 其他/超时  → 按"原文兜底"(original)处理，流程继续。

时间预算（解决 CI 超时）：
    NEWS_BUDGET_SECONDS  整轮墙钟预算（默认 2400s = 40min）
    MAX_PROBES_PER_RUN   本轮最多探测条数（默认 35）
    PROBE_WORKERS        探测并发（默认 2）
    PROBE_TOTAL_TIMEOUT  单条探测上限（默认 45s）
    AUTO_ARCHIVE_BUDGET  本轮最多自动存档条数（默认 6）
    SYND_BUDGET          本轮最多解析转载版条数（默认 8）
    READ_TEXT_BUDGET     本轮最多抓正文条数（默认 20）
    TEXT_WORKERS         正文抓取并发（默认 2）

阶段到点即停、已抓到的结果照常写出；CI 的提交步骤用 if: always() 兜底，
超时也不会丢进度。

环境变量：
    SOURCES_CONFIG      源配置路径（默认 config/sources.json）
    OUTPUT_PATH         输出路径（默认 docs/news.json）
    CACHE_PATH          缓存路径（默认 data/cache.json）
    MAX_PER_SOURCE / MAX_TOTAL   数量上限
    HTTP_PROXY / HTTPS_PROXY     本机走代理时直接生效
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

import cache_store
import feed_api
import readability as rd
import syndication
from clean_url import strip_tracking

BASE = Path(__file__).resolve().parents[1]
PROBE_CLI = BASE / "scripts" / "archive_probe.py"

EXIT_CHALLENGE = 75  # 与 verify_guard.EXIT_CHALLENGE 保持一致

RSS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
FEED_TIMEOUT = (10, 25)
FEED_HEADERS = {"Accept": "application/rss+xml, application/xml, text/xml, */*"}
FEED_HEADERS_UA = {**FEED_HEADERS, "User-Agent": RSS_UA}

START_TS = time.monotonic()
DEADLINE = START_TS + float(os.environ.get("NEWS_BUDGET_SECONDS", "2400"))

_WS_RE = re.compile(r"\s+")
# 兜底：googlenewsdecoder 等第三方库内部有裸 requests 调用，没有 timeout 时可能挂死。
# 设置全局 socket 超时，避免单个解码拖垮整轮 CI。
socket.setdefaulttimeout(15)
_auto_lock = threading.Lock()
_auto_left = int(os.environ.get("AUTO_ARCHIVE_BUDGET", "6") or 6)


def _remaining() -> float:
    return DEADLINE - time.monotonic()


def _log(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _http_get(url: str) -> bytes:
    """抓取 RSS 内容。

    优先用 curl_cffi 的浏览器指纹：经济学人等源在 Cloudflare 后面，普通
    requests 会被 "Just a moment..." 拦下，模拟浏览器 TLS 指纹可直连。
    未安装或失败时退回 requests。两种方式都自动遵循 HTTP(S)_PROXY 环境变量。
    """
    errors = []
    try:
        from curl_cffi import requests as creq

        try:
            resp = creq.get(url, impersonate="safari17_0", timeout=35, headers=FEED_HEADERS)
            if resp.status_code == 200:
                return resp.content
            errors.append(f"curl_cffi HTTP {resp.status_code}")
        except Exception as exc:
            errors.append(f"curl_cffi {type(exc).__name__}: {exc}")
    except ImportError:
        errors.append("curl_cffi 未安装")
    try:
        resp = requests.get(url, headers=FEED_HEADERS_UA, timeout=FEED_TIMEOUT)
        if resp.status_code == 200:
            return resp.content
        errors.append(f"requests HTTP {resp.status_code}")
    except Exception as exc:
        errors.append(f"requests {type(exc).__name__}: {exc}")
    raise RuntimeError("; ".join(errors) or "fetch failed")


def _entry_time(entry) -> datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        st = entry.get(key)
        if st:
            return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
    return None


def fetch_entries(feed_url: str) -> list:
    content = _http_get(feed_url)
    parsed = feedparser.parse(content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"feed 解析失败: {parsed.bozo_exception}")
    return list(parsed.entries)


_IMG_IN_HTML_RE = re.compile(r'<img[^>]+src=["\']([^"\']+)["\']', re.I)
_OG_RE = re.compile(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']{10,})["\']', re.I)
_TW_RE = re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']{10,})["\']', re.I)


def _norm_image_url(u: str) -> str | None:
    u = (u or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("http"):
        return u
    return None


def _entry_image(entry) -> str | None:
    """从 RSS 条目提取缩略图：media 字段 → enclosure → 正文第一个 <img>。"""
    for key in ("media_thumbnail", "media_content"):
        for m in entry.get(key) or []:
            u = _norm_image_url(m.get("url"))
            if u:
                return u
    for link in entry.get("links") or []:
        if link.get("rel") == "enclosure" and str(link.get("type") or "").startswith("image/"):
            u = _norm_image_url(link.get("href"))
            if u:
                return u
    bodies = [(entry.get("content") or [{}])[0].get("value", ""), entry.get("summary", "")]
    for body in bodies:
        if not body:
            continue
        m = _IMG_IN_HTML_RE.search(body)
        if m:
            u = _norm_image_url(m.group(1))
            if u:
                return u
    return None


# ---------------------------------------------------------------------------
# TVB 自定义源：news.tvb.com 无 RSS，首页为 Nuxt SPA，文章数据内嵌在
# __NUXT_DATA__（devalue 扁平化格式）里。此处只做轻量解析：定位含
# title_hk / publish_at / cover 的文章对象，按引用还原字段，不碰正文。
# ---------------------------------------------------------------------------
_NUXT_DATA_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _tvb_deref(arr, idx):
    """devalue 格式：对象/数组里的整数值是对 arr 下标的引用（0 除外）。"""
    if isinstance(idx, int) and 0 < idx < len(arr):
        return arr[idx]
    return idx


def _tvb_parse(html: str) -> list:
    m = _NUXT_DATA_RE.search(html)
    if not m:
        return []
    try:
        arr = json.loads(m.group(1))
    except ValueError:
        return []
    articles = {}
    for v in arr:
        if not (isinstance(v, dict) and "title_hk" in v and "publish_at" in v and "id" in v):
            continue
        try:
            aid = _tvb_deref(arr, v.get("id"))
            if not isinstance(aid, int) or aid in articles:
                continue
            title = _tvb_deref(arr, v.get("title_hk")) or _tvb_deref(arr, v.get("title")) or ""
            publish_at = _tvb_deref(arr, v.get("publish_at"))
            image = None
            cover = _tvb_deref(arr, v.get("cover"))
            if isinstance(cover, list):  # cover 编码为 [图片对象引用] 的单元素数组
                covers = [_tvb_deref(arr, x) for x in cover]
                cover = covers[0] if covers else None
            if isinstance(cover, dict) and "url" in cover:
                image = _tvb_deref(arr, cover["url"])
            articles[aid] = {"id": aid, "title": str(title).strip(), "publish_at": publish_at, "image": image}
        except (KeyError, TypeError, ValueError):
            continue
    return list(articles.values())


def fetch_tvb_entries(list_url: str) -> list:
    """抓 TVB 新闻列表页，产出与 feedparser entries 同形的条目字典。"""
    html = _http_get(list_url).decode("utf-8", errors="replace")
    entries = []
    for a in _tvb_parse(html):
        pub_struct = None
        try:
            dt = datetime.strptime(a["publish_at"], "%Y-%m-%d %H:%M")
            pub_struct = dt.replace(tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc).timetuple()
        except (ValueError, TypeError):
            pass
        entries.append({
            "title": a["title"],
            "link": f"https://news.tvb.com/en/{a['id']}",
            "published_parsed": pub_struct,
            "media_content": [{"url": a["image"]}] if a.get("image") else [],
        })
    return entries


def fetch_og_image(url: str) -> str | None:
    """抓文章页 <meta og:image> 作为缩略图兜底。

    只提取图片元数据，不抓取正文；浏览器指纹直连（部分源在 Cloudflare 后面）。
    """
    try:
        from curl_cffi import requests as creq

        resp = creq.get(url, impersonate="safari17_0", timeout=12,
                        headers={"Accept": "text/html,application/xhtml+xml"})
        if resp.status_code == 200 and len(resp.content) > 500:
            for rx in (_OG_RE, _TW_RE, _IMG_IN_HTML_RE):
                m = rx.search(resp.text)
                if m:
                    u = _norm_image_url(m.group(1))
                    if u and not u.endswith((".svg", ".ico")):
                        return u
    except Exception:
        pass
    return None


def _unwrap_google_news(url: str) -> str:
    """Google News RSS 的 <link> 是 news.google.com 跳转链接，无法直接探测/存档。

    用开源的 googlenewsdecoder 解码还原原文链接；解码失败时保留跳转链接
    （仍可点开阅读，只是存档探测不理想），绝不让单条解码失败影响整体。
    """
    if "news.google.com" not in url:
        return url
    try:
        from googlenewsdecoder import gnewsdecoder

        result = gnewsdecoder(url, interval=1)
        decoded = result.get("decoded_url") if isinstance(result, dict) else result
        if isinstance(decoded, str) and decoded.startswith("http"):
            return decoded
    except Exception:
        pass
    return url


def _feed_entries(source: dict, feed: dict) -> list:
    """按源策略取条目：RSS / TVB / WordPress / NYT / Guardian（API 失败回落 RSS）。"""
    feed_url = feed.get("url") or ""
    if feed.get("type") == "tvb":
        return fetch_tvb_entries(feed_url)
    api = feed.get("api")
    if isinstance(api, dict):
        kind = api.get("kind")
        try:
            if kind == "wp":
                return feed_api.fetch_wp_entries(api.get("url") or feed_url)
            if kind == "nyt" and os.environ.get("NYT_API_KEY"):
                return feed_api.fetch_nyt_entries(api.get("section") or "home",
                                                  os.environ["NYT_API_KEY"].strip())
            if kind == "guardian" and os.environ.get("GUARDIAN_API_KEY"):
                return feed_api.fetch_guardian_entries(api.get("section") or "world",
                                                       os.environ["GUARDIAN_API_KEY"].strip())
        except Exception as exc:
            _log(f"[warn] API 失败，回落 RSS: {source.get('name')} {kind} ({type(exc).__name__})")
    return fetch_entries(feed_url)


def collect_items(config: dict, deadline: float | None = None) -> list:
    """抓取全部源 → 清洗 → 去重 → 按时间排序 → 截断。

    feed 配置支持字符串（仅 URL）或对象 {"url": ..., "topic": ...}，
    topic 用于前端的话题/分类筛选；可选 "api" 指定官方 API。
    """
    limit = deadline or DEADLINE
    max_per_source = int(config.get("max_per_source", _env_int("MAX_PER_SOURCE", 40)))
    max_total = int(config.get("max_total", _env_int("MAX_TOTAL", 300)))
    dedup = {}
    allowed = set()

    for source in config.get("sources", []):
        name = source.get("name", "unknown")
        for feed in source.get("feeds", []):
            if time.monotonic() >= limit:
                _log("[warn] RSS 阶段到点，跳过剩余源")
                break
            if isinstance(feed, str):
                feed_url, topic = feed, None
            else:
                feed_url, topic = (feed.get("url") or "").strip(), feed.get("topic")
            if not feed_url:
                continue
            topic = (topic or "General").strip()
            # gnews 源只解码前 N 条（后续条目反正会被截断丢弃）；解码是整轮最慢的一步
            gnews_left = _env_int("GNEWS_DECODE_PER_FEED", 12)
            try:
                entries = _feed_entries(source, feed if isinstance(feed, dict) else {"url": feed_url})
            except Exception as exc:  # 单个源失败不影响整体
                _log(f"[warn] 源不可用: {name} {feed_url} ({type(exc).__name__}: {exc})")
                continue
            _log(f"[rss] {name} [{topic}] {len(entries)} 条 ← {feed_url}")
            for entry in entries:
                if time.monotonic() >= limit:
                    _log(f"[warn] RSS 阶段到点，停止处理 {name}")
                    break
                raw_link = (entry.get("link") or "").strip()
                title = _WS_RE.sub(" ", entry.get("title") or "").strip()
                if not raw_link or not title:
                    continue
                url = strip_tracking(raw_link)
                if "news.google.com" in url:
                    if gnews_left <= 0:
                        continue
                    url = _unwrap_google_news(url)
                    gnews_left -= 1
                published = _entry_time(entry)
                item = {
                    "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
                    "title": title,
                    "source": name,
                    "topic": topic,
                    "published": published.strftime("%Y-%m-%dT%H:%M:%SZ") if published else None,
                    "_ts": calendar.timegm(published.timetuple()) if published else 0,
                    "image": _entry_image(entry),
                    "url": url,
                }
                old = dedup.get(url)
                if old is None or item["_ts"] > old["_ts"]:
                    dedup[url] = item
        # 同一源内按时间排序后截断，避免单一源占满配额
        kept = sorted(
            (it for it in dedup.values() if it["source"] == name),
            key=lambda it: it["_ts"],
            reverse=True,
        )[:max_per_source]
        _log(f"[rss] {name} 保留 {len(kept)} 条")
        allowed.update(it["url"] for it in kept)

    all_items = sorted(
        (it for it in dedup.values() if it["url"] in allowed),
        key=lambda it: it["_ts"],
        reverse=True,
    )[:max_total]
    for it in all_items:
        it.pop("_ts", None)
    return all_items


def _take_auto_archive() -> bool:
    global _auto_left
    with _auto_lock:
        if _auto_left <= 0:
            return False
        _auto_left -= 1
        return True


def run_probe(url: str, auto_archive: bool = False) -> dict:
    """子进程调用探测环节；任何异常都以 original 兜底，不崩溃。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    cap = 75.0 if auto_archive else 45.0
    timeout = max(5.0, min(cap, _remaining()))
    cmd = [sys.executable, "-X", "utf8", str(PROBE_CLI), url]
    if auto_archive:
        cmd += ["--auto-archive", "--wait", os.environ.get("AUTO_ARCHIVE_WAIT", "40")]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            cwd=str(BASE),
        )
    except subprocess.TimeoutExpired:
        _log(f"[probe] 超时，按原文兜底: {url}")
        return {"status": "original"}
    if proc.stderr and proc.stderr.strip():
        sys.stderr.write(proc.stderr)  # 透传子进程日志（含"需要人工验证"提示）
    if proc.returncode == EXIT_CHALLENGE:
        _log(f"[probe] 验证待处理: {url}")
        try:
            lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            data = json.loads(lines[-1])
            if isinstance(data, dict) and data.get("status") == "challenge":
                return data
        except (IndexError, json.JSONDecodeError):
            pass
        return {"status": "challenge"}
    if proc.returncode == 0:
        try:
            lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            return json.loads(lines[-1])
        except (IndexError, json.JSONDecodeError):
            _log(f"[probe] 输出解析失败，按原文兜底: {url}")
            return {"status": "original"}
    _log(f"[probe] 异常退出码 {proc.returncode}，按原文兜底: {url}")
    return {"status": "original"}


def apply_entry(item: dict, entry: dict) -> None:
    item["status"] = entry.get("status", "original")
    item["archive_url"] = entry.get("archive_url")
    item["wayback_url"] = entry.get("wayback_url")


def probe_stage(items: list, cache: dict, now: float, now_iso: str, paywalled: set) -> None:
    """快照探测：缓存优先；未命中/过期条目按预算并发探测（付费墙源可自动存档）。"""
    budget = _env_int("MAX_PROBES_PER_RUN", 35)
    pending = []
    for item in items:
        # 直连/转载版正文已经进 news-cache：优先它，不再为这条探测 archive
        if item.get("_direct") or _text_cached(cache, item["url"], now):
            item["status"] = "direct"
            item["archive_url"] = None
            item["wayback_url"] = None
            continue
        fresh = cache_store.fresh_entry(cache, item["url"], now)
        if fresh is not None:
            apply_entry(item, fresh)
        elif budget > 0 and time.monotonic() < DEADLINE:
            budget -= 1
            pending.append(item)
        else:
            # 本轮预算耗尽：保持原文兜底且不写缓存，下一轮继续探测
            item["status"] = "original"
            item["archive_url"] = None
            item["wayback_url"] = None

    if not pending:
        return
    workers = max(1, _env_int("PROBE_WORKERS", 2))
    _log(f"[probe] 待探测 {len(pending)} 条，并发 {workers}")
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for item in pending:
            # 只对付费墙源尝试 "archive this url"：普通源 Wayback 已够用，
            # 且自动存档单条要等 30-60s，必须严格限量。
            auto = item["source"] in paywalled and _take_auto_archive()
            futures[pool.submit(run_probe, item["url"], auto)] = item
            time.sleep(0.4)  # 错开 archive.today 请求，降低 429
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                result = fut.result()
            except Exception as exc:
                _log(f"[probe] 异常: {item['url']} {type(exc).__name__}")
                result = {"status": "original"}
            results[item["url"]] = result

    for item in pending:
        result = results.get(item["url"]) or {"status": "original"}
        entry = {
            "status": result.get("status", "original"),
            "archive_url": result.get("archive_url"),
            "wayback_url": result.get("wayback_url"),
            "checked_at": now_iso,
        }
        cache[item["url"]] = entry
        apply_entry(item, entry)


def _synd_cache_fresh(marker: dict, now: float) -> bool:
    ts = cache_store.parse_iso(marker.get("at"))
    if not ts:
        return False
    # 转载版命中基本稳定（30 天）；未命中 12h 后重试（新文章可能还没被 Google News 收录）
    ttl = 30 * 86400 if marker.get("ok") else 12 * 3600
    return now - ts < ttl


def syndication_stage(items: list, cache: dict, now: float, now_iso: str,
                      paywalled: set, providers_by_source: dict) -> dict:
    """付费墙源：用转载版补齐标题/封面图，并缓存转载页地址（正文后续再抓）。

    返回 {原文url: 本次已解析的转载结果}，供正文阶段直接复用，避免重复检索。
    """
    budget = _env_int("SYND_BUDGET", 6)
    payloads = {}
    stage_deadline = min(DEADLINE, time.monotonic() + 300)
    for item in items:
        if budget <= 0 or time.monotonic() >= stage_deadline:
            break
        if item["source"] not in paywalled:
            continue
        if item.get("image") and item.get("syndicated_url"):
            continue
        key = "synd:" + item["url"]
        marker = cache.get(key)
        if isinstance(marker, dict) and _synd_cache_fresh(marker, now):
            if marker.get("ok"):
                item["syndicated_url"] = marker.get("url")
                item["syndicated_title"] = marker.get("title")
                if not item.get("image") and marker.get("image"):
                    item["image"] = marker["image"]
            continue
        providers = providers_by_source.get(item["source"]) or list(syndication.DEFAULT_ORDER)
        budget -= 1
        try:
            res = syndication.find_copy(
                item["title"], providers,
                deadline=min(stage_deadline, time.monotonic() + 45),
            )
        except Exception as exc:
            _log(f"[synd] 解析失败: {type(exc).__name__}: {str(exc)[:80]}")
            res = None
        if res:
            item["syndicated_url"] = res["url"]
            item["syndicated_title"] = res.get("title")
            if not item.get("image") and res.get("image"):
                item["image"] = res["image"]
            payloads[item["url"]] = res
            cache[key] = {"at": now_iso, "ok": 1, "url": res["url"],
                          "title": res.get("title"), "image": res.get("image"),
                          "provider": res.get("provider")}
            _log(f"[synd] {item['source']} ← {res.get('provider')}: {res['url'][:80]}")
        else:
            cache[key] = {"at": now_iso, "ok": 0}
    return payloads


def _load_syndicated(marker: dict, title: str, deadline: float) -> dict | None:
    """从缓存的转载页地址重新抓取 HTML 并解析正文（不存正文到公开缓存）。"""
    url = marker.get("url")
    if not url:
        return None
    html = rd.fetch_html(url, deadline=deadline)
    if not html:
        return None
    art = rd.extract_article_html(html, marker.get("title") or title, url)
    if not art or not rd.quality_ok(art["blocks"], title):
        return None
    return {"url": url, "title": art.get("title"), "image": art.get("image"), "blocks": art["blocks"]}


def _text_cached(cache: dict, url: str, now: float) -> bool:
    marker = cache.get("text:" + url)
    if not isinstance(marker, dict) or marker.get("v") != rd.TEXT_CACHE_VERSION or not marker.get("ok"):
        return False
    ts = cache_store.parse_iso(marker.get("at"))
    return bool(ts and now - ts < 30 * 86400)


def direct_stage(items: list, cache: dict, now: float, now_iso: str,
                 synd_payloads: dict, direct_sources: set) -> int:
    """直连正文优先：能直接读的源 / 已找到转载版的付费墙源先写 news-cache。

    这一步放在 archive 探测之前；成功的条目会跳过后续 archive 探测。
    失败的条目不写 marker，交给 probe + text_stage 再试（archive/Jina 兜底）。
    """
    token = os.environ.get("NEWS_CACHE_TOKEN", "").strip()
    if not token:
        return 0
    jina_key = os.environ.get("JINA_KEY", "").strip() or None
    repo = os.environ.get("NEWS_CACHE_REPO", "lin-z-h/news-cache")
    budget = _env_int("DIRECT_TEXT_BUDGET", 16)
    todo = []
    for item in items:
        if budget <= 0 or time.monotonic() >= DEADLINE:
            break
        if _text_cached(cache, item["url"], now):
            item["_direct"] = True
            continue
        if item["source"] not in direct_sources and item["url"] not in synd_payloads:
            continue
        budget -= 1
        todo.append(item)
    if not todo:
        return 0

    def work(item: dict):
        synd = synd_payloads.get(item["url"])
        if synd is None:
            marker = cache.get("synd:" + item["url"])
            if isinstance(marker, dict) and marker.get("ok"):
                synd = _load_syndicated(marker, item["title"],
                                        min(DEADLINE, time.monotonic() + 40))
        blocks = rd.fetch_text(
            item["url"], key=jina_key, title=item["title"], syndicated=synd,
            deadline=min(DEADLINE, time.monotonic() + 60),
        )
        return item, blocks

    stored = 0
    workers = max(1, _env_int("DIRECT_TEXT_WORKERS", 2))
    _log(f"[direct] 直连正文 {len(todo)} 条，并发 {workers}")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, it) for it in todo]
        for fut in as_completed(futures):
            try:
                item, blocks = fut.result()
            except Exception as exc:
                _log(f"[direct] 异常: {type(exc).__name__}: {str(exc)[:80]}")
                continue
            if not blocks:
                continue
            payload = {
                "v": rd.TEXT_CACHE_VERSION,
                "url": item["url"], "title": item["title"], "fetched_at": now_iso,
                "source": "syndicated" if item.get("syndicated_url") else "direct",
                "syndicated_url": item.get("syndicated_url"),
                "blocks": blocks,
            }
            try:
                ok = rd.cache_put(token, repo, item["id"] + ".json", payload)
            except Exception as exc:
                _log(f"[direct] 写入失败: {type(exc).__name__}: {str(exc)[:60]}")
                ok = False
            if ok:
                cache["text:" + item["url"]] = {"at": now_iso, "ok": 1,
                                                 "v": rd.TEXT_CACHE_VERSION}
                item["_direct"] = True
                stored += 1
    return stored


def text_stage(items: list, cache: dict, now: float, now_iso: str,
               synd_payloads: dict) -> int:
    """正文缓存：转载版 → 原文 → Jina，只写私有仓库 news-cache。"""
    token = os.environ.get("NEWS_CACHE_TOKEN", "").strip()
    if not token:
        return 0
    jina_key = os.environ.get("JINA_KEY", "").strip() or None
    repo = os.environ.get("NEWS_CACHE_REPO", "lin-z-h/news-cache")
    budget = _env_int("READ_TEXT_BUDGET", 20)
    todo = []
    for item in items:
        if budget <= 0 or time.monotonic() >= DEADLINE:
            break
        marker = cache.get("text:" + item["url"])
        if marker and cache_store.parse_iso(marker.get("at")):
            age = now - cache_store.parse_iso(marker.get("at"))
            if marker.get("v") == rd.TEXT_CACHE_VERSION and age < (30 * 86400 if marker.get("ok") else 3 * 86400):
                continue
        budget -= 1
        todo.append(item)
    if not todo:
        return 0
    workers = max(1, _env_int("TEXT_WORKERS", 2))
    _log(f"[text] 待抓正文 {len(todo)} 条，并发 {workers}")

    def work(item: dict):
        synd = synd_payloads.get(item["url"])
        if synd is None:
            marker = cache.get("synd:" + item["url"])
            if isinstance(marker, dict) and marker.get("ok"):
                if not item.get("syndicated_url"):
                    item["syndicated_url"] = marker.get("url")
                    item["syndicated_title"] = marker.get("title")
                synd = _load_syndicated(marker, item["title"],
                                        min(DEADLINE, time.monotonic() + 40))
        blocks = rd.fetch_text(
            item["url"], key=jina_key, title=item["title"], syndicated=synd,
            archive_url=item.get("archive_url") or "",
            deadline=min(DEADLINE, time.monotonic() + 90),
        )
        return item, blocks

    stored = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(work, it) for it in todo]
        for fut in as_completed(futures):
            try:
                item, blocks = fut.result()
            except Exception as exc:
                _log(f"[text] 异常: {type(exc).__name__}: {str(exc)[:80]}")
                continue
            ok = False
            if blocks:
                payload = {
                    "v": rd.TEXT_CACHE_VERSION,
                    "url": item["url"], "title": item["title"], "fetched_at": now_iso,
                    "source": "syndicated" if item.get("syndicated_url") else "web",
                    "syndicated_url": item.get("syndicated_url"),
                    "blocks": blocks,
                }
                try:
                    ok = rd.cache_put(token, repo, item["id"] + ".json", payload)
                except Exception as exc:
                    _log(f"[text] 写入失败: {type(exc).__name__}: {str(exc)[:60]}")
            cache["text:" + item["url"]] = {"at": now_iso, "ok": 1 if ok else 0,
                                             "v": rd.TEXT_CACHE_VERSION}
            if ok:
                stored += 1
    return stored


def _favorites_stage(items: list, cache: dict, now: float, now_iso: str) -> int:
    """收藏保障：为掉出 news.json 的收藏文章补正文（独立预算）。"""
    token = os.environ.get("NEWS_CACHE_TOKEN", "").strip()
    if not token:
        return 0
    jina_key = os.environ.get("JINA_KEY", "").strip() or None
    repo = os.environ.get("NEWS_CACHE_REPO", "lin-z-h/news-cache")
    budget = _env_int("FAV_TEXT_BUDGET", 10)
    try:
        fav = rd.cache_read(token, repo, "favorites.json")
    except Exception:
        return 0
    fav_list = (fav or {}).get("items") or []
    ok_count = 0
    for f in fav_list:
        if budget <= 0 or time.monotonic() >= DEADLINE:
            break
        url = (f.get("url") or "").strip()
        fid = f.get("id")
        if not url or not fid or not re.match(r"^https?://", url):
            continue
        marker = cache.get("text:" + url)
        if marker and cache_store.parse_iso(marker.get("at")):
            age = now - cache_store.parse_iso(marker.get("at"))
            if marker.get("v") == rd.TEXT_CACHE_VERSION and age < (180 * 86400 if marker.get("ok") else 3 * 86400):
                continue
        budget -= 1
        blocks = rd.fetch_text(url, key=jina_key, title=f.get("title") or fid,
                               deadline=min(DEADLINE, time.monotonic() + 90))
        if not blocks:
            cache["text:" + url] = {"at": now_iso, "ok": 0, "v": rd.TEXT_CACHE_VERSION}
            continue
        payload = {"v": rd.TEXT_CACHE_VERSION, "url": url,
                   "title": f.get("title") or fid, "fetched_at": now_iso,
                   "blocks": blocks}
        ok = rd.cache_put(token, repo, fid + ".json", payload)
        cache["text:" + url] = {"at": now_iso, "ok": 1 if ok else 0, "v": rd.TEXT_CACHE_VERSION}
        if ok:
            ok_count += 1
    return ok_count


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError:
            pass

    config_path = Path(os.environ.get("SOURCES_CONFIG") or BASE / "config" / "sources.json")
    output_path = Path(os.environ.get("OUTPUT_PATH") or BASE / "docs" / "news.json")
    cache_path = Path(os.environ.get("CACHE_PATH") or BASE / "data" / "cache.json")

    with open(config_path, "r", encoding="utf-8") as fh:
        config = json.load(fh)

    cache = cache_store.load(cache_path)
    now = datetime.now(timezone.utc).timestamp()
    now_iso = _utc_now_iso()
    items: list = []
    status_count: dict = {}

    try:
        items = collect_items(config)
        _log(f"[main] 共收集 {len(items)} 条（去重、截断后）")

        # og:image 回填：给无缩略图的条目抓文章页 og:image（仅元数据），
        # 结果（含失败）缓存 30 天；每轮限量，几轮之内逐步补齐。
        img_budget = _env_int("OG_IMAGE_BUDGET", 15)
        for item in items:
            if img_budget <= 0 or time.monotonic() >= DEADLINE:
                break
            if item.get("image"):
                continue
            key = "img:" + item["url"]
            cached = cache.get(key)
            if isinstance(cached, dict):
                ts = cache_store.parse_iso(cached.get("checked_at"))
                if ts and now - ts < 30 * 86400:
                    item["image"] = cached.get("image")
                    continue
            img_budget -= 1
            og = fetch_og_image(item["url"])
            cache[key] = {"image": og, "checked_at": now_iso}
            if og:
                item["image"] = og

        paywalled = {s.get("name") for s in config.get("sources", [])
                     if s.get("cover_only_when_unarchived")}
        direct_sources = {s.get("name") for s in config.get("sources", [])
                          if s.get("direct")}
        providers_by_source = {
            s.get("name"): s.get("syndication") for s in config.get("sources", [])
            if s.get("syndication")
        }

        # 顺序很重要：先转载版/原文直连写 news-cache，再对没拿到的条目探测 archive
        synd_payloads = syndication_stage(items, cache, now, now_iso, paywalled, providers_by_source)
        direct_stored = direct_stage(items, cache, now, now_iso, synd_payloads, direct_sources)
        if direct_stored:
            _log(f"[direct] 直连正文新增 {direct_stored} 条 → news-cache")
        probe_stage(items, cache, now, now_iso, paywalled)
        stored = text_stage(items, cache, now, now_iso, synd_payloads)
        if stored:
            _log(f"[text] 正文缓存新增 {stored} 条 → news-cache")
        fav_ok = _favorites_stage(items, cache, now, now_iso)
        if fav_ok:
            _log(f"[text] 收藏文章正文补齐 {fav_ok} 条")

        # 隐藏规则：墙源（hide_unarchived）里"探测确认无快照"的文章先不上页面——
        # 点进去既无快照也读不了正文，是死胡同；等 12h 验证重试或镜像站收录后自动回归。
        # 只隐藏探测确认过的（cache 有记录）；被人机验证卡住的（challenge）保留展示。
        hide_map = {s.get("name"): bool(s.get("hide_unarchived")) for s in config.get("sources", [])}
        visible = []
        hidden = 0
        for it in items:
            if (hide_map.get(it["source"]) and it["status"] in ("original", "unarchived")
                    and not it.get("archive_url") and not it.get("wayback_url") and it["url"] in cache):
                hidden += 1
                continue
            visible.append(it)
        if hidden:
            _log(f"[main] 隐藏无快照的墙源文章 {hidden} 条（后续快照出现会自动回归）")
        items = visible

        for it in items:
            # 付费墙源且没有可用快照：只渲染封面，等用户点进文章再决定手动流程。
            # 有正文缓存时前端会先读缓存（cover_only 在缓存之后才生效）。
            it["cover_only"] = bool(
                it["source"] in paywalled
                and it["status"] not in ("archive", "wayback")
            )
            status_count[it["status"]] = status_count.get(it["status"], 0) + 1
    except Exception as exc:
        _log(f"[main] 流程异常，输出已完成部分: {type(exc).__name__}: {str(exc)[:120]}")
        if not items:
            # 整轮在抓取阶段就失败：保留上一次的 news.json，避免把站点刷成空白
            try:
                with open(output_path, "r", encoding="utf-8") as fh:
                    items = json.load(fh).get("items") or []
            except (OSError, ValueError):
                items = []
    finally:
        payload = {
            "generated_at": now_iso,
            "sources": [s.get("name") for s in config.get("sources", [])],
            "items": items,
        }
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        cache_store.save(cache_path, cache)

    _log(f"[main] 完成: {dict(status_count)}，输出 {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
