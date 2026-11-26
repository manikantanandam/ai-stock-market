"""Estimated fundamental score history, for the dashboard's charts.

Yahoo serves only today's ratios, so past scores are rebuilt from the annual
statements (`fundamentals.get_statements`, about 4 fiscal years): ROE,
debt/equity, net margin and year-on-year revenue and earnings growth per
fiscal year, each in effect from LAG_DAYS after the year end (about when
Indian annual results are out). P/E is the latest logged trailing P/E scaled
by price and EPS changes, which cancels the reporting currency (some NSE
stocks report in USD). It only approximates the live score, whose ratios
Yahoo computes on trailing quarters, so it is shown, never traded on.
"""
import numpy as np
import pandas as pd

from aistock.fundamental.score import fundamental_score

LAG_DAYS = 60


def _div(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


def yearly_ratios(years: list[dict]) -> list[tuple[pd.Timestamp, dict, float | None]]:
    """(date the year's figures take effect, ratios in get_fundamentals' units, EPS) per fiscal year."""
    out = []
    for prev, cur in zip([None, *years[:-1]], years):
        equity = cur["equity"] if cur["equity"] and cur["equity"] > 0 else None

        def growth(k):
            return _div(cur[k] - prev[k], abs(prev[k])) if prev and cur[k] is not None and prev[k] is not None else None

        debt_to_equity = _div(cur["debt"], equity)
        ratios = {"returnOnEquity": _div(cur["net_income"], equity),
                  "debtToEquity": None if debt_to_equity is None else debt_to_equity * 100,  # Yahoo's percent
                  "revenueGrowth": growth("revenue"), "earningsGrowth": growth("net_income"),
                  "profitMargins": _div(cur["net_income"], cur["revenue"])}
        out.append((pd.Timestamp(cur["date"]) + pd.Timedelta(days=LAG_DAYS), ratios, cur["eps"]))
    return out


def score_history(years: list[dict], close: pd.Series, pe_now: float | None, close_now: float | None) -> pd.Series:
    """Estimated fundamental score per bar of `close`; NaN before the first fiscal year takes effect.

    pe_now / close_now: the latest logged trailing P/E and the close it was logged at.
    """
    periods = yearly_ratios(years)
    out = pd.Series(np.nan, index=close.index)
    if not periods:
        return out
    eps_ref = periods[-1][2]
    anchored = bool(pe_now and pe_now > 0 and close_now and eps_ref and eps_ref > 0)
    for i, (start, ratios, eps) in enumerate(periods):
        end = periods[i + 1][0] if i + 1 < len(periods) else pd.Timestamp.max
        for day in close.index[(close.index >= start) & (close.index < end)]:
            pe = pe_now * close[day] / close_now * eps_ref / eps if anchored and eps and eps > 0 else None
            out[day] = fundamental_score({**ratios, "trailingPE": pe})[0]
    return out
