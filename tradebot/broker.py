"""Αυτόματο PAPER trading στο Alpaca (alpaca.markets). ΜΟΝΟ paper: η διεύθυνση είναι σταθερή στο
paper-api, και το bot αρνείται να δουλέψει αν το κλειδί ανήκει σε πραγματικό λογαριασμό.

Ροή (ίδιοι κανόνες με τα χειροκίνητα trades και το backtest):
- Πρωινή σάρωση (autoscan.py): για κάθε ΝΕΟ σήμα LONG στις ~100 μεγαλύτερες μετοχές, εντολή Market
  (εκτελείται στο άνοιγμα) με συνδεδεμένο Stop (OTO). Ποσότητα από το ρίσκο του broker (προεπιλογή 0.5%
  του λογαριασμού Alpaca, έως 10% ανά θέση). Καταγράφεται στο my_trades (broker='alpaca') και το σήμα στο
  journal, οπότε /trades, /stop, το ανέβασμα του stop και ο έλεγχος εκτέλεσης δουλεύουν όπως πάντα.
- Κάθε 10 λεπτά `sync()`: τιμή εκτέλεσης, έλεγχος ότι υπάρχει stop, κλείσιμο όταν εκτελεστεί το stop.
  Αν η μετοχή άνοιξε κάτω από το stop, η θέση κλείνει αμέσως (όπως ο κανόνας «μην μπεις»).
- Πρωί: όταν ανεβαίνει το trailing stop, αλλάζει και η εντολή stop στο Alpaca (`set_stop`).
- `health()`: δικλείδα για ΛΑΘΗ (όχι για τη στρατηγική): πτώση λογαριασμού > max_day_loss_pct σε μία μέρα,
  θέση SHORT, θέση πολύ μεγαλύτερη από το όριο, πάρα πολλές αγορές σε μία μέρα → παύση των αυτόματων trades.
- `compare()`: το ίδιο σήμα στο backtest έναντι της πραγματικής εκτέλεσης (σε R του plan).

Όρια (config.yaml → broker): auto_trade (διακόπτης, /auto on|off), risk_per_trade_pct, max_position_pct,
max_positions, max_new_per_day, max_order_usd, max_day_loss_pct, universe. Ποτέ margin (μόνο μετρητά),
ποτέ δεύτερη θέση στο ίδιο σύμβολο, ποτέ θέση χωρίς stop.
"""
import json
import os
from datetime import datetime, timezone

import requests

from . import journal, portfolio
from .config import DATA_DIR, load_config, save_config

BASE = "https://paper-api.alpaca.markets/v2"   # σταθερό: ΠΟΤΕ το live api.alpaca.markets
DEFAULTS = {"auto_trade": False, "universe": "top100", "risk_per_trade_pct": 0.5, "max_position_pct": 10,
            "max_positions": 10, "max_new_per_day": 5, "max_order_usd": 25000, "max_day_loss_pct": 5}
EQUITY_FILE = DATA_DIR / "broker_equity.json"   # κεφάλαιο Alpaca ανά μέρα (για τη σύγκριση με το SPY)
STATE_FILE = DATA_DIR / "broker_state.json"     # γιατί έγινε παύση, ποιες ειδοποιήσεις στάλθηκαν σήμερα


class BrokerError(Exception):
    pass


def keys() -> tuple[str, str]:
    return os.getenv("ALPACA_API_KEY", "").strip(), os.getenv("ALPACA_SECRET_KEY", "").strip()


def configured() -> bool:
    return all(keys())


def settings() -> dict:
    return {**DEFAULTS, **(load_config().get("broker") or {})}


def enabled() -> bool:
    return configured() and bool(settings()["auto_trade"])


def set_enabled(on: bool) -> None:
    cfg = load_config()
    cfg["broker"] = {**DEFAULTS, **(cfg.get("broker") or {}), "auto_trade": bool(on)}
    save_config(cfg)
    if on:
        _set_state(paused=None)


