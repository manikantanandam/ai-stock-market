"""Static dashboard of the journal and reports, published on GitHub Pages.

`build` copies every CSV under journal/ and reports/ into <out>/data/ and
writes data/manifest.json describing each file (group, kind, columns, rows).
The page in static/ reads the manifest and draws each file by its kind, so
new reports show up without changes here. Pages can't list directories,
hence the manifest.

Kinds: "day" (one scan session), "stock" (one stock's signal history),
"trades" (entry/exit rows), "series" (Date column + numeric columns, e.g.
equity curves), "yearly" (returns per calendar year), "table" (anything else).
"""
import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
STATIC = Path(__file__).parent / "static"

GROUPS = ["Signals", "Paper accounts", "Reports", "Stocks"]


def kind_of(rel: str, columns: list[str]) -> str:
    """Decide how the page draws a CSV, from its path and header."""
    parts = rel.split("/")
    name = parts[-1]
    if parts[:2] == ["journal", "days"]:
        return "day"
    if parts[:2] == ["journal", "stocks"]:
        return "stock"
    if {"entry_date", "ret"} <= set(columns):
        return "trades"
    if name.startswith("yearly_"):
        return "yearly"
    if columns and columns[0].lower() == "date" and len(columns) > 1:
        return "series"
    return "table"


def group_of(rel: str) -> str:
    parts = rel.split("/")
    if parts[:2] == ["journal", "days"]:
        return "Signals"
    if parts[:2] == ["journal", "stocks"]:
        return "Stocks"
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


def build(out: Path, root: Path = ROOT) -> dict:
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
    manifest = {"built": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "groups": GROUPS, "files": files, "accounts": accounts}
    (out / "data").mkdir(exist_ok=True)
    (out / "data" / "manifest.json").write_text(json.dumps(manifest))
    return manifest
