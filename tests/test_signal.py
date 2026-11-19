import pytest

from aistock.strategy import signal
from aistock.strategy.strategies import PRESETS
from conftest import random_walk

GOOD_FUNDAMENTALS = {"longName": "Test Ltd", "sector": "Tech", "returnOnEquity": 0.25, "debtToEquity": 10,
                     "revenueGrowth": 0.2, "earningsGrowth": 0.2, "profitMargins": 0.2, "trailingPE": 20}


@pytest.fixture
def stub(monkeypatch):
    """Offline evaluate(): synthetic prices, fixed fundamentals, counted news fetches."""
    calls = {"news": 0}

    def setup(prices, sentiment=0.5, fundamentals=GOOD_FUNDAMENTALS):
        monkeypatch.setattr(signal, "get_prices", lambda s: prices[["Open", "High", "Low", "Close", "Volume"]])
        monkeypatch.setattr(signal, "get_fundamentals", lambda s: fundamentals)

        def news(symbol, name):
            calls["news"] += 1
            return []
        monkeypatch.setattr(signal, "get_news", news)
        monkeypatch.setattr(signal, "sentiment_score", lambda items: (sentiment, ["stub"]))
        return calls
    return setup


def uptrend():
    return random_walk(400, seed=3, drift=0.004)  # strong uptrend: swing/trend fire


def downtrend():
    return random_walk(400, seed=3, drift=-0.004)


def test_swing_buys_in_uptrend_with_good_news(stub):
    stub(uptrend(), sentiment=0.5)
    s = signal.evaluate("TEST", strategies=[PRESETS["swing"]], market=True)
    assert s.technical >= 0.4
    assert s.action == "BUY" and s.buys == ["swing"]


def test_negative_news_vetoes(stub):
    stub(uptrend(), sentiment=-0.8)
    s = signal.evaluate("TEST", strategies=[PRESETS["swing"]], market=True)
    assert s.action != "BUY"
    assert any("news veto" in w for w in s.strategies["swing"].why)


def test_market_filter_blocks_regime_strategies(stub):
    stub(uptrend())
    s = signal.evaluate("TEST", strategies=[PRESETS["trend"]], market=False)
    assert s.strategies["trend"].action == "WATCH"
    assert "market filter" in s.strategies["trend"].why[0]


def test_candidates_mode_skips_news_when_nothing_can_fire(stub):
    calls = stub(downtrend())
    s = signal.evaluate("TEST", news="candidates", market=True)
    assert calls["news"] == 0 and s.sentiment is None and s.action != "BUY"


def test_candidates_mode_fetches_news_for_possible_buys(stub):
    calls = stub(uptrend())
    signal.evaluate("TEST", news="candidates", market=True)
    assert calls["news"] == 1


def test_short_history_rejected(stub):
    stub(random_walk(150))
    with pytest.raises(ValueError, match="sessions of history"):
        signal.evaluate("TEST", market=True)


def test_stops_follow_each_preset(stub):
    stub(uptrend())
    s = signal.evaluate("TEST", market=True)
    for name, st in PRESETS.items():
        r = s.strategies[name]
        assert r.stop == pytest.approx(s.close - st.stop_atr * s.atr, abs=0.02)
        assert (r.target is None) == (st.target_atr is None)
