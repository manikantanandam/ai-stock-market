"""Merge technical, fundamental and sentiment scores into one trade signal.

Technicals decide timing, fundamentals act as a quality filter, and news
sentiment can veto. Only the technical rule is backtestable today: there is
no free history of fundamentals snapshots or headlines, so the weights and
gates here are untested priors until the signal journal builds that history.
"""
from dataclasses import asdict, dataclass, field

from aistock.data.fundamentals import get_fundamentals
from aistock.data.news import get_news
from aistock.data.prices import get_prices
from aistock.fundamental.score import fundamental_score
from aistock.sentiment.finbert import sentiment_score
from aistock.technical.indicators import add_indicators, technical_score


@dataclass
class Config:
    w_technical: float = 0.5
    w_fundamental: float = 0.3
    w_sentiment: float = 0.2
    buy_combined: float = 0.4     # BUY needs combined >= this ...
    buy_technical: float = 0.4    # ... and an uptrend-level technical score ...
    veto_sentiment: float = -0.3  # ... and news not clearly negative
    avoid_combined: float = -0.2
    stop_atr: float = 2.0
    target_atr: float = 3.0
    capital: float = 100_000.0
    risk_per_trade: float = 0.01  # lose at most 1% of capital if the stop hits


@dataclass
class Signal:
    symbol: str
    name: str
    sector: str
    action: str
    combined: float
    technical: float
    fundamental: float
    sentiment: float | None  # None when news was not fetched
    close: float
    stop: float
    target: float
    shares: int
    reasons: dict[str, list[str]] = field(default_factory=dict)
    as_of: str = ""  # date of the price bar the signal is based on

    def row(self) -> dict:
        """Flat dict for the journal / tables (no reasons or as_of)."""
        d = asdict(self)
        d.pop("reasons")
        d.pop("as_of")
        return d


def could_buy(t: float, f: float, c: Config) -> bool:
    """Whether the best possible sentiment (+1) could still make this a BUY."""
    return t >= c.buy_technical and c.w_technical * t + c.w_fundamental * f + c.w_sentiment >= c.buy_combined


def decide(t: float, f: float, s: float | None, c: Config) -> tuple[float, str, list[str]]:
    """Missing sentiment counts as neutral (0) and cannot veto."""
    s = 0.0 if s is None else s
    combined = c.w_technical * t + c.w_fundamental * f + c.w_sentiment * s
    if combined >= c.buy_combined and t >= c.buy_technical and s > c.veto_sentiment:
        return combined, "BUY", ["all gates passed"]
    if combined <= c.avoid_combined:
        return combined, "AVOID", [f"combined {combined:+.2f} <= {c.avoid_combined}"]
    why = []
    if combined < c.buy_combined:
        why.append(f"combined {combined:+.2f} < {c.buy_combined}")
    if t < c.buy_technical:
        why.append(f"technical {t:+.2f} < {c.buy_technical} (no confirmed uptrend)")
    if s <= c.veto_sentiment:
        why.append(f"news veto: sentiment {s:+.2f}")
    return combined, "WATCH", why


NEWS_MODES = ("all", "candidates", "none")
MIN_HISTORY = 201


def evaluate(symbol: str, c: Config = Config(), news: str = "all") -> Signal:
    """news: 'all' fetches headlines for every stock, 'candidates' only where
    sentiment could still change the outcome to BUY (keeps large scans fast and
    under Google News rate limits), 'none' skips news."""
    symbol = symbol.upper()
    df = get_prices(symbol)
    if len(df) < MIN_HISTORY:  # SMA200 needs 200 sessions; recent listings can't be scored
        raise ValueError(f"only {len(df)} sessions of history, need {MIN_HISTORY}")
    f = get_fundamentals(symbol)
    t_score, t_why = technical_score(df)
    f_score, f_why = fundamental_score(f)
    if news == "all" or (news == "candidates" and could_buy(t_score, f_score, c)):
        s_score, s_why = sentiment_score(get_news(symbol, f.get("longName")))
    else:
        s_score, s_why = None, ["not fetched" + (" (cannot reach BUY)" if news == "candidates" else "")]
    combined, action, d_why = decide(t_score, f_score, s_score, c)

    last = add_indicators(df).iloc[-1]
    stop = last.Close - c.stop_atr * last.atr14
    target = last.Close + c.target_atr * last.atr14
    shares = min(int(c.capital * c.risk_per_trade / (last.Close - stop)), int(c.capital / last.Close))
    return Signal(
        symbol, f.get("longName") or symbol, f.get("sector") or "n/a", action,
        round(combined, 3), round(t_score, 3), round(f_score, 3), None if s_score is None else round(s_score, 3),
        round(float(last.Close), 2), round(float(stop), 2), round(float(target), 2), shares,
        {"technical": t_why, "fundamental": f_why, "sentiment": s_why, "decision": d_why},
        as_of=f"{last.name:%Y-%m-%d}",
    )
