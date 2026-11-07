"""【机器验证处理 —— 独立环节】

archive.today 前面的 Cloudflare 挑战 / CAPTCHA 是常态而非异常。本模块
独立负责"验证页"这一件事：

1. 验证页检测：模式匹配关键词（security check / captcha / recaptcha /
   cloudflare.*challenge 等），同时校验响应是否包含足够行数与字符数的
   实质内容 —— 防止把"正文恰好提到 captcha"的正常归档页误判为验证页；
2. 人工介入信号：先识别验证类型（cloudflare / captcha），然后用 nopecha
   等工具小规模尝试自动解题（可选，需 NOPECHA_API_KEY，最多尝试一次）；
   失败则向 stderr 输出触发验证的 URL 与"需要人工验证"提示，并以明确
   退出码 75（EX_TEMPFAIL）终止当前链接的探测；
3. 上层 fetch_news.py 看到退出码 75 后，把该条新闻标记为"验证待处理"
   （status=challenge），整个流程继续运行，不会崩溃。
"""
from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from typing import NoReturn, Optional

# 退出码 75 = EX_TEMPFAIL：临时失败，人工介入后可重试
EXIT_CHALLENGE = 75

# 实质内容阈值：低于任一阈值视为"内容单薄"（挑战页/占位页的典型特征）
MIN_SUBSTANTIVE_LINES = 40
MIN_SUBSTANTIVE_CHARS = 2000

# 强特征：只出现在真实挑战/验证/限速页上，正常文章几乎不可能包含。
# 除 Cloudflare 标记外，还包含验证组件的真实挂载标记（脚本路径、sitekey
# 挂载点）——archive.today 的限速页内容可以很"厚"，仅靠弱关键词会漏判。
_STRONG = [
    r"just\s+a\s+moment",                 # Cloudflare 等待页标题
    r"attention\s+required",              # Cloudflare 拦截页标题
    r"checking\s+your\s+browser",
    r"enable\s+javascript\s+and\s+cookies",
    r"cf[-_]chl[\w-]*",                   # cf-chl / cf_chl_opt 等内部标记
    r"challenge-platform",
    r"cdn-cgi/challenge-platform",
    r"recaptcha/api\.js",                 # reCAPTCHA 组件脚本
    r"challenges\.cloudflare\.com/turnstile",  # Turnstile 组件脚本
    r"data-sitekey=",                     # 验证组件挂载点
    r"security\s+check\s+to\s+access",    # archive.today 限速页措辞
]
# 弱特征：可能出现在验证页，也可能出现在"讨论 CAPTCHA 的文章正文"里
_WEAK = [
    r"captcha",
    r"recaptcha",
    r"hcaptcha",
    r"turnstile",
    r"security\s+check",
    r"cloudflare[\s\S]{0,60}challenge",
]

_STRONG_RE = [re.compile(p, re.I) for p in _STRONG]
_WEAK_RE = [re.compile(p, re.I) for p in _WEAK]
# 强特征里再区分 Cloudflare 与 reCAPTCHA：archive.today 的限速页其实是 reCAPTCHA，
# 不要把提示写成 “cloudflare 验证页”
_CLOUDFLARE_MARK_RE = re.compile(
    r"just\s+a\s+moment|attention\s+required|checking\s+your\s+browser|"
    r"cf[-_]chl|challenge-platform|cdn-cgi/challenge-platform|enable\s+javascript\s+and\s+cookies", re.I)
_CAPTCHA_MARK_RE = re.compile(
    r"recaptcha/api\.js|data-sitekey=|g-recaptcha|hcaptcha|turnstile", re.I)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SITEKEY_RE = re.compile(
    r"""(?:data-sitekey|sitekey|site_key)["']?\s*[:=]\s*["']([A-Za-z0-9_-]{20,})["']""",
    re.I,
)


@dataclass
class Challenge:
    kind: str                      # "cloudflare" | "captcha"
    flavor: Optional[str] = None   # "turnstile" | "recaptcha2" | "hcaptcha" | None
    sitekey: Optional[str] = None


def substantive_content(body: str) -> bool:
    """内容是否足够"厚"：行数与字符数需同时达标。"""
    if not body:
        return False
    lines = body.count("\n") + 1
    return lines >= MIN_SUBSTANTIVE_LINES and len(body) >= MIN_SUBSTANTIVE_CHARS


def _match_any(patterns, text: str) -> bool:
    return any(p.search(text) for p in patterns)


