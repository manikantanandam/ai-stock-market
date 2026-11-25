"""Gradient-boosted ranker, trained and evaluated walk-forward.

The model predicts each stock's cross-sectional rank of the forward excess
return on a date (0 = median), not the raw return: ranks ignore the label's
skew and market-wide level, which is all a ranker needs. A fixed seed and no
early stopping keep it deterministic. Training uses every STEP-th date, since
20-session labels on neighbouring dates overlap heavily.

Evaluation per test year (each model trained only on earlier years, see
backtest.walkforward): mean daily rank IC of the predictions, the share of days
with IC > 0, and the excess 20-session return of the top / bottom decile over
the average stock, all next to the current ranking (technical score, then
126-day momentum) as the baseline to beat.
"""
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from aistock.backtest.walkforward import Window
from aistock.ml.features import FEATURES, daily_ic

STEP = 5
GO_IC = 0.03  # minimum mean rank IC worth taking to a portfolio test


def make_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(max_iter=300, learning_rate=0.05, max_leaf_nodes=31,
                                         min_samples_leaf=500, l2_regularization=1.0,
                                         early_stopping=False, random_state=0)


def target(df: pd.DataFrame) -> pd.Series:
    """Per-date percentile rank of the label, centred on 0."""
    return df.label.groupby(level="date").rank(pct=True) - 0.5


def fit(train: pd.DataFrame, step: int = STEP) -> HistGradientBoostingRegressor:
    d = train.dropna(subset=["label"])
    dates = d.index.get_level_values("date")
    d = d[dates.isin(dates.unique()[::step])]
    return make_model().fit(d[FEATURES], target(d))


def baseline(df: pd.DataFrame) -> pd.Series:
    """Today's portfolio ranking: technical score, ties broken by 126-day momentum."""
    return df.tech_score + 0.01 * df.ret_126.clip(-1, 1).fillna(-1)


def deciles(df: pd.DataFrame, col: str) -> tuple[float, float]:
    """Mean label of the top and bottom 10% by `col`, minus the day's average, over all dates."""
    pct = df[col].groupby(level="date").rank(pct=True)
    day_mean = df.label.groupby(level="date").transform("mean")
    excess = df.label - day_mean
    by_day = lambda mask: excess[mask].groupby(level="date").mean().mean()
    return float(by_day(pct > 0.9)), float(by_day(pct <= 0.1))


def evaluate(test: pd.DataFrame) -> dict:
    """IC and decile stats of `pred` and the baseline on labelled test rows."""
    t = test.dropna(subset=["label"]).assign(base=lambda d: baseline(d))
    ic = daily_ic(t, ["pred", "base"])
    top, bottom = deciles(t, "pred")
    base_top, _ = deciles(t, "base")
    return {"rows": len(t), "ic": float(ic.pred.mean()), "ic_pos": float((ic.pred > 0).mean()),
            "ic_base": float(ic.base.mean()), "top": top, "bottom": bottom, "top_base": base_top}


def walk_forward(df: pd.DataFrame, wins: list[Window], step: int = STEP,
                 progress=iter) -> tuple[pd.DataFrame, pd.Series, HistGradientBoostingRegressor, pd.DataFrame]:
    """Returns (one row per test year, out-of-sample predictions for every test
    row, the last window's model, and its test rows) for importance."""
    dates = df.index.get_level_values("date")
    rows, preds = [], []
    model = test = None
    for w in progress(wins):
        train = df[dates <= w.train_end]
        test = df[(dates >= w.test_start) & (dates <= w.test_end)]
        model = fit(train, step)
        test = test.assign(pred=model.predict(test[FEATURES]))
        preds.append(test.pred)
        rows.append({"year": w.year, "train_rows": int(train.label.notna().sum()), **evaluate(test)})
    return pd.DataFrame(rows), pd.concat(preds), model, test


def importance(model: HistGradientBoostingRegressor, test: pd.DataFrame, seed: int = 0) -> pd.Series:
    """Drop in mean daily IC when each feature is shuffled across the test rows (bigger = more useful)."""
    t = test.dropna(subset=["label"])
    rng = np.random.default_rng(seed)
    base_ic = daily_ic(t.assign(pred=model.predict(t[FEATURES])), ["pred"]).pred.mean()
    out = {}
    for f in FEATURES:
        x = t[FEATURES].copy()
        x[f] = rng.permutation(x[f].to_numpy())
        out[f] = base_ic - daily_ic(t.assign(pred=model.predict(x)), ["pred"]).pred.mean()
    return pd.Series(out).sort_values(ascending=False)


def verdict(rows: pd.DataFrame) -> tuple[bool, str]:
    """Go to the portfolio test only if the edge is big enough, consistent, and beats the baseline."""
    ic, pos_years = rows.ic.mean(), (rows.ic > 0).mean()
    beats = (rows.ic > rows.ic_base).mean()
    ok = ic >= GO_IC and pos_years >= 2 / 3 and beats >= 0.5
    return ok, (f"mean IC {ic:+.3f} (need >= {GO_IC}), positive in {pos_years:.0%} of years (need >= 67%), "
                f"beats baseline in {beats:.0%} (need >= 50%)")
