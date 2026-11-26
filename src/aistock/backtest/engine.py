"""Single-stock (or single-contract), bar-by-bar backtest on daily candles.

Rules: when entries[t] is True and flat, buy at bar t+1 open.
Stop = entry - stop_atr * ATR(t); target = entry + target_atr * ATR(t) (None = no target).
With trail_atr, the stop ratchets up to (highest high since entry - trail_atr * ATR(t)).
If a bar hits both stop and target, the stop is assumed (conservative).
Gaps fill at the open. Positions close at the bar's close after max_hold bars.

Futures can also go short: when shorts[t] is True and flat, sell at bar t+1
open, with every level mirrored (stop above, target below, trail from the
lowest low). A short is marked as negative units, so equity = cash + units *
close. If both fire on one bar, the long wins.
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from aistock.backtest.costs import CostModel


@dataclass
class Params:
    stop_atr: float = 2.0
    target_atr: float | None = 3.0
    trail_atr: float | None = None
    max_hold: int = 20
    capital: float = 100_000.0
    costs: CostModel = field(default_factory=CostModel)
    fractional: bool = False  # trade fractional units (futures priced above the capital)


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.Series
    stats: dict


def run(ind: pd.DataFrame, entries: pd.Series, p: Params = Params(), shorts: pd.Series | None = None) -> Result:
    """ind: add_indicators() frame; entries (and optional shorts): bool per bar aligned to ind."""
    o, h, l, c = (ind[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    atr, ent = ind.atr14.to_numpy(), entries.fillna(False).to_numpy(dtype=bool)
    sht = np.zeros(len(ind), dtype=bool) if shorts is None else shorts.fillna(False).to_numpy(dtype=bool)
    cash, units, side, equity, trades = p.capital, 0, 0, [], []
    entry_i = stop = target = entry_px = entry_atr = extreme = None

    def close_position(i, px, reason):
        nonlocal cash, units
        if side > 0:
            fill = px * (1 - p.costs.slippage)
            value = units * fill
            cash += value - p.costs.sell_cost(value)
            pnl = value - p.costs.sell_cost(value) - basis
        else:  # buy back a short
            fill = px * (1 + p.costs.slippage)
            value = units * fill
            cash -= value + p.costs.buy_cost(value)
            pnl = basis - value - p.costs.buy_cost(value)
        # ret is on the money invested (incl. entry costs), matching portfolio.run;
        # cash left over from rounding to whole shares doesn't dilute it
        trades.append({"entry_date": ind.index[entry_i], "exit_date": ind.index[i],
                       "entry": entry_px, "exit": fill, "bars": i - entry_i + 1,
                       "reason": reason, "pnl": pnl, "ret": pnl / (basis if side > 0 else units * entry_px),
                       "side": "long" if side > 0 else "short", "stop": stop,
                       "target": np.nan if np.isinf(target) else target})
        units = 0

    for i in range(len(ind)):
        if units and side > 0:
            if l[i] <= stop:
                close_position(i, min(o[i], stop), "stop")
            elif h[i] >= target:
                close_position(i, max(o[i], target), "target")
            elif i - entry_i + 1 >= p.max_hold:
                close_position(i, c[i], "time")
            elif p.trail_atr:
                extreme = max(extreme, h[i])
                stop = max(stop, extreme - p.trail_atr * entry_atr)
        elif units:
            if h[i] >= stop:
                close_position(i, max(o[i], stop), "stop")
            elif l[i] <= target:
                close_position(i, min(o[i], target), "target")
            elif i - entry_i + 1 >= p.max_hold:
                close_position(i, c[i], "time")
            elif p.trail_atr:
                extreme = min(extreme, l[i])
                stop = min(stop, extreme + p.trail_atr * entry_atr)
        elif i > 0 and (ent[i - 1] or sht[i - 1]) and not np.isnan(atr[i - 1]):
            side = 1 if ent[i - 1] else -1
            entry_px = o[i] * (1 + side * p.costs.slippage)
            n = cash / (entry_px * (1 + 0.0012))  # leave room for entry costs
            n = n if p.fractional else int(n)
            if n > 0:
                value = n * entry_px
                if side > 0:
                    basis = value + p.costs.buy_cost(value)
                    cash -= basis
                else:  # short proceeds sit in cash; the position is marked as -units
                    basis = value - p.costs.sell_cost(value)
                    cash += basis
                units, entry_i, entry_atr = n, i, atr[i - 1]
                extreme = h[i] if side > 0 else l[i]
                stop = entry_px - side * p.stop_atr * entry_atr
                target = entry_px + side * p.target_atr * entry_atr if p.target_atr else side * np.inf
                if (l[i] <= stop) if side > 0 else (h[i] >= stop):  # stopped out on the entry bar
                    close_position(i, stop, "stop")
        equity.append(cash + side * units * c[i])

    if units:
        close_position(len(ind) - 1, c[-1], "end")
        equity[-1] = cash

    eq = pd.Series(equity, index=ind.index)
    tr = pd.DataFrame(trades)
    return Result(tr, eq, _stats(eq, tr, c, p.capital))


def _stats(eq: pd.Series, tr: pd.DataFrame, close: np.ndarray, capital: float) -> dict:
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    daily = eq.pct_change(fill_method=None).dropna()
    n = len(tr)
    return {
        "trades": n,
        "win_rate": float((tr.pnl > 0).mean()) if n else 0.0,
        "avg_trade_pct": float(tr.ret.mean()) if n else 0.0,
        "total_return": eq.iloc[-1] / capital - 1,
        "cagr": (eq.iloc[-1] / capital) ** (1 / years) - 1,
        "max_drawdown": float((eq / eq.cummax() - 1).min()),
        "sharpe": float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0,
        "exposure": float(tr.bars.sum() / len(eq)) if n else 0.0,
        "buy_hold": close[-1] / close[0] - 1,
        "buy_hold_cagr": (close[-1] / close[0]) ** (1 / years) - 1,
    }
