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
    old, fields = [], []
    if JOURNAL.exists():  # one row per symbol per session: a re-run replaces it
        with JOURNAL.open() as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or [])
            old = [r for r in reader if (r["date"], r["symbol"]) not in new_keys]
    # Columns change as the signal evolves; keep the union so old rows stay
    # intact and missing values are left blank
    fields += [k for k in rows[0] if k not in fields]
    with JOURNAL.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(old + rows)
    return JOURNAL
