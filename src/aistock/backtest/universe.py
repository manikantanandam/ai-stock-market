"""Run the single-stock backtest over a watchlist and pool the results.

Pooled trade stats (win rate, expectancy, profit factor) are the main
read-out: one stock gives a few dozen trades, the universe gives hundreds.
Caveat: the watchlist is today's constituents, so history is
survivorship-biased (stocks that fell out of the index are missing).
"""
from datetime import date
from pathlib import Path

import pandas as pd

from aistock.backtest import engine
from aistock.data.prices import get_prices
from aistock.technical import rules
from aistock.technical.indicators import add_indicators

REPORTS = Path(__file__).resolve().parents[3] / "reports"
MIN_BARS = 400  # 200 bars of SMA200 warm-up plus enough to test
INDEX = "^NSEI"  # NIFTY 50, used for the market-regime filter
SEGMENTS = ("In-sample", "Out-of-sample")


def load(symbols: list[str], years: int, progress=iter) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """Indicator frames per symbol, skipping ones that fail or are too short."""
    data, skipped = {}, []
    for sym in progress(symbols):
        try:
            ind = add_indicators(get_prices(sym, years))
        except Exception as e:
            skipped.append(f"{sym} ({e})")
            continue
        if len(ind) < MIN_BARS:
            skipped.append(f"{sym} (only {len(ind)} bars)")
        else:
            data[sym] = ind
    return data, skipped


def regime(years: int) -> pd.Series:
    return rules.market_regime(add_indicators(get_prices(INDEX, years)))


def segments(ind: pd.DataFrame, entries: pd.Series, oos: float) -> dict[str, tuple]:
    """Split into in-sample / out-of-sample; indicators use full history (all causal)."""
    split = int(len(ind) * (1 - oos))
    return {SEGMENTS[0]: (ind.iloc[:split], entries.iloc[:split]),
            SEGMENTS[1]: (ind.iloc[split:], entries.iloc[split:])}


def run_universe(data: dict[str, pd.DataFrame], entries: dict[str, pd.Series], p: engine.Params,
                 oos: float, only: tuple[str, ...] = SEGMENTS) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (per-stock stats, all trades) for the requested segments."""
    stats, trades = [], []
    for sym, ind in data.items():
        for seg, (i, e) in segments(ind, entries[sym], oos).items():
            if seg not in only:
                continue
            r = engine.run(i, e, p)
            stats.append({"symbol": sym, "segment": seg, **r.stats})
            if len(r.trades):
                trades.append(r.trades.assign(symbol=sym, segment=seg))
    all_trades = pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(columns=["segment"])
    return pd.DataFrame(stats), all_trades


def build_entries(data: dict[str, pd.DataFrame], rule: str, market: pd.Series | None) -> dict[str, pd.Series]:
    fn = rules.build(rule)
    return {sym: rules.with_regime(fn(ind), market) for sym, ind in data.items()}


def pooled(stats: pd.DataFrame, trades: pd.DataFrame, segment: str) -> dict:
    s = stats[stats.segment == segment]
    t = trades[trades.segment == segment]
    if t.empty:
        return {"stocks": len(s), "trades": 0}
    wins, losses = t.ret[t.ret > 0].sum(), -t.ret[t.ret <= 0].sum()
    return {
        "stocks": len(s),
        "trades": len(t),
        "win_rate": (t.ret > 0).mean(),
        "avg_trade": t.ret.mean(),
        "median_trade": t.ret.median(),
        "profit_factor": wins / losses if losses else float("inf"),
        "avg_bars": t.bars.mean(),
        "exit_stop": (t.reason == "stop").mean(),
        "exit_target": (t.reason == "target").mean(),
        "exit_time": (t.reason == "time").mean(),
        "median_cagr": s.cagr.median(),
        "median_buy_hold_cagr": s.buy_hold_cagr.median(),
        "median_max_dd": s.max_drawdown.median(),
        "median_sharpe": s.sharpe.median(),
        "exposure": s.exposure.median(),
        "profitable_stocks": (s.total_return > 0).mean(),
        "beat_buy_hold": (s.total_return > s.buy_hold).mean(),
    }


def save(stats: pd.DataFrame, trades: pd.DataFrame, name: str) -> Path:
    REPORTS.mkdir(exist_ok=True)
    stem = REPORTS / f"backtest_{name}_{date.today()}"
    stats.to_csv(f"{stem}_stocks.csv", index=False)
    trades.to_csv(f"{stem}_trades.csv", index=False)
    return Path(f"{stem}_stocks.csv")
