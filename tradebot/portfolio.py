"""Τα ΔΙΚΑ σου paper trades: ό,τι πατάς «✅ Το πήρα» στο Telegram.

Διαφέρει από το journal.py (που κρατάει ΟΛΑ τα σήματα του bot για στατιστική): εδώ μπαίνουν μόνο
όσα άνοιξες πράγματι στην πλατφόρμα paper trading, με την ποσότητα που έβαλες.
Το bot ελέγχει την τιμή, κλείνει αυτόματα ό,τι χτύπησε SL/TP και ενημερώνει το κεφάλαιο στο config.

Κανόνες (ίδιοι με το δελτίο εντολής): δύο εντολές με μισή ποσότητα η καθεμία, η πρώτη με TP1 και η
δεύτερη με TP2, ίδιο SL. Όταν κλείσει η πρώτη, το SL της δεύτερης πάει στην είσοδο. Αν η ποσότητα δεν
χωρίζεται (π.χ. 1 μετοχή), μία εντολή με TP το TP1.
Έξοδος «τάσης» (`trend_atr`): μία εντολή χωρίς TP. Κάθε πρωί `update_trend_stops` ανεβάζει το stop στο
(υψηλότερο high από την είσοδο − trend_atr × ATR) και το bot σού λέει να το αλλάξεις στην πλατφόρμα.
"""
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

import pandas as pd

from . import indicators, journal, market
from .config import DATA_DIR, load_config, save_config

DB = DATA_DIR / "journal.db"


@contextmanager
def _conn():
    """`with _conn() as c:` κάνει commit (rollback σε λάθος) και κλείνει τη σύνδεση στο τέλος."""
    c = _open()
    try:
        with c:
            yield c
    finally:
        c.close()


def _open():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute("""
        CREATE TABLE IF NOT EXISTS my_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            opened_at TEXT, symbol TEXT, kind TEXT, timeframe TEXT, side TEXT, score REAL,
            entry REAL, stop_loss REAL, tp1 REAL, tp2 REAL,
            qty1 REAL, qty2 REAL,              -- εντολή 1 (TP1) και εντολή 2 (TP2)
            tp1_done INTEGER DEFAULT 0, tp1_at TEXT,
            status TEXT DEFAULT 'OPEN',        -- OPEN, CLOSED, CANCELLED
            exit_reason TEXT, closed_at TEXT, exit_price REAL,
            pnl REAL DEFAULT 0                 -- πραγματοποιημένο κέρδος/ζημιά σε νόμισμα
        )""")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(my_trades)")}
    for col, typ in (("trend_atr", "REAL"), ("sl0", "REAL"), ("stop_at", "TEXT"),
                     ("broker", "TEXT"), ("broker_order", "TEXT"), ("filled", "INTEGER DEFAULT 0"),
                     ("plan_entry", "REAL"), ("signal_id", "INTEGER")):
        # Έξοδος «τάσης»: πόσα ATR, το αρχικό SL (για το R) και από πότε ισχύει το τρέχον stop_loss.
        # broker: 'alpaca' για τα αυτόματα trades (broker.py), με την εντολή και αν έχει εκτελεστεί.
        # plan_entry: η είσοδος του plan (κλείσιμο του σήματος): το 1R = plan_entry − sl0, όπως στο backtest.
        # signal_id: το σήμα του journal από το οποίο βγήκε (για τη σύγκριση εκτέλεσης με το backtest)
        if col not in cols:
            c.execute(f"ALTER TABLE my_trades ADD COLUMN {col} {typ}")
    return c


# ---------- Δελτίο εντολής ----------

def round_qty(qty: float, kind: str) -> float:
    """Μετοχές: ακέραιες. Crypto: 4 σημαντικά ψηφία, πάντα προς τα κάτω (για να μην ξεπεράσεις το ρίσκο)."""
    if qty <= 0:
        return 0.0
    if kind == "stock":
        return float(math.floor(qty))
    digits = 3 - int(math.floor(math.log10(qty)))
    f = 10 ** digits
    return math.floor(qty * f) / f


def plain(x: float) -> str:
    """Τιμή χωρίς διαχωριστικό χιλιάδων, όπως τη γράφεις σε πεδίο εντολής."""
    return f"{x:.2f}" if x >= 1 else f"{x:.6g}"


def ticket(asset: market.Asset, plan) -> dict:
    total = round_qty(plan.qty, asset.kind)
    q1 = round_qty(total / 2, asset.kind)
    q2 = round_qty(total - q1, asset.kind) if q1 else 0.0
    if plan.trend_atr is not None or not q1 or not q2:   # έξοδος τάσης ή δεν χωρίζεται: μία εντολή
        q1, q2 = total, 0.0
    return {
        "tv_symbol": f"BINANCE:{asset.symbol}USDT" if asset.kind == "crypto" else asset.symbol,
        "side_word": "Buy" if plan.side == "LONG" else "Sell",
        "qty1": q1, "qty2": q2, "total": q1 + q2,
        "value": (q1 + q2) * plan.entry,
        "risk": (q1 + q2) * plan.risk_per_unit,
    }


