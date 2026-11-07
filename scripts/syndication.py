"""转载版（联合发布）解析：付费墙源的正文、标题、图片兜底。

思路：
* 付费墙原文（Bloomberg / FT / WSJ / Economist 等）在 CI 里通常只能拿到导语；
* 这些媒体的稿件常被 The Edge Malaysia / The Edge Singapore / Yahoo Finance /
  BNN Bloomberg / Financial Post / Business Standard / Moneyweb 等转载；
* 用 Google News 按“精确标题 + 站点”检索转载版，解码跳转链接，直接抓 HTML，
  用 readability.extract_article_html 取 JSON-LD/文章段落 → 得到标题、图片、正文。

只抓公开的转载页面，不绕付费墙；每个条目有预算与超时，失败不影响主流程。
"""
from __future__ import annotations

import hashlib
import re
import socket
import time
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Iterable, Optional

import requests

import readability as rd

# 第三方解码库内部可能有无 timeout 的请求；全局兜底避免挂死整轮
socket.setdefaulttimeout(15)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

PROVIDERS = {
    "edge_my": {
        "name": "The Edge Malaysia",
        "hosts": ("theedgemalaysia.com",),
        "site": "theedgemalaysia.com",
    },
    "edge_sg": {
        "name": "The Edge Singapore",
        "hosts": ("theedgesingapore.com",),
        "site": "theedgesingapore.com",
    },
    "yahoo": {
        "name": "Yahoo Finance",
        "hosts": ("finance.yahoo.com", "sg.finance.yahoo.com", "uk.finance.yahoo.com",
                  "ca.finance.yahoo.com", "au.finance.yahoo.com"),
        "site": "finance.yahoo.com",
    },
    "bnn": {
        "name": "BNN Bloomberg",
        "hosts": ("bnnbloomberg.ca",),
        "site": "bnnbloomberg.ca",
    },
    "fpost": {
        "name": "Financial Post",
        "hosts": ("financialpost.com",),
        "site": "financialpost.com",
    },
    "bst": {
        "name": "Business Standard",
        "hosts": ("business-standard.com",),
        "site": "business-standard.com",
    },
    "moneyweb": {
        "name": "Moneyweb",
        "hosts": ("moneyweb.co.za",),
        "site": "moneyweb.co.za",
    },
    "businesstoday": {
        "name": "BusinessToday Malaysia",
        "hosts": ("businesstoday.com.my",),
        "site": "businesstoday.com.my",
    },
    "sedaily": {
        "name": "Seoul Economic Daily",
        "hosts": ("en.sedaily.com", "sedaily.com"),
        "site": "en.sedaily.com",
    },
    "economictimes": {
        "name": "The Economic Times",
        "hosts": ("economictimes.indiatimes.com",),
        "site": "economictimes.indiatimes.com",
    },
    "bworld": {
        "name": "BusinessWorld",
        "hosts": ("bworldonline.com",),
        "site": "bworldonline.com",
    },
    "investing": {
        "name": "Investing.com",
        "hosts": ("investing.com",),
        "site": "investing.com",
    },
}

DEFAULT_ORDER = ("edge_my", "edge_sg", "yahoo", "bnn", "fpost", "bst", "moneyweb",
                 "businesstoday", "sedaily", "economictimes", "bworld", "investing")

_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at", "by",
    "from", "as", "is", "are", "was", "were", "be", "been", "it", "its", "this", "that",
    "after", "before", "over", "under", "says", "said", "new", "amid",
}

_WORD_NUM = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
}
_DURATION_RE = re.compile(
    r"(\d{1,3}|" + "|".join(_WORD_NUM) + r")\s*(years?|months?|weeks?|days?)\b", re.I)


def _durations(text: str) -> set:
    """标题里的时长（月）：Two Years → 24，19 Months → 19，用于拒绝同题旧文。"""
    out = set()
    for num, unit in _DURATION_RE.findall(text or ""):
        val = int(num) if num.isdigit() else _WORD_NUM.get(num.lower())
        if val is None:
            continue
        unit = unit.lower()
        if unit.startswith("year"):
            out.add(val * 12)
        elif unit.startswith("month"):
            out.add(val)
        elif unit.startswith("week"):
            out.add(val / 4.0)
        elif unit.startswith("day"):
            out.add(val / 30.0)
    return out


def _tokens(title: str) -> set:
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return {w for w in words if len(w) > 2 and w not in _STOP}


