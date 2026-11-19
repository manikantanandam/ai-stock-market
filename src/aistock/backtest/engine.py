"""Long-only, single-stock, bar-by-bar backtest on daily candles.

Rules: when entries[t] is True and flat, buy at bar t+1 open.
Stop = entry - stop_atr * ATR(t); target = entry + target_atr * ATR(t) (None = no target).
With trail_atr, the stop ratchets up to (highest high since entry - trail_atr * ATR(t)).
If a bar hits both stop and target, the stop is assumed (conservative).
Gaps fill at the open. Positions close at the bar's close after max_hold bars.
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


@dataclass
class Result:
    trades: pd.DataFrame
    equity: pd.Series
    stats: dict


def run(ind: pd.DataFrame, entries: pd.Series, p: Params = Params()) -> Result:
    """ind: add_indicators() frame; entries: bool per bar aligned to ind."""
    o, h, l, c = (ind[k].to_numpy() for k in ("Open", "High", "Low", "Close"))
    atr, ent = ind.atr14.to_numpy(), entries.fillna(False).to_numpy(dtype=bool)
    cash, shares, equity, trades = p.capital, 0, [], []
    entry_i = stop = target = entry_px = entry_atr = highest = None

    def close_position(i, px, reason):
        nonlocal cash, shares
        fill = px * (1 - p.costs.slippage)
        value = shares * fill
        proceeds = value - p.costs.sell_cost(value)
        cash += proceeds
        # ret is on the money invested (incl. buy costs), matching portfolio.run;
        # cash left over from rounding to whole shares doesn't dilute it
        trades.append({"entry_date": ind.index[entry_i], "exit_date": ind.index[i],
                       "entry": entry_px, "exit": fill, "bars": i - entry_i + 1,
                       "reason": reason, "pnl": proceeds - cost_basis,
                       "ret": proceeds / cost_basis - 1})
        shares = 0

    for i in range(len(ind)):
        if shares:
            if l[i] <= stop:
                close_position(i, min(o[i], stop), "stop")
            elif h[i] >= target:
                close_position(i, max(o[i], target), "target")
            elif i - entry_i + 1 >= p.max_hold:
                close_position(i, c[i], "time")
            elif p.trail_atr:
                highest = max(highest, h[i])
                stop = max(stop, highest - p.trail_atr * entry_atr)
        elif i > 0 and ent[i - 1] and not np.isnan(atr[i - 1]):
            entry_px = o[i] * (1 + p.costs.slippage)
            n = int(cash / (entry_px * (1 + 0.0012)))  # leave room for buy costs
            if n > 0:
                value = n * entry_px
                cost_basis = value + p.costs.buy_cost(value)
                cash -= cost_basis
                shares, entry_i, entry_atr, highest = n, i, atr[i - 1], h[i]
                stop = entry_px - p.stop_atr * entry_atr
                target = entry_px + p.target_atr * entry_atr if p.target_atr else np.inf
                if l[i] <= stop:  # stopped out on the entry bar
                    close_position(i, stop, "stop")
        equity.append(cash + shares * c[i])

    if shares:
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
