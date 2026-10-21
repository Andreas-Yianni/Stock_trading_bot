"""broker.py with every Alpaca call mocked (the conftest also blocks the network)."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from tradebot import broker, config, portfolio
from tradebot.engine import TradePlan
from tradebot.market import Asset

STOCK = Asset("AAPL", "stock", None, "AAPL", "Apple")


def analysis(entry=100.0, sl=98.0, side="LONG", asset=STOCK):
    risk = abs(entry - sl)
    p = TradePlan(side, entry, sl, None, None, risk, 1, entry, risk, trend_atr=6.0)
    return SimpleNamespace(plan=p, asset=asset, timeframe="1d", score=45.0)


@pytest.fixture
def alpaca(monkeypatch):
    """Fake Alpaca account. Edit `state` in a test; posted orders are collected in `state.sent`."""
    state = SimpleNamespace(equity=100000.0, cash=100000.0, positions=[], orders=[], sent=[], reject=[])

    def fake_req(method, path, **kw):
        if method == "POST" and path == "/orders":
            state.sent.append(kw["json"])
            if state.reject:
                raise broker.BrokerError(state.reject.pop(0))
            return {"id": f"order-{len(state.sent)}"}
        raise AssertionError(f"unexpected Alpaca call {method} {path}")

    monkeypatch.setattr(broker, "account", lambda: {"equity": state.equity, "cash": state.cash,
                                                    "account_number": "PA123"})
    monkeypatch.setattr(broker, "positions", lambda: state.positions)
    monkeypatch.setattr(broker, "open_orders", lambda: state.orders)
    monkeypatch.setattr(broker, "_req", fake_req)
    return state


# ---------- safety constants ----------

def test_base_url_is_paper_only():
    assert broker.BASE.startswith("https://paper-api.alpaca.markets/")


def test_account_refuses_live_account(monkeypatch):
    monkeypatch.setattr(broker, "_req", lambda *a, **k: {"account_number": "LA999", "equity": "1"})
    with pytest.raises(broker.BrokerError):
        broker.account()


def test_account_refuses_live_key(monkeypatch):
    monkeypatch.setenv("ALPACA_API_KEY", "AKLIVEKEY")
    monkeypatch.setattr(broker, "_req", lambda *a, **k: {"account_number": "PA123", "equity": "1"})
    with pytest.raises(broker.BrokerError):
        broker.account()


def test_req_without_keys_fails_before_any_request(monkeypatch):
    monkeypatch.delenv("ALPACA_API_KEY")
    with pytest.raises(broker.BrokerError):
        broker._req("GET", "/account")


# ---------- open_from_analysis: sizing ----------

def test_order_capped_by_max_position_pct(alpaca):
    # risk 0.5% of 100k = 500 / 2 per share = 250 shares, but 10% cap = 10k / 100 = 100 shares
    t = broker.open_from_analysis(analysis())
    body = alpaca.sent[0]
    assert body["qty"] == "100"
    assert body["side"] == "buy" and body["type"] == "market" and body["order_class"] == "oto"
    assert body["stop_loss"] == {"stop_price": "98.00"}
    assert body["client_order_id"] == f"tb-AAPL-{datetime.now(timezone.utc):%Y%m%d}"
    assert t["broker"] == "alpaca" and t["qty1"] == 100 and t["broker_order"] == "order-1"


def test_order_sized_by_risk_when_stop_is_wide(alpaca):
    broker.open_from_analysis(analysis(sl=80.0))   # 500 / 20 = 25 shares (2.5k, under the cap)
    assert alpaca.sent[0]["qty"] == "25"


def test_order_limited_by_free_cash(alpaca):
    alpaca.cash = 3000.0
    broker.open_from_analysis(analysis())
    assert alpaca.sent[0]["qty"] == "30"


def test_unfilled_orders_reserve_cash(alpaca):
    alpaca.cash = 5000.0
    other = Asset("MSFT", "stock", None, "MSFT", "Microsoft")
    portfolio.open_trade(other, "1d", analysis().plan, 45, qty=20, broker="alpaca", broker_order="x")  # 2k pending
    broker.open_from_analysis(analysis())
    assert alpaca.sent[0]["qty"] == "30"   # (5000 - 2000) / 100


def test_order_capped_by_max_order_usd(alpaca):
    alpaca.equity = alpaca.cash = 1_000_000.0   # 10% = 100k, max_order_usd = 25k
    broker.open_from_analysis(analysis(entry=100.0, sl=50.0))
    assert alpaca.sent[0]["qty"] == "100"   # risk-based 5000/50 = 100 shares = 10k, under 25k
    alpaca.sent.clear()
    broker.open_from_analysis(analysis(entry=100.0, sl=99.0, asset=Asset("NVDA", "stock", None, "NVDA", "")))
    assert alpaca.sent[0]["qty"] == "250"   # 25k / 100


def test_skip_when_not_enough_cash(alpaca):
    alpaca.cash = 50.0
    assert "skip" in broker.open_from_analysis(analysis())
    assert alpaca.sent == []


def test_skip_when_position_already_exists(alpaca):
    alpaca.positions = [{"symbol": "AAPL", "qty": "5"}]
    assert "skip" in broker.open_from_analysis(analysis())
    assert alpaca.sent == []


def test_skip_when_max_positions_reached(alpaca):
    alpaca.positions = [{"symbol": f"S{i}", "qty": "1"} for i in range(10)]
    assert "skip" in broker.open_from_analysis(analysis())
    assert alpaca.sent == []


@pytest.mark.parametrize("side, kind", [("SHORT", "stock"), ("LONG", "crypto")])
def test_only_long_stocks_are_traded(alpaca, side, kind):
    a = analysis(sl=102.0 if side == "SHORT" else 98.0, side=side,
                 asset=Asset("BTC", kind, None, "BTC-USD", "Bitcoin"))
    assert broker.open_from_analysis(a) is None
    assert alpaca.sent == []


def test_gtc_rejected_retries_with_day(alpaca):
    alpaca.reject = ["Alpaca 422: invalid time_in_force"]
    t = broker.open_from_analysis(analysis())
    assert [b["time_in_force"] for b in alpaca.sent] == ["gtc", "day"]
    assert t["broker_order"] == "order-2"


def test_duplicate_client_order_id_is_a_skip(alpaca):
    alpaca.reject = ["Alpaca 422: client_order_id must be unique"]
    assert broker.open_from_analysis(analysis()) == {"skip": "έχει ήδη σταλεί εντολή σήμερα"}
    assert portfolio.open_trades() == []


# ---------- health(): bug circuit breaker ----------

@pytest.fixture
def no_buys_today(monkeypatch):
    monkeypatch.setattr(broker, "_today_buys", lambda: 0)


def acc(equity=100000.0, last=100000.0, **kw):
    return {"equity": str(equity), "last_equity": str(last), **kw}


def kinds(out):
    return [k for k, _ in out]


def test_healthy_account_has_no_alerts(no_buys_today):
    assert broker.health(acc(), []) == []


def test_day_loss_over_limit_pauses(no_buys_today):
    assert kinds(broker.health(acc(equity=94000), [])) == ["pause"]   # -6% > 5%
    assert broker.health(acc(equity=96000), []) == []                  # -4%


def test_blocked_account_pauses(no_buys_today):
    assert kinds(broker.health(acc(trading_blocked=True), [])) == ["pause"]


def test_short_position_pauses(no_buys_today):
    portfolio.open_trade(STOCK, "1d", analysis().plan, 45, qty=5, broker="alpaca", broker_order="o")
    out = broker.health(acc(), [{"symbol": "AAPL", "qty": "-5", "cost_basis": "500"}])
    assert kinds(out) == ["pause"]


def test_oversized_position_pauses(no_buys_today):
    portfolio.open_trade(STOCK, "1d", analysis().plan, 45, qty=5, broker="alpaca", broker_order="o")
    # cap = 10% of 100k = 10k; 1.5x = 15k
    assert kinds(broker.health(acc(), [{"symbol": "AAPL", "qty": "160", "cost_basis": "16000"}])) == ["pause"]
    assert broker.health(acc(), [{"symbol": "AAPL", "qty": "140", "cost_basis": "14000"}]) == []


def test_untracked_position_only_warns(no_buys_today):
    assert kinds(broker.health(acc(), [{"symbol": "TSLA", "qty": "1", "cost_basis": "300"}])) == ["warn"]


def test_too_many_buys_today_pauses(monkeypatch):
    monkeypatch.setattr(broker, "_today_buys", lambda: 6)
    assert kinds(broker.health(acc(), [])) == ["pause"]


def test_pause_switches_auto_trade_off_and_records_reason():
    broker.pause("test reason")
    assert config.load_config()["broker"]["auto_trade"] is False   # the temp config
    assert broker.paused()["reason"] == "test reason"
    broker.set_enabled(True)
    assert config.load_config()["broker"]["auto_trade"] is True
    assert broker.paused() is None
