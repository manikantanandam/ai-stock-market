"""Named strategy presets: an entry rule, optional market filter, and exits.

The technical part (rule, regime, exits) is what the backtester tests. The
live gates (min_combined, min_fundamental, news veto) are applied only in
scans, because there is no free history of fundamentals or headlines. They
are untested priors.
"""
from dataclasses import dataclass

from aistock.backtest.engine import Params


@dataclass(frozen=True)
class Strategy:
    name: str
    description: str
    rule: str                     # see technical.rules.build
    regime: bool                  # only enter when NIFTY 50 > its SMA200
    stop_atr: float
    target_atr: float | None      # None = no fixed target
    trail_atr: float | None = None
    max_hold: int = 20
    min_combined: float | None = None     # live gate on the weighted combined score
    min_fundamental: float | None = None  # live gate on the fundamental score

    def params(self, **overrides) -> Params:
        """Backtest params, with CLI overrides (None means keep the preset)."""
        values = {"stop_atr": self.stop_atr, "target_atr": self.target_atr,
                  "trail_atr": self.trail_atr, "max_hold": self.max_hold}
        values.update({k: v for k, v in overrides.items() if v is not None})
        # 0 on the CLI means "off" for the optional exits
        return Params(stop_atr=values["stop_atr"], target_atr=values["target_atr"] or None,
                      trail_atr=values["trail_atr"] or None, max_hold=values["max_hold"])

    def describe(self) -> str:
        exits = [f"stop {self.stop_atr:g} ATR",
                 f"target {self.target_atr:g} ATR" if self.target_atr else "no target"]
        if self.trail_atr:
            exits.append(f"trail {self.trail_atr:g} ATR")
        exits.append(f"max {self.max_hold} days")
        entry = self.rule + (" + market filter" if self.regime else "")
        return f"{entry}; {', '.join(exits)}"


PRESETS = {s.name: s for s in [
    Strategy("swing", "Original combined signal: technical score >= 0.4 and combined score >= 0.4",
             "score:0.4", regime=False, stop_atr=2.0, target_atr=3.0, max_hold=20, min_combined=0.4),
    Strategy("trend", "Best sweep result: ride uptrends with a wide stop and no target",
             "score:0.4", regime=True, stop_atr=3.0, target_atr=None, max_hold=90, min_fundamental=0.0),
    Strategy("breakout", "Close above the 20-day high on 1.5x volume, in an uptrend",
             "breakout", regime=True, stop_atr=3.0, target_atr=None, max_hold=90, min_fundamental=0.0),
    Strategy("pullback", "Dip (RSI < 40) inside an established uptrend",
             "pullback", regime=False, stop_atr=2.0, target_atr=3.0, max_hold=20, min_fundamental=0.0),
    Strategy("ema-cross", "EMA100 crosses above EMA200 (slow golden cross)",
             "ema_cross", regime=False, stop_atr=3.0, target_atr=None, max_hold=90, min_fundamental=0.0),
]}
DEFAULT = "swing"


def get(name: str) -> Strategy:
    if name not in PRESETS:
        raise ValueError(f"unknown strategy {name!r}; choose from {', '.join(PRESETS)}")
    return PRESETS[name]
