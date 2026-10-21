"""Αυτόματη σάρωση για το Alpaca paper: οι ~100 μεγαλύτερες μετοχές του S&P 500, με ΑΚΡΙΒΩΣ τους κανόνες
του backtest (και του τίμιου τεστ):
- σκορ μόνο από την τεχνική ανάλυση (χωρίς ειδήσεις: δεν υπάρχει ιστορικό τους, άρα δεν δοκιμάστηκαν),
- από το εβδομαδιαίο timeframe μόνο κεριά που έχουν κλείσει,
- «νέο» σήμα = η πλευρά του τελευταίου ΚΛΕΙΣΜΕΝΟΥ ημερήσιου κεριού διαφέρει από του προηγούμενου,
- τα σήματα της ίδιας μέρας με σειρά σκορ (πρώτα το μεγαλύτερο), όπως στην προσομοίωση λογαριασμού.

Δεν χρειάζεται «μνήμη» από χθες: το νέο σήμα βγαίνει από τα ίδια τα δεδομένα, όπως στο backtest. Το
`data/autoscan.json` κρατάει μόνο ποιο κερί επεξεργάστηκε, ώστε να μην ξαναγίνει η ίδια δουλειά (π.χ. αργία).
"""
import json
import time
from dataclasses import dataclass, field

import pandas as pd
import yfinance as yf

from . import broker, engine, indicators, journal, levels, market, portfolio
from .config import DATA_DIR, load_config

STATE = DATA_DIR / "autoscan.json"
TIMEFRAME = "1d"

# Οι 99 μετοχές του S&P 500 με τον μεγαλύτερο μέσο τζίρο (τιμή × όγκος, διάμεσος 63 ημερών) στις 16/10/2026,
# χωρίς δεύτερες κατηγορίες μετοχών (GOOG, BRK-B...). Από τα δεδομένα του research/honest_test.py.
# Αλλάζει με λίστα στο config.yaml → broker.universe.
UNIVERSE = (
    "MU NVDA SNDK AAPL TSLA AMD MSFT META INTC AMZN GOOGL AVGO PLTR MRVL LITE ORCL STX WDC AMAT DELL LLY LRCX MRNA "
    "WMT NFLX CRM JPM V XOM GEV PANW HOOD CAT CSCO COST NOW GS CRWD COHR QCOM BAC UNH KLAC JNJ APP MA CVX TXN HD "
    "COIN KO GLW SMCI IBM BA MCD C MRK ADI TMO GE ABBV PG WFC UBER ADBE INTU VRT BKNG NKE PEP AMGN HPE ACN TJX "
    "ANET VLO VZ T LIN MS APH TER NEE DDOG MPWR PFE DIS ISRG BSX AXP CIEN ABT FCX PM MPC GILD RTX COP"
).split()


def universe() -> list[str]:
    u = broker.settings().get("universe")
    return [s.upper() for s in u] if isinstance(u, list) and u else list(UNIVERSE)


@dataclass
class Signal:
    """Ό,τι χρειάζονται το broker.open_from_analysis και το journal.record (σαν μικρό engine.Analysis)."""
    asset: market.Asset
    score: float
    bias: str
    plan: engine.TradePlan | None
    factors: list = field(default_factory=list)
    timeframe: str = TIMEFRAME


def _stock(symbol: str) -> market.Asset:
    # Ρητά μετοχή: κάποια σύμβολα (π.χ. STX) υπάρχουν και ως crypto
    return market.resolve(f"{symbol}.US")


def download(symbols: list[str], period: str = "5y", attempts: int = 3) -> dict[str, pd.DataFrame]:
    """Ημερήσια κεριά για πολλές μετοχές (ίδια μορφή με το market: index σε UTC). Το Yahoo αρνείται κάποιες
    φορές προσωρινά («possibly delisted»): όσες δεν κατέβηκαν ξαναζητούνται, με μικρή αναμονή."""
    out = {}
    for n in range(attempts):
        todo = [s for s in symbols if s not in out]
        if not todo:
            break
        if n:
            time.sleep(10 * n)
        out |= _download(todo, period)
    return out


def _download(symbols: list[str], period: str) -> dict[str, pd.DataFrame]:
    raw = yf.download(symbols, period=period, interval="1d", auto_adjust=True, group_by="ticker",
                      threads=True, progress=False)
    out = {}
    if raw is None or raw.empty:
        return out
    for s in symbols:
        try:
            d = raw[s] if isinstance(raw.columns, pd.MultiIndex) else raw
        except KeyError:
            continue
        d = d.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        if not len(d):
            continue
        idx = pd.to_datetime(d.index)
        d.index = (idx.tz_localize("America/New_York") if idx.tz is None else idx).tz_convert("UTC")
        d.index.name = "ts"
        out[s] = d
    return out


