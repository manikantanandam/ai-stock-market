import numpy as np
import pandas as pd
import pytest

from aistock.backtest import engine, sweep, walkforward
from conftest import random_walk


def test_windows_expand_with_a_gap_before_each_test_year():
    dates = pd.bdate_range("2018-01-01", "2023-06-30")
    wins = walkforward.windows(dates, min_train_years=3, gap=20)
    assert [w.year for w in wins] == [2021, 2022, 2023]
    for w in wins:
        assert w.train_start == dates[0]
        assert w.test_start == dates[dates.year == w.year][0]
        assert w.test_end == dates[dates.year == w.year][-1]
        assert dates.get_loc(w.test_start) - dates.get_loc(w.train_end) == 21   # 20 sessions skipped
    assert wins[-1].test_end == dates[-1]                                          # partial last year


def test_yearly_returns_chain_to_the_total():
    curve = pd.Series(np.linspace(100, 200, 600), index=pd.bdate_range("2020-03-02", periods=600))
    yr = walkforward.yearly_returns(curve)
    assert list(yr.index) == [2020, 2021, 2022]
    assert (1 + yr).prod() == pytest.approx(2.0)
    assert yr.iloc[0] == pytest.approx(curve[curve.index.year == 2020].iloc[-1] / 100 - 1)


def test_trade_stats():
    s = walkforward.trade_stats(pd.Series([0.1, -0.05, 0.05, -0.05]))
    assert s == {"trades": 4, "profit_factor": pytest.approx(1.5), "avg_trade": pytest.approx(0.0125),
                 "win_rate": 0.5}
    assert walkforward.trade_stats(pd.Series(dtype=float))["trades"] == 0


def test_walk_forward_picks_on_training_trades_only():
    data = {s: random_walk(1500, seed=i, start="2018-01-01") for i, s in enumerate("ABCD")}
    dates = data["A"].index
    grid = sweep.GRID | {"rule": ["score:0.4"], "regime": [False], "rs": [False], "sector": [False],
                         "stop_atr": [1.5, 3.0], "target_atr": [2.0, None], "trail_atr": [None], "max_hold": [20]}
    wins = walkforward.windows(dates[200:], min_train_years=2)
    rows, pooled = sweep.walk_forward(data, None, 2, "profit_factor", 5, wins, grid)
    assert list(rows.year) == [w.year for w in wins] and len(wins) >= 2

    # recompute each combo's trades directly and check the choice and the test slice
    entries = sweep._entries(data, None, 2, grid)
    for w, r in zip(wins, rows.to_dict("records")):
        best, best_pf, best_test = None, -np.inf, None
        for c in sweep.combos(grid):
            p = engine.Params(stop_atr=c["stop_atr"], target_atr=c["target_atr"], max_hold=c["max_hold"])
            t = pd.concat([engine.run(ind, entries[(c["rule"], False, False, False)][s], p).trades
                           for s, ind in data.items()])
            train = t[(t.entry_date >= w.train_start) & (t.exit_date <= w.train_end)]
            pf = walkforward.trade_stats(train.ret)["profit_factor"]
            if len(train) >= 5 and pf > best_pf:
                best, best_pf = c, pf
                best_test = t[(t.entry_date >= w.test_start) & (t.entry_date <= w.test_end)]
        assert (r["stop_atr"], r["target_atr"]) == (best["stop_atr"], best["target_atr"])
        assert r["test_trades"] == len(best_test)
    assert pooled["picked"]["trades"] == rows.test_trades.sum()
    assert "base_trades" not in rows  # baseline (target 3.0) isn't in this grid
