"""RSS 之外的官方/半官方源适配（可选，有 key 时优先，无 key 回落 RSS）。

* NYT Top Stories API（NYT_API_KEY）
* Guardian Open Platform（GUARDIAN_API_KEY）
* WordPress REST API（TIME 等，无需 key）
* Google News 检索（已在 fetch_news 内实现，不在此模块）

只取标题、链接、发布时间、缩略图等元数据，不抓正文。
"""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
TIMEOUT = (8, 20)


def _iso_to_parsed(value: str):
    try:
        dt = datetime.fromisoformat((value or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.utctimetuple()
    except ValueError:
        return None


def _norm_image(u):
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    return u if u.startswith("http") else None


def _get_json(url: str, params=None, headers=None):
    r = requests.get(url, params=params, headers={**{"User-Agent": UA}, **(headers or {})},
                     timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def fetch_wp_entries(api_url: str, per_page: int = 20) -> list:
    """WordPress REST API（/wp-json/wp/v2/posts）→ feedparser 同形条目。"""
    data = _get_json(api_url, params={
        "per_page": per_page,
        "_fields": "link,title,date,jetpack_featured_media_url,excerpt",
    })
    entries = []
    for post in data if isinstance(data, list) else []:
        title = (post.get("title") or {}).get("rendered") or ""
        title = html.unescape(re.sub(r"<[^>]+>", "", title)).strip()
        link = post.get("link") or ""
        if not title or not link:
            continue
        entries.append({
            "title": title,
            "link": link,
            "published_parsed": _iso_to_parsed(post.get("date") or ""),
            "media_content": [{"url": post["jetpack_featured_media_url"]}]
            if post.get("jetpack_featured_media_url") else [],
        })
    return entries


def fetch_nyt_entries(section: str, api_key: str) -> list:
    """NYT Top Stories API。"""
    section = (section or "home").strip().lower()
    data = _get_json(f"https://api.nytimes.com/svc/topstories/v2/{section}.json",
                     params={"api-key": api_key})
    entries = []
    for art in data.get("results") or []:
        title = (art.get("title") or "").strip()
        link = (art.get("url") or "").strip()
        if not title or not link:
            continue
        image = None
        priority = {"largeThreeByTwo": 0, "mediumThreeByTwo440": 1, "threeByTwoSmallAt2X": 2}
        best_rank = 99
        for media in art.get("multimedia") or []:
            url = _norm_image(media.get("url"))
            rank = priority.get(media.get("format"), 50)
            if url and rank < best_rank:
                image, best_rank = url, rank
        if not image:
            for media in art.get("multimedia") or []:
                image = _norm_image(media.get("url"))
                if image:
                    break
        entries.append({
            "title": title,
            "link": link,
            "published_parsed": _iso_to_parsed(art.get("published_date") or ""),
            "media_content": [{"url": image}] if image else [],
        })
    return entries


def fetch_guardian_entries(section: str, api_key: str) -> list:
    """Guardian Open Platform。"""
    params = {
        "page-size": 20,
        "show-fields": "thumbnail,headline",
        "api-key": api_key,
    }
    if section and section != "all":
        params["section"] = section
    data = _get_json("https://content.guardianapis.com/search", params=params)
    entries = []
    for art in (data.get("response") or {}).get("results") or []:
        title = (art.get("webTitle") or "").strip()
        link = (art.get("webUrl") or "").strip()
        if not title or not link:
            continue
        thumb = _norm_image(((art.get("fields") or {}).get("thumbnail") or ""))
        entries.append({
            "title": title,
            "link": link,
            "published_parsed": _iso_to_parsed(art.get("webPublicationDate") or ""),
            "media_content": [{"url": thumb}] if thumb else [],
        })
    return entries
