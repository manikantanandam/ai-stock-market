"""Append-only daily log of signals, kept so sentiment and the combined
signal can be backtested once enough history has accumulated.

Rows are keyed by the trading session the signal is based on (the last
price bar's date), not the wall-clock date, so a scan that runs late, e.g.
the next morning after the Mac was asleep, still lands on the right day.
"""
import csv
from pathlib import Path

from aistock.strategy.signal import Signal

JOURNAL = Path(__file__).resolve().parents[3] / "journal" / "signals.csv"


def record(signals: list[Signal]) -> Path:
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"date": s.as_of, **s.row()} for s in signals]
    new_keys = {(r["date"], r["symbol"]) for r in rows}
    if JOURNAL.exists():  # one row per symbol per session: a re-run replaces it
        with JOURNAL.open() as fh:
            old = [r for r in csv.DictReader(fh) if (r["date"], r["symbol"]) not in new_keys]
    else:
        old = []
    with JOURNAL.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(old + rows)
    return JOURNAL
