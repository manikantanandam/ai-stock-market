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

Run the test suite (offline, a few seconds) with `uv run pytest`. It also runs on every PR as the required `pytest` check.

The first command that uses news downloads FinBERT (~440MB) to the Hugging Face cache.

## Usage

```bash
# One stock: technical + fundamental + news, combined signal and trade plan
uv run aistock analyze INFY
uv run aistock analyze RELIANCE --capital 500000 --no-news

# Rank a watchlist (nifty50 default, nifty500, or --symbols TCS,INFY) and log signals to journal/days/ and journal/stocks/
uv run aistock scan
uv run aistock scan --watchlist nifty500 --news candidates --top 20

# Refresh index constituents from niftyindices.com (NSE rebalances twice a year)
uv run aistock update-watchlists
uv run aistock site --serve    # dashboard of journal + reports at localhost:8000

# Strategy presets (swing, trend, breakout, pullback, ema-cross)
uv run aistock strategies
uv run aistock compare --watchlist nifty500            # all presets side by side, portfolio backtest

# Backtest a strategy: one stock, or pooled across the watchlist (options override the preset)
uv run aistock backtest INFY --strategy trend
uv run aistock backtest-all --strategy breakout --max-hold 40

# Grid search over rules, market filter and exits
uv run aistock sweep --rank-by profit_factor --top 10

# Shared-capital portfolio (N slots) vs NIFTY 50 and an equal-weight hold of the watchlist
uv run aistock portfolio --watchlist nifty500 --strategy trend --positions 10
```

```bash
# Paper-trade every journaled BUY forward (next-day open entry, preset exits, costs) vs NIFTY 50
uv run aistock paper
uv run aistock paper --strategy trend --since 2026-12-01
```

`paper` is the bias-free scorecard. Each signal was recorded before its outcome was known, and it includes the fundamental and news gates that backtests can't test. Repeat signals for a strategy and stock are ignored while a position is open. The daily GitHub run adds the report to its job summary.

`scan` and `analyze` run every preset by default (`--strategy trend,breakout` to narrow it). A stock is BUY if any strategy says BUY, and the journal's `buys` column records which ones did.

Backtest commands take `--watchlist nifty50|nifty500`. For large lists, `scan` fetches news only for stocks that could still reach BUY (`--news candidates`, the default above 60 stocks). Recent listings with under 200 sessions of history are skipped.

**Daily scan on GitHub Actions:** `.github/workflows/daily-scan.yml` runs `aistock scan --watchlist nifty500` at 16:47 IST on weekdays (with a 20:17 IST backup if that session is missing) and merges `journal/` into `main` through an automated PR. `main` is protected: every change, including the daily journal update, goes through a pull request. Run `git pull` before running `scan` locally, and don't commit local journal changes. You can also trigger it from the Actions tab (`workflow_dispatch`).

Symbols are bare NSE tickers (`INFY`, `M&M`); `.NS` is added internally. Run `uv run aistock <command> --help` for all options.

## How it works

| Layer | Source | Output |
|---|---|---|
| Technical | yfinance daily OHLCV, pandas-ta (SMA 50/200, RSI, MACD, ATR, volume) | score in [-1, 1] + reasons |
| Fundamental | yfinance ratios (ROE, debt/equity, growth, margins, P/E) | score in [-1, 1] + reasons |
| Sentiment | Google News RSS + ET / Mint / BusinessLine RSS (+ filtered Yahoo news), FinBERT, recency-weighted (3-day half-life) | score in [-1, 1] + top headlines |
| Combined | weighted 50 / 30 / 20 | used for ranking, AVOID, and the swing gate |

**Strategies** (`src/aistock/strategy/strategies.py`): each preset is an entry rule, an optional market filter (NIFTY 50 above its 200-day SMA), ATR-based exits, and live gates. A preset says BUY when its rule fires on the latest bar, its gates pass (swing: combined ≥ 0.4; others: fundamental ≥ 0), and news isn't clearly negative.

| Preset | Entry | Exits |
|---|---|---|
| swing | technical score ≥ 0.4 | 2 ATR stop, 3 ATR target, 20 days |
| trend | technical score ≥ 0.4, market filter | 3 ATR stop, no target, 90 days |
| breakout | close > 20-day high on 1.5× volume, SMA50 > SMA200, market filter | 3 ATR stop, no target, 90 days |
| pullback | uptrend with RSI < 40 | 2 ATR stop, 3 ATR target, 20 days |
| ema-cross | EMA100 crosses above EMA200 | 3 ATR stop, no target, 90 days |

Position size risks 1% of capital if the stop hits.

**Backtester:** long-only, daily bars, next-day-open entries, gap-aware stop/target fills, optional trailing stop, and Indian delivery costs (STT, stamp duty, exchange fees, GST, DP charge, slippage). Every run is split into in-sample (first 70%) and out-of-sample (last 30%). The sweep ranks on in-sample only and then re-tests the top picks out-of-sample.

Only the technical layer can be backtested: there is no free history of fundamentals snapshots or headlines. `aistock scan` writes daily signals to `journal/days/<date>.csv` and `journal/stocks/<SYMBOL>.csv` so the combined signal can be tested once enough history builds up.

## Data sources

All sources are free and need no API key or account.

