"""阅读模式正文提取与私有缓存（CI 侧，v2）。

正文获取链（按可靠性排序）：
1. 转载/联合发布版（The Edge Malaysia / The Edge Singapore / Yahoo Finance 等，
   仅付费墙源使用，由 syndication.py 解析后传入）；
2. 发布者原文 HTML：JSON-LD ``articleBody`` → 文章容器段落（curl_cffi 浏览器指纹）；
3. Jina Reader markdown（r.jina.ai）；
4. 都失败则返回 None，前端降级为“封面 + 开始阅读”，不渲染垃圾。

清洗规则同时服务于 CI 和前端（前端 denoiseReader 与本模块保持同一套特征）：
* 付费墙/登录/同意墙（``$1``、``Continue with Apple/Google``、``Do not sell or share``）；
* 人机验证页（Cloudflare / CAPTCHA）；
* archive.today 索引页（跨来源标题列表，常被误当成正文）；
* 页脚/推荐区/募捐区/评论摘要；短行、URL 行、与标题重复的行。

结果只保留清洗后的段落与图片地址，存入私有仓库 news-cache（GitHub Contents API）。
不会绕过付费墙：拿到的是转载版公开内容或原文公开片段。
"""
from __future__ import annotations

import base64
import html as htmllib
import json
import os
import re
import time
from html.parser import HTMLParser
from typing import Optional

import requests

# ---------------------------------------------------------------------------
# 识别特征
# ---------------------------------------------------------------------------
GARBAGE_RE = re.compile(
    r"are you a robot|access denied|just a moment|unusual traffic|verify (?:you are )?human|"
    r"403 forbidden|captcha|enable javascript and cookies|are you human|error code|request id|"
    r"we apologise for any inconvenience|for help please visit|security check to access", re.I)

# 付费墙 / 登录 / 同意墙：这些整页出现时代表“没有正文”，绝不能当文章渲染
PAYWALL_MARKERS = (
    "continue with apple", "continue with google", "sign in with google",
    "do not sell or share my personal information", "unlock this article",
    "unlock election coverage", "unlock the global benchmark", "already a subscriber",
    "subscribe to continue", "create a free account", "get unlimited access",
    "sign in to continue", "by continuing, i agree to the", "claim this offer",
    "we've updated our terms", "two ways to read", "already have an account",
)
PAYWALL_RE = re.compile("|".join(re.escape(m) for m in PAYWALL_MARKERS), re.I)

# archive.today 索引/“newest”页特征：跨来源标题列表（快照页本身不算）
ARCHIVE_INDEX_RE = re.compile(
    r"no results|create a snapshot|this page is not available|submit url", re.I)
INDEX_PUBLICATIONS = (
    "the new york times", "financial times", "the washington post", "lianhe zaobao",
    "reuters", "bloomberg", "the guardian", "the economist", "wall street journal",
    "south china morning post", "time", "associated press",
)

READER_END_RE = re.compile(
    r"^#{0,4}\s*(most viewed|most read|related storie|more from|top reads|explore more on these topics|"
    r"topics in this article|reuse this content|advertisement|year-end fundraiser|"
    r"at this unsettling time|now live|please contribute|was this helpful|sign up for our email|"
    r"fund independent|what readers are saying|do not sell or share|get unlimited access|"
    r"we've updated our terms|claim this offer|©\s*20\d\d|already have an account|two ways to read|"
    r"continue with (?:apple|google)|sign in|subscribe|home\b|terms of service)", re.I)
NOISE_ANY_RE = re.compile(
    r"^enter your email|after newsletter promotion|^free newsletter|^sign up to |^gift this article|"
    r"^add us on google|^terms and conditions|^copyright \d{4}|^thanks for sharing|^download now\b|"
    r"^most viewed|^most read|^related storie|^advertisement|^contact us|^confidential tip|"
    r"^site feedback|^back to top|^share this|^listen to this|^before it'?s here|^learn more|"
    r"^bloomberg terminal|^get the terminal|^read more$|^continue reading|^archive\.today|"
    r"^saved from|^all snapshots|^webpage capture|^archived \d|^short link$|^long link$|"
    r"^markdown$|^html code$|^wiki code$", re.I)
NOISE_HEAD = re.compile(
    r"^(support|sign in|sign up|sign|share|search|menu|close|subscribe|fund independent|"
    r"most viewed|most read|related|live|follow|products|company|media)", re.I)
