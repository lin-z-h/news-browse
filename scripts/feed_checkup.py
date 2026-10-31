"""体检所有源：抓取健康度 + 内容新鲜度（揪出 WSJ 型"活着但冻结"的源）。"""
import json
import sys
import calendar
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import feedparser
from fetch_news import _http_get

now = datetime.now(timezone.utc).timestamp()
config = json.load(open(Path(__file__).resolve().parent.parent / "config" / "sources.json", encoding="utf-8"))

print(f"{'源':<22}{'feed':<52}{'条数':>5}{'最新文章距今':>14}{'无日期':>6}  feed.updated")
print("-" * 120)
problems = []
for src in config["sources"]:
    for feed in src["feeds"]:
        url = feed if isinstance(feed, str) else feed["url"]
        topic = "General" if isinstance(feed, str) else feed.get("topic", "General")
        label = f"{src['name']} [{topic}]"
        try:
            entries = feedparser.parse(_http_get(url)).entries
        except Exception as exc:
            print(f"{label:<22}{url[:50]:<52}{'FAIL':>5}  {type(exc).__name__}: {str(exc)[:40]}")
            problems.append((label, url, "FETCH FAIL"))
            continue
        dates = []
        for e in entries:
            st = e.get("published_parsed") or e.get("updated_parsed")
            dates.append(calendar.timegm(st) if st else None)
        nodate = sum(1 for d in dates if d is None)
        newest = max((d for d in dates if d), default=None)
        age_h = (now - newest) / 3600 if newest else None
        age_s = f"{age_h:.0f}h" if age_h is not None and age_h < 48 else (f"{age_h/24:.1f}d" if age_h is not None else "?")
        upd = getattr(feedparser.parse, "__name__", "") and ""
        fresh = (age_h is not None and age_h <= 72)
        flag = "" if (fresh and len(entries) >= 3) else "  <-- STALE/THIN"
        print(f"{label:<22}{url[:50]:<52}{len(entries):>5}{age_s:>14}{nodate:>6}  {flag}")
        if not fresh or len(entries) < 3:
            problems.append((label, url, f"newest={age_s}, entries={len(entries)}"))

print()
if problems:
    print("发现问题:")
    for p in problems:
        print("  *", p[0], p[2])
else:
    print("全部源健康。")