| Data | Source | Cache | Pros | Cons |
|---|---|---|---|---|
| Price data (daily OHLCV, NIFTY 50 `^NSEI`) | Yahoo Finance via `yfinance` (`data/prices.py`), batch-downloaded with `yf.download` | `.cache/prices/` per IST date and market phase | Free, no key; 10+ years of daily history; split/dividend-adjusted; covers all NSE stocks and the index; fast batch download | Unofficial scraping API that can break or rate-limit without notice; bars can lag after the close; occasional bad ticks or missing days; no delisted stocks (survivorship bias); no history for many NSE sector indices |
| Technical indicators (SMA, EMA, RSI, MACD, ATR, volume) | Computed locally with `pandas-ta` from the price data (`technical/indicators.py`) | Not cached (cheap to recompute) | Deterministic and reproducible; works offline; full history, so every rule can be backtested | Only as good as the Yahoo prices it reads; standard textbook formulas, so no edge by itself |
| Sector trends | Equal-weight indices built locally from NIFTY 500 members per industry (`data/sectors.py`) | Uses the price cache | Covers every industry the same way, including sectors Yahoo lacks | Today's constituents only (survivorship bias); equal weight differs from official cap-weighted NSE indices |
| Fundamentals (P/E, P/B, ROE, debt/equity, growth, margins) | Yahoo Finance `Ticker.info` via `yfinance` (`data/fundamentals.py`) | `.cache/fundamentals/`, 7 days | Free; one call gives many ratios; good enough to screen out weak balance sheets | Current snapshot only, with no history, so it can't be backtested; fields are often missing or stale for smaller NSE stocks; ~500 slow per-stock calls (hence the weekly cache) |
| News headlines | Google News RSS (India edition, last 14 days), publishers' own RSS feeds (Economic Times, Mint, BusinessLine: markets and companies sections), plus Yahoo news filtered to the company name (`data/news.py`) | `.cache/news/` per day | Free, no key; Google covers most Indian outlets, and the publisher feeds add same-day headlines straight from the source, so news doesn't hinge on Google alone | Headlines only, no article text; matched by company name or upper-case symbol, so some noise (e.g. the stock BSE vs the exchange) and some misses; publisher feeds keep only their latest ~50 items; past headlines only via Google News date filters (`after:`/`before:`), which are good for recent weeks but thin for older dates and small caps, so sentiment still can't be backtested reliably (the journal now keeps each day's reasons); Moneycontrol's feeds are stale and Business Standard blocks scripts, so neither is used |
| News sentiment | FinBERT (`ProsusAI/finbert`, Hugging Face), run locally (`sentiment/finbert.py`) | Model in `~/.cache/huggingface` | Finance-tuned model; free; runs on an 8GB M1 and in CI on CPU | Trained on English financial text, not Indian market slang; scores headlines in isolation, so sarcasm and context are lost; ~400MB download |
| Watchlists (NIFTY 50 / NIFTY 500 constituents) | niftyindices.com constituent CSVs (`data/watchlists.py`), committed in `data/watchlists/` | Committed snapshot | Official NSE index source; includes the industry used for sectors | Changes only at the twice-yearly rebalance, so it needs a manual `update-watchlists`; site occasionally blocks scripted downloads |

## Project layout

```
src/aistock/
  data/         prices, fundamentals (7-day cache), news (cached in .cache/), watchlist CSVs
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

**Portfolio test** (shared capital, 10 slots, 3 ATR stop, no target, 90 bars). CAGR, in-sample (2017–2024) → out-of-sample (2024–2026):

| Watchlist | Strategy | Strategy + market filter | NIFTY 50 index | Equal-weight hold of watchlist |
|---|---|---|---|---|
| NIFTY 50 | 23.5% → 3.6% | 17.3% → -3.6% | 13.0% → 5.4% | 24.6% → 11.3% |
| NIFTY 500 | 40.7% → 9.5% | 37.8% → 14.3% | 13.0% → 5.4% | 26.6% → 13.9% |

On NIFTY 500 the strategy beats the NIFTY 50 index, but not an equal-weight hold of the same stocks. That comparison shares the survivorship bias, so it is the fair test. Today's NIFTY 500 is partly made up of stocks that joined *because* they rallied. Swapping a single NIFTY 50 constituent (WIPRO → BSE) moved the NIFTY 50 results by several points. Conclusion so far: **no demonstrated edge**.

**Strategy comparison** (`aistock compare --watchlist nifty500`, 10 slots). CAGR, in-sample → out-of-sample (2024–2026), and the out-of-sample margin vs an equal-weight hold:

| Preset | CAGR | vs EW hold (OOS) | OOS max DD |
|---|---|---|---|
| swing | 14.7% → -10.8% | -24.6% | -39.0% |
| trend | 37.8% → 14.3% | +0.4% | -24.5% |
| breakout | 33.6% → 9.3% | -4.6% | -21.1% |
| pullback | -0.1% → 4.5% | -9.3% | -8.2% |
| ema-cross | 20.3% → -1.0% | -14.8% | -25.5% |
| *EW hold of NIFTY 500* | 26.6% → 13.9% | | -21.2% |

Only trend matches the equal-weight hold. On NIFTY 50, every preset loses money out-of-sample.

## Roadmap

- [x] Portfolio simulation: shared capital, benchmarked against NIFTY 50 and equal-weight watchlist
- [x] Longer holding periods in the sweep (60/90 bars)
- [x] NIFTY 500 watchlist
- [ ] Point-in-time index constituents to remove survivorship bias
- [ ] Chart-pattern detection
- [ ] Local LLM (Ollama `qwen2.5:3b`) to write plain-English reports: explanations only, never trade decisions
- [ ] Streamlit dashboard
- [x] Paper-trade journaled signals forward (`aistock paper`)
