from dataclasses import replace
from datetime import date

import numpy as np
import pandas as pd
import typer
from rich import print
from rich.progress import track
from rich.table import Table

from aistock.backtest import engine, portfolio, sweep, universe, walkforward
from aistock.commodity.cli import app as commodity_app
from aistock.data import fundamentals, prices, sectors, watchlists
from aistock.data.prices import get_prices, market_phase
from aistock.data.watchlists import WATCHLISTS
from aistock.llm import backfill
from aistock.llm import notes as llm_notes
from aistock.ml import features as ml_features
from aistock.ml import model as ml_model
from aistock.strategy import account, journal, signal, strategies
from aistock.strategy import paper as paper_trading
from aistock.strategy.strategies import PRESETS, Strategy
from aistock.technical import rules
from aistock.technical.indicators import add_indicators

app = typer.Typer(help="Local AI stock assistant (NSE swing trading)", no_args_is_help=True)
app.add_typer(commodity_app, name="commodity")


@app.callback()
def main():
    """Local AI stock assistant."""


COLORS = {"BUY": "green", "WATCH": "yellow", "AVOID": "red"}


def _strategy_list(names: str) -> list[Strategy]:
    """'all' or a comma-separated list of preset names."""
    if names == "all":
        return list(PRESETS.values())
    try:
        return [strategies.get(n.strip()) for n in names.split(",") if n.strip()]
    except ValueError as e:
        raise typer.BadParameter(str(e))


@app.command("strategies")
def list_strategies():
    """List the strategy presets."""
    table = Table(title="Strategy presets")
    for col in ["Name", "Entry and exits", "Live gates", "Description"]:
        table.add_column(col)
    for st in PRESETS.values():
        gates = [f"combined >= {st.min_combined}" if st.min_combined is not None else "",
                 f"fundamental >= {st.min_fundamental}" if st.min_fundamental is not None else "",
                 "no news veto"]
        table.add_row(st.name, st.describe(), ", ".join(g for g in gates if g), st.description)
    print(table)


@app.command()
def analyze(
    symbol: str,
    capital: float = typer.Option(100_000, help="Account size in INR, for position sizing"),
    no_news: bool = typer.Option(False, "--no-news", help="Skip news/FinBERT"),
    strategy: str = typer.Option("all", "--strategy", "-s", help="'all' or comma-separated preset names"),
    notes: bool = typer.Option(False, "--notes", help="Add a local-LLM (Ollama) note explaining each BUY"),
):
    """Technical + fundamental + news scores for one stock, and each strategy's verdict."""
    s = signal.evaluate(symbol, signal.Config(capital=capital), news="none" if no_news else "all",
                        strategies=_strategy_list(strategy))
    print(f"[bold]{s.symbol}[/bold] {s.name} ({s.sector})  close {s.close:.2f}  ATR {s.atr:.2f}  as of {s.as_of}")
    print(f"[bold {COLORS[s.action]}]{s.action}[/]  combined {s.combined:+.2f}")
    for part, value in [("technical", s.technical), ("fundamental", s.fundamental), ("sentiment", s.sentiment)]:
        print(f"\n[bold]{part.capitalize()}[/bold] {_score(value)}")
        for w in s.reasons[part]:
            print(f" - {w}")

    table = Table(title="Strategies")
    for col in ["Strategy", "Action", "Stop", "Target", "Qty", "Why"]:
        table.add_column(col, justify="right" if col in ("Stop", "Target", "Qty") else "left")
    for r in s.strategies.values():
        table.add_row(r.strategy, f"[{COLORS[r.action]}]{r.action}[/]", f"{r.stop:.2f}",
                      "-" if r.target is None else f"{r.target:.2f}", str(r.shares), "; ".join(r.why))
    print(table)
    if notes:
        _print_notes([(s, name) for name in s.buys])


def _print_notes(items: list[tuple]) -> list[tuple]:
    """LLM notes for (signal, strategy) pairs; stops at the first sign Ollama is unavailable.
    Returns the (signal, strategy, note) triples written."""
    if not items:
        print("No BUY to explain.")
        return []
    written = []
    for s, name in track(items, description="Writing notes", transient=True):
        try:
            text = llm_notes.note(s, name)
        except llm_notes.Unavailable as e:
            print(f"[yellow]Notes skipped: {e}. Run `brew services start ollama` and "
                  f"`ollama pull {llm_notes.MODEL}`.[/yellow]")
            break
        print(f"\n[bold]{s.symbol} · {name}[/bold]: {text}")
        written.append((s, name, text))
    return written