def title_similar(a: str, b: str) -> float:
    """标题相似度：词集合 Jaccard（转载版常带 “- Bloomberg” 后缀或改写小标题）。"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / max(1, len(ta | tb))


def _clean_title(t: str) -> str:
    t = re.sub(r"\s*[-–—|]\s*(The Edge (?:Malaysia|Singapore)|Yahoo Finance(?:\s+\w+)?|"
               r"BNN Bloomberg|Financial Post|Business Standard|Moneyweb)\s*$", "", t or "", flags=re.I)
    t = re.sub(r"\s*[-–—]\s*Bloomberg\s*$", "", t, flags=re.I)
    return re.sub(r"\s+", " ", t).strip()


def _google_news(query: str, timeout: float = 25) -> list:
    url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
           + "&hl=en-US&gl=US&ceid=US:en")
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for item in root.iter("item"):
        out.append({
            "title": (item.findtext("title") or "").strip(),
            "link": (item.findtext("link") or "").strip(),
            "source": (item.findtext("source") or "").strip(),
        })
    return out


def _decode(link: str) -> Optional[str]:
    if not link:
        return None
    for attempt in range(2):
        try:
            from googlenewsdecoder import gnewsdecoder

            res = gnewsdecoder(link, interval=0)
            real = res.get("decoded_url") if isinstance(res, dict) else res
            if isinstance(real, str) and real.startswith("http"):
                return real
        except Exception:
            pass
        if attempt == 0:
            time.sleep(0.8)
    return None


def _host_ok(url: str, hosts: Iterable[str]) -> bool:
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return False
    return any(host == h or host.endswith("." + h) for h in hosts)


def _remaining(deadline: Optional[float]) -> float:
    if not deadline:
        return 25.0
    return max(1.0, min(25.0, deadline - time.monotonic()))


def _provider_for(url: str, provider_ids: Iterable[str]) -> Optional[dict]:
    for pid in provider_ids:
        provider = PROVIDERS.get(pid)
        if provider and _host_ok(url, provider["hosts"]):
            return provider
    return None


def _date_ts(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _try_copy(real: str, cand_title: str, title: str, provider: dict,
              deadline: Optional[float], published: str = "") -> Optional[dict]:
    html = rd.fetch_html(real, deadline=deadline)
    if not html:
        return None
    art = rd.extract_article_html(html, cand_title, real)
    if not art or not rd.quality_ok(art["blocks"], title):
        return None
    # 同标题不同日期的旧文（如 “19 months” vs “Two Years”）必须拒绝
    if published and art.get("published"):
        a, b = _date_ts(published), _date_ts(art.get("published"))
        if a and b and abs(a - b) > 3 * 86400:
            return None
    # 日期没抓到时的兜底：标题时长必须相容（two years=24 months）
    found_title = art.get("title") or cand_title
    dt, df = _durations(title), _durations(found_title)
    if dt and df and not (dt & df):
        return None
    overlap = _tokens(title) & _tokens(found_title)
    if len(overlap) < min(4, len(_tokens(title))):
        return None
    if title_similar(title, found_title) < 0.35:
        return None
    return {
        "provider": provider["name"],
        "url": real,
        "title": found_title,
        "image": art.get("image"),
        "published": art.get("published"),
        "blocks": art["blocks"],
    }


def find_copy(title: str, provider_ids: Iterable[str] = DEFAULT_ORDER,
              deadline: Optional[float] = None, max_candidates: int = 8,
              published: str = "") -> Optional[dict]:
    """按标题检索转载版；返回 {provider, url, title, image, blocks} 或 None。

    先做一次通用检索（转载版标题常被改写，如 “China Extends Gold-Buying Streak
    to Two Years” → “China Central Bank Extends Gold-Buying Streak Amid Price
    Decline”），命中已知转载站再抓正文；再用“精确标题 + 站点”补漏。
    """
    title = (title or "").strip()
    if len(title) < 15:
        return None
    query_title = re.sub(r'["“”]', " ", title)
    query_title = re.sub(r"\s+", " ", query_title).strip()
    ids = [pid for pid in provider_ids if pid in PROVIDERS]
    seen = set()
    tried = 0

    # 1) 通用检索：标题改写/不同站点也能覆盖（不加引号/不加 Bloomberg，
    #    否则 Google News 只召回原文，转载版（Seoul Economic Daily 等）会被挤掉）
    for hit in _google_news(query_title, timeout=_remaining(deadline))[:12]:
        if tried >= max_candidates or (deadline and time.monotonic() >= deadline):
            return None
        if "bloomberg.com" in (hit.get("source") or "").lower():
            continue  # 原文本身，不是转载版；省一次 decode
        cand_title = _clean_title(hit["title"])
        if title_similar(title, cand_title) < 0.3:
            continue
        real = _decode(hit["link"])
        if not real or real in seen:
            continue
        seen.add(real)
        provider = _provider_for(real, ids)
        if not provider:
            continue
        tried += 1
        res = _try_copy(real, cand_title, title, provider, deadline, published)
        if res:
            return res

    # 2) 精确标题 + 站点检索：只补前 3 个高优先级源，避免“没有转载”的文章
    #    把 12 个站点全查一遍（这是 syndication 阶段最慢的部分）
    fallback_ids = ids[:3] if tried == 0 else []
    for pid in fallback_ids:
        provider = PROVIDERS.get(pid)
        if not provider or (deadline and time.monotonic() >= deadline):
            continue
        results = _google_news(f'"{query_title}" site:{provider["site"]}',
                               timeout=_remaining(deadline))
        for hit in results[:5]:
            if tried >= max_candidates or (deadline and time.monotonic() >= deadline):
                return None
            cand_title = _clean_title(hit["title"])
            if title_similar(title, cand_title) < 0.45:
                continue
            real = _decode(hit["link"])
            if not real or real in seen:
                continue
            seen.add(real)
            if not _host_ok(real, provider["hosts"]):
                continue
            tried += 1
            res = _try_copy(real, cand_title, title, provider, deadline, published)
            if res:
                return res
    return None


def cache_key(title: str, provider_ids: Iterable[str]) -> str:
    digest = hashlib.sha1((title + "|" + ",".join(provider_ids)).encode("utf-8")).hexdigest()[:16]
    return f"syndsearch:{digest}"
