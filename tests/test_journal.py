from types import SimpleNamespace

import pytest
from conftest import candles

from tradebot import journal
from tradebot.engine import TradePlan
from tradebot.market import Asset

# ---------- trend_stop: never moves backwards ----------


def test_trend_stop_long_rises_but_never_falls():
    assert journal.trend_stop("LONG", 95.0, 110.0, 1.0, 6) == 104.0
    assert journal.trend_stop("LONG", 95.0, 100.0, 1.0, 6) == 95.0


def test_trend_stop_short_is_mirror():
    assert journal.trend_stop("SHORT", 105.0, 90.0, 1.0, 6) == 96.0
    assert journal.trend_stop("SHORT", 105.0, 100.0, 1.0, 6) == 105.0


# ---------- simulate: trend exit ----------

def test_trend_exit_initial_sl_hit_is_minus_1r():
    df = candles([(100, 101, 94, 95)])
    status, r, _, px = journal.simulate("LONG", 100, 95, None, None, df, trend_atr=2)
    assert (status, px) == ("SL", 95)
    assert r == pytest.approx(-1.0)


def test_trend_exit_trailing_stop_locks_profit():
    # bar 1: high 105 -> stop rises to 105 - 2*1 = 103; bar 2: low 102 hits it
    df = candles([(100, 105, 99, 104), (104, 104.5, 102, 102.5)])
    status, r, when, px = journal.simulate("LONG", 100, 95, None, None, df, trend_atr=2)
    assert (status, px) == ("STOP", 103)
    assert r == pytest.approx(0.6)   # (103 - 100) / 5
    assert when == df.index[1]


def test_trend_exit_gap_below_stop_fills_at_open():
    df = candles([(100, 105, 99, 104), (101, 101.5, 100, 100.5)])   # opens at 101, under the 103 stop
    status, r, _, px = journal.simulate("LONG", 100, 95, None, None, df, trend_atr=2)
    assert (status, px) == ("STOP", 101)
    assert r == pytest.approx(0.2)


def test_trend_exit_open_trade_and_close_at_end():
    df = candles([(100, 102, 99, 101), (101, 103, 100, 102)])
    assert journal.simulate("LONG", 100, 95, None, None, df, trend_atr=6) == (None, None, None, None)
    status, r, _, px = journal.simulate("LONG", 100, 95, None, None, df, trend_atr=6, close_at_end=True)
    assert (status, px) == ("EXPIRED", 102)
    assert r == pytest.approx(0.4)


def test_trend_exit_short_side():
    df = candles([(100, 101, 95, 96), (96, 98, 95.5, 97.5)])   # low 95 -> stop 97, bar 2 high 98 hits it
    status, r, _, px = journal.simulate("SHORT", 100, 105, None, None, df, trend_atr=2)
    assert (status, px) == ("STOP", 97)
    assert r == pytest.approx(0.6)


def test_risk_override_measures_r_in_plan_units():
    df = candles([(100, 101, 94, 95)])
    _, r, _, _ = journal.simulate("LONG", 100, 95, None, None, df, trend_atr=2, risk=10)
    assert r == pytest.approx(-0.5)


# ---------- simulate: fixed exits (TP1 half + breakeven, TP2) ----------

def test_fixed_tp2_hit_directly():
    df = candles([(100, 110, 99, 109)])
    status, r, _, px = journal.simulate("LONG", 100, 95, 107.5, 115, df.assign(high=116.0))
    assert (status, px) == ("TP2", 115)
    assert r == pytest.approx(0.5 * 1.5 + 0.5 * 3)


def test_fixed_tp1_then_breakeven():
    df = candles([(100, 108, 99, 107), (107, 107.5, 99.5, 100)])
    status, r, _, px = journal.simulate("LONG", 100, 95, 107.5, 115, df)
    assert (status, px) == ("TP1", 100)
    assert r == pytest.approx(0.75)   # half closed at 1.5R, rest at entry


def test_fixed_same_candle_assumes_worst_case():
    df = candles([(100, 108, 94, 100)])   # touches both TP1 and SL
    status, r, _, _ = journal.simulate("LONG", 100, 95, 107.5, 115, df)
    assert status == "SL" and r == -1.0


# ---------- replay: next-open entry like the backtest ----------

def signal(**kw):
    return {"created_at": "2026-01-01T21:00:00+00:00", "timeframe": "1d", "side": "LONG", "entry": 100.0,
            "stop_loss": 95.0, "trend_atr": 2.0, **kw}


def test_replay_enters_at_next_open_and_tracks_stop():
    # bar 1 high 106 -> stop 104; bar 2 low 104.5 stays above it, high 107 -> stop 105
    df = candles([(101, 106, 100, 105), (105, 107, 104.5, 106)], start="2026-01-02")
    out = journal.replay(signal(), df)
    assert out["fill"] == 101 and out["status"] is None
    assert out["stop_now"] == pytest.approx(105.0)   # highest high 107 - 2 * 1


def test_replay_skips_when_opening_below_stop():
    df = candles([(94, 95, 90, 91)], start="2026-01-02")
    out = journal.replay(signal(), df)
    assert out["status"] == "SKIP" and out["r"] == 0.0


def test_replay_closed_trade_reports_r_in_plan_units():
    df = candles([(102, 102.5, 94, 95)], start="2026-01-02")   # fill 102, SL 95 hit
    out = journal.replay(signal(), df)
    assert out["status"] == "SL" and out["exit"] == 95
    assert out["r"] == pytest.approx(-7 / 5)   # risk unit = plan entry 100 - SL 95


def test_replay_ignores_candles_closed_before_the_signal():
    # signal at 2026-01-01 21:00: the 31/12 candle closed before it, the 1/1 candle closes after it (midnight)
    df = candles([(50, 51, 49, 50), (101, 103, 100, 102)], start="2025-12-31")
    assert journal.replay(signal(), df)["fill"] == 101


# ---------- record / stats with the temp DB ----------

def _analysis(symbol="AAPL"):
    p = TradePlan("LONG", 100.0, 95.0, None, None, 5.0, 10, 1000.0, 50.0, trend_atr=6.0)
    return SimpleNamespace(plan=p, asset=Asset(symbol, "stock", None, symbol, symbol), timeframe="1d", score=40.0)


def test_record_skips_duplicate_open_signal():
    sid = journal.record(_analysis())
    assert sid is not None
    assert journal.record(_analysis()) is None
    assert journal.open_signal_id("AAPL", "LONG") == sid
    assert journal.get(sid)["kind"] == "stock"
    assert journal.stats() == {"closed": 0, "open": 1}


def test_record_ignores_analysis_without_plan():
    assert journal.record(SimpleNamespace(plan=None)) is None
