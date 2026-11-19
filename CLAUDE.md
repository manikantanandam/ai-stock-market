# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Local, subscription-free AI assistant for swing trading Indian stocks (NSE/BSE, daily candles). It combines technical, fundamental, news-sentiment and pattern analysis into trade ideas (entry/stop/target plus rationale). The full roadmap is in `~/.claude/plans/i-want-to-have-parsed-spring.md`.

Target hardware is an M1 with 8GB RAM, so only small local models work: FinBERT for sentiment and Ollama `qwen2.5:3b` for writing text.

## Commands

- Install/sync deps: `uv sync` (Python 3.12 is pinned, because `pandas-ta` requires >=3.12)
- Add a dependency: `uv add <pkg>`
- Run the CLI: `uv run aistock analyze <SYMBOL>` (bare NSE symbol, e.g. `INFY`; `.NS` is appended internally; `^`-prefixed index tickers like `^NSEI` are passed through)
- Daily watchlist scan (appends to `journal/signals.csv`): `uv run aistock scan [--no-news]`
- Backtests: `uv run aistock backtest <SYMBOL>`, `uv run aistock backtest-all`, `uv run aistock sweep` (all take `--rule score:0.4|pullback|breakout`, `--regime`, ATR exit options; outputs CSVs to `reports/`)
- Ollama (installed via Homebrew): `brew services start ollama`, then `ollama pull qwen2.5:3b`

There are no tests or linters configured yet.

## Architecture

Package `src/aistock/` (uv `--package` layout; the console script `aistock` maps to `aistock.cli:app`, a Typer app):

- `data/`: data fetching. `prices.get_prices` pulls yfinance daily OHLCV, strips the timezone, and caches to `.cache/prices/<SYMBOL>_<date>.parquet` (one file per day, so data refreshes daily).
- `technical/`: `add_indicators` adds columns to the OHLCV frame. `technical_score` returns `(score in [-1, 1], reasons)` for the latest bar.
- `cli.py`: commands compose the modules. ATR-based stop (2×ATR) and target (3×ATR) are computed here.

Planned modules follow the same pattern: each analysis area (`fundamental/`, `sentiment/`) exposes a score in [-1, 1] plus human-readable reasons, `strategy/` merges them, `backtest/` validates rules including Indian costs (brokerage, STT, slippage), and `llm/` + `report/` write the output, with a Streamlit `app/` after the CLI.

## Design rules

- Deterministic code makes the trading decisions. The LLM only explains or summarizes and must never decide trades.
- Every signal rule should be backtested out-of-sample with costs before it is trusted.
