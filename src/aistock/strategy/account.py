"""Paper accounts: each strategy trades its live journal BUYs with shared capital.

One account per preset, stepped forward session by session with the same
rules as portfolio.run: the previous session's BUYs are filled at today's open
into the slots and cash free at that close (best technical score first), then
positions opened earlier are checked for gap-aware exits. buy(), check_exit()
and sell() are shared with the backtest, so the two cannot drift apart.

Unlike `paper` (a replay of every signal at a fixed notional), the state is
saved, so it shows what one account with N slots would really have held, and
positions keep the exits they were opened with even if a preset changes later.
Each run catches up on every completed session since the last saved one.

Yahoo prices are split- and dividend-adjusted, and the adjustment rewrites
past bars after each event. Each open position therefore saves the close it
was last marked at (ref_date, ref_close); on the next run, readjust() compares
it with the current data and rescales the position's prices (and shares by the
inverse), so stops keep their place relative to the price and the value is
unchanged. A split or bonus shows up as a large ratio, a dividend as a small one.

Files per strategy in journal/paper/<strategy>/: account.json (capital,
slots), positions.csv (open), trades.csv (closed), equity.csv (per session).
"""
import json
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

import numpy as np
import pandas as pd

from aistock.backtest import portfolio
from aistock.backtest.engine import Params
from aistock.data.prices import prefetch
from aistock.strategy import journal, paper
from aistock.strategy.strategies import PRESETS
from aistock.technical.indicators import score_series

ROOT = journal.ROOT / "paper"
TRADE_COLS = ["symbol", "entry_date", "exit_date", "entry", "exit", "bars", "reason", "pnl", "ret"]
EQUITY_COLS = ["date", "cash", "invested", "equity"]


@dataclass
class Account:
    strategy: str
    capital: float
    max_positions: int
    cash: float
    positions: dict[str, portfolio.Position] = field(default_factory=dict)  # entry_i indexes the calendar
    trades: list[dict] = field(default_factory=list)
    equity: list[dict] = field(default_factory=list)  # date, cash, invested, equity
    refs: dict[str, tuple[str, float]] = field(default_factory=dict)  # symbol -> (date, close) at last mark
    notes: list[str] = field(default_factory=list)  # adjustments made this run

    @property
    def last(self) -> pd.Timestamp | None:
        return pd.Timestamp(self.equity[-1]["date"]) if self.equity else None


