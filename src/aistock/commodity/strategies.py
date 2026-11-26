"""Strategy presets for commodity futures: each has a long and a short rule.

Same Strategy type as the stock presets, without the NIFTY, RS and sector
filters or fundamental gates. trend-long keeps only the long side, so
`aistock commodity compare` shows whether the shorts add anything. All are
untested priors until that out-of-sample comparison says otherwise.
"""
from aistock.strategy.strategies import Strategy

PRESETS = {s.name: s for s in [
    Strategy("trend", "Ride trends both ways: technical score >= 0.4 long, <= -0.4 short",
             "score:0.4", short_rule="score_below:-0.4", regime=False, stop_atr=3.0, target_atr=None, max_hold=90),
    Strategy("trend-long", "trend, long side only (as for stocks)",
             "score:0.4", regime=False, stop_atr=3.0, target_atr=None, max_hold=90),
    Strategy("channel", "Close beyond the prior 20-day high (long) or low (short), with the SMA50/200 trend",
             "channel", short_rule="channel_short", regime=False, stop_atr=3.0, target_atr=None, max_hold=90),
    Strategy("pullback", "Dip (RSI < 40) in an uptrend long, rally (RSI > 60) in a downtrend short",
             "pullback", short_rule="rally", regime=False, stop_atr=2.0, target_atr=3.0, max_hold=20),
    Strategy("ema-cross", "EMA100 crossing EMA200, either way",
             "ema_cross", short_rule="ema_cross_down", regime=False, stop_atr=3.0, target_atr=None, max_hold=90),
]}
DEFAULT = "trend"


def get(name: str) -> Strategy:
    if name not in PRESETS:
        raise ValueError(f"unknown commodity strategy {name!r}; choose from {', '.join(PRESETS)}")
    return PRESETS[name]
