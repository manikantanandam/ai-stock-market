"""Paper trading: replay the journal's BUY signals forward in time.

Each (strategy, symbol) BUY becomes a trade simulated with engine.run, the
same fills, exits and costs as the backtests: buy at the next session's open,
the preset's ATR stop/target/max hold, and Indian delivery costs. Open trades
are marked at the last close, net of selling costs. While a position is open,
repeat signals for the same strategy and symbol are ignored.

Unlike backtests this is free of survivorship and look-ahead bias, and it
tests the full live signal (fundamental gates and news veto included),
because every signal was recorded before its outcome was known.
Caveat: exits use the presets as they are defined today.
"""
from datetime import datetime

import pandas as pd

from aistock.backtest import engine
from aistock.data.prices import IST, get_prices, market_phase, prefetch
from aistock.strategy import journal
from aistock.strategy.strategies import PRESETS
from aistock.technical.indicators import add_indicators

INDEX = "^NSEI"


def load_signals(since: str | None = None, only: list[str] | None = None) -> pd.DataFrame:
    """One row per (date, symbol, strategy) BUY. Rows from before strategy
    presets existed (no `buys` column) were swing signals."""
    j = journal.load(dtype={"buys": str})
    if j.empty:
        return pd.DataFrame(columns=["date", "symbol", "strategy"])
    if "buys" not in j:
        j["buys"] = None
    legacy = j.buys.isna() & (j.action == "BUY")
    j.loc[legacy, "buys"] = "swing"
    j = j[j.buys.notna() & (j.buys != "")]
    sig = j.assign(strategy=j.buys.str.split(";")).explode("strategy")[["date", "symbol", "strategy"]]
    if since:
        sig = sig[sig.date >= since]
    if only:
        sig = sig[sig.strategy.isin(only)]
    sig = sig[sig.strategy.isin(PRESETS)]  # ignore presets that no longer exist
    return sig.sort_values(["strategy", "symbol", "date"]).reset_index(drop=True)


def session_prices(symbol: str) -> pd.DataFrame:
    ind = add_indicators(get_prices(symbol, years=2))
    # A bar from today's open session is still forming; don't trade on it
    today = pd.Timestamp(datetime.now(IST).date())
    if market_phase() == "live" and len(ind) and ind.index[-1] == today:
        ind = ind.iloc[:-1]
    return ind


def run(since: str | None = None, only: list[str] | None = None,
        notional: float = 100_000.0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (trades, pending). Trades have status 'open' or 'closed';
    pending signals are waiting for the next session's open."""
    sig = load_signals(since, only)
    if sig.empty:
        return pd.DataFrame(), pd.DataFrame()
    prefetch(sorted(set(sig.symbol)) + [INDEX], years=2)
    nifty = session_prices(INDEX).Close
    trades, pending, cache = [], [], {}

    for (strategy, symbol), group in sig.groupby(["strategy", "symbol"]):
        try:
            ind = cache.setdefault(symbol, session_prices(symbol))
        except Exception:
            continue  # delisted / no data: nothing to replay
        p = PRESETS[strategy].params()
        p.capital = notional
        busy_until = None  # exit date of the last trade; None while flat
        for date in group.date:
            d = pd.Timestamp(date)
            if busy_until is not None and (busy_until == "open" or d < busy_until):
                continue  # still in a position from an earlier signal
            # Signal bar = last bar on/before the signal date (Yahoo occasionally
            # drops a session after the fact); entry is the following bar's open
            before = ind.loc[:d]
            if before.empty:
                continue
            window = ind.loc[before.index[-1]:]
            if len(window) < 2:
                pending.append({"strategy": strategy, "symbol": symbol, "signal_date": date})
                continue
            entries = pd.Series(False, index=window.index)
            entries.iloc[0] = True
            t = engine.run(window, entries, p).trades
            if t.empty:
                continue  # not enough cash for one share at this notional
            t = t.iloc[0]
            is_open = t.reason == "end"
            exit_date = t.exit_date
            nifty_ret = nifty.asof(exit_date) / nifty.asof(d) - 1
            trades.append({
                "strategy": strategy, "symbol": symbol, "signal_date": date,
                "entry_date": f"{t.entry_date:%Y-%m-%d}", "entry": round(t.entry, 2),
                "exit_date": None if is_open else f"{exit_date:%Y-%m-%d}",
                "last" if is_open else "exit": round(t.exit, 2),
                "status": "open" if is_open else "closed", "reason": None if is_open else t.reason,
                "days": int(t.bars), "ret": t.ret, "nifty_ret": nifty_ret, "excess": t.ret - nifty_ret,
                "stop": round(t.entry - p.stop_atr * window.atr14.iloc[0], 2),
                "target": round(t.entry + p.target_atr * window.atr14.iloc[0], 2) if p.target_atr else None,
            })
            busy_until = "open" if is_open else exit_date
    return pd.DataFrame(trades), pd.DataFrame(pending)


def summary(trades: pd.DataFrame, pending: pd.DataFrame) -> pd.DataFrame:
    """Per-strategy stats; closed-trade stats are the real scorecard."""
    rows = []
    names = sorted(set(trades.get("strategy", [])) | set(pending.get("strategy", [])))
    for name in names:
        t = trades[trades.strategy == name] if len(trades) else trades
        c = t[t.status == "closed"] if len(t) else t
        o = t[t.status == "open"] if len(t) else t
        rows.append({
            "strategy": name,
            "pending": int((pending.strategy == name).sum()) if len(pending) else 0,
            "open": len(o),
            "closed": len(c),
            "win_rate": (c.ret > 0).mean() if len(c) else None,
            "avg_closed": c.ret.mean() if len(c) else None,
            "avg_open": o.ret.mean() if len(o) else None,
            "avg_all": t.ret.mean() if len(t) else None,
            "excess_vs_nifty": t.excess.mean() if len(t) else None,
            "avg_days": t.days.mean() if len(t) else None,
        })
    return pd.DataFrame(rows)
