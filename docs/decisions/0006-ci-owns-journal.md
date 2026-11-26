# 0006. CI owns `journal/`; changes reach `main` only through PRs

- Status: Accepted
- Date: 2026-11-25

## Context
The daily scan runs on GitHub Actions (`daily-scan.yml`, 11:17 UTC weekdays plus a 14:47 backup) and merges
the journal into `main` through its own PR. `main` is protected: PRs must pass `pytest`, no direct or force
pushes, no bypass.

## Decision
- Only CI commits `journal/`. Local `scan` runs modify it; discard those changes (`git checkout journal/`,
  `git clean -fd journal/`) before committing, and `git pull` before scanning locally.
- Claude works on branches and opens PRs; the user reviews and merges.
- Don't merge a PR touching `.github/workflows/*` while a Daily scan run is in progress: GITHUB_TOKEN can't
  push a ref whose workflow files differ from `main`, so the run's journal PR is rejected and the session
  is lost (seen 2026-11-25, run 45613507383). If it happens, re-dispatch Daily scan after the merge.

## Revisit when
The journal moves out of git (e.g. to a database or release assets).
