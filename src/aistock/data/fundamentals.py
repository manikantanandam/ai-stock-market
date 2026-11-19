import json
from datetime import date
from pathlib import Path

import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "fundamentals"

FIELDS = [
    "longName", "sector", "industry", "marketCap", "trailingPE", "priceToBook",
    "returnOnEquity", "debtToEquity", "revenueGrowth", "earningsGrowth",
    "profitMargins", "currentRatio", "dividendYield", "trailingPegRatio",
]


def get_fundamentals(symbol: str) -> dict:
    """Snapshot of key ratios for an NSE symbol, cached per day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{date.today()}.json"
    if path.exists():
        return json.loads(path.read_text())
    info = yf.Ticker(f"{symbol}.NS").info
    data = {k: info.get(k) for k in FIELDS}
    path.write_text(json.dumps(data))
    return data
