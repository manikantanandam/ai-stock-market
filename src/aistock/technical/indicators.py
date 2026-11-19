import numpy as np
import pandas as pd
import pandas_ta as ta


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["sma20"] = ta.sma(out.Close, 20)
    out["sma50"] = ta.sma(out.Close, 50)
    out["sma200"] = ta.sma(out.Close, 200)
    out["rsi14"] = ta.rsi(out.Close, 14)
    macd = ta.macd(out.Close)
    out["macd_hist"] = macd.iloc[:, 1]
    out["atr14"] = ta.atr(out.High, out.Low, out.Close, 14)
    out["vol_avg20"] = out.Volume.rolling(20).mean()
    return out


def score_series(ind: pd.DataFrame) -> pd.Series:
    """Technical score in [-1, 1] for every bar of an add_indicators() frame."""
    up = (ind.Close > ind.sma50) & (ind.sma50 > ind.sma200)
    down = (ind.Close < ind.sma50) & (ind.sma50 < ind.sma200)
    score = (
        np.where(up, 0.4, np.where(down, -0.4, 0.0))
        + np.where(ind.rsi14 < 30, 0.2, np.where(ind.rsi14 > 70, -0.2, 0.0))
        + np.where(ind.macd_hist > 0, 0.2, -0.2)
    )
    return pd.Series(score, index=ind.index).clip(-1, 1).where(ind.sma200.notna())


def technical_score(df: pd.DataFrame) -> tuple[float, list[str]]:
    """Score in [-1, 1] from the latest bar, with human-readable reasons."""
    ind = add_indicators(df)
    last = ind.iloc[-1]
    why = []
    if last.Close > last.sma50 > last.sma200:
        why.append("uptrend: price > SMA50 > SMA200")
    elif last.Close < last.sma50 < last.sma200:
        why.append("downtrend: price < SMA50 < SMA200")
    if last.rsi14 < 30:
        why.append(f"RSI oversold ({last.rsi14:.0f})")
    elif last.rsi14 > 70:
        why.append(f"RSI overbought ({last.rsi14:.0f})")
    why.append("MACD histogram " + ("positive" if last.macd_hist > 0 else "negative"))
    if last.Volume > 1.5 * last.vol_avg20:
        why.append("volume spike vs 20d average")
    return float(score_series(ind).iloc[-1]), why
