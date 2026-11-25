import pandas as pd
from conftest import random_walk

from aistock.llm import backfill, notes
from aistock.strategy import journal
from aistock.strategy.signal import Signal, StrategySignal
from aistock.technical.indicators import technical_score


def row(**kw) -> dict:
    return {"date": "2026-11-18", "symbol": "ABC", "name": "ABC Ltd", "sector": "Tech", "action": "BUY",
            "buys": "swing;trend", "combined": 0.5, "technical": 0.6, "fundamental": 0.4,
            "sentiment": float("nan"), "close": 100.0, "atr": 5.0} | kw


def test_signal_from_row_recomputes_levels_and_marks_unsaved_reasons():
    s = backfill.signal_from_row(row())
    assert s.buys == ["swing", "trend"] and s.as_of == "2026-11-18" and s.sentiment is None
    assert (s.strategies["swing"].stop, s.strategies["swing"].target) == (90.0, 115.0)  # 2 ATR stop, 3 ATR target
    assert s.reasons == {"technical": [backfill.UNSAVED], "fundamental": [backfill.UNSAVED],
                         "sentiment": ["news not fetched for this session"]}
    assert "fundamental reason: " + backfill.UNSAVED in notes.facts(s, "swing")


def test_rebuilt_news_reasons_only_where_the_day_had_a_sentiment_score():
    why = ["headlines re-fetched later ...", "+0.80 2026-11-17 ABC wins order (Mint)"]
    assert backfill.signal_from_row(row(sentiment=0.4), sentiment_why=why).reasons["sentiment"] == why
    assert backfill.signal_from_row(row(sentiment=0.4)).reasons["sentiment"] == [backfill.UNSAVED]
    assert backfill.signal_from_row(row(), sentiment_why=why).reasons["sentiment"] == [
        "news not fetched for this session"]


def test_signal_from_row_uses_saved_reasons_and_skips_unknown_presets():
    s = backfill.signal_from_row(row(buys="squeeze;swing", technical_reasons="uptrend | MACD histogram positive"))
    assert s.buys == ["swing"]
    assert s.reasons["technical"] == ["uptrend", "MACD histogram positive"]
    assert backfill.signal_from_row(row(buys="squeeze")) is None


def test_technical_reasons_only_when_score_reproduces():
    df = random_walk()
    day = f"{df.index[-5]:%Y-%m-%d}"
    score, why = technical_score(df.iloc[:-4])
    assert backfill.technical_reasons(df, day, round(score, 3)) == why
    assert "could not be rebuilt" in backfill.technical_reasons(df, day, round(score, 3) + 0.5)[0]
    assert "no price bar" in backfill.technical_reasons(df, "1990-01-01", score)[0]


def test_journal_row_keeps_reasons_and_ratios(tmp_path, monkeypatch):
    for name in ("ROOT", "DAYS", "STOCKS", "NOTES"):
        monkeypatch.setattr(journal, name, tmp_path / name.lower())
    s = Signal("ABC", "ABC Ltd", "Tech", "BUY", 0.5, 0.6, 0.4, None, 100.0, 5.0,
               {"swing": StrategySignal("swing", "BUY", True, 90.0, 115.0, 10, [])},
               {"technical": ["uptrend", "RSI overbought (71)"], "fundamental": ["ROE 18.0%"]},
               as_of="2026-11-18", fundamentals={"returnOnEquity": 0.18})
    journal.record([s])
    saved = pd.read_csv(tmp_path / "days" / "2026-11-18.csv").iloc[0]
    assert saved.technical_reasons == "uptrend | RSI overbought (71)"
    assert saved.f_returnOnEquity == 0.18
    # and a backfill from that row gets the saved reasons back
    assert backfill.signal_from_row(saved.to_dict()).reasons["technical"] == ["uptrend", "RSI overbought (71)"]
    assert backfill.sessions_without_notes() == ["2026-11-18"]
