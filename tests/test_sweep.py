from aistock.backtest import sweep, universe
from aistock.data import sectors
from conftest import random_walk


def test_pinned_fixes_only_given_dimensions():
    grid = sweep.pinned(regime=None, rs=True, sector=False)
    assert grid["rs"] == [True] and grid["sector"] == [False]
    assert grid["regime"] == sweep.GRID["regime"]
    assert len(sweep.combos(grid)) == len(sweep.combos()) // 4


def test_run_sweeps_filters_and_skips_missing_baseline(monkeypatch):
    ind = random_walk(500, seed=2, drift=0.002)
    monkeypatch.setattr(universe, "get_prices", lambda s, y: ind.assign(Close=100.0))
    monkeypatch.setattr(sectors, "INDUSTRY", {"A": "IT"})
    monkeypatch.setattr(sectors, "index", lambda industry, years: ind.Close)
    grid = sweep.GRID | {"rule": ["score:0.4"], "regime": [False], "rs": [True], "sector": [False, True],
                         "stop_atr": [2.0], "target_atr": [3.0], "trail_atr": [None], "max_hold": [20]}
    results, final = sweep.run({"A": ind}, ind.Close > 0, 0.3, "profit_factor", 0, 5, 2, grid)
    assert len(results) == 2 and set(results.sector) == {False, True}
    assert not final.baseline.any()             # baseline has rs off, which this grid excludes
