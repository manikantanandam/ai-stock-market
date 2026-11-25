import json

from aistock import site


def test_kind_of_routes_by_path_and_header():
    assert site.kind_of("journal/days/2026-11-23.csv", ["date", "symbol"]) == "day"
    assert site.kind_of("journal/stocks/INFY.csv", ["date", "symbol"]) == "stock"
    assert site.kind_of("reports/paper_trades.csv", ["strategy", "entry_date", "ret"]) == "trades"
    assert site.kind_of("journal/paper/trend/trades.csv", ["symbol", "entry_date", "ret"]) == "trades"
    assert site.kind_of("reports/yearly_nifty500_2026-11-25.csv", ["Date", "breakout"]) == "yearly"
    assert site.kind_of("reports/compare_nifty50_2026-11-25.csv", ["Date", "trend In-sample"]) == "series"
    assert site.kind_of("journal/paper/trend/equity.csv", ["date", "cash", "equity"]) == "series"
    assert site.kind_of("reports/sweep_nifty500_2026-11-25.csv", ["rule", "regime"]) == "table"


def test_build_copies_csvs_and_writes_manifest(tmp_path):
    root = tmp_path / "repo"
    (root / "journal" / "days").mkdir(parents=True)
    (root / "journal" / "days" / "2026-11-23.csv").write_text("date,symbol,action\n2026-11-23,INFY,BUY\n2026-11-23,TCS,WATCH\n")
    (root / "journal" / "paper" / "trend").mkdir(parents=True)
    (root / "journal" / "paper" / "trend" / "account.json").write_text('{"capital": 100000, "slots": 10}')
    (root / "journal" / "paper" / "trend" / "equity.csv").write_text("date,cash,invested,equity\n")
    out = tmp_path / "site"

    manifest = site.build(out, root=root)

    assert (out / "index.html").exists() and (out / "app.js").exists()
    assert (out / "data" / "journal" / "days" / "2026-11-23.csv").exists()
    assert json.loads((out / "data" / "manifest.json").read_text()) == manifest
    day, equity = manifest["files"]
    assert (day["kind"], day["group"], day["rows"]) == ("day", "Signals", 2)
    assert (equity["group"], equity["title"]) == ("Paper accounts", "trend · equity")
    assert manifest["accounts"] == {"trend": {"capital": 100000, "slots": 10}}
