import pandas as pd

from aistock.technical import rules


def frame(**cols) -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    return pd.DataFrame(cols, index=pd.bdate_range("2026-01-01", periods=n))


def test_ema_cross_fires_only_on_the_crossing_bar():
    ind = frame(ema100=[9, 10, 11, 12, 11, 12], ema200=[10, 10.5, 10.5, 10.5, 11.5, 11.5])
    assert rules.ema_cross(ind).tolist() == [False, False, True, False, False, True]


def test_ema_cross_not_true_on_first_bar_when_already_above():
    ind = frame(ema100=[12, 13], ema200=[10, 10])
    assert not rules.ema_cross(ind).any()


def test_breakout_needs_new_high_volume_and_trend():
    # 24 quiet bars (high 10), then a close of 11 on double volume
    n = 25
    ind = frame(High=[10] * (n - 1) + [12], Close=[9] * (n - 1) + [11], Volume=[100] * (n - 1) + [200],
                vol_avg20=[100] * n, sma50=[2] * n, sma200=[1] * n)
    fired = rules.breakout(ind)
    assert not fired.iloc[:-1].any()
    assert fired.iloc[-1]
    assert not rules.breakout(ind.assign(Volume=100)).iloc[-1]      # no volume spike
    assert not rules.breakout(ind.assign(sma50=0.5)).iloc[-1]       # no uptrend


def test_with_regime_aligns_and_forward_fills():
    entries = pd.Series(True, index=pd.bdate_range("2026-01-01", periods=4))
    regime = pd.Series([True, False], index=[entries.index[0], entries.index[2]])
    assert rules.with_regime(entries, regime).tolist() == [True, True, False, False]
    assert rules.with_regime(entries, None).all()


def test_build_resolves_names_and_thresholds():
    assert rules.build("pullback") is rules.pullback
    assert rules.build("ema_cross") is rules.ema_cross
    assert callable(rules.build("score:0.6"))


def test_outperforms_needs_both_3_and_6_months():
    idx = pd.Series(100.0, index=pd.bdate_range("2026-01-01", periods=200))
    flat_then_up = pd.Series([100.0] * 150 + [110.0] * 50, index=idx.index)
    fired = rules.outperforms(flat_then_up, idx)
    assert not fired.iloc[:150].any()           # no outperformance, and NaN before 126 bars
    assert fired.iloc[150:150 + 63].all()       # +10% within both windows
    assert not rules.outperforms(flat_then_up, idx * 1.2 ** (idx.index >= idx.index[150])).any()


def test_sector_trend_is_close_above_sma50():
    close = pd.Series([100.0] * 60 + [90.0] * 5 + [120.0] * 5, index=pd.bdate_range("2026-01-01", periods=70))
    trend = rules.sector_trend(close)
    assert not trend.iloc[:65].any()            # flat (not above) or below the SMA
    assert trend.iloc[65:].all()


def test_ema_cross_down_fires_only_on_the_crossing_bar():
    ind = frame(ema100=[12, 11, 10, 9, 10, 9], ema200=[10.5, 10.5, 10.5, 10.5, 9.5, 9.5])
    assert rules.ema_cross_down(ind).tolist() == [False, False, True, False, False, True]


def test_channel_rules_need_the_trend_and_ignore_volume():
    n = 25
    high, low = [10.0] * n, [9.0] * n
    up = frame(High=high, Low=low, Close=[9.5] * (n - 1) + [10.5], sma50=[2.0] * n, sma200=[1.0] * n, Volume=[0] * n)
    assert rules.channel(up).iloc[-1] and not rules.channel(up.assign(sma50=0.5)).iloc[-1]
    down = up.assign(Close=[9.5] * (n - 1) + [8.5], sma50=0.5)
    assert rules.channel_short(down).iloc[-1] and not rules.channel_short(down.assign(sma50=2.0)).iloc[-1]


def test_build_score_below_for_shorts():
    from conftest import random_walk
    ind = random_walk(300, seed=2, drift=-0.004)
    fired = rules.build("score_below:-0.4")(ind)
    assert fired.any() and not (fired & rules.build("score:0.4")(ind)).any()
    assert rules.build("rally") is rules.rally
