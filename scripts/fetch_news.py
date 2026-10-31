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
from datetime import datetime, timezone
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


def collect_items(config: dict) -> list:
    """抓取全部源 → 清洗 → 去重 → 按时间排序 → 截断。"""
    max_per_source = int(config.get("max_per_source", _env_int("MAX_PER_SOURCE", 20)))
    max_total = int(config.get("max_total", _env_int("MAX_TOTAL", 60)))
    dedup = {}
    allowed = set()

    for source in config.get("sources", []):
        name = source.get("name", "unknown")
        got = 0
        for feed_url in source.get("feeds", []):
            try:
                entries = fetch_entries(feed_url)
            except Exception as exc:  # 单个源失败不影响整体
                _log(f"[warn] 源不可用: {name} {feed_url} ({type(exc).__name__}: {exc})")
                continue
            _log(f"[rss] {name} {len(entries)} 条 ← {feed_url}")
            for entry in entries:
                raw_link = (entry.get("link") or "").strip()
                title = _WS_RE.sub(" ", entry.get("title") or "").strip()
                if not raw_link or not title:
                    continue
                url = strip_tracking(raw_link)
                published = _entry_time(entry)
                item = {
                    "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:12],
                    "title": title,
                    "source": name,
                    "published": published.strftime("%Y-%m-%dT%H:%M:%SZ") if published else None,
                    "_ts": calendar.timegm(published.timetuple()) if published else 0,
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
