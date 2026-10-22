"""Γνώμη AI (Google Gemini, δωρεάν επίπεδο) πάνω στην ανάλυση των κανόνων.

Το AI βλέπει ΑΚΡΙΒΩΣ ό,τι και η ανάλυση: τους τίτλους των ειδήσεων του συμβόλου και τις γενικές ειδήσεις
αγοράς (αριθμημένες), τους δείκτες/factors, τα επίπεδα, το πλάνο και τα επερχόμενα γεγονότα. Επιστρέφει JSON:
σκορ -1..+1, βεβαιότητα, σύνοψη, τι θα έκανε σε σχέση με το σήμα των κανόνων, και για κάθε είδηση που
έπαιξε ρόλο την επίδρασή της και το γιατί. Όλα φαίνονται στον χρήστη (report, /ai, dashboard).

- Είναι ΓΝΩΜΗ, όχι εντολή: γίνεται Factor «AI (Gemini)» με βάρος `ai.weight` (προεπιλογή 0 = μόνο
  παρατήρηση, δεν αλλάζει σκορ/σήματα). Δεν μπορεί να ελεγχθεί με backtest, άρα πρώτα μετράμε: κάθε νέα
  γνώμη γράφεται στο data/ai_log.jsonl (σύμβολο, τιμή, σκορ κανόνων, σκορ AI) για σύγκριση με ό,τι έγινε μετά.
- Τα αυτόματα trades του Alpaca (autoscan) ΔΕΝ περνούν από εδώ.
- Δωρεάν όριο: cache ανά σύμβολο + ίδιους τίτλους για `cache_hours`, και τουλάχιστον MIN_GAP δευτερόλεπτα
  ανάμεσα σε κλήσεις (όριο ανά λεπτό). Χωρίς GEMINI_API_KEY ή με ai.enabled: false, δεν κάνει τίποτα.
"""
import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

import requests

from .config import DATA_DIR, load_config

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
# Μοντέλα με σειρά προτίμησης: αν το πρώτο είναι υπερφορτωμένο (5xx), αργεί ή εξάντλησε το δωρεάν όριο (429),
# ρωτάμε το επόμενο (κάθε μοντέλο έχει δικό του όριο). Δοκιμή 22/10/2026: 3.5-flash ~12 s με καλύτερη ανάλυση,
# flash-lite-latest ~2 s· το gemini-flash-latest έδινε 503/timeout στο δωρεάν επίπεδο.
DEFAULTS = {"enabled": True, "models": ["gemini-3.5-flash", "gemini-flash-lite-latest"], "weight": 0,
            "cache_hours": 12}
TIMEOUT = 45           # δευτερόλεπτα ανά μοντέλο
CACHE_FILE = DATA_DIR / "ai_cache.json"
LOG_FILE = DATA_DIR / "ai_log.jsonl"
MAX_NEWS = 15          # ειδήσεις συμβόλου που στέλνονται (όσες έχει η ανάλυση)
MAX_MARKET = 8         # γενικές ειδήσεις αγοράς
MIN_GAP = 7.0          # δευτερόλεπτα ανάμεσα σε κλήσεις (δωρεάν όριο ~10 ανά λεπτό)

SYSTEM = """Είσαι προσεκτικός αναλυτής μετοχών και crypto για έναν αρχάριο trader που κάνει paper trading.
Σου δίνονται τα αποτελέσματα ενός bot με τεχνικούς κανόνες και αριθμημένοι τίτλοι ειδήσεων.
Κανόνες:
- Βασίσου ΜΟΝΟ στα δεδομένα που σου δίνονται. Μην επινοείς γεγονότα, αριθμούς ή ειδήσεις.
- Ορίζοντας: οι επόμενες 1-4 εβδομάδες (ημερήσια κεριά).
- score: -1 (πολύ αρνητικό) έως +1 (πολύ θετικό) για την τιμή στον ορίζοντα αυτό. Αν οι ειδήσεις είναι
  λίγες, άσχετες ή αντιφατικές, μείνε κοντά στο 0 με confidence "low".
- news: μόνο οι ειδήσεις που πραγματικά επηρεάζουν την κρίση σου (έως 6), με τον αριθμό τους, την
  επίδραση ("+", "-" ή "0") και σύντομη εξήγηση. Οι γενικές ειδήσεις αγοράς μετράνε μόνο αν αφορούν το σύμβολο.
- action: μία πρόταση για το τι θα έκανες σε σχέση με το σήμα των κανόνων (π.χ. συμφωνώ, θα περίμενα,
  θα απέφευγα), χωρίς να υπόσχεσαι κέρδη. Οι κανόνες παίρνουν μόνο LONG (αγορά) στις μετοχές.
- Γράψε summary, action, why και risks στα ελληνικά, απλά και σύντομα."""

SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "score": {"type": "NUMBER"},
        "confidence": {"type": "STRING", "enum": ["low", "medium", "high"]},
        "summary": {"type": "STRING"},
        "action": {"type": "STRING"},
        "agrees_with_rules": {"type": "BOOLEAN"},
        "news": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "n": {"type": "INTEGER"},
            "effect": {"type": "STRING", "enum": ["+", "-", "0"]},
            "why": {"type": "STRING"}},
            "required": ["n", "effect", "why"]}},
        "risks": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["score", "confidence", "summary", "action", "agrees_with_rules", "news", "risks"],
}

