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
