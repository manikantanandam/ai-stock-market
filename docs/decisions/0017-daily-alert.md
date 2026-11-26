# 0017. Daily alert to Telegram, email and a GitHub issue

- Status: Accepted
- Date: 2026-11-26

## Context
The daily scan and paper accounts run in CI; seeing the result meant opening the dashboard. The user wanted a
push summary and chose both channels.

## Decision
`aistock alert` (`src/aistock/alerts.py`) builds a short plain-text summary from the journal only (no LLM, 0001):
BUY count with new and dropped BUYs since the previous session, then per paper account its return, the
equal-weight hold since its start, open positions, and that session's entries and exits; ends with the
dashboard link. Each channel gets its own rendering of the same summary: Telegram HTML (bold titles,
accounts as a ~32-character monospace table that fits a phone), markdown for the issue, plain text for email. `--date` rebuilds it for any journaled session (open positions as of that date come from
positions plus closed trades). The Daily scan workflow runs it as its last step with `continue-on-error`; the
Send alert workflow (`alert.yml`) runs it by hand for a chosen date (default: the latest session). Channels:
- Telegram, when the `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` repo secrets exist;
- email over SMTP (stdlib, SSL), when `SMTP_USERNAME` and `SMTP_PASSWORD` exist (Gmail app password by
  default; `ALERT_EMAIL_TO`, `SMTP_SERVER`, `SMTP_PORT` optional);
- a comment on the open "Daily alerts" issue (created on first use; off by default in the manual workflow).
A channel without its secrets is skipped; a failing one is reported and the others still go out (the
command then exits non-zero, which fails the manual run).

## Consequences
- One alert per scan run: a manual re-run of Daily scan sends another.
- Adds `issues: write` to the Daily scan workflow; this is a workflow change, so merge it outside a running
  Daily scan (0006).
- Tokens and passwords live only in repo secrets; never print or commit them, and don't paste them into a
  Claude session (set them with `gh secret set NAME` in your own terminal, which prompts without echo).

## Revisit when
Alerts should carry verdict changes (0011) once accounts have enough closed trades, or a channel is unused.
