# 0017. Daily alert to Telegram and a GitHub issue

- Status: Accepted
- Date: 2026-11-26

## Context
The daily scan and paper accounts run in CI; seeing the result meant opening the dashboard. The user wanted a
push summary and chose both channels.

## Decision
`aistock alert` (`src/aistock/alerts.py`) builds a short plain-text summary from the journal only (no LLM, 0001):
BUY count with new and dropped BUYs since the previous session, then per paper account its return, the
equal-weight hold since its start, open positions, and that session's entries and exits; ends with the
dashboard link. The Daily scan workflow runs it as its last step with `continue-on-error`, sending:
- Telegram, when the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` repo secrets exist (skipped otherwise);
- a comment on the open "Daily alerts" issue (created on first use; subscribe to it for notifications).

## Consequences
- One alert per scan run: a manual re-run of Daily scan sends another.
- Adds `issues: write` to the Daily scan workflow; this is a workflow change, so merge it outside a running
  Daily scan (0006).
- The bot token lives only in repo secrets; never print or commit it.

## Revisit when
Alerts should carry verdict changes (0011) once accounts have enough closed trades, or a channel is unused.
