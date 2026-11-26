# 0016. Point-in-time index constituents: parked

- Status: Rejected for now (parked)
- Date: 2026-11-26

## Context
Backtests use today's index members, so they carry survivorship bias (0002) and the conclusion so far is
"no demonstrated edge". Point-in-time NIFTY 500 membership for ~2015-2026 would remove that bias.
A feasibility check on 2026-11-26 found:
- NIFTY 500: no free, verified history. GitHub search found nothing; the official source would be the
  semi-annual index-change press releases on niftyindices.com.
- niftyindices.com didn't answer scripted requests that day (press releases, index changes, and even the
  constituent CSV `update-watchlists` uses all timed out). It wasn't worked around (0008).
- NIFTY 50 only: github.com/vishalvx/nifty-indices-datasets (MIT) rebuilds monthly membership from 2008 from
  NSE releases; unofficial "binary approximations", no exact change dates.

## Decision
Parked. Keep judging against the equal-weight hold of today's watchlist (0002). Don't use the NIFTY 50
dataset alone: the key results are on nifty500.

## Consequences
- The README roadmap item "Point-in-time index constituents" stays open.
- Backtest results stay optimistic; paper accounts (0011) remain the honest test.

## Revisit when
The user downloads the niftyindices NIFTY 500 change notices (back to ~2015) and supplies them as files, or a
free, verified NIFTY 500 membership dataset appears. Then: parse them into per-date membership and rebuild
`universe.load` around it.