# 正文开始前的署名/日期/工具条
START_SKIP_RE = re.compile(
    r"^(by\s+\S|published|updated on|save$|translate$|share$|listen|follow us|"
    r"gift this|add us on google|contact us|confidential tip|site feedback|"
    r"\w+ \d{1,2}, 20\d\d at |\d{1,2} \w+ 20\d\d at )", re.I)

READER_DROP = {
    "news.tvb.com": [re.compile(r"^\d+\.\s[\s\S]*\d+\.\s"), re.compile(r"^publish[:：]", re.I),
                     re.compile(r"^by[:：]", re.I)],
}

PAYWALL_HOSTS = ("bloomberg.com", "wsj.com", "ft.com", "economist.com", "washingtonpost.com")

JINA_ENDPOINT = "https://r.jina.ai/"
# 正文缓存格式版本：v2 = 转载版/HTML/JSON-LD + 严格质量关。
# 旧缓存（无 v 字段）视为过期，避免继续渲染历史垃圾正文。
TEXT_CACHE_VERSION = 2


def _norm(s: str) -> str:
    return re.sub(r"[\s·•—–'\"“”,，.。！？!?:：()（）「」『』|#\[\]{}]", "", s).lower()


def _abs_img(u: str, base: str = "") -> Optional[str]:
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


def _origin(url: str) -> str:
    m = re.match(r"(https?://[^/]+)", url or "")
    return m.group(1) if m else ""


def _strip_tags(s: str) -> str:
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</p\s*>", "\n\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    return htmllib.unescape(s)


# ---------------------------------------------------------------------------
# 通用质量关：太短 / 结构不像文章 → 拒绝
# ---------------------------------------------------------------------------
def looks_like_paywall(text: str) -> bool:
    if not text:
        return False
    low = text.lower()
    hits = sum(1 for m in PAYWALL_MARKERS if m in low)
    if hits >= 2:
        return True
    return "do not sell or share my personal information" in low and "$1" in text


