"""Backtest commodity presets (long and short) on each contract, with MCX futures costs.

Each contract is traded on its own with the full capital and fractional
units (no leverage, so returns are per unit of notional, not of margin).
History is split in-sample / out-of-sample like the stock backtests, and
results are pooled over contracts. The benchmark is buying and holding each
contract (in INR), which ignores roll costs, so it flatters the hold.
"""
from datetime import date
from pathlib import Path

import pandas as pd

from aistock.backtest import engine, universe
from aistock.commodity.signal import Config
from aistock.data import commodities
from aistock.strategy.strategies import Strategy
from aistock.technical import rules
from aistock.technical.indicators import add_indicators

REPORTS = universe.REPORTS


def load(symbols: list[str], years: int) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Indicator frames per contract, skipping ones that fail or are too short."""
    commodities.prefetch(years)
    data, skipped = {}, []
    for sym in symbols:
        try:
            ind = add_indicators(commodities.get_prices(sym, years))
        except Exception as e:
            skipped.append(f"{sym} ({e})")
            continue
        if len(ind) < universe.MIN_BARS:
            skipped.append(f"{sym} (only {len(ind)} bars)")
        else:
            data[sym] = ind
    return data, skipped


def run(data: dict[str, pd.DataFrame], st: Strategy, oos: float,
        c: Config = Config()) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (per-contract stats, all trades) for both segments."""
    p = c.params(st)
    stats, trades = [], []
    for sym, ind in data.items():
        long = rules.build(st.rule)(ind)
        short = rules.build(st.short_rule)(ind) if st.short_rule else pd.Series(False, index=ind.index)
        split = int(len(ind) * (1 - oos))
        for seg, part in zip(universe.SEGMENTS, (slice(None, split), slice(split, None))):
            r = engine.run(ind.iloc[part], long.iloc[part], p, short.iloc[part])
            stats.append({"symbol": sym, "segment": seg, **r.stats})
            if len(r.trades):
                trades.append(r.trades.assign(symbol=sym, segment=seg))
    all_trades = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(columns=["segment", "side"])
    return pd.DataFrame(stats), all_trades


def pooled(stats: pd.DataFrame, trades: pd.DataFrame, segment: str) -> dict:
    """universe.pooled plus the long and short sides' average trade."""
    out = universe.pooled(stats, trades, segment)
    t = trades[trades.segment == segment]
    for side in ("long", "short"):
        s = t[t.side == side]
        out[f"{side}_trades"] = len(s)
        out[f"{side}_avg_trade"] = s.ret.mean() if len(s) else float("nan")
    return out


def save(trades: pd.DataFrame, name: str) -> Path:
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"commodities_{name}_{date.today()}_trades.csv"
    trades.to_csv(path, index=False)
    return path
