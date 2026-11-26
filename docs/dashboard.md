# Dashboard

Static page in `src/aistock/site/static/` (`index.html`, `app.js`, `style.css`), built by `src/aistock/site/__init__.py`
(why static: [decision 0009](decisions/0009-static-dashboard.md)).

- Build and serve: `uv run aistock site --serve` (localhost:8000; `--no-prices` skips the per-stock price download)
- Published: `.github/workflows/pages.yml` (journal + the latest weekly run's `reports/` artifacts)
- Tests: `tests/test_site.py` covers the build (kinds, manifest, price history, equal-weight series); the page has no tests

## Build output

`site/data/` mirrors every CSV under `journal/` and `reports/`. `data/manifest.json` holds:

| Field | What |
|---|---|
| `files` | per CSV: `path`, `group` (nav section, `null` = not in nav), `kind` (which view draws it), `title`, `columns`, `rows` |
| `groups` | nav section order |
| `accounts` | `journal/paper/<strategy>/account.json` per strategy |
| `equal_weight` | session date → level of the equal-weight hold of scanned stocks ([0011](decisions/0011-paper-vs-backtest-verdict.md)) |
| `summaries` | symbols with `data/summaries/stocks/<SYM>.json` / `summaries/contracts/<C>.json`: that page's LLM notes, paper trades and open positions, gathered at build time |
| `decisions` | session dates with `data/decisions/<date>.csv`: every paper account's decisions on that session's BUYs (`strategy` column added) |
| `prices`, `fundamentals` | symbols with `data/prices/<SYM>.csv` / `data/fundamentals/<SYM>.csv` (full Yahoo history, estimated fundamentals) |

Kinds (`site.kind_of`): `day`, `notes`, `stock`, `cday`, `cnotes`, `cheadlines`, `contract`, `trades`, `series`, `yearly`, `table`.

## Pages

| Route | Renderer | Shows | Loads |
|---|---|---|---|
| `#today` (default) | `renderToday` | latest session tiles, changes vs previous session, BUY table, what the accounts did with the previous session's BUYs, paper accounts table + return chart, starred stocks | latest 2 day files, the previous session's decisions file, paper equity/positions/trades |
| `#accounts` | `renderAccounts` | accounts vs hold, return chart, live vs backtest, open positions, closed trades, settings | paper files, latest `compare_*_stats.csv` |
| `#journal/days/<date>.csv` | `renderDay` | tiles, sector and score charts, changes card, scan table (with each account's decision on every BUY), decisions card, LLM notes | that day + previous day, its notes, its decisions file |
| `#journal/stocks/<SYM>.csv` | `renderStock` | price chart (BUYs; paper stops/targets/exits if held, else the journal's planned levels), scores, paper positions/trades, daily and fiscal tables, notes, logged sessions | its journal CSV, price/fundamental history, its summary JSON (~5 requests however long the journal gets) |
| commodity day / contract | `renderCommodityDay` / `renderStock` | same idea for futures | |
| reports | `renderTrades` / `renderSeries` / `renderYearly` / `table` | by kind | the file |

Nav: one search box for every page (symbol → stock page; else "title · group"), sessions folded by month,
paper account files folded under Overview.

## Conventions in `app.js`

- `table(rows, columns, opts)` is the one table: search, filters, `more` (custom filters), sort, paging,
  "All columns". Its state lives in the URL hash ([0010](decisions/0010-dashboard-view-state.md)).
- Columns whose name contains `ret`, `rate`, `return`, `excess`, ... are fractions shown as % (`PCT`);
  name new fraction columns accordingly or give them a cell renderer.
- Scores in [-1, 1] use `scoreCell`; reasons use `reasonsCell` (toned chips).
- Colors come from CSS tokens (`--s1..--s8`, `--good`, `--critical`, `--ink-3`), light and dark.
- Heavy data is computed at build time (manifest fields, `summaries/`, `decisions/`), not derived from many CSVs in
  the browser: no page should load a file per session.

## Screenshots (for PRs)

Automatic ([0014](decisions/0014-automatic-pr-screenshots.md)): the PR screenshots workflow runs on every PR
touching `src/aistock/site/` and rewrites the description's screenshots section. To pick pages, put a line like
`<!-- screenshots: today accounts journal/stocks/INOXINDIA.csv?range=1M -->` in the description and push (or
`gh workflow run pr-screenshots.yml -f pr=<N>`). Locally, after `uv run aistock site`:

```sh
uv run aistock pr-screenshots <N> [--page today --page accounts ...] [--theme light]   # needs gh auth and push access
```

It serves `site/` on a free port, so a running `--serve` doesn't clash, and captures in dark theme once the page sets
`body[data-shown]` (`show` in `app.js`; keep that line when changing `show`). Don't capture while a build runs: it
deletes `site/data` first. Images live on
the orphan `pr-screenshots` branch (`pr-<N>/*.png`), linked by commit via raw.githubusercontent.com.
