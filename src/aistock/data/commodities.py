"""Commodity futures: Yahoo's continuous front-month contracts, shown in INR.

Yahoo has no MCX data, so each commodity uses its global benchmark (COMEX,
NYMEX) converted with USDINR=X into MCX's quote unit (gold per 10 g, silver
per kg, ...). MCX prices also carry import duty and their own basis, so levels
sit a few percent off MCX; the trend and the signals follow the global
contract. Volume stays in the global contract's lots.

A futures day closes at 17:00 New York (CME), i.e. ~02:30-03:30 IST the next
day, so a scan during the Indian day sees a partial bar for today. Only
completed sessions are kept.
"""
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from aistock.data import prices

FX = "USDINR=X"
NEW_YORK = ZoneInfo("America/New_York")
SESSION_CLOSE = time(17, 0)
OUNCE_G = 31.1034768
POUND_KG = 0.45359237
OHLC = ["Open", "High", "Low", "Close"]


@dataclass(frozen=True)
class Contract:
    symbol: str    # MCX name, used in the journal
    name: str
    ticker: str    # Yahoo continuous future
    unit: str      # MCX quote unit the INR price is in
    factor: float  # Yahoo's USD quote -> USD per MCX unit
    group: str
    news: str      # Google News phrase


CONTRACTS = {c.symbol: c for c in [
    Contract("GOLD", "Gold", "GC=F", "10 g", 10 / OUNCE_G, "Bullion", "gold price"),
    Contract("SILVER", "Silver", "SI=F", "1 kg", 1000 / OUNCE_G, "Bullion", "silver price"),
    Contract("CRUDEOIL", "Crude oil (WTI)", "CL=F", "1 bbl", 1.0, "Energy", "crude oil price"),
    Contract("NATURALGAS", "Natural gas", "NG=F", "1 mmBtu", 1.0, "Energy", "natural gas price"),
    Contract("COPPER", "Copper", "HG=F", "1 kg", 1 / POUND_KG, "Base metals", "copper price"),
    Contract("ALUMINIUM", "Aluminium", "ALI=F", "1 kg", 1 / 1000, "Base metals", "aluminium price"),
]}


def get(symbol: str) -> Contract:
    symbol = symbol.upper()
    if symbol not in CONTRACTS:
        raise ValueError(f"unknown commodity {symbol!r}; choose from {', '.join(CONTRACTS)}")
    return CONTRACTS[symbol]


def to_inr(usd: pd.DataFrame, fx: pd.Series, factor: float) -> pd.DataFrame:
    """OHLC in INR per MCX unit, using the latest USDINR close on or before each bar.
    Bars at or below zero (WTI in April 2020) are dropped: ATR stops and returns
    don't work through negative prices."""
    rate = fx.reindex(fx.index.union(usd.index)).ffill().reindex(usd.index)
    out = usd.copy()
    for k in OHLC:
        out[k] = usd[k] * rate * factor
    out = out.dropna(subset=OHLC)
    return out[out.Low > 0]


def completed(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    """Drop the last bar if its session hasn't closed yet (17:00 New York)."""
    now = (now or datetime.now(NEW_YORK)).astimezone(NEW_YORK)
    if df.empty:
        return df
    last = df.index[-1]
    closes = datetime.combine(last.date(), SESSION_CLOSE, NEW_YORK)
    return df.iloc[:-1] if now < closes else df


def get_prices(symbol: str, years: int = 2) -> pd.DataFrame:
    """Daily OHLCV for a commodity in INR per its MCX unit, completed sessions only."""
    c = get(symbol)
    return completed(to_inr(prices.get_prices(c.ticker, years), usd_inr(years), c.factor))


def usd_inr(years: int = 2) -> pd.Series:
    return prices.get_prices(FX, years).Close


def prefetch(years: int) -> None:
    prices.prefetch([c.ticker for c in CONTRACTS.values()] + [FX], years)
