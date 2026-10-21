import numpy as np
import pandas as pd
import pytest
from conftest import make_ohlcv

from tradebot import indicators


def test_ema_matches_recursive_formula():
    # span=3 -> alpha=0.5: 1, 1.5, 2.25, 3.125
    out = indicators.ema(pd.Series([1.0, 2.0, 3.0, 4.0]), 3)
    assert list(out) == pytest.approx([1.0, 1.5, 2.25, 3.125])


def test_ema_of_constant_is_constant():
    assert (indicators.ema(pd.Series([7.0] * 50), 20) == 7.0).all()


def test_rsi_extremes():
    assert indicators.rsi(pd.Series(np.arange(1.0, 60.0))).iloc[-1] == pytest.approx(100)
    assert indicators.rsi(pd.Series(np.arange(60.0, 1.0, -1))).iloc[-1] == pytest.approx(0)


def test_rsi_stays_between_0_and_100():
    rng = np.random.default_rng(0)
    close = pd.Series(100 + rng.normal(0, 1, 500).cumsum())
    r = indicators.rsi(close)
    assert r.between(0, 100).all()


def test_atr_of_constant_range_without_gaps():
    df = make_ohlcv([100.0] * 40, spread=1.0)   # every candle: high - low = 2, no gaps
    assert indicators.atr(df).iloc[-1] == pytest.approx(2.0)


def test_adx_in_steady_uptrend():
    df = make_ohlcv(np.linspace(100, 200, 120))
    out = indicators.adx(df).iloc[-1]
    assert out.plus_di > out.minus_di
    assert out.adx > 25


def test_add_all_adds_columns_without_touching_input():
    df = make_ohlcv(np.linspace(100, 150, 250))
    before = df.copy()
    out = indicators.add_all(df)
    expected = {"ema20", "ema50", "ema200", "rsi", "macd", "macd_signal", "macd_hist",
                "bb_mid", "bb_up", "bb_low", "atr", "adx", "plus_di", "minus_di", "vol_avg20"}
    assert expected <= set(out.columns)
    assert len(out) == len(df)
    pd.testing.assert_frame_equal(df, before)
    last = out.iloc[-1]
    assert last.bb_low < last.bb_mid < last.bb_up
    assert last.macd_hist == pytest.approx(last.macd - last.macd_signal)
