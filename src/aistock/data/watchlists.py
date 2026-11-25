"""Index constituent lists, snapshotted as CSVs in data/watchlists/.

NSE rebalances indices twice a year; refresh with `aistock update-watchlists`.
The CSVs are committed so scans are reproducible and CI never depends on
niftyindices.com being reachable.
"""
import csv
import urllib.request
from pathlib import Path

DIR = Path(__file__).parent / "watchlists"
SOURCES = {
    "nifty50": "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "nifty500": "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}


def _rows(name: str) -> list[dict]:
    with (DIR / f"{name}.csv").open() as fh:
        return list(csv.DictReader(fh))


def load(name: str) -> list[str]:
    return [row["Symbol"].strip() for row in _rows(name)]


def industries(name: str) -> dict[str, str]:
    """Symbol -> NSE industry (e.g. 'Information Technology')."""
    return {row["Symbol"].strip(): row["Industry"].strip() for row in _rows(name)}


def update() -> dict[str, int]:
    """Download fresh constituent CSVs; returns rows per list."""
    counts = {}
    for name, url in SOURCES.items():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        body = urllib.request.urlopen(req, timeout=30).read().decode()
        rows = list(csv.DictReader(body.splitlines()))
        if not rows or "Symbol" not in rows[0]:
            raise ValueError(f"Unexpected CSV from {url}")
        (DIR / f"{name}.csv").write_text(body)
        counts[name] = len(rows)
    return counts


WATCHLISTS = {name: load(name) for name in SOURCES}
