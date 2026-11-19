from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf

CACHE = Path(__file__).resolve().parents[3] / ".cache" / "prices"


def get_prices(symbol: str, years: int = 2) -> pd.DataFrame:
    """Daily OHLCV for an NSE symbol (e.g. RELIANCE) or a Yahoo index (e.g. ^NSEI), cached per day."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{symbol}_{years}y_{date.today()}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    ticker = symbol if symbol.startswith("^") else f"{symbol}.NS"
    df = yf.Ticker(ticker).history(period=f"{years}y", auto_adjust=True)
    if df.empty:
        raise ValueError(f"No price data for {ticker}")
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    df.index = df.index.tz_localize(None)
    df.to_parquet(path)
    return df
