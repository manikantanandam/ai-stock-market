import csv

import pytest

from aistock.strategy import journal
from aistock.strategy.signal import Signal, StrategySignal


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    path = tmp_path / "signals.csv"
    monkeypatch.setattr(journal, "JOURNAL", path)
    return path


def sig(symbol: str, as_of: str, buy: bool = False) -> Signal:
    st = StrategySignal("swing", "BUY" if buy else "WATCH", buy, 90.0, 115.0, 10, [])
    return Signal(symbol, symbol, "Tech", "BUY" if buy else "WATCH", 0.5, 0.6, 0.4, None, 100.0, 5.0,
                  {"swing": st}, as_of=as_of)


def read(path):
    with path.open() as fh:
        return list(csv.DictReader(fh))


def test_rows_keyed_by_session_and_rerun_replaces(tmp_journal):
    journal.record([sig("A", "2026-11-18"), sig("B", "2026-11-18")])
    journal.record([sig("A", "2026-11-18", buy=True)])  # same session re-run
    journal.record([sig("A", "2026-11-19")])
    rows = read(tmp_journal)
    assert [(r["date"], r["symbol"]) for r in rows] == [("2026-11-18", "B"), ("2026-11-18", "A"), ("2026-11-19", "A")]
    assert rows[1]["buys"] == "swing" and rows[1]["action"] == "BUY"


def test_old_columns_are_kept_when_schema_changes(tmp_journal):
    tmp_journal.write_text("date,symbol,action,stop\n2026-11-01,OLD,BUY,95\n")
    journal.record([sig("NEW", "2026-11-18")])
    rows = read(tmp_journal)
    assert rows[0]["stop"] == "95" and rows[0]["atr"] == ""
    assert rows[1]["stop"] == "" and rows[1]["atr"] == "5.0"


def test_missing_sentiment_is_blank(tmp_journal):
    journal.record([sig("A", "2026-11-18")])
    assert read(tmp_journal)[0]["sentiment"] == ""
