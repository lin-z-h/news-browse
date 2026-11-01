"""主流程：抓 RSS → 清洗链接 → 探测快照 → 生成 docs/news.json + data/cache.json。

只保留标题、来源、发布时间、链接、快照链接等元数据，不抓取、不存储新闻正文。

探测以子进程方式调用 archive_probe.py（独立环节）：
* 退出码 0   → stdout 最后一行为 JSON 结果；
* 退出码 75  → 该条新闻标记为"验证待处理"(challenge)，流程继续；
* 其他/超时  → 按"原文兜底"(original)处理，流程继续。

环境变量：
    SOURCES_CONFIG      源配置路径（默认 config/sources.json）
    OUTPUT_PATH         输出路径（默认 docs/news.json）
    CACHE_PATH          缓存路径（默认 data/cache.json）
    MAX_PER_SOURCE / MAX_TOTAL / MAX_PROBES_PER_RUN   数量上限
    HTTP_PROXY / HTTPS_PROXY   本机走代理时直接生效（requests 原生支持）
"""
from __future__ import annotations

import calendar
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import requests

import cache_store
from clean_url import strip_tracking

BASE = Path(__file__).resolve().parents[1]
PROBE_CLI = BASE / "scripts" / "archive_probe.py"

EXIT_CHALLENGE = 75  # 与 verify_guard.EXIT_CHALLENGE 保持一致

RSS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
FEED_TIMEOUT = (10, 30)
FEED_HEADERS = {"Accept": "application/rss+xml, application/xml, text/xml, */*"}
FEED_HEADERS_UA = {**FEED_HEADERS, "User-Agent": RSS_UA}

