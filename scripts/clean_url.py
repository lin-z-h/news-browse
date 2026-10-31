"""链接清洗：移除 utm_*、fbclid 等跟踪参数，保留业务参数。

规则刻意做成"黑名单式移除"：只删已知跟踪参数，其余一律保留，
宁可漏删也不误删业务参数。全部为纯函数，方便单元测试。
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 精确匹配的跟踪参数（比较时转为小写）
EXACT_TRACKING = {
    # 广告/社交点击跟踪
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid", "ttclid",
    "li_fat_id", "igshid", "mkt_tok", "vero_id", "wickedid", "ncid",
    # 邮件营销
    "mc_cid", "mc_eid", "s_cid", "otaid",
    # 媒体站点自有跟踪
    "cmpid",                        # Bloomberg
    "smid", "smtyp", "smvid",       # NYT 分享跟踪
    "ref_src", "ref_url", "share_id", "shem",
    # 统计工具
    "_ga", "_gl", "spm",
}

# 前缀匹配：utm_source / utm_medium / utm_campaign … 全家桶
TRACKING_PREFIXES = ("utm_",)


def is_tracking_param(name: str) -> bool:
    n = name.strip().lower()
    return n in EXACT_TRACKING or n.startswith(TRACKING_PREFIXES)


def strip_tracking(url: str) -> str:
    """移除 URL 中的跟踪参数，保留其余部分（含 fragment）。

    非 http(s) 链接或无 query 时原样返回；仅当确实移除了参数时才重建
    query，避免无谓的编码往返改变原链接。
    """
    if not url:
        return ""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if parts.scheme.lower() not in ("http", "https") or not parts.query:
        return url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if not is_tracking_param(k)]
    if len(kept) == len(pairs):
        return url
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment)
    )
