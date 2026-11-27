# 0018. Commodities scan after the New York close, in their own workflow

- Status: Accepted
- Date: 2026-11-27
- PR: #

## Context
The commodity scan ran inside the daily stock scan (11:17 UTC, 16:47 IST). A futures day closes at
17:00 New York (21:00-22:00 UTC), so that run could only log the previous US session: on the morning of
27 Nov the latest commodity session was 25 Nov. The user wants each morning (IST) to show the previous
day's stocks and commodities.

## Decision
`commodity-scan.yml` scans commodities at 22:17 UTC weekdays (after the close in both summer and winter
time), with a 00:47 UTC backup that runs only if main lacks the session. It merges `journal/commodities`
through its own PR and sends the commodity card alone to Telegram (`aistock alert --no-stocks`). The
daily stock scan no longer scans commodities and alerts with `--no-commodities`. Pages runs at 23:37 and
02:07 UTC to publish it.

## Consequences
Two Telegram messages a day at different times: stocks around 17:00 IST, commodities around 04:00 IST.
The two journal PRs touch different folders, so they don't conflict. Email and the issue carry stocks only.

## Revisit when
GitHub delays the 22:17 run past an IST morning often enough to matter, or an MCX data source (0007)
makes Indian session times the right clock.
