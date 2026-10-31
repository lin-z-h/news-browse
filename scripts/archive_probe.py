"""archive.today 快照探测 + Wayback Machine 兜底（可独立运行的探测环节）。

archive.today 没有官方 API，探测方式：
* 依次轮换镜像 archive.ph / archive.md / archive.li / archive.is；
* 请求 <镜像>/<原文URL>，若已有快照会 302 到最新快照页；
* 用响应头 Memento-Datetime 判定命中（可靠信号），最终 URL 形如
  /<快照id>/<原文URL> 作为兜底信号；
* 设置超时、重试、限速；CAPTCHA/429 不崩溃 —— CAPTCHA 走 verify_guard
  （退出码 75 → 上层标记"验证待处理"），429 视为未命中换下一个镜像。

CLI 用法（上层 fetch_news.py 以子进程方式调用，读退出码）：
    python scripts/archive_probe.py <清洗后的URL>
退出码：0 正常（stdout 输出一行 JSON 结果）；75 需要人工验证；
       其他 = 意外错误（上层按"原文兜底"处理）。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.parse
from typing import Optional

import requests

import verify_guard

# 镜像轮换顺序（可用环境变量 ARCHIVE_MIRRORS 覆盖，逗号分隔）
DEFAULT_MIRRORS = [
    "https://archive.ph",
    "https://archive.md",
    "https://archive.li",
    "https://archive.is",
]

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CONNECT_TIMEOUT = float(os.environ.get("PROBE_CONNECT_TIMEOUT", "10"))
READ_TIMEOUT = float(os.environ.get("PROBE_READ_TIMEOUT", "25"))
RETRIES = int(os.environ.get("PROBE_RETRIES", "2"))
RETRY_BACKOFF = float(os.environ.get("PROBE_BACKOFF", "3"))
REQUEST_INTERVAL = float(os.environ.get("PROBE_INTERVAL", "1.5"))

_SNAP_URL_RE = re.compile(r"/[0-9a-z]{6,}/https?://", re.I)


class ChallengeBlocked(Exception):
    """当前链接被验证页拦下，需要人工介入（对应退出码 75）。"""

    def __init__(self, challenge, page_url: str):
        super().__init__(f"challenge page at {page_url}")
        self.challenge = challenge
        self.page_url = page_url


_last_request_ts = 0.0


def _rate_limit() -> None:
    global _last_request_ts
    wait = REQUEST_INTERVAL - (time.time() - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.time()


def _headers() -> dict:
    ua = os.environ.get("ARCHIVE_UA", "").strip() or DEFAULT_UA
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    # cf_clearance 复用：通过环境变量 ARCHIVE_COOKIE 注入手动获取的 cookie。
    # 注意：cf_clearance 通常绑定 IP 和 User-Agent，过期后需要重新获取，
    # 因此这是"可改善但非永久"的方案；若同时配置 ARCHIVE_UA，务必与获取
    # cookie 时所用浏览器的 UA 保持一致，否则 cookie 无效。
    cookie = os.environ.get("ARCHIVE_COOKIE", "").strip()
    if cookie:
        if "cf_clearance" not in cookie and ";" not in cookie:
            cookie = f"cf_clearance={cookie}"  # 允许只填裸值
        headers["Cookie"] = cookie
    return headers


def _log(msg: str) -> None:
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()


def _get(url: str) -> Optional[requests.Response]:
    for attempt in range(1, RETRIES + 1):
        _rate_limit()
        try:
            return requests.get(
                url,
                headers=_headers(),
                timeout=(CONNECT_TIMEOUT, READ_TIMEOUT),
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            _log(f"  请求失败({attempt}/{RETRIES}): {type(exc).__name__} {url}")
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    return None


def _snapshot_from_response(resp: requests.Response) -> Optional[str]:
    # 命中判定的可靠信号：Memento-Datetime 响应头
    if resp.headers.get("Memento-Datetime"):
        return resp.url
    # 兜底信号：最终 URL 形如 /<快照id>/<原文URL>
    if _SNAP_URL_RE.search(resp.url or ""):
        return resp.url
    return None


def probe_archive(url: str, mirrors: Optional[list] = None) -> Optional[str]:
    """探测 archive.today 是否已有快照；命中返回快照 URL。

    返回 None = 所有镜像均确认未命中；被验证页拦下时抛 ChallengeBlocked。
    """
    if mirrors is None:
        env = os.environ.get("ARCHIVE_MIRRORS", "").strip()
        mirrors = (
            [m.strip().rstrip("/") for m in env.split(",") if m.strip()]
            if env
            else DEFAULT_MIRRORS
        )
    first_challenge = None
    for mirror in mirrors:
        target = f"{mirror}/{url}"
        resp = _get(target)
        if resp is None:
            continue
        if resp.status_code == 200:
            snap = _snapshot_from_response(resp)
            if snap:
                return snap
            ch = verify_guard.detect_challenge(resp.text)
            if ch and first_challenge is None:
                first_challenge = (ch, target)
            # 200 但无 Memento 头：多半是提交表单/搜索页 → 未命中，换镜像
            continue
        if resp.status_code in (403, 429, 503):
            ch = verify_guard.detect_challenge(resp.text)
            if ch and first_challenge is None:
                first_challenge = (ch, target)
            if resp.status_code == 429:
                _log(f"  429 限速，暂停 10s 后换镜像: {mirror}")
                time.sleep(10)
            continue
        _log(f"  HTTP {resp.status_code}: {target}")
    if first_challenge:
        raise ChallengeBlocked(*first_challenge)
    return None


def probe_wayback(url: str) -> Optional[str]:
    """Wayback Machine 可用性 API 兜底。"""
    api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    resp = _get(api)
    if resp is None or resp.status_code != 200:
        return None
    try:
        closest = (resp.json().get("archived_snapshots") or {}).get("closest") or {}
    except ValueError:
        return None
    wb = closest.get("url")
    status = str(closest.get("status") or "")
    if wb and status in ("", "200"):
        return wb
    return None


def probe_link(url: str) -> dict:
    """单条链接的完整探测：archive.today → Wayback → 原文兜底。"""
    try:
        snap = probe_archive(url)
    except ChallengeBlocked as blocked:
        # 先识别类型并小规模尝试 nopecha（内部尽力而为），失败即退出码 75
        token = verify_guard.attempt_nopecha(blocked.challenge, blocked.page_url)
        if token:
            _log(
                f"  nopecha 返回令牌（长度 {len(token)}），但纯 HTTP 会话无法直接"
                f"换取 cf_clearance，仍需人工验证 / 注入 ARCHIVE_COOKIE"
            )
        # 被 archive.today 验证页拦下时仍尝试 Wayback 兜底，尽量给出可用快照；
        # status 保持 challenge（12h 后自动重试 archive.today），exit 75 语义不变
        wb = probe_wayback(url)
        print(json.dumps(
            {"url": url, "status": "challenge", "archive_url": None, "wayback_url": wb},
            ensure_ascii=False,
        ))
        verify_guard.bail_out(url, blocked.challenge)  # stderr 提示 + exit 75
    if snap:
        return {"url": url, "status": "archive", "archive_url": snap, "wayback_url": None}
    wb = probe_wayback(url)
    if wb:
        return {"url": url, "status": "wayback", "archive_url": None, "wayback_url": wb}
    return {"url": url, "status": "original", "archive_url": None, "wayback_url": None}


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="探测单条链接的归档快照")
    parser.add_argument("url", help="清洗后的原文链接")
    args = parser.parse_args(argv)
    result = probe_link(args.url.strip())
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
