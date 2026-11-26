# 0007. Commodities from Yahoo global futures in INR; shorts unvalidated

- Status: Accepted
- Date: 2026-11-26

## Context
Yahoo has no MCX data, and an MCX broker API was rejected (subscription-free rule). The user asked for
BUY/SELL (short) signals.

## Decision
Use Yahoo continuous futures (GC=F, SI=F, CL=F, NG=F, HG=F, ALI=F) converted with USDINR=X into MCX quote
units, with MCX costs (`costs.FuturesCostModel`). The first `aistock commodity compare` (15 years, OOS from
~2022-05, 6 contracts): no preset beat buy-and-hold out-of-sample; short trades averaged negative (trend
short -3.1%/trade); only `channel` and `ema-cross` were positive.

## Consequences
- Commodity SELL signals are untested priors: don't present them as validated.
- New short rules must beat this baseline, and must not be tuned on the same OOS years (see 0003).
- `channel`/`channel_short` skip the volume test because Yahoo futures volume is unreliable.

## Revisit when
A free MCX data source appears, or a new out-of-sample year accumulates.
