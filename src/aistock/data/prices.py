import logging
import time
from datetime import datetime
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "prices"
IST = ZoneInfo("Asia/Kolkata")
LIVE_TTL_SECONDS = 15 * 60


def market_phase(now: datetime | None = None) -> str:
    """'pre' before the open, 'live' during the session (with a buffer for
    Yahoo's final bar), 'close' after it and on weekends. Holidays read as a
    normal weekday; Yahoo then simply has no bar for that date."""
    now = now or datetime.now(IST)
    if now.weekday() >= 5 or now.time() >= dtime(15, 45):
        return "close"
    return "pre" if now.time() < dtime(9, 15) else "live"


def get_prices(symbol: str, years: int | None = 2) -> pd.DataFrame:
    """Daily OHLCV for an NSE symbol (e.g. RELIANCE) or a Yahoo index, future or FX ticker (^NSEI, GC=F).
    years=None fetches the full history.

    Cached per IST date and market phase, so a mid-session fetch (with a
    partial bar for today) is never reused after the close; live data is
    refreshed every 15 minutes.
    """
    path = _path(symbol, years)
    if _fresh(path):
        return pd.read_parquet(path)
    raw = yf.Ticker(_ticker(symbol)).history(period=_period(years), auto_adjust=True)
    df = _clean(raw) if not raw.empty else raw
    if df.empty:
        raise ValueError(f"no price data for {_ticker(symbol)}")
    df.to_parquet(path)
    return df


def prefetch(symbols: list[str], years: int | None, chunk: int = 100) -> None:
    """Batch-download uncached symbols with yf.download (threaded), which is far
    faster than one request per symbol for large watchlists. Symbols that come
    back empty are left for get_prices to retry and report."""
    todo = [s for s in symbols if not _fresh(_path(s, years))]
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)  # failures surface via get_prices
    for start in range(0, len(todo), chunk):
        batch = todo[start:start + chunk]
        raw = yf.download([_ticker(s) for s in batch], period=_period(years), auto_adjust=True,
                          group_by="ticker", threads=True, progress=False)
        for sym in batch:
            if _ticker(sym) not in raw.columns.get_level_values(0):
                continue
            df = _clean(raw[_ticker(sym)])
            if not df.empty:
                df.to_parquet(_path(sym, years))


def _ticker(symbol: str) -> str:
    """Yahoo tickers for indices (^NSEI), futures (GC=F) and FX (USDINR=X) pass through."""
    return symbol if symbol.startswith("^") or "=" in symbol else f"{symbol}.NS"


def _period(years: int | None) -> str:
    return "max" if years is None else f"{years}y"


def _path(symbol: str, years: int | None) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    return CACHE / f"{symbol}_{_period(years)}_{datetime.now(IST).date()}_{market_phase()}.parquet"


def _fresh(path: Path) -> bool:
    if not path.exists():
        return False
    return not path.stem.endswith("_live") or time.time() - path.stat().st_mtime < LIVE_TTL_SECONDS


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    # Yahoo sometimes returns a placeholder row with NaN prices for the latest session
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df.index.name = "Date"
    return df
