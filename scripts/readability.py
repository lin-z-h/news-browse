"""阅读模式正文提取与私有缓存（CI 侧使用）。

正文获取链：发布者原文 → Wayback → archive.md（经 r.jina.ai，可带 key）。
清洗逻辑与前端 denoiseReader v3.1 同步；结果存入私有仓库 news-cache
（GitHub Contents API，仅 token 持有者可见），供阅读模式优先读取。
只存清洗后的正文段落与图片地址；不存页面结构；付费源只能取到公开部分，不绕墙。
"""
import base64
import json
import os
import re
import time

import requests

GARBAGE_RE = re.compile(
    r"are you a robot|access denied|just a moment|unusual traffic|verify (?:you are )?human|"
    r"403 forbidden|captcha|enable javascript and cookies|are you human|error code|request id|"
    r"we apologise for any inconvenience|for help please visit", re.I)
READER_END_RE = re.compile(
    r"^#{0,4}\s*(most viewed|related storie|more from|explore more on these topics|"
    r"topics in this article|reuse this content|advertisement|year-end fundraiser|"
    r"at this unsettling time|now live|please contribute|was this helpful|sign up for our email|"
    r"fund independent|what readers are saying|do not sell or share|get unlimited access|"
    r"we've updated our terms|claim this offer|©\s*20\d\d|already have an account|two ways to read)", re.I)
NOISE_ANY_RE = re.compile(
    r"^enter your email|after newsletter promotion|^free newsletter|^sign up to |^gift this article|"
    r"^add us on google|^terms and conditions|^copyright \d{4}|^thanks for sharing|^download now\b|"
    r"^most viewed|^related storie|^advertisement", re.I)
READER_DROP = {
    "news.tvb.com": [re.compile(r"^\d+\.\s[\s\S]*\d+\.\s"), re.compile(r"^publish[:：]", re.I),
                     re.compile(r"^by[:：]", re.I)],
}
NOISE_HEAD = re.compile(r"^(support|sign in|sign|share|search|menu|close|fund independent|most viewed|related|live)", re.I)

JINA_ENDPOINT = "https://r.jina.ai/"


def _norm(s):
    return re.sub(r"[\s·•—–'\"“,，.。！？!?:：()（）「」『』|#[\]{}]", "", s).lower()


def _abs_img(u, base):
    if not u:
        return None
    if re.match(r"^blob:", u, re.I):
        return None
    if re.match(r"^https?://", u, re.I):
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/") and base:
        return base + u
    return None


def denoise(md, host, title):
    """清洗 jina markdown → [{"text", "head"} / {"img"}]；垃圾/太短返回 None。"""
    body = md
    base = ""
    m = re.search(r"URL Source: (\S+)", body)
    if m:
        try:
            from urllib.parse import urlparse
            base = urlparse(m.group(1)).origin
        except Exception:
            pass
    cut = body.find("Markdown Content:")
    if cut >= 0:
        body = body[cut + 18:]
    if GARBAGE_RE.search(body[:3000]):
        return None
    raw = [b.strip() for b in body.split("\n\n") if b.strip()]
    clean = []
    for b in raw:
        im = re.match(r"^!\[[^\]]*\]\(([^)\s]+)", b)
        if im:
            u = _abs_img(im.group(1), base)
            if u:
                clean.append({"img": u})
                continue
        head = bool(re.match(r"^#{1,3}\s", b))
        b = re.sub(r"^#{1,4}\s+", "", b, flags=re.M)
        b = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", b)
        b = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", b)
        b = re.sub(r"\*\*([^*]+)\*\*", r"\1", b)
        b = re.sub(r"^[-*]\s+", "· ", b, flags=re.M).strip()
        if b:
            clean.append({"text": b, "head": head})

    def is_sentence(b):
        return re.search(r"[。！？]", b) or (re.search(r"[.!?]", b) and len(b) > 90)

    drop = READER_DROP.get(host)
    tnorm = _norm(title)
    start = next((i for i, x in enumerate(clean)
                  if x.get("head") and len(x["text"]) >= 25 and not NOISE_ANY_RE.match(x["text"])
                  and not NOISE_HEAD.match(x["text"])), -1)
    if start < 0:
        start = next((i for i, x in enumerate(clean)
                      if x.get("text") and is_sentence(x["text"]) and len(x["text"]) >= 20), -1)
    end = -1
    if start >= 0:
        for i, x in enumerate(clean):
            if i <= start:
                continue
            if (drop and any(r.match(x.get("text", "")) for r in drop)) or \
               (x.get("text") and READER_END_RE.match(x["text"])):
                end = i
                break
    lo = max(start, 0)
    hi = end if end > start else len(clean)
    seen = {tnorm}
    kept = []
    for x in clean[lo:hi]:
        if "img" in x:
            kept.append(x)
            continue
        t = x["text"]
        if len(t) < 14 or NOISE_ANY_RE.match(t):
            continue
        if re.search(r"cookie", t, re.I) or re.match(r"^[·\s—–-]+$", t):
            continue
        if re.match(r"^what readers are saying", t, re.I):
            continue
        k = _norm(t)
        if not k or k in seen:
            continue
        seen.add(k)
        kept.append(x)
    total = sum(len(x["text"]) for x in kept if "text" in x)
    if total < 40:
        return None
    return kept


def jina_fetch(url, key=None, timeout=75):
    """经 r.jina.ai 抓取；带可选 key（提升配额/解锁 wayback）。返回文本或 None。"""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    for attempt in range(2):
        try:
            r = requests.get(JINA_ENDPOINT + url, headers=headers, timeout=timeout)
        except Exception:
            return None
        if r.status_code == 200:
            return r.text
        if r.status_code in (403, 429) and attempt == 0:
            time.sleep(20)
            continue
        return None
    return None


def fetch_text(url, key=None):
    """正文获取链：发布者 → Wayback → archive.md。返回去噪后的块列表或 None。"""
    host = ""
    m = re.match(r"https?://([^/]+)", url)
    if m:
        host = m.group(1)
    candidates = [
        url,
        "https://web.archive.org/web/2/" + url,
        "https://archive.md/" + url,
    ]
    for cu in candidates:
        md = jina_fetch(cu, key=key)
        if md is None:
            continue
        kept = denoise(md, host, title="")
        if kept is None:
            continue
        return kept
    return None


# ---------------------------------------------------------------------------
# 私有仓库缓存（GitHub Contents API）
# ---------------------------------------------------------------------------

def cache_get(token, repo, path):
    r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}?ref=main",
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json"}, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("sha")


def cache_put(token, repo, path, payload):
    body = {"message": f"text cache: {path}", "content": base64.b64encode(
        json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")}
    sha = cache_get(token, repo, path)
    if sha:
        body["sha"] = sha
    r = requests.put(f"https://api.github.com/repos/{repo}/contents/{path}",
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json"},
                     json=body, timeout=30)
    return r.status_code in (200, 201)


def cache_read(token, repo, path):
    """读取缓存正文（前端用浏览器 fetch，CI/本地用此函数均可）。"""
    r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}?ref=main",
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json"}, timeout=30)
    if r.status_code != 200:
        return None
    data = r.json()
    raw = base64.b64decode(data.get("content", "").replace("\n", ""))
    try:
        return json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
