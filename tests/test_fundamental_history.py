import pandas as pd
import pytest

from aistock.fundamental.history import LAG_DAYS, estimate, yearly
from aistock.fundamental.score import fundamental_score

YEARS = [
    {"date": "2024-03-31", "revenue": 100.0, "net_income": 10.0, "eps": 1.0, "equity": 100.0, "debt": 20.0,
     "current_assets": 50.0, "current_liabilities": 25.0, "shares": 10.0},
    {"date": "2025-03-31", "revenue": 120.0, "net_income": 18.0, "eps": 2.0, "equity": 200.0, "debt": 20.0,
     "current_assets": 60.0, "current_liabilities": 40.0, "shares": 10.0},
]
RATIOS = ["returnOnEquity", "debtToEquity", "revenueGrowth", "earningsGrowth", "profitMargins"]


def test_yearly_ratios_in_yahoo_units_with_growth_from_the_prior_year():
    first, second = yearly(YEARS).to_dict("records")
    assert second["start"] == (pd.Timestamp("2025-03-31") + pd.Timedelta(days=LAG_DAYS)).date().isoformat()
    assert pd.isna(first["revenueGrowth"]) and pd.isna(first["earningsGrowth"])  # no prior year
    assert {k: second[k] for k in [*RATIOS, "currentRatio"]} == pytest.approx(
        {"returnOnEquity": 0.09, "debtToEquity": 10.0, "revenueGrowth": 0.2, "earningsGrowth": 0.8,
         "profitMargins": 0.15, "currentRatio": 1.5})
    assert second["eps"] == 2.0 and second["shares"] == 10.0  # statement figures kept


def test_negative_equity_gives_no_roe_or_leverage():
    row = yearly([{**YEARS[0], "equity": -5.0}]).iloc[0]
    assert row.returnOnEquity is None and row.debtToEquity is None


def test_estimate_scales_ratios_by_price_and_per_share_figures_per_fiscal_year():
    close = pd.Series(50.0, index=pd.bdate_range("2024-01-01", "2025-12-31"))
    est = estimate(YEARS, close, {"trailingPE": 20.0, "priceToBook": 2.5, "close": 50.0})

    assert est[: pd.Timestamp("2024-05-29")].isna().all().all()  # before the first year takes effect
    first, second = est.loc[pd.Timestamp("2024-06-03")], est.loc[pd.Timestamp("2025-12-31")]
    # Same price; the first year had half the EPS and half the book value per share
    assert (first.trailingPE, first.priceToBook, first.marketCap) == (40.0, 5.0, 500.0)
    assert (second.trailingPE, second.priceToBook) == (20.0, 2.5)
    rows = yearly(YEARS).to_dict("records")
    for row, day in zip(rows, (first, second)):
        ratios = {k: None if pd.isna(row[k]) else row[k] for k in RATIOS}
        assert day.score == fundamental_score({**ratios, "trailingPE": day.trailingPE})[0]


def test_estimate_without_statements_is_blank():
    close = pd.Series(1.0, index=pd.bdate_range("2025-01-01", periods=3))
    assert estimate([], close, {"trailingPE": 20.0, "close": 1.0}).isna().all().all()


def test_to_inr_converts_flows_at_the_year_average_and_balances_at_year_end():
    from aistock.fundamental.history import to_inr

    fx = pd.Series([80.0, 90.0, 100.0], index=pd.to_datetime(["2024-06-30", "2024-12-31", "2025-03-31"]))
    usd = {**YEARS[1], "currency": "USD"}
    inr = {**YEARS[0], "currency": "INR"}

    converted, kept = to_inr([usd, inr], fx)

    assert kept == inr  # already INR
    assert converted["currency"] == "INR" and converted["converted_from"] == "USD"
    assert (converted["revenue"], converted["eps"]) == (120.0 * 90, 2.0 * 90)  # average of the fiscal year
    assert converted["equity"] == 200.0 * 100  # rate at the year end
    assert converted["shares"] == 10.0  # counts are not converted


def test_checks_flag_inconsistent_eps_big_jumps_and_missing_figures():
    from aistock.fundamental.history import checks

    assert checks(YEARS[1], YEARS[0]) == ""  # EPS 2 x 10 shares = 20 vs 18: within 15%; equity doubling is growth
    assert checks({**YEARS[1], "eps": 3.0}, None) == "EPS x shares is +67% off net income"
    assert checks({**YEARS[0], "revenue": None, "shares": 13.0}, YEARS[0]) == (
        "revenue missing; EPS x shares is +30% off net income; shares +30% vs previous year")
    assert yearly([YEARS[0], {**YEARS[1], "shares": 20.0}]).check.tolist() == [
        "", "EPS x shares is +122% off net income; shares +100% vs previous year"]
