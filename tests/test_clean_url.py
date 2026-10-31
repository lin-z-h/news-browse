from clean_url import is_tracking_param, strip_tracking


def test_removes_utm_and_fbclid():
    url = (
        "https://www.nytimes.com/2026/10/31/world/x.html"
        "?utm_source=rss&utm_medium=link&fbclid=abc&id=42"
    )
    assert strip_tracking(url) == "https://www.nytimes.com/2026/10/31/world/x.html?id=42"


def test_removes_all_utm_prefix_params():
    url = "https://example.com/a?utm_campaign=c&utm_content=z&utm_term=t&keep=1"
    assert strip_tracking(url) == "https://example.com/a?keep=1"


def test_keeps_business_params():
    url = "https://example.com/search?q=economist&lang=en&p=3"
    assert strip_tracking(url) == url


def test_case_insensitive_param_names():
    url = "https://example.com/a?UTM_Source=rss&FBCLID=x&id=1"
    assert strip_tracking(url) == "https://example.com/a?id=1"


def test_media_site_specific_params():
    assert "cmpid" not in strip_tracking("https://bloomberg.com/x?cmpid=abc&id=1")
    assert "smid" not in strip_tracking("https://nytimes.com/x?smid=nytcore&smtyp=share&id=2")


def test_no_query_unchanged():
    assert strip_tracking("https://example.com/a/b") == "https://example.com/a/b"


def test_fragment_preserved():
    url = "https://example.com/a?fbclid=x#section"
    assert strip_tracking(url) == "https://example.com/a#section"


def test_non_http_unchanged():
    url = "mailto:a@b.c?subject=hi"
    assert strip_tracking(url) == url


def test_blank_tracking_param_removed():
    assert strip_tracking("https://example.com/a?fbclid=&id=1") == "https://example.com/a?id=1"


def test_is_tracking_param():
    assert is_tracking_param("utm_medium")
    assert is_tracking_param("fbclid")
    assert not is_tracking_param("page")
    assert not is_tracking_param("q")
