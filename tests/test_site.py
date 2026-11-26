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


def test_equal_weight_averages_close_changes_of_stocks_in_both_sessions(tmp_path):
    days = tmp_path / "journal" / "days"
    days.mkdir(parents=True)
    (days / "2026-11-23.csv").write_text("date,symbol,close\n2026-11-23,INFY,100\n2026-11-23,TCS,200\n")
    # INFY +10%, TCS -5%, NEW has no previous close and is left out
    (days / "2026-11-24.csv").write_text("date,symbol,close\n2026-11-24,INFY,110\n2026-11-24,TCS,190\n2026-11-24,NEW,50\n")
    (days / "2026-11-25.csv").write_text("date,symbol,close\n2026-11-25,INFY,110\n2026-11-25,TCS,190\n2026-11-25,NEW,55\n")

    ew = site.equal_weight(tmp_path)

    assert ew["2026-11-23"] == 1.0
    assert ew["2026-11-24"] == 1.025
    assert ew["2026-11-25"] == round(1.025 * (1 + 0.1 / 3), 6)


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
    statements = [{"date": "2001-03-31", "revenue": 10.0, "net_income": 1.0, "eps": 1.0, "equity": 5.0, "debt": 1.0,
                   "current_assets": None, "current_liabilities": None, "shares": 2.0}]
    monkeypatch.setattr(fundamentals, "get_statements", lambda sym: statements)
    monkeypatch.setattr(prices, "get_prices", fake_get)
    out = tmp_path / "site"

    manifest = site.build(out, root=root, prices=True)

    assert manifest["prices"] == manifest["fundamentals"] == ["INFY"]  # GONE: no prices, falls back to journal closes
    assert asked == [None, None]  # full history
    daily = pd.read_csv(out / "data" / "prices" / "INFY.csv", index_col="Date", parse_dates=True)
    assert list(daily.columns) == site.DAILY
    assert daily.Close.iloc[:2].tolist() == [1.23, 2.0]
    assert daily.Technical.iloc[:199].isna().all()  # no score before SMA200
    assert daily.Technical.iloc[199:].tolist() == score_series(add_indicators(frame)).round(2).iloc[199:].tolist()
    assert daily.marketCap.iloc[-1] == 2.0 * close[-1]  # shares x close from the fiscal year in effect
    yearly = pd.read_csv(out / "data" / "fundamentals" / "INFY.csv")
    assert yearly.loc[0, ["date", "profitMargins", "returnOnEquity"]].tolist() == ["2001-03-31", 0.1, 0.2]
