"""官方 API 适配层的形状测试（不联网）。"""
import feed_api


def test_wp_entries(monkeypatch):
    monkeypatch.setattr(feed_api, "_get_json", lambda *a, **k: [
        {"link": "https://time.com/1", "title": {"rendered": "A &amp; B"},
         "date": "2026-11-03T10:00:00", "jetpack_featured_media_url": "https://img/1.jpg"},
        {"link": "", "title": {"rendered": "skip"}},
    ])
    entries = feed_api.fetch_wp_entries("https://time.com/wp-json/wp/v2/posts")
    assert len(entries) == 1
    assert entries[0]["title"] == "A & B"
    assert entries[0]["link"] == "https://time.com/1"
    assert entries[0]["media_content"][0]["url"] == "https://img/1.jpg"


def test_nyt_entries(monkeypatch):
    monkeypatch.setattr(feed_api, "_get_json", lambda *a, **k: {
        "results": [{
            "title": "Markets rally",
            "url": "https://nytimes.com/a",
            "published_date": "2026-11-03T10:00:00-05:00",
            "multimedia": [
                {"url": "https://img/small.jpg", "format": "threeByTwoSmallAt2X"},
                {"url": "https://img/large.jpg", "format": "largeThreeByTwo"},
            ],
        }]
    })
    entries = feed_api.fetch_nyt_entries("home", "key")
    assert len(entries) == 1
    assert entries[0]["media_content"][0]["url"] == "https://img/large.jpg"
    assert entries[0]["published_parsed"] is not None


def test_guardian_entries(monkeypatch):
    monkeypatch.setattr(feed_api, "_get_json", lambda *a, **k: {
        "response": {"results": [{
            "webTitle": "Story",
            "webUrl": "https://theguardian.com/a",
            "webPublicationDate": "2026-11-03T10:00:00Z",
            "fields": {"thumbnail": "https://img/g.jpg"},
        }]}
    })
    entries = feed_api.fetch_guardian_entries("world", "key")
    assert len(entries) == 1
    assert entries[0]["media_content"][0]["url"] == "https://img/g.jpg"
