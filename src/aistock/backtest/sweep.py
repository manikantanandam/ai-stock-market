"""Grid search over entry rules, market / RS / sector filters and exits on a watchlist.

Every combo is ranked on IN-SAMPLE pooled results only; the top few are then
run once on the out-of-sample segment. With hundreds of combos the best
in-sample result is partly luck, so expect it to shrink out-of-sample: trust
combos whose OOS numbers hold up, and neighbours that score similarly.

walk_forward() is the stricter test: each year the combo is chosen on earlier
years only, so the pooled test-year results estimate what the selection
process itself is worth, not just the luckiest combo.
"""
from datetime import date
from itertools import product
from pathlib import Path

import pandas as pd

from aistock.backtest import engine, universe, walkforward

GRID = {
    "rule": ["score:0.4", "score:0.6", "pullback", "breakout", "ema_cross"],
    "regime": [False, True],
    "rs": [False, True],
    "sector": [False, True],
    "stop_atr": [1.5, 2.0, 3.0],
    "target_atr": [2.0, 3.0, 5.0, None],
    "trail_atr": [None, 3.0],
    "max_hold": [20, 40, 60, 90],
}
BASELINE = {"rule": "score:0.4", "regime": False, "rs": False, "sector": False, "stop_atr": 2.0,
            "target_atr": 3.0, "trail_atr": None, "max_hold": 20}
RANK_BY = ("profit_factor", "avg_trade", "median_cagr", "median_sharpe")
WF_RANK_BY = ("profit_factor", "avg_trade")  # trade-based, so they can be cut to any window


def pinned(**fixed) -> dict[str, list]:
    """GRID with some dimensions fixed to one value (None = keep sweeping it)."""
    return GRID | {k: [v] for k, v in fixed.items() if v is not None}


def combos(grid: dict[str, list] = GRID) -> list[dict]:
    return [dict(zip(grid, values)) for values in product(*grid.values())]


def _params(row: dict) -> dict:
    """Grid params from a results row; pandas turns None into NaN, so map it back."""
    return {k: (None if not isinstance(row[k], str) and pd.isna(row[k]) else row[k]) for k in GRID}


def run(data: dict[str, pd.DataFrame], market: pd.Series, oos: float, rank_by: str,
        min_trades: int, top: int, years: int, grid: dict[str, list] = GRID,
        progress=iter) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (all combos with in-sample stats, top + baseline with IS and OOS stats).
    The baseline is left out when the grid excludes it (e.g. RS pinned on)."""
    entries = _entries(data, market, years, grid)

    def evaluate(c: dict, segment: str) -> dict:
        p = engine.Params(stop_atr=c["stop_atr"], target_atr=c["target_atr"],
                          trail_atr=c["trail_atr"], max_hold=c["max_hold"])
        key = (c["rule"], c["regime"], c["rs"], c["sector"])
        stats, trades = universe.run_universe(data, entries[key], p, oos, only=(segment,))
        return universe.pooled(stats, trades, segment)

    rows = [{**c, **evaluate(c, "In-sample")} for c in progress(combos(grid))]
    results = pd.DataFrame(rows)
    eligible = results[results.trades >= min_trades].sort_values(rank_by, ascending=False)

    picks = [_params(r) | {"_row": r} for r in eligible.head(top).to_dict("records")]
    if not any(_params(p["_row"]) == BASELINE for p in picks):
        base = next((r for r in results.to_dict("records") if _params(r) == BASELINE), None)
        if base is not None:
            picks.append(_params(base) | {"_row": base})

    final = []
    for pick in picks:
        r = pick.pop("_row")
        c = pick
        oos_stats = evaluate(c, "Out-of-sample")
        final.append({**c, "baseline": c == BASELINE,
                      **{f"is_{k}": v for k, v in r.items() if k not in GRID},
                      **{f"oos_{k}": v for k, v in oos_stats.items()}})
    return results, pd.DataFrame(final)


def _entries(data: dict[str, pd.DataFrame], market: pd.Series, years: int,
             grid: dict[str, list]) -> dict[tuple, dict[str, pd.Series]]:
    """Entry signals per (rule, regime, rs, sector), shared by all exit settings."""
    filters = {(rs, sec): universe.filters(data, rs, sec, years) for rs, sec in product(grid["rs"], grid["sector"])}
    return {(rule, reg, rs, sec): universe.build_entries(data, rule, market if reg else None, filters[(rs, sec)])
            for rule, reg, rs, sec in product(grid["rule"], grid["regime"], grid["rs"], grid["sector"])}


def walk_forward(data: dict[str, pd.DataFrame], market: pd.Series, years: int, rank_by: str, min_trades: int,
                 wins: list[walkforward.Window], grid: dict[str, list] = GRID,
                 progress=iter) -> tuple[pd.DataFrame, dict]:
    """Returns (one row per test year, pooled test-year stats {"picked": ..., "baseline": ...}).
    Per test year: the combo with the best `rank_by` on its training
    window (only trades closed by train_end, at least `min_trades`), and that
    combo's trades entered in the test year, next to the baseline's.
    Each combo runs once over the full history; windows just slice its trades."""
    entries = _entries(data, market, years, grid)
    cs = combos(grid)
    trades = []
    for c in progress(cs):
        p = engine.Params(stop_atr=c["stop_atr"], target_atr=c["target_atr"],
                          trail_atr=c["trail_atr"], max_hold=c["max_hold"])
        key = (c["rule"], c["regime"], c["rs"], c["sector"])
        t = [engine.run(ind, entries[key][sym], p).trades for sym, ind in data.items()]
        t = [x[["entry_date", "exit_date", "ret"]] for x in t if len(x)]
        trades.append(pd.concat(t, ignore_index=True) if t else
                      pd.DataFrame({"entry_date": pd.to_datetime([]), "exit_date": pd.to_datetime([]), "ret": []}))
    base = next((k for k, c in enumerate(cs) if c == BASELINE), None)

    def test_year(k: int, w: walkforward.Window) -> pd.Series:
        t = trades[k]
        return t.ret[(t.entry_date >= w.test_start) & (t.entry_date <= w.test_end)]

    rows, picked, baseline = [], [], []
    for w in wins:
        best, best_stats = None, None
        for k, t in enumerate(trades):
            s = walkforward.trade_stats(t.ret[(t.entry_date >= w.train_start) & (t.exit_date <= w.train_end)])
            if s["trades"] >= min_trades and (best is None or s[rank_by] > best_stats[rank_by]):
                best, best_stats = k, s
        row = {"year": w.year, "train_end": f"{w.train_end:%Y-%m-%d}"}
        if best is not None:
            row |= cs[best] | {f"train_{k}": v for k, v in best_stats.items()}
            picked.append(test_year(best, w))
            row |= {f"test_{k}": v for k, v in walkforward.trade_stats(picked[-1]).items()}
        if base is not None:
            baseline.append(test_year(base, w))
            row |= {f"base_{k}": v for k, v in walkforward.trade_stats(baseline[-1]).items()}
        rows.append(row)
    pool = lambda rets: walkforward.trade_stats(pd.concat(rets) if rets else pd.Series(dtype=float))
    return pd.DataFrame(rows), {"picked": pool(picked), "baseline": pool(baseline)}


def save(results: pd.DataFrame, name: str) -> Path:
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"sweep_{name}_{date.today()}.csv"
    results.to_csv(path, index=False)
    return path
