"""Λήψη τιμών (OHLCV) για μετοχές (Yahoo Finance) και crypto (ccxt / δημόσια δεδομένα exchange)."""
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache

import ccxt
import pandas as pd
import yfinance as yf

from .config import load_config

TIMEFRAMES = ["15m", "1h", "4h", "1d", "1w"]
HIGHER_TF = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": "1w", "1w": "1w"}

KNOWN_CRYPTO = {
    "BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "LINK", "POL", "MATIC",
    "LTC", "TRX", "SHIB", "PEPE", "SUI", "TON", "NEAR", "APT", "ARB", "OP", "ATOM", "UNI",
    "XLM", "HBAR", "INJ", "RENDER", "FET", "WIF", "BONK", "SEI", "TIA", "AAVE", "ETC", "BCH",
    "FIL", "ICP", "KAS", "ONDO", "JUP", "ENA", "HYPE", "TAO", "XMR", "ALGO", "VET", "STX",
}

# Μεγάλες μετοχές με το ίδιο σύμβολο με κάποιο crypto: εννοούμε τη μετοχή (για το crypto: T/USDT)
PREFER_STOCK = {"T", "F", "C", "CVX", "AI"}

CRYPTO_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana", "BNB": "BNB", "XRP": "XRP Ripple",
    "ADA": "Cardano", "DOGE": "Dogecoin", "AVAX": "Avalanche", "DOT": "Polkadot",
    "LINK": "Chainlink", "LTC": "Litecoin", "SHIB": "Shiba Inu", "SUI": "Sui",
    "TON": "Toncoin", "NEAR": "NEAR Protocol", "PEPE": "Pepe coin",
}


@dataclass
class Asset:
    symbol: str          # Αυτό που βλέπει ο χρήστης, π.χ. "BTC" ή "AAPL"
    kind: str            # "crypto" ή "stock"
    ccxt_symbol: str | None
    yf_symbol: str
    name: str            # Για αναζήτηση ειδήσεων


@lru_cache(maxsize=256)
def company_name(symbol: str) -> str:
    """Σύντομο όνομα εταιρείας (π.χ. 'Apple') για καλύτερη αναζήτηση ειδήσεων."""
    try:
        name = yf.Ticker(symbol).info.get("shortName") or symbol
    except Exception:
        return symbol
    for suffix in [",", " Inc", " Corp", " Corporation", " Ltd", " plc", " N.V.", " S.A.", " Holdings", " Company"]:
        name = name.split(suffix)[0]
    return name.strip() or symbol


@lru_cache(maxsize=4)
def _exchange_bases(quote: str) -> frozenset[str]:
    """Όλα τα crypto που έχει το exchange σε ζεύγος με το quote (π.χ. WLD/USDT), όχι μόνο όσα ξέρουμε."""
    try:
        markets = _exchange().load_markets()
    except Exception:
        return frozenset()
    return frozenset(m["base"] for m in markets.values()
                     if m.get("quote") == quote and m.get("spot") and m.get("active", True))


def resolve(raw: str) -> Asset:
    s = raw.strip().upper().replace(" ", "")
    quote = load_config()["crypto"]["quote"]
    if s.endswith(".US"):  # ρητά μετοχή, π.χ. T.US = AT&T
        return Asset(s[:-3], "stock", None, s[:-3], s[:-3])
    base = None
    if "/" in s:
        base = s.split("/")[0]
    elif s.endswith("-USD") and s[:-4] in KNOWN_CRYPTO:
        base = s[:-4]
    elif s.endswith(quote) and s[: -len(quote)] in KNOWN_CRYPTO:
        base = s[: -len(quote)]
    elif s in KNOWN_CRYPTO or (s not in PREFER_STOCK and s in _exchange_bases(quote)):
        base = s
    if base:
        return Asset(base, "crypto", f"{base}/{quote}", f"{base}-USD", CRYPTO_NAMES.get(base, base))
    return Asset(s, "stock", None, s, s)


@lru_cache(maxsize=1)
def _exchange():
    name = load_config()["crypto"]["exchange"]
    ex = getattr(ccxt, name)({"enableRateLimit": True})
    return ex


def _from_ccxt(asset: Asset, tf: str, limit: int = 500) -> pd.DataFrame:
    rows = _exchange().fetch_ohlcv(asset.ccxt_symbol, timeframe=tf, limit=limit)
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df.set_index("ts")


_YF_PARAMS = {
    "15m": ("15m", "60d"),
    "1h": ("1h", "180d"),
    "4h": ("1h", "720d"),  # Το Yahoo δεν έχει 4h, το φτιάχνουμε από 1h
    "1d": ("1d", "5y"),
    "1w": ("1wk", "10y"),
}