_WS_RE = re.compile(r"\s+")


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
            resp = creq.get(url, impersonate="safari17_0", timeout=40, headers=FEED_HEADERS)
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

        resp = creq.get(url, impersonate="safari17_0", timeout=15,
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


def collect_items(config: dict) -> list:
    """抓取全部源 → 清洗 → 去重 → 按时间排序 → 截断。

    feed 配置支持字符串（仅 URL）或对象 {"url": ..., "topic": ...}，
    topic 用于前端的话题/分类筛选。
    """
    max_per_source = int(config.get("max_per_source", _env_int("MAX_PER_SOURCE", 40)))
    max_total = int(config.get("max_total", _env_int("MAX_TOTAL", 120)))
    dedup = {}
    allowed = set()

    for source in config.get("sources", []):
        name = source.get("name", "unknown")
        for feed in source.get("feeds", []):
            if isinstance(feed, str):
                feed_url, topic = feed, None
            else:
                feed_url, topic = (feed.get("url") or "").strip(), feed.get("topic")
            if not feed_url:
                continue
            topic = (topic or "General").strip()
            gnews_left = 60  # gnews 源只解码前 N 条（后续条目反正会被截断丢弃）
            try:
                if isinstance(feed, dict) and feed.get("type") == "tvb":
                    entries = fetch_tvb_entries(feed_url)
                else:
                    entries = fetch_entries(feed_url)
            except Exception as exc:  # 单个源失败不影响整体
                _log(f"[warn] 源不可用: {name} {feed_url} ({type(exc).__name__}: {exc})")
                continue
            _log(f"[rss] {name} [{topic}] {len(entries)} 条 ← {feed_url}")
            for entry in entries:
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


def run_probe(url: str) -> dict:
    """子进程调用探测环节；任何异常都以 original 兜底，不崩溃。"""
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", str(PROBE_CLI), url],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=float(os.environ.get("PROBE_TOTAL_TIMEOUT", "180")),
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
        # 退出码 75 = 验证待处理；stdout 里可能带有 Wayback 兜底结果，一并采用
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
    items = collect_items(config)
    _log(f"[main] 共收集 {len(items)} 条（去重、截断后）")

    now = datetime.now(timezone.utc).timestamp()
    now_iso = _utc_now_iso()

    # og:image 回填：给无缩略图的条目抓文章页 og:image（仅元数据），
    # 结果（含失败）缓存 30 天；每轮限量，几轮之内逐步补齐。
    img_budget = _env_int("OG_IMAGE_BUDGET", 25)
    img_cutoff = now - 30 * 86400
    for item in items:
        if item.get("image"):
            continue
        key = "img:" + item["url"]
        cached = cache.get(key)
        if isinstance(cached, dict):
            ts = cache_store.parse_iso(cached.get("checked_at"))
            if ts and now - ts < 30 * 86400:
                item["image"] = cached.get("image")
                continue
        if img_budget <= 0:
            continue
        img_budget -= 1
        og = fetch_og_image(item["url"])
        cache[key] = {"image": og, "checked_at": now_iso}
        if og:
            item["image"] = og
            _log(f"[img] og:image ← {item['url'][:80]}")
    budget = _env_int("MAX_PROBES_PER_RUN", 30)
    status_count = {}

    for item in items:
        fresh = cache_store.fresh_entry(cache, item["url"], now)
        if fresh is not None:
            apply_entry(item, fresh)
        elif budget > 0:
            result = run_probe(item["url"])
            budget -= 1
            entry = {
                "status": result.get("status", "original"),
                "archive_url": result.get("archive_url"),
                "wayback_url": result.get("wayback_url"),
                "checked_at": now_iso,
            }
            cache[item["url"]] = entry
            apply_entry(item, entry)
        else:
            # 本轮预算耗尽：保持原文兜底且不写缓存，下一轮继续探测
            item["status"] = "original"
            item["archive_url"] = None
            item["wayback_url"] = None

        status_count[item["status"]] = status_count.get(item["status"], 0) + 1

    # 隐藏规则：墙源（hide_unarchived）里"探测确认无快照"的文章先不上页面——
    # 点进去既无快照也读不了正文，是死胡同；等 12h 验证重试或镜像站收录后自动回归。
    # 只隐藏探测确认过的（cache 有记录）；被人机验证卡住的（challenge）保留展示。
    hide_map = {s.get("name"): bool(s.get("hide_unarchived")) for s in config.get("sources", [])}
    visible = []
    hidden = 0
    for it in items:
        if (hide_map.get(it["source"]) and it["status"] == "original"
                and not it.get("archive_url") and not it.get("wayback_url") and it["url"] in cache):
            hidden += 1
            continue
        visible.append(it)
    if hidden:
        _log(f"[main] 隐藏无快照的墙源文章 {hidden} 条（后续快照出现会自动回归）")
    items = visible

    # 正文缓存（私有仓库 news-cache，仅 token 持有者可见）：CI 抓正文并存入，
    # 阅读模式优先读取，摆脱对本地网络的依赖。只存清洗后的段落与图片地址。
    cache_token = os.environ.get("NEWS_CACHE_TOKEN", "").strip()
    jina_key = os.environ.get("JINA_KEY", "").strip()
    text_budget = _env_int("READ_TEXT_BUDGET", 40)
    fav_budget = _env_int("FAV_TEXT_BUDGET", 20)
    if cache_token:
        import readability as rd
        stored = 0
        try:
            for it in items:
                if text_budget <= 0:
                    break
                marker = cache.get("text:" + it["url"])
                if marker and cache_store.parse_iso(marker.get("at")):
                    age = now - cache_store.parse_iso(marker.get("at"))
                    if age < (30 * 86400 if marker.get("ok") else 3 * 86400):
                        continue
                text_budget -= 1
                blocks = rd.fetch_text(it["url"], key=jina_key or None)
                payload = {"url": it["url"], "title": it["title"], "fetched_at": now_iso,
                           "blocks": blocks or []}
                ok = rd.cache_put(cache_token, "lin-z-h/news-cache", it["id"] + ".json", payload)
                cache["text:" + it["url"]] = {"at": now_iso, "ok": 1 if ok else 0}
                if ok:
                    stored += 1
                time.sleep(1)
            if stored:
                _log(f"[text] 正文缓存新增 {stored} 条 → lin-z-h/news-cache")

            # 收藏保障：读取私有仓库里的收藏清单，为掉出 news.json 的收藏文章补正文
            # （收藏 = 用户明确要长期可读的内容，独立预算，优先级高于普通文章）
            try:
                fav = rd.cache_read(cache_token, "lin-z-h/news-cache", "favorites.json")
            except Exception:
                fav = None
            fav_list = (fav or {}).get("items") or []
            if fav_list:
                fav_ok = 0
                for f in fav_list:
                    if fav_budget <= 0:
                        break
                    u = (f.get("url") or "").strip()
                    fid = f.get("id")
                    if not u or not fid or not re.match(r"^https?://", u):
                        continue
                    marker = cache.get("text:" + u)
                    if marker and cache_store.parse_iso(marker.get("at")):
                        age = now - cache_store.parse_iso(marker.get("at"))
                        if age < (180 * 86400 if marker.get("ok") else 3 * 86400):
                            continue
                    fav_budget -= 1
                    blocks = rd.fetch_text(u, key=jina_key or None)
                    payload = {"url": u, "title": f.get("title") or fid, "fetched_at": now_iso,
                               "blocks": blocks or []}
                    ok = rd.cache_put(cache_token, "lin-z-h/news-cache", fid + ".json", payload)
                    cache["text:" + u] = {"at": now_iso, "ok": 1 if ok else 0}
                    if ok:
                        fav_ok += 1
                    time.sleep(1)
                if fav_ok:
                    _log(f"[text] 收藏文章正文补齐 {fav_ok} 条")
        except Exception as exc:
            _log(f"[warn] 正文缓存失败: {type(exc).__name__}: {str(exc)[:80]}")

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
