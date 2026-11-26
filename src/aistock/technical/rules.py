"""Entry rules: each maps an add_indicators() frame to a bool Series that is
True on bars where an entry is allowed (filled at the next bar's open).
Long rules are used for stocks and futures; the short rules (mirror images)
only for futures, which can be sold first."""
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


def ema_cross(ind: pd.DataFrame) -> pd.Series:
    """EMA100 crosses above EMA200 on this bar (a slow 'golden cross')."""
    above = ind.ema100 > ind.ema200
    return above & ~above.shift(1, fill_value=True)


def _downtrend(ind: pd.DataFrame) -> pd.Series:
    return (ind.Close < ind.sma50) & (ind.sma50 < ind.sma200)


def rally(ind: pd.DataFrame) -> pd.Series:
    """Short: bounce inside an established downtrend, RSI above 60."""
    return _downtrend(ind) & (ind.rsi14 > 60)


def breakdown(ind: pd.DataFrame) -> pd.Series:
    """Short: close below the prior 20-day low on 1.5x volume, with SMA50 < SMA200."""
    prior_low = ind.Low.shift(1).rolling(20).min()
    return (ind.Close < prior_low) & (ind.sma50 < ind.sma200) & (ind.Volume > 1.5 * ind.vol_avg20)


def ema_cross_down(ind: pd.DataFrame) -> pd.Series:
    """Short: EMA100 crosses below EMA200 on this bar (a slow 'death cross')."""
    below = ind.ema100 < ind.ema200
    return below & ~below.shift(1, fill_value=True)


def channel(ind: pd.DataFrame) -> pd.Series:
    """Close above the prior 20-day high with SMA50 > SMA200, no volume test
    (Yahoo's continuous-futures volume is unreliable)."""
    return (ind.Close > ind.High.shift(1).rolling(20).max()) & (ind.sma50 > ind.sma200)


def channel_short(ind: pd.DataFrame) -> pd.Series:
    """Short: close below the prior 20-day low with SMA50 < SMA200, no volume test."""
    return (ind.Close < ind.Low.shift(1).rolling(20).min()) & (ind.sma50 < ind.sma200)


RULES = {"pullback": pullback, "breakout": breakout, "ema_cross": ema_cross, "channel": channel}
SHORT_RULES = {"rally": rally, "breakdown": breakdown, "ema_cross_down": ema_cross_down,
               "channel_short": channel_short}


def build(name: str) -> Rule:
    """'score:0.4' (technical score >= 0.4), 'score_below:-0.4' (<= -0.4, for
    shorts), or a name in RULES or SHORT_RULES."""
    if name.startswith("score:"):
        threshold = float(name.split(":", 1)[1])
        return lambda ind: score_series(ind) >= threshold
    if name.startswith("score_below:"):
        threshold = float(name.split(":", 1)[1])
        return lambda ind: score_series(ind) <= threshold
    return RULES.get(name) or SHORT_RULES[name]


def market_regime(index_ind: pd.DataFrame) -> pd.Series:
    """True when the index (e.g. NIFTY 50) closes above its 200-day SMA."""
    return index_ind.Close > index_ind.sma200


RS_PERIODS = (63, 126)  # about 3 and 6 months of sessions


def outperforms(close: pd.Series, index_close: pd.Series) -> pd.Series:
    """True when the stock's return beat the index's over both ~3 and ~6 months."""
    idx = index_close.reindex(close.index).ffill()
    beats = [close.pct_change(n, fill_method=None) > idx.pct_change(n, fill_method=None) for n in RS_PERIODS]
    return beats[0] & beats[1]


def sector_trend(sector_close: pd.Series) -> pd.Series:
    """True when the sector index closes above its 50-day SMA."""
    return sector_close > sector_close.rolling(50).mean()


def with_regime(entries: pd.Series, regime: pd.Series | None) -> pd.Series:
    if regime is None:
        return entries
    aligned = regime.astype(float).reindex(entries.index).ffill().fillna(0.0).astype(bool)
    return entries & aligned
