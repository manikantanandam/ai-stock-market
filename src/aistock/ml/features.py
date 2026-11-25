"""Features and labels for the ML ranker: one row per (date, symbol).

Every feature at bar t uses only data up to t's close (tests truncate the
history and check nothing changes). Features are ratios, so stocks at any
price level are comparable. The label is the forward excess return: buy at
t+1's open, sell at t+HORIZON's close, minus NIFTY 50 over the same span,
which is what a ranker should predict for a swing entry at the next open.
The model only ranks candidates; strategy rules still decide entries.
"""
import numpy as np
import pandas as pd

from aistock.technical import rules
from aistock.technical.indicators import score_series

HORIZON = 20  # sessions held for the label
FEATURES = [
    "ret_5", "ret_21", "ret_63", "ret_126", "rs_63", "rs_126",
    "dist_sma50", "dist_sma200", "sma50_slope", "sma200_slope",
    "rsi14", "macd_hist", "atr_pct", "vol_ratio", "dist_high20",
    "sector_trend", "sector_rs_63", "market",
]


def features(ind: pd.DataFrame, index: pd.DataFrame, sector_close: pd.Series | None) -> pd.DataFrame:
    """ind, index: add_indicators() frames of the stock and NIFTY 50; sector_close:
    the stock's sector index (None if unknown, giving NaN sector features)."""
    c = ind.Close
    idx = index.Close.reindex(ind.index).ffill()
    f = pd.DataFrame(index=ind.index)
    for n in (5, 21, 63, 126):
        f[f"ret_{n}"] = c.pct_change(n, fill_method=None)
    for n in (63, 126):
        f[f"rs_{n}"] = f[f"ret_{n}"] - idx.pct_change(n, fill_method=None)
    f["dist_sma50"] = c / ind.sma50 - 1
    f["dist_sma200"] = c / ind.sma200 - 1
    f["sma50_slope"] = ind.sma50.pct_change(20, fill_method=None)
    f["sma200_slope"] = ind.sma200.pct_change(20, fill_method=None)
    f["rsi14"] = ind.rsi14 / 100
    f["macd_hist"] = ind.macd_hist / c
    f["atr_pct"] = ind.atr14 / c
    f["vol_ratio"] = ind.Volume / ind.vol_avg20
    f["dist_high20"] = c / ind.High.rolling(20).max() - 1
    if sector_close is None:
        f["sector_trend"] = f["sector_rs_63"] = np.nan
    else:
        sec = sector_close.reindex(ind.index).ffill()
        f["sector_trend"] = sec / sector_close.rolling(50).mean().reindex(ind.index).ffill() - 1
        f["sector_rs_63"] = sec.pct_change(63, fill_method=None) - idx.pct_change(63, fill_method=None)
    f["market"] = rules.market_regime(index).astype(float).reindex(ind.index).ffill()
    return f.replace([np.inf, -np.inf], np.nan)[FEATURES].astype("float32")


def label(ind: pd.DataFrame, index: pd.DataFrame, horizon: int = HORIZON) -> pd.Series:
    """Forward excess return from t+1's open to t+horizon's close, vs NIFTY 50 (NaN at the end)."""
    idx = index.reindex(ind.index).ffill()
    stock = ind.Close.shift(-horizon) / ind.Open.shift(-1) - 1
    nifty = idx.Close.shift(-horizon) / idx.Open.shift(-1) - 1
    return (stock - nifty).astype("float32").rename("label")


def dataset(data: dict[str, pd.DataFrame], index: pd.DataFrame,
            sector_of: dict[str, pd.Series | None], progress=iter) -> pd.DataFrame:
    """Long panel indexed by (date, symbol) with FEATURES, `label`, and
    `tech_score` (today's portfolio ranking, kept as the baseline to beat, not a
    feature), from the SMA200 warm-up on. sector_of: symbol -> sector index close (or None)."""
    parts = []
    for sym in progress(list(data)):
        ind = data[sym]
        f = features(ind, index, sector_of.get(sym)).assign(
            label=label(ind, index), tech_score=score_series(ind).astype("float32"), symbol=sym)
        parts.append(f[ind.sma200.notna()])
    df = pd.concat(parts)
    df.index.name = "date"
    return df.set_index("symbol", append=True).sort_index()


def rank_ic(df: pd.DataFrame, cols: list[str] = FEATURES) -> pd.DataFrame:
    """Mean per-date Spearman correlation of each column with the label, by year:
    a quick read of which features carry signal before any model is trained."""
    ic = daily_ic(df, cols)
    return ic.groupby(ic.index.year).mean()


def daily_ic(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Per-date Spearman correlation of each column with the label (date x column)."""
    d = df.dropna(subset=["label"])
    ranks = d[cols + ["label"]].groupby(level="date").rank(pct=True)
    ranks = ranks.sub(ranks.groupby(level="date").transform("mean"))
    num = ranks[cols].mul(ranks.label, axis=0).groupby(level="date").sum()
    sq = (ranks ** 2).groupby(level="date").sum()
    return num.div(np.sqrt(sq[cols].mul(sq.label, axis=0)))
