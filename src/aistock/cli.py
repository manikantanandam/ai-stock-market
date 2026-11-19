from datetime import date

import pandas as pd
import typer
from rich import print
from rich.progress import track
from rich.table import Table

from aistock.backtest import engine, portfolio, sweep, universe
from aistock.data.prices import get_prices, market_phase
from aistock.data.watchlists import WATCHLISTS
from aistock.strategy import journal, signal
from aistock.technical import rules
from aistock.technical.indicators import add_indicators

app = typer.Typer(help="Local AI stock assistant (NSE swing trading)", no_args_is_help=True)


@app.callback()
def main():
    """Local AI stock assistant."""


COLORS = {"BUY": "green", "WATCH": "yellow", "AVOID": "red"}


@app.command()
def analyze(
    symbol: str,
    capital: float = typer.Option(100_000, help="Account size in INR, for position sizing"),
    no_news: bool = typer.Option(False, "--no-news", help="Skip news/FinBERT"),
):
    """Combined technical + fundamental + news signal for one stock."""
    s = signal.evaluate(symbol, signal.Config(capital=capital), use_news=not no_news)
    color = COLORS[s.action]
    print(f"[bold]{s.symbol}[/bold] {s.name} ({s.sector})  close {s.close:.2f}")
    print(f"[bold {color}]{s.action}[/]  combined {s.combined:+.2f}")
    for part, value in [("technical", s.technical), ("fundamental", s.fundamental), ("sentiment", s.sentiment)]:
        print(f"\n[bold]{part.capitalize()}[/bold] {value:+.2f}")
        for w in s.reasons[part]:
            print(f" - {w}")
    print("\n[bold]Decision[/bold]")
    for w in s.reasons["decision"]:
        print(f" - {w}")
    if s.action == "BUY":
        print(f"\nPlan: buy ~{s.shares} shares near {s.close:.2f}, stop {s.stop:.2f}, target {s.target:.2f} (1.5R)")
    else:
        print(f"\nIf it turns BUY: stop {s.stop:.2f}, target {s.target:.2f}")


@app.command()
def scan(
    watchlist: str = typer.Option("nifty50", help=f"One of: {', '.join(WATCHLISTS)}"),
    symbols: str = typer.Option("", help="Comma-separated symbols instead of a watchlist"),
    capital: float = typer.Option(100_000),
    no_news: bool = typer.Option(False, "--no-news", help="Skip news/FinBERT (faster)"),
    top: int = typer.Option(15, help="Rows to show"),
):
    """Rank a watchlist by combined score and log the signals to journal/signals.csv."""
    names = [x.strip().upper() for x in symbols.split(",") if x.strip()] or WATCHLISTS[watchlist]
    cfg, results, failed = signal.Config(capital=capital), [], []
    for sym in track(names, description="Scanning"):
        try:
            results.append(signal.evaluate(sym, cfg, use_news=not no_news))
        except Exception as e:  # one bad ticker shouldn't stop the scan
            failed.append(f"{sym} ({e})")
    if not results:
        print("[red]No symbols could be evaluated[/red]")
        raise typer.Exit(1)
    # Mid-session bars are partial, so only completed sessions go in the journal
    phase = market_phase()
    logged = f"logged to {journal.record(results)}" if phase != "live" else "not logged (market open)"
    results.sort(key=lambda s: s.combined, reverse=True)

    table = Table(title=f"Scan: {len(results)} stocks, as of {results[0].as_of}")
    for col in ["Symbol", "Action", "Combined", "Tech", "Fund", "News", "Close", "Stop", "Target", "Qty"]:
        table.add_column(col, justify="left" if col in ("Symbol", "Action") else "right")
    for s in results[:top]:
        table.add_row(s.symbol, f"[{COLORS[s.action]}]{s.action}[/]", f"{s.combined:+.2f}",
                      f"{s.technical:+.2f}", f"{s.fundamental:+.2f}", f"{s.sentiment:+.2f}",
                      f"{s.close:.2f}", f"{s.stop:.2f}", f"{s.target:.2f}", str(s.shares))
    print(table)
    counts = {a: sum(s.action == a for s in results) for a in COLORS}
    print(f"BUY {counts['BUY']}  WATCH {counts['WATCH']}  AVOID {counts['AVOID']}  · {logged}")
    if failed:
        print(f"[yellow]Skipped: {', '.join(failed)}[/yellow]")


pct = lambda v: f"{v * 100:.1f}%"
num = lambda v: f"{v:.2f}"

# Shared backtest options; the default rule matches the BUY gate's technical threshold.
YEARS = typer.Option(10, help="Years of daily history")
RULE = typer.Option(f"score:{signal.Config().buy_technical}", help="score:<threshold>, pullback or breakout")
REGIME = typer.Option(False, "--regime", help="Only enter when NIFTY 50 is above its 200-day SMA")
STOP_ATR = typer.Option(2.0)
TARGET_ATR = typer.Option(3.0, help="0 = no fixed target")
TRAIL_ATR = typer.Option(0.0, help="Trailing stop distance in ATR; 0 = off")
MAX_HOLD = typer.Option(20, help="Max bars in a trade")
OOS = typer.Option(0.3, help="Fraction of history held out as out-of-sample")
WATCHLIST = typer.Option("nifty50", help=f"One of: {', '.join(WATCHLISTS)}")