CONFIDENCE_GR = {"low": "Χαμηλή", "medium": "Μέτρια", "high": "Υψηλή"}

_lock = threading.Lock()
_last_call = 0.0


class AIError(Exception):
    pass


class _Retry(AIError):
    """Προσωρινό πρόβλημα του μοντέλου: δοκιμάζουμε το επόμενο της λίστας."""


@dataclass
class Seen:
    """Μια είδηση όπως τη «είδε» το AI (αριθμός = αυτός στο prompt)."""
    n: int
    title: str
    link: str
    source: str
    market: bool                  # True = γενική είδηση αγοράς
    effect: str | None = None     # "+", "-", "0" ή None αν το AI δεν τη θεώρησε σημαντική
    why: str = ""


@dataclass
class Opinion:
    score: float
    confidence: str
    summary: str
    action: str
    agrees_with_rules: bool
    risks: list[str]
    seen: list[Seen]
    model: str
    created_at: str
    cached: bool = False
    rules_score: float = 0.0
    rules_bias: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def stance(self) -> str:
        return "θετική" if self.score >= 0.2 else "αρνητική" if self.score <= -0.2 else "ουδέτερη"

    @property
    def cited(self) -> list[Seen]:
        return [s for s in self.seen if s.effect is not None]


def settings() -> dict:
    return {**DEFAULTS, **(load_config().get("ai") or {})}


def api_key() -> str:
    return os.getenv("GEMINI_API_KEY", "").strip()


def configured() -> bool:
    return bool(api_key()) and bool(settings()["enabled"])


def _seen_items(a) -> list[Seen]:
    out = [Seen(i + 1, n.title, n.link, n.source, False) for i, n in enumerate(a.news[:MAX_NEWS])]
    k = len(out)
    out += [Seen(k + i + 1, n.title, n.link, n.source, True) for i, n in enumerate(a.market_news[:MAX_MARKET])]
    return out


def build_prompt(a, seen: list[Seen]) -> str:
    """Τα δεδομένα της ανάλυσης σε κείμενο για το AI (χωρίς κλειδιά ή προσωπικά στοιχεία)."""
    last = a.df.iloc[-1]
    lines = [
        f"Σύμβολο: {a.asset.symbol} ({'crypto' if a.asset.kind == 'crypto' else 'μετοχή ΗΠΑ'}), timeframe {a.timeframe}",
        f"Τιμή: {a.price:.4g}",
        f"Δείκτες: RSI {last.rsi:.0f}, ADX {last.adx:.0f}, EMA20 {last.ema20:.4g}, EMA50 {last.ema50:.4g}, "
        f"EMA200 {last.ema200:.4g}, ATR {last.atr:.4g}",
        f"Απόδοση: 5 κεριά {_ret(a.df, 5)}, 20 κεριά {_ret(a.df, 20)}, 60 κεριά {_ret(a.df, 60)}",
        f"Σκορ κανόνων: {a.score:+.0f}/100, σήμα: {a.bias}",
        "Factors των κανόνων:",
    ]
    lines += [f"- {f.name}: {f.note}" for f in a.factors if not f.name.startswith("AI")]
    lv = a.levels
    lines.append("Αντιστάσεις: " + (", ".join(f"{p:.4g}" for p, _ in lv["resistance"]) or "-"))
    lines.append("Στηρίξεις: " + (", ".join(f"{p:.4g}" for p, _ in lv["support"]) or "-"))
    if a.plan:
        lines.append(f"Πλάνο κανόνων: {a.plan.side} είσοδος {a.plan.entry:.4g}, stop {a.plan.stop_loss:.4g}")
    for ev in a.events:
        lines.append(f"Επερχόμενο γεγονός: {ev.title} ({ev.when:%d/%m %H:%M} UTC)")
    lines.append("")
    lines.append("Ειδήσεις συμβόλου:" if any(not s.market for s in seen) else "Ειδήσεις συμβόλου: καμία")
    lines += [f"[{s.n}] {s.title} ({s.source})" for s in seen if not s.market]
    if any(s.market for s in seen):
        lines.append("Γενικές ειδήσεις αγοράς:")
        lines += [f"[{s.n}] {s.title} ({s.source})" for s in seen if s.market]
    return "\n".join(lines)


def _ret(df, n: int) -> str:
    if len(df) <= n:
        return "-"
    return f"{(df['close'].iloc[-1] / df['close'].iloc[-1 - n] - 1) * 100:+.1f}%"


