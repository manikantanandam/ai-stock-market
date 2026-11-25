"""Portfolio backtest: one pool of capital shared across a watchlist.

Each day, yesterday's entry signals are filled at today's open into the slots
free at yesterday's close; then positions opened earlier are checked for
exits on today's bar (same rules as engine.run: stop, target, trailing stop,
max hold, gap-aware fills). Cash from an intraday exit is only reusable the
next day, matching the single-stock engine. Each
new position gets 1/max_positions of current equity. When more stocks signal
than there are free slots, the best rank wins (rank_inputs: technical score
by default, or SMA200 slope), with 126-day momentum as the tie-break.

Benchmark: NIFTY 50 price index (^NSEI), which excludes dividends (~1-1.5%/yr),
so it slightly understates buy-and-hold.
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from aistock.backtest.engine import Params
from aistock.technical.indicators import score_series


@dataclass
class Position:
    sym: str
    shares: int
    entry_i: int
    entry_px: float
    cost_basis: float  # cash spent including buy costs
    stop: float
    target: float
    atr: float
    highest: float
    max_hold: int                 # exits are fixed at entry, so a later preset change
    trail_atr: float | None       # doesn't alter positions already open


RANKS = ("score", "slope")


def rank_inputs(ind: pd.DataFrame, rank_by: str = "score") -> tuple[pd.Series, pd.Series]:
    """(primary, tie-break) per bar for ranking candidates: the technical score
    ("score") or the 20-day change of SMA200 ("slope"), then 126-day momentum."""
    mom = ind.Close.pct_change(126, fill_method=None)
    if rank_by == "score":
        return score_series(ind), mom
    if rank_by == "slope":
        return ind.sma200.pct_change(20, fill_method=None), mom
    raise ValueError(f"unknown rank {rank_by!r}; choose from {', '.join(RANKS)}")


def rank(score: float, mom: float) -> tuple:
    """Sort key for candidates competing for slots: primary rank input, then momentum (NaN last)."""
    return np.nan_to_num(score, nan=-np.inf), np.nan_to_num(mom, nan=-1)


def buy(sym: str, entry_i: int, open_: float, high: float, atr: float, mark: float, cash: float,
        p: Params, max_positions: int) -> Position | None:
    """Fill at the open with 1/max_positions of equity (capped by cash); None if not even one share."""
    px = open_ * (1 + p.costs.slippage)
    budget = min(mark / max_positions, cash)
    n = int(budget / (px * (1 + 0.0012)))  # leave room for buy costs
    if n <= 0:
        return None
    stop = px - p.stop_atr * atr
    target = px + p.target_atr * atr if p.target_atr else np.inf
    return Position(sym, n, entry_i, px, n * px + p.costs.buy_cost(n * px), stop, target, atr, high,
                    p.max_hold, p.trail_atr)


def check_exit(pos: Position, o: float, h: float, l: float, c: float, held: int) -> tuple[float, str] | None:
    """(price, reason) if the position exits on this bar (gap-aware), else None.
    `held` counts bars including the entry bar. Ratchets the trailing stop."""
    if l <= pos.stop:
        return min(o, pos.stop), "stop"
    if h >= pos.target:
        return max(o, pos.target), "target"
    if held >= pos.max_hold:
        return c, "time"
    if pos.trail_atr:
        pos.highest = max(pos.highest, h)
        pos.stop = max(pos.stop, pos.highest - pos.trail_atr * pos.atr)
    return None


def sell(pos: Position, px: float, p: Params) -> tuple[float, float]:
    """(fill price after slippage, cash proceeds after selling costs)."""
    fill = px * (1 - p.costs.slippage)
    return fill, pos.shares * fill - p.costs.sell_cost(pos.shares * fill)


def _panel(data: dict[str, pd.DataFrame], dates: pd.DatetimeIndex, col: str) -> np.ndarray:
    return np.column_stack([data[s][col].reindex(dates).to_numpy(dtype=float) for s in data])


def run(data: dict[str, pd.DataFrame], entries: dict[str, pd.Series], dates: pd.DatetimeIndex,
        p: Params, max_positions: int, rank_by: str = "score") -> tuple[pd.Series, pd.Series, pd.DataFrame]:
    """Returns (daily equity, fraction of equity invested, trades).
    `dates` is the trading calendar (e.g. the index's)."""
    syms = list(data)
    o, h, l, c, atr = (_panel(data, dates, k) for k in ("Open", "High", "Low", "Close", "atr14"))
    ent = np.column_stack([entries[s].astype(bool).reindex(dates, fill_value=False).to_numpy() for s in syms])
    keys = [rank_inputs(data[s], rank_by) for s in syms]
    score = np.column_stack([k[0].reindex(dates).to_numpy(dtype=float) for k in keys])
    mom = np.column_stack([k[1].reindex(dates).to_numpy(dtype=float) for k in keys])
    last_close = pd.DataFrame(c).ffill().to_numpy()

    cash, positions, trades, equity, invested = p.capital, {}, [], [], []

    def close(j: int, i: int, px: float, reason: str):
        nonlocal cash
        pos = positions.pop(j)
        fill, proceeds = sell(pos, px, p)
        cash += proceeds
        trades.append({"symbol": pos.sym, "entry_date": dates[pos.entry_i], "exit_date": dates[i],
                       "entry": pos.entry_px, "exit": fill, "bars": i - pos.entry_i + 1, "reason": reason,
                       "pnl": proceeds - pos.cost_basis, "ret": proceeds / pos.cost_basis - 1})

    for i in range(len(dates)):
        # 1. entries at today's open from yesterday's signals, using only the
        #    slots and cash available before today's exits (those happen intraday)
        free = max_positions - len(positions)
        if i > 0 and free > 0:
            cand = [j for j in np.flatnonzero(ent[i - 1])
                    if j not in positions and not np.isnan(o[i, j]) and not np.isnan(atr[i - 1, j])]
            cand.sort(key=lambda j: rank(score[i - 1, j], mom[i - 1, j]), reverse=True)
            mark = cash + sum(pos.shares * last_close[i - 1, j] for j, pos in positions.items())
            for j in cand[:free]:
                pos = buy(syms[j], i, o[i, j], h[i, j], atr[i - 1, j], mark, cash, p, max_positions)
                if pos is None:
                    continue
                cash -= pos.cost_basis
                positions[j] = pos
                if l[i, j] <= pos.stop:  # stopped out on the entry bar
                    close(j, i, pos.stop, "stop")

        # 2. exits on today's bar for positions opened before today
        for j in [j for j, pos in positions.items() if pos.entry_i < i]:
            if np.isnan(o[i, j]):
                continue  # no bar today (suspension)
            hit = check_exit(positions[j], o[i, j], h[i, j], l[i, j], c[i, j], i - positions[j].entry_i + 1)
            if hit:
                close(j, i, *hit)

        held = sum(pos.shares * last_close[i, j] for j, pos in positions.items())
        equity.append(cash + held)
        invested.append(held / (cash + held))

    for j in list(positions):  # liquidate at the end
        close(j, len(dates) - 1, last_close[-1, j], "end")
    equity[-1] = cash
    return pd.Series(equity, index=dates), pd.Series(invested, index=dates), pd.DataFrame(trades)


def stats(eq: pd.Series, invested: pd.Series, trades: pd.DataFrame, bench: pd.Series,
          universe: pd.Series) -> dict:
    """Strategy vs the index (bench) and equal-weight watchlist (universe) over the same dates."""
    def curve(s: pd.Series) -> dict:
        years = max((s.index[-1] - s.index[0]).days / 365.25, 1e-9)
        daily = s.pct_change().dropna()
        return {"cagr": (s.iloc[-1] / s.iloc[0]) ** (1 / years) - 1,
                "max_drawdown": float((s / s.cummax() - 1).min()),
                "sharpe": float(daily.mean() / daily.std() * np.sqrt(252)) if daily.std() > 0 else 0.0}

    s, b, u = curve(eq), curve(bench.reindex(eq.index).ffill()), curve(universe.reindex(eq.index).ffill())
    n = len(trades)
    wins, losses = (trades.ret[trades.ret > 0].sum(), -trades.ret[trades.ret <= 0].sum()) if n else (0, 0)
    return {
        **s,
        "total_return": eq.iloc[-1] / eq.iloc[0] - 1,
        "trades": n,
        "trades_per_year": n / max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9),
        "win_rate": float((trades.ret > 0).mean()) if n else 0.0,
        "avg_trade": float(trades.ret.mean()) if n else 0.0,
        "profit_factor": wins / losses if losses else float("inf"),
        "avg_bars": float(trades.bars.mean()) if n else 0.0,
        "exposure": float(invested.mean()),
        "nifty_cagr": b["cagr"],
        "nifty_max_drawdown": b["max_drawdown"],
        "nifty_sharpe": b["sharpe"],
        "excess_cagr": s["cagr"] - b["cagr"],
        "universe_cagr": u["cagr"],
        "universe_max_drawdown": u["max_drawdown"],
        "excess_vs_universe": s["cagr"] - u["cagr"],
    }


def equal_weight(data: dict[str, pd.DataFrame], dates: pd.DatetimeIndex) -> pd.Series:
    """Daily-rebalanced equal-weight buy-and-hold of the watchlist (no costs).
    It shares the strategy's survivorship bias, so beating it is the fairer test."""
    close = pd.DataFrame({s: d.Close for s, d in data.items()}).reindex(dates)
    daily = close.pct_change(fill_method=None).mean(axis=1).fillna(0.0)
    return (1 + daily).cumprod()
