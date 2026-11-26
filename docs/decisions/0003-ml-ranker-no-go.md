# 0003. Price-only ML ranker: NO-GO

- Status: Rejected
- Date: 2026-11-25
- PR: #11

## Context
`ml/` trains a gradient-boosted ranker on 18 causal price features (label: 20-session forward excess return
vs NIFTY), evaluated walk-forward with `aistock ml-eval` against today's ranking (technical score, then
126-day momentum). Per-date ranked features were agreed in advance as the last attempt.

## Decision
NO-GO on nifty500: mean rank IC +0.021 (ranked features; +0.015 raw) vs +0.022 for the technical-score
baseline, beating it in only 1 of 6 test years. The ranker stays in the repo but ranks nothing live.

## Consequences
- No more hyperparameter or feature tuning on the 2021-2026 test years: each pass is data snooping, so a
  later "pass" would be untrustworthy.
- SMA200 slope as a tie-break (`leaders-slope`) was chosen after seeing this data; judge it by its paper
  account, not by backtests.

## Revisit when
Genuinely new information (delivery %, earnings data) or new out-of-sample years exist.
