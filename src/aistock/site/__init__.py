"""Static dashboard of the journal and reports, published on GitHub Pages.

`build` copies every CSV under journal/ and reports/ into <out>/data/ and
writes data/manifest.json describing each file (group, kind, columns, rows).
The page in static/ reads the manifest and draws each file by its kind, so
new reports show up without changes here. Pages can't list directories,
hence the manifest.

Kinds: "day" (one scan session), "notes" (LLM notes on a session's BUYs),
"stock" (one stock's signal history), "cday" / "cnotes" / "contract" (the
same for commodity futures, under journal/commodities/; a session's cnotes
are drawn on its cday page), "trades" (entry/exit rows), "series"
(Date column + numeric columns, e.g. equity curves), "yearly" (returns per
calendar year), "table" (anything else).

With prices=True, `build` also writes each journaled stock's full Yahoo
history (Date,Close,Technical: the per-bar technical score, blank until
SMA200 exists) to data/prices/<SYMBOL>.csv and lists those symbols in the
manifest, so a stock page charts more than the logged sessions. There is no
fundamental or news history to add: Yahoo only serves today's values.
"""
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).parent / "static"

# Nav sections. Files whose group isn't listed get no nav entry: LLM notes and headlines are
# drawn on their session's page, and the ~500 per-stock pages are reached through the
# search box at the top of Stocks.
GROUPS = ["Stocks", "Commodities", "Paper accounts", "Reports", "Contracts"]
COMMODITY = {"days": ("cday", "Commodities"), "notes": ("cnotes", None), "headlines": ("cheadlines", None),
             "contracts": ("contract", "Contracts")}  # journal/commodities/<sub>: (kind, group)


def kind_of(rel: str, columns: list[str]) -> str:
    """Decide how the page draws a CSV, from its path and header."""
    parts = rel.split("/")
    name = parts[-1]
    if parts[:2] == ["journal", "commodities"] and len(parts) == 4 and parts[2] in COMMODITY:
        return COMMODITY[parts[2]][0]
    if parts[:2] == ["journal", "days"]:
        return "day"
    if parts[:2] == ["journal", "stocks"]:
        return "stock"
    if parts[:2] == ["journal", "notes"]:
        return "notes"
    if {"entry_date", "ret"} <= set(columns):
        return "trades"
    if name.startswith("yearly_"):
        return "yearly"
    if columns and columns[0].lower() == "date" and len(columns) > 1:
        return "series"
    return "table"


def group_of(rel: str) -> str | None:
    parts = rel.split("/")
    if parts[:2] == ["journal", "commodities"] and len(parts) == 4 and parts[2] in COMMODITY:
        return COMMODITY[parts[2]][1]
    if parts[:2] == ["journal", "days"]:
        return "Stocks"
    if parts[:2] == ["journal", "stocks"]:
        return "Stock history"
    if parts[:2] == ["journal", "notes"]:
        return None
    if parts[:2] == ["journal", "paper"]:
        return "Paper accounts"
    return "Reports"


def title_of(rel: str) -> str:
    parts = rel.split("/")
    stem = parts[-1].removesuffix(".csv")
    if parts[:2] == ["journal", "paper"] and len(parts) == 4:
        return f"{parts[2]} · {stem}"
    return stem.replace("_", " ")


def _describe(path: Path, rel: str) -> dict:
    with path.open(newline="") as f:
        reader = csv.reader(f)
        columns = next(reader, [])
        rows = sum(1 for _ in reader)
    return {"path": rel, "group": group_of(rel), "kind": kind_of(rel, columns),
            "title": title_of(rel), "columns": columns, "rows": rows}


def write_prices(out: Path, symbols: list[str]) -> list[str]:
    """Full daily close and technical score history per symbol into <out>/data/prices/;
    returns the symbols written."""
    from aistock.data import prices
    from aistock.technical.indicators import add_indicators, score_series

    (out / "data" / "prices").mkdir(parents=True, exist_ok=True)
    prices.prefetch(symbols, None)
    written = []
    for sym in symbols:
        try:
            df = prices.get_prices(sym, None)
        except Exception as e:  # delisted or renamed symbols: the page falls back to journal closes
            print(f"prices: skipped {sym}: {e}")
            continue
        # pandas-ta returns None on series shorter than its window; the score needs SMA200 anyway
        df = df.assign(Technical=score_series(add_indicators(df)) if len(df) >= 200 else float("nan"))
        df[["Close", "Technical"]].round(2).to_csv(out / "data" / "prices" / f"{sym}.csv", index_label="Date", date_format="%Y-%m-%d")
        written.append(sym)
    return written


def build(out: Path, root: Path = ROOT, prices: bool = False) -> dict:
    """Write the site to `out`; returns the manifest."""
    shutil.rmtree(out / "data", ignore_errors=True)  # drop CSVs that no longer exist
    shutil.copytree(STATIC, out, dirs_exist_ok=True)
    files = []
    for sub in ("journal", "reports"):
        for path in sorted((root / sub).rglob("*.csv")) if (root / sub).exists() else []:
            rel = path.relative_to(root).as_posix()
            dest = out / "data" / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, dest)
            files.append(_describe(path, rel))
    accounts = {}
    for cfg in sorted((root / "journal" / "paper").glob("*/account.json")):
        accounts[cfg.parent.name] = json.loads(cfg.read_text())
    stocks = [f["title"] for f in files if f["kind"] == "stock"]
    manifest = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "groups": GROUPS, "files": files, "accounts": accounts,
                "prices": write_prices(out, stocks) if prices else []}
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "manifest.json").write_text(json.dumps(manifest))
    return manifest
