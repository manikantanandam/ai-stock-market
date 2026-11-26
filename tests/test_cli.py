from typer.testing import CliRunner

from aistock import cli
from aistock.strategy.signal import Signal, StrategySignal
from aistock.strategy.strategies import PRESETS


def fake_signal(sym, *a, **kw):
    strategies = {n: StrategySignal(n, "WATCH", False, 90.0, None, 1, []) for n in PRESETS}
    return Signal(sym, sym, "Tech", "WATCH", 0.1, 0.1, 0.1, None, 100.0, 5.0, strategies, {}, as_of="2026-11-26")


def run_scan(monkeypatch, before: str, after: str):
    """scan over one stock, offline; market_phase is `before` until a stock has
    been evaluated (prices downloaded) and `after` from then on."""
    logged, evaluated = [], []
    monkeypatch.setattr(cli, "market_phase", lambda: after if evaluated else before)
    monkeypatch.setattr(cli.prices, "prefetch", lambda *a, **kw: None)
    monkeypatch.setattr(cli.fundamentals, "prefetch", lambda *a, **kw: None)
    monkeypatch.setattr(cli.signal, "market_ok", lambda: True)
    monkeypatch.setattr(cli.signal, "evaluate", lambda *a, **kw: evaluated.append(1) or fake_signal(*a, **kw))
    monkeypatch.setattr(cli.journal, "record", lambda results: logged.append(results) or "journal/")
    out = CliRunner().invoke(cli.app, ["scan", "--symbols", "ABC", "--news", "none"])
    assert out.exit_code == 0, out.output
    return logged, out.output


def test_scan_started_mid_session_is_not_logged_even_if_it_ends_after_the_close(monkeypatch):
    logged, output = run_scan(monkeypatch, "live", "close")
    assert not logged and "not logged" in output


def test_scan_started_after_the_close_is_logged(monkeypatch):
    logged, _ = run_scan(monkeypatch, "close", "close")
    assert len(logged) == 1


def test_compare_stats_has_one_row_per_strategy_and_segment():
    runs = {"trend": {"In-sample (2017-09 to 2023-12)": {"win_rate": 0.4, "avg_trade": 0.02},
                      "Out-of-sample (2024-01 to 2026-11)": {"win_rate": 0.5, "avg_trade": 0.01}}}

    df = cli.compare_stats(runs)

    assert df.to_dict("records") == [
        {"strategy": "trend", "segment": "In-sample", "period": "2017-09 to 2023-12", "win_rate": 0.4, "avg_trade": 0.02},
        {"strategy": "trend", "segment": "Out-of-sample", "period": "2024-01 to 2026-11", "win_rate": 0.5, "avg_trade": 0.01}]
