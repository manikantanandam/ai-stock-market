from dataclasses import replace

import pandas as pd
import pytest

from aistock.backtest import portfolio
from aistock.backtest.engine import Params
from aistock.strategy import account
from conftest import bars, random_walk


def every_n(ind: pd.DataFrame, n: int, offset: int) -> pd.Series:
    s = pd.Series(False, index=ind.index)
    s.iloc[220 + offset::n] = True  # after SMA200 warm-up
    return s


def setup(n_days: int = 450):
    data = {s: random_walk(n_days, seed=i) for i, s in enumerate("ABCDE")}
    entries = {s: every_n(d, 4, i) for i, (s, d) in enumerate(data.items())}
    signals = {}
    for s, e in entries.items():
        for d in e.index[e]:
            signals.setdefault(d, []).append(s)
    return data, entries, signals, data["A"].index


def test_matches_portfolio_backtest():
    """Same signals, same rules: the account must reproduce portfolio.run (real costs)."""
    data, entries, signals, dates = setup()
    p = Params(stop_atr=2.0, target_atr=3.0, max_hold=20)
    eq, _, trades = portfolio.run(data, entries, dates, p, max_positions=2)
    acct = account.Account("t", p.capital, 2, p.capital)
    account.advance(acct, dates, data, signals, p)

    closed = trades[trades.reason != "end"]  # portfolio liquidates on the last day
    got = pd.DataFrame(acct.trades)
    assert len(got) == len(closed) > 10
    assert got.ret.to_numpy() == pytest.approx(closed.ret.to_numpy())
    assert list(got.reason) == list(closed.reason)
    mine = pd.Series([r["equity"] for r in acct.equity], index=dates)
    assert mine.iloc[:-1].to_numpy() == pytest.approx(eq.iloc[:-1].to_numpy())


def test_catch_up_across_runs_and_shifting_window(tmp_path, monkeypatch):
    """Saving and resuming later (with a 2-year window that has moved on) gives
    the same result as one uninterrupted run."""
    monkeypatch.setattr(account, "ROOT", tmp_path)
    data, _, signals, dates = setup()
    p = Params(stop_atr=2.0, target_atr=None, trail_atr=2.0, max_hold=30)
    whole = account.Account("t", p.capital, 3, p.capital)
    account.advance(whole, dates, data, signals, p)

    part = account.Account("t", p.capital, 3, p.capital)
    account.advance(part, dates[:300], data, signals, p)
    assert part.positions  # something is open across the save
    account.save(part, dates[:300])
    resumed = account.load("t", dates[40:])
    account.advance(resumed, dates[40:], data, signals, p)

    assert pd.DataFrame(resumed.trades).ret.tolist() == pytest.approx(pd.DataFrame(whole.trades).ret.tolist())
    assert [r["equity"] for r in resumed.equity] == pytest.approx([r["equity"] for r in whole.equity])


def test_open_positions_keep_their_exits_when_the_preset_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(account, "ROOT", tmp_path)
    flat = bars([(100, 101, 99, 100)] * 12).assign(sma50=90.0, sma200=80.0, rsi14=50.0, macd_hist=1.0)
    dates = flat.index
    signals = {dates[0]: ["A"]}
    opened_with = Params(stop_atr=5.0, target_atr=5.0, max_hold=8)
    acct = account.Account("t", 100_000.0, 1, 100_000.0)
    account.advance(acct, dates[:3], {"A": flat}, signals, opened_with)
    account.save(acct, dates)

    acct = account.load("t", dates)
    account.advance(acct, dates, {"A": flat}, signals, replace(opened_with, max_hold=2))
    assert len(acct.trades) == 1
    assert acct.trades[0]["reason"] == "time" and acct.trades[0]["bars"] == 8


def test_new_account_starts_at_first_signal():
    data, _, signals, dates = setup()
    acct = account.Account("t", 100_000.0, 2, 100_000.0)
    first = min(signals)
    account.advance(acct, dates, data, signals, Params(), start=first)
    assert acct.equity[0]["date"] == f"{first:%Y-%m-%d}"