def weekly(d: pd.DataFrame) -> pd.DataFrame:
    """Εβδομαδιαία κεριά από τα ημερήσια (εβδομάδα από Δευτέρα, όπως του Yahoo)."""
    ny = d.index.tz_convert("America/New_York")
    wk = ny.normalize() - pd.to_timedelta(ny.weekday, unit="D")
    w = d.groupby(wk).agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    w.index = pd.DatetimeIndex(w.index).tz_convert("UTC")
    return w


def _evaluate(df: pd.DataFrame, htf: pd.DataFrame, k: int, cfg: dict):
    """Το ίδιο με το backtest.run → evaluate(k): σκορ και πλευρά στο κλείσιμο του κεριού k."""
    window = df.iloc[: k + 1]
    closes_at = df.index[k] + market.TF_DELTA[TIMEFRAME]
    h = htf.iloc[: int((htf.index + market.TF_DELTA["1w"]).searchsorted(closes_at, side="right"))]
    factors = engine._trend_factors(window, h, "1w")
    score, bias = engine.combine(factors, cfg)
    bias, _ = engine.trend_filter(bias, window.iloc[-1], cfg)
    return window, factors, score, bias


def fresh_signal(symbol: str, d: pd.DataFrame, cfg: dict, now: pd.Timestamp | None = None) -> Signal | None:
    """Νέο σήμα LONG στο τελευταίο κλεισμένο κερί (ή None)."""
    now = now or pd.Timestamp.now(tz="UTC")
    d = d[d.index + market.TF_DELTA[TIMEFRAME] <= now]   # μόνο κλεισμένα κεριά
    if len(d) < 260:
        return None
    df = indicators.add_all(d)
    htf = indicators.add_all(weekly(d))
    window, factors, score, bias = _evaluate(df, htf, len(df) - 1, cfg)
    if bias != "LONG" or _evaluate(df, htf, len(df) - 2, cfg)[3] == "LONG":
        return None
    plan = engine._build_plan(bias, window, levels.find_levels(window), cfg)
    return Signal(_stock(symbol), score, bias, plan, factors)


def _state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def run(force: bool = False) -> dict:
    """Βρίσκει τα νέα σήματα και ανοίγει θέσεις στο Alpaca (με τα όρια του broker).
    Επιστρέφει {"bar", "scanned", "signals", "opened": [trade], "skipped": [(σύμβολο, λόγος)], "errors"}."""
    cfg = load_config()
    syms = universe()
    data = download(syms)
    errors = [s for s in syms if s not in data]
    last = max((d.index[-1] for d in data.values()), default=None)
    out = {"bar": None, "scanned": len(data), "signals": 0, "opened": [], "skipped": [], "errors": errors}
    if last is None:
        return out
    now = pd.Timestamp.now(tz="UTC")
    closed = [d[d.index + market.TF_DELTA[TIMEFRAME] <= now] for d in data.values()]
    bar = max((d.index[-1] for d in closed if len(d)), default=None)
    out["bar"] = str(bar.date()) if bar is not None else None
    if not force and out["bar"] and _state().get("last_bar") == out["bar"]:
        out["already"] = True   # αυτό το κερί το επεξεργαστήκαμε ήδη (π.χ. αργία)
        return out

    signals = []
    for s, d in data.items():
        try:
            sig = fresh_signal(s, d, cfg, now)
        except Exception:
            out["errors"].append(s)
            continue
        if sig and len(d[d.index + market.TF_DELTA[TIMEFRAME] <= now]) and \
                d[d.index + market.TF_DELTA[TIMEFRAME] <= now].index[-1] == bar:
            signals.append(sig)
    signals.sort(key=lambda x: -x.score)
    out["signals"] = len(signals)

    st = broker.settings()
    today = now.strftime("%Y-%m-%d")
    opened_today = sum(1 for t in portfolio.open_trades() + portfolio.closed_trades(broker=True)
                       if t["broker"] and str(t["opened_at"]).startswith(today))
    for sig in signals:
        if opened_today >= int(st["max_new_per_day"]):
            out["skipped"].append((sig.asset.symbol, f"όριο {st['max_new_per_day']} νέων θέσεων τη μέρα"))
            continue
        try:
            t = broker.open_from_analysis(sig)
        except broker.BrokerError as e:
            out["skipped"].append((sig.asset.symbol, str(e)))
            continue
        if not t:
            continue
        if "skip" in t:
            out["skipped"].append((sig.asset.symbol, t["skip"]))
            continue
        sid = journal.record(sig) or journal.open_signal_id(sig.asset.symbol, "LONG")
        portfolio.link_signal(t["id"], sid)
        out["opened"].append(portfolio.get(t["id"]))
        opened_today += 1
    STATE.write_text(json.dumps({"last_bar": out["bar"], "at": now.isoformat(), "signals": out["signals"],
                                 "opened": [t["symbol"] for t in out["opened"]]}), encoding="utf-8")
    return out
