"""Εντοπισμός στηρίξεων / αντιστάσεων από swing highs/lows."""
import pandas as pd


def _pivots(df: pd.DataFrame, k: int = 5) -> list[float]:
    highs, lows = df["high"], df["low"]
    win = 2 * k + 1
    is_high = highs == highs.rolling(win, center=True).max()
    is_low = lows == lows.rolling(win, center=True).min()
    return list(highs[is_high]) + list(lows[is_low])


def find_levels(df: pd.DataFrame, lookback: int = 300, k: int = 5) -> dict:
    """Επιστρέφει {'support': [(τιμή, αγγίγματα), ...], 'resistance': [...]}, πιο κοντινά πρώτα."""
    recent = df.tail(lookback)
    price = recent["close"].iloc[-1]
    tol = recent["atr"].iloc[-1] * 0.5

    clusters: list[list[float]] = []
    for p in sorted(_pivots(recent, k)):
        if clusters and p - clusters[-1][-1] <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    levels = [(sum(c) / len(c), len(c)) for c in clusters]

    support = sorted([l for l in levels if l[0] < price], key=lambda l: price - l[0])[:3]
    resistance = sorted([l for l in levels if l[0] > price], key=lambda l: l[0] - price)[:3]
    return {"support": support, "resistance": resistance}
