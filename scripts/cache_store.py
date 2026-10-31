"""探测结果缓存：同一链接不重复探测。

缓存以"清洗后的原文 URL"为键，随仓库提交（data/cache.json），
GitHub Actions 每次运行都能复用上次结果。不同状态使用不同 TTL：
archive 命中的快照基本稳定（180 天），wayback 较稳定（30 天），
未命中 original 3 天后重试，验证待处理 challenge 12 小时后重试。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Optional

TTL_SECONDS = {
    "archive": 180 * 86400,
    "wayback": 30 * 86400,
    "original": 3 * 86400,
    "challenge": 12 * 3600,
}


def load(path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save(path, data: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{os.path.abspath(path)}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, path)


def parse_iso(value) -> Optional[float]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


_parse_iso = parse_iso  # 兼容旧名


def fresh_entry(cache: dict, url: str, now_ts: float) -> Optional[dict]:
    """返回未过期的缓存条目；缺失/损坏/过期返回 None。"""
    entry = cache.get(url)
    if not isinstance(entry, dict) or "status" not in entry:
        return None
    checked = parse_iso(entry.get("checked_at"))
    if checked is None:
        return None
    ttl = TTL_SECONDS.get(entry.get("status"), 86400)
    if now_ts - checked >= ttl:
        return None
    return entry