# ---------- Καταγραφή ----------

def open_trade(asset: market.Asset, timeframe: str, plan, score: float, qty: float | None = None,
               broker: str | None = None, broker_order: str | None = None) -> int:
    t = ticket(asset, plan)
    if qty is not None:   # αυτόματο trade: η ποσότητα βγήκε από τον λογαριασμό του broker
        t = {**t, "qty1": float(qty), "qty2": 0.0, "total": float(qty)}
    if not t["total"]:
        raise ValueError("Η ποσότητα βγαίνει 0 με το τρέχον κεφάλαιο/ρίσκο")
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO my_trades (opened_at, symbol, kind, timeframe, side, score, entry, stop_loss, tp1, tp2, "
            "qty1, qty2, trend_atr, sl0, stop_at, broker, broker_order, plan_entry) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (now, asset.symbol, asset.kind, timeframe, plan.side, score, plan.entry, plan.stop_loss, plan.tp1,
             plan.tp2, t["qty1"], t["qty2"], plan.trend_atr, plan.stop_loss, now, broker, broker_order, plan.entry))
        return cur.lastrowid


def link_signal(trade_id: int, signal_id: int | None) -> None:
    with _conn() as c:
        c.execute("UPDATE my_trades SET signal_id=? WHERE id=?", (signal_id, trade_id))


def set_fill(trade_id: int, price: float, qty: float) -> None:
    """Αυτόματο trade: η πραγματική τιμή και ποσότητα εκτέλεσης στον broker."""
    with _conn() as c:
        c.execute("UPDATE my_trades SET entry=?, qty1=?, qty2=0, filled=1 WHERE id=?", (price, qty, trade_id))


