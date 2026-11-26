# 0002. Judge strategies against an equal-weight hold of the same watchlist

- Status: Accepted
- Date: 2026-11-20

## Context
Watchlists are today's index constituents, so backtests carry survivorship bias: NIFTY 500 partly consists
of stocks that joined because they rallied. Swapping one NIFTY 50 member (WIPRO → BSE) moved results by
several points. Beating the NIFTY 50 index is therefore easy and meaningless.

## Decision
The bar is a daily-rebalanced equal-weight buy-and-hold of the same watchlist, without costs
(`backtest.portfolio.equal_weight`). It shares the bias, so beating it is the fair test. NIFTY 50 is shown
for reference only.

## Consequences
- `portfolio`, `compare` and `yearly` report `excess_vs_universe`; README findings quote it.
- As of 2026-11-25 only `trend` matches the hold out-of-sample on nifty500 (+0.4%); conclusion so far:
  no demonstrated edge.
- Paper accounts use the same benchmark, built from journal closes (see 0011).

## Revisit when
Point-in-time index constituents become available (removes the bias; the index becomes a fair bar again).