def looks_like_archive_index(blocks: list) -> bool:
    texts = [b.get("text", "") for b in blocks if b.get("text")]
    if not texts:
        return False
    joined = " ".join(texts)
    low = joined.lower()
    if ARCHIVE_INDEX_RE.search(low):
        return True
    name_hits = sum(1 for n in INDEX_PUBLICATIONS if n in low)
    if name_hits >= 4:
        # 跨来源标题列表通常缺少句末标点；正常文章会成句
        sentences = len(re.findall(r"[.!?。！？](?:\s|$)", joined))
        if sentences < max(4, len(texts) // 3):
            return True
    short = sum(1 for t in texts if len(t) < 60)
    return len(texts) >= 8 and short / len(texts) > 0.7 and len(joined) < 1500


def quality_ok(blocks: list, title: str = "") -> bool:
    """正文质量门槛：≥3 个长段、≥400 字符（中文 ≥200 字）、多数段落成句。"""
    if not blocks:
        return False
    texts = [b.get("text", "") for b in blocks if b.get("text")]
    total = sum(len(t) for t in texts)
    if total < 150:
        return False
    cjk = len(re.findall(r"[\u4e00-\u9fff]", "".join(texts)))
    if total < 200 and cjk < 100:
        return False
    paras = [t for t in texts if len(t) >= 30 or cjk > 100]
    # 中文短讯（TVB 等）经常只有 2 段、200 字左右，不能按英文长文要求 3 段/400 字符
    min_paras = 2 if cjk >= 120 else 3
    if len(paras) < min_paras:
        return False
    if total < 400 and cjk < 120:
        return False
    sentence_like = sum(1 for t in paras if re.search(r"[.!?。！？]", t))
    if sentence_like < max(2, int(len(paras) * 0.6)):
        return False
    if looks_like_paywall(" ".join(texts)) and total < 2000:
        return False
    if looks_like_archive_index(blocks):
        return False
    return True


def _finish(clean: list, host: str, title: str) -> Optional[list]:
    """从候选块中切出正文区间并做最终过滤；失败返回 None。"""
    if not clean:
        return None
    tnorm = _norm(title)
    start = -1
    if tnorm:
        for i, x in enumerate(clean):
            t = x.get("text") or ""
            if len(t) >= 12 and (tnorm in _norm(t) or _norm(t) in tnorm) and len(_norm(t)) >= 12:
                start = i + 1
                break
    if start < 0:
        start = next((i for i, x in enumerate(clean)
                      if x.get("head") and len(x.get("text", "")) >= 25
                      and not NOISE_ANY_RE.match(x["text"]) and not NOISE_HEAD.match(x["text"])), -1)
    if start < 0:
        start = next((i for i, x in enumerate(clean)
                      if x.get("text") and re.search(r"[.!?。！？]", x["text"])
                      and len(x["text"]) >= 40), -1)
    if start < 0:
        return None

    # 跳过标题后的署名/日期/工具条
    while start < len(clean):
        t = clean[start].get("text") or ""
        if not t or len(t) < 20 or START_SKIP_RE.match(t) or NOISE_ANY_RE.match(t):
            start += 1
            continue
        break

    drop = READER_DROP.get(host)
    end = -1
    for i in range(start + 1, len(clean)):
        x = clean[i]
        t = x.get("text") or ""
        if (drop and any(r.match(t) for r in drop)) or (t and READER_END_RE.match(t)):
            end = i
            break
    hi = end if end > start else len(clean)
    kept: list = []
    seen = {tnorm}
    for x in clean[start:hi]:
        if "img" in x:
            kept.append(x)
            continue
        t = x.get("text", "")
        if len(t) < 14 or NOISE_ANY_RE.match(t):
            continue
        if "cookie" in t.lower() or re.match(r"^[·\s—–-]+$", t):
            continue
        k = _norm(t)
        if not k or k in seen:
            continue
        seen.add(k)
        kept.append(x)

    # 去掉正文尾部夹带的付费墙/推荐块
    while kept and "text" in kept[-1] and (
            looks_like_paywall(kept[-1]["text"]) or READER_END_RE.match(kept[-1]["text"])):
        kept.pop()
    # 开头家具修剪
    while kept and "text" in kept[0] and len(kept[0]["text"]) < 40:
        kept.pop(0)
    if not quality_ok(kept, title):
        return None
    # 付费墙源：只有 2 段导语/摘要不算正文，交给转载版（The Edge 等）兜底
    if any(h in (host or "") for h in PAYWALL_HOSTS):
        texts = [b.get("text", "") for b in kept if b.get("text")]
        if len(texts) < 4 or sum(len(t) for t in texts) < 900:
            return None
    return kept


# ---------------------------------------------------------------------------
# Jina Reader markdown 清洗
# ---------------------------------------------------------------------------
def denoise(md: str, host: str, title: str) -> Optional[list]:
    """清洗 jina markdown → [{"text","head"} / {"img"}]；垃圾/太短返回 None。"""
    body = md or ""
    src = re.search(r"URL Source: (\S+)", body)
    base = _origin(src.group(1)) if src else ""
    cut = body.find("Markdown Content:")
    if cut >= 0:
        body = body[cut + len("Markdown Content:"):]
    if GARBAGE_RE.search(body[:3000]):
        return None
    raw = [b.strip() for b in body.split("\n\n") if b.strip()]
    clean: list = []
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
    return _finish(clean, host, title)


# ---------------------------------------------------------------------------
# 原文 HTML 解析（JSON-LD → 文章段落）
# ---------------------------------------------------------------------------
class _BlockParser(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "aside", "form", "button"}
    BLOCK = {"p", "h1", "h2", "h3", "h4", "li", "blockquote", "figcaption"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.cur = None
        self.buf: list[str] = []
        self.blocks: list[dict] = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in self.SKIP:
            self.skip_depth += 1
        elif self.skip_depth == 0 and tag in self.BLOCK:
            self.flush()
            self.cur = tag
            self.buf = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in self.SKIP:
            if self.skip_depth:
                self.skip_depth -= 1
        elif self.skip_depth == 0 and tag in self.BLOCK and self.cur == tag:
            self.flush()
            self.cur = None

    def handle_data(self, data):
        if self.skip_depth == 0 and self.cur:
            self.buf.append(data)

    def flush(self):
        t = re.sub(r"\s+", " ", "".join(self.buf)).strip()
        if t:
            self.blocks.append({"text": t, "head": self.cur in ("h1", "h2", "h3", "h4")})
        self.buf = []

    def close(self):
        super().close()
        self.flush()


def _jsonld_objects(html: str):
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S | re.I):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        stack = [data]
        while stack:
            x = stack.pop()
            if isinstance(x, dict):
                if isinstance(x.get("@graph"), list):
                    stack.extend(x["@graph"])
                yield x
            elif isinstance(x, list):
                stack.extend(x)


def _ld_image(x) -> Optional[str]:
    image = x.get("image")
    if isinstance(image, str):
        return image
    if isinstance(image, dict):
        return image.get("url") or image.get("contentUrl")
    if isinstance(image, list) and image:
        return _ld_image({"image": image[0]})
    return None


_TVB_DATA_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.S)


