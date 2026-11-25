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
- Prices are batch-downloaded via `prices.prefetch` (`yf.download`) before per-symbol `get_prices` reads; fundamentals have a 7-day cache
- Daily scan also runs on GitHub Actions (`.github/workflows/daily-scan.yml`, 11:17 UTC weekdays, plus a 14:47 UTC backup that runs only if the latest session is missing) and merges the journal into `main` through its own PR, so `git pull` before local scans. Linux uses CPU-only torch via `[tool.uv.sources]` in `pyproject.toml`.
- Paper trading: `uv run aistock paper` replays journal BUYs through `engine.run` (`strategy/paper.py`); the daily CI run adds its report to the job summary
- Strategies: presets live in `strategy/strategies.py` (`uv run aistock strategies`); `uv run aistock compare` backtests all presets side by side
- Backtests: `uv run aistock backtest <SYMBOL>`, `backtest-all`, `portfolio` take `--strategy <preset>`; `--rule/--regime/--rs/--sector/--stop-atr/...` override the preset. `sweep` grid-searches rules, market/RS/sector filters and exits independently of presets (3840 combos; `--rs/--no-rs` etc. pin a filter to cut the grid). Outputs CSVs to `reports/`
- Ollama (installed via Homebrew): `brew services start ollama`, then `ollama pull qwen2.5:3b`

- Tests: `uv run pytest` (all offline, synthetic data; ~3s). Single test: `uv run pytest tests/test_engine.py::test_gap_down_fills_at_open_not_stop`. `tests/conftest.py` has `bars()` for exact OHLC scenarios and `random_walk()` for frames with indicators.

No linter is configured.

## Architecture

Package `src/aistock/` (uv `--package` layout; the console script `aistock` maps to `aistock.cli:app`, a Typer app):

- `data/`: yfinance prices, cached in `.cache/prices/` per IST date and market phase (`pre`/`live`/`close`), so intraday partial bars are never reused after the close. Also fundamentals, Google News headlines, watchlist CSVs, and `sectors` (equal-weight sector indices built from NIFTY 500 members per `Industry`, because Yahoo lacks many NSE sector indices).
- `technical/`: `add_indicators` adds indicator columns. `score_series`/`technical_score` give a per-bar or latest technical score. `rules.py` holds entry rules: bool Series, True = enter at the next bar's open. It also has the entry filters: market regime, relative strength (`outperforms`: beat NIFTY over 63 and 126 bars) and `sector_trend` (sector above its SMA50).
- `fundamental/`, `sentiment/`: each returns `(score in [-1, 1], reasons)`; sentiment runs FinBERT.
- `strategy/`: `strategies.PRESETS` (rule + market filter + exits + live gates). `signal.evaluate` scores a stock once and gives each preset a BUY/WATCH verdict. `journal` keeps one row per (session date, symbol), keyed by the last bar's date, in both a per-day and a per-stock CSV, with a union of columns as the schema evolves; `journal.load()` reads the day files.
- `backtest/`: `engine.run` (single stock) and `portfolio.run` (shared capital, N slots: entries at the open use only yesterday's cash, then intraday exits). Both use the same exit rules and `costs.CostModel`. `universe` loads data and splits in-sample/out-of-sample; `sweep` grid-searches.

Only rules, market filter and exits are backtestable; fundamentals and news have no free history. Portfolio results are judged against an equal-weight hold of the same watchlist, because today's constituents carry survivorship bias.

## Git workflow

- `main` is protected by a GitHub ruleset: changes only through PRs that pass the `pytest` check (Tests workflow), with no direct or force pushes and no bypass. Work on a branch, push it, and open a PR with `gh pr create`. The user reviews and merges PRs on GitHub; Claude does not merge them.
- CI owns `journal/`. Local `scan` runs modify it; don't commit those changes (`git checkout journal/` and `git clean -fd journal/` before committing) to avoid conflicting with the daily PR.

## Design rules

- Deterministic code makes the trading decisions. The LLM only explains or summarizes and must never decide trades.
- Every signal rule should be backtested out-of-sample with costs before it is trusted.
