import json
import urllib.parse
from datetime import date, datetime, timezone
from pathlib import Path

import feedparser
import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "news"


def _google_news(query: str, days: int) -> list[dict]:
    q = urllib.parse.quote(f"{query} when:{days}d")
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en")
    out = []
    for e in feed.entries:
        source = e.get("source", {}).get("title", "")
        title = e.title.removesuffix(f" - {source}") if source else e.title
        published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
        out.append({"title": title, "source": source, "published": published.isoformat(), "link": e.link})
    return out


def _yahoo_news(symbol: str, keyword: str) -> list[dict]:
    """yfinance news is mostly US/ADR coverage; keep only items naming the company."""
    out = []
    for item in yf.Ticker(f"{symbol}.NS").news or []:
        c = item.get("content", {})
        title = c.get("title", "")
        if keyword.lower() not in title.lower():
            continue
        out.append({"title": title, "source": (c.get("provider") or {}).get("displayName", "Yahoo"),
                    "published": c.get("pubDate", "").replace("Z", "+00:00"),
                    "link": (c.get("canonicalUrl") or {}).get("url", "")})
    return out


def get_news(symbol: str, company: str | None, days: int = 14) -> list[dict]:
    """Recent headlines for an NSE stock, newest first, deduplicated, cached per day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{days}d_{date.today()}.json"
    if path.exists():
        return json.loads(path.read_text())
    # "Infosys Limited" -> "Infosys": the short name matches far more headlines
    keyword = (company or symbol).replace(" Limited", "").replace(" Ltd", "").strip()
    items = _google_news(f'"{keyword}" share', days) + _yahoo_news(symbol, keyword.split()[0])
    seen, news = set(), []
    for n in sorted(items, key=lambda n: n["published"], reverse=True):
        key = n["title"].lower()
        if key not in seen:
            seen.add(key)
            news.append(n)
    path.write_text(json.dumps(news))
    return news
