"""Guards for the test setup itself: tests must never see the real config, data or network."""
import pytest
import requests

from tradebot import broker, config, journal, portfolio


def test_config_and_db_point_to_temp_dir(isolated):
    assert config.ROOT == isolated
    assert journal.DB.parent == isolated and portfolio.DB.parent == isolated
    assert broker.STATE_FILE.parent == isolated and broker.EQUITY_FILE.parent == isolated
    assert config.load_config()["watchlist"] == ["AAPL"]   # the test config, not the real one


def test_network_is_blocked():
    with pytest.raises(Exception, match="Network access is disabled"):
        requests.get("https://paper-api.alpaca.markets/v2/account", timeout=1)
