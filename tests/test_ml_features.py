import numpy as np
import pandas as pd
import pytest

from aistock.ml import features as ml
from aistock.technical.indicators import add_indicators
from conftest import random_walk

RAW = ["Open", "High", "Low", "Close", "Volume"]


def setup(n: int = 500):
    stock = random_walk(n, seed=1, drift=0.001)[RAW]
    index = random_walk(n, seed=2)[RAW]
    sector = random_walk(n, seed=3).Close
    return stock, index, sector


@pytest.mark.parametrize("cut", [260, 333, 420])
def test_features_use_no_future_data(cut):
    """Features computed on history cut at bar `cut` (indicators recomputed from
    scratch) must equal the full-history features up to that bar."""
    stock, index, sector = setup()
    full = ml.features(add_indicators(stock), add_indicators(index), sector)
    part = ml.features(add_indicators(stock.iloc[:cut]), add_indicators(index.iloc[:cut]), sector.iloc[:cut])
    pd.testing.assert_frame_equal(part, full.iloc[:cut], rtol=1e-5)
    assert full.iloc[250:].notna().all().all()  # every feature defined after the warm-up


def test_label_is_next_open_to_horizon_close_minus_nifty():
    stock, index, _ = setup()
    ind, idx = add_indicators(stock), add_indicators(index)
    lab = ml.label(ind, idx, horizon=20)
    t = 300
    expected = (stock.Close.iloc[t + 20] / stock.Open.iloc[t + 1]
                - index.Close.iloc[t + 20] / index.Open.iloc[t + 1])
    assert lab.iloc[t] == pytest.approx(expected, rel=1e-5)
    assert lab.iloc[-20:].isna().all() and lab.iloc[:-20].notna().all()
    # bars after t+horizon don't affect the label at t
    later = stock.copy()
    later.iloc[t + 21:, :4] *= 2
    assert ml.label(add_indicators(later), idx, 20).iloc[t] == pytest.approx(lab.iloc[t])


def test_dataset_panel_and_rank_ic():
    data = {s: add_indicators(random_walk(400, seed=i)[RAW]) for i, s in enumerate("ABCDEF")}
    index = add_indicators(random_walk(400, seed=9)[RAW])
    df = ml.dataset(data, index, {"A": index.Close})
    assert df.index.names == ["date", "symbol"]
    assert list(df.columns) == ml.FEATURES + ["label", "tech_score"]
    assert df.index.get_level_values("date").min() == data["A"].index[data["A"].sma200.notna()][0]
    assert df.loc[(slice(None), "B"), "sector_trend"].isna().all()   # unknown sector
    ic = ml.rank_ic(df.assign(perfect=df.label, noise=np.random.default_rng(0).random(len(df))),
                    ["perfect", "noise"])
    assert (ic.perfect > 0.999).all()
    assert (ic.noise.abs() < 0.3).all()
