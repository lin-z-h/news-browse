"""archive_probe 的纯函数/状态测试（不联网）。"""
import archive_probe as ap


class FakeResp:
    def __init__(self, url, headers=None):
        self.url = url
        self.headers = headers or {}


def test_snapshot_from_memento_header():
    r = FakeResp("https://archive.ph/abc", {"Memento-Datetime": "2026-11-03T00:00:00Z"})
    assert ap._snapshot_from_response(r) == "https://archive.ph/abc"


def test_snapshot_from_url_shape():
    r = FakeResp("https://archive.ph/AbC123/https://example.com/x")
    assert ap._snapshot_from_response(r) == r.url


def test_no_snapshot_signal():
    assert ap._snapshot_from_response(FakeResp("https://archive.ph/newest/https://x")) is None


def test_probe_link_archive(monkeypatch):
    monkeypatch.setattr(ap, "probe_archive", lambda url: "https://archive.ph/abc/https://x")
    res = ap.probe_link("https://x")
    assert res["status"] == "archive"
    assert res["archive_url"].endswith("https://x")


def test_probe_link_original_when_nothing(monkeypatch):
    monkeypatch.setattr(ap, "probe_archive", lambda url: None)
    monkeypatch.setattr(ap, "probe_wayback", lambda url: None)
    res = ap.probe_link("https://x")
    assert res == {"url": "https://x", "status": "original",
                   "archive_url": None, "wayback_url": None}


def test_probe_link_unarchived_when_auto_archive_fails(monkeypatch):
    monkeypatch.setattr(ap, "probe_archive", lambda url: None)
    monkeypatch.setattr(ap, "submit_archive", lambda *a, **k: None)
    monkeypatch.setattr(ap, "probe_wayback", lambda url: None)
    res = ap.probe_link("https://x", auto_archive=True, wait_seconds=1)
    assert res["status"] == "unarchived"
