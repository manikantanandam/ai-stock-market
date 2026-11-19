import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "fundamentals"
TTL_SECONDS = 7 * 24 * 3600  # ratios only change with quarterly results

FIELDS = [
    "longName", "sector", "industry", "marketCap", "trailingPE", "priceToBook",
    "returnOnEquity", "debtToEquity", "revenueGrowth", "earningsGrowth",
    "profitMargins", "currentRatio", "dividendYield", "trailingPegRatio",
]


def get_fundamentals(symbol: str) -> dict:
    """Snapshot of key ratios for an NSE symbol, cached for 7 days."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}.json"
    if path.exists() and time.time() - path.stat().st_mtime < TTL_SECONDS:
        return json.loads(path.read_text())
    info = yf.Ticker(f"{symbol}.NS").info
    data = {k: info.get(k) for k in FIELDS}
    path.write_text(json.dumps(data))
    return data


def prefetch(symbols: list[str], workers: int = 8) -> None:
    """Warm the cache in parallel; failures are left for get_fundamentals to raise."""
    def one(sym):
        try:
            get_fundamentals(sym)
        except Exception:
            pass
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, symbols))
