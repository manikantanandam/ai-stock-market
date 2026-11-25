import numpy as np
import pandas as pd
import pytest

from aistock.backtest import walkforward
from aistock.ml import model as ml
from aistock.ml.features import FEATURES


def panel(signal: float, years: int = 4, n_sym: int = 20, seed: int = 0) -> pd.DataFrame:
    """Synthetic panel: the label depends on `sma200_slope` with strength `signal`, plus noise."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=252 * years)
    idx = pd.MultiIndex.from_product([dates, [f"S{i}" for i in range(n_sym)]], names=["date", "symbol"])
    df = pd.DataFrame(rng.normal(size=(len(idx), len(FEATURES))).astype("float32"), index=idx, columns=FEATURES)
    df["label"] = (signal * df.sma200_slope + rng.normal(size=len(idx))).astype("float32")
    df["tech_score"] = np.round(rng.uniform(-1, 1, len(idx)), 1).astype("float32")  # unrelated to the label
    return df


def wins(df):
    return walkforward.windows(df.index.get_level_values("date").unique(), min_train_years=2, gap=20)


def test_finds_a_real_signal_and_beats_the_baseline():
    df = panel(signal=0.5)
    rows, preds, model, test = ml.walk_forward(df, wins(df))
    assert list(rows.year) == [2021, 2022]
    assert (rows.ic > 0.3).all() and (rows.ic_base.abs() < 0.05).all()
    assert (rows.top > 0.3).all() and (rows.bottom < -0.3).all()
    assert ml.verdict(rows)[0]
    assert ml.importance(model, test).index[0] == "sma200_slope"
    assert len(preds) == sum(df.index.get_level_values("date").year >= 2021)


def test_no_signal_is_a_no_go():
    df = panel(signal=0.0)
    rows, *_ = ml.walk_forward(df, wins(df))
    assert rows.ic.abs().max() < 0.05  # noise only, with 20 stocks a day
    assert not ml.verdict(rows)[0]


def test_deterministic_and_trained_only_on_past_rows():
    df = panel(signal=0.5, years=3)
    w = wins(df)[0]
    a = ml.fit(df[df.index.get_level_values("date") <= w.train_end])
    b = ml.fit(df[df.index.get_level_values("date") <= w.train_end])
    x = df.iloc[:500][FEATURES]
    assert (a.predict(x) == b.predict(x)).all()
    # scrambling the test year's labels can't change the model trained for it
    scrambled = df.copy()
    future = scrambled.index.get_level_values("date") > w.train_end
    scrambled.loc[future, "label"] = 99.0
    _, preds_a, *_ = ml.walk_forward(df, [w])
    _, preds_b, *_ = ml.walk_forward(scrambled, [w])
    pd.testing.assert_series_equal(preds_a, preds_b)


def test_target_is_centred_rank_per_date():
    df = panel(signal=0.0, years=1, n_sym=5)
    t = ml.target(df)
    assert t.groupby(level="date").mean().abs().max() == pytest.approx(0.1)  # ranks 0.2..1.0 minus 0.5
    assert t.groupby(level="date").nunique().eq(5).all()


def test_ranked_inputs_are_per_date_percentiles_except_market():
    df = panel(signal=0.0, years=1, n_sym=5)
    x = ml.inputs(df)
    assert x.drop(columns="market").groupby(level="date").max().eq(1.0).all().all()
    pd.testing.assert_series_equal(x.market, df.market)
    pd.testing.assert_frame_equal(ml.inputs(df, ranked=False), df[FEATURES])
