from datetime import datetime

import pandas as pd
import pytest

from aistock import site
from aistock.backtest import engine
from aistock.commodity import backtest, signal
from aistock.commodity.strategies import PRESETS
from aistock.data import commodities
from aistock.llm import notes
from aistock.strategy import journal
from aistock.technical import rules
from conftest import random_walk

NY = commodities.NEW_YORK


def usd_frame(closes, start="2026-01-05"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 100})


def test_to_inr_uses_latest_rate_and_unit_factor():
    usd = usd_frame([100, 110, 120])
    fx = pd.Series([80.0, 90.0], index=[usd.index[0], usd.index[2]])  # no FX bar on day 2
    out = commodities.to_inr(usd, fx, factor=0.5)
    assert out.Close.tolist() == [4000, 4400, 5400]  # day 2 carries day 1's rate


def test_to_inr_drops_non_positive_bars():
    usd = usd_frame([20, 0.5, 18])  # Low = -0.5 on day 2 (WTI, April 2020)
    out = commodities.to_inr(usd, pd.Series(80.0, index=usd.index), factor=1)
    assert len(out) == 2


def test_completed_drops_a_session_still_trading_in_new_york():
    df = usd_frame([1, 2, 3], start="2026-11-23")  # Mon..Wed
    assert len(commodities.completed(df, datetime(2026, 11, 25, 16, 59, tzinfo=NY))) == 2
    assert len(commodities.completed(df, datetime(2026, 11, 25, 17, 0, tzinfo=NY))) == 3


def test_gold_is_quoted_per_10_grams():
    assert commodities.get("gold").factor == pytest.approx(10 / 31.1034768)
    with pytest.raises(ValueError):
        commodities.get("ZINC")


# ---------- verdicts ----------

TREND = PRESETS["trend"]
CFG = signal.Config()


def pos(open_, side="long", reason=None):
    return signal.Position(side, open_, 100.0, "2026-11-01", 90.0, None, None if open_ else 95.0, reason)


def test_flat_long_rule_says_buy_with_stop_below():
    r = signal.decide(TREND, True, False, None, 0.1, 100.0, 5.0, CFG)
    assert (r.action, r.side, r.stop, r.target) == ("BUY", "long", 85.0, None)
    assert r.qty == pytest.approx(1000 / 15, abs=0.01)  # 1% risk over a 15-point stop


def test_flat_short_rule_says_sell_with_stop_above():
    r = signal.decide(PRESETS["pullback"], False, True, None, None, 100.0, 5.0, CFG)
    assert (r.action, r.side, r.stop, r.target) == ("SELL", "short", 110.0, 85.0)


def test_news_veto_is_directional():
    assert signal.decide(TREND, True, False, None, -0.5, 100.0, 5.0, CFG).action == "WATCH"
    assert signal.decide(TREND, False, True, None, -0.5, 100.0, 5.0, CFG).action == "SELL"
    assert signal.decide(TREND, False, True, None, 0.5, 100.0, 5.0, CFG).action == "WATCH"


def test_open_position_holds_even_when_a_rule_fires():
    r = signal.decide(TREND, True, False, pos(True), None, 100.0, 5.0, CFG)
    assert (r.action, r.side, r.stop) == ("HOLD", "long", 90.0)


def test_position_closed_today_is_an_exit_unless_a_new_entry_fires():
    assert signal.decide(TREND, False, False, pos(False, "short", "stop"), None, 100.0, 5.0, CFG).action == "EXIT"
    r = signal.decide(TREND, True, False, pos(False, "short", "stop"), None, 100.0, 5.0, CFG)
    assert r.action == "BUY" and "closed today by stop" in r.why[1]


def test_overall_action_priority():
    mk = lambda *acts: {str(i): signal.StrategySignal(str(i), a, None, None, None, 0, []) for i, a in enumerate(acts)}
    assert signal.overall(mk("WATCH", "HOLD", "EXIT")) == "EXIT"
    assert signal.overall(mk("HOLD", "SELL")) == "SELL"
    assert signal.overall(mk("BUY", "SELL")) == "MIXED"
    assert signal.overall(mk("WATCH")) == "WATCH"