def _from_yf(symbol: str, tf: str, start: pd.Timestamp | None = None, adjusted: bool = True) -> pd.DataFrame:
    """adjusted=False: πραγματικές τιμές (μόνο για splits), όπως εκτελέστηκαν οι εντολές. Χρειάζεται όταν
    συγκρίνουμε αποθηκευμένες τιμές (είσοδος, stop) με παλιά κεριά: με adjusted=True, μετά από κάθε μέρισμα οι
    παλιές τιμές «κατεβαίνουν» και θα φαινόταν χτύπημα του stop που δεν έγινε."""
    interval, period = _YF_PARAMS[tf]
    if start is not None:
        df = yf.Ticker(symbol).history(start=start.strftime("%Y-%m-%d"), interval=interval, auto_adjust=adjusted)
    else:
        df = yf.Ticker(symbol).history(period=period, interval=interval, auto_adjust=adjusted)
    if df.empty:
        raise ValueError(f"Δεν βρέθηκαν δεδομένα για '{symbol}'. Είναι σωστό το σύμβολο;")
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df.index = pd.to_datetime(df.index, utc=True)
    df.index.name = "ts"
    if tf == "4h":
        df = df.resample("4h").agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna()
    return df


TF_DELTA = {
    "15m": pd.Timedelta(minutes=15), "1h": pd.Timedelta(hours=1), "4h": pd.Timedelta(hours=4),
    "1d": pd.Timedelta(days=1), "1w": pd.Timedelta(weeks=1),
}
# Πόσο πίσω δίνει το Yahoo για intraday δεδομένα
_YF_MAX_DAYS = {"15m": 59, "1h": 720, "4h": 720}


def _ccxt_history(asset: Asset, tf: str, since: pd.Timestamp) -> pd.DataFrame:
    ex = _exchange()
    step = ex.parse_timeframe(tf) * 1000
    since_ms, now_ms = int(since.timestamp() * 1000), ex.milliseconds()
    rows = []
    for _ in range(300):  # σελίδες των 1000 κεριών
        batch = ex.fetch_ohlcv(asset.ccxt_symbol, timeframe=tf, since=since_ms, limit=1000)
        if not batch:
            break
        rows += batch
        since_ms = batch[-1][0] + step
        if since_ms > now_ms:
            break
    if not rows:
        raise ValueError(f"Δεν βρέθηκαν δεδομένα για '{asset.ccxt_symbol}'")
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = df.set_index("ts")
    return df[~df.index.duplicated()]


_memo: dict | None = None


@contextmanager
def history_cache():
    """Μέσα σε αυτό το block το ίδιο ιστορικό (σύμβολο, timeframe, μέρες) κατεβαίνει μόνο μία φορά,
    π.χ. όταν τρέχουμε πολλές παραλλαγές backtest στα ίδια σύμβολα."""
    global _memo
    outer, _memo = _memo, ({} if _memo is None else _memo)
    try:
        yield
    finally:
        _memo = outer


def fetch_history(asset: Asset, tf: str, days: int) -> pd.DataFrame:
    """Μεγάλο ιστορικό για backtest (crypto: όσο έχει το exchange, μετοχές: όσο δίνει το Yahoo)."""
    memo = _memo
    if memo is not None:
        key = (asset.symbol, asset.kind, tf, days)
        if key not in memo:
            memo[key] = _fetch_history(asset, tf, days)
        return memo[key]
    return _fetch_history(asset, tf, days)


def _fetch_history(asset: Asset, tf: str, days: int) -> pd.DataFrame:
    if tf not in TIMEFRAMES:
        raise ValueError(f"Μη έγκυρο timeframe '{tf}'. Επιλογές: {', '.join(TIMEFRAMES)}")
    now = pd.Timestamp.now(tz="UTC")
    since = now - pd.Timedelta(days=days)
    if asset.kind == "crypto":
        try:
            return _ccxt_history(asset, tf, since)
        except Exception:
            pass
    since = max(since, now - pd.Timedelta(days=_YF_MAX_DAYS.get(tf, days)))
    df = _from_yf(asset.yf_symbol, tf, start=since)
    return df[df.index >= since]


def fetch_ohlcv(asset: Asset, tf: str, adjusted: bool = True) -> pd.DataFrame:
    """`adjusted=False` (μόνο μετοχές): πραγματικές τιμές χωρίς προσαρμογή για μερίσματα (βλ. _from_yf)."""
    if tf not in TIMEFRAMES:
        raise ValueError(f"Μη έγκυρο timeframe '{tf}'. Επιλογές: {', '.join(TIMEFRAMES)}")
    if asset.kind == "crypto":
        try:
            return _from_ccxt(asset, tf)
        except Exception:
            # Αν το exchange δεν είναι διαθέσιμο από τη χώρα σου ή δεν έχει το ζεύγος
            return _from_yf(asset.yf_symbol, tf)
    return _from_yf(asset.yf_symbol, tf, adjusted=adjusted)
