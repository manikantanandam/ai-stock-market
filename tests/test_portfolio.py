from dataclasses import replace

import pandas as pd
import pytest

from aistock.backtest import engine, portfolio
from conftest import random_walk


def every_n(ind: pd.DataFrame, n: int) -> pd.Series:
    s = pd.Series(False, index=ind.index)
    s.iloc[220::n] = True  # after SMA200 warm-up
    return s


def test_single_stock_matches_engine(free_params):
    """With one stock and one slot, the portfolio must reproduce the engine."""
    ind = random_walk(500, seed=1)
    entries = every_n(ind, 7)
    _, _, pt = portfolio.run({"A": ind}, {"A": entries}, ind.index, free_params, max_positions=1)
    et = engine.run(ind, entries, free_params).trades
    assert len(pt) == len(et) > 5
    assert pt.ret.to_numpy() == pytest.approx(et.ret.to_numpy())
    assert list(pt.reason) == list(et.reason)


def test_slots_are_respected(free_params):
    data = {s: random_walk(400, seed=i) for i, s in enumerate("ABCDE")}
    entries = {s: every_n(d, 3) for s, d in data.items()}
    dates = data["A"].index
    _, invested, trades = portfolio.run(data, entries, dates, replace(free_params, max_hold=30), max_positions=2)
    # Count overlapping positions on every date
    for d in dates[::10]:
        open_ = ((pd.to_datetime(trades.entry_date) <= d) & (pd.to_datetime(trades.exit_date) > d)).sum()
        assert open_ <= 2
    assert invested.max() <= 1.0 + 1e-9


def test_cash_from_intraday_exit_not_reused_same_day(free_params):
    """Stock A stops out on day 2 while B signals on day 1; with one slot,
    B can only be bought on day 3 (the slot frees after the day's exits)."""
    idx = pd.bdate_range("2026-01-01", periods=5)
    base = {"Volume": 1_000, "atr14": 10.0, "sma50": 1.0, "sma200": 1.0, "rsi14": 50.0, "macd_hist": 1.0}
    a = pd.DataFrame({"Open": [100, 100, 100, 100, 100], "High": [101, 101, 101, 101, 101],
                      "Low": [99, 99, 70, 99, 99], "Close": [100] * 5, **base}, index=idx)
    b = a.assign(Low=99.0)
    ent_a = pd.Series([True, False, False, False, False], index=idx)
    ent_b = pd.Series([False, True, True, False, False], index=idx)
    _, _, t = portfolio.run({"A": a, "B": b}, {"A": ent_a, "B": ent_b}, idx, free_params, max_positions=1)
    a_trade = t[t.symbol == "A"].iloc[0]
    b_trade = t[t.symbol == "B"].iloc[0]
    assert a_trade.exit_date == idx[2] and a_trade.reason == "stop"
    assert b_trade.entry_date == idx[3]


def test_equal_weight_benchmark():
    idx = pd.bdate_range("2026-01-01", periods=3)
    data = {"A": pd.DataFrame({"Close": [100, 110, 121]}, index=idx),
            "B": pd.DataFrame({"Close": [100, 90, 81]}, index=idx)}
    curve = portfolio.equal_weight(data, idx)
    assert curve.iloc[-1] == pytest.approx(1.0)  # +10% and -10% average to 0 each day


def test_slope_rank_gives_the_slot_to_the_steepest_sma200(free_params):
    """Three stocks signal on the same bar with one free slot: 'slope' picks the
    steepest SMA200, 'score' doesn't care about slope."""
    data = {s: random_walk(400, seed=i, drift=d) for i, (s, d) in enumerate([("FLAT", 0.0), ("UP", 0.003), ("MID", 0.0015)])}
    dates = data["UP"].index
    entries = {s: pd.Series(dates == dates[300], index=dates) for s in data}
    slope = {s: portfolio.rank_inputs(d, "slope")[0].iloc[300] for s, d in data.items()}
    _, _, trades = portfolio.run(data, entries, dates, free_params, max_positions=1, rank_by="slope")
    assert trades.symbol.iloc[0] == max(slope, key=slope.get)
    with pytest.raises(ValueError):
        portfolio.rank_inputs(data["UP"], "nope")