def _tvb_deref(arr: list, idx, depth: int = 0):
    """devalue 格式：对象/数组里的整数值是对 arr 下标的引用（0 除外）。"""
    if depth > 8:
        return idx
    if isinstance(idx, int) and 0 < idx < len(arr):
        return _tvb_deref(arr, arr[idx], depth + 1)
    return idx


def extract_tvb_article(html: str, url: str, title: str = "") -> Optional[dict]:
    """TVB 文章页是 Nuxt SPA，正文在 __NUXT_DATA__ 的 content/content_hk 字段。

    直接解析内嵌 JSON，不用 archive、不用 Jina；中文短讯 2 段也算完整正文。
    """
    m = _TVB_DATA_RE.search(html or "")
    if not m:
        return None
    try:
        arr = json.loads(m.group(1))
    except ValueError:
        return None
    wanted = None
    mid = re.search(r"/(?:en|tc|sc)/(\d+)", url or "")
    if mid:
        wanted = int(mid.group(1))
    for v in arr:
        if not (isinstance(v, dict) and "content" in v and "title_hk" in v):
            continue
        aid = _tvb_deref(arr, v.get("id"))
        if wanted is not None and aid != wanted:
            continue
        body = _tvb_deref(arr, v.get("content_hk") or v.get("content"))
        if not isinstance(body, str) or len(body) < 20:
            continue
        text = _strip_tags(body)
        blocks = []
        for para in re.split(r"\n{2,}", text):
            para = re.sub(r"\s+", " ", para).strip()
            if len(para) >= 10:
                blocks.append({"text": para, "head": False})
        if not blocks:
            continue
        image = None
        cover = _tvb_deref(arr, v.get("cover"))
        if isinstance(cover, list) and cover:
            cover = _tvb_deref(arr, cover[0])
        if isinstance(cover, dict):
            image = _tvb_deref(arr, cover.get("url"))
        found_title = _tvb_deref(arr, v.get("title_hk")) or title
        return {
            "title": found_title if isinstance(found_title, str) else title,
            "image": image if isinstance(image, str) else None,
            "blocks": blocks,
        }
    return None


def extract_article_html(html: str, title: str = "", url: str = "") -> Optional[dict]:
    """从原文 HTML 提取 {title, image, blocks}；失败返回 None。"""
    if not html or len(html) < 500:
        return None
    if "news.tvb.com" in (url or ""):
        tvb = extract_tvb_article(html, url, title)
        if tvb:
            return tvb
    if re.search(r"<title[^>]*>\s*(just a moment|attention required)", html, re.I):
        return None
    meta_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]*content=["\']([^"\']+)', html, re.I)
    meta_image = re.search(r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]*content=["\']([^"\']+)', html, re.I)
    if not meta_image:
        meta_image = re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]*content=["\']([^"\']+)', html, re.I)
    out_title = htmllib.unescape(meta_title.group(1)).strip() if meta_title else title
    out_image = _abs_img(meta_image.group(1)) if meta_image else None

    # 1) JSON-LD articleBody（最干净）
    for x in _jsonld_objects(html):
        types = x.get("@type")
        types = types if isinstance(types, list) else [types]
        if not any(t in ("NewsArticle", "Article", "ReportageNewsArticle", "BlogPosting") for t in types):
            continue
        body = x.get("articleBody")
        if not isinstance(body, str) or len(body) < 200:
            continue
        text = _strip_tags(body)
        blocks = []
        for para in re.split(r"\n{2,}|(?<=[.!?])\s{2,}", text):
            para = re.sub(r"\s+", " ", para).strip()
            if len(para) >= 30:
                blocks.append({"text": para, "head": False})
        if not blocks:
            continue
        blocks = _finish(blocks, _origin(x.get("url") or url), out_title or title)
        if blocks:
            return {"title": out_title or title, "image": _ld_image(x) or out_image, "blocks": blocks}

    # 2) 页面段落（跳过 nav/footer/script）
    parser = _BlockParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return None
    blocks = parser.blocks
    if not blocks:
        return None
    # 先做一遍明显的垃圾过滤，再交给 _finish 切区间
    filtered = []
    for b in blocks:
        t = b["text"]
        if len(t) < 12:
            continue
        if NOISE_ANY_RE.match(t) or NOISE_HEAD.match(t):
            continue
        if re.match(r"^(home|news|business|markets|tech|opinion|life|sports)$", t, re.I):
            continue
        filtered.append(b)
    blocks = _finish(filtered, _origin(url), out_title or title)
    if not blocks:
        return None
    return {"title": out_title or title, "image": out_image, "blocks": blocks}


