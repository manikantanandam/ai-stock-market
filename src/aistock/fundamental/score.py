"""Rule-based fundamental quality score.

Thresholds are absolute for now; sector-relative scoring comes later.
yfinance reports debtToEquity as a percentage (8.5 means 0.085x).
"""


def _check(value, good, bad, weight, label, fmt, why):
    """+weight if good(value), -weight if bad(value), 0 if neutral or missing."""
    if value is None:
        why.append(f"{label}: n/a")
        return 0.0
    text = f"{label} {fmt(value)}"
    if good(value):
        why.append(f"{text} (good)")
        return weight
    if bad(value):
        why.append(f"{text} (weak)")
        return -weight
    why.append(text)
    return 0.0


def fundamental_score(f: dict) -> tuple[float, list[str]]:
    """Score in [-1, 1] from a fundamentals snapshot, with reasons."""
    pct = lambda v: f"{v * 100:.1f}%"
    why: list[str] = []
    score = sum([
        _check(f.get("returnOnEquity"), lambda v: v >= 0.15, lambda v: v < 0.08, 0.25, "ROE", pct, why),
        _check(f.get("debtToEquity"), lambda v: v < 50, lambda v: v > 150, 0.2, "Debt/Equity",
               lambda v: f"{v / 100:.2f}x", why),
        _check(f.get("revenueGrowth"), lambda v: v >= 0.10, lambda v: v < 0, 0.2, "Revenue growth", pct, why),
        _check(f.get("earningsGrowth"), lambda v: v >= 0.10, lambda v: v < 0, 0.15, "Earnings growth", pct, why),
        _check(f.get("profitMargins"), lambda v: v >= 0.12, lambda v: v < 0.04, 0.1, "Net margin", pct, why),
        _check(f.get("trailingPE"), lambda v: 0 < v <= 25, lambda v: v <= 0 or v > 60, 0.1, "P/E",
               lambda v: f"{v:.1f}", why),
    ])
    return max(-1.0, min(1.0, score)), why
