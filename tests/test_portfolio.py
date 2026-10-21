import pytest

from tradebot import config, portfolio
from tradebot.engine import TradePlan
from tradebot.market import Asset

STOCK = Asset("AAPL", "stock", None, "AAPL", "Apple")
CRYPTO = Asset("BTC", "crypto", "BTC/USDT", "BTC-USD", "Bitcoin")


def plan(qty, entry=100.0, sl=97.0, side="LONG", trend=True):
    risk = abs(entry - sl)
    tp1, tp2 = (None, None) if trend else (entry + 1.5 * risk, entry + 3 * risk)
    return TradePlan(side, entry, sl, tp1, tp2, risk, qty, qty * entry, qty * risk, trend_atr=6.0 if trend else None)


# ---------- round_qty ----------

@pytest.mark.parametrize("qty, kind, expected", [
    (12.9, "stock", 12.0),
    (0.9, "stock", 0.0),
    (0.123456, "crypto", 0.1234),
    (12.3456, "crypto", 12.34),
    (1234.56, "crypto", 1234.0),
    (0.0, "crypto", 0.0),
    (-1.0, "stock", 0.0),
])
def test_round_qty_always_rounds_down(qty, kind, expected):
    assert portfolio.round_qty(qty, kind) == pytest.approx(expected)


# ---------- ticket ----------

def test_ticket_trend_exit_is_a_single_order():
    t = portfolio.ticket(STOCK, plan(250.7))
    assert (t["qty1"], t["qty2"], t["total"]) == (250.0, 0.0, 250.0)
    assert t["value"] == pytest.approx(25000.0)
    assert t["risk"] == pytest.approx(750.0)
    assert t["tv_symbol"] == "AAPL" and t["side_word"] == "Buy"


def test_ticket_fixed_exit_splits_in_two_orders():
    t = portfolio.ticket(STOCK, plan(11, trend=False))
    assert (t["qty1"], t["qty2"], t["total"]) == (5.0, 6.0, 11.0)


def test_ticket_fixed_exit_one_share_cannot_be_split():
    t = portfolio.ticket(STOCK, plan(1.4, trend=False))
    assert (t["qty1"], t["qty2"]) == (1.0, 0.0)


def test_ticket_crypto_symbol_and_short_side():
    t = portfolio.ticket(CRYPTO, plan(0.56789, entry=60000.0, sl=61000.0, side="SHORT"))
    assert t["tv_symbol"] == "BINANCE:BTCUSDT"
    assert t["side_word"] == "Sell"
    assert t["total"] == pytest.approx(0.5678)


def test_ticket_risk_never_exceeds_plan_risk():
    p = plan(333.33)
    assert portfolio.ticket(STOCK, p)["risk"] <= p.risk_amount


# ---------- open / close with a temp DB and temp config ----------

def test_open_trade_with_zero_qty_is_refused():
    with pytest.raises(ValueError):
        portfolio.open_trade(STOCK, "1d", plan(0.5), score=40)


def test_manual_trade_close_updates_balance():
    tid = portfolio.open_trade(STOCK, "1d", plan(100), score=40)
    t = portfolio.get(tid)
    assert t["status"] == "OPEN" and t["sl0"] == 97.0 and t["plan_entry"] == 100.0
    out = portfolio.close(tid, 106.0, "STOP")
    assert out["gain"] == pytest.approx(600.0)
    assert out["balance_before"] == pytest.approx(100000.0)
    assert out["balance_after"] == pytest.approx(100600.0)
    assert config.load_config()["account"]["balance"] == pytest.approx(100600.0)   # the temp config
    assert portfolio.plan_r(portfolio.get(tid)) == pytest.approx(2.0)   # +6 per share / 3 risk
    assert portfolio.open_trades() == []


def test_broker_trade_close_does_not_touch_balance():
    tid = portfolio.open_trade(STOCK, "1d", plan(100), score=40, qty=10, broker="alpaca", broker_order="o1")
    out = portfolio.close(tid, 94.0, "SL")
    assert out["gain"] == pytest.approx(-60.0)
    assert config.load_config()["account"]["balance"] == pytest.approx(100000.0)
    assert portfolio.stats(broker=True)["closed"] == 1
    assert portfolio.stats(broker=False)["closed"] == 0


def test_short_trade_pnl_sign():
    tid = portfolio.open_trade(CRYPTO, "1d", plan(1, entry=100.0, sl=103.0, side="SHORT"), score=-40)
    assert portfolio.close(tid, 90.0)["gain"] == pytest.approx(10.0)


def test_cannot_close_twice_and_cancel_only_open():
    tid = portfolio.open_trade(STOCK, "1d", plan(10), score=40)
    portfolio.close(tid, 100.0)
    with pytest.raises(ValueError):
        portfolio.close(tid, 100.0)
    with pytest.raises(ValueError):
        portfolio.cancel(tid)
    tid2 = portfolio.open_trade(STOCK, "1d", plan(10), score=40)
    portfolio.cancel(tid2)
    assert portfolio.get(tid2)["status"] == "CANCELLED"
