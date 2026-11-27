"""Notes for sessions journaled before notes were saved (or when Ollama was down).

Each BUY's Signal is rebuilt from its journal row: close, ATR, scores and the
strategies that said BUY come from the row, stops and targets from the preset,
as on the day. Reasons come from the row when it has them (rows from before
they were journaled don't); otherwise technical reasons are recomputed from
prices cut at that session and kept only if they reproduce the journaled
technical score. News reasons are rebuilt from Google News limited to the 14
days before the session (the journaled sentiment score is kept; the re-fetched
headlines may differ a little from what the scan saw). Past fundamentals can't
be fetched, so those reasons are stated as unavailable rather than taken from
today's data.
"""
import math
from datetime import date

import pandas as pd

from aistock.data.news import get_news_as_of, session_cutoff
from aistock.data.prices import get_prices
from aistock.sentiment.finbert import sentiment_score
from aistock.strategy import journal
from aistock.strategy.signal import REASONS_SEP, Config, Signal, StrategySignal, levels
from aistock.strategy.strategies import PRESETS
from aistock.technical.indicators import technical_score

UNSAVED = "not saved for this session (score only)"
NEWS_DAYS = 14


def sessions_without_notes() -> list[str]:
    """Journaled sessions that have no notes file, newest first."""
    have = {p.stem for p in journal.NOTES.glob("*.csv")}
    return sorted((p.stem for p in journal.DAYS.glob("*.csv") if p.stem not in have), reverse=True)


def _num(v) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) else x


def technical_reasons(prices: pd.DataFrame, day: str, expected: float | None) -> list[str]:
    """Reasons for the technical score as of `day`, if they reproduce `expected`."""
    df = prices[prices.index <= pd.Timestamp(day)]
    if df.empty or f"{df.index[-1]:%Y-%m-%d}" != day:
        return ["could not be rebuilt (no price bar for this session)"]
    score, why = technical_score(df)
    if expected is None or abs(round(score, 3) - expected) > 1e-6:
        return ["could not be rebuilt (prices have changed since this session)"]
    return why


def news_reasons(symbol: str, company: str | None, day: str) -> list[str]:
    """Sentiment reasons from headlines re-fetched for the NEWS_DAYS before `day`."""
    d = date.fromisoformat(day)
    _, why = sentiment_score(get_news_as_of(symbol, company, d, NEWS_DAYS), now=session_cutoff(d))
    return [f"headlines re-fetched later for the {NEWS_DAYS} days to {day}; the score is the one journaled that day"] + why


def signal_from_row(row: dict, prices: pd.DataFrame | None = None, c: Config = Config(),
                    sentiment_why: list[str] | None = None) -> Signal | None:
    """Rebuild a BUY's Signal from its journal row; None if no known preset said BUY.
    sentiment_why: rebuilt news reasons, used when the row has none saved."""
    buys = [b for b in str(row.get("buys") or "").split(";") if b in PRESETS]
    close, atr = _num(row.get("close")), _num(row.get("atr"))
    if not buys or close is None or atr is None:
        return None
    strategies = {}
    for name in buys:
        stop, target, shares = levels(PRESETS[name], close, atr, c)
        strategies[name] = StrategySignal(name, "BUY", True, stop, target, shares, ["all gates passed"])
    saved = {part: str(row.get(f"{part}_reasons") or "") for part in ("technical", "fundamental", "sentiment")}
    reasons = {part: text.split(REASONS_SEP) for part, text in saved.items() if text and text != "nan"}
    if "technical" not in reasons:
        reasons["technical"] = (technical_reasons(prices, row["date"], _num(row.get("technical")))
                                if prices is not None else [UNSAVED])
    if "sentiment" not in reasons:
        if _num(row.get("sentiment")) is None:
            reasons["sentiment"] = ["news not fetched for this session"]
        else:
            reasons["sentiment"] = sentiment_why or [UNSAVED]
    reasons.setdefault("fundamental", [UNSAVED])
    return Signal(row["symbol"], row.get("name") or row["symbol"], row.get("sector") or "n/a", "BUY",
                  _num(row.get("combined")) or 0.0, _num(row.get("technical")) or 0.0,
                  _num(row.get("fundamental")) or 0.0, _num(row.get("sentiment")),
                  close, atr, strategies, reasons, as_of=row["date"])


def session_signals(day: str, limit: int, symbols: list[str] | None = None) -> list[Signal]:
    """The session's top BUYs, ranked as the scan ranks them (strategies saying BUY, then combined); only
    `symbols` when given."""
    rows = pd.read_csv(journal.DAYS / f"{day}.csv", dtype={"buys": str}).to_dict("records")
    rows = [r for r in rows if r.get("action") == "BUY" and (symbols is None or r["symbol"] in symbols)]
    rows.sort(key=lambda r: (len(str(r.get("buys") or "").split(";")), _num(r.get("combined")) or 0), reverse=True)
    out = []
    for r in rows[:limit]:
        try:
            prices = get_prices(r["symbol"])
        except Exception:  # a delisted or renamed ticker still gets a note without technical reasons
            prices = None
        saved = str(r.get("sentiment_reasons") or "")
        needs_news = (not saved or saved == "nan") and _num(r.get("sentiment")) is not None
        s = signal_from_row(r, prices, sentiment_why=news_reasons(r["symbol"], r.get("name"), r["date"])
                            if needs_news else None)
        if s:
            out.append(s)
    return out
