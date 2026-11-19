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
    sentiment: float
    close: float
    stop: float
    target: float
    shares: int
    reasons: dict[str, list[str]] = field(default_factory=dict)

    def row(self) -> dict:
        """Flat dict for the journal / tables (no reasons)."""
        d = asdict(self)
        d.pop("reasons")
        return d


def decide(t: float, f: float, s: float, c: Config) -> tuple[float, str, list[str]]:
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


def evaluate(symbol: str, c: Config = Config(), use_news: bool = True) -> Signal:
    symbol = symbol.upper()
    df = get_prices(symbol)
    f = get_fundamentals(symbol)
    t_score, t_why = technical_score(df)
    f_score, f_why = fundamental_score(f)
    if use_news:
        s_score, s_why = sentiment_score(get_news(symbol, f.get("longName")))
    else:
        s_score, s_why = 0.0, ["skipped"]
    combined, action, d_why = decide(t_score, f_score, s_score, c)

    last = add_indicators(df).iloc[-1]
    stop = last.Close - c.stop_atr * last.atr14
    target = last.Close + c.target_atr * last.atr14
    shares = min(int(c.capital * c.risk_per_trade / (last.Close - stop)), int(c.capital / last.Close))
    return Signal(
        symbol, f.get("longName") or symbol, f.get("sector") or "n/a", action,
        round(combined, 3), round(t_score, 3), round(f_score, 3), round(s_score, 3),
        round(float(last.Close), 2), round(float(stop), 2), round(float(target), 2), shares,
        {"technical": t_why, "fundamental": f_why, "sentiment": s_why, "decision": d_why},
    )
