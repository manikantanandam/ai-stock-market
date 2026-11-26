"""Estimated fundamental history, for the dashboard's tables and charts.

Yahoo serves only today's ratios, so past ones are rebuilt from the annual
statements (`fundamentals.get_statements`, about 4 fiscal years): ROE,
debt/equity, net margin, current ratio and year-on-year revenue and earnings
growth per fiscal year, each in effect from LAG_DAYS after the year end
(about when Indian annual results are out). P/E and P/B are the latest
logged values scaled by price and per-share changes, which cancels the
reporting currency (some NSE stocks report in USD); market cap is shares
times close. It only approximates the live ratios, which Yahoo computes on
trailing quarters, so it is shown, never traded on.
"""
import numpy as np
import pandas as pd

from aistock.fundamental.score import fundamental_score

LAG_DAYS = 60
RATIOS = ["returnOnEquity", "debtToEquity", "revenueGrowth", "earningsGrowth", "profitMargins", "currentRatio"]


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def yearly(years: list[dict]) -> pd.DataFrame:
    """One row per fiscal year: `date` (year end), `start` (when it takes effect), the statement
    figures, and RATIOS in get_fundamentals' units (debtToEquity in percent, like Yahoo)."""
    rows = []
    for prev, cur in zip([None, *years[:-1]], years):
        g = cur.get
        equity = g("equity") if g("equity") and g("equity") > 0 else None

        def growth(k):
            return _div(g(k) - prev[k], abs(prev[k])) if prev and g(k) is not None and prev.get(k) is not None else None

        debt_to_equity = _div(g("debt"), equity)
        rows.append({"date": cur["date"],
                     "start": (pd.Timestamp(cur["date"]) + pd.Timedelta(days=LAG_DAYS)).date().isoformat(),
                     **{k: v for k, v in cur.items() if k != "date"},
                     "returnOnEquity": _div(g("net_income"), equity),
                     "debtToEquity": None if debt_to_equity is None else debt_to_equity * 100,
                     "revenueGrowth": growth("revenue"), "earningsGrowth": growth("net_income"),
                     "profitMargins": _div(g("net_income"), g("revenue")),
                     "currentRatio": _div(g("current_assets"), g("current_liabilities"))})
    return pd.DataFrame(rows)


def estimate(years: list[dict], close: pd.Series, now: dict) -> pd.DataFrame:
    """Per bar of `close`: trailingPE, priceToBook, marketCap and the fundamental score (`score`);
    NaN before the first fiscal year takes effect.

    now: the latest logged `trailingPE`, `priceToBook` and the `close` they were logged at.
    """
    out = pd.DataFrame(np.nan, index=close.index, columns=["trailingPE", "priceToBook", "marketCap", "score"])
    table = yearly(years)
    if table.empty:
        return out
    per_share = lambda row, k: _div(row.get(k), row.get("shares")) if k != "eps" else row.get("eps")

    def scaled(ratio_now: float | None, k: str, row: dict) -> pd.Series | None:
        """ratio_t = ratio_now * (close_t / close_now) * (per-share figure now / per-share figure in row's year)."""
        ref, cur = per_share(table.iloc[-1], k), per_share(row, k)
        if not (ratio_now and ratio_now > 0 and now.get("close") and ref and ref > 0 and cur and cur > 0):
            return None
        return ratio_now * close / now["close"] * ref / cur

    starts = [pd.Timestamp(d) for d in table.start] + [pd.Timestamp.max]
    for i, row in table.iterrows():
        mask = (close.index >= starts[i]) & (close.index < starts[i + 1])
        if not mask.any():
            continue
        for col, ratio_now, k in (("trailingPE", now.get("trailingPE"), "eps"), ("priceToBook", now.get("priceToBook"), "equity")):
            series = scaled(ratio_now, k, row)
            if series is not None:
                out.loc[mask, col] = series[mask]
        if row.get("shares"):
            out.loc[mask, "marketCap"] = close[mask] * row["shares"]
        ratios = {k: (None if pd.isna(row[k]) else row[k]) for k in RATIOS}
        out.loc[mask, "score"] = [fundamental_score({**ratios, "trailingPE": None if pd.isna(pe) else pe})[0]
                                  for pe in out.loc[mask, "trailingPE"]]
    return out