def _req(method: str, path: str, **kw):
    k, s = keys()
    if not (k and s):
        raise BrokerError("Λείπουν τα ALPACA_API_KEY / ALPACA_SECRET_KEY στο .env")
    r = requests.request(method, BASE + path, headers={"APCA-API-KEY-ID": k, "APCA-API-SECRET-KEY": s},
                         timeout=20, **kw)
    if r.status_code >= 400:
        try:
            msg = r.json().get("message", r.text)
        except Exception:
            msg = r.text
        raise BrokerError(f"Alpaca {r.status_code}: {msg}")
    return r.json() if r.content else None


def account() -> dict:
    a = _req("GET", "/account")
    # Τα κλειδιά paper ξεκινούν από "PK" και ο λογαριασμός από "PA". Οτιδήποτε άλλο: σταματάμε.
    if not str(a.get("account_number", "")).startswith("PA") or not keys()[0].startswith("PK"):
        raise BrokerError("Αυτό δεν μοιάζει με λογαριασμό PAPER του Alpaca: δεν κάνω τίποτα.")
    return a


def positions() -> list[dict]:
    return _req("GET", "/positions")


def open_orders() -> list[dict]:
    return _req("GET", "/orders", params={"status": "open", "nested": "true", "limit": 500})


# Εντολές που ακόμα «δουλεύουν» (και όσες αλλάζουν τώρα: αλλιώς θα βάζαμε δεύτερο stop)
ACTIVE = ("new", "accepted", "held", "pending_new", "partially_filled", "pending_replace",
          "accepted_for_bidding", "calculated")


def _stop_orders(symbol: str) -> list[dict]:
    out = []
    for o in open_orders():
        for x in [o] + (o.get("legs") or []):
            if x["symbol"] == symbol and x["side"] == "sell" and x["type"] in ("stop", "stop_limit") \
                    and x["status"] in ACTIVE:
                out.append(x)
    return out


# ---------- Κατάσταση (παύση, ειδοποιήσεις) ----------

def _state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _set_state(**kw) -> None:
    st = {**_state(), **kw}
    STATE_FILE.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")


def pause(reason: str) -> None:
    """Σταματάει τα ΝΕΑ αυτόματα trades (οι ανοιχτές θέσεις κρατάνε το stop τους και συνεχίζουν)."""
    cfg = load_config()
    cfg["broker"] = {**DEFAULTS, **(cfg.get("broker") or {}), "auto_trade": False}
    save_config(cfg)
    _set_state(paused={"at": datetime.now(timezone.utc).isoformat(), "reason": reason})


def paused() -> dict | None:
    return _state().get("paused")