def test_position_replays_the_backtest():
    ind = random_walk(400, seed=3, drift=-0.004)  # steady downtrend: trend goes short
    build = lambda name: rules.build(name)(ind)
    trade = engine.run(ind, build(TREND.rule), CFG.params(TREND), build(TREND.short_rule)).trades.iloc[0]
    p = signal.position(TREND, ind.iloc[:ind.index.get_loc(trade.entry_date) + 5], CFG)  # inside the first trade
    assert (p.side, p.open, p.entry_date) == ("short", True, f"{trade.entry_date:%Y-%m-%d}")
    closed = signal.position(TREND, ind.loc[:trade.exit_date], CFG)
    assert not closed.open and closed.reason == trade.reason
    assert signal.position(PRESETS["trend-long"], ind, CFG) is None


HEADLINE = {"title": "Gold slips", "source": "Mint", "published": "2026-11-25T10:00:00+00:00", "link": "https://x",
            "score": -0.8, "weight": 1.0}


@pytest.fixture
def offline(monkeypatch):
    def setup(prices, sentiment=0.0):
        monkeypatch.setattr(commodities, "get_prices", lambda s, years=2: prices[["Open", "High", "Low", "Close", "Volume"]])
        monkeypatch.setattr(commodities, "usd_inr", lambda years=2: pd.Series(80.0, index=prices.index))
        monkeypatch.setattr(signal, "get_commodity_news", lambda s, q: [])
        monkeypatch.setattr(signal, "score_headlines", lambda items: [HEADLINE])
        monkeypatch.setattr(signal, "summarize", lambda h: (sentiment, ["stub"]))
    return setup


def test_evaluate_downtrend_is_short_side(offline):
    offline(random_walk(400, seed=3, drift=-0.004))
    s = signal.evaluate("GOLD", news="none")
    assert s.strategies["trend"].side == "short" and s.strategies["trend"].action in ("SELL", "HOLD", "EXIT")
    assert s.strategies["trend-long"].action == "WATCH"
    assert s.close_usd == pytest.approx(s.close / 80 / commodities.CONTRACTS["GOLD"].factor, abs=0.01)
    row = s.row()
    assert row["unit"] == "10 g" and "fundamental" not in row


def test_journal_and_notes_go_under_commodities(offline, tmp_path, monkeypatch):
    offline(random_walk(400, seed=3, drift=-0.004))
    monkeypatch.setattr(journal, "COMMODITIES", tmp_path / "commodities")
    s = signal.evaluate("SILVER", news="none")
    journal.record([s], root=journal.COMMODITIES, per_symbol="contracts")
    journal.record_notes([(s, "trend", "a note")], root=journal.COMMODITIES)
    assert (tmp_path / "commodities" / "days" / f"{s.as_of}.csv").exists()
    assert (tmp_path / "commodities" / "contracts" / "SILVER.csv").exists()
    assert (tmp_path / "commodities" / "notes" / f"{s.as_of}.csv").exists()


def test_commodity_facts_name_direction_unit_and_stop_side(offline):
    offline(random_walk(400, seed=3, drift=-0.004))
    s = signal.evaluate("GOLD", news="none")
    s.strategies["trend"] = signal.StrategySignal("trend", "SELL", "short", 123.45, None, 1.5, [])
    text = notes.commodity_facts(s, "trend")
    assert "SELL, open a short" in text and "INR per 10 g" in text and "123.45" in text and "above the close" in text
    assert "other strategy 'trend-long': WATCH" in text