@app.command("paper-account")
def paper_account(
    strategy: str = typer.Option("all", "--strategy", "-s", help="'all' or comma-separated preset names"),
    capital: float = typer.Option(100_000, help="Starting capital for new accounts"),
    positions: int = typer.Option(10, help="Slots for new accounts; each buy gets 1/N of equity"),
    show: bool = typer.Option(False, help="List open positions"),
):
    """Advance each strategy's paper account (shared capital, N slots) to the latest session and save it."""
    only = None if strategy == "all" else [st.name for st in _strategy_list(strategy)]
    accounts = account.update(only, capital, positions)
    if not accounts:
        print("No BUY signals in the journal yet. Run `aistock scan` daily to build history.")
        return
    nifty = paper_trading.session_prices(paper_trading.INDEX).Close
    table = Table(title=f"Paper accounts, saved in {account.ROOT}")
    for col in ["Strategy", "Since", "Sessions", "Equity", "Return", "NIFTY", "Max DD", "Open", "Closed", "Win"]:
        table.add_column(col, justify="left" if col in ("Strategy", "Since") else "right")
    for a in accounts:
        r = account.summary(a, nifty)
        table.add_row(r["strategy"], r["since"], str(r["sessions"]), f"{r['equity']:,.0f}", pct(r["return"]),
                      pct(r["nifty_return"]), pct(r["max_drawdown"]), str(r["open"]), str(r["closed"]),
                      "-" if r["win_rate"] is None else pct(r["win_rate"]))
    print(table)
    for a in accounts:
        for note in a.notes:
            print(f"[yellow]{a.strategy}: {note}[/yellow]")
    if show:
        for a in accounts:
            for pos in a.positions.values():
                print(f"{a.strategy}: {pos.sym} {pos.shares:g} @ {pos.entry_px:.2f}, stop {pos.stop:.2f}, "
                      f"target {'-' if pos.target == float('inf') else f'{pos.target:.2f}'}")


@app.command()
def scan(
    watchlist: str = typer.Option("nifty50", help=f"One of: {', '.join(WATCHLISTS)}"),
    symbols: str = typer.Option("", help="Comma-separated symbols instead of a watchlist"),
    strategy: str = typer.Option("all", "--strategy", "-s", help="'all' or comma-separated preset names"),
    capital: float = typer.Option(100_000),
    news: str = typer.Option("auto", help="all, candidates (only stocks a strategy could BUY), none; "
                                          "auto = all for <= 60 stocks, else candidates"),
    top: int = typer.Option(15, help="Rows to show"),
    notes: bool = typer.Option(False, "--notes", help="Add a local-LLM (Ollama) note for each BUY shown"),
    notes_max: int = typer.Option(15, help="Most BUYs to explain with --notes (each takes ~30s on CPU)"),
):
    """Run every strategy over a watchlist, list BUYs first, and log to journal/ (day and stock files)."""
    names = [x.strip().upper() for x in symbols.split(",") if x.strip()] or WATCHLISTS[watchlist]
    chosen = _strategy_list(strategy)
    if news == "auto":
        news = "all" if len(names) <= 60 else "candidates"
    if news not in signal.NEWS_MODES:
        raise typer.BadParameter(f"news must be auto or one of {signal.NEWS_MODES}")
    # The phase when prices are downloaded decides logging: a scan started before the
    # 15:45 IST cutoff holds partial bars even if it finishes after it
    phase = market_phase()
    print(f"Downloading prices and fundamentals for {len(names)} stocks...")
    prices.prefetch(names, years=2)
    fundamentals.prefetch(names)
    market = signal.market_ok()
    print(f"Market filter: NIFTY 50 {'above' if market else 'BELOW'} its 200-day SMA")
    cfg, results, failed = signal.Config(capital=capital), [], []
    for sym in track(names, description="Scanning"):
        try:
            results.append(signal.evaluate(sym, cfg, news=news, strategies=chosen, market=market))
        except Exception as e:  # one bad ticker shouldn't stop the scan
            failed.append(f"{sym} ({e})")
    if not results:
        print("[red]No symbols could be evaluated[/red]")
        raise typer.Exit(1)
    # Mid-session bars are partial, so only completed sessions go in the journal
    logged = f"logged to {journal.record(results)}" if phase != "live" else "not logged (started while the market was open)"
    results.sort(key=lambda s: (len(s.buys), s.combined), reverse=True)

    table = Table(title=f"Scan: {len(results)} stocks, as of {results[0].as_of}")
    for col in ["Symbol", "Action", "Strategies", "Combined", "Tech", "Fund", "News", "Close", "Stop", "Target", "Qty"]:
        table.add_column(col, justify="left" if col in ("Symbol", "Action", "Strategies") else "right")
    for s in results[:top]:
        # stop/target shown for the first strategy that says BUY (preset order), else the first chosen
        r = s.strategies[s.buys[0] if s.buys else chosen[0].name]
        table.add_row(s.symbol, f"[{COLORS[s.action]}]{s.action}[/]", ", ".join(s.buys) or "-",
                      f"{s.combined:+.2f}", f"{s.technical:+.2f}", f"{s.fundamental:+.2f}", _score(s.sentiment),
                      f"{s.close:.2f}", f"{r.stop:.2f}", "-" if r.target is None else f"{r.target:.2f}",
                      str(r.shares))
    print(table)
    per_strategy = "  ".join(f"{st.name} {sum(st.name in s.buys for s in results)}" for st in chosen)
    counts = {a: sum(s.action == a for s in results) for a in COLORS}
    with_news = sum(s.sentiment is not None for s in results)
    print(f"BUY by strategy: {per_strategy}")
    print(f"Stocks: BUY {counts['BUY']}  WATCH {counts['WATCH']}  AVOID {counts['AVOID']}  · "
          f"news for {with_news}/{len(results)} · {logged}")
    if notes:
        written = _print_notes([(s, s.buys[0]) for s in results[:top] if s.buys][:notes_max])
        if written and phase != "live":
            journal.record_notes(written)
            print(f"Notes logged to {journal.NOTES}")
    _print_skipped(failed)


