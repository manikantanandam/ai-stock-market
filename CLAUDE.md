# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local, subscription-free AI assistant for swing trading Indian stocks (NSE/BSE, daily candles). It combines technical, fundamental, news-sentiment and pattern analysis into trade ideas (entry/stop/target plus rationale). The full roadmap is in `~/.claude/plans/i-want-to-have-parsed-spring.md`.

Target hardware is an M1 with 8GB RAM, so only small local models work: FinBERT for sentiment and Ollama `qwen2.5:3b` for writing text.

## Commands

- Install/sync deps: `uv sync` (Python 3.12 is pinned, because `pandas-ta` requires >=3.12)
- Add a dependency: `uv add <pkg>`
- Run the CLI: `uv run aistock analyze <SYMBOL>` (bare NSE symbol, e.g. `INFY`; `.NS` is appended internally; `^`-prefixed index tickers like `^NSEI` are passed through)
- Daily watchlist scan (logs to `journal/days/<date>.csv` and `journal/stocks/<SYMBOL>.csv`): `uv run aistock scan [--watchlist nifty500] [--news all|candidates|none]`
- Watchlists are committed CSV snapshots in `src/aistock/data/watchlists/`; refresh with `uv run aistock update-watchlists`
- Dashboard: `uv run aistock site --serve` builds `site/` (gitignored) from every CSV in `journal/` and `reports/` and serves it on localhost:8000; `src/aistock/site/` writes `data/manifest.json` and picks a view per file kind, `static/` holds the page (Chart.js + PapaParse from cdnjs, no build step). `.github/workflows/pages.yml` publishes it to GitHub Pages (journal + the latest weekly run's report artifacts)
- Prices are batch-downloaded via `prices.prefetch` (`yf.download`) before per-symbol `get_prices` reads; fundamentals have a 7-day cache
- Daily scan also runs on GitHub Actions (`.github/workflows/daily-scan.yml`, 11:17 UTC weekdays, plus a 14:47 UTC backup that runs only if the latest session is missing) and merges the journal into `main` through its own PR, so `git pull` before local scans. Linux uses CPU-only torch via `[tool.uv.sources]` in `pyproject.toml`.
- Paper trading: `uv run aistock paper` replays journal BUYs through `engine.run` (`strategy/paper.py`); the daily CI run adds its report to the job summary
- Paper accounts: `uv run aistock paper-account` steps a saved account per strategy (shared capital, 10 slots, same rules as `portfolio.run`) through every session since its last save; state is in `journal/paper/<strategy>/` and the daily CI run commits it. A weekly workflow (`weekly-backtest.yml`) re-runs `compare`, `yearly` and `sweep` on nifty500 (sweep in its own job)
- Strategies: presets live in `strategy/strategies.py` (`rank` picks who gets a slot when signals outnumber slots: technical score or SMA200 slope, see `portfolio.rank_inputs`) (`uv run aistock strategies`); `uv run aistock compare` backtests all presets side by side
- Backtests: `uv run aistock backtest <SYMBOL>`, `backtest-all`, `portfolio` take `--strategy <preset>`; `--rule/--regime/--rs/--sector/--stop-atr/...` override the preset. `sweep` grid-searches rules, market/RS/sector filters and exits independently of presets (3840 combos; `--rs/--no-rs` etc. pin a filter to cut the grid). `sweep --walk-forward` picks the best combo on earlier years and tests it on each next year; `uv run aistock yearly` shows each preset's return per calendar year vs the equal-weight hold. Outputs CSVs to `reports/`
- Ollama (installed via Homebrew): `brew services start ollama`, then `ollama pull qwen2.5:3b`. `analyze --notes` / `scan --notes` add an LLM note per BUY; `scan --notes` also saves them to `journal/notes/<date>.csv` (at most `--notes-max`, default 15). The daily CI scan installs Ollama (CPU, cached) and runs `--notes`; the dashboard shows them under LLM notes, with each BUY's strategies and combined score. `uv run aistock backfill-notes` writes notes for journaled sessions without any, rebuilding each BUY from its journal row (`llm/backfill.py`; news reasons from Google News limited to the 14 days before the session via `news.get_news_as_of`); CI runs it after the scan, and a manual Daily scan run can rewrite chosen sessions' notes via the `backfill_dates` input (`gh workflow run daily-scan.yml -f backfill_dates=2026-11-18,2026-11-19`)

- Commodity futures: `uv run aistock commodity list|analyze GOLD|scan [--notes]|backtest -s <preset>|compare`. Contracts are Yahoo continuous futures (GC=F, SI=F, CL=F, NG=F, HG=F, ALI=F) converted with USDINR=X into MCX quote units (gold per 10 g, silver per kg, ...); Yahoo has no MCX data. Verdicts per preset: BUY/SELL (long/short entry at the next open), EXIT (the strategy's position closed on the latest bar), HOLD, WATCH. `scan` logs to `journal/commodities/{days,contracts,notes}/`; the daily CI run scans after the stock scan and the weekly workflow runs `compare`

- Tests: `uv run pytest` (all offline, synthetic data; ~5s). Single test: `uv run pytest tests/test_engine.py::test_gap_down_fills_at_open_not_stop`. `tests/conftest.py` has `bars()` for exact OHLC scenarios and `random_walk()` for frames with indicators.

No linter is configured.

## Architecture

Package `src/aistock/` (uv `--package` layout; the console script `aistock` maps to `aistock.cli:app`, a Typer app):

- `data/`: yfinance prices, cached in `.cache/prices/` per IST date and market phase (`pre`/`live`/`close`), so intraday partial bars are never reused after the close. Also fundamentals, news headlines (Google News RSS plus publisher RSS feeds in `news.FEEDS`, fetched once per process and matched by company name or symbol), watchlist CSVs, and `sectors` (equal-weight sector indices built from NIFTY 500 members per `Industry`, because Yahoo lacks many NSE sector indices).
- `technical/`: `add_indicators` adds indicator columns. `score_series`/`technical_score` give a per-bar or latest technical score. `rules.py` holds entry rules: bool Series, True = enter at the next bar's open. Short rules for futures are in `SHORT_RULES` (plus `score_below:<x>`); `channel`/`channel_short` skip the volume test because Yahoo's futures volume is unreliable. It also has the entry filters: market regime, relative strength (`outperforms`: beat NIFTY over 63 and 126 bars) and `sector_trend` (sector above its SMA50).
- `llm/notes.py`: explains a BUY from the signal's numbers and reasons via Ollama (temperature 0); a note quoting any figure not in those facts is withheld.
- `fundamental/`, `sentiment/`: each returns `(score in [-1, 1], reasons)`; sentiment runs FinBERT.
- `strategy/`: `strategies.PRESETS` (rule + market filter + exits + live gates). `signal.evaluate` scores a stock once and gives each preset a BUY/WATCH verdict. `journal` keeps one row per (session date, symbol), keyed by the last bar's date, in both a per-day and a per-stock CSV, with a union of columns as the schema evolves; rows also keep each score's reasons (`*_reasons`) and the raw fundamental ratios (`f_*`), since Yahoo only serves today's fundamentals. `journal.load()` reads the day files.
- `ml/`: `features.py` builds the ranker's panel: one row per (date, symbol), 18 causal ratio features, label = 20-session forward excess return vs NIFTY (next open to close). `uv run aistock ml-dataset` caches it in `.cache/ml/` and prints per-feature rank IC by year. `model.py` trains a deterministic `HistGradientBoostingRegressor` on the per-date label rank; `uv run aistock ml-eval` runs it walk-forward (yearly models, 20-session gap) against today's ranking (technical score, then 126-day momentum) and prints a GO/NO-GO. The model may only rank candidates; rules still decide entries.
- `commodity/`: futures presets with a long `rule` and a `short_rule` (`strategies.py`, same `Strategy` type), `signal.evaluate` (technical + news only; HOLD/EXIT come from replaying each preset through `engine.run`, so they match the backtest exits), `backtest.py` (per-contract runs with `costs.FuturesCostModel` and fractional units, pooled long/short stats) and the `aistock commodity` Typer sub-app (`cli.py`). Prices come from `data/commodities.py`, which drops the bar of a session still trading in New York (closes 17:00 ET).
- `backtest/`: `engine.run` (single stock or contract; optional `shorts` series and `Params.fractional` for futures) and `portfolio.run` (shared capital, N slots: entries at the open use only yesterday's cash, then intraday exits). Both use the same exit rules and `costs.CostModel`; `portfolio.buy/check_exit/sell` are shared with `strategy/account.py`. `universe` loads data and splits in-sample/out-of-sample; `sweep` grid-searches; `walkforward` has yearly expanding windows (train on all earlier years minus a 20-session gap, test on the year).

Only rules, market filter and exits are backtestable; fundamentals and news have no free history. Portfolio results are judged against an equal-weight hold of the same watchlist, because today's constituents carry survivorship bias.

## Git workflow

- `main` is protected by a GitHub ruleset: changes only through PRs that pass the `pytest` check (Tests workflow), with no direct or force pushes and no bypass. Work on a branch, push it, and open a PR with `gh pr create`. The user reviews and merges PRs on GitHub; Claude does not merge them.
- CI owns `journal/`. Local `scan` runs modify it; don't commit those changes (`git checkout journal/` and `git clean -fd journal/` before committing) to avoid conflicting with the daily PR.

## Design rules

- Deterministic code makes the trading decisions. The LLM only explains or summarizes and must never decide trades.
- Every signal rule should be backtested out-of-sample with costs before it is trusted.
