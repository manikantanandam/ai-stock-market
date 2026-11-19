"""Entry rules: each maps an add_indicators() frame to a bool Series that is
True on bars where a long entry is allowed (filled at the next bar's open)."""
from typing import Callable

import pandas as pd

from aistock.technical.indicators import score_series

Rule = Callable[[pd.DataFrame], pd.Series]


def _uptrend(ind: pd.DataFrame) -> pd.Series:
    return (ind.Close > ind.sma50) & (ind.sma50 > ind.sma200)


def pullback(ind: pd.DataFrame) -> pd.Series:
    """Dip inside an established uptrend: trend intact, RSI cooled below 40."""
    return _uptrend(ind) & (ind.rsi14 < 40)


def breakout(ind: pd.DataFrame) -> pd.Series:
    """Close above the prior 20-day high on 1.5x volume, with SMA50 > SMA200."""
    prior_high = ind.High.shift(1).rolling(20).max()
    return (ind.Close > prior_high) & (ind.sma50 > ind.sma200) & (ind.Volume > 1.5 * ind.vol_avg20)


def build(name: str) -> Rule:
    """'score:0.4' (technical score >= 0.4), 'pullback' or 'breakout'."""
    if name.startswith("score:"):
        threshold = float(name.split(":", 1)[1])
        return lambda ind: score_series(ind) >= threshold
    return {"pullback": pullback, "breakout": breakout}[name]


def market_regime(index_ind: pd.DataFrame) -> pd.Series:
    """True when the index (e.g. NIFTY 50) closes above its 200-day SMA."""
    return index_ind.Close > index_ind.sma200


def with_regime(entries: pd.Series, regime: pd.Series | None) -> pd.Series:
    if regime is None:
        return entries
    aligned = regime.astype(float).reindex(entries.index).ffill().fillna(0.0).astype(bool)
    return entries & aligned