POOLED_ROWS = [("stocks", str), ("trades", str), ("win_rate", pct), ("avg_trade", pct), ("median_trade", pct),
               ("profit_factor", num), ("avg_bars", lambda v: f"{v:.1f}"), ("exit_stop", pct),
               ("exit_target", pct), ("exit_time", pct), ("median_cagr", pct), ("median_buy_hold_cagr", pct),
               ("median_max_dd", pct), ("median_sharpe", num), ("exposure", pct),
               ("profitable_stocks", pct), ("beat_buy_hold", pct)]


def _metric_table(title: str, columns: dict[str, dict], rows: list[tuple]) -> Table:
    table = Table(title=title)
    table.add_column("Metric")
    for name in columns:
        table.add_column(name, justify="right")
    for key, fmt in rows:
        table.add_row(key, *(fmt(c[key]) if key in c else "-" for c in columns.values()))
    return table


def _params(stop_atr, target_atr, trail_atr, max_hold) -> engine.Params:
    return engine.Params(stop_atr=stop_atr, target_atr=target_atr or None,
                         trail_atr=trail_atr or None, max_hold=max_hold)


def _describe(rule, regime, stop_atr, target_atr, trail_atr, max_hold) -> str:
    parts = [rule, "regime" if regime else "no regime", f"stop {stop_atr} ATR",
             f"target {target_atr} ATR" if target_atr else "no target"]
    if trail_atr:
        parts.append(f"trail {trail_atr} ATR")
    return ", ".join(parts + [f"max {max_hold} bars"])


@app.command()
def backtest(symbol: str, years: int = YEARS, rule: str = RULE, regime: bool = REGIME,
             stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR, trail_atr: float = TRAIL_ATR,
             max_hold: int = MAX_HOLD, oos: float = OOS):
    """Backtest an entry rule on one stock with Indian delivery costs."""
    symbol = symbol.upper()
    data, skipped = universe.load([symbol], years)
    if not data:
        print(f"[red]{skipped[0]}[/red]")
        raise typer.Exit(1)
    entries = universe.build_entries(data, rule, universe.regime(years) if regime else None)
    p = _params(stop_atr, target_atr, trail_atr, max_hold)
    ind = data[symbol]
    runs = {seg: engine.run(i, e, p).stats for seg, (i, e) in universe.segments(ind, entries[symbol], oos).items()}
    rows = [("trades", str), ("win_rate", pct), ("avg_trade_pct", pct), ("total_return", pct),
            ("cagr", pct), ("max_drawdown", pct), ("sharpe", num), ("exposure", pct), ("buy_hold", pct)]
    split = ind.index[int(len(ind) * (1 - oos))]
    print(_metric_table(f"{symbol}: {_describe(rule, regime, stop_atr, target_atr, trail_atr, max_hold)}\n"
                        f"{ind.index[0]:%Y-%m-%d} to {ind.index[-1]:%Y-%m-%d}, split {split:%Y-%m-%d}", runs, rows))


@app.command("backtest-all")
def backtest_all(watchlist: str = WATCHLIST, years: int = YEARS, rule: str = RULE, regime: bool = REGIME,
                 stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR, trail_atr: float = TRAIL_ATR,
                 max_hold: int = MAX_HOLD, oos: float = OOS):
    """Backtest an entry rule on every stock in a watchlist; per-stock results saved to reports/."""
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    entries = universe.build_entries(data, rule, universe.regime(years) if regime else None)
    stats, trades = universe.run_universe(data, entries, _params(stop_atr, target_atr, trail_atr, max_hold), oos)
    if trades.empty:
        print("[red]No trades generated[/red]")
        raise typer.Exit(1)
    path = universe.save(stats, trades, watchlist)
    pooled = {seg: universe.pooled(stats, trades, seg) for seg in universe.SEGMENTS}
    print(_metric_table(f"{watchlist}: {_describe(rule, regime, stop_atr, target_atr, trail_atr, max_hold)}",
                        pooled, POOLED_ROWS))

    oos_stats = stats[stats.segment == "Out-of-sample"].sort_values("total_return")
    show = lambda d: ", ".join(f"{r.symbol} {r.total_return * 100:+.0f}%" for r in d.itertuples())
    print(f"OOS best:  {show(oos_stats.tail(5).iloc[::-1])}")
    print(f"OOS worst: {show(oos_stats.head(5))}")
    print(f"Saved per-stock stats and trades next to {path}")
    if skipped:
        print(f"[yellow]Skipped: {', '.join(skipped)}[/yellow]")