@app.command("backfill-notes")
def backfill_notes(
    date_: str = typer.Option(None, "--date", help="One session (YYYY-MM-DD); default: every session without notes"),
    sessions: int = typer.Option(None, help="Most sessions to fill this run, newest first"),
    notes_max: int = typer.Option(25, help="Most BUYs to explain per session"),
):
    """Write LLM notes for journaled sessions that have none, from their journal rows."""
    days = [date_] if date_ else backfill.sessions_without_notes()[:sessions]
    if not days:
        print("Every journaled session has notes.")
        return
    for day in days:
        print(f"[bold]{day}[/bold]")
        written = _print_notes([(s, s.buys[0]) for s in backfill.session_signals(day, notes_max)])
        if not written:
            break  # Ollama unavailable, or nothing to explain
        journal.record_notes(written, source="backfill")
        print(f"{len(written)} notes logged to {journal.NOTES / f'{day}.csv'}")


@app.command()
def paper(
    strategy: str = typer.Option("all", "--strategy", "-s", help="'all' or comma-separated preset names"),
    since: str = typer.Option(None, help="Only signals on/after this date (YYYY-MM-DD)"),
    notional: float = typer.Option(100_000, help="INR per trade (costs include a flat DP charge)"),
    show: int = typer.Option(15, help="Open and recently closed trades to list"),
):
    """Paper-trade the journal's BUY signals forward: the bias-free scorecard for each strategy."""
    only = None if strategy == "all" else [st.name for st in _strategy_list(strategy)]
    trades, pending = paper_trading.run(since, only, notional)
    if trades.empty and pending.empty:
        print("No BUY signals in the journal yet. Run `aistock scan` daily to build history.")
        return
    summ = paper_trading.summary(trades, pending)
    opt_pct = lambda v: "-" if v is None or pd.isna(v) else pct(v)
    table = Table(title=f"Paper trading · {notional:,.0f} INR per trade · after costs")
    for col in ["Strategy", "Pending", "Open", "Closed", "Win rate", "Avg closed", "Avg open",
                "Avg all", "vs NIFTY", "Avg days"]:
        table.add_column(col, justify="left" if col == "Strategy" else "right")
    for r in summ.to_dict("records"):
        table.add_row(r["strategy"], str(r["pending"]), str(r["open"]), str(r["closed"]), opt_pct(r["win_rate"]),
                      opt_pct(r["avg_closed"]), opt_pct(r["avg_open"]), opt_pct(r["avg_all"]),
                      opt_pct(r["excess_vs_nifty"]),
                      "-" if pd.isna(r["avg_days"]) else f"{r['avg_days']:.1f}")
    print(table)

    if not trades.empty:
        for status, sort_col, title in [("open", "ret", "Open positions (best first)"),
                                        ("closed", "exit_date", "Recently closed")]:
            t = trades[trades.status == status].sort_values(sort_col, ascending=False).head(show)
            if t.empty:
                continue
            tbl = Table(title=title)
            price_col = "last" if status == "open" else "exit"
            for col in ["Strategy", "Symbol", "Entry date", "Entry", price_col.capitalize(),
                        "Stop", "Target", "Days", "Return", "vs NIFTY"] + (["Exit"] if status == "closed" else []):
                tbl.add_column(col, justify="left" if col in ("Strategy", "Symbol", "Exit") else "right")
            for r in t.to_dict("records"):
                tbl.add_row(r["strategy"], r["symbol"], r["entry_date"], f"{r['entry']:.2f}",
                            f"{r[price_col]:.2f}", f"{r['stop']:.2f}",
                            "-" if r["target"] is None or pd.isna(r["target"]) else f"{r['target']:.2f}",
                            str(r["days"]), pct(r["ret"]), pct(r["excess"]),
                            *([f"{r['reason']} {r['exit_date']}"] if status == "closed" else []))
            print(tbl)
        universe.REPORTS.mkdir(exist_ok=True)
        path = universe.REPORTS / "paper_trades.csv"
        trades.to_csv(path, index=False)
        print(f"All trades saved to {path}")
    if not pending.empty:
        print(f"{len(pending)} signals waiting for the next session's open")


@app.command()
def site(
    out: str = typer.Option("site", help="Output folder"),
    serve: bool = typer.Option(False, "--serve", help="Serve the site on localhost after building"),
    port: int = typer.Option(8000, help="Port for --serve"),
    prices: bool = typer.Option(True, "--prices/--no-prices", help="Download each stock's full price history"),
):
    """Build the dashboard (journal + reports CSVs with charts) as a static site."""
    from pathlib import Path

    from aistock import site as dashboard

    manifest = dashboard.build(Path(out), prices=prices)
    print(f"Built {out}/ with {len(manifest['files'])} CSV files")
    if serve:
        import functools
        import http.server

        handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=out)
        print(f"Serving http://localhost:{port} (Ctrl+C to stop)")
        http.server.ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()


@app.command("pr-screenshots")
def pr_screenshots(
    pr: int = typer.Argument(..., help="Pull request number"),
    site_dir: str = typer.Option("site", "--site", help="Built site folder (aistock site)"),
    page: list[str] = typer.Option(None, "--page", help="Page hash to capture, e.g. today (repeatable); "
                                                        "default: the PR body's <!-- screenshots: ... --> line, else a standard set"),
):
    """Screenshot dashboard pages and put them in the PR description (images on the pr-screenshots branch)."""
    from pathlib import Path

    from aistock.site import screenshots

    print(screenshots.publish(pr, Path(site_dir), Path.cwd(), page or None))