def _call(prompt: str, model: str) -> dict:
    """Μία κλήση στο Gemini με structured output (JSON). Σέβεται το MIN_GAP ανάμεσα σε κλήσεις."""
    global _last_call
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json",
                             "responseSchema": SCHEMA},
    }
    with _lock:
        wait = MIN_GAP - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        try:
            r = requests.post(API.format(model=model), json=body, timeout=TIMEOUT,
                              headers={"x-goog-api-key": api_key(), "Content-Type": "application/json"})
        except (requests.Timeout, requests.ConnectionError) as e:
            raise _Retry(f"δεν απάντησε ({e.__class__.__name__})") from e
        finally:
            _last_call = time.monotonic()
    if r.status_code == 429:
        raise _Retry("όριο δωρεάν χρήσης (429)")
    if r.status_code >= 500:
        raise _Retry(f"υπερφορτωμένο ({r.status_code})")
    if r.status_code >= 400:
        try:
            msg = r.json().get("error", {}).get("message", r.text)
        except Exception:
            msg = r.text
        raise AIError(f"Gemini {r.status_code}: {msg[:200]}")
    try:
        parts = r.json()["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
        return json.loads(text)
    except (KeyError, IndexError, ValueError) as e:
        raise AIError(f"μη έγκυρη απάντηση από το Gemini ({e.__class__.__name__})") from e


def models() -> list[str]:
    st = settings()
    out = list(st["models"])
    if st.get("model"):                      # παλιό/απλό config: ένα μοντέλο, μπαίνει πρώτο
        out = [st["model"]] + [m for m in out if m != st["model"]]
    return out


def _ask(prompt: str) -> tuple[dict, str]:
    """Ρωτάει τα μοντέλα με τη σειρά μέχρι να απαντήσει ένα. Επιστρέφει (απάντηση, μοντέλο)."""
    problems = []
    for model in models():
        try:
            return _call(prompt, model), model
        except _Retry as e:
            problems.append(f"{model}: {e}")
    raise AIError("κανένα μοντέλο Gemini δεν απάντησε (" + "; ".join(problems) + ")")


def parse(raw: dict, seen: list[Seen], model: str) -> Opinion:
    """Ελέγχει/καθαρίζει την απάντηση: σκορ στο [-1, 1], μόνο υπαρκτοί αριθμοί ειδήσεων."""
    try:
        score = max(-1.0, min(1.0, float(raw.get("score", 0))))
    except (TypeError, ValueError):
        score = 0.0
    by_n = {s.n: s for s in seen}
    for item in raw.get("news") or []:
        try:
            s = by_n.get(int(item.get("n")))
        except (TypeError, ValueError):
            continue
        if s and item.get("effect") in ("+", "-", "0"):
            s.effect, s.why = item["effect"], str(item.get("why", ""))[:200]
    conf = raw.get("confidence") if raw.get("confidence") in CONFIDENCE_GR else "low"
    return Opinion(score=score, confidence=conf, summary=str(raw.get("summary", ""))[:600],
                   action=str(raw.get("action", ""))[:300], agrees_with_rules=bool(raw.get("agrees_with_rules")),
                   risks=[str(x)[:200] for x in (raw.get("risks") or [])][:3], seen=seen, model=model,
                   created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))


def _key(a, seen: list[Seen], model: str) -> str:
    titles = "|".join(s.title for s in seen)
    return f"{a.asset.symbol}|{a.timeframe}|{model}|{hashlib.sha1(titles.encode()).hexdigest()[:12]}"


def _load_cache() -> dict:
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _from_dict(d: dict) -> Opinion:
    d = dict(d)
    d["seen"] = [Seen(**s) for s in d.get("seen", [])]
    return Opinion(**d)


def opinion(a) -> Opinion | None:
    """Η γνώμη του AI για την ανάλυση `a` (από cache αν οι ίδιες ειδήσεις ρωτήθηκαν πρόσφατα).
    None αν το AI δεν είναι ρυθμισμένο. AIError αν η κλήση αποτύχει."""
    if not configured():
        return None
    st = settings()
    seen = _seen_items(a)
    key = _key(a, seen, "+".join(models()))
    cache = _load_cache()
    hit = cache.get(key)
    if hit and time.time() - hit["t"] < float(st["cache_hours"]) * 3600:
        op = _from_dict(hit["op"])
        op.cached = True
        return op
    raw, model = _ask(build_prompt(a, seen))
    op = parse(raw, seen, model)
    op.rules_score, op.rules_bias = round(a.score, 1), a.bias
    now = time.time()
    cache = {k: v for k, v in cache.items() if now - v["t"] < 7 * 86400}
    cache[key] = {"t": now, "op": asdict(op)}
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": op.created_at, "symbol": a.asset.symbol, "tf": a.timeframe, "price": a.price,
                            "rules_score": op.rules_score, "bias": a.bias, "ai_score": op.score,
                            "confidence": op.confidence, "agrees": op.agrees_with_rules, "model": model,
                            "news": len([s for s in seen if not s.market])}, ensure_ascii=False) + "\n")
    return op


def factor_note(op: Opinion, weight: float) -> str:
    note = f"Γνώμη {op.stance} ({op.score:+.2f}, βεβαιότητα {CONFIDENCE_GR[op.confidence].lower()}): {op.summary}"
    if not weight:
        note += " [μόνο παρατήρηση: δεν μετράει στο σκορ]"
    return note
