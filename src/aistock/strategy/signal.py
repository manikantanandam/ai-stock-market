"""Evaluate a stock against every strategy preset.

Scores (technical, fundamental, news sentiment) are computed once per stock.
Each strategy then decides BUY or not: its entry rule must fire on the latest
bar (plus the market filter if it uses one), it must pass its live gates
(combined / fundamental score), and news must not be clearly negative. Only
the rule + market filter + exits are backtestable; the weights and gates are
untested priors until the signal journal builds history.
"""
from dataclasses import dataclass, field

from aistock.data import sectors
from aistock.data.fundamentals import RATIOS, get_fundamentals
from aistock.data.news import get_news
from aistock.data.prices import get_prices
from aistock.fundamental.score import fundamental_score
from aistock.sentiment.finbert import sentiment_score
from aistock.strategy.strategies import PRESETS, Strategy
from aistock.technical import rules
from aistock.technical.indicators import add_indicators, technical_score

NEWS_MODES = ("all", "candidates", "none")
REASONS_SEP = " | "  # joins a part's reasons in one journal cell
MIN_HISTORY = 201
INDEX = "^NSEI"


@dataclass
class Config:
    w_technical: float = 0.5
    w_fundamental: float = 0.3
    w_sentiment: float = 0.2
    veto_sentiment: float = -0.3  # BUY needs news not clearly negative
    avoid_combined: float = -0.2
    capital: float = 100_000.0
    risk_per_trade: float = 0.01  # lose at most 1% of capital if the stop hits

    def combined(self, t: float, f: float, s: float | None) -> float:
        """Missing sentiment counts as neutral (0)."""
        return self.w_technical * t + self.w_fundamental * f + self.w_sentiment * (s or 0.0)


@dataclass
class StrategySignal:
    strategy: str
    action: str           # BUY or WATCH
    fired: bool           # entry rule (and market, RS, sector filters) true on the latest bar
    stop: float
    target: float | None
    shares: int
    why: list[str]


@dataclass
class Signal:
    symbol: str
    name: str
    sector: str
    action: str           # BUY if any strategy says BUY, else AVOID / WATCH
    combined: float
    technical: float
    fundamental: float
    sentiment: float | None  # None when news was not fetched
    close: float
    atr: float
    strategies: dict[str, StrategySignal] = field(default_factory=dict)
    reasons: dict[str, list[str]] = field(default_factory=dict)
    as_of: str = ""       # date of the price bar the signal is based on
    rs_3m: float | None = None    # return minus NIFTY 50's over ~3 months
    rs_6m: float | None = None    # same over ~6 months
    sector_up: bool | None = None  # sector index above its SMA50; None when not checked or unknown
    fundamentals: dict = field(default_factory=dict)  # raw ratios behind the fundamental score

    @property
    def buys(self) -> list[str]:
        return [n for n, s in self.strategies.items() if s.action == "BUY"]

    def row(self) -> dict:
        """Flat dict for the journal. Stops/targets are recomputable from close, atr and the preset.
        Reasons and raw ratios are kept so notes can be rewritten and fundamentals
        backtested later (Yahoo only serves today's snapshot)."""
        return {"symbol": self.symbol, "name": self.name, "sector": self.sector, "action": self.action,
                "buys": ";".join(self.buys), "combined": self.combined, "technical": self.technical,
                "fundamental": self.fundamental, "sentiment": self.sentiment, "close": self.close,
                "atr": self.atr, "rs_3m": self.rs_3m, "rs_6m": self.rs_6m, "sector_up": self.sector_up,
                **{f"{part}_reasons": REASONS_SEP.join(self.reasons.get(part, []))
                   for part in ("technical", "fundamental", "sentiment")},
                **{f"f_{k}": self.fundamentals.get(k) for k in RATIOS}}


def market_ok() -> bool:
    """Market filter for today: NIFTY 50 above its 200-day SMA."""
    return bool(rules.market_regime(add_indicators(get_prices(INDEX))).iloc[-1])


def sector_up(symbol: str) -> bool | None:
    """Today's sector filter: the stock's sector index above its SMA50 (None if not in NIFTY 500)."""
    industry = sectors.INDUSTRY.get(symbol)
    return None if industry is None else bool(rules.sector_trend(sectors.index(industry)).iloc[-1])


def _filters_off(st: Strategy, market: bool, rs_ok: bool, sector_ok: bool | None) -> list[str]:
    """Entry filters of a strategy that block it today (empty = all pass)."""
    off = []
    if st.regime and not market:
        off.append("market filter off (NIFTY below SMA200)")
    if st.rs and not rs_ok:
        off.append("RS filter off (lagging NIFTY over 3 or 6 months)")
    if st.sector and not sector_ok:
        off.append("sector filter off (" + ("sector unknown" if sector_ok is None else "sector below SMA50") + ")")
    return off


