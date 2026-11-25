"""Walk-forward validation: yearly test windows, each trained only on earlier data.

windows() gives expanding windows: train on everything before the test year,
minus a gap of `gap` sessions so nothing learned from training (a trade still
open, or a forward-return label) overlaps the test year; test on that calendar
year. It is used to pick sweep combos on the training window and score them on
the next year (sweep.walk_forward), and to train the ML ranker.

Fixed presets have nothing to train, so for them yearly_returns() breaks one
continuous portfolio run into calendar years: consistency across years is the
check a single in-sample/out-of-sample split can't give.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp

    @property
    def year(self) -> int:
        return self.test_start.year


def windows(dates: pd.DatetimeIndex, min_train_years: float = 3.0, gap: int = 20) -> list[Window]:
    """One window per calendar year in `dates` with at least `min_train_years` of
    history before it (the training window is that, minus the gap). `dates` should start where
    signals are valid (e.g. after the SMA200 warm-up)."""
    out = []
    for year in sorted(set(dates.year)):
        test = dates[dates.year == year]
        end_i = dates.get_loc(test[0]) - gap - 1
        if end_i < 0 or (test[0] - dates[0]).days < min_train_years * 365.25:
            continue
        out.append(Window(dates[0], dates[end_i], test[0], test[-1]))
    return out


def yearly_returns(curve: pd.Series) -> pd.Series:
    """Calendar-year returns of an equity or price curve; the first year runs from its first value."""
    ends = curve.groupby(curve.index.year).last()
    starts = ends.shift(1)
    starts.iloc[0] = curve.iloc[0]
    return ends / starts - 1


def trade_stats(ret: pd.Series) -> dict:
    """Pooled stats of per-trade returns (the sweep's ranking metrics)."""
    n = len(ret)
    if not n:
        return {"trades": 0, "profit_factor": np.nan, "avg_trade": np.nan, "win_rate": np.nan}
    wins, losses = ret[ret > 0].sum(), -ret[ret <= 0].sum()
    return {"trades": n, "profit_factor": wins / losses if losses else np.inf,
            "avg_trade": float(ret.mean()), "win_rate": float((ret > 0).mean())}
