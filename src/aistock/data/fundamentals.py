import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "fundamentals"
TTL_SECONDS = 7 * 24 * 3600  # ratios only change with quarterly results

FIELDS = [
    "longName", "sector", "industry", "marketCap", "trailingPE", "priceToBook",
    "returnOnEquity", "debtToEquity", "revenueGrowth", "earningsGrowth",
    "profitMargins", "currentRatio", "dividendYield", "trailingPegRatio",
]
RATIOS = FIELDS[3:]  # the numbers, without name / sector / industry


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


# Annual statement rows per figure: (statement, Yahoo row label)
STATEMENT_ROWS = {"revenue": ("income_stmt", "Total Revenue"), "net_income": ("income_stmt", "Net Income"),
                  "eps": ("income_stmt", "Diluted EPS"), "equity": ("balance_sheet", "Stockholders Equity"),
                  "debt": ("balance_sheet", "Total Debt")}


def get_statements(symbol: str) -> list[dict]:
    """Annual statement figures (about 4 fiscal years), oldest first: one dict per year end with
    `date` and the STATEMENT_ROWS keys (None when missing), cached for 7 days. Amounts are in the
    company's reporting currency, which for some NSE stocks is USD."""
    path = CACHE / "statements" / f"{symbol}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime < TTL_SECONDS:
        return json.loads(path.read_text())
    ticker = yf.Ticker(f"{symbol}.NS")
    frames = {name: getattr(ticker, name) for name in ("income_stmt", "balance_sheet")}

    def figure(df: pd.DataFrame, row: str, col) -> float | None:
        if row not in df.index or col not in df.columns or pd.isna(df.at[row, col]):
            return None
        return float(df.at[row, col])

    ends = sorted(set(frames["income_stmt"].columns) | set(frames["balance_sheet"].columns))
    years = [{"date": pd.Timestamp(end).date().isoformat(),
              **{k: figure(frames[stmt], row, end) for k, (stmt, row) in STATEMENT_ROWS.items()}} for end in ends]
    years = [y for y in years if any(y[k] is not None for k in STATEMENT_ROWS)]  # Yahoo pads with empty years
    path.write_text(json.dumps(years))
    return years


def prefetch(symbols: list[str], workers: int = 8, fetch=get_fundamentals) -> None:
    """Warm the cache in parallel; failures are left for `fetch` to raise when called again."""
    def one(sym):
        try:
            fetch(sym)
        except Exception:
            pass
    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, symbols))
