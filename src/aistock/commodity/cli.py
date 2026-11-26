"""`aistock commodity ...`: scan, analyze and backtest commodity futures."""
import pandas as pd
import typer
from rich import print
from rich.progress import track
from rich.table import Table

from aistock.commodity import backtest, signal
from aistock.commodity.strategies import DEFAULT, PRESETS
from aistock.commodity.strategies import get as get_strategy
from aistock.data import commodities
from aistock.llm import notes as llm_notes
from aistock.strategy import journal
from aistock.strategy.signal import NEWS_MODES

app = typer.Typer(help="Commodity futures (global contracts in INR): BUY/SELL/EXIT/HOLD signals and backtests",
                  no_args_is_help=True)

COLORS = {"BUY": "green", "SELL": "red", "EXIT": "magenta", "HOLD": "cyan", "WATCH": "yellow", "MIXED": "white"}
STRATEGY = typer.Option("all", "--strategy", "-s", help=f"'all' or comma-separated: {', '.join(PRESETS)}")
YEARS = typer.Option(15, help="Years of daily history")
OOS = typer.Option(0.3, help="Fraction of history held out as out-of-sample")
pct = lambda v: "-" if v != v else f"{v * 100:.1f}%"  # NaN-safe
num = lambda v: f"{v:.2f}"
level = lambda v: "-" if v is None else f"{v:,.2f}"
score = lambda v: "-" if v is None else f"{v:+.2f}"


def _strategies(names: str) -> list:
    if names == "all":
        return list(PRESETS.values())
    try:
        return [get_strategy(n.strip()) for n in names.split(",") if n.strip()]
    except ValueError as e:
        raise typer.BadParameter(str(e))


def _symbols(names: str) -> list[str]:
    symbols = [x.strip().upper() for x in names.split(",") if x.strip()] or list(commodities.CONTRACTS)
    for s in symbols:
        try:
            commodities.get(s)
        except ValueError as e:
            raise typer.BadParameter(str(e))
    return symbols


@app.command("list")
def list_all():
    """List the contracts and the commodity strategy presets."""
    table = Table(title="Contracts (Yahoo continuous futures, converted with USDINR=X)")
    for col in ["Symbol", "Name", "Group", "Yahoo", "INR per"]:
        table.add_column(col)
    for c in commodities.CONTRACTS.values():
        table.add_row(c.symbol, c.name, c.group, c.ticker, c.unit)
    print(table)
    table = Table(title="Commodity strategies")
    for col in ["Name", "Entries and exits", "Description"]:
        table.add_column(col)
    for st in PRESETS.values():
        table.add_row(st.name, st.describe(), st.description)
    print(table)


def _verdicts(s: signal.Signal) -> Table:
    table = Table(title="Strategies")
    for col in ["Strategy", "Action", "Side", "Stop", "Target", "Qty", "Why"]:
        table.add_column(col, justify="right" if col in ("Stop", "Target", "Qty") else "left")
    for r in s.strategies.values():
        table.add_row(r.strategy, f"[{COLORS[r.action]}]{r.action}[/]", r.side or "-", level(r.stop),
                      level(r.target), f"{r.qty:g}" if r.qty else "-", "; ".join(r.why))
    return table


def _notes(items: list[tuple]) -> list[tuple]:
    """LLM notes for (signal, strategy) pairs; stops at the first sign Ollama is unavailable."""
    if not items:
        print("No BUY or SELL to explain.")
        return []
    written = []
    for s, name in track(items, description="Writing notes", transient=True):
        try:
            text = llm_notes.commodity_note(s, name)
        except llm_notes.Unavailable as e:
            print(f"[yellow]Notes skipped: {e}. Run `brew services start ollama` and "
                  f"`ollama pull {llm_notes.MODEL}`.[/yellow]")
            break
        print(f"\n[bold]{s.symbol} · {name} · {s.strategies[name].action}[/bold]: {text}")
        written.append((s, name, text))
    return written