def advance(acct: Account, dates: pd.DatetimeIndex, frames: dict[str, pd.DataFrame],
            signals: dict[pd.Timestamp, list[str]], p: Params, start: pd.Timestamp | None = None) -> None:
    """Step the account through every session in `dates` after its last saved one
    (a new account starts at `start`, or the first date).
    frames: add_indicators() frames for every symbol it holds or may buy;
    signals: session date -> symbols with a BUY that session; p: today's preset."""
    score = {s: score_series(f) for s, f in frames.items()}
    mom = {s: f.Close.pct_change(126, fill_method=None) for s, f in frames.items()}
    bar = lambda s, d: frames[s].loc[d] if s in frames and d in frames[s].index else None

    def last_close(s: str, d: pd.Timestamp) -> float:
        """Latest close on/before d; entry price if the stock has no data (e.g. delisted)."""
        before = frames[s].Close.loc[:d] if s in frames else []
        return float(before.iloc[-1]) if len(before) else acct.positions[s].entry_px

    def close(sym: str, i: int, px: float, reason: str):
        pos = acct.positions.pop(sym)
        fill, proceeds = portfolio.sell(pos, px, p)
        acct.cash += proceeds
        acct.trades.append({"symbol": sym, "entry_date": f"{dates[pos.entry_i]:%Y-%m-%d}",
                            "exit_date": f"{dates[i]:%Y-%m-%d}", "entry": pos.entry_px, "exit": fill,
                            "bars": i - pos.entry_i + 1, "reason": reason, "pnl": proceeds - pos.cost_basis,
                            "ret": proceeds / pos.cost_basis - 1})

    if acct.last is not None:
        first = int(dates.searchsorted(acct.last, side="right"))
    else:
        first = 0 if start is None else int(dates.searchsorted(start))
    for i in range(first, len(dates)):
        d = dates[i]
        # 1. entries at today's open from the previous session's BUYs
        free = acct.max_positions - len(acct.positions)
        if i > 0 and free > 0:
            prev = dates[i - 1]
            cand = [s for s in signals.get(prev, []) if s in frames and s not in acct.positions
                    and bar(s, d) is not None and bar(s, prev) is not None
                    and not np.isnan(bar(s, d).Open) and not np.isnan(bar(s, prev).atr14)]
            cand.sort(key=lambda s: portfolio.rank(score[s].get(prev, np.nan), mom[s].get(prev, np.nan)), reverse=True)
            mark = acct.cash + sum(pos.shares * last_close(s, prev) for s, pos in acct.positions.items())
            for s in cand[:free]:
                today = bar(s, d)
                pos = portfolio.buy(s, i, today.Open, today.High, frames[s].atr14.loc[prev], mark, acct.cash,
                                    p, acct.max_positions)
                if pos is None:
                    continue
                acct.cash -= pos.cost_basis
                acct.positions[s] = pos
                if today.Low <= pos.stop:  # stopped out on the entry bar
                    close(s, i, pos.stop, "stop")

        # 2. exits on today's bar for positions opened before today
        for s in [s for s, pos in acct.positions.items() if pos.entry_i < i]:
            today = bar(s, d)
            if today is None or np.isnan(today.Open):
                continue  # no bar today (suspension or late data)
            pos = acct.positions[s]
            hit = portfolio.check_exit(pos, today.Open, today.High, today.Low, today.Close, i - pos.entry_i + 1)
            if hit:
                close(s, i, *hit)

        held = sum(pos.shares * last_close(s, d) for s, pos in acct.positions.items())
        acct.equity.append({"date": f"{d:%Y-%m-%d}", "cash": acct.cash, "invested": held,
                            "equity": acct.cash + held})

    if acct.last is not None:  # remember each open position's mark, to detect re-adjusted prices later
        acct.refs = {s: (f"{acct.last:%Y-%m-%d}", last_close(s, acct.last)) for s in acct.positions}


SPLIT_RATIO = 0.8   # a re-adjustment beyond 20% is a split or bonus, smaller is a dividend
TOLERANCE = 1e-4    # ignore float noise between Yahoo downloads


def readjust(acct: Account, frames: dict[str, pd.DataFrame]) -> None:
    """Rescale open positions whose past prices Yahoo has re-adjusted since the last save."""
    for s, pos in acct.positions.items():
        if s not in acct.refs or s not in frames:
            continue
        date, saved = acct.refs[s]
        before = frames[s].Close.loc[:pd.Timestamp(date)]
        if before.empty or not saved:
            continue
        f = float(before.iloc[-1]) / saved
        if abs(f - 1) < TOLERANCE:
            continue
        pos.entry_px, pos.stop, pos.target = pos.entry_px * f, pos.stop * f, pos.target * f
        pos.atr, pos.highest, pos.shares = pos.atr * f, pos.highest * f, pos.shares / f
        acct.refs[s] = (date, float(before.iloc[-1]))
        kind = "split/bonus" if min(f, 1 / f) < SPLIT_RATIO else "dividend"
        acct.notes.append(f"{s}: prices re-adjusted x{f:.4f} ({kind}); position rescaled")


def _dir(strategy: str) -> Path:
    return ROOT / strategy


