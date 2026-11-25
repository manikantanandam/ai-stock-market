import pandas as pd
import pytest

from aistock.backtest import universe
from aistock.data import sectors
from conftest import random_walk


def test_equal_weight_averages_returns_of_members_trading_that_day():
    d = pd.bdate_range("2026-01-01", periods=3)
    a = pd.Series([100.0, 110.0, 121.0], index=d)   # +10%, +10%
    b = pd.Series([50.0, 45.0], index=d[1:])        # listed on day 2, then -10%
    level = sectors.equal_weight({"A": a, "B": b})
    assert level.tolist() == pytest.approx([1.0, 1.1, 1.1])


def test_filters_combine_rs_and_sector(monkeypatch):
    ind = random_walk(300, seed=1, drift=0.003)
    monkeypatch.setattr(universe, "get_prices", lambda s, y: ind.assign(Close=100.0))  # flat index
    monkeypatch.setattr(sectors, "INDUSTRY", {"UP": "IT"})
    monkeypatch.setattr(sectors, "index", lambda industry, years: ind.Close)
    out = universe.filters({"UP": ind, "NOSECTOR": ind}, rs=True, sector=True, years=2)
    expected = (ind.Close / ind.Close.shift(126) > 1) & (ind.Close / ind.Close.shift(63) > 1) \
        & (ind.Close > ind.Close.rolling(50).mean())
    assert out["UP"].tolist() == expected.tolist()
    assert not out["NOSECTOR"].any()                # unknown sector blocks entries
    assert universe.filters({"UP": ind}, rs=False, sector=False, years=2) is None