def get(trade_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM my_trades WHERE id=?", (trade_id,)).fetchone()
        return dict(r) if r else None


def open_trades() -> list[dict]:
    with _conn() as c:
        return [dict(r) for r in c.execute("SELECT * FROM my_trades WHERE status='OPEN' ORDER BY id")]


def closed_trades(broker: bool | None = False) -> list[dict]:
    """Κλειστά trades: χειροκίνητα (προεπιλογή), αυτόματα (broker=True) ή όλα (None)."""
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM my_trades WHERE status='CLOSED' ORDER BY id")]
    return rows if broker is None else [r for r in rows if bool(r["broker"]) == broker]


def _pnl(t: dict, qty: float, price: float) -> float:
    return (price - t["entry"]) * qty * (1 if t["side"] == "LONG" else -1)


def _add_to_balance(amount: float) -> tuple[float, float]:
    cfg = load_config()
    before = float(cfg["account"]["balance"])
    cfg["account"]["balance"] = round(before + amount, 2)
    save_config(cfg)
    return before, cfg["account"]["balance"]


def remaining_qty(t: dict) -> float:
    return t["qty2"] if t["tp1_done"] else t["qty1"] + t["qty2"]


def close(trade_id: int, price: float, reason: str = "MANUAL", when: str | None = None) -> dict:
    """Κλείνει ό,τι έχει μείνει ανοιχτό στην τιμή `price`. Επιστρέφει το ενημερωμένο trade + κεφάλαιο."""
    t = get(trade_id)
    if not t or t["status"] != "OPEN":
        raise ValueError(f"Δεν υπάρχει ανοιχτό trade #{trade_id}")
    gain = _pnl(t, remaining_qty(t), price)
    with _conn() as c:
        c.execute("UPDATE my_trades SET status='CLOSED', exit_reason=?, closed_at=?, exit_price=?, pnl=pnl+? "
                  "WHERE id=?", (reason, when or datetime.now(timezone.utc).isoformat(), price, gain, trade_id))
    if t["broker"]:   # ο λογαριασμός του broker ενημερώνεται μόνος του· το config.balance είναι του TradingView
        cur = float(load_config()["account"]["balance"])
        before, after = cur, cur
    else:
        before, after = _add_to_balance(gain)
    return {**get(trade_id), "gain": gain, "balance_before": before, "balance_after": after}


def cancel(trade_id: int) -> None:
    t = get(trade_id)
    if not t or t["status"] != "OPEN" or t["tp1_done"]:
        raise ValueError(f"Το #{trade_id} δεν μπορεί να ακυρωθεί (δεν είναι ανοιχτό ή έχει ήδη κλείσει μέρος του)")
    with _conn() as c:
        c.execute("UPDATE my_trades SET status='CANCELLED' WHERE id=?", (trade_id,))


def _tp1_partial(t: dict, when) -> dict:
    gain = _pnl(t, t["qty1"], t["tp1"])
    with _conn() as c:
        c.execute("UPDATE my_trades SET tp1_done=1, tp1_at=?, pnl=pnl+? WHERE id=?", (str(when), gain, t["id"]))
    before, after = _add_to_balance(gain)
    return {**get(t["id"]), "gain": gain, "balance_before": before, "balance_after": after}


def _r_multiple(t: dict) -> float:
    risk = abs(t["entry"] - (t["sl0"] or t["stop_loss"])) * (t["qty1"] + t["qty2"])
    return t["pnl"] / risk if risk else 0.0


def plan_r(t: dict, price: float | None = None) -> float | None:
    """Αποτέλεσμα σε R του PLAN (1R = είσοδος plan − αρχικό SL), όπως μετράει το backtest. Χωρίς `price`:
    το πραγματοποιημένο (κλειστό trade), με `price`: το τωρινό ενός ανοιχτού."""
    unit = abs((t["plan_entry"] or t["entry"]) - (t["sl0"] or t["stop_loss"])) * (t["qty1"] + t["qty2"])
    if not unit:
        return None
    if price is None:
        return t["pnl"] / unit
    sign = 1 if t["side"] == "LONG" else -1
    return (t["pnl"] + sign * (price - t["entry"]) * remaining_qty(t)) / unit


def history(limit: int = 15, broker: bool | None = None) -> list[dict]:
    """Τα πιο πρόσφατα κλειστά trades (πρώτα το νεότερο), με το R τους."""
    rows = sorted(closed_trades(broker), key=lambda t: t["closed_at"] or "", reverse=True)[:limit]
    return [{**t, "r": _r_multiple(t)} for t in rows]


def check_all() -> list[tuple[str, dict]]:
    """Ελέγχει τα ανοιχτά trades με κεριά 15 λεπτών από τη στιγμή που άνοιξαν.
    Επιστρέφει γεγονότα: ("TP1", trade) για μερικό κλείσιμο, ("SL"/"TP"/"BE", trade) για πλήρες κλείσιμο."""
    events = []
    for t in open_trades():
        if t["broker"]:
            continue   # τα αυτόματα trades τα κλείνει ο broker (broker.sync)
        opened = pd.Timestamp(t["opened_at"])
        asset = _asset(t)
        try:
            days = max(2, (pd.Timestamp.now(tz="UTC") - opened).days + 2)
            df = market.fetch_history(asset, "15m", days)
        except Exception:
            continue
        df = df[df.index + market.TF_DELTA["15m"] > opened]  # κεριά που έκλεισαν μετά την είσοδο
        if t["trend_atr"]:
            ev = _check_trend(t, df)
            if ev:
                events.append(ev)
            continue
        if t["tp1_done"]:
            df = df[df.index > pd.Timestamp(t["tp1_at"])]  # το SL στην είσοδο ισχύει μόνο μετά το TP1
        sign = 1 if t["side"] == "LONG" else -1
        tp1_done = bool(t["tp1_done"])
        for ts, row in df.iterrows():
            adverse, favorable = (row.low, row.high) if sign > 0 else (row.high, row.low)
            stop = t["entry"] if tp1_done else t["stop_loss"]
            if sign * (adverse - stop) <= 0:   # πρώτα το χειρότερο, αν χτυπήθηκαν και τα δύο
                reason = "BE" if tp1_done else "SL"
                events.append((reason, close(t["id"], stop, reason, str(ts))))
                break
            if not tp1_done and sign * (favorable - t["tp1"]) >= 0:
                if t["qty2"]:
                    t = _tp1_partial(t, ts)
                    events.append(("TP1", t))
                    tp1_done = True
                    continue
                events.append(("TP", close(t["id"], t["tp1"], "TP", str(ts))))
                break
            if tp1_done and sign * (favorable - t["tp2"]) >= 0:
                events.append(("TP", close(t["id"], t["tp2"], "TP", str(ts))))
                break
    return events


def _asset(t: dict) -> market.Asset:
    return market.resolve(f"{t['symbol']}/USDT" if t["kind"] == "crypto" else f"{t['symbol']}.US")


def _check_trend(t: dict, df: pd.DataFrame) -> tuple[str, dict] | None:
    """Έξοδος τάσης: μόνο stop. Ελέγχουμε τα κεριά από τη στιγμή που ισχύει το τρέχον stop (τα παλιότερα
    ελέγχθηκαν ήδη με το stop που ίσχυε τότε)."""
    since = pd.Timestamp(t["stop_at"] or t["opened_at"])
    sign = 1 if t["side"] == "LONG" else -1
    for ts, row in df[df.index + market.TF_DELTA["15m"] > since].iterrows():
        adverse = row.low if sign > 0 else row.high
        if sign * (adverse - t["stop_loss"]) <= 0:
            px = row.open if sign * (row.open - t["stop_loss"]) < 0 else t["stop_loss"]   # gap: στο άνοιγμα
            reason = "STOP" if sign * (t["stop_loss"] - t["sl0"]) > 0 else "SL"
            return reason, close(t["id"], float(px), reason, str(ts))
    return None


def stop_steps(asset: market.Asset, tf: str, side: str, opened: pd.Timestamp, stop: float,
               trend_atr: float) -> dict | None:
    """Ο υπολογισμός του trailing stop βήμα-βήμα, από τα ΚΛΕΙΣΜΕΝΑ κεριά μετά την είσοδο:
    ακραία τιμή (LONG: υψηλότερο high), ATR του τελευταίου κεριού, επίπεδο = ακραία − trend_atr × ATR,
    νέο stop = το μεγαλύτερο από το τωρινό και το επίπεδο (ποτέ προς τα κάτω)."""
    bar = market.TF_DELTA[tf]
    df = indicators.add_all(market.fetch_ohlcv(asset, tf, adjusted=False))   # πραγματικές τιμές, όπως το stop
    now = pd.Timestamp.now(tz="UTC")
    df = df[(df.index + bar > opened) & (df.index + bar <= now)]
    if not len(df):
        return None
    k = df["high"].idxmax() if side == "LONG" else df["low"].idxmin()
    extreme = float(df.at[k, "high"] if side == "LONG" else df.at[k, "low"])
    atr = float(df["atr"].iloc[-1])
    sign = 1 if side == "LONG" else -1
    return {"extreme": extreme, "extreme_at": k, "atr": atr, "atr_at": df.index[-1], "bars": len(df),
            "level": extreme - sign * trend_atr * atr, "close": float(df["close"].iloc[-1]),
            "new": journal.trend_stop(side, stop, extreme, atr, trend_atr), "stop": stop, "k": trend_atr}


def trend_stop_now(t: dict) -> float | None:
    """Το stop που ισχύει από σήμερα για ένα trade τάσης, από τα ΚΛΕΙΣΜΕΝΑ κεριά του timeframe του."""
    st = stop_steps(_asset(t), t["timeframe"], t["side"], pd.Timestamp(t["opened_at"]), t["stop_loss"],
                    t["trend_atr"])
    return st["new"] if st else None


def update_trend_stops() -> tuple[list[tuple[str, dict]], list[tuple[dict, float, float]]]:
    """Ανεβάζει (ποτέ δεν κατεβάζει) το stop των ανοιχτών trades τάσης. Πρώτα ελέγχει όλα τα trades με τα
    stops που ίσχυαν μέχρι τώρα (check_all), ώστε να μη χαθεί χτύπημα αν το bot ήταν κλειστό.
    Επιστρέφει (γεγονότα του check_all, [(trade, παλιό stop, νέο stop)])."""
    events = check_all()
    raised = []
    for t in open_trades():
        if not t["trend_atr"]:
            continue
        try:
            new = trend_stop_now(t)
        except Exception:
            continue
        sign = 1 if t["side"] == "LONG" else -1
        if new is None or sign * (new - t["stop_loss"]) <= 0:
            continue
        if new >= 1:   # σε cents, προς τη συντηρητική πλευρά (LONG: κάτω)
            new = math.floor(new * 100) / 100 if sign > 0 else math.ceil(new * 100) / 100
            if sign * (new - t["stop_loss"]) <= 0:
                continue
        with _conn() as c:
            c.execute("UPDATE my_trades SET stop_loss=?, stop_at=? WHERE id=?",
                      (new, datetime.now(timezone.utc).isoformat(), t["id"]))
        raised.append((get(t["id"]), t["stop_loss"], new))
    return events, raised


def stats(broker: bool = False) -> dict:
    """Χειροκίνητα trades (TradingView) ή, με broker=True, τα αυτόματα του Alpaca."""
    rows = closed_trades(broker)
    out = {"open": sum(bool(t["broker"]) == broker for t in open_trades()), "closed": len(rows),
           "balance": float(load_config()["account"]["balance"])}
    if rows:
        rs = [_r_multiple(t) for t in rows]
        out.update({
            "wins": sum(1 for t in rows if t["pnl"] > 0),
            "pnl": sum(t["pnl"] for t in rows),
            "total_r": sum(rs), "avg_r": sum(rs) / len(rs),
        })
    return out
