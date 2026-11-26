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
history to data/prices/<SYMBOL>.csv (OHLCV, indicators, the per-bar
technical score, and P/E, P/B, market cap and fundamental score estimated
by `fundamental.history`, since Yahoo has no fundamental history) and its
fiscal-year statement figures and ratios to data/fundamentals/<SYMBOL>.csv,
listing the symbols of each in the manifest, so a stock page shows more than
the logged sessions. News has no history to add.

`write_summaries` also gathers what a stock page would otherwise collect from every notes and paper-account
file: data/summaries/stocks/<SYMBOL>.json (its LLM notes, closed paper trades, open paper positions) and
data/summaries/contracts/<CONTRACT>.json (its notes), plus data/decisions/<date>.csv (every account's
decisions on that session's BUYs, with a strategy column). Pages then load a few small files however long
the journal gets.
"""
import csv
import json
import shutil
from collections import defaultdict
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


def _last_row(path: Path) -> dict:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[-1] if rows else {}


def _float(v: str | None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# Daily history columns: OHLCV, add_indicators' columns, the technical score, then the estimated fundamentals
INDICATORS = ["sma20", "sma50", "sma200", "ema100", "ema200", "rsi14", "macd_hist", "atr14", "vol_avg20"]
DAILY = ["Open", "High", "Low", "Close", "Volume", *INDICATORS, "Technical",
         "trailingPE", "priceToBook", "marketCap", "Fundamental"]


def write_history(out: Path, symbols: list[str], root: Path = ROOT) -> dict:
    """Per symbol, the full daily history (DAILY columns) into <out>/data/prices/ and the fiscal-year
    fundamentals into <out>/data/fundamentals/; returns the symbols written for each, by folder."""
    from aistock.data import fundamentals, prices
    from aistock.fundamental import history
    from aistock.technical.indicators import add_indicators, score_series

    for sub in ("prices", "fundamentals"):
        (out / "data" / sub).mkdir(parents=True, exist_ok=True)
    prices.prefetch(symbols, None)
    fundamentals.prefetch(symbols, fetch=fundamentals.get_statements)
    written = {"prices": [], "fundamentals": []}
    for sym in symbols:
        try:
            df = prices.get_prices(sym, None)
        except Exception as e:  # delisted or renamed symbols: the page falls back to journal closes
            print(f"prices: skipped {sym}: {e}")
            continue
        # pandas-ta returns None on series shorter than its window; the score needs SMA200 anyway
        if len(df) >= 200:
            df = add_indicators(df)
            df["Technical"] = score_series(df)
        try:
            years = fundamentals.get_statements(sym)
        except Exception as e:  # the chart keeps the logged sessions' fundamental scores
            print(f"statements: skipped {sym}: {e}")
            years = []
        foreign = {y.get("currency") for y in years} - {None, "INR"}
        for cur in foreign:  # e.g. INFY reports in USD
            try:
                years = history.to_inr(years, prices.get_prices(f"{cur}INR=X", None).Close)
            except Exception as e:  # amounts stay in the reporting currency, which the page labels
                print(f"fx: no {cur}INR for {sym}: {e}")
        last = _last_row(root / "journal" / "stocks" / f"{sym}.csv")
        now = {"trailingPE": _float(last.get("f_trailingPE")), "priceToBook": _float(last.get("f_priceToBook")),
               "close": _float(last.get("close"))}
        df = df.join(history.estimate(years, df.Close, now).rename(columns={"score": "Fundamental"}))
        df = df.reindex(columns=DAILY).round(2)
        for col in ("Volume", "vol_avg20", "marketCap"):  # whole numbers; ".0" on ~7000 rows adds up
            df[col] = df[col].round().astype("Int64")
        df.to_csv(
            out / "data" / "prices" / f"{sym}.csv", index_label="Date", date_format="%Y-%m-%d")
        written["prices"].append(sym)
        if years:
            history.yearly(years).round(4).to_csv(out / "data" / "fundamentals" / f"{sym}.csv", index=False)
            written["fundamentals"].append(sym)
    return written


def equal_weight(root: Path = ROOT) -> dict[str, float]:
    """The paper accounts' benchmark per session date: a daily-rebalanced equal-weight hold of the scanned
    stocks, as `backtest.portfolio.equal_weight` builds it for backtests (no costs), from the closes in
    journal/days: each session moves it by the mean close-to-close change of the stocks in both sessions."""
    out, prev, level = {}, None, 1.0
    for path in sorted((root / "journal" / "days").glob("*.csv")):
        with path.open(newline="") as f:
            close = {r["symbol"]: c for r in csv.DictReader(f) if (c := _float(r.get("close")))}
        if prev is not None:
            changes = [close[s] / prev[s] - 1 for s in close.keys() & prev.keys()]
            if changes:
                level *= 1 + sum(changes) / len(changes)
        out[path.stem] = round(level, 6)
        prev = close
    return out


def _records(path: Path) -> list[dict]:
    """A CSV's rows with numbers parsed (as the page's CSV parser does); NaN and infinity become null."""
    import numpy as np
    import pandas as pd

    if not path.exists() or path.stat().st_size == 0:
        return []
    df = pd.read_csv(path).replace([np.inf, -np.inf], np.nan)
    return json.loads(df.to_json(orient="records"))


def write_summaries(out: Path, root: Path = ROOT) -> dict:
    """Per-stock and per-contract JSON summaries and per-session decision files (see the module docstring);
    returns the manifest fields listing what was written."""
    import pandas as pd

    data, journal = out / "data", root / "journal"
    notes = defaultdict(list)  # (kind, symbol) -> notes rows
    for kind, folder in (("stocks", journal / "notes"), ("contracts", journal / "commodities" / "notes")):
        for path in sorted(folder.glob("*.csv")):
            for r in _records(path):
                notes[kind, r["symbol"]].append(r)
    trades, positions, decisions = defaultdict(list), defaultdict(list), defaultdict(list)
    for account in sorted(p for p in (journal / "paper").glob("*") if p.is_dir()):
        tag = {"strategy": account.name}
        for r in _records(account / "trades.csv"):
            trades[r["symbol"]].append(tag | r)
        for r in _records(account / "positions.csv"):
            positions[r["sym"]].append(tag | r)
        for r in _records(account / "decisions.csv"):
            decisions[r["signal_date"]].append(tag | r)
    written = {}
    for kind, folder in (("stocks", journal / "stocks"), ("contracts", journal / "commodities" / "contracts")):
        (data / "summaries" / kind).mkdir(parents=True, exist_ok=True)
        written[kind] = [p.stem for p in sorted(folder.glob("*.csv"))]
        for sym in written[kind]:
            summary = {"notes": notes[kind, sym]}
            if kind == "stocks":
                summary |= {"paper_trades": trades[sym], "positions": positions[sym]}
            (data / "summaries" / kind / f"{sym}.json").write_text(json.dumps(summary))
    (data / "decisions").mkdir(parents=True, exist_ok=True)
    for date, rows in decisions.items():
        pd.DataFrame(rows).to_csv(data / "decisions" / f"{date}.csv", index=False)
    return {"summaries": written, "decisions": sorted(decisions)}


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
                "groups": GROUPS, "files": files, "accounts": accounts, "equal_weight": equal_weight(root),
                **write_summaries(out, root),
                **(write_history(out, stocks, root) if prices else {"prices": [], "fundamentals": []})}
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "manifest.json").write_text(json.dumps(manifest))
    return manifest
