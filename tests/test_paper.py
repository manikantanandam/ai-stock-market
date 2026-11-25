import pandas as pd
import pytest

from aistock.strategy import paper
from conftest import bars

# 10 bars of a stock that drifts up 1/day with ATR 10; the "index" is flat.
UP = bars([(100 + i, 101 + i, 99 + i, 100 + i) for i in range(10)], atr=10.0, start="2026-09-01")
DATES = UP.index


@pytest.fixture
def replay(tmp_path, monkeypatch):
    """Run paper.run on an in-memory journal with synthetic prices."""
    def go(rows: list[dict], prices: dict[str, pd.DataFrame] | None = None, **kw):
        days = tmp_path / "days"
        days.mkdir()
        for date, g in pd.DataFrame(rows).groupby("date"):
            g.to_csv(days / f"{date}.csv", index=False)
        frames = {"^NSEI": UP.assign(Close=100.0), **(prices or {"A": UP})}
        monkeypatch.setattr(paper.journal, "DAYS", days)
        monkeypatch.setattr(paper, "prefetch", lambda *a, **k: None)
        monkeypatch.setattr(paper, "session_prices", lambda s: frames[s])
        return paper.run(**kw)
    return go


def day(i: int) -> str:
    return f"{DATES[i]:%Y-%m-%d}"


def test_repeat_signals_ignored_while_position_open(replay):
    rows = [{"date": day(i), "symbol": "A", "action": "BUY", "buys": "trend"} for i in (0, 1, 2)]
    trades, pending = replay(rows)
    assert len(trades) == 1 and pending.empty
    t = trades.iloc[0]
    assert (t.status, t.entry_date) == ("open", day(1))
    assert t.entry == pytest.approx(101 * 1.0005, abs=0.01)  # next open + slippage, rounded


def test_one_trade_per_strategy(replay):
    trades, _ = replay([{"date": day(0), "symbol": "A", "action": "BUY", "buys": "trend;swing"}])
    assert sorted(trades.strategy) == ["swing", "trend"]


def test_signal_on_latest_bar_is_pending(replay):
    trades, pending = replay([{"date": day(9), "symbol": "A", "action": "BUY", "buys": "trend"}])
    assert trades.empty and len(pending) == 1


def test_legacy_rows_count_as_swing(replay):
    trades, _ = replay([{"date": day(0), "symbol": "A", "action": "BUY", "buys": None}])
    assert list(trades.strategy) == ["swing"]


def test_signal_date_without_bar_uses_previous_bar(replay):
    gap = UP.drop(DATES[3])
    trades, _ = replay([{"date": day(3), "symbol": "A", "action": "BUY", "buys": "trend"}], {"A": gap})
    assert trades.iloc[0].entry_date == day(4)


def test_new_signal_after_exit_opens_new_trade(replay):
    # Stop hit on bar 3 (low 50); a fresh signal on bar 3 enters on bar 4
    crash = UP.copy()
    crash.iloc[3, crash.columns.get_loc("Low")] = 50
    rows = [{"date": day(0), "symbol": "A", "action": "BUY", "buys": "swing"},
            {"date": day(3), "symbol": "A", "action": "BUY", "buys": "swing"}]
    trades, _ = replay(rows, {"A": crash})
    assert list(trades.status) == ["closed", "open"]
    assert trades.iloc[0].reason == "stop" and trades.iloc[1].entry_date == day(4)


def test_excess_is_versus_nifty(replay):
    trades, _ = replay([{"date": day(0), "symbol": "A", "action": "BUY", "buys": "trend"}])
    t = trades.iloc[0]
    assert t.nifty_ret == pytest.approx(0.0) and t.excess == pytest.approx(t.ret)


def test_summary_counts(replay):
    rows = [{"date": day(0), "symbol": "A", "action": "BUY", "buys": "trend"},
            {"date": day(9), "symbol": "A", "action": "BUY", "buys": "breakout"}]
    trades, pending = replay(rows)
    s = paper.summary(trades, pending).set_index("strategy")
    assert s.loc["trend", "open"] == 1 and s.loc["breakout", "pending"] == 1
