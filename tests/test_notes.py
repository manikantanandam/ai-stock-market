import urllib.error

import pytest

from aistock.llm import notes
from aistock.strategy.signal import Signal, StrategySignal


def sig() -> Signal:
    s = Signal("ABC", "ABC Ltd", "Industrials", "BUY", 0.62, 0.8, 0.5, 0.3, 1234.5, 42.25,
               reasons={"technical": ["uptrend: price > SMA50 > SMA200"], "fundamental": ["ROE 18%"],
                        "sentiment": ["+0.71 2026-11-20 ABC wins order (Mint)"]},
               as_of="2026-11-24")
    s.strategies["trend"] = StrategySignal("trend", "BUY", True, 1107.75, None, 23, ["all gates passed"])
    return s


def fake(response: str, calls: list):
    def post(payload):
        calls.append(payload)
        return {"response": response}
    return post


def test_facts_carry_the_numbers_and_reasons():
    f = notes.facts(sig(), "trend")
    for text in ["1234.50", "1107.75", "quantity 23", "target none", "BUY by strategy 'trend'",
                 "ROE 18%", "ABC wins order"]:
        assert text in f


def test_note_is_deterministic_and_passes_when_figures_match(monkeypatch):
    calls = []
    monkeypatch.setattr(notes, "_post", fake("ABC is in an uptrend  at 1,234.50 with a stop at 1107.75.\n", calls))
    assert notes.note(sig(), "trend") == "ABC is in an uptrend at 1,234.50 with a stop at 1107.75."
    opts = calls[0]["options"]
    assert opts["temperature"] == 0 and opts["seed"] == 0 and calls[0]["model"] == notes.MODEL
    assert calls[0]["prompt"] == notes.facts(sig(), "trend")


def test_note_with_invented_figures_is_withheld(monkeypatch):
    monkeypatch.setattr(notes, "_post", fake("Upside of 15% to 1420 looks likely.", []))
    out = notes.note(sig(), "trend")
    assert out.startswith("(note withheld") and "15" in out and "1420" in out


def test_unreachable_ollama_raises_unavailable(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("connection refused")
    monkeypatch.setattr(notes.urllib.request, "urlopen", refuse)
    with pytest.raises(notes.Unavailable, match="not reachable"):
        notes.note(sig(), "trend")
