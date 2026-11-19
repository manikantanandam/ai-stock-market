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


def get_prices(symbol: str, years: int = 2) -> pd.DataFrame:
    """Daily OHLCV for an NSE symbol (e.g. RELIANCE) or a Yahoo index (e.g. ^NSEI).

    Cached per IST date and market phase, so a mid-session fetch (with a
    partial bar for today) is never reused after the close; live data is
    refreshed every 15 minutes.
    """
    CACHE.mkdir(parents=True, exist_ok=True)
    phase = market_phase()
    path = CACHE / f"{symbol}_{years}y_{datetime.now(IST).date()}_{phase}.parquet"
    if path.exists() and (phase != "live" or time.time() - path.stat().st_mtime < LIVE_TTL_SECONDS):
        return pd.read_parquet(path)
    ticker = symbol if symbol.startswith("^") else f"{symbol}.NS"
    df = yf.Ticker(ticker).history(period=f"{years}y", auto_adjust=True)
    if df.empty:
        raise ValueError(f"No price data for {ticker}")
    # Yahoo sometimes returns a placeholder row with NaN prices for the latest session
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "High", "Low", "Close"])
    df.index = df.index.tz_localize(None)
    df.to_parquet(path)
    return df
