import fetch_news


def test_entry_image_media_content():
    e = {"media_content": [{"url": "https://a.com/pic.jpg"}]}
    assert fetch_news._entry_image(e) == "https://a.com/pic.jpg"


def test_entry_image_enclosure():
    e = {"links": [{"rel": "enclosure", "type": "image/jpeg", "href": "https://a.com/x.png"}]}
    assert fetch_news._entry_image(e) == "https://a.com/x.png"


def test_entry_image_from_content_html():
    e = {"content": [{"value": '<p>hi <img src="https://a.com/in.jpg"> bye</p>'}]}
    assert fetch_news._entry_image(e) == "https://a.com/in.jpg"


def test_entry_image_from_summary_protocol_relative():
    e = {"summary": '<img src="//static.a.com/p.webp">'}
    assert fetch_news._entry_image(e) == "https://static.a.com/p.webp"


def test_entry_image_skips_non_image_enclosure():
    e = {"links": [{"rel": "enclosure", "type": "audio/mpeg", "href": "https://a.com/x.mp3"}]}
    assert fetch_news._entry_image(e) is None


def test_entry_image_none():
    assert fetch_news._entry_image({}) is None
    assert fetch_news._entry_image({"summary": "no images here"}) is None
