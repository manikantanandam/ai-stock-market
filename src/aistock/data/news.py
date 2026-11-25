import json
import re
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from functools import cache
from pathlib import Path

import feedparser
import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "news"
# Publishers' own feeds (RSS is published for programs to read). Each holds only
# the latest ~50 headlines, so they add same-day coverage on top of Google News.
# Moneycontrol's feeds stopped updating in 2024 and Business Standard refuses
# scripted requests (403), so neither is listed.
FEEDS = {
    "Economic Times": ["https://economictimes.indiatimes.com/markets/stocks/news/rssfeeds/2146842.cms",
                       "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
                       "https://economictimes.indiatimes.com/industry/rssfeeds/13352306.cms"],
    "Mint": ["https://www.livemint.com/rss/markets",
             "https://www.livemint.com/rss/companies"],
    "BusinessLine": ["https://www.thehindubusinessline.com/markets/feeder/default.rss",
                     "https://www.thehindubusinessline.com/companies/feeder/default.rss",
                     "https://www.thehindubusinessline.com/markets/stock-markets/feeder/default.rss"],
}
USER_AGENT = "aistock/0.1 (+https://github.com/manikantanandam/ai-stock-market)"


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


@cache
def _publisher_items() -> tuple[dict, ...]:
    """Every headline in FEEDS, fetched once per process (a scan reads them for ~500 stocks).
    A feed that fails is skipped; the others still count."""
    out = []
    for source, urls in FEEDS.items():
        for url in urls:
            feed = feedparser.parse(url, request_headers={"User-Agent": USER_AGENT})
            for e in feed.entries:
                if not e.get("title") or not e.get("published_parsed"):
                    continue
                published = datetime(*e.published_parsed[:6], tzinfo=timezone.utc)
                out.append({"title": " ".join(e.title.split()), "source": source,
                            "published": published.isoformat(), "link": e.get("link", "")})
    return tuple(out)


def mentions(title: str, keyword: str, symbol: str) -> bool:
    """Title names the company: its short name as whole words (any case), or its
    NSE symbol as an upper-case word (headlines often say TCS, BPCL, ITC)."""
    if re.search(rf"\b{re.escape(keyword)}\b", title, re.IGNORECASE):
        return True
    return len(symbol) >= 3 and re.search(rf"\b{re.escape(symbol)}\b", title) is not None


def _publisher_news(symbol: str, keyword: str, days: int) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return [n for n in _publisher_items() if n["published"] >= since and mentions(n["title"], keyword, symbol)]


def get_news(symbol: str, company: str | None, days: int = 14) -> list[dict]:
    """Recent headlines for an NSE stock, newest first, deduplicated, cached per day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{days}d_{date.today()}.json"
    if path.exists():
        return json.loads(path.read_text())
    # "Infosys Limited" -> "Infosys": the short name matches far more headlines
    keyword = (company or symbol).replace(" Limited", "").replace(" Ltd", "").strip()
    items = (_google_news(f'"{keyword}" share', days) + _publisher_news(symbol, keyword, days)
             + _yahoo_news(symbol, keyword.split()[0]))
    seen, news = set(), []
    for n in sorted(items, key=lambda n: n["published"], reverse=True):
        key = n["title"].lower()
        if key not in seen:
            seen.add(key)
            news.append(n)
    path.write_text(json.dumps(news))
    return news