@app.command()
def analyze(
    symbol: str,
    capital: float = typer.Option(100_000, help="Account size in INR, for position sizing"),
    no_news: bool = typer.Option(False, "--no-news", help="Skip news/FinBERT"),
    strategy: str = STRATEGY,
    notes: bool = typer.Option(False, "--notes", help="Add a local-LLM (Ollama) note for each BUY/SELL"),
):
    """Scores and each strategy's verdict for one commodity (e.g. GOLD)."""
    try:
        s = signal.evaluate(symbol, signal.Config(capital=capital), "none" if no_news else "all", _strategies(strategy))
    except ValueError as e:
        raise typer.BadParameter(str(e))
    print(f"[bold]{s.symbol}[/bold] {s.name} ({s.group})  close ₹{s.close:,.2f} per {s.unit} "
          f"(USD {s.close_usd:,.2f} at {s.usdinr:.2f})  ATR {s.atr:,.2f}  as of {s.as_of}")
    print(f"[bold {COLORS[s.action]}]{s.action}[/]  combined {s.combined:+.2f}")
    for part, value in [("technical", s.technical), ("sentiment", s.sentiment)]:
        print(f"\n[bold]{part.capitalize()}[/bold] {score(value)}")
        for w in s.reasons[part]:
            print(f" - {w}")
    print(_verdicts(s))
    if notes:
        _notes([(s, name) for name in s.entries])


@app.command()
def scan(
    symbols: str = typer.Option("", help="Comma-separated contracts (default: all)"),
    strategy: str = STRATEGY,
    capital: float = typer.Option(100_000),
    news: str = typer.Option("all", help=f"One of {NEWS_MODES}"),
    notes: bool = typer.Option(False, "--notes", help="Add a local-LLM (Ollama) note for each BUY/SELL"),
):
    """Run every commodity strategy over the contracts and log to journal/commodities/."""
    if news not in NEWS_MODES:
        raise typer.BadParameter(f"news must be one of {NEWS_MODES}")
    names, chosen = _symbols(symbols), _strategies(strategy)
    commodities.prefetch(years=2)
    cfg, results, failed = signal.Config(capital=capital), [], []
    for sym in track(names, description="Scanning"):
        try:
            results.append(signal.evaluate(sym, cfg, news, chosen))
        except Exception as e:  # one bad ticker shouldn't stop the scan
            failed.append(f"{sym} ({e})")
    if not results:
        print("[red]No contracts could be evaluated[/red]")
        raise typer.Exit(1)
    # Only completed futures sessions are used, so every scan can be logged
    root = journal.record(results, root=journal.COMMODITIES, per_symbol="contracts")
    order = list(COLORS)
    results.sort(key=lambda s: (order.index(s.action), -abs(s.combined)))

    table = Table(title=f"Commodity scan: {len(results)} contracts, as of {max(s.as_of for s in results)}")
    for col in ["Symbol", "Action", "Strategies", "Combined", "Tech", "News", "Close", "Per", "Stop", "Target"]:
        table.add_column(col, justify="left" if col in ("Symbol", "Action", "Strategies", "Per") else "right")
    for s in results:
        acting = [n for n, r in s.strategies.items() if r.action == s.action] if s.action != "MIXED" else s.entries
        r = s.strategies[acting[0]] if acting and s.action != "WATCH" else None
        table.add_row(s.symbol, f"[{COLORS[s.action]}]{s.action}[/]",
                      ", ".join(f"{n} ({s.strategies[n].side})" for n in acting) if r else "-",
                      f"{s.combined:+.2f}", f"{s.technical:+.2f}", score(s.sentiment), f"{s.close:,.2f}", s.unit,
                      level(r.stop) if r else "-", level(r.target) if r else "-")
    print(table)
    counts = "  ".join(f"{a} {sum(s.action == a for s in results)}" for a in COLORS)
    print(f"Contracts: {counts} · logged to {root}")
    if notes:
        written = _notes([(s, name) for s in results for name in s.entries])
        if written:
            journal.record_notes(written, root=journal.COMMODITIES)
            print(f"Notes logged to {journal.COMMODITIES / 'notes'}")
    if failed:
        print(f"[yellow]Skipped {len(failed)}: {', '.join(failed)}[/yellow]")


