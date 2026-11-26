# 0015. Paper accounts record what they did with every BUY

- Status: Accepted
- Date: 2026-11-26

## Context
Strategies often fire on the same stocks, yet their paper accounts diverge. With 10 slots and ~100 BUYs a
session, most BUYs are never traded, and nothing showed which ones or why. Judging live results (0011) needs
that: an account can lag simply because its ranking filled the slots with the wrong stocks.

## Decision
`account.advance` writes one row per BUY offered to an account into `journal/paper/<strategy>/decisions.csv`:
signal date, entry date (next open), rank among the candidates, and the outcome: entered, entered + stopped,
no slot, held, no data, no cash. Recording only: entry order and the slot cut are unchanged (a declined buy
still doesn't pass its slot to the next candidate, as in `portfolio.run`). The dashboard shows a "Paper
accounts" column on session pages and a per-strategy card on Today for the previous session's BUYs.

## Consequences
- Decisions exist from the first CI run with this code; earlier sessions have none (no backfill: CI owns the
  journal, 0006, and saved accounts don't replay past sessions).
- The latest session's BUYs show no decisions until the next session's run.
- The file grows by ~100-300 rows a session per account; fine for years in CSV.

## Revisit when
The file grows too large for the page (then summarise per session at build time), or ranking is changed and
needs comparing against what the old ranking would have entered.
