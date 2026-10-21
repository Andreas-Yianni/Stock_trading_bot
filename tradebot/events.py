"""Οικονομικό ημερολόγιο (CPI, Fed, θέσεις εργασίας...) και ημερομηνίες αποτελεσμάτων (earnings).

Δεν προβλέπει την κατεύθυνση: προειδοποιεί πότε η τιμή μπορεί να κινηθεί απότομα, ώστε να μην
ανοίξεις θέση λίγο πριν από ένα μεγάλο γεγονός.
Πηγές: Forex Factory (δημόσιο JSON, μόνο η τρέχουσα εβδομάδα) και Yahoo Finance (earnings).
"""
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import requests
import yfinance as yf

from .cache import cached
from .config import DATA_DIR
from .market import Asset

CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (trading-analysis-bot)"}
CACHE_SECONDS = 3600
# Το Forex Factory μπλοκάρει όποιον ζητάει το αρχείο πάνω από μία φορά την ώρα. Το κρατάμε στον
# δίσκο, ώστε CLI, dashboard και Telegram bot να μοιράζονται την ίδια λήψη.
CALENDAR_FILE = DATA_DIR / "ff_calendar.json"

# Ελληνικά ονόματα για τα πιο συχνά γεγονότα των ΗΠΑ (το πρώτο που ταιριάζει κερδίζει)
GREEK_NAMES = [
    ("Core CPI", "Πληθωρισμός ΗΠΑ χωρίς ενέργεια/τρόφιμα (Core CPI)"),
    ("CPI", "Πληθωρισμός ΗΠΑ (CPI)"),
    ("Core PCE", "Πληθωρισμός PCE (ο δείκτης που κοιτάει η Fed)"),
    ("PCE", "Πληθωρισμός PCE"),
    ("PPI", "Τιμές παραγωγού ΗΠΑ (PPI)"),
    ("Non-Farm", "Θέσεις εργασίας ΗΠΑ (NFP)"),
    ("Unemployment Rate", "Ανεργία ΗΠΑ"),
    ("Unemployment Claims", "Αιτήσεις επιδομάτων ανεργίας ΗΠΑ"),
    ("Federal Funds Rate", "Απόφαση επιτοκίων της Fed"),
    ("FOMC Statement", "Ανακοίνωση της Fed (FOMC)"),
    ("FOMC Press Conference", "Συνέντευξη Τύπου της Fed"),
    ("FOMC Meeting Minutes", "Πρακτικά συνεδρίασης της Fed"),
    ("Fed Chair", "Ομιλία του προέδρου της Fed"),
    ("GDP", "ΑΕΠ ΗΠΑ"),
    ("Retail Sales", "Λιανικές πωλήσεις ΗΠΑ"),
    ("ISM Manufacturing", "Βιομηχανία ΗΠΑ (ISM)"),
    ("ISM Services", "Υπηρεσίες ΗΠΑ (ISM)"),
    ("JOLTS", "Θέσεις εργασίας προς κάλυψη (JOLTS)"),
]


@dataclass
class Event:
    title: str            # στα ελληνικά όπου γίνεται
    when: datetime        # UTC
    kind: str             # "macro" ή "earnings"
    impact: str = ""      # High / Medium (μόνο για macro)
    forecast: str = ""
    previous: str = ""
    all_day: bool = False  # earnings: ξέρουμε μόνο τη μέρα, όχι την ώρα

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.title}:{self.when:%Y-%m-%d %H:%M}"

    def hours_left(self, now: datetime | None = None) -> float:
        return (self.when - (now or datetime.now(timezone.utc))).total_seconds() / 3600

    def when_local(self) -> str:
        days = ["Δευ", "Τρι", "Τετ", "Πεμ", "Παρ", "Σαβ", "Κυρ"]
        t = self.when.astimezone()
        return f"{days[t.weekday()]} {t:%d/%m}" + ("" if self.all_day else f" {t:%H:%M}")


def _greek(title: str) -> str:
    for needle, gr in GREEK_NAMES:
        if needle.lower() in title.lower():
            return gr
    return title


