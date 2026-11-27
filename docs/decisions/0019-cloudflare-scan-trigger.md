# 0019. Scans are started on time by a Cloudflare Worker

- Status: Accepted
- Date: 2026-11-27
- PR: #57

## Context
GitHub's cron is best effort: the 11:17 UTC Daily scan of 26 Nov started at 20:12 UTC, almost 9 hours
late. The alerts are only useful on time (stocks before the evening, commodities before an IST morning).

## Decision
A Cloudflare Worker (`ops/cloudflare/`, free plan) with Cron Triggers at 11:17 and 22:17 UTC calls GitHub's
`workflow_dispatch` API for `daily-scan.yml` and `commodity-scan.yml` with `gated: true`. A gated run skips
like a scheduled one when main already has the session. It also dispatches `pages.yml` at 12:37 and
22:47 UTC, once each scan's journal PR has merged. The GitHub schedules stay as the fallback.

## Consequences
One external service and a fine-grained token (Actions: write on this repo only) that expires and must be
renewed (`ops/cloudflare/README.md`). If the Worker or token fails, scans fall back to GitHub's late cron.
Alternatives not taken: cron-job.org (no code in the repo), a Claude routine (spends quota on an API call),
launchd on the Mac (asleep at 03:47 IST).

## Revisit when
GitHub's cron becomes punctual, or the Worker misses runs.