def load(strategy: str, dates: pd.DatetimeIndex) -> Account | None:
    """Saved account with position entry dates mapped onto `dates`; None if it doesn't exist yet."""
    path = _dir(strategy)
    if not (path / "account.json").exists():
        return None
    cfg = json.loads((path / "account.json").read_text())
    read = lambda name: pd.read_csv(path / name).to_dict("records") if (path / name).exists() else []
    equity, trades = read("equity.csv"), read("trades.csv")
    positions = {}
    refs = {}
    for r in read("positions.csv"):
        refs[r["sym"]] = (r.pop("ref_date"), r.pop("ref_close"))
        r["entry_i"] = int(dates.get_loc(pd.Timestamp(r.pop("entry_date"))))
        r["trail_atr"] = None if pd.isna(r["trail_atr"]) else r["trail_atr"]
        r["max_hold"] = int(r["max_hold"])
        r["shares"] = float(r["shares"])  # fractional after a dividend re-adjustment
        positions[r["sym"]] = portfolio.Position(**r)
    cash = equity[-1]["cash"] if equity else cfg["capital"]
    return Account(strategy, cfg["capital"], cfg["max_positions"], cash, positions, trades, equity, refs)


def save(acct: Account, dates: pd.DatetimeIndex) -> Path:
    path = _dir(acct.strategy)
    path.mkdir(parents=True, exist_ok=True)
    (path / "account.json").write_text(json.dumps({"capital": acct.capital,
                                                   "max_positions": acct.max_positions}, indent=2) + "\n")
    rows = []
    for pos in acct.positions.values():
        r = asdict(pos)
        ref_date, ref_close = acct.refs[pos.sym]
        rows.append({"entry_date": f"{dates[r.pop('entry_i')]:%Y-%m-%d}", **r,
                     "ref_date": ref_date, "ref_close": ref_close})
    cols = ["entry_date"] + [f.name for f in fields(portfolio.Position) if f.name != "entry_i"] + ["ref_date", "ref_close"]
    pd.DataFrame(rows, columns=cols).to_csv(path / "positions.csv", index=False)
    pd.DataFrame(acct.trades, columns=TRADE_COLS).to_csv(path / "trades.csv", index=False)
    pd.DataFrame(acct.equity, columns=EQUITY_COLS).to_csv(path / "equity.csv", index=False)
    return path


def update(only: list[str] | None = None, capital: float = 100_000.0, max_positions: int = 10) -> list[Account]:
    """Bring every strategy's account up to the latest completed session and save it.
    A new account starts on its strategy's first journal BUY; capital and slots
    only apply when an account is created."""
    sig = paper.load_signals(only=only)
    if sig.empty:
        return []
    dates = paper.session_prices(paper.INDEX).index
    accounts = {name: load(name, dates) or Account(name, capital, max_positions, capital)
                for name in sig.strategy.unique()}
    symbols = sorted(set(sig.symbol) | {s for a in accounts.values() for s in a.positions})
    prefetch(symbols, years=2)
    frames = {}
    for sym in symbols:
        try:
            frames[sym] = paper.session_prices(sym)
        except Exception:
            continue  # delisted / no data: can't be bought
    for name, acct in accounts.items():
        group = sig[sig.strategy == name]
        signals = {pd.Timestamp(d): list(g.symbol) for d, g in group.groupby("date")}
        readjust(acct, frames)
        advance(acct, dates, frames, signals, PRESETS[name].params(), start=pd.Timestamp(group.date.min()))
        save(acct, dates)
    return list(accounts.values())


def summary(acct: Account, nifty: pd.Series) -> dict:
    """Account value vs NIFTY 50 over the same sessions."""
    eq = pd.Series([r["equity"] for r in acct.equity], index=pd.to_datetime([r["date"] for r in acct.equity]))
    t = pd.DataFrame(acct.trades)
    n = nifty.reindex(eq.index).ffill()
    return {
        "strategy": acct.strategy,
        "since": f"{eq.index[0]:%Y-%m-%d}",
        "sessions": len(eq),
        "equity": eq.iloc[-1],
        "return": eq.iloc[-1] / acct.capital - 1,
        "nifty_return": n.iloc[-1] / n.iloc[0] - 1,
        "max_drawdown": float((eq / eq.cummax() - 1).min()),
        "open": len(acct.positions),
        "closed": len(t),
        "win_rate": float((t.ret > 0).mean()) if len(t) else None,
    }
