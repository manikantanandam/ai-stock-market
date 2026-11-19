import pytest

from aistock.backtest.costs import CostModel


def test_buy_cost_on_one_lakh():
    # exchange + SEBI with 18% GST, plus 0.1% STT and 0.015% stamp duty
    expected = 100_000 * (0.0000297 + 0.000001) * 1.18 + 100_000 * (0.001 + 0.00015)
    assert CostModel().buy_cost(100_000) == pytest.approx(expected)
    assert CostModel().buy_cost(100_000) == pytest.approx(118.62, abs=0.01)


def test_sell_cost_has_no_stamp_but_flat_dp_charge():
    expected = 100_000 * (0.0000297 + 0.000001) * 1.18 + 100_000 * 0.001 + 15.93
    assert CostModel().sell_cost(100_000) == pytest.approx(expected)


def test_brokerage_attracts_gst():
    base = CostModel().buy_cost(100_000)
    with_brokerage = CostModel(brokerage=0.0003).buy_cost(100_000)
    assert with_brokerage - base == pytest.approx(100_000 * 0.0003 * 1.18)


def test_round_trip_is_roughly_a_quarter_percent():
    c = CostModel()
    assert 0.002 < (c.buy_cost(100_000) + c.sell_cost(100_000)) / 100_000 < 0.003
