"""Append-only daily log of signals, kept so sentiment and the combined
signal can be backtested once enough history has accumulated."""
import csv
from datetime import date
from pathlib import Path

from aistock.strategy.signal import Signal

JOURNAL = Path(__file__).resolve().parents[3] / "journal" / "signals.csv"


def record(signals: list[Signal]) -> Path:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    today = str(date.today())
    rows = [{"date": today, **s.row()} for s in signals]
    if JOURNAL.exists():  # one row per symbol per day: drop today's earlier run
        with JOURNAL.open() as fh:
            old = [r for r in csv.DictReader(fh)
                   if not (r["date"] == today and r["symbol"] in {s.symbol for s in signals})]
    else:
        old = []
    with JOURNAL.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(old + rows)
    return JOURNAL