@app.command("update-watchlists")
def update_watchlists():
    """Refresh index constituent CSVs from niftyindices.com (NSE rebalances twice a year)."""
    for name, n in watchlists.update().items():
        print(f"{name}: {n} stocks")
    print("Review with git diff, then commit src/aistock/data/watchlists/")


def _score(v: float | None) -> str:
    return "-" if v is None else f"{v:+.2f}"


def _print_skipped(skipped: list[str], limit: int = 10):
    if skipped:
        more = f" (+{len(skipped) - limit} more)" if len(skipped) > limit else ""
        print(f"[yellow]Skipped {len(skipped)}: {', '.join(skipped[:limit])}{more}[/yellow]")


pct = lambda v: f"{v * 100:.1f}%"
num = lambda v: f"{v:.2f}"

# Shared backtest options: --strategy picks a preset; the rest override it when given.
STRATEGY = typer.Option(strategies.DEFAULT, "--strategy", "-s", help=f"One of: {', '.join(PRESETS)}")
YEARS = typer.Option(10, help="Years of daily history")
RULE = typer.Option(None, help=f"Override entry rule: score:<threshold>, {', '.join(rules.RULES)}")
REGIME = typer.Option(None, "--regime/--no-regime", help="Override the NIFTY 50 > SMA200 market filter")
RS = typer.Option(None, "--rs/--no-rs", help="Override the filter: stock beat NIFTY 50 over 3 and 6 months")
SECTOR = typer.Option(None, "--sector/--no-sector", help="Override the filter: sector index above its SMA50")
STOP_ATR = typer.Option(None, help="Override stop distance in ATR")
TARGET_ATR = typer.Option(None, help="Override target in ATR; 0 = no fixed target")
TRAIL_ATR = typer.Option(None, help="Override trailing stop in ATR; 0 = off")
MAX_HOLD = typer.Option(None, help="Override max days in a trade")
OOS = typer.Option(0.3, help="Fraction of history held out as out-of-sample")
WATCHLIST = typer.Option("nifty50", help=f"One of: {', '.join(WATCHLISTS)}")

POOLED_ROWS = [("stocks", str), ("trades", str), ("win_rate", pct), ("avg_trade", pct), ("median_trade", pct),
               ("profit_factor", num), ("avg_bars", lambda v: f"{v:.1f}"), ("exit_stop", pct),
               ("exit_target", pct), ("exit_time", pct), ("median_cagr", pct), ("median_buy_hold_cagr", pct),
               ("median_max_dd", pct), ("median_sharpe", num), ("exposure", pct),
               ("profitable_stocks", pct), ("beat_buy_hold", pct)]


def _resolve(strategy, rule, regime, rs, sector, stop_atr, target_atr, trail_atr, max_hold) -> Strategy:
    """The preset with any CLI overrides applied (renamed '<preset>*' if changed)."""
    try:
        st = strategies.get(strategy)
    except ValueError as e:
        raise typer.BadParameter(str(e))
    overrides = {k: v for k, v in {"rule": rule, "regime": regime, "rs": rs, "sector": sector,
                                   "stop_atr": stop_atr, "max_hold": max_hold,
                                   "target_atr": target_atr, "trail_atr": trail_atr}.items() if v is not None}
    for k in ("target_atr", "trail_atr"):  # 0 means off
        if overrides.get(k) == 0:
            overrides[k] = None
    return replace(st, name=f"{st.name}*", **overrides) if overrides else st


def _metric_table(title: str, columns: dict[str, dict], rows: list[tuple]) -> Table:
    table = Table(title=title)
    table.add_column("Metric")
    for name in columns:
        table.add_column(name, justify="right")
    for key, fmt in rows:
        table.add_row(key, *(fmt(c[key]) if key in c else "-" for c in columns.values()))
    return table


@app.command()
def backtest(symbol: str, strategy: str = STRATEGY, years: int = YEARS, rule: str = RULE, regime: bool = REGIME,
             rs: bool = RS, sector: bool = SECTOR, stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR, trail_atr: float = TRAIL_ATR,
             max_hold: int = MAX_HOLD, oos: float = OOS):
    """Backtest a strategy on one stock with Indian delivery costs."""
    st = _resolve(strategy, rule, regime, rs, sector, stop_atr, target_atr, trail_atr, max_hold)
    symbol = symbol.upper()
    data, skipped = universe.load([symbol], years)
    if not data:
        print(f"[red]{skipped[0]}[/red]")
        raise typer.Exit(1)
    entries = universe.strategy_entries(data, st, years)
    ind = data[symbol]
    runs = {seg: engine.run(i, e, st.params()).stats
            for seg, (i, e) in universe.segments(ind, entries[symbol], oos).items()}
    rows = [("trades", str), ("win_rate", pct), ("avg_trade_pct", pct), ("total_return", pct),
            ("cagr", pct), ("max_drawdown", pct), ("sharpe", num), ("exposure", pct), ("buy_hold", pct)]
    split = ind.index[int(len(ind) * (1 - oos))]
    print(_metric_table(f"{symbol} · {st.name}: {st.describe()}\n"
                        f"{ind.index[0]:%Y-%m-%d} to {ind.index[-1]:%Y-%m-%d}, split {split:%Y-%m-%d}", runs, rows))


