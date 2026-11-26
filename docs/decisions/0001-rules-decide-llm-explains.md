# 0001. Deterministic rules decide trades; the LLM only explains

- Status: Accepted
- Date: 2026-11 (project start)

## Context
The target machine is an M1 with 8 GB RAM, so only small local models run (FinBERT, Ollama `qwen2.5:3b`).
A 3B model can't be backtested, isn't reproducible across versions, and invents figures.

## Decision
Entries, exits, sizing and verdicts (BUY/WATCH/SELL/EXIT/HOLD) come from code in `technical/`, `strategy/`
and `backtest/`. The LLM writes notes that explain a verdict already reached (`llm/notes.py`, temperature 0).
A note quoting any figure not in the signal's facts is retried once, then withheld.

## Consequences
- Every signal rule must be backtested out-of-sample with costs before it is trusted.
- Only rules, the market filter and exits are backtestable; fundamentals and news have no free history,
  so their scores shape ranking and display, not validated edges.
- The dashboard labels notes as LLM-written and never sorts or filters trades by them.

## Revisit when
Never for "LLM picks trades". A larger model only changes note quality.
