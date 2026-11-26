# 0005. Volatility-squeeze breakout rule

- Status: Rejected (code never merged)
- Date: 2026-11-25

## Context
`squeeze_breakout`: `breakout()` plus 20-day Bollinger Band width in the narrowest 20% of 126 bars within
the 5 bars before. Tested on nifty500 against plain `breakout` with the same exits (3 ATR stop, no target,
90 days, market filter).

## Decision
Dropped. Out-of-sample PF 1.36 vs 1.55, avg trade +2.2% vs +3.3%, stop-outs 62.5% vs 58.7%; the portfolio
beat the equal-weight hold in 2/10 years vs 6/10. In-sample PF was identical (2.27 vs 2.26).

## Consequences
A squeeze means a low ATR at entry, so the ATR-based stop is tight and gets hit more often.

## Revisit when
Only with a different stop (e.g. range-based, not ATR) and a fresh pre-registered test.
