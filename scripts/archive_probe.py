"""archive.today 快照探测 + 自动存档 + Wayback 兜底（可独立运行的探测环节）。

archive.today 没有官方 API，探测方式：
* 依次轮换镜像 archive.ph / archive.md / archive.li / archive.is；
* 请求 <镜像>/<原文URL>，若已有快照会 302 到最新快照页；
* 用响应头 Memento-Datetime 判定命中（可靠信号），最终 URL 形如
  /<快照id>/<原文URL> 作为兜底信号；
* 设置超时、重试、限速；CAPTCHA/429 不崩溃 —— CAPTCHA 走 verify_guard
  （退出码 75 → 上层标记"验证待处理"），429 视为未命中换下一个镜像。

v2 增加 best-effort 自动存档（"archive this url"）：
* 探测确认无快照后，向 /submit/ 提交一次，再轮询 <镜像>/<url> 最多 N 秒；
* 提交/轮询被人机验证拦下时仍按 challenge 处理，不重试、不阻塞；
* 只在前端/CI 预算允许时启用（--auto-archive / AUTO_ARCHIVE_BUDGET）。

CLI 用法（上层 fetch_news.py 以子进程方式调用，读退出码）：
    python scripts/archive_probe.py <清洗后的URL> [--auto-archive] [--wait 45]
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
RETRIES = int(os.environ.get("PROBE_RETRIES", "1"))
RETRY_BACKOFF = float(os.environ.get("PROBE_BACKOFF", "2"))
REQUEST_INTERVAL = float(os.environ.get("PROBE_INTERVAL", "1.0"))

_SNAP_URL_RE = re.compile(r"/[0-9a-z]{6,}/https?://", re.I)
_NON_SNAPSHOT_PATH_RE = re.compile(r"/(?:newest|submit|search|timemap|timemap/)", re.I)


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


def _proxy_kwargs() -> dict:
    """ARCHIVE_PROXY 可只给 archive.today 走代理（本机 7890），不影响其他源。"""
    proxy = os.environ.get("ARCHIVE_PROXY", "").strip()
    if not proxy:
        return {}
    return {"proxies": {"http": proxy, "https": proxy}}


def _get(url: str, timeout: Optional[float] = None) -> Optional[requests.Response]:
    limit = timeout or (CONNECT_TIMEOUT + READ_TIMEOUT)
    read_to = min(READ_TIMEOUT, max(5.0, limit))
    last_exc = None
    for attempt in range(1, RETRIES + 1):
        _rate_limit()
        # archive.today 会对普通 Python TLS 指纹返回 SSLError/超时（本机代理下实测），
        # 优先用 curl_cffi 的 Safari 指纹；未安装时回落 requests。
        try:
            from curl_cffi import requests as creq

            return creq.get(
                url,
                impersonate="safari17_0",
                headers=_headers(),
                timeout=(CONNECT_TIMEOUT, read_to),
                allow_redirects=True,
                **_proxy_kwargs(),
            )
        except ImportError:
            pass
        except Exception as exc:
            last_exc = exc
            _log(f"  curl_cffi 请求失败({attempt}/{RETRIES}): {type(exc).__name__} {url}")
        try:
            return requests.get(
                url,
                headers=_headers(),
                timeout=(CONNECT_TIMEOUT, read_to),
                allow_redirects=True,
                **_proxy_kwargs(),
            )
        except requests.RequestException as exc:
            last_exc = exc
            _log(f"  请求失败({attempt}/{RETRIES}): {type(exc).__name__} {url}")
        if attempt < RETRIES:
            time.sleep(RETRY_BACKOFF * attempt)
    if last_exc is not None:
        return None
    return None


def _post(url: str, data: dict, timeout: Optional[float] = None) -> bool:
    read_to = min(READ_TIMEOUT, max(5.0, timeout or (CONNECT_TIMEOUT + READ_TIMEOUT)))
    try:
        from curl_cffi import requests as creq

        creq.post(url, data=data, impersonate="safari17_0", headers=_headers(),
                  timeout=(CONNECT_TIMEOUT, read_to), allow_redirects=True,
                  **_proxy_kwargs())
        return True
    except Exception:
        pass
    try:
        requests.post(url, data=data, headers=_headers(),
                      timeout=(CONNECT_TIMEOUT, read_to), allow_redirects=True,
                      **_proxy_kwargs())
        return True
    except requests.RequestException:
        return False


def _snapshot_from_response(resp: requests.Response) -> Optional[str]:
    # 命中判定的可靠信号：Memento-Datetime 响应头
    if resp.headers.get("Memento-Datetime"):
        return resp.url
    # 兜底信号：最终 URL 形如 /<快照id>/<原文URL>；排除 /newest/ 等索引页
    if _SNAP_URL_RE.search(resp.url or "") and not _NON_SNAPSHOT_PATH_RE.search(resp.url or ""):
        return resp.url
    return None


def _mirrors(explicit: Optional[list] = None) -> list:
    if explicit:
        return explicit
    env = os.environ.get("ARCHIVE_MIRRORS", "").strip()
    if env:
        return [m.strip().rstrip("/") for m in env.split(",") if m.strip()]
    return DEFAULT_MIRRORS


def probe_archive(url: str, mirrors: Optional[list] = None) -> Optional[str]:
    """探测 archive.today 是否已有快照；命中返回快照 URL。

    返回 None = 所有镜像均确认未命中；被验证页拦下时抛 ChallengeBlocked。
    """
    first_challenge = None
    for mirror in _mirrors(mirrors):
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
                _log(f"  429 限速，暂停 5s 后换镜像: {mirror}")
                time.sleep(5)
            continue
        _log(f"  HTTP {resp.status_code}: {target}")
    if first_challenge:
        raise ChallengeBlocked(*first_challenge)
    return None


def submit_archive(url: str, mirrors: Optional[list] = None, wait_seconds: float = 45,
                   deadline: Optional[float] = None) -> Optional[str]:
    """best-effort "archive this url"：提交一次并轮询快照；失败返回 None。

    这是 CI/前端的兜底路径：archive.today 可能要求人机验证，提交不成功就
    返回 None，由上层标记 unarchived（前端转为手动流程），不会无限等待。
    """
    end = time.monotonic() + max(5.0, wait_seconds)
    if deadline:
        end = min(end, deadline)
    for mirror in _mirrors(mirrors):
        if time.monotonic() >= end:
            return None
        submit_url = f"{mirror}/submit/?url={urllib.parse.quote(url, safe='')}"
        _log(f"  自动存档提交: {submit_url[:120]}")
        resp = _get(submit_url, timeout=max(5.0, end - time.monotonic()))
        if resp is None:
            continue
        if resp.status_code in (403, 429, 503):
            ch = verify_guard.detect_challenge(resp.text)
            if ch:
                raise ChallengeBlocked(ch, submit_url)
            continue
        if resp.status_code == 200:
            ch = verify_guard.detect_challenge(resp.text)
            if ch:
                raise ChallengeBlocked(ch, submit_url)
        # 表单页通常含 url 字段；POST 一次触发保存（无 CSRF 时有效）
        _rate_limit()
        _post(f"{mirror}/submit/", {"url": url}, timeout=max(5.0, end - time.monotonic()))
        # 轮询：出现 Memento-Datetime 或 /<id>/<url> 即成功
        while time.monotonic() < end:
            time.sleep(6)
            poll = _get(f"{mirror}/{url}", timeout=max(5.0, end - time.monotonic()))
            if poll is None:
                continue
            if poll.status_code in (403, 429, 503):
                ch = verify_guard.detect_challenge(poll.text)
                if ch:
                    raise ChallengeBlocked(ch, f"{mirror}/{url}")
                continue
            if poll.status_code == 200:
                snap = _snapshot_from_response(poll)
                if snap:
                    return snap
                ch = verify_guard.detect_challenge(poll.text)
                if ch:
                    raise ChallengeBlocked(ch, f"{mirror}/{url}")
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


def probe_link(url: str, auto_archive: bool = False, wait_seconds: float = 45,
               deadline: Optional[float] = None) -> dict:
    """单条链接的完整探测：archive.today → (自动存档) → Wayback → 原文兜底。"""
    try:
        snap = probe_archive(url)
    except ChallengeBlocked as blocked:
        # 先识别类型并小规模尝试 nopecha（内部尽力而为），失败即退出码 75
        token = verify_guard.attempt_nopecha(blocked.challenge, blocked.page_url)
        if token:
            _log(
                f"  nopecha 返回令牌（长度 {len(token)}），但纯 HTTP 会话无法直接"
                f" 换取 cf_clearance，仍需人工验证 / 注入 ARCHIVE_COOKIE"
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

    if auto_archive and (deadline is None or time.monotonic() < deadline):
        try:
            submitted = submit_archive(url, wait_seconds=wait_seconds, deadline=deadline)
        except ChallengeBlocked as blocked:
            wb = probe_wayback(url)
            print(json.dumps(
                {"url": url, "status": "challenge", "archive_url": None, "wayback_url": wb},
                ensure_ascii=False,
            ))
            verify_guard.bail_out(url, blocked.challenge)
        if submitted:
            return {"url": url, "status": "archive", "archive_url": submitted, "wayback_url": None}

    wb = probe_wayback(url)
    if wb:
        return {"url": url, "status": "wayback", "archive_url": None, "wayback_url": wb}
    status = "unarchived" if auto_archive else "original"
    return {"url": url, "status": status, "archive_url": None, "wayback_url": None}


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="探测单条链接的归档快照")
    parser.add_argument("url", help="清洗后的原文链接")
    parser.add_argument("--auto-archive", action="store_true", help="无快照时尝试自动存档")
    parser.add_argument("--wait", type=float, default=45, help="自动存档等待秒数")
    args = parser.parse_args(argv)
    result = probe_link(args.url.strip(), auto_archive=args.auto_archive, wait_seconds=args.wait)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
