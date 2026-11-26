# 0008. Fundamental history estimated from Yahoo annual statements

- Status: Accepted
- Date: 2026-11-26
- PR: #38

## Context
Yahoo only serves today's fundamentals, so the journal stores raw ratios (`f_*`) per session. Stock pages
also want a longer history.

## Decision
Estimate P/E, P/B, market cap and the fundamental score per day from Yahoo annual statements
(`fundamental/history.py`, ~4 fiscal years; correlation 0.63 with logged scores). Ratios take effect ~60
days after the fiscal year end. Financial Services figures carry a warning (Yahoo's bank/NBFC statements
don't match filings).

## Consequences
- NSE returns 403 to scripted requests, and bypassing it with browser-spoofed headers was refused; BSE is
  similar. Screener/Moneycontrol scraping breaks ToS; paid APIs break the subscription-free rule. Don't
  re-propose these.
- Fundamentals stay unbacktestable (0001).

## Revisit when
The user supplies manually downloaded result files, or accepts a paid API.