@app.command("backtest-all")
def backtest_all(watchlist: str = WATCHLIST, strategy: str = STRATEGY, years: int = YEARS, rule: str = RULE,
                 regime: bool = REGIME, rs: bool = RS, sector: bool = SECTOR, stop_atr: float = STOP_ATR,
                 target_atr: float = TARGET_ATR, trail_atr: float = TRAIL_ATR, max_hold: int = MAX_HOLD,
                 oos: float = OOS):
    """Backtest a strategy on every stock in a watchlist; per-stock results saved to reports/."""
    st = _resolve(strategy, rule, regime, rs, sector, stop_atr, target_atr, trail_atr, max_hold)
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    entries = universe.strategy_entries(data, st, years)
    stats, trades = universe.run_universe(data, entries, st.params(), oos)
    if trades.empty:
        print("[red]No trades generated[/red]")
        raise typer.Exit(1)
    path = universe.save(stats, trades, watchlist)
    pooled = {seg: universe.pooled(stats, trades, seg) for seg in universe.SEGMENTS}
    print(_metric_table(f"{watchlist} · {st.name}: {st.describe()}", pooled, POOLED_ROWS))

    oos_stats = stats[stats.segment == "Out-of-sample"].sort_values("total_return")
    show = lambda d: ", ".join(f"{r.symbol} {r.total_return * 100:+.0f}%" for r in d.itertuples())
    print(f"OOS best:  {show(oos_stats.tail(5).iloc[::-1])}")
    print(f"OOS worst: {show(oos_stats.head(5))}")
    print(f"Saved per-stock stats and trades next to {path}")
    _print_skipped(skipped)


def _portfolio_runs(data, index, st: Strategy, positions: int, oos: float,
                   years: int) -> tuple[dict, list[pd.Series]]:
    """Portfolio backtest per segment; returns ({segment label: stats}, equity curves)."""
    entries = universe.strategy_entries(data, st, years, rules.market_regime(index))
    dates = index.index[index.sma200.notna()]  # start once SMA200 is warm everywhere
    split = int(len(dates) * (1 - oos))
    columns, curves = {}, []
    for seg, d in zip(universe.SEGMENTS, (dates[:split], dates[split:])):
        eq, invested, trades = portfolio.run(data, entries, d, st.params(), positions, st.rank)
        columns[f"{seg} ({d[0]:%Y-%m} to {d[-1]:%Y-%m})"] = portfolio.stats(
            eq, invested, trades, index.Close, portfolio.equal_weight(data, d))
        curves.append(eq.rename(f"{st.name} {seg}"))
    return columns, curves


POSITIONS = typer.Option(10, help="Max concurrent positions; each gets 1/N of equity")


@app.command("portfolio")
def portfolio_cmd(watchlist: str = WATCHLIST, strategy: str = STRATEGY, years: int = YEARS, rule: str = RULE,
                  regime: bool = REGIME, rs: bool = RS, sector: bool = SECTOR, stop_atr: float = STOP_ATR,
                  target_atr: float = TARGET_ATR, trail_atr: float = TRAIL_ATR, max_hold: int = MAX_HOLD,
                  oos: float = OOS, positions: int = POSITIONS):
    """Shared-capital portfolio backtest of a strategy, vs NIFTY 50 and an equal-weight hold of the watchlist."""
    st = _resolve(strategy, rule, regime, rs, sector, stop_atr, target_atr, trail_atr, max_hold)
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    index = add_indicators(get_prices(universe.INDEX, years))
    columns, curves = _portfolio_runs(data, index, st, positions, oos, years)
    rows = [("cagr", pct), ("nifty_cagr", pct), ("universe_cagr", pct), ("excess_cagr", pct),
            ("excess_vs_universe", pct), ("max_drawdown", pct), ("nifty_max_drawdown", pct),
            ("universe_max_drawdown", pct), ("sharpe", num), ("nifty_sharpe", num), ("exposure", pct),
            ("trades", str), ("trades_per_year", lambda v: f"{v:.0f}"), ("win_rate", pct),
            ("avg_trade", pct), ("profit_factor", num), ("avg_bars", lambda v: f"{v:.1f}")]
    print(_metric_table(f"{watchlist} portfolio, {positions} slots · {st.name}: {st.describe()}", columns, rows))
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"portfolio_{watchlist}_{st.name.rstrip('*')}_{date.today()}.csv"
    pd.concat(curves, axis=1).to_csv(path)
    print(f"Equity curves saved to {path}")
    _print_skipped(skipped)


