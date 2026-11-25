"""Equal-weight sector indices built from NIFTY 500 constituents by industry.

Yahoo has no history for several NSE sector indices (Capital Goods, Chemicals,
Healthcare, ...), so each sector is the mean daily return of its NIFTY 500
members, which covers every industry the same way. Same survivorship caveat as
the watchlists: members are today's constituents.
"""
from functools import cache

import pandas as pd

from aistock.data.prices import get_prices, prefetch
from aistock.data.watchlists import industries

INDUSTRY = industries("nifty500")


def equal_weight(closes: dict[str, pd.Series]) -> pd.Series:
    """Index level (starting at 1) from the mean daily return of the members trading each day."""
    rets = pd.DataFrame(closes).sort_index().pct_change(fill_method=None)
    return (1 + rets.mean(axis=1).fillna(0.0)).cumprod()


@cache
def index(industry: str, years: int = 2) -> pd.Series:
    """Sector index for an industry; members without price data are left out."""
    members = [s for s, i in INDUSTRY.items() if i == industry]
    prefetch(members, years)
    closes = {}
    for sym in members:
        try:
            closes[sym] = get_prices(sym, years).Close
        except ValueError:
            continue
    if not closes:
        raise ValueError(f"no price data for any {industry} stock")
    return equal_weight(closes)
