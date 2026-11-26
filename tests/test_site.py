import json

from aistock import site


def test_kind_of_routes_by_path_and_header():
    assert site.kind_of("journal/days/2026-11-23.csv", ["date", "symbol"]) == "day"
    assert site.kind_of("journal/stocks/INFY.csv", ["date", "symbol"]) == "stock"
    assert site.kind_of("journal/notes/2026-11-23.csv", ["date", "symbol", "note"]) == "notes"
    assert site.group_of("journal/notes/2026-11-23.csv") is None  # drawn on the session page
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
    assert (day["kind"], day["group"], day["rows"]) == ("day", "Stocks", 2)
    assert (equity["group"], equity["title"]) == ("Paper accounts", "trend · equity")
    assert manifest["accounts"] == {"trend": {"capital": 100000, "slots": 10}}


def test_build_writes_full_price_history_per_stock(tmp_path, monkeypatch):
    import pandas as pd

    from aistock.data import fundamentals, prices
    from aistock.technical.indicators import add_indicators, score_series

    root = tmp_path / "repo"
    (root / "journal" / "stocks").mkdir(parents=True)
    for sym in ("INFY", "GONE"):
        (root / "journal" / "stocks" / f"{sym}.csv").write_text(f"date,symbol,close\n2026-11-23,{sym},1\n")
    close = [1.234, 2.0] + [100.0 + i for i in range(250)]
    frame = pd.DataFrame({"Open": close, "High": close, "Low": close, "Close": close, "Volume": 1000.0},
                         index=pd.bdate_range("2001-01-02", periods=len(close)))
    asked = []

    def fake_get(sym, years):
        asked.append(years)
        if sym == "GONE":
            raise ValueError("no price data")
        return frame.copy()

    monkeypatch.setattr(prices, "prefetch", lambda symbols, years: None)
    monkeypatch.setattr(fundamentals, "prefetch", lambda symbols, fetch: None)
    monkeypatch.setattr(fundamentals, "get_statements", lambda sym: [])
    monkeypatch.setattr(prices, "get_prices", fake_get)
    out = tmp_path / "site"

    manifest = site.build(out, root=root, prices=True)

    assert manifest["prices"] == ["INFY"]  # GONE has no data and falls back to journal closes
    assert asked == [None, None]  # full history
    lines = (out / "data" / "prices" / "INFY.csv").read_text().splitlines()
    assert lines[:3] == ["Date,Close,Technical,Fundamental", "2001-01-02,1.23,,", "2001-01-03,2.0,,"]  # no score before SMA200
    expected = score_series(add_indicators(frame)).round(2)
    assert [float(x.split(",")[2]) for x in lines[200:]] == expected.iloc[199:].tolist()