@app.command()
def compare(watchlist: str = WATCHLIST, strategy: str = typer.Option("all", "--strategy", "-s",
                                                                     help="'all' or comma-separated preset names"),
            years: int = YEARS, oos: float = OOS, positions: int = POSITIONS):
    """Portfolio backtest of several strategies side by side, with both benchmarks."""
    chosen = _strategy_list(strategy)
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    index = add_indicators(get_prices(universe.INDEX, years))
    runs, curves = {}, []
    for st in track(chosen, description="Backtesting strategies"):
        runs[st.name], c = _portfolio_runs(data, index, st, positions, oos, years)
        curves += c

    is_label, oos_label = list(next(iter(runs.values())))
    table = Table(title=f"{watchlist}: strategy comparison, {positions} slots. Out-of-sample = {oos_label}")
    for col in ["Strategy", "IS CAGR", "OOS CAGR", "OOS vs EW hold", "OOS max DD", "OOS Sharpe",
                "OOS PF", "OOS trades/yr", "OOS win"]:
        table.add_column(col, justify="left" if col == "Strategy" else "right")
    for name, cols in runs.items():
        i, o = cols[is_label], cols[oos_label]
        table.add_row(name, pct(i["cagr"]), pct(o["cagr"]), pct(o["excess_vs_universe"]), pct(o["max_drawdown"]),
                      num(o["sharpe"]), num(o["profit_factor"]), f"{o['trades_per_year']:.0f}", pct(o["win_rate"]))
    any_run = next(iter(runs.values()))
    i, o = any_run[is_label], any_run[oos_label]
    table.add_row("NIFTY 50 index", pct(i["nifty_cagr"]), pct(o["nifty_cagr"]), "", pct(o["nifty_max_drawdown"]),
                  num(o["nifty_sharpe"]), "", "", "", style="dim")
    table.add_row(f"Equal-weight {watchlist}", pct(i["universe_cagr"]), pct(o["universe_cagr"]), "",
                  pct(o["universe_max_drawdown"]), "", "", "", "", style="dim")
    print(table)
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"compare_{watchlist}_{date.today()}.csv"
    pd.concat(curves, axis=1).to_csv(path)
    print(f"Equity curves saved to {path}")
    stats_path = path.with_name(f"{path.stem}_stats.csv")
    compare_stats(runs).to_csv(stats_path, index=False)
    print(f"Statistics saved to {stats_path} (the dashboard sets paper accounts against them)")
    _print_skipped(skipped)


def compare_stats(runs: dict[str, dict[str, dict]]) -> pd.DataFrame:
    """One row per (strategy, segment) from `compare`'s runs ({strategy: {"<segment> (<period>)": stats}})."""
    rows = []
    for name, cols in runs.items():
        for label, stats in cols.items():
            seg, period = label.split(" (", 1)
            rows.append({"strategy": name, "segment": seg, "period": period.rstrip(")"), **stats})
    return pd.DataFrame(rows)


def _sweep_walk_forward(data, watchlist, years, grid, rank_by, min_trades, min_train_years):
    index = add_indicators(get_prices(universe.INDEX, years))
    wins = walkforward.windows(index.index[index.sma200.notna()], min_train_years)
    if not wins:
        raise typer.BadParameter("not enough history for one test year; lower --min-train-years or raise --years")
    rows, pooled = sweep.walk_forward(data, rules.market_regime(index), years, rank_by, min_trades, wins, grid,
                                      progress=lambda xs: track(xs, description=f"Running {len(xs)} combos"))
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"sweep_wf_{watchlist}_{date.today()}.csv"
    rows.to_csv(path, index=False)

    table = Table(title=f"Walk-forward: best in-sample {rank_by} (min {min_trades} trades) on earlier years, "
                        f"tested on each year")
    for col in ["Year", "Rule", "Mkt", "RS", "Sec", "Stop", "Tgt", "Trail", "Hold", "Train PF",
                "Test n", "Test PF", "Test avg", "Test win", "Base n", "Base PF", "Base avg"]:
        table.add_column(col, justify="left" if col in ("Rule", "Mkt", "RS", "Sec") else "right")
    on_off = lambda v: "on" if v else "off"
    opt = lambda v: "-" if v is None or pd.isna(v) else f"{v:g}"
    fmt = lambda r, k, f: f(r[k]) if k in r and not pd.isna(r[k]) else "-"
    for r in rows.to_dict("records"):
        picked = "rule" in r and isinstance(r["rule"], str)
        table.add_row(str(r["year"]), r["rule"] if picked else "(none)",
                      *([on_off(r["regime"]), on_off(r["rs"]), on_off(r["sector"]), opt(r["stop_atr"]),
                         opt(r["target_atr"]), opt(r["trail_atr"]), opt(r["max_hold"])] if picked else ["-"] * 7),
                      fmt(r, "train_profit_factor", num), fmt(r, "test_trades", lambda v: f"{v:.0f}"),
                      fmt(r, "test_profit_factor", num), fmt(r, "test_avg_trade", pct), fmt(r, "test_win_rate", pct),
                      fmt(r, "base_trades", lambda v: f"{v:.0f}"), fmt(r, "base_profit_factor", num),
                      fmt(r, "base_avg_trade", pct))
    for name, st in pooled.items():
        if st["trades"]:
            table.add_row(f"All ({name})", "", "", "", "", "", "", "", "", "", str(st["trades"]),
                          num(st["profit_factor"]), pct(st["avg_trade"]), pct(st["win_rate"]), "", "", "",
                          style="bold" if name == "picked" else "dim")
    print(table)
    print(f"Saved to {path}")


