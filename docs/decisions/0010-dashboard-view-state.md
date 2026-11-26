# 0010. View state in the URL hash, stars in the browser

- Status: Accepted
- Date: 2026-11-26
- PR: #45

## Context
Links to a filtered scan table or a chart range should reopen the same view. Users want to follow a few
stocks across sessions. The site is static (0009), so there is nowhere to save per-user data.

## Decision
- The hash is `#<path>?<state>`: `range` (price chart), `log` (series charts) and per table `t<N>.<key>`
  (`q`, `sort`, `asc`, `page`, `size`, `all`, `f.<column>`, plus extra filters such as `strategy`, `min`,
  `starred`). Tables number themselves in drawing order per page. Only non-default values are written,
  via `history.replaceState` (no hashchange, no history spam).
- Stars live in `localStorage` (`stars`), per browser; Today lists starred stocks.

## Consequences
- Adding or reordering tables on a page shifts `t<N>` keys, so old links to that page may restore the
  wrong table's state. Acceptable; append new tables after existing ones when possible.
- Stars don't sync across devices or appear on GitHub Pages for others.

## Revisit when
Stars need to sync (would need a backend, which 0009 rules out).
