"""正文提取的回归测试：付费墙/验证页/archive 索引页必须拒绝，正常文章必须通过。"""
import readability as rd


def _article_md(title="Central banks weigh rate path as inflation cools", n=4):
    paras = [
        f"{title}.",
        "Policymakers are entering the final stretch of the year with inflation cooling faster than "
        "they expected, leaving investors to debate how quickly borrowing costs can fall.",
        "Officials have repeatedly said they will move meeting by meeting, but markets are already "
        "pricing in several cuts over the next twelve months, according to futures data.",
        "Economists warn that a premature easing cycle could revive demand and push prices higher "
        "again, while waiting too long risks a sharper slowdown in hiring and investment.",
        "The central bank will publish its latest projections alongside the decision, and the "
        "governor is scheduled to brief reporters afterwards.",
        "A separate report showed wage growth easing in several services industries, a development "
        "that officials have described as necessary before they can be confident about inflation.",
    ]
    body = "\n\n".join([f"# {title}", *paras[:n]])
    return (
        f"Title: {title}\n\nURL Source: https://example.com/news/story\n\n"
        f"Markdown Content:\n{body}\n"
    )


def test_normal_article_passes():
    blocks = rd.denoise(_article_md(), "example.com", "Central banks weigh rate path as inflation cools")
    assert blocks
    texts = [b["text"] for b in blocks if "text" in b]
    assert len(texts) >= 3
    assert "Policymakers" in texts[0]


def test_paywall_shell_rejected():
    """截图里的 Bloomberg 付费墙/登录页：不能当正文渲染。"""
    md = (
        "Title: Bloomberg\n\nURL Source: https://www.bloomberg.com/\n\nMarkdown Content:\n"
        "$1Do Not Sell or Share My Personal Information$1$1\n\n"
        "$1©2026 Bloomberg L.P. All Rights Reserved.\n\n"
        "Unlock election coverage, expert analysis and more\n\n"
        "By continuing, I agree to the $1 and $1.\n\n"
        "Continue with Apple\n\n"
        "Sign in with Google Sign in with Google. Opens in new tab\n\n"
        "Continue with Google\n\n"
        "Continue with BBA\n\n"
        "Sign In$1$1Do Not Sell or Share My Personal Information\n"
    )
    assert rd.denoise(md, "bloomberg.com", "Some story") is None
    assert rd.looks_like_paywall(md)


def test_archive_index_page_rejected():
    """archive.today 的 newest 页：跨来源标题列表，不能当正文。"""
    md = (
        "Title: Australia's social media ban\n\nURL Source: https://archive.ph/newest/https://x\n\n"
        "Markdown Content:\n"
        "# Australia's social media ban 'ill-thought-out', Reddit argues in high court challenge\n\n"
        "The New York Times\n\nFinancial Times\n\nThe Washington Post\n\nLianhe Zaobao (EN)\n\n"
        "投资者343萬元入市大埔翠屏花園1房戶\n\n"
        "Vice-Premier He Lifeng vows support for Hong Kong as international financial centre\n\n"
        "Single check-in for those flying through Hong Kong from mainland China under new scheme\n\n"
        "Hochstein: Markets Should Be Cognizant of Strike Risk (Video)\n"
    )
    assert rd.denoise(md, "archive.ph", "Australia's social media ban") is None


def test_short_bloomberg_teaser_rejected():
    """付费墙源只拿到 2 段导语：交给转载版，不渲染半篇文章。"""
    md = _article_md("Bayer Raises Profit Outlook", n=2).replace("example.com", "bloomberg.com")
    md = md.replace("https://example.com/news/story", "https://www.bloomberg.com/news/articles/x")
    assert rd.denoise(md, "www.bloomberg.com", "Bayer Raises Profit Outlook") is None


EDGE_HTML = """<!doctype html><html><head>
<title>Bayer raises profit outlook on strong demand for drugs</title>
<meta property="og:title" content="Bayer raises profit outlook on strong demand for drugs">
<meta property="og:image" content="https://assets.theedgemarkets.com/bayer.jpg">
</head><body>
<nav><p>Home</p><p>Markets</p><p>Tech</p></nav>
<article>
<p>(Nov 3): Bayer AG raised its annual profit outlook after increased demand for some of its newer
drugs drove a better-than-expected performance at its pharmaceutical unit.</p>
<p>Adjusted earnings before interest, tax, depreciation and amortisation are now expected between
9.8 billion euros and 10.3 billion euros this year, the German company said on Tuesday.</p>
<p>Sales of prostate cancer drug Nubeqa and kidney therapy Kerendia exceeded expectations in the
quarter, prompting the company to lift its guidance for the second time this year.</p>
<p>Third-quarter earnings also exceeded the average estimate of analysts surveyed by Bloomberg,
according to a statement published after the market closed.</p>
</article>
<section><h2>Most Read</h2><p>Another headline that should not be included in the article body
because it belongs to the recommendation rail at the bottom of the page.</p></section>
<footer><p>© 2026 The Edge Malaysia. All rights reserved.</p></footer>
</body></html>"""


def test_extract_edge_malaysia_html():
    art = rd.extract_article_html(EDGE_HTML, "Bayer raises profit outlook on strong demand for drugs",
                                  "https://theedgemalaysia.com/node/1")
    assert art is not None
    assert art["image"] == "https://assets.theedgemarkets.com/bayer.jpg"
    assert len(art["blocks"]) >= 3
    joined = " ".join(b["text"] for b in art["blocks"])
    assert "Bayer AG raised" in joined
    assert "Most Read" not in joined


def test_jsonld_article_body_extracted():
    body = ("Stocks rose on Tuesday as investors welcomed cooler inflation data and growing hopes "
            "that the central bank will slow the pace of interest-rate increases.\\n\\n"
            "The benchmark index gained for a third straight session, led by technology and consumer "
            "shares, while trading volumes remained above their twenty-day averages.\\n\\n"
            "Bond yields fell after the report, while the dollar weakened against most major peers as "
            "traders trimmed their expectations for further tightening this year.")
    html = ('<html><head><script type="application/ld+json">'
            '{"@type":"NewsArticle","headline":"Markets rally","image":"https://x.test/a.jpg",'
            '"articleBody":"' + body + '"}</script></head><body></body></html>')
    art = rd.extract_article_html(html, "Markets rally", "https://x.test/a")
    assert art is not None
    assert art["image"] == "https://x.test/a.jpg"
    assert len(art["blocks"]) == 3
