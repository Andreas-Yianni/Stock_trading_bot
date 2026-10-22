"""Shared test setup.

Every test runs isolated from the real bot state:
- config.yaml -> a temp copy with fixed test values (tradebot.config.ROOT points to tmp_path)
- data/journal.db, broker state/equity files -> temp files
- Alpaca keys -> fake paper-looking values (never the ones from .env); no Gemini key
- AI cache/log files -> temp files
- network -> blocked: any socket connection raises, so a missing mock fails loudly
"""
import socket
import sys
from pathlib import Path

import pandas as pd
import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradebot import ai, broker, config, journal, portfolio  # noqa: E402

TEST_CONFIG = {
    "account": {"currency": "USD", "balance": 100000.0, "risk_per_trade_pct": 1.0, "max_position_pct": 25},
    "analysis": {"default_timeframe": "1d", "min_score_for_signal": 30, "min_reward_risk": 1.5,
                 "trend_filter": False, "exit_mode": "trend", "trend_atr": 6, "trail_atr": 3, "long_only": True},
    "crypto": {"exchange": "binance", "quote": "USDT"},
    "watchlist": ["AAPL"],
    "backtest": {"fee_pct": 0.1, "max_days": 30},
    "broker": {"auto_trade": True, "universe": "top100", "risk_per_trade_pct": 0.5, "max_position_pct": 10,
               "max_positions": 10, "max_new_per_day": 5, "max_order_usd": 25000, "max_day_loss_pct": 5},
    "alerts": {"timeframe": "1d", "daily_scan_utc": "06:00", "scan_every_minutes": 60},
}


def _no_network(*args, **kwargs):
    raise RuntimeError("Network access is disabled in tests - mock the call instead")


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(TEST_CONFIG, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(config, "ROOT", tmp_path)
    db = tmp_path / "journal.db"
    monkeypatch.setattr(journal, "DB", db)
    monkeypatch.setattr(portfolio, "DB", db)
    monkeypatch.setattr(broker, "STATE_FILE", tmp_path / "broker_state.json")
    monkeypatch.setattr(broker, "EQUITY_FILE", tmp_path / "broker_equity.json")
    monkeypatch.setenv("ALPACA_API_KEY", "PKTESTKEY")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test-secret")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(ai, "CACHE_FILE", tmp_path / "ai_cache.json")
    monkeypatch.setattr(ai, "LOG_FILE", tmp_path / "ai_log.jsonl")
    monkeypatch.setattr(ai, "MIN_GAP", 0.0)
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    return tmp_path


@pytest.fixture
def cfg():
    """A fresh copy of the test config (dict), for pure functions that take cfg as an argument."""
    return yaml.safe_load(yaml.safe_dump(TEST_CONFIG))


def make_ohlcv(closes, start="2026-01-01", freq="1D", spread=1.0, volume=1000.0) -> pd.DataFrame:
    """Simple candles from a list of closes: open = previous close, high/low = max/min(open, close) ± spread."""
    closes = [float(c) for c in closes]
    opens = [closes[0]] + closes[:-1]
    idx = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC", name="ts")
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) + spread for o, c in zip(opens, closes)],
        "low": [min(o, c) - spread for o, c in zip(opens, closes)],
        "close": closes,
        "volume": [volume] * len(closes),
    }, index=idx)


def candles(rows, start="2026-01-01", freq="1D", atr=1.0) -> pd.DataFrame:
    """Explicit candles from (open, high, low, close) tuples, with a constant `atr` column."""
    idx = pd.date_range(start, periods=len(rows), freq=freq, tz="UTC", name="ts")
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"], index=idx, dtype=float)
    df["atr"] = atr
    return df
