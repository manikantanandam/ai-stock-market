# 0009. Dashboard is a static site: CSVs + manifest, no build step

- Status: Accepted (supersedes the README roadmap's "Streamlit dashboard")
- Date: 2026-11

## Context
The dashboard must publish free on GitHub Pages from CI artifacts and run locally on 8 GB RAM.

## Decision
`aistock site` copies every CSV under `journal/` and `reports/` into `site/data/` and writes
`data/manifest.json` (path, group, kind, columns, rows per file, plus account settings and the
equal-weight series). One static page (`src/aistock/site/static/`: plain JS, Chart.js and PapaParse from
cdnjs) picks a view per file *kind*, so new reports appear without page changes. Details:
[docs/dashboard.md](../dashboard.md).

## Consequences
- No server, no framework, no bundler; anything the page needs must be a CSV or a manifest field.
- Heavy derived data (price history, benchmark series) is computed at build time, not in the browser:
  loading every session file on a page doesn't scale (~300 KB each, ~250 a year).
- Pages can't list directories, hence the manifest.

## Revisit when
The page needs to write data (it can't), or per-page loads exceed a few MB.