# ---------------------------------------------------------------------------
# 网络抓取
# ---------------------------------------------------------------------------
def _remaining(deadline: Optional[float]) -> float:
    if not deadline:
        return 30.0
    return max(1.0, min(30.0, deadline - time.monotonic()))


def fetch_html(url: str, deadline: Optional[float] = None) -> Optional[str]:
    """直接抓原文 HTML（curl_cffi 浏览器指纹 → requests 兜底）。"""
    timeout = _remaining(deadline)
    try:
        from curl_cffi import requests as creq

        resp = creq.get(url, impersonate="safari17_0", timeout=timeout, allow_redirects=True,
                        headers={"Accept": "text/html,application/xhtml+xml",
                                 "Accept-Language": "en-US,en;q=0.9"})
        if resp.status_code == 200 and len(resp.content) > 800:
            return resp.text
    except Exception:
        pass
    try:
        resp = requests.get(url, timeout=timeout, headers={
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"),
            "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9",
        })
        if resp.status_code == 200 and len(resp.content) > 800:
            return resp.text
    except Exception:
        pass
    return None


def jina_fetch(url: str, key: Optional[str] = None, timeout: float = 35,
               deadline: Optional[float] = None) -> Optional[str]:
    """经 r.jina.ai 抓取；带可选 key（提升配额/解锁 wayback）。返回文本或 None。"""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    for attempt in range(2):
        if deadline and time.monotonic() >= deadline:
            return None
        try:
            r = requests.get(JINA_ENDPOINT + url, headers=headers, timeout=timeout)
        except Exception:
            return None
        if r.status_code == 200:
            return r.text
        if r.status_code in (403, 429) and attempt == 0:
            time.sleep(5)
            continue
        return None
    return None


def fetch_text(url: str, key: Optional[str] = None, title: str = "",
               syndicated: Optional[dict] = None, archive_url: str = "",
               deadline: Optional[float] = None) -> Optional[list]:
    """正文获取链：转载版 → 原文 HTML → Jina 原文 → Jina 快照。返回块列表或 None。"""
    host = _origin(url).replace("https://", "").replace("http://", "")
    if syndicated and syndicated.get("blocks"):
        blocks = syndicated["blocks"]
        if quality_ok(blocks, title):
            return blocks
    html = fetch_html(url, deadline=deadline)
    if html:
        art = extract_article_html(html, title, url)
        if art and quality_ok(art["blocks"], title):
            return art["blocks"]
    md = jina_fetch(url, key=key, deadline=deadline)
    if md:
        blocks = denoise(md, host, title)
        if blocks and quality_ok(blocks, title):
            return blocks
    # Jina 的出口 IP 能过 archive.today 的 reCAPTCHA；直连被拦时用它读快照
    snapshots = ([archive_url] if archive_url else []) + ["https://archive.ph/newest/" + url]
    for snap in snapshots:
        md = jina_fetch(snap, key=key, deadline=deadline)
        if not md:
            continue
        blocks = denoise(md, host, title)
        if blocks and quality_ok(blocks, title):
            return blocks
    return None


# ---------------------------------------------------------------------------
# 私有仓库缓存（GitHub Contents API）
# ---------------------------------------------------------------------------

def cache_get(token: str, repo: str, path: str):
    r = requests.get(f"https://api.github.com/repos/{repo}/contents/{path}?ref=main",
                     headers={"Authorization": f"Bearer {token}",
                              "Accept": "application/vnd.github+json"}, timeout=30)
    if r.status_code != 200:
        return None
    return r.json().get("sha")


def cache_put(token: str, repo: str, path: str, payload) -> bool:
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


def cache_read(token: str, repo: str, path: str):
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