METRICS = [("stocks", str), ("trades", str), ("win_rate", pct), ("avg_trade", pct), ("median_trade", pct),
           ("profit_factor", num), ("long_trades", str), ("long_avg_trade", pct), ("short_trades", str),
           ("short_avg_trade", pct), ("avg_bars", lambda v: f"{v:.1f}"), ("exit_stop", pct), ("exit_target", pct),
           ("exit_time", pct), ("median_cagr", pct), ("median_buy_hold_cagr", pct), ("median_max_dd", pct),
           ("median_sharpe", num), ("exposure", pct), ("profitable_stocks", pct), ("beat_buy_hold", pct)]


@app.command("backtest")
def backtest_cmd(symbols: str = typer.Option("", help="Comma-separated contracts (default: all)"),
                 strategy: str = typer.Option(DEFAULT, "--strategy", "-s",
                                              help=f"One of: {', '.join(PRESETS)}"),
                 years: int = YEARS, oos: float = OOS):
    """Backtest one commodity strategy (long and short) per contract, pooled, with MCX costs."""
    st = _strategies(strategy)[0]
    data, skipped = backtest.load(_symbols(symbols), years)
    if not data:
        print(f"[red]No data: {', '.join(skipped)}[/red]")
        raise typer.Exit(1)
    stats, trades = backtest.run(data, st, oos)
    columns = {seg: backtest.pooled(stats, trades, seg) for seg in ("In-sample", "Out-of-sample")}
    table = Table(title=f"Commodities · {st.name}: {st.describe()}")
    table.add_column("Metric")
    for name in columns:
        table.add_column(name, justify="right")
    for key, fmt in METRICS:
        table.add_row("contracts" if key == "stocks" else key,
                      *(fmt(c[key]) if key in c else "-" for c in columns.values()))
    print(table)
    oos_stats = stats[stats.segment == "Out-of-sample"]
    print("OOS per contract: " + ", ".join(f"{r.symbol} {r.total_return * 100:+.0f}% (hold {r.buy_hold * 100:+.0f}%)"
                                           for r in oos_stats.itertuples()))
    if len(trades):
        print(f"Trades saved to {backtest.save(trades.assign(strategy=st.name), st.name)}")
    if skipped:
        print(f"[yellow]Skipped: {', '.join(skipped)}[/yellow]")


@app.command()
def compare(strategy: str = STRATEGY, years: int = YEARS, oos: float = OOS):
    """Backtest every commodity strategy side by side; trades saved to reports/."""
    chosen = _strategies(strategy)
    data, skipped = backtest.load(list(commodities.CONTRACTS), years)
    if not data:
        print(f"[red]No data: {', '.join(skipped)}[/red]")
        raise typer.Exit(1)
    split = min(ind.index[int(len(ind) * (1 - oos))] for ind in data.values())
    table = Table(title=f"Commodities: strategy comparison over {len(data)} contracts, "
                        f"{years}y, out-of-sample from ~{split:%Y-%m}")
    for col in ["Strategy", "IS avg trade", "OOS trades", "OOS win", "OOS avg trade", "OOS PF", "OOS long avg",
                "OOS short avg", "OOS median CAGR", "OOS median hold CAGR", "OOS beat hold"]:
        table.add_column(col, justify="left" if col == "Strategy" else "right")
    all_trades = []
    for st in track(chosen, description="Backtesting strategies"):
        stats, trades = backtest.run(data, st, oos)
        i, o = (backtest.pooled(stats, trades, seg) for seg in ("In-sample", "Out-of-sample"))
        if not o["trades"]:
            table.add_row(st.name, pct(i.get("avg_trade", float("nan"))), "0", *["-"] * 8)
            continue
        table.add_row(st.name, pct(i.get("avg_trade", float("nan"))), str(o["trades"]), pct(o["win_rate"]),
                      pct(o["avg_trade"]), num(o["profit_factor"]), pct(o["long_avg_trade"]),
                      pct(o["short_avg_trade"]), pct(o["median_cagr"]), pct(o["median_buy_hold_cagr"]),
                      pct(o["beat_buy_hold"]))
        all_trades.append(trades.assign(strategy=st.name))
    print(table)
    if all_trades:
        print(f"Trades saved to {backtest.save(pd.concat(all_trades, ignore_index=True), 'compare')}")
    if skipped:
        print(f"[yellow]Skipped: {', '.join(skipped)}[/yellow]")
