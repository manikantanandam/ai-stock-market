# Decision records

One short file per decision: what was decided, why, and when it is worth revisiting. Read the index before
proposing a strategy rule, filter, data source or dashboard change; a "Rejected" entry means the idea was
tested and dropped, so don't re-propose it unless its "Revisit when" condition holds.

New record: copy [template.md](template.md) to the next number, keep it under ~40 lines, add a row below.
Superseding a decision: write a new record and set the old one's status to "Superseded by NNNN".

| # | Decision | Status | Date |
|---|---|---|---|
| [0001](0001-rules-decide-llm-explains.md) | Deterministic rules decide trades; the LLM only explains | Accepted | 2026-11 |
| [0002](0002-equal-weight-hold-benchmark.md) | Judge strategies against an equal-weight hold of the same watchlist | Accepted | 2026-11-20 |
| [0003](0003-ml-ranker-no-go.md) | Price-only ML ranker: NO-GO | Rejected | 2026-11-25 |
| [0004](0004-earnings-filter-dropped.md) | Results-date entry filter | Rejected | 2026-11-25 |
| [0005](0005-squeeze-breakout-dropped.md) | Volatility-squeeze breakout rule | Rejected | 2026-11-25 |
| [0006](0006-ci-owns-journal.md) | CI owns `journal/`; merges go through PRs | Accepted | 2026-11-25 |
| [0007](0007-commodities-yahoo-inr.md) | Commodities from Yahoo global futures in INR; shorts unvalidated | Accepted | 2026-11-26 |
| [0008](0008-fundamental-history-yahoo.md) | Fundamental history estimated from Yahoo annual statements | Accepted | 2026-11-26 |
| [0009](0009-static-dashboard.md) | Dashboard is a static site: CSVs + manifest, no build step | Accepted | 2026-11 |
| [0010](0010-dashboard-view-state.md) | View state in the URL hash, stars in the browser | Accepted | 2026-11-26 |
| [0011](0011-paper-vs-backtest-verdict.md) | Paper accounts: hold benchmark and a fixed live-vs-backtest verdict | Accepted | 2026-11-26 |
| [0012](0012-chart-levels-from-paper-positions.md) | Chart stop/target come from open paper positions | Accepted | 2026-11-26 |
| [0013](0013-journal-records-planned-levels.md) | The journal records each BUY strategy's planned stop and target | Accepted | 2026-11-26 |
| [0014](0014-automatic-pr-screenshots.md) | Dashboard PRs get screenshots automatically | Accepted | 2026-11-26 |
