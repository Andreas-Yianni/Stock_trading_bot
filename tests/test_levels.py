import numpy as np
import pandas as pd
import pytest

from tradebot import levels


def zigzag(low=90.0, high=110.0, half_period=10, cycles=6, last=100.0) -> pd.DataFrame:
    """Price bouncing between `low` and `high`, ending at `last`, with a constant ATR of 2."""
    up = np.linspace(low, high, half_period, endpoint=False)
    down = np.linspace(high, low, half_period, endpoint=False)
    closes = np.concatenate([np.concatenate([up, down]) for _ in range(cycles)] + [np.linspace(low, last, 6)])
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1D", tz="UTC")
    return pd.DataFrame({"high": closes, "low": closes, "close": closes, "atr": 2.0}, index=idx)


def test_finds_repeated_support_and_resistance():
    lv = levels.find_levels(zigzag())
    (sup, sup_touches), (res, res_touches) = lv["support"][0], lv["resistance"][0]
    assert sup == pytest.approx(90.0)
    assert res == pytest.approx(110.0)
    assert sup_touches >= 3 and res_touches >= 3


def test_levels_are_on_the_right_side_nearest_first_and_at_most_three():
    rng = np.random.default_rng(1)
    closes = 100 + rng.normal(0, 1.5, 400).cumsum()
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="1D", tz="UTC")
    df = pd.DataFrame({"high": closes + 1, "low": closes - 1, "close": closes, "atr": 1.5}, index=idx)
    lv = levels.find_levels(df)
    price = closes[-1]
    sup = [p for p, _ in lv["support"]]
    res = [p for p, _ in lv["resistance"]]
    assert len(sup) <= 3 and len(res) <= 3
    assert all(p < price for p in sup) and all(p > price for p in res)
    assert sup == sorted(sup, reverse=True)   # nearest (highest) support first
    assert res == sorted(res)                 # nearest (lowest) resistance first
