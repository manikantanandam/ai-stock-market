# 0012. Chart stop/target come from open paper positions

- Status: Accepted
- Date: 2026-11-26
- PR: #47

## Context
#45 drew each BUY's stop and target on the stock price chart from journal rows. The journal's `stop`,
`target` and `shares` columns exist but are always empty: `signal.levels()` computes them for the CLI
table only, so the lines never drew.

## Decision
Draw levels from open paper positions instead (`positions.csv`: current `stop`, `target`), from the entry
date, one pair per strategy holding the stock. Closed paper trades are marked at their exit price with
strategy, reason and return in the tooltip.

## Consequences
- An account keeps only the current stop, so a trailing stop shows as a flat line at today's level, not
  its history.
- Stocks no paper account holds show the journal's planned levels instead (0013).

## Revisit when
Accounts save stop history (would show trailing stops moving).