def test_note_focus_and_facts_cover_hold_exit_and_watch(offline):
    offline(random_walk(400, seed=3, drift=-0.004))
    s = signal.evaluate("GOLD", news="none")
    mk = lambda a, side: signal.StrategySignal("x", a, side, 120.0, None, 0.0, ["holding short from 2026-11-01 at 130.0"])
    s.strategies = {"trend": mk("WATCH", None), "channel": mk("HOLD", "short"), "ema-cross": mk("EXIT", "long")}
    assert s.focus == "ema-cross"  # an exit outranks a hold
    assert "EXIT, the strategy's long position closed today" in notes.commodity_facts(s, "ema-cross")
    hold = notes.commodity_facts(s, "channel")
    assert "HOLD" in hold and "stop 120.00" in hold and "quantity" not in hold
    s.strategies = {"trend": mk("WATCH", None)}
    assert s.focus is None and "no strategy has a position" in notes.commodity_facts(s, None)


def test_backtest_pools_long_and_short_trades():
    data = {"A": random_walk(500, seed=3, drift=-0.003), "B": random_walk(500, seed=4, drift=0.003)}
    stats, trades = backtest.run(data, TREND, oos=0.3)
    assert set(stats.segment) == {"In-sample", "Out-of-sample"} and len(stats) == 4
    assert {"long", "short"} <= set(trades.side)
    pooled = backtest.pooled(stats, trades, "In-sample")
    assert pooled["long_trades"] + pooled["short_trades"] == pooled["trades"]


def test_site_routes_commodity_journal():
    assert site.kind_of("journal/commodities/days/2026-11-25.csv", ["date"]) == "cday"
    assert site.kind_of("journal/commodities/notes/2026-11-25.csv", ["date"]) == "cnotes"
    assert site.group_of("journal/commodities/contracts/GOLD.csv") == "Contracts"
    assert site.group_of("journal/commodities/notes/2026-11-25.csv") is None  # drawn on the session page
    assert site.kind_of("journal/commodities/headlines/2026-11-25.csv", ["date"]) == "cheadlines"
    assert site.kind_of("journal/days/2026-11-25.csv", ["date"]) == "day"


def test_side_of_the_overall_action(offline):
    offline(random_walk(400, seed=3, drift=-0.004))
    s = signal.evaluate("GOLD", news="none")
    mk = lambda a, side: signal.StrategySignal("x", a, side, None, None, 0.0, [])
    s.strategies = {"a": mk("HOLD", "short"), "b": mk("WATCH", None)}
    s.action = signal.overall(s.strategies)
    assert (s.action, s.side, s.row()["side"]) == ("HOLD", "short", "short")
    s.strategies["b"] = mk("HOLD", "long")
    assert s.side == "mixed"
    s.strategies = {"a": mk("BUY", "long"), "b": mk("SELL", "short")}
    s.action = signal.overall(s.strategies)
    assert (s.action, s.side) == ("MIXED", "mixed")
    s.strategies = {"a": mk("WATCH", None)}
    s.action = "WATCH"
    assert s.side is None and s.row()["side"] == ""


def test_headlines_saved_per_session_and_rerun_replaces_a_contract(offline, tmp_path):
    offline(random_walk(400, seed=3, drift=-0.004))
    gold, silver = signal.evaluate("GOLD"), signal.evaluate("SILVER")
    assert gold.headlines == [HEADLINE] and "headlines" not in gold.row()
    journal.record_headlines([gold, silver], root=tmp_path)
    gold.headlines = [HEADLINE, {**HEADLINE, "title": "Gold rallies", "score": 0.9}]
    journal.record_headlines([gold], root=tmp_path)
    rows = pd.read_csv(tmp_path / "headlines" / f"{gold.as_of}.csv")
    assert sorted(rows.symbol) == ["GOLD", "GOLD", "SILVER"]
    assert set(rows.columns) == {"date", "symbol", "published", "score", "weight", "title", "source"}
    assert signal.evaluate("GOLD", news="none").headlines == []


def test_summarize_is_the_recency_weighted_mean():
    from aistock.sentiment.finbert import summarize
    h = [{**HEADLINE, "score": 0.8, "weight": 1.0}, {**HEADLINE, "score": -0.4, "weight": 0.5}]
    score, why = summarize(h)
    assert score == pytest.approx((0.8 - 0.2) / 1.5) and why[0] == "2 headlines: 1 positive, 1 negative, 0 neutral"
    assert summarize([]) == (0.0, ["no recent headlines"])