@app.command("portfolio")
def portfolio_cmd(watchlist: str = WATCHLIST, years: int = YEARS, rule: str = RULE, regime: bool = REGIME,
                  stop_atr: float = STOP_ATR, target_atr: float = TARGET_ATR, trail_atr: float = TRAIL_ATR,
                  max_hold: int = MAX_HOLD, oos: float = OOS,
                  positions: int = typer.Option(10, help="Max concurrent positions; each gets 1/N of equity")):
    """Shared-capital portfolio backtest across a watchlist, vs holding NIFTY 50."""
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    index = add_indicators(get_prices(universe.INDEX, years))
    entries = universe.build_entries(data, rule, rules.market_regime(index) if regime else None)
    p = _params(stop_atr, target_atr, trail_atr, max_hold)
    dates = index.index[index.sma200.notna()]  # start once SMA200 is warm everywhere
    split = int(len(dates) * (1 - oos))
    columns, curves = {}, []
    for seg, d in zip(universe.SEGMENTS, (dates[:split], dates[split:])):
        eq, invested, trades = portfolio.run(data, entries, d, p, positions)
        columns[f"{seg} ({d[0]:%Y-%m} to {d[-1]:%Y-%m})"] = portfolio.stats(eq, invested, trades, index.Close)
        curves.append(eq.rename(seg))
    rows = [("cagr", pct), ("nifty_cagr", pct), ("excess_cagr", pct), ("max_drawdown", pct),
            ("nifty_max_drawdown", pct), ("sharpe", num), ("nifty_sharpe", num), ("exposure", pct),
            ("trades", str), ("trades_per_year", lambda v: f"{v:.0f}"), ("win_rate", pct),
            ("avg_trade", pct), ("profit_factor", num), ("avg_bars", lambda v: f"{v:.1f}")]
    print(_metric_table(f"{watchlist} portfolio, {positions} slots: "
                        f"{_describe(rule, regime, stop_atr, target_atr, trail_atr, max_hold)}", columns, rows))
    universe.REPORTS.mkdir(exist_ok=True)
    path = universe.REPORTS / f"portfolio_{watchlist}_{date.today()}.csv"
    pd.concat(curves, axis=1).to_csv(path)
    print(f"Equity curves saved to {path}")
    if skipped:
        print(f"[yellow]Skipped: {', '.join(skipped)}[/yellow]")


@app.command("sweep")
def sweep_cmd(watchlist: str = WATCHLIST, years: int = YEARS, oos: float = OOS,
              rank_by: str = typer.Option("profit_factor", help=f"One of: {', '.join(sweep.RANK_BY)}"),
              min_trades: int = typer.Option(300, help="Ignore combos with fewer in-sample trades"),
              top: int = typer.Option(10, help="Combos to re-test out-of-sample")):
    """Grid-search rules, market filter and exits; rank in-sample, then check the top picks out-of-sample."""
    if rank_by not in sweep.RANK_BY:
        raise typer.BadParameter(f"rank-by must be one of {sweep.RANK_BY}")
    data, skipped = universe.load(WATCHLISTS[watchlist], years, lambda xs: track(xs, description="Loading"))
    results, final = sweep.run(data, universe.regime(years), oos, rank_by, min_trades, top,
                               progress=lambda xs: track(xs, description=f"Sweeping {len(xs)} combos"))
    path = sweep.save(results, watchlist)

    table = Table(title=f"Top {top} by in-sample {rank_by} (min {min_trades} trades), then out-of-sample")
    for col in ["#", "Rule", "Mkt", "Stop", "Tgt", "Trail", "Hold",
                "IS n", "IS PF", "IS avg", "OOS n", "OOS PF", "OOS avg", "OOS win", "OOS CAGR", "B&H CAGR"]:
        table.add_column(col, justify="left" if col in ("Rule", "Mkt") else "right")
    opt = lambda v: "-" if v is None or pd.isna(v) else f"{v:g}"
    for n, r in enumerate(final.to_dict("records"), 1):
        g = lambda k, f: f(r[k]) if r.get(k) is not None and not pd.isna(r[k]) else "-"
        table.add_row(
            "base" if r["baseline"] else str(n), r["rule"], "on" if r["regime"] else "off",
            opt(r["stop_atr"]), opt(r["target_atr"]), opt(r["trail_atr"]), str(r["max_hold"]),
            g("is_trades", str), g("is_profit_factor", num), g("is_avg_trade", pct),
            g("oos_trades", str), g("oos_profit_factor", num), g("oos_avg_trade", pct),
            g("oos_win_rate", pct), g("oos_median_cagr", pct), g("oos_median_buy_hold_cagr", pct),
            style="dim" if r["baseline"] else None)
    print(table)
    print(f"{len(results)} combos x {len(data)} stocks; all in-sample results saved to {path}")
    if skipped:
        print(f"[yellow]Skipped: {', '.join(skipped)}[/yellow]")


if __name__ == "__main__":
    app()
