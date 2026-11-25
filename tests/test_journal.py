import csv

import pytest

from aistock.strategy import journal
from aistock.strategy.signal import Signal, StrategySignal


@pytest.fixture
def tmp_journal(tmp_path, monkeypatch):
    monkeypatch.setattr(journal, "ROOT", tmp_path)
    monkeypatch.setattr(journal, "DAYS", tmp_path / "days")
    monkeypatch.setattr(journal, "STOCKS", tmp_path / "stocks")
    monkeypatch.setattr(journal, "NOTES", tmp_path / "notes")
    return tmp_path


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
    day = read(tmp_journal / "days" / "2026-11-18.csv")
    assert [r["symbol"] for r in day] == ["B", "A"]
    assert day[1]["buys"] == "swing" and day[1]["action"] == "BUY"
    assert [r["symbol"] for r in read(tmp_journal / "days" / "2026-11-19.csv")] == ["A"]
    stock = read(tmp_journal / "stocks" / "A.csv")
    assert [(r["date"], r["action"]) for r in stock] == [("2026-11-18", "BUY"), ("2026-11-19", "WATCH")]
    assert [r["date"] for r in read(tmp_journal / "stocks" / "B.csv")] == ["2026-11-18"]


def test_stock_file_stays_in_date_order(tmp_journal):
    journal.record([sig("A", "2026-11-19")])
    journal.record([sig("A", "2026-11-18")])  # backfill of an earlier session
    assert [r["date"] for r in read(tmp_journal / "stocks" / "A.csv")] == ["2026-11-18", "2026-11-19"]


def test_old_columns_are_kept_when_schema_changes(tmp_journal):
    (tmp_journal / "days").mkdir()
    (tmp_journal / "days" / "2026-11-18.csv").write_text("date,symbol,action,stop\n2026-11-18,OLD,BUY,95\n")
    journal.record([sig("NEW", "2026-11-18")])
    rows = read(tmp_journal / "days" / "2026-11-18.csv")
    assert rows[0]["stop"] == "95" and rows[0]["atr"] == ""
    assert rows[1]["stop"] == "" and rows[1]["atr"] == "5.0"


def test_missing_sentiment_is_blank(tmp_journal):
    journal.record([sig("A", "2026-11-18")])
    assert read(tmp_journal / "days" / "2026-11-18.csv")[0]["sentiment"] == ""


def test_load_concatenates_days_in_order(tmp_journal):
    assert journal.load().empty
    journal.record([sig("B", "2026-11-19")])
    journal.record([sig("A", "2026-11-18")])
    assert list(journal.load()[["date", "symbol"]].itertuples(index=False, name=None)) == [
        ("2026-11-18", "A"), ("2026-11-19", "B")]


def test_notes_saved_per_session_and_rerun_replaces(tmp_journal):
    journal.record_notes([(sig("A", "2026-11-18", buy=True), "swing", "first"),
                          (sig("B", "2026-11-18", buy=True), "swing", "other")])
    journal.record_notes([(sig("A", "2026-11-18", buy=True), "swing", "second")])
    rows = read(tmp_journal / "notes" / "2026-11-18.csv")
    assert sorted((r["symbol"], r["note"]) for r in rows) == [("A", "second"), ("B", "other")]
    assert rows[0].keys() == {"date", "symbol", "name", "strategy", "note"}
