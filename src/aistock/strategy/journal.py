"""Append-only daily log of signals, kept so sentiment and the combined
signal can be backtested once enough history has accumulated.

Rows are keyed by the trading session the signal is based on (the last
price bar's date), not the wall-clock date, so a scan that runs late, e.g.
the next morning after the Mac was asleep, still lands on the right day.

The same rows are stored twice so each view stays small to read:
`days/<date>.csv` holds one session's scan, `stocks/<SYMBOL>.csv` one
stock's history. The day files are the source of truth for `load`.
"""
import csv
from collections import defaultdict
from pathlib import Path

import pandas as pd

from aistock.strategy.signal import Signal

ROOT = Path(__file__).resolve().parents[3] / "journal"
DAYS = ROOT / "days"
STOCKS = ROOT / "stocks"


def record(signals: list[Signal]) -> Path:
    rows = [{"date": s.as_of, **s.row()} for s in signals]
    by_day, by_stock = defaultdict(list), defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
        by_stock[r["symbol"]].append(r)
    for day, day_rows in by_day.items():
        _upsert(DAYS / f"{day}.csv", day_rows, key="symbol")
    for symbol, stock_rows in by_stock.items():
        _upsert(STOCKS / f"{symbol}.csv", stock_rows, key="date")
    return ROOT


def load(**read_csv_kw) -> pd.DataFrame:
    """Every session's rows in date order (empty if nothing is logged yet)."""
    paths = sorted(DAYS.glob("*.csv"))
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(p, **read_csv_kw) for p in paths], ignore_index=True)


def _upsert(path: Path, rows: list[dict], key: str) -> None:
    """Merge rows into a CSV, one row per `key`: a re-run replaces it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_keys = {r[key] for r in rows}
    old, fields = [], []
    if path.exists():
        with path.open() as fh:
            reader = csv.DictReader(fh)
            fields = list(reader.fieldnames or [])
            old = [r for r in reader if r[key] not in new_keys]
    # Columns change as the signal evolves; keep the union so old rows stay
    # intact and missing values are left blank
    fields += [k for k in rows[0] if k not in fields]
    merged = sorted(old + rows, key=lambda r: r["date"]) if key == "date" else old + rows
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, restval="")
        w.writeheader()
        w.writerows(merged)
