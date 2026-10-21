"""Τεχνικοί δείκτες, υλοποιημένοι με pandas (χωρίς εξωτερικές βιβλιοθήκες TA)."""
import numpy as np
import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=df.index)
    a = atr(df, n)
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / a
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return pd.DataFrame(
        {"adx": dx.ewm(alpha=1 / n, adjust=False).mean(), "plus_di": plus_di, "minus_di": minus_di}
    )


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    c = df["close"]
    df["ema20"] = ema(c, 20)
    df["ema50"] = ema(c, 50)
    df["ema200"] = ema(c, 200)
    df["rsi"] = rsi(c)
    macd = ema(c, 12) - ema(c, 26)
    df["macd"] = macd
    df["macd_signal"] = ema(macd, 9)
    df["macd_hist"] = macd - df["macd_signal"]
    mid = c.rolling(20).mean()
    std = c.rolling(20).std()
    df["bb_mid"], df["bb_up"], df["bb_low"] = mid, mid + 2 * std, mid - 2 * std
    df["atr"] = atr(df)
    df = df.join(adx(df))
    df["vol_avg20"] = df["volume"].rolling(20).mean()
    return df