def once_today(key: str) -> bool:
    """True την πρώτη φορά σήμερα για αυτή την ειδοποίηση (να μη στέλνεται κάθε 10 λεπτά)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    sent = _state().get("alerts", {})
    if sent.get(key) == today:
        return False
    _set_state(alerts={**{k: v for k, v in sent.items() if v == today}, key: today})
    return True


def _today_buys() -> int:
    """Πόσες εντολές αγοράς έστειλε το bot σήμερα (από το Alpaca, όχι από τη δική μας βάση)."""
    start = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    orders = _req("GET", "/orders", params={"status": "all", "after": start, "limit": 500})
    return sum(1 for o in orders if o["side"] == "buy" and str(o.get("client_order_id", "")).startswith("tb-")
               and not str(o.get("client_order_id", "")).startswith("tb-test-"))


def health(acc: dict | None = None, pos: list | None = None) -> list[tuple[str, str]]:
    """Δικλείδα για λάθη. [("pause", μήνυμα)] σταματάει τα αυτόματα trades, [("warn", μήνυμα)] μόνο ειδοποίηση."""
    st = settings()
    acc = acc or account()
    pos = positions() if pos is None else pos
    out = []
    eq, last = float(acc["equity"]), float(acc.get("last_equity") or acc["equity"])
    if last > 0 and eq < last * (1 - float(st["max_day_loss_pct"]) / 100):
        out.append(("pause", f"ο λογαριασμός έπεσε {100 * (1 - eq / last):.1f}% σήμερα "
                             f"(όριο {st['max_day_loss_pct']}%)"))
    if acc.get("trading_blocked") or acc.get("account_blocked"):
        out.append(("pause", "ο λογαριασμός Alpaca είναι μπλοκαρισμένος"))
    tracked = {t["symbol"] for t in portfolio.open_trades() if t["broker"]}
    cap = eq * float(st["max_position_pct"]) / 100
    for x in pos:
        sym, qty, cost = x["symbol"], float(x["qty"]), abs(float(x.get("cost_basis") or 0))
        if qty < 0:
            out.append(("pause", f"θέση SHORT στο {sym}: δεν έπρεπε να υπάρχει"))
        if cost > 1.5 * cap + 1:
            out.append(("pause", f"η θέση {sym} κόστισε {cost:,.0f}$, πάνω από 1.5× το όριο ({cap:,.0f}$)"))
        if sym not in tracked:
            out.append(("warn", f"θέση {sym} στο Alpaca που δεν την άνοιξε το bot (δεν την αγγίζω)"))
    n = _today_buys()
    if n > int(st["max_new_per_day"]):
        out.append(("pause", f"{n} εντολές αγοράς σήμερα (όριο {st['max_new_per_day']})"))
    return out


# ---------- Κεφάλαιο ανά μέρα ----------

def snapshot(acc: dict | None = None) -> dict:
    """Γράφει το κεφάλαιο Alpaca της ημέρας (μία τιμή ανά μέρα) στο data/broker_equity.json."""
    acc = acc or account()
    hist = equity_history()
    hist[datetime.now(timezone.utc).strftime("%Y-%m-%d")] = float(acc["equity"])
    EQUITY_FILE.write_text(json.dumps(dict(sorted(hist.items()))), encoding="utf-8")
    return hist


def equity_history() -> dict[str, float]:
    try:
        return json.loads(EQUITY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------- Άνοιγμα ----------

def open_from_analysis(a) -> dict | None:
    """Ανοίγει θέση για νέο σήμα LONG μετοχής. Επιστρέφει το trade (my_trades), ή {"skip": λόγος}, ή None."""
    p = a.plan
    if not p or p.side != "LONG" or a.asset.kind != "stock":
        return None
    st = settings()
    acc = account()
    if acc.get("trading_blocked") or acc.get("account_blocked"):
        raise BrokerError("Ο λογαριασμός Alpaca είναι μπλοκαρισμένος")
    sym = a.asset.symbol
    pos = positions()
    if any(x["symbol"] == sym for x in pos) or any(o["symbol"] == sym for o in open_orders()) \
            or any(t["symbol"] == sym and t["broker"] for t in portfolio.open_trades()):
        return {"skip": "υπάρχει ήδη θέση/εντολή"}
    n_open = len({x["symbol"] for x in pos} | {t["symbol"] for t in portfolio.open_trades() if t["broker"]})
    if n_open >= int(st["max_positions"]):
        return {"skip": f"ήδη {n_open} θέσεις (όριο {st['max_positions']})"}

    equity, cash = float(acc["equity"]), float(acc["cash"])
    # Μετρητά που έχουν ήδη «κλειδώσει» εντολές αγοράς που δεν έχουν εκτελεστεί (π.χ. σήμερα το πρωί)
    pending = sum(t["entry"] * portfolio.remaining_qty(t) for t in portfolio.open_trades()
                  if t["broker"] and not t["filled"])
    free = max(cash - pending, 0)
    qty = equity * float(st["risk_per_trade_pct"]) / 100 / p.risk_per_unit
    value_cap = min(equity * float(st["max_position_pct"]) / 100, float(st["max_order_usd"]), free)
    qty = int(min(qty, value_cap / p.entry))
    if qty < 1:
        return {"skip": f"δεν φτάνουν τα μετρητά (ελεύθερα {free:,.0f}$)"}

    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    body = {"symbol": sym, "qty": str(qty), "side": "buy", "type": "market", "time_in_force": "gtc",
            "order_class": "oto", "stop_loss": {"stop_price": f"{p.stop_loss:.2f}"},
            "client_order_id": f"tb-{sym}-{day}"}   # το Alpaca απορρίπτει διπλή εντολή με το ίδιο id
    try:
        order = _req("POST", "/orders", json=body)
    except BrokerError as e:
        if "client_order_id" in str(e):
            return {"skip": "έχει ήδη σταλεί εντολή σήμερα"}
        try:
            # Αν δεν δεχτεί gtc: day (το stop λήγει το βράδυ, το sync() το ξαναβάζει ως gtc)
            order = _req("POST", "/orders", json={**body, "time_in_force": "day"})
        except BrokerError as e2:
            if "client_order_id" in str(e2):
                return {"skip": "έχει ήδη σταλεί εντολή σήμερα"}
            raise BrokerError(f"{e} · και με day: {e2}")
    tid = portfolio.open_trade(a.asset, a.timeframe, p, a.score, qty=qty, broker="alpaca", broker_order=order["id"])
    return portfolio.get(tid)


# ---------- Παρακολούθηση ----------

def _order(oid: str) -> dict:
    return _req("GET", f"/orders/{oid}", params={"nested": "true"})


def sync() -> list[tuple[str, dict]]:
    """Ενημερώνει τα αυτόματα trades από το Alpaca. Επιστρέφει γεγονότα για το Telegram:
    ("FILLED", t), ("CANCELLED", t), ("SL"/"STOP"/"GAP", t κλεισμένο, με t["cmp"] = σύγκριση με το backtest)."""
    events = []
    trades = [t for t in portfolio.open_trades() if t["broker"] == "alpaca"]
    if not trades:
        return events
    held = {x["symbol"]: x for x in positions()}
    for t in trades:
        parent = _order(t["broker_order"])
        if not t["filled"]:
            if parent["status"] in ("canceled", "expired", "rejected"):
                portfolio.cancel(t["id"])
                events.append(("CANCELLED", {**t, "why": parent["status"]}))
                continue
            if parent["status"] != "filled":
                continue
            fill = float(parent["filled_avg_price"])
            portfolio.set_fill(t["id"], fill, int(float(parent["filled_qty"])))
            t = portfolio.get(t["id"])
            events.append(("FILLED", {**t, "cmp": _safe_compare(t)}))
            if fill <= t["stop_loss"]:
                # Άνοιξε κάτω από το stop: κλείνουμε αμέσως (στο backtest αυτό το trade παραλείπεται)
                for o in _stop_orders(t["symbol"]):
                    _req("DELETE", f"/orders/{o['id']}")
                _req("DELETE", f"/positions/{t['symbol']}")
                closed = portfolio.close(t["id"], fill, "GAP")
                events.append(("GAP", closed))
                continue
        if t["symbol"] in held:
            if not _stop_orders(t["symbol"]):   # ποτέ θέση χωρίς stop (π.χ. αν έληξε ή το ακύρωσες)
                _req("POST", "/orders", json={"symbol": t["symbol"], "qty": str(int(float(held[t["symbol"]]["qty"]))),
                                              "side": "sell", "type": "stop", "time_in_force": "gtc",
                                              "stop_price": f"{t['stop_loss']:.2f}"})
            continue
        # Η θέση δεν υπάρχει πια: εκτελέστηκε το stop (ή την έκλεισες στο Alpaca)
        sells = [o for o in _req("GET", "/orders", params={"status": "closed", "symbols": t["symbol"],
                                                            "limit": 50, "direction": "desc"})
                 if o["side"] == "sell" and o["status"] == "filled"]
        px = float(sells[0]["filled_avg_price"]) if sells else t["stop_loss"]
        when = sells[0]["filled_at"] if sells else None
        reason = "STOP" if t["stop_loss"] > t["sl0"] else "SL"
        closed = portfolio.close(t["id"], px, reason, when)
        events.append((reason, {**closed, "cmp": _safe_compare(closed)}))
    return events


def set_stop(t: dict, new_stop: float) -> None:
    """Ανεβάζει την εντολή stop στο Alpaca (αλλαγή της υπάρχουσας, αλλιώς νέα)."""
    orders = _stop_orders(t["symbol"])
    for o in orders:
        try:
            _req("PATCH", f"/orders/{o['id']}", json={"stop_price": f"{new_stop:.2f}"})
            return
        except BrokerError:
            _req("DELETE", f"/orders/{o['id']}")
    _req("POST", "/orders", json={"symbol": t["symbol"], "qty": str(int(portfolio.remaining_qty(t))),
                                  "side": "sell", "type": "stop", "time_in_force": "gtc",
                                  "stop_price": f"{new_stop:.2f}"})


def close_position(t: dict) -> float:
    """Κλείνει τη θέση στο Alpaca (ακυρώνει και το stop). Επιστρέφει την τιμή εκτέλεσης (περίπου)."""
    for o in _stop_orders(t["symbol"]):
        _req("DELETE", f"/orders/{o['id']}")
    held = {x["symbol"]: x for x in positions()}
    if t["symbol"] not in held:
        raise BrokerError(f"Δεν υπάρχει θέση {t['symbol']} στο Alpaca")
    px = float(held[t["symbol"]]["current_price"])
    _req("DELETE", f"/positions/{t['symbol']}")
    return px


# ---------- Έλεγχος εκτέλεσης: πραγματικό trade έναντι backtest του ίδιου σήματος ----------

def compare(t: dict) -> dict | None:
    """Το ίδιο σήμα όπως θα το έπαιζε το backtest (journal.replay) έναντι του τι έγινε στο Alpaca.
    Όλα σε R του plan (1R = είσοδος plan − αρχικό SL). Θετικό fill_diff_r = πλήρωσες ακριβότερα."""
    if not t.get("signal_id"):
        return None
    s = journal.get(t["signal_id"])
    if not s or not s["trend_atr"]:
        return None
    rp = journal.replay(s)
    if not rp:
        return None
    unit = abs((t["plan_entry"] or t["entry"]) - (t["sl0"] or t["stop_loss"]))
    out = {"bt_fill": rp["fill"], "bt_status": rp["status"], "bt_r": rp["r"], "bt_exit": rp["exit"],
           "bt_exit_at": rp["exit_at"], "bt_stop": rp["stop_now"], "live_fill": t["entry"] if t["filled"] else None,
           "fill_diff_r": (t["entry"] - rp["fill"]) / unit if t["filled"] and unit else None,
           "live_r": None, "diff_r": None}
    if t["status"] == "CLOSED":
        out["live_r"] = portfolio.plan_r(t)
        if rp["r"] is not None and out["live_r"] is not None:
            out["diff_r"] = out["live_r"] - rp["r"]
    return out


def _safe_compare(t: dict) -> dict | None:
    try:
        return compare(t)
    except Exception:
        return None


def execution_summary() -> dict:
    """Σύνοψη για όλα τα κλειστά αυτόματα trades με γνωστό σήμα: πόσο απέχει η πράξη από το backtest."""
    rows = []
    for t in portfolio.closed_trades(broker=True):
        c = _safe_compare(t)
        if c:   # και όσα διαφωνούν στην έκβαση (στο backtest ακόμα ανοιχτά ή χωρίς είσοδο): αυτά μας ενδιαφέρουν
            rows.append({"id": t["id"], "symbol": t["symbol"], **c})
    diffs = [r["diff_r"] for r in rows if r["diff_r"] is not None]
    fills = [r["fill_diff_r"] for r in rows if r["fill_diff_r"] is not None]
    return {"n": len(rows), "rows": rows, "avg_diff": sum(diffs) / len(diffs) if diffs else None,
            "worst": max(diffs, key=abs) if diffs else None,
            "avg_fill": sum(fills) / len(fills) if fills else None,
            "outcome_mismatch": sum(1 for r in rows if r["bt_status"] in (None, "SKIP"))}


def status() -> dict:
    acc = account()
    snapshot(acc)
    return {"equity": float(acc["equity"]), "cash": float(acc["cash"]), "last_equity": float(acc.get("last_equity") or 0),
            "positions": positions(), "enabled": enabled(), "settings": settings(), "paused": paused()}
