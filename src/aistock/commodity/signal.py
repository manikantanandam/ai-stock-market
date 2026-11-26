"""Evaluate a commodity future against every commodity preset.

Each strategy's verdict on the latest completed session:
- BUY / SELL: its long / short rule fired and it is flat, so enter long /
  short at the next open (unless the news veto blocks it).
- EXIT: the position the strategy held closed today (stop, target or time).
- HOLD: the strategy still holds a long or short from an earlier signal.
- WATCH: nothing to do.
The position comes from replaying the strategy over the price history with
the backtest engine (no news gates), so HOLD and EXIT follow exactly the exits
that `aistock commodity compare` tests. Only the rules and exits are
backtestable; the news veto is an untested prior.
"""
from dataclasses import dataclass, field, replace

import pandas as pd

from aistock.backtest import engine
from aistock.backtest.costs import FuturesCostModel
from aistock.commodity.strategies import PRESETS
from aistock.data import commodities
from aistock.data.news import get_commodity_news
from aistock.sentiment.finbert import sentiment_score
from aistock.strategy.signal import MIN_HISTORY, REASONS_SEP
from aistock.strategy.strategies import Strategy
from aistock.technical import rules
from aistock.technical.indicators import add_indicators, technical_score


@dataclass
class Config:
    w_technical: float = 0.7
    w_sentiment: float = 0.3
    veto_sentiment: float = 0.3  # BUY needs news above -0.3, SELL below +0.3
    capital: float = 100_000.0
    risk_per_trade: float = 0.01

    def combined(self, t: float, s: float | None) -> float:
        """Missing sentiment counts as neutral (0)."""
        return self.w_technical * t + self.w_sentiment * (s or 0.0)

    def params(self, st: Strategy) -> engine.Params:
        return replace(st.params(), capital=self.capital, costs=FuturesCostModel(), fractional=True)


@dataclass
class Position:
    side: str                 # long or short
    open: bool                # False = closed on the latest bar
    entry: float
    entry_date: str
    stop: float
    target: float | None
    exit: float | None = None
    reason: str | None = None  # why it closed (stop, target, time)


@dataclass
class StrategySignal:
    strategy: str
    action: str               # BUY, SELL, EXIT, HOLD or WATCH
    side: str | None          # long/short for every action but WATCH
    stop: float | None
    target: float | None
    qty: float                # MCX units for a new entry, sized by risk
    why: list[str]


@dataclass
class Signal:
    symbol: str
    name: str
    group: str
    unit: str
    action: str               # BUY/SELL if any strategy enters (MIXED if both), else EXIT, HOLD, WATCH
    combined: float
    technical: float
    sentiment: float | None
    close: float              # INR per unit
    atr: float
    close_usd: float          # the global contract's own quote
    usdinr: float
    strategies: dict[str, StrategySignal] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    as_of: str = ""

    def named(self, action: str) -> list[str]:
        return [n for n, s in self.strategies.items() if s.action == action]

    @property
    def entries(self) -> list[str]:
        """Strategies opening a position (BUY or SELL), the ones notes explain."""
        return self.named("BUY") + self.named("SELL")

    def row(self) -> dict:
        tagged = lambda a: ";".join(f"{n}:{s.side}" for n, s in self.strategies.items() if s.action == a)
        return {"symbol": self.symbol, "name": self.name, "group": self.group, "unit": self.unit,
                "action": self.action, "buys": ";".join(self.named("BUY")), "sells": ";".join(self.named("SELL")),
                "exits": tagged("EXIT"), "holds": tagged("HOLD"), "combined": self.combined,
                "technical": self.technical, "sentiment": self.sentiment, "close": self.close, "atr": self.atr,
                "close_usd": self.close_usd, "usdinr": self.usdinr,
                **{f"{part}_reasons": REASONS_SEP.join(self.reasons.get(part, []))
                   for part in ("technical", "sentiment")}}


def position(st: Strategy, ind: pd.DataFrame, c: Config) -> Position | None:
    """The strategy's position on the latest bar per the backtest: open, closed
    on that bar, or None when flat."""
    long = rules.build(st.rule)(ind)
    short = rules.build(st.short_rule)(ind) if st.short_rule else None
    t = engine.run(ind, long, c.params(st), short).trades
    if t.empty:
        return None
    last = t.iloc[-1]
    is_open = last.reason == "end"
    if not is_open and last.exit_date != ind.index[-1]:
        return None
    target = None if pd.isna(last.target) else round(float(last.target), 2)
    return Position(last.side, is_open, round(float(last.entry), 2), f"{last.entry_date:%Y-%m-%d}",
                    round(float(last.stop), 2), target,
                    None if is_open else round(float(last.exit), 2), None if is_open else last.reason)


