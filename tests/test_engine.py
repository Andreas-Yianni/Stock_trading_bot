import numpy as np
import pandas as pd
import pytest
from conftest import make_ohlcv

from tradebot import engine, indicators
from tradebot.engine import Factor


# ---------- combine: weighted score and bias ----------

def test_combine_weighted_score():
    factors = [Factor("a", 1.0, 3, ""), Factor("b", -1.0, 1, "")]
    score, bias = engine.combine(factors, {"analysis": {"min_score_for_signal": 30}})
    assert score == pytest.approx(50.0)   # 100 * (3 - 1) / 4
    assert bias == "LONG"


@pytest.mark.parametrize("value, expected", [(0.30, "LONG"), (0.299, "NEUTRAL"), (-0.299, "NEUTRAL"),
                                             (-0.30, "SHORT")])
def test_combine_threshold_is_inclusive(value, expected):
    _, bias = engine.combine([Factor("x", value, 1, "")], {"analysis": {"min_score_for_signal": 30}})
    assert bias == expected


# ---------- trend_filter / long_only ----------

def _last(close, ema200):
    return pd.Series({"close": close, "ema200": ema200})


def test_long_only_turns_short_into_neutral(cfg):
    bias, msg = engine.trend_filter("SHORT", _last(100, 90), cfg)
    assert bias == "NEUTRAL" and msg


def test_trend_filter_off_passes_long_through(cfg):
    assert engine.trend_filter("LONG", _last(80, 100), cfg) == ("LONG", None)


def test_trend_filter_on_blocks_long_below_ema200(cfg):
    cfg["analysis"]["trend_filter"] = True
    bias, msg = engine.trend_filter("LONG", _last(80, 100), cfg)
    assert bias == "NEUTRAL" and msg
    assert engine.trend_filter("LONG", _last(120, 100), cfg) == ("LONG", None)


def test_trend_atr_depends_on_exit_mode(cfg):
    assert engine.trend_atr(cfg) == 6.0
    cfg["analysis"]["exit_mode"] = "fixed"
    assert engine.trend_atr(cfg) is None


# ---------- _trend_factors ----------

def _two_bars(**last):
    base = {"open": 100, "close": 101, "ema20": 100, "ema50": 99, "ema200": 90, "adx": 30, "macd_hist": 1.0,
            "rsi": 60, "volume": 1000, "vol_avg20": 1000}
    prev = {**base, "macd_hist": 0.5}
    return pd.DataFrame([prev, {**base, **last}])


def _factor(factors, name):
    return next(f for f in factors if f.name == name)


def test_trend_factor_bullish_with_strong_adx():
    f = _factor(engine._trend_factors(_two_bars(), None, "1w"), "Τάση")
    assert f.score == 1.0 and f.weight == 3


def test_trend_factor_ignored_when_adx_weak():
    assert _factor(engine._trend_factors(_two_bars(adx=15), None, "1w"), "Τάση").score == 0.0


@pytest.mark.parametrize("rsi, expected", [(25, 0.8), (75, 0.8), (60, 0.0), (40, -0.3)])
def test_rsi_factor_rules(rsi, expected):
    assert _factor(engine._trend_factors(_two_bars(rsi=rsi), None, "1w"), "RSI").score == expected


def test_volume_spike_counts_only_on_green_candle():
    green = _two_bars(volume=2000, open=100, close=101)
    red = _two_bars(volume=2000, open=101, close=100)
    assert _factor(engine._trend_factors(green, None, "1w"), "Όγκος").score == 0.7
    assert _factor(engine._trend_factors(red, None, "1w"), "Όγκος").score == 0.0


def test_htf_factor_only_with_enough_history():
    short_htf = pd.DataFrame([{"close": 110, "ema20": 105, "ema50": 100}] * 10)
    long_htf = pd.DataFrame([{"close": 110, "ema20": 105, "ema50": 100}] * 60)
    names = [f.name for f in engine._trend_factors(_two_bars(), short_htf, "1w")]
    assert "Τάση 1w" not in names
    f = _factor(engine._trend_factors(_two_bars(), long_htf, "1w"), "Τάση 1w")
    assert f.score == 1.0 and f.weight == 1


def test_factors_on_real_indicator_output_give_bounded_score(cfg):
    rng = np.random.default_rng(2)
    df = indicators.add_all(make_ohlcv(100 + rng.normal(0.1, 1, 300).cumsum()))
    factors = engine._trend_factors(df, None, "1w")
    score, bias = engine.combine(factors, cfg)
    assert -100 <= score <= 100
    assert bias in ("LONG", "SHORT", "NEUTRAL")
    assert all(-1 <= f.score <= 1 for f in factors)


# ---------- _build_plan: stop loss, targets, position size ----------

def _plan_df(close=100.0, atr=2.0, ema20=99.0):
    return pd.DataFrame([{"close": close, "atr": atr, "ema20": ema20}])


NO_LEVELS = {"support": [], "resistance": []}


def test_stop_behind_support_between_1_and_3_atr(cfg):
    lv = {"support": [(96.0, 3)], "resistance": []}
    p = engine._build_plan("LONG", _plan_df(), lv, cfg)
    assert p.stop_loss == pytest.approx(95.5)   # 96 - 0.25 * ATR
    assert p.risk_per_unit == pytest.approx(4.5)


def test_stop_falls_back_to_1_5_atr_when_no_suitable_level(cfg):
    lv = {"support": [(99.5, 2), (80.0, 2)], "resistance": []}   # one too close, one too far
    p = engine._build_plan("LONG", _plan_df(), lv, cfg)
    assert p.stop_loss == pytest.approx(97.0)


def test_trend_exit_has_no_take_profit(cfg):
    p = engine._build_plan("LONG", _plan_df(), NO_LEVELS, cfg)
    assert p.tp1 is None and p.tp2 is None
    assert p.trend_atr == 6.0
    assert p.rr_tp1 is None and p.rr_tp2 is None


def test_fixed_exit_targets(cfg):
    cfg["analysis"]["exit_mode"] = "fixed"
    p = engine._build_plan("LONG", _plan_df(), NO_LEVELS, cfg)   # entry 100, SL 97, risk 3
    assert p.tp1 == pytest.approx(104.5)
    assert p.tp2 == pytest.approx(109.0)
    assert p.rr_tp1 == pytest.approx(1.5) and p.rr_tp2 == pytest.approx(3.0)
    assert p.trend_atr is None


def test_size_from_risk_when_under_position_cap(cfg):
    cfg["account"]["max_position_pct"] = 100
    p = engine._build_plan("LONG", _plan_df(), NO_LEVELS, cfg)   # risk 1% of 100k = 1000, 3 per share
    assert p.risk_amount == pytest.approx(1000.0)
    assert p.qty == pytest.approx(1000 / 3)


def test_size_capped_by_max_position_pct(cfg):
    p = engine._build_plan("LONG", _plan_df(), NO_LEVELS, cfg)   # 333 shares * 100 > 25% of 100k
    assert p.position_value == pytest.approx(25000.0)
    assert p.qty == pytest.approx(250.0)
    assert p.risk_amount == pytest.approx(750.0) and p.risk_amount < 1000


def test_short_plan_is_mirror_image(cfg):
    cfg["analysis"]["exit_mode"] = "fixed"
    lv = {"support": [], "resistance": [(104.0, 2)]}
    p = engine._build_plan("SHORT", _plan_df(ema20=101.0), lv, cfg)
    assert p.stop_loss == pytest.approx(104.5)   # resistance + 0.25 ATR
    assert p.tp1 < p.entry < p.stop_loss