@app.command()
def yearly(watchlist: str = WATCHLIST, strategy: str = typer.Option("all", "--strategy", "-s",
                                                                    help="'all' or comma-separated preset names"),
           years: int = YEARS, positions: int = POSITIONS):
    """Portfolio return per calendar year for each preset, vs NIFTY 50 and an equal-weight hold of the watchlist."""
    chosen = _strategy_list(strategy)
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    index = add_indicators(get_prices(universe.INDEX, years))
    dates = index.index[index.sma200.notna()]
    cols = {}
    for st in track(chosen, description="Backtesting strategies"):
        entries = universe.strategy_entries(data, st, years, rules.market_regime(index))
        eq, _, _ = portfolio.run(data, entries, dates, st.params(), positions, st.rank)
        cols[st.name] = walkforward.yearly_returns(eq)
    cols["EW hold"] = walkforward.yearly_returns(portfolio.equal_weight(data, dates))
    cols["NIFTY 50"] = walkforward.yearly_returns(index.Close.reindex(dates))
    res = pd.DataFrame(cols)

    table = Table(title=f"{watchlist}: yearly return, {positions} slots ({dates[0]:%Y-%m-%d} to {dates[-1]:%Y-%m-%d})")
    for col in ["Year", *res.columns]:
        table.add_column(col, justify="right")
    for year, r in res.iterrows():
        table.add_row(str(year), *(pct(v) for v in r))
    excess = res[[st.name for st in chosen]].sub(res["EW hold"], axis=0)
    table.add_row("Beat EW", *(f"{(excess[c] > 0).sum()}/{len(res)}" for c in excess), "", "", style="bold")
    table.add_row("Median vs EW", *(pct(excess[c].median()) for c in excess), "", "", style="bold")
    print(table)
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"yearly_{watchlist}_{date.today()}.csv"
    res.to_csv(path)
    print(f"Saved to {path}")
    _print_skipped(skipped)


ML_CACHE = prices.CACHE.parent / "ml"


def _ml_panel(watchlist: str, years: int, rebuild: bool = False):
    """(panel, cache path, skipped symbols); reuses today's cached panel unless
    rebuild, or it lacks columns added since it was built."""
    path = ML_CACHE / f"dataset_{watchlist}_{years}y_{date.today()}.parquet"
    if path.exists() and not rebuild:
        df = pd.read_parquet(path)
        if {*ml_features.FEATURES, "label", "tech_score"} <= set(df.columns):
            return df, path, []
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    index = add_indicators(get_prices(universe.INDEX, years))
    industries = {sectors.INDUSTRY.get(s) for s in data} - {None}
    sector_close = {i: sectors.index(i, years) for i in track(sorted(industries), description="Sectors")}
    sector_of = {s: sector_close.get(sectors.INDUSTRY.get(s)) for s in data}
    df = ml_features.dataset(data, index, sector_of, lambda xs: track(xs, description="Features"))
    ML_CACHE.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path)
    return df, path, skipped


@app.command("ml-eval")
def ml_eval(watchlist: str = typer.Option("nifty500", help=f"One of: {', '.join(WATCHLISTS)}"),
            years: int = YEARS,
            min_train_years: float = typer.Option(3.0, help="Years of history before the first test year"),
            step: int = typer.Option(ml_model.STEP, help="Train on every Nth date (labels overlap)"),
            raw: bool = typer.Option(False, "--raw", help="Use raw feature values instead of per-date ranks")):
    """Walk-forward test of the ML ranker vs today's ranking: rank IC and decile returns per year."""
    df, _, skipped = _ml_panel(watchlist, years)
    dates = df.index.get_level_values("date").unique().sort_values()
    wins = walkforward.windows(dates, min_train_years, gap=ml_features.HORIZON)
    if not wins:
        raise typer.BadParameter("not enough history for one test year")
    rows, preds, last_model, last_test = ml_model.walk_forward(
        df, wins, step, not raw, lambda xs: track(xs, description=f"Training {len(xs)} yearly models"))

    h = ml_features.HORIZON
    table = Table(title=f"{watchlist}: ML ranker walk-forward, {'raw' if raw else 'per-date ranked'} features "
                        f"({h}-session excess return vs the average stock)")
    for col in ["Year", "Train rows", "Test rows", "IC", "IC>0 days", "Base IC", "Top 10%", "Bottom 10%",
                "Base top 10%"]:
        table.add_column(col, justify="right")
    for r in rows.to_dict("records"):
        table.add_row(str(r["year"]), f"{r['train_rows']:,}", f"{r['rows']:,}", f"{r['ic']:+.3f}",
                      pct(r["ic_pos"]), f"{r['ic_base']:+.3f}", pct(r["top"]), pct(r["bottom"]), pct(r["top_base"]))
    table.add_row("Mean", "", "", f"{rows.ic.mean():+.3f}", pct(rows.ic_pos.mean()), f"{rows.ic_base.mean():+.3f}",
                  pct(rows.top.mean()), pct(rows.bottom.mean()), pct(rows.top_base.mean()), style="bold")
    print(table)

    imp = ml_model.importance(last_model, last_test, not raw)
    print(f"Feature importance ({wins[-1].year} model, IC drop when shuffled): "
          + ", ".join(f"{f} {v:+.3f}" for f, v in imp.head(8).items()))
    ok, why = ml_model.verdict(rows)
    print(f"[{'green' if ok else 'red'}]{'GO' if ok else 'NO-GO'}[/]: {why}")
    path = ML_CACHE / f"oos_predictions_{watchlist}_{years}y_{date.today()}.parquet"
    preds.to_frame("pred").to_parquet(path)
    print(f"Out-of-sample predictions saved to {path}")
    _print_skipped(skipped)


