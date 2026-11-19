"""Shared fixtures. Tests never touch the network: price data is synthetic."""
import numpy as np
import pandas as pd
import pytest

from aistock.backtest.costs import CostModel
from aistock.backtest.engine import Params
from aistock.technical.indicators import add_indicators

FREE = CostModel(brokerage=0, stt=0, exchange=0, sebi=0, stamp_buy=0, gst=0, dp_per_sell=0, slippage=0)


def bars(rows: list[tuple], atr: float = 10.0, start: str = "2026-01-01") -> pd.DataFrame:
    """Frame from (open, high, low, close) rows with a constant ATR, for exact engine tests."""
    df = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"],
                      index=pd.bdate_range(start, periods=len(rows)))
    df["Volume"] = 1_000
    df["atr14"] = atr
    return df


def entry_on(df: pd.DataFrame, *positions: int) -> pd.Series:
    s = pd.Series(False, index=df.index)
    s.iloc[list(positions)] = True
    return s


def random_walk(n: int = 400, seed: int = 0, drift: float = 0.0005, start_price: float = 100.0,
                start: str = "2024-01-01") -> pd.DataFrame:
    """Realistic-ish OHLCV with indicators, for tests that need add_indicators columns."""
    rng = np.random.default_rng(seed)
    close = start_price * np.exp(np.cumsum(rng.normal(drift, 0.015, n)))
    open_ = close * (1 + rng.normal(0, 0.003, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.006, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.006, n)))
    df = pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close,
                       "Volume": rng.integers(1_000, 5_000, n)}, index=pd.bdate_range(start, periods=n))
    return add_indicators(df)


@pytest.fixture
def free_params() -> Params:
    """No costs or slippage: fills and returns are exact."""
    return Params(stop_atr=2.0, target_atr=3.0, max_hold=20, capital=100_000.0, costs=FREE)
