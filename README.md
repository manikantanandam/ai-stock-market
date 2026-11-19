# aistock

A local, subscription-free AI assistant for **swing trading Indian stocks (NSE)**. It combines technical analysis, fundamentals and news sentiment into BUY / WATCH / AVOID signals with entry, stop, target and position size, and includes a backtester to check whether a rule actually makes money after costs.

Everything runs on your machine with free data (Yahoo Finance, Google News RSS) and small local models (FinBERT). It runs fine on an M1 MacBook Air with 8GB RAM.

> **Not financial advice.** This is decision support for your own research. Backtests so far show the technical rules lag buy-and-hold (see [Current findings](#current-findings)).

## Setup

Requires macOS/Linux with [uv](https://docs.astral.sh/uv/) (Python 3.12 is pinned because `pandas-ta` needs it).

```bash
brew install uv
uv sync
```

The first command that uses news downloads FinBERT (~440MB) to the Hugging Face cache.

## Usage

```bash
# One stock: technical + fundamental + news, combined signal and trade plan
uv run aistock analyze INFY
uv run aistock analyze RELIANCE --capital 500000 --no-news

# Rank the NIFTY 50 (or --symbols TCS,INFY) and log today's signals to journal/signals.csv
uv run aistock scan
uv run aistock scan --no-news --top 20

# Backtest an entry rule: one stock, or pooled across the watchlist
uv run aistock backtest INFY
uv run aistock backtest-all --rule breakout --regime --stop-atr 3 --target-atr 0 --max-hold 40

# Grid search over rules, market filter and exits
uv run aistock sweep --rank-by profit_factor --top 10
```

Symbols are bare NSE tickers (`INFY`, `M&M`); `.NS` is added internally. Run `uv run aistock <command> --help` for all options.

## How it works

| Layer | Source | Output |
|---|---|---|
| Technical | yfinance daily OHLCV, pandas-ta (SMA 50/200, RSI, MACD, ATR, volume) | score in [-1, 1] + reasons |
| Fundamental | yfinance ratios (ROE, debt/equity, growth, margins, P/E) | score in [-1, 1] + reasons |
| Sentiment | Google News RSS (+ filtered Yahoo news), FinBERT, recency-weighted (3-day half-life) | score in [-1, 1] + top headlines |
| Signal | weighted 50 / 30 / 20 | BUY if combined ≥ 0.4, technical ≥ 0.4 and no negative-news veto |

Stops and targets are ATR-based (2× / 3× ATR by default). Position size risks 1% of capital if the stop hits.

**Backtester:** long-only, daily bars, next-day-open entries, gap-aware stop/target fills, optional trailing stop, and Indian delivery costs (STT, stamp duty, exchange fees, GST, DP charge, slippage). Every run is split into in-sample (first 70%) and out-of-sample (last 30%). The sweep ranks on in-sample only and then re-tests the top picks out-of-sample.

Only the technical layer can be backtested: there is no free history of fundamentals snapshots or headlines. `aistock scan` writes daily signals to `journal/signals.csv` so the combined signal can be tested once enough history builds up.

## Project layout

```
src/aistock/
  data/         prices, fundamentals, news (cached daily in .cache/), watchlists
  technical/    indicators, technical score, entry rules (score, pullback, breakout, market regime)
  fundamental/  fundamental score
  sentiment/    FinBERT headline scoring
  strategy/     combined signal, signal journal
  backtest/     engine, cost model, universe runs, parameter sweep
  cli.py        Typer CLI (`aistock`)
journal/        daily signal log (committed)
reports/        backtest/sweep CSVs (git-ignored)
```

## Current findings

NIFTY 50, 10 years of daily data. Out-of-sample = the last 30% (~3 years):

| Setup | OOS trades | Profit factor | Avg trade | Median stock CAGR | Buy & hold CAGR |
|---|---|---|---|---|---|
| Default (score ≥ 0.4, 2 ATR stop, 3 ATR target, 20 bars) | 1086 | 1.06 | +0.1% | 0.1% | 11.7% |
| Best sweep family (3 ATR stop, no target, 40 bars, market filter) | 442 | 1.61 | +1.8% | 1.3% | 11.7% |

Wider stops and longer holds clearly help per trade, and the edge survives out-of-sample. Each stock is only invested ~35% of the time, though, so per-stock returns still trail buy-and-hold. Results are optimistic because the watchlist uses today's index constituents (survivorship bias).

## Roadmap

- [ ] Portfolio simulation: shared capital across the watchlist, benchmarked against NIFTY 50
- [ ] Longer holding periods in the sweep (40 bars sits at the edge of the grid)
- [ ] Chart-pattern detection
- [ ] Local LLM (Ollama `qwen2.5:3b`) to write plain-English reports: explanations only, never trade decisions
- [ ] Streamlit dashboard
- [ ] Backtest the combined signal from the journal once history accumulates