def _calendar_json() -> list:
    fresh = CALENDAR_FILE.exists() and time.time() - CALENDAR_FILE.stat().st_mtime < CACHE_SECONDS
    if not fresh:
        try:
            resp = requests.get(CALENDAR_URL, headers=HEADERS, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            CALENDAR_FILE.write_text(json.dumps(data), encoding="utf-8")
            return data
        except Exception:
            # Μην ξαναδοκιμάσεις πριν περάσει μία ώρα: κράτα το παλιό αρχείο (ή ένα κενό)
            if CALENDAR_FILE.exists():
                CALENDAR_FILE.touch()
            else:
                CALENDAR_FILE.write_text("[]", encoding="utf-8")
    try:
        return json.loads(CALENDAR_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def _fetch_macro() -> list[Event]:
    data = _calendar_json()
    merged: dict[tuple, Event] = {}
    for d in data:
        if d.get("country") != "USD" or d.get("impact") != "High":
            continue
        try:
            when = pd.Timestamp(d["date"]).tz_convert("UTC").to_pydatetime()
        except Exception:
            continue
        title, fc, prev = _greek(d["title"]), d.get("forecast") or "", d.get("previous") or ""
        e = merged.get((title, when))
        if e:  # π.χ. CPI μηνιαίο + ετήσιο: ένα γεγονός με δύο αριθμούς
            e.forecast = " / ".join(x for x in (e.forecast, fc) if x)
            e.previous = " / ".join(x for x in (e.previous, prev) if x)
        else:
            merged[(title, when)] = Event(title, when, "macro", d["impact"], fc, prev)
    return sorted(merged.values(), key=lambda e: e.when)


def macro_events() -> list[Event]:
    """Γεγονότα υψηλής σημασίας των ΗΠΑ αυτής της εβδομάδας (επηρεάζουν μετοχές ΚΑΙ crypto)."""
    return cached("macro", _fetch_macro, CACHE_SECONDS)


def _fetch_earnings(symbol: str) -> Event | None:
    try:
        dates = yf.Ticker(symbol).calendar.get("Earnings Date") or []
    except Exception:
        return None
    today = date.today()
    future = sorted(d for d in dates if isinstance(d, date) and d >= today)
    if not future:
        return None
    when = datetime.combine(future[0], datetime.min.time(), tzinfo=timezone.utc)
    return Event(f"Αποτελέσματα (earnings) {symbol}", when, "earnings", all_day=True)


def earnings(asset: Asset) -> Event | None:
    if asset.kind != "stock":
        return None
    return cached(f"earnings:{asset.symbol}", lambda: _fetch_earnings(asset.symbol), 6 * 3600)


def upcoming(asset: Asset, days: int = 7) -> list[Event]:
    """Ό,τι σημαντικό έρχεται τις επόμενες μέρες για αυτό το σύμβολο."""
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=days)
    evs = [e for e in macro_events() if now - timedelta(hours=1) <= e.when <= horizon]
    er = earnings(asset)
    if er and er.when.date() <= horizon.date():
        evs.append(er)
    return sorted(evs, key=lambda e: e.when)


def risk_warnings(events: list[Event]) -> list[str]:
    """Προειδοποιήσεις για γεγονότα που είναι πολύ κοντά."""
    out = []
    soon: dict[datetime, list[Event]] = {}
    for e in events:
        if e.kind == "macro" and -1 <= e.hours_left() <= 24:
            soon.setdefault(e.when, []).append(e)
    for when, group in sorted(soon.items()):
        h = group[0].hours_left()
        left = "τώρα" if h < 0.5 else f"σε {h:.0f} ώρες" if h >= 1.5 else "σε περίπου 1 ώρα"
        names = " + ".join(e.title for e in group)
        out.append(f"📅 {names} {left} ({group[0].when_local()}). Η τιμή μπορεί να κινηθεί απότομα: "
                   f"καλύτερα να μην ανοίξεις νέα θέση λίγο πριν.")
    for e in events:
        h = e.hours_left()
        if e.kind == "earnings" and h <= 72:
            out.append(f"📅 {e.title} την {e.when_local()}. Οι μετοχές συχνά κάνουν μεγάλο «άλμα» μετά τα "
                       f"αποτελέσματα, που μπορεί να περάσει πάνω από το stop loss σου.")
    return out