def levels(st: Strategy, side: str, close: float, atr: float, c: Config) -> tuple[float, float | None, float]:
    """Stop, target and quantity (MCX units, 2 decimals) for a new entry at `close`."""
    sign = 1 if side == "long" else -1
    stop = close - sign * st.stop_atr * atr
    target = close + sign * st.target_atr * atr if st.target_atr else None
    qty = min(c.capital * c.risk_per_trade / abs(close - stop), c.capital / close)
    return round(stop, 2), None if target is None else round(target, 2), round(qty, 2)


def decide(st: Strategy, long: bool, short: bool, pos: Position | None, sentiment: float | None,
           close: float, atr: float, c: Config) -> StrategySignal:
    """One strategy's verdict from its rules on the latest bar and its replayed position."""
    flat = pos is None or not pos.open
    side = "long" if long else "short" if short else None
    why = []
    if flat and side:
        if sentiment is not None and (sentiment <= -c.veto_sentiment if side == "long" else sentiment >= c.veto_sentiment):
            why.append(f"news veto on the {side} entry: sentiment {sentiment:+.2f}")
        else:
            stop, target, qty = levels(st, side, close, atr, c)
            rule = st.rule if side == "long" else st.short_rule
            closed = [f"previous {pos.side} closed today by {pos.reason} at {pos.exit:.2f}"] if pos else []
            return StrategySignal(st.name, "BUY" if side == "long" else "SELL", side, stop, target, qty,
                                  [f"{rule} fired; enter {side} at the next open"] + closed)
    if pos and not pos.open:
        return StrategySignal(st.name, "EXIT", pos.side, pos.stop, pos.target, 0.0,
                              [f"{pos.side} from {pos.entry_date} at {pos.entry:.2f} closed by {pos.reason} "
                               f"at {pos.exit:.2f}"] + why)
    if pos:
        return StrategySignal(st.name, "HOLD", pos.side, pos.stop, pos.target, 0.0,
                              [f"holding {pos.side} from {pos.entry_date} at {pos.entry:.2f}"])
    rules_off = f"{st.rule}" + (f" / {st.short_rule}" if st.short_rule else "") + " not triggered"
    return StrategySignal(st.name, "WATCH", None, None, None, 0.0, why or [rules_off])


def overall(results: dict[str, StrategySignal]) -> str:
    actions = {r.action for r in results.values()}
    if {"BUY", "SELL"} <= actions:
        return "MIXED"
    return next(a for a in ("BUY", "SELL", "EXIT", "HOLD", "WATCH") if a in actions or a == "WATCH")


def evaluate(symbol: str, c: Config = Config(), news: str = "all",
             strategies: list[Strategy] | None = None) -> Signal:
    """news: 'all' fetches headlines for every contract, 'candidates' only where
    a strategy's rule fired while flat, 'none' skips news."""
    strategies = strategies or list(PRESETS.values())
    contract = commodities.get(symbol)
    df = commodities.get_prices(contract.symbol)
    if len(df) < MIN_HISTORY:
        raise ValueError(f"only {len(df)} sessions of history, need {MIN_HISTORY}")
    ind = add_indicators(df)
    last = ind.iloc[-1]
    t_score, t_why = technical_score(df)
    fired = {st.name: (bool(rules.build(st.rule)(ind).iloc[-1]),
                       bool(st.short_rule and rules.build(st.short_rule)(ind).iloc[-1])) for st in strategies}
    positions = {st.name: position(st, ind, c) for st in strategies}
    candidate = any(any(fired[n]) and (p is None or not p.open) for n, p in positions.items())
    if news == "all" or (news == "candidates" and candidate):
        s_score, s_why = sentiment_score(get_commodity_news(contract.symbol, contract.news))
    else:
        s_score, s_why = None, ["not fetched" + (" (no strategy can enter)" if news == "candidates" else "")]

    close, atr = float(last.Close), float(last.atr14)
    results = {st.name: decide(st, *fired[st.name], positions[st.name], s_score, close, atr, c) for st in strategies}
    fx = commodities.usd_inr().asof(last.name)
    return Signal(
        contract.symbol, contract.name, contract.group, contract.unit, overall(results),
        round(c.combined(t_score, s_score), 3), round(t_score, 3), None if s_score is None else round(s_score, 3),
        round(close, 2), round(atr, 2), round(close / fx / contract.factor, 2), round(float(fx), 2), results,
        {"technical": t_why, "sentiment": s_why}, as_of=f"{last.name:%Y-%m-%d}",
    )
