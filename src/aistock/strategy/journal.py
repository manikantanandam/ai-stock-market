"""Append-only daily log of signals, kept so sentiment and the combined
signal can be backtested once enough history has accumulated.

Rows are keyed by the trading session the signal is based on (the last
price bar's date), not the wall-clock date, so a scan that runs late, e.g.
the next morning after the Mac was asleep, still lands on the right day.

The same rows are stored twice so each view stays small to read:
`days/<date>.csv` holds one session's scan, `stocks/<SYMBOL>.csv` one
stock's history. The day files are the source of truth for `load`.
LLM notes on a session's BUYs go to `notes/<date>.csv`, one row per stock.
Day rows also keep each score's reasons and the raw fundamental ratios
(`f_*`), since Yahoo can't serve past fundamentals snapshots.
Commodity futures get the same layout under `commodities/`, with
`contracts/` in place of `stocks/`, plus `headlines/<date>.csv`: every scored
headline behind each contract's news score (stocks keep only the top ones in
their reasons; all of theirs would be ~3000 rows a day).
"""
import csv
from collections import defaultdict
from pathlib import Path

import pandas as pd

from aistock.strategy.signal import Signal

ROOT = Path(__file__).resolve().parents[3] / "journal"
DAYS = ROOT / "days"
STOCKS = ROOT / "stocks"
NOTES = ROOT / "notes"
COMMODITIES = ROOT / "commodities"


def record(signals: list[Signal], root: Path | None = None, per_symbol: str = "stocks") -> Path:
    """Signals are stock or commodity signals (anything with as_of, symbol, row()).
    root: None for the stock journal, COMMODITIES (with per_symbol="contracts") for futures."""
    days, symbols = (DAYS, STOCKS) if root is None else (root / "days", root / per_symbol)
    rows = [{"date": s.as_of, **s.row()} for s in signals]
    by_day, by_stock = defaultdict(list), defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
        by_stock[r["symbol"]].append(r)
    for day, day_rows in by_day.items():
        _upsert(days / f"{day}.csv", day_rows, key="symbol")
    for symbol, stock_rows in by_stock.items():
        _upsert(symbols / f"{symbol}.csv", stock_rows, key="date")
    return days.parent


def record_notes(notes: list[tuple[Signal, str, str]], source: str = "live", root: Path | None = None) -> None:
    """Save (signal, strategy, note) triples by session; a re-run replaces a stock's note.
    source: "live" (written by the scan) or "backfill" (rebuilt later from the journal).
    strategy may be None for a commodity note on the whole contract (all WATCH)."""
    by_day = defaultdict(list)
    for s, strategy, text in notes:
        action = s.strategies[strategy].action if strategy in s.strategies else s.action
        by_day[s.as_of].append({"date": s.as_of, "symbol": s.symbol, "name": s.name, "strategy": strategy or "",
                                "action": action, "note": text, "source": source})
    for day, rows in by_day.items():
        _upsert((NOTES if root is None else root / "notes") / f"{day}.csv", rows, key="symbol")


def record_headlines(signals: list, root: Path) -> None:
    """Save each signal's scored headlines by session; a re-run replaces a symbol's rows.
    Links are left out: Google News redirect URLs are ~70% of the file; the
    dashboard searches the title instead."""
    by_day = defaultdict(list)
    for s in signals:
        by_day[s.as_of] += [{"date": s.as_of, "symbol": s.symbol, "published": n["published"],
                             "score": round(n["score"], 3), "weight": round(n["weight"], 3),
                             "title": n["title"], "source": n["source"]}
                            for n in s.headlines]
    for day, rows in by_day.items():
        if not rows:
            continue
        path = root / "headlines" / f"{day}.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        symbols = {r["symbol"] for r in rows}
        old = pd.read_csv(path) if path.exists() else pd.DataFrame()
        kept = [old[~old.symbol.isin(symbols)]] if len(old) else []
        merged = pd.concat([*kept, pd.DataFrame(rows)], ignore_index=True)
        merged.sort_values(["symbol", "published"], ascending=[True, False]).to_csv(path, index=False)


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