def test_update_reads_the_journal_and_saves_each_strategy(tmp_path, monkeypatch):
    from aistock.strategy import paper
    up = bars([(100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)], start="2026-09-01") \
        .assign(sma50=90.0, sma200=80.0, rsi14=50.0, macd_hist=1.0)
    days = tmp_path / "days"
    days.mkdir()
    for i, buys in [(2, "trend"), (3, "trend;breakout")]:
        pd.DataFrame([{"date": f"{up.index[i]:%Y-%m-%d}", "symbol": "A", "action": "BUY", "buys": buys}]) \
            .to_csv(days / f"{up.index[i]:%Y-%m-%d}.csv", index=False)
    frames = {"^NSEI": up.assign(Close=100.0), "A": up}
    monkeypatch.setattr(paper.journal, "DAYS", days)
    monkeypatch.setattr(account, "ROOT", tmp_path / "paper")
    monkeypatch.setattr(account, "prefetch", lambda *a, **k: None)
    monkeypatch.setattr(paper, "session_prices", lambda s: frames[s])

    accounts = {a.strategy: a for a in account.update()}
    assert set(accounts) == {"trend", "breakout"}
    assert accounts["trend"].equity[0]["date"] == f"{up.index[2]:%Y-%m-%d}"
    assert list(accounts["trend"].positions) == ["A"]          # bought once, repeat signal ignored
    assert accounts["breakout"].positions["A"].entry_i == 4
    assert (tmp_path / "paper" / "breakout" / "positions.csv").exists()
    again = {a.strategy: a for a in account.update()}           # nothing new: no extra sessions
    assert len(again["trend"].equity) == len(accounts["trend"].equity)
    s = account.summary(again["trend"], frames["^NSEI"].Close)
    assert s["open"] == 1 and s["closed"] == 0 and s["nifty_return"] == 0


def flat_frame(price: float, atr: float, n: int = 12) -> pd.DataFrame:
    return bars([(price, price * 1.01, price * 0.99, price)] * n, atr=atr) \
        .assign(sma50=price * 0.9, sma200=price * 0.8, rsi14=50.0, macd_hist=1.0)


@pytest.mark.parametrize("factor, kind", [(0.5, "split/bonus"), (0.99, "dividend")])
def test_readjust_rescales_open_positions(tmp_path, monkeypatch, factor, kind):
    """Yahoo re-adjusts past bars after a split (or dividend). Without rescaling,
    the old stop (80) would sit far above the new adjusted price (50) and fire."""
    monkeypatch.setattr(account, "ROOT", tmp_path)
    before, after = flat_frame(100.0, 10.0), flat_frame(100.0 * factor, 10.0 * factor)
    dates = before.index
    p = Params(stop_atr=2.0, target_atr=None, max_hold=90)
    acct = account.Account("t", 100_000.0, 1, 100_000.0)
    account.advance(acct, dates[:4], {"A": before}, {dates[0]: ["A"]}, p)
    account.save(acct, dates)
    pos = acct.positions["A"]
    shares, stop, value = pos.shares, pos.stop, acct.equity[-1]["equity"]

    acct = account.load("t", dates)
    account.readjust(acct, {"A": after})
    account.advance(acct, dates, {"A": after}, {dates[0]: ["A"]}, p)
    pos = acct.positions["A"]
    assert not acct.trades                                  # no false stop-out
    assert pos.shares == pytest.approx(shares / factor)
    assert pos.stop == pytest.approx(stop * factor)
    assert acct.equity[-1]["equity"] == pytest.approx(value)  # value unchanged by the adjustment
    assert len(acct.notes) == 1 and kind in acct.notes[0]
    account.save(acct, dates)
    again = account.load("t", dates)
    account.readjust(again, {"A": after})
    assert not again.notes                                  # already applied: not rescaled twice


def test_matches_portfolio_backtest_with_slope_rank():
    data, entries, signals, dates = setup()
    p = Params(stop_atr=2.0, target_atr=None, max_hold=40)
    _, _, trades = portfolio.run(data, entries, dates, p, max_positions=2, rank_by="slope")
    acct = account.Account("t", p.capital, 2, p.capital)
    account.advance(acct, dates, data, signals, p, rank_by="slope")
    closed = trades[trades.reason != "end"]
    got = pd.DataFrame(acct.trades)
    assert list(got.symbol) == list(closed.symbol) and len(got) > 5
    assert got.ret.to_numpy() == pytest.approx(closed.ret.to_numpy())
