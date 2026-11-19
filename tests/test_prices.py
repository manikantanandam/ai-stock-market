from datetime import datetime

import numpy as np
import pandas as pd

from aistock.data import prices
from aistock.data.prices import IST, market_phase


def at(day: str, hm: str) -> datetime:
    return datetime.fromisoformat(f"{day}T{hm}").replace(tzinfo=IST)


def test_market_phases():
    assert market_phase(at("2026-11-20", "09:00")) == "pre"     # Friday
    assert market_phase(at("2026-11-20", "09:15")) == "live"
    assert market_phase(at("2026-11-20", "15:44")) == "live"
    assert market_phase(at("2026-11-20", "15:45")) == "close"
    assert market_phase(at("2026-11-21", "11:00")) == "close"   # Saturday


def test_clean_drops_placeholder_rows_and_timezone():
    idx = pd.date_range("2026-11-17", periods=3, tz="Asia/Kolkata")
    raw = pd.DataFrame({"Open": [1.0, 2.0, np.nan], "High": [1.0, 2.0, np.nan], "Low": [1.0, 2.0, np.nan],
                        "Close": [1.0, 2.0, np.nan], "Volume": [10, 20, 0], "Dividends": 0.0}, index=idx)
    df = prices._clean(raw)
    assert len(df) == 2
    assert df.index.tz is None
    assert list(df.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_index_tickers_pass_through():
    assert prices._ticker("^NSEI") == "^NSEI"
    assert prices._ticker("M&M") == "M&M.NS"