def _passes_gates(st: Strategy, f: float, combined: float) -> list[str]:
    """Reasons a fired strategy is blocked by its live gates (empty = passes)."""
    blocked = []
    if st.min_combined is not None and combined < st.min_combined:
        blocked.append(f"combined {combined:+.2f} < {st.min_combined}")
    if st.min_fundamental is not None and f < st.min_fundamental:
        blocked.append(f"fundamental {f:+.2f} < {st.min_fundamental}")
    return blocked


def levels(st: Strategy, close: float, atr: float, c: Config) -> tuple[float, float | None, int]:
    """Stop, target and share count for a strategy entering at `close`."""
    stop = close - st.stop_atr * atr
    target = close + st.target_atr * atr if st.target_atr else None
    shares = min(int(c.capital * c.risk_per_trade / (close - stop)), int(c.capital / close))
    return round(stop, 2), None if target is None else round(target, 2), shares


def evaluate(symbol: str, c: Config = Config(), news: str = "all",
             strategies: list[Strategy] | None = None, market: bool | None = None) -> Signal:
    """news: 'all' fetches headlines for every stock, 'candidates' only where a
    strategy fired and could still pass its gates (keeps large scans fast and
    under Google News rate limits), 'none' skips news.
    market: today's market filter; computed if not given (pass it in for scans)."""
    strategies = strategies or list(PRESETS.values())
    market = market_ok() if market is None and any(s.regime for s in strategies) else market
    symbol = symbol.upper()
    df = get_prices(symbol)
    if len(df) < MIN_HISTORY:  # SMA200 needs 200 sessions; recent listings can't be scored
        raise ValueError(f"only {len(df)} sessions of history, need {MIN_HISTORY}")
    ind = add_indicators(df)
    last = ind.iloc[-1]
    f = get_fundamentals(symbol)
    t_score, t_why = technical_score(df)
    f_score, f_why = fundamental_score(f)

    index_close = get_prices(INDEX).Close
    idx = index_close.reindex(ind.index).ffill()
    rs = {n: float(ind.Close.iloc[-1] / ind.Close.iloc[-1 - n] - idx.iloc[-1] / idx.iloc[-1 - n]) for n in rules.RS_PERIODS}
    rs_ok = bool(rules.outperforms(ind.Close, index_close).iloc[-1])
    sector_ok = sector_up(symbol) if any(st.sector for st in strategies) else None
    off = {st.name: _filters_off(st, market, rs_ok, sector_ok) for st in strategies}
    fired = {st.name: bool(rules.build(st.rule)(ind).iloc[-1]) and not off[st.name] for st in strategies}
    best_case = c.combined(t_score, f_score, 1.0)
    candidate = any(fired[st.name] and not _passes_gates(st, f_score, best_case) for st in strategies)
    if news == "all" or (news == "candidates" and candidate):
        s_score, s_why = sentiment_score(get_news(symbol, f.get("longName")))
    else:
        s_score, s_why = None, ["not fetched" + (" (no strategy can reach BUY)" if news == "candidates" else "")]
    combined = c.combined(t_score, f_score, s_score)

    close, atr = float(last.Close), float(last.atr14)
    results = {}
    for st in strategies:
        if not fired[st.name]:
            why = off[st.name] or [f"{st.rule} not triggered"]
        else:
            why = _passes_gates(st, f_score, combined)
            if s_score is not None and s_score <= c.veto_sentiment:
                why.append(f"news veto: sentiment {s_score:+.2f}")
        stop, target, shares = levels(st, close, atr, c)
        action = "BUY" if fired[st.name] and not why else "WATCH"
        results[st.name] = StrategySignal(st.name, action, fired[st.name], stop, target, shares,
                                          why or ["all gates passed"])

    buys = [n for n, r in results.items() if r.action == "BUY"]
    action = "BUY" if buys else ("AVOID" if combined <= c.avoid_combined else "WATCH")
    return Signal(
        symbol, f.get("longName") or symbol, f.get("sector") or "n/a", action,
        round(combined, 3), round(t_score, 3), round(f_score, 3), None if s_score is None else round(s_score, 3),
        round(close, 2), round(atr, 2), results,
        {"technical": t_why, "fundamental": f_why, "sentiment": s_why},
        as_of=f"{last.name:%Y-%m-%d}",
        rs_3m=round(rs[rules.RS_PERIODS[0]], 4), rs_6m=round(rs[rules.RS_PERIODS[1]], 4), sector_up=sector_ok,
        fundamentals={k: f.get(k) for k in RATIOS},
    )
