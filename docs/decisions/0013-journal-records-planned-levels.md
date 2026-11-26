# 0013. The journal records each BUY strategy's planned stop and target

- Status: Accepted
- Date: 2026-11-26

## Context
`signal.levels()` computed a stop, target and size per strategy for the CLI table only, so the trade plan
behind a logged BUY was lost (0012). Recomputing from close and ATR later isn't faithful: presets change.

## Decision
`Signal.row()` writes `stops` and `targets`: ";"-joined in `buys` order (blank target where the strategy has
none), planned from the signal's close. Shares are not stored: they depend on a capital setting, and the paper
accounts size positions themselves. Commodity rows are unchanged.

The dashboard draws these as dotted "Plan stop/target · <strategies>" lines on stocks no paper account holds,
from each BUY session until a close crosses a level (a later BUY replaces them); strategies sharing a level
share a line. Held stocks keep the paper positions' actual levels (0012), so the two never stack.

## Consequences
- Rows logged before this change have no levels (CI owns the journal, 0006; no backfill).
- Levels are from the signal's close; the real entry is the next open, so paper accounts' levels differ slightly.
- The old empty `stop`/`target`/`shares` columns in early per-stock files stay as they are.

## Revisit when
Plans need sizes (store a per-strategy risk fraction rather than shares), or commodities need the same.
