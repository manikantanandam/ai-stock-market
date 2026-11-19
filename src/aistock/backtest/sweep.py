"""Grid search over entry rules, market filter and exits on a watchlist.

Every combo is ranked on IN-SAMPLE pooled results only; the top few are then
run once on the out-of-sample segment. With hundreds of combos the best
in-sample result is partly luck, so expect it to shrink out-of-sample: trust
combos whose OOS numbers hold up, and neighbours that score similarly.
"""
from datetime import date
from itertools import product
from pathlib import Path

import pandas as pd

from aistock.backtest import engine, universe

GRID = {
    "rule": ["score:0.4", "score:0.6", "pullback", "breakout"],
    "regime": [False, True],
    "stop_atr": [1.5, 2.0, 3.0],
    "target_atr": [2.0, 3.0, 5.0, None],
    "trail_atr": [None, 3.0],
    "max_hold": [20, 40, 60, 90],
}
BASELINE = {"rule": "score:0.4", "regime": False, "stop_atr": 2.0,
            "target_atr": 3.0, "trail_atr": None, "max_hold": 20}
RANK_BY = ("profit_factor", "avg_trade", "median_cagr", "median_sharpe")


def combos() -> list[dict]:
    return [dict(zip(GRID, values)) for values in product(*GRID.values())]


def _params(row: dict) -> dict:
    """Grid params from a results row; pandas turns None into NaN, so map it back."""
    return {k: (None if not isinstance(row[k], str) and pd.isna(row[k]) else row[k]) for k in GRID}


def run(data: dict[str, pd.DataFrame], market: pd.Series, oos: float, rank_by: str,
        min_trades: int, top: int, progress=iter) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (all combos with in-sample stats, top + baseline with IS and OOS stats)."""
    entries = {(rule, reg): universe.build_entries(data, rule, market if reg else None)
               for rule, reg in product(GRID["rule"], GRID["regime"])}

    def evaluate(c: dict, segment: str) -> dict:
        p = engine.Params(stop_atr=c["stop_atr"], target_atr=c["target_atr"],
                          trail_atr=c["trail_atr"], max_hold=c["max_hold"])
        stats, trades = universe.run_universe(data, entries[(c["rule"], c["regime"])], p, oos, only=(segment,))
        return universe.pooled(stats, trades, segment)

    rows = [{**c, **evaluate(c, "In-sample")} for c in progress(combos())]
    results = pd.DataFrame(rows)
    eligible = results[results.trades >= min_trades].sort_values(rank_by, ascending=False)

    picks = [_params(r) | {"_row": r} for r in eligible.head(top).to_dict("records")]
    if not any(_params(p["_row"]) == BASELINE for p in picks):
        base = next(r for r in results.to_dict("records") if _params(r) == BASELINE)
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


def save(results: pd.DataFrame, name: str) -> Path:
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"sweep_{name}_{date.today()}.csv"
    results.to_csv(path, index=False)
    return path