@app.command("ml-dataset")
def ml_dataset(watchlist: str = typer.Option("nifty500", help=f"One of: {', '.join(WATCHLISTS)}"),
               years: int = YEARS):
    """Build the ML ranker's feature/label panel, cache it, and show each feature's rank IC by year."""
    df, path, skipped = _ml_panel(watchlist, years, rebuild=True)

    labelled = df.label.notna()
    dates = df.index.get_level_values("date")
    print(f"{len(df):,} rows, {df.index.get_level_values('symbol').nunique()} stocks, "
          f"{dates.min():%Y-%m-%d} to {dates.max():%Y-%m-%d}; {labelled.mean():.1%} labelled "
          f"(last {ml_features.HORIZON} sessions have no label)")
    print(f"Label ({ml_features.HORIZON}-session excess return vs NIFTY): mean {df.label.mean():+.2%}, "
          f"median {df.label.median():+.2%}, std {df.label.std():.2%}")
    ic = ml_features.rank_ic(df).T
    table = Table(title="Rank IC by year (mean daily Spearman of feature vs label; |IC| >= 0.03 is useful)")
    table.add_column("Feature")
    for y in ic.columns:
        table.add_column(str(y), justify="right")
    table.add_column("Mean", justify="right")
    table.add_column("Same sign", justify="right")
    for feat, r in ic.iterrows():
        if r.isna().all():  # e.g. `market`: one value per date, so no ranking signal on its own
            table.add_row(f"{feat} (same for all stocks)", *("-" for _ in range(len(r) + 2)), style="dim")
            continue
        same = (np.sign(r) == np.sign(r.mean())).sum()
        table.add_row(feat, *(f"{v:+.3f}" for v in r), f"{r.mean():+.3f}", f"{same}/{len(r)}",
                      style="bold" if abs(r.mean()) >= 0.03 else None)
    print(table)
    print(f"Saved to {path}")
    _print_skipped(skipped)


@app.command("sweep")
def sweep_cmd(watchlist: str = WATCHLIST, years: int = YEARS, oos: float = OOS,
              rank_by: str = typer.Option("profit_factor", help=f"One of: {', '.join(sweep.RANK_BY)}"),
              min_trades: int = typer.Option(300, help="Ignore combos with fewer in-sample trades"),
              top: int = typer.Option(10, help="Combos to re-test out-of-sample"),
              regime: bool = typer.Option(None, "--regime/--no-regime", help="Pin the market filter instead of sweeping it"),
              rs: bool = typer.Option(None, "--rs/--no-rs", help="Pin the RS filter instead of sweeping it"),
              sector: bool = typer.Option(None, "--sector/--no-sector", help="Pin the sector filter instead of sweeping it"),
              walk_forward: bool = typer.Option(False, "--walk-forward",
                                                help="Pick the best combo on earlier years, test it on each next year"),
              min_train_years: float = typer.Option(3.0, help="Walk-forward: years of history before the first test year")):
    """Grid-search rules, market/RS/sector filters and exits; rank in-sample, then check the top picks out-of-sample."""
    allowed = sweep.WF_RANK_BY if walk_forward else sweep.RANK_BY
    if rank_by not in allowed:
        raise typer.BadParameter(f"rank-by must be one of {allowed}")
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    grid = sweep.pinned(regime=regime, rs=rs, sector=sector)
    if walk_forward:
        _sweep_walk_forward(data, watchlist, years, grid, rank_by, min_trades, min_train_years)
        _print_skipped(skipped)
        return
    results, final = sweep.run(data, universe.regime(years), oos, rank_by, min_trades, top, years, grid,
                               progress=lambda xs: track(xs, description=f"Sweeping {len(xs)} combos"))
    path = sweep.save(results, watchlist)

    table = Table(title=f"Top {top} by in-sample {rank_by} (min {min_trades} trades), then out-of-sample")
    for col in ["#", "Rule", "Mkt", "RS", "Sec", "Stop", "Tgt", "Trail", "Hold",
                "IS n", "IS PF", "IS avg", "OOS n", "OOS PF", "OOS avg", "OOS win", "OOS CAGR", "B&H CAGR"]:
        table.add_column(col, justify="left" if col in ("Rule", "Mkt", "RS", "Sec") else "right")
    on_off = lambda v: "on" if v else "off"
    opt = lambda v: "-" if v is None or pd.isna(v) else f"{v:g}"
    for n, r in enumerate(final.to_dict("records"), 1):
        g = lambda k, f: f(r[k]) if r.get(k) is not None and not pd.isna(r[k]) else "-"
        table.add_row(
            "base" if r["baseline"] else str(n), r["rule"], on_off(r["regime"]), on_off(r["rs"]), on_off(r["sector"]),
            opt(r["stop_atr"]), opt(r["target_atr"]), opt(r["trail_atr"]), str(r["max_hold"]),
            g("is_trades", str), g("is_profit_factor", num), g("is_avg_trade", pct),
            g("oos_trades", str), g("oos_profit_factor", num), g("oos_avg_trade", pct),
            g("oos_win_rate", pct), g("oos_median_cagr", pct), g("oos_median_buy_hold_cagr", pct),
            style="dim" if r["baseline"] else None)
    print(table)
    print(f"{len(results)} combos x {len(data)} stocks; all in-sample results saved to {path}")
    _print_skipped(skipped)


if __name__ == "__main__":
    app()
