import pandas as pd
import pytest

from aistock.fundamental.history import LAG_DAYS, score_history, yearly_ratios
from aistock.fundamental.score import fundamental_score

YEARS = [
    {"date": "2024-03-31", "revenue": 100.0, "net_income": 10.0, "eps": 1.0, "equity": 100.0, "debt": 20.0},
    {"date": "2025-03-31", "revenue": 120.0, "net_income": 18.0, "eps": 2.0, "equity": 100.0, "debt": 20.0},
]


def test_yearly_ratios_in_yahoo_units_with_growth_from_the_prior_year():
    (start0, first, _), (start1, second, eps) = yearly_ratios(YEARS)
    assert start1 == pd.Timestamp("2025-03-31") + pd.Timedelta(days=LAG_DAYS)
    assert first["revenueGrowth"] is None and first["earningsGrowth"] is None  # no prior year
    assert second == pytest.approx({"returnOnEquity": 0.18, "debtToEquity": 20.0, "revenueGrowth": 0.2,
                                    "earningsGrowth": 0.8, "profitMargins": 0.15})
    assert eps == 2.0


def test_negative_equity_gives_no_roe_or_leverage():
    (_, ratios, _), = yearly_ratios([{**YEARS[0], "equity": -5.0}])
    assert ratios["returnOnEquity"] is None and ratios["debtToEquity"] is None


def test_score_history_steps_per_fiscal_year_and_scales_pe_by_price_and_eps():
    days = pd.bdate_range("2024-01-01", "2025-12-31")
    close = pd.Series(50.0, index=days)
    scores = score_history(YEARS, close, pe_now=20.0, close_now=50.0)

    assert scores[: pd.Timestamp("2024-05-29")].isna().all()  # before the first year takes effect
    first, second = yearly_ratios(YEARS)
    # Same price, half the EPS in the first year: P/E 40 there (neutral), 20 in the second (good)
    assert scores[pd.Timestamp("2024-06-03")] == fundamental_score({**first[1], "trailingPE": 40.0})[0]
    assert scores[pd.Timestamp("2025-12-31")] == fundamental_score({**second[1], "trailingPE": 20.0})[0]


def test_score_history_without_statements_is_blank():
    close = pd.Series(1.0, index=pd.bdate_range("2025-01-01", periods=3))
    assert score_history([], close, 20.0, 1.0).isna().all()
