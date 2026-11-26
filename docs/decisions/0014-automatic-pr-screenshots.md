# 0014. Dashboard PRs get screenshots automatically

- Status: Accepted
- Date: 2026-11-26

## Context
Reviewers check dashboard changes from the PR, so each one needs screenshots. Taking them by hand was
forgotten once (#49), and the Chrome extension isn't installed.

## Decision
`.github/workflows/pr-screenshots.yml` runs on every PR touching `src/aistock/site/` (or by hand:
`gh workflow run pr-screenshots.yml -f pr=<N>`). It builds the site from the PR's code and journal and runs
`aistock pr-screenshots <N>` (`site/screenshots.py`): headless Chrome on a free local port, images pushed to
the orphan `pr-screenshots` branch under `pr-<N>/`, and a marked section of the PR description rewritten with
links pinned to that commit (so GitHub's image cache can't show an old run). Pages: the description's
`<!-- screenshots: ... -->` line, else Today, Paper accounts, the latest session and a held stock.

## Consequences
- The bot owns the block between `<!-- screenshots:start -->` and `<!-- screenshots:end -->`; edits there are
  overwritten on the next push. Hand-made screenshots go outside it.
- Screenshots show real journal data only; a feature needing data that doesn't exist yet still needs a
  labelled sample screenshot added by hand.
- Fork PRs are skipped (read-only token). Adding this workflow is a workflow change: don't merge it while a
  Daily scan run is in progress (0006).

## Revisit when
Screenshots need interaction (clicks, hovers): switch the capture to Playwright.
