"""Plain-English trade notes from a local LLM (Ollama, qwen2.5:3b).

The LLM only explains a verdict the rules already reached: it is given the
signal's numbers and reasons, chooses nothing, and changes nothing. A 3B model
can invent figures, so a note that quotes any number not in the facts it was
given is withheld. Notes are optional: `scan --notes` saves them to
journal/notes/ (`commodity scan --notes` to journal/commodities/notes/), and
the daily CI scan runs Ollama on CPU to write them.
"""
import json
import re
import urllib.error
import urllib.request

from aistock.commodity import signal as commodity_signal
from aistock.commodity.strategies import PRESETS as COMMODITY_PRESETS
from aistock.strategy.signal import Signal
from aistock.strategy.strategies import PRESETS

URL = "http://localhost:11434/api/generate"
MODEL = "qwen2.5:3b"
TIMEOUT = 120
SYSTEM = ("You write short notes for a swing trader about a decision that rule-based code already made. "
          "Use only the facts given. Do not add any number that is not in the facts, do not compute "
          "percentages, and do not give advice or say whether to buy. Write 3 or 4 plain sentences: "
          "why the strategy fired, what supports it, the main risk, and where the stop is.")
COMMODITY_SYSTEM = ("You write short notes for a futures trader about a decision that rule-based code already made. "
                    "Use only the facts given. Do not add any number that is not in the facts, do not compute "
                    "percentages, and do not give advice or say whether to trade. Write 3 or 4 plain sentences: "
                    "why the strategy fired and in which direction, what supports it, the main risk, and where "
                    "the stop is.")
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


class Unavailable(Exception):
    """Ollama isn't running or the model isn't pulled."""


def facts(s: Signal, strategy: str) -> str:
    """Everything the note may say, as plain lines."""
    r = s.strategies[strategy]
    news = "not fetched" if s.sentiment is None else f"{s.sentiment:+.2f}"
    target = "none (exit by stop or time)" if r.target is None else f"{r.target:.2f}"
    lines = [
        f"Stock: {s.symbol} ({s.name}), sector {s.sector}, data as of {s.as_of}",
        f"Rules verdict: {r.action} by strategy '{strategy}': {PRESETS[strategy].describe()}",
        f"Close {s.close:.2f}, ATR {s.atr:.2f}, stop {r.stop:.2f}, target {target}, quantity {r.shares}",
        f"Scores from -1 to +1: technical {s.technical:+.2f}, fundamental {s.fundamental:+.2f}, "
        f"news {news}, combined {s.combined:+.2f}",
    ]
    for part in ("technical", "fundamental", "sentiment"):
        lines += [f"{part} reason: {w}" for w in s.reasons.get(part, [])]
    return "\n".join(lines)


def commodity_facts(s: commodity_signal.Signal, strategy: str) -> str:
    """Everything a commodity note may say, as plain lines."""
    r = s.strategies[strategy]
    news = "not fetched" if s.sentiment is None else f"{s.sentiment:+.2f}"
    target = "none (exit by stop or time)" if r.target is None else f"{r.target:.2f}"
    side = {"long": "long (BUY: profits if the price rises)", "short": "short (SELL: profits if the price falls)"}
    lines = [
        f"Commodity future: {s.symbol} ({s.name}), {s.group}, data as of {s.as_of}",
        f"Prices in INR per {s.unit}, converted from the global contract (USD {s.close_usd:.2f}) at USDINR {s.usdinr:.2f}",
        f"Rules verdict: {r.action}, {side[r.side]}, by strategy '{strategy}': {COMMODITY_PRESETS[strategy].describe()}",
        f"Close {s.close:.2f}, ATR {s.atr:.2f}, stop {r.stop:.2f}, target {target}, quantity {r.qty:g} units",
        "The stop is " + ("below the close: a fall to it" if r.side == "long" else "above the close: a rise to it")
        + " closes the trade at a loss",
        f"Scores from -1 to +1: technical {s.technical:+.2f}, news {news}, combined {s.combined:+.2f}",
    ]
    lines += [f"{part} reason: {w}" for part in ("technical", "sentiment") for w in s.reasons.get(part, [])]
    return "\n".join(lines)


def _canon(n: str) -> str:
    return f"{float(n.replace(',', '')):g}"


def invented_numbers(note: str, known: str) -> list[str]:
    """Numbers in the note that don't appear in the facts (1,234.50 matches 1234.5)."""
    allowed = {_canon(n) for n in NUMBER.findall(known)}
    return [n for n in NUMBER.findall(note) if _canon(n) not in allowed]


def _post(payload: dict) -> dict:
    req = urllib.request.Request(URL, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise Unavailable(f"Ollama error {e.code}: {e.read().decode()[:200]}") from e
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        raise Unavailable(f"Ollama not reachable at {URL} ({e})") from e


def note(s: Signal, strategy: str, model: str = MODEL) -> str:
    """A short note on one strategy's verdict, or a withheld-note message if the
    model quoted figures that aren't in the facts. Raises Unavailable."""
    return write(facts(s, strategy), SYSTEM, model)


def commodity_note(s: commodity_signal.Signal, strategy: str, model: str = MODEL) -> str:
    """note() for a commodity BUY or SELL."""
    return write(commodity_facts(s, strategy), COMMODITY_SYSTEM, model)


def write(known: str, system: str, model: str = MODEL) -> str:
    out = _post({"model": model, "system": system, "prompt": known, "stream": False,
                 "options": {"temperature": 0, "seed": 0, "num_predict": 220}})
    text = " ".join(out.get("response", "").split())
    bad = invented_numbers(text, known)
    if bad:
        return f"(note withheld: the model quoted figures not in the data: {', '.join(bad[:5])})"
    return text