def detect_challenge(body: str) -> Optional[Challenge]:
    """根据响应体判断是否为验证页。规则（顺序即优先级）：

    * <title> 命中强特征 → cloudflare；
    * 正文任意位置命中强特征（基础设施标记，正常文章不含）→ cloudflare；
    * 内容单薄且命中弱特征 → captcha；
    * 内容厚实（实质内容达标）时忽略弱特征 —— 那多半是归档的文章本身
      在讨论 CAPTCHA，避免误判。
    """
    if not body:
        return None
    m = _TITLE_RE.search(body)
    title = m.group(1) if m else ""
    if _match_any(_STRONG_RE, title):
        return _with_flavor(_challenge_kind(body), body)
    lowered = body.lower()
    if _match_any(_STRONG_RE, lowered):
        return _with_flavor(_challenge_kind(body), body)
    if not substantive_content(body) and _match_any(_WEAK_RE, lowered):
        return _with_flavor("captcha", body)
    return None


def _challenge_kind(body: str) -> str:
    """强特征命中后细分类型：Cloudflare 优先，否则 reCAPTCHA/hCaptcha/Turnstile。"""
    if _CLOUDFLARE_MARK_RE.search(body):
        return "cloudflare"
    if _CAPTCHA_MARK_RE.search(body):
        return "captcha"
    return "cloudflare"


def _with_flavor(kind: str, body: str) -> Challenge:
    low = body.lower()
    if "turnstile" in low:
        flavor = "turnstile"
    elif "hcaptcha" in low:
        flavor = "hcaptcha"
    elif "recaptcha" in low or "g-recaptcha" in low:
        flavor = "recaptcha2"
    else:
        flavor = None
    return Challenge(kind=kind, flavor=flavor, sitekey=extract_sitekey(body))


def extract_sitekey(body: str) -> Optional[str]:
    m = _SITEKEY_RE.search(body)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# nopecha 小规模自动解题（可选、尽力而为）
#
# * 需要 NOPECHA_API_KEY 环境变量，未配置时直接跳过；
# * 每个链接最多提交 1 个任务、轮询约 30 秒 —— 刻意小规模，不把打码
#   服务当常规依赖；
# * nopecha API 形状可能随版本变化，这里按社区文档实现并对所有异常兜底；
#   即使拿到 turnstile/recaptcha token，纯 requests 会话也无法直接换取
#   cf_clearance，因此无论成败都会如实记录，失败仍走退出码 75 的人工路径。
# ---------------------------------------------------------------------------
_NOPECHA_TYPE = {"turnstile": "turnstile", "recaptcha2": "recaptcha2", "hcaptcha": "hcaptcha"}
_NOPECHA_ENDPOINT = "https://api.nopecha.com/token/"


def attempt_nopecha(challenge: Challenge, page_url: str) -> Optional[str]:
    key = os.environ.get("NOPECHA_API_KEY", "").strip()
    if not key or not challenge.flavor or not challenge.sitekey:
        return None
    ntype = _NOPECHA_TYPE.get(challenge.flavor)
    if not ntype:
        return None
    try:
        import requests  # 局部导入：未安装 requests 不影响检测本身

        resp = requests.post(
            _NOPECHA_ENDPOINT,
            json={"key": key, "type": ntype, "sitekey": challenge.sitekey, "url": page_url},
            timeout=30,
        )
        resp.raise_for_status()
        try:
            task_id = resp.json().get("id") or resp.json().get("data")
        except ValueError:
            task_id = resp.text.strip()
        if not task_id:
            return None
        for _ in range(6):  # 6 次 × 5s ≈ 30s，单次任务
            time.sleep(5)
            poll = requests.get(_NOPECHA_ENDPOINT, params={"key": key, "id": task_id}, timeout=15)
            text = (poll.text or "").strip()
            if "ERROR" in text.upper():
                return None
            if len(text) >= 20:  # 有效 token 通常为长随机串
                return text
        return None
    except Exception:
        return None


def bail_out(url: str, challenge: Optional[Challenge]) -> NoReturn:
    """输出人工介入提示，并以退出码 75 终止当前链接的探测。"""
    kind = challenge.kind if challenge else "unknown"
    sys.stderr.write(
        f"\n[需要人工验证] 检测到 {kind} 验证页，触发验证的 URL: {url}\n"
        f"[需要人工验证] 可在浏览器中手动打开该链接完成验证；或为运行环境配置 "
        f"ARCHIVE_COOKIE（cf_clearance）后重试。\n"
    )
    sys.stderr.flush()
    sys.exit(EXIT_CHALLENGE)
