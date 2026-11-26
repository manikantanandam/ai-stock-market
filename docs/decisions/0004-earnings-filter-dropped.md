# 0004. Results-date entry filter

- Status: Rejected (code never merged)
- Date: 2026-11-25

## Context
Idea: skip entries when results are due within 5 sessions (Yahoo `get_earnings_dates`, IST dates), to avoid
results gaps through the stop. Backtested on nifty500.

## Decision
Dropped. It blocked only ~2% of trades and left out-of-sample pooled stats unchanged (swing PF 1.01 → 1.00,
leaders 1.86 → 1.87, stop-outs ~0.1 pt lower). Leaders portfolio OOS CAGR fell 19.3% → 16.9% (max DD
-19.2% → -16.3%).

## Consequences
Yahoo's results history often starts only in 2018-2022, 90-day holds pass through results anyway, and one
results gap is small next to a 2-3 ATR stop.

## Revisit when
Full NSE board-meeting history is available, or paper accounts show results gaps causing real losses.
