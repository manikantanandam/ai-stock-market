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
