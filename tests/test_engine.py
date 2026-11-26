from dataclasses import replace

import pytest

from aistock.backtest import engine
from conftest import bars, entry_on

# Signal on bar 0 -> entry at bar 1's open (100). ATR 10, so with the default
# params stop = 80 (2 ATR) and target = 130 (3 ATR).


def only_trade(df, entries, p):
    t = engine.run(df, entries, p).trades
    assert len(t) == 1
    return t.iloc[0]


def test_target_hit(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (104, 135, 103, 120)])
    t = only_trade(df, entry_on(df, 0), free_params)
    assert (t.reason, t.entry, t.exit) == ("target", 100, 130)
    assert t.ret == pytest.approx(0.30)  # on money invested, not diluted by leftover cash


def test_gap_down_fills_at_open_not_stop(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (70, 72, 65, 68)])
    t = only_trade(df, entry_on(df, 0), free_params)
    assert (t.reason, t.exit) == ("stop", 70)


def test_gap_up_fills_at_open_above_target(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (140, 145, 139, 142)])
    t = only_trade(df, entry_on(df, 0), free_params)
    assert (t.reason, t.exit) == ("target", 140)


def test_stop_wins_when_bar_hits_both(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (100, 135, 75, 110)])
    t = only_trade(df, entry_on(df, 0), free_params)
    assert (t.reason, t.exit) == ("stop", 80)


def test_stopped_out_on_entry_bar(free_params):
    df = bars([(99, 101, 98, 100), (100, 101, 75, 78), (78, 80, 77, 79)])
    t = only_trade(df, entry_on(df, 0), free_params)
    assert (t.reason, t.exit, t.bars) == ("stop", 80, 1)


def test_time_exit_at_close(free_params):
    p = replace(free_params, max_hold=3)
    df = bars([(99, 101, 98, 100)] + [(100, 105, 95, 102)] * 5)
    t = only_trade(df, entry_on(df, 0), p)
    assert (t.reason, t.exit, t.bars) == ("time", 102, 3)


def test_no_target_rides_until_time(free_params):
    p = replace(free_params, target_atr=None, max_hold=3)
    df = bars([(99, 101, 98, 100), (100, 150, 99, 140), (140, 200, 139, 190), (190, 260, 189, 250)])
    t = only_trade(df, entry_on(df, 0), p)
    assert (t.reason, t.exit) == ("time", 250)


def test_trailing_stop_ratchets_up(free_params):
    # Trail 2 ATR (20) under the highest high: after a high of 150, the stop is 130
    p = replace(free_params, target_atr=None, trail_atr=2.0, max_hold=50)
    df = bars([(99, 101, 98, 100), (100, 110, 99, 108), (108, 150, 107, 148), (145, 146, 125, 128)])
    t = only_trade(df, entry_on(df, 0), p)
    assert (t.reason, t.exit) == ("stop", 130)


def test_open_position_closed_at_end(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (104, 108, 100, 106)])
    t = only_trade(df, entry_on(df, 0), free_params)
    assert (t.reason, t.exit) == ("end", 106)


def test_no_reentry_while_in_position(free_params):
    df = bars([(99, 101, 98, 100)] + [(100, 105, 95, 102)] * 5)
    trades = engine.run(df, entry_on(df, 0, 1, 2, 3), free_params).trades
    assert len(trades) == 1


def test_signal_on_last_bar_does_nothing(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104)])
    assert engine.run(df, entry_on(df, 1), free_params).trades.empty


def test_costs_reduce_return():
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (104, 135, 103, 120)])
    t = only_trade(df, entry_on(df, 0), engine.Params())
    assert 0.28 < t.ret < 0.30  # 30% gross minus ~0.3% costs and slippage


# Shorts mirror longs: entry at bar 1's open (100), stop 120 (2 ATR above), target 70 (3 ATR below).

def test_short_target_hit(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 96), (96, 97, 65, 80)])
    t = engine.run(df, entry_on(df), free_params, shorts=entry_on(df, 0)).trades.iloc[0]
    assert (t.side, t.reason, t.entry, t.exit) == ("short", "target", 100, 70)
    assert t.ret == pytest.approx(0.30)


def test_short_gap_up_fills_at_open_above_stop(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (130, 135, 128, 132)])
    t = engine.run(df, entry_on(df), free_params, shorts=entry_on(df, 0)).trades.iloc[0]
    assert (t.reason, t.exit) == ("stop", 130)
    assert t.ret == pytest.approx(-0.30)


def test_short_equity_marks_against_the_position(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 100), (100, 112, 99, 110)] + [(110, 112, 108, 110)] * 2)
    eq = engine.run(df, entry_on(df), free_params, shorts=entry_on(df, 0)).equity
    units = int(100_000 / (100 * 1.0012))  # room left for entry costs
    assert eq.iloc[2] == pytest.approx(100_000 - units * 10)  # price up 10 against the short


def test_short_trail_ratchets_down_from_lowest_low(free_params):
    p = replace(free_params, target_atr=None, trail_atr=1.0, max_hold=50)
    df = bars([(99, 101, 98, 100), (100, 101, 90, 92), (92, 93, 60, 62), (62, 75, 61, 74)])
    t = engine.run(df, entry_on(df), p, shorts=entry_on(df, 0)).trades.iloc[0]
    assert (t.reason, t.exit) == ("stop", 70)  # lowest low 60 + 1 ATR


def test_long_wins_when_both_fire(free_params):
    df = bars([(99, 101, 98, 100), (100, 105, 95, 104), (104, 135, 103, 120)])
    t = engine.run(df, entry_on(df, 0), free_params, shorts=entry_on(df, 0)).trades.iloc[0]
    assert t.side == "long"


def test_fractional_units_trade_above_capital(free_params):
    p = replace(free_params, capital=1_000.0)
    df = bars([(99, 101, 98, 100), (5_000, 5_100, 4_990, 5_050), (5_050, 5_100, 5_000, 5_060)], atr=50)
    assert engine.run(df, entry_on(df, 0), p).trades.empty  # less than one whole unit
    t = engine.run(df, entry_on(df, 0), replace(p, fractional=True)).trades.iloc[0]
    assert t.pnl == pytest.approx(1_000 / (5_000 * 1.0012) * 60)  # closed at the last close, 5060
