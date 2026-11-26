# 0011. Paper accounts: hold benchmark and a fixed live-vs-backtest verdict

- Status: Accepted
- Date: 2026-11-26
- PR: #46, #47

## Context
Paper accounts (`journal/paper/<strategy>/`) trade the daily nifty500 scan forward. Two questions: are they
beating the fair benchmark (0002), and are they behaving like their backtest? Live samples are tiny at
first, and eyeballing small samples invites over-reaction.

## Decision
- Benchmark: `site.equal_weight` builds the daily-rebalanced equal-weight hold of each session's scanned
  stocks from `journal/days` closes (same method as `portfolio.equal_weight`), stored in the manifest.
  Accounts show return, hold return and excess over each account's own date range.
- `compare` also writes `compare_<watchlist>_<date>_stats.csv`. The Paper accounts page sets each account's
  closed trades (win rate, avg trade, profit factor, avg bars) against the latest out-of-sample row,
  nifty500 first.
- Verdict, fixed in advance: "Too few trades" below 10 closed trades; then "Below/Above backtest" when the
  live average trade is more than 2 standard errors from the backtest's, else "In line".

## Consequences
- Backtest trades last ~50 bars, so verdicts need weeks; until then every account reads "Too few trades".
- The rule is a flag, not an automatic switch-off: changing a strategy still needs a new out-of-sample test.
- The hold has no costs while accounts pay them (same as backtests).

## Revisit when
Accounts reach ~30 closed trades each (consider comparing win rate and stop-out share too), or the
watchlist the scan uses changes.
