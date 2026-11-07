"""转载版解析的纯函数测试（不联网）。"""
import syndication as s


def test_title_similar_ignores_publisher_suffix():
    a = "Bayer Raises Profit Outlook on Strong Demand for Drugs"
    b = "Bayer raises profit outlook on strong demand for drugs - The Edge Malaysia"
    assert s.title_similar(a, s._clean_title(b)) > 0.95


def test_title_similar_rejects_unrelated():
    a = "Bayer Raises Profit Outlook on Strong Demand for Drugs"
    b = "Moneychangers group says enough pounds to meet demand"
    assert s.title_similar(a, s._clean_title(b)) < 0.2


def test_clean_title_strips_bloomberg_suffix():
    assert s._clean_title("Stocks climb - Bloomberg") == "Stocks climb"
    assert s._clean_title("Story - The Edge Singapore") == "Story"


def test_cache_key_stable():
    k1 = s.cache_key("A title", ["edge_my", "yahoo"])
    k2 = s.cache_key("A title", ["edge_my", "yahoo"])
    k3 = s.cache_key("A title", ["edge_my"])
    assert k1 == k2 and k1 != k3
