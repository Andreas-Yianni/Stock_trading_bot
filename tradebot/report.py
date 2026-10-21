"""Μετατροπή μιας ανάλυσης σε κείμενο (HTML για Telegram, απλό για terminal)."""
import html
import re
from datetime import datetime

from . import events as ev
from . import portfolio
from .backtest import stats
from .config import DISCLAIMER, load_config
from .engine import Analysis

BIAS_TEXT = {
    "LONG": "🟢 ΑΝΟΔΙΚΗ τάση: πιθανό LONG (αγορά)",
    "SHORT": "🔴 ΚΑΘΟΔΙΚΗ τάση: πιθανό SHORT / μην αγοράσεις",
    "NEUTRAL": "⚪ ΟΥΔΕΤΕΡΟ: δεν υπάρχει καθαρό σήμα, καλύτερα αναμονή",
}


def bias_text(a: Analysis) -> str:
    """Επικεφαλίδα σήματος. Αν το σκορ είναι καθοδικό αλλά τα SHORT είναι κλειστά (long_only), το λέμε καθαρά."""
    if a.bias == "NEUTRAL" and a.score <= -load_config()["analysis"]["min_score_for_signal"]:
        return "🔴 ΚΑΘΟΔΙΚΗ τάση: μην αγοράσεις τώρα (δεν παίρνουμε SHORT σε μετοχές)"
    return BIAS_TEXT[a.bias]


def fmt_price(x: float) -> str:
    return f"{x:,.2f}" if x >= 1 else f"{x:.6g}"


def events_lines(events: list, warn: bool = True) -> list[str]:
    """Επερχόμενα γεγονότα + έντονη προειδοποίηση για όσα είναι πολύ κοντά."""
    if not events:
        return []
    e = html.escape
    lines = ["", "<b>📅 Επερχόμενα γεγονότα</b>"]
    for w in (ev.risk_warnings(events) if warn else []):
        lines.append(f"⚠️ <b>{e(w.removeprefix('📅 '))}</b>")
    for x in events[:6]:
        nums = ""
        if x.forecast or x.previous:
            nums = f" · πρόβλεψη {e(x.forecast or '-')}, προηγούμενο {e(x.previous or '-')}"
        lines.append(f"• {x.when_local()}: {e(x.title)}{nums}")
    return lines


def market_news_lines(items: list, n: int) -> list[str]:
    """Γενικές ειδήσεις αγοράς. Χωρίς χρώμα θετικό/αρνητικό: το απλό sentiment τις διαβάζει συχνά λάθος."""
    if not items or not n:
        return []
    e = html.escape
    lines = ["", "<b>🌍 Γενικές ειδήσεις αγοράς:</b>"]
    for x in items[:n]:
        when = datetime.fromtimestamp(x.published).strftime("%d/%m %H:%M")
        lines.append(f'• <a href="{e(x.link)}">{e(x.title)}</a> <i>({e(x.source)}, {when})</i>')
    return lines


def macro_html(events: list, items: list) -> str:
    """Για την εντολή /macro: τι έρχεται αυτή την εβδομάδα και τι γίνεται γενικά στην αγορά."""
    lines = ["<b>🌍 Αγορά &amp; οικονομία</b>"]
    if events:
        lines += events_lines(events)[1:]
    else:
        lines.append("\nΔεν βρέθηκαν άλλα σημαντικά γεγονότα (ΗΠΑ) για αυτή την εβδομάδα.")
    lines += market_news_lines(items, 10)
    lines.append("\n<i>Τα γεγονότα υψηλής σημασίας των ΗΠΑ επηρεάζουν και τις μετοχές και τα crypto. "
                 "Γύρω από αυτά η τιμή κινείται συχνά απότομα και προς τις δύο κατευθύνσεις.</i>")
    lines.append(f"\n<i>{html.escape(DISCLAIMER)}</i>")
    return "\n".join(lines)


def ticket_lines(a: Analysis) -> list[str]:
    """Ακριβώς τι γράφεις σε κάθε πεδίο της εντολής στην πλατφόρμα paper trading (π.χ. TradingView)."""
    p = a.plan
    if not p:
        return []
    cur = load_config()["account"]
    t = portfolio.ticket(a.asset, p)
    unit = a.asset.symbol if a.asset.kind == "crypto" else "μετοχές"
    lines = ["", "<b>📋 Τι να βάλεις στην εντολή</b> (paper trading, π.χ. TradingView)"]
    if not t["total"]:
        return lines + [f"Με κεφάλαιο {cur['balance']:,.0f} {cur['currency']} και ρίσκο {cur['risk_per_trade_pct']:g}% "
                        f"η ποσότητα βγαίνει 0 (δεν φτάνει ούτε για 1 {'μετοχή' if a.asset.kind == 'stock' else 'μονάδα'}). "
                        f"Παράλειψε αυτό το trade."]
    lines += [
        f"Σύμβολο: <code>{t['tv_symbol']}</code> · Πλευρά: <b>{t['side_word']}</b> · Τύπος: <b>Market</b>",
    ]
    if p.trend_atr is not None:
        lines += [
            f"<b>Μία εντολή:</b> Ποσότητα <code>{t['total']:g}</code> · Stop Loss <code>{portfolio.plain(p.stop_loss)}</code>"
            f" · Take Profit: <b>κανένα</b>",
            "Κάθε πρωί το bot σού στέλνει το νέο Stop Loss (μόνο προς τα πάνω): άλλαξέ το στην εντολή.",
        ]
    elif t["qty2"]:
        lines += [
            f"<b>Εντολή 1:</b> Ποσότητα <code>{t['qty1']:g}</code> · Stop Loss <code>{portfolio.plain(p.stop_loss)}</code>"
            f" · Take Profit <code>{portfolio.plain(p.tp1)}</code>",
            f"<b>Εντολή 2:</b> Ποσότητα <code>{t['qty2']:g}</code> · Stop Loss <code>{portfolio.plain(p.stop_loss)}</code>"
            f" · Take Profit <code>{portfolio.plain(p.tp2)}</code>",
            f"Όταν κλείσει η Εντολή 1 (TP1), άλλαξε το Stop Loss της Εντολής 2 σε <code>{portfolio.plain(p.entry)}</code> "
            f"(την είσοδο): από εκεί και πέρα δεν μπορείς να χάσεις.",
        ]
    else:
        lines.append(f"<b>Μία εντολή:</b> Ποσότητα <code>{t['qty1']:g}</code> · Stop Loss "
                     f"<code>{portfolio.plain(p.stop_loss)}</code> · Take Profit <code>{portfolio.plain(p.tp1)}</code>")
    lines.append(f"Σύνολο: {t['total']:g} {unit} ≈ {t['value']:,.2f} {cur['currency']} · "
                 f"Αν χτυπήσει το SL χάνεις ≈ <b>{t['risk']:,.2f} {cur['currency']}</b>")
    if a.asset.kind == "stock":
        lines.append(f"Αν η μετοχή ανοίξει κάτω από το Stop Loss ({portfolio.plain(p.stop_loss)}), μην μπεις.")
    try:
        used = sum(o["entry"] * portfolio.remaining_qty(o) for o in portfolio.open_trades() if not o["broker"])
    except Exception:
        used = 0.0
    free = cur["balance"] - used
    if used and t["value"] > free:
        # Όπως στην προσομοίωση λογαριασμού: χωρίς margin, και αν δεν χωράει ούτε η μισή θέση, παράλειψη
        if free < 0.5 * t["value"]:
            lines.append(f"⚠️ Τα ανοιχτά σου trades δεσμεύουν ≈ {used:,.0f} {cur['currency']}: δεν φτάνουν τα "
                         f"διαθέσιμα ({max(free, 0):,.0f}). <b>Παράλειψε αυτό το trade</b> (χωρίς margin).")
        else:
            q = portfolio.round_qty(free / p.entry, a.asset.kind)
            lines.append(f"⚠️ Διαθέσιμα μόνο ≈ {free:,.0f} {cur['currency']} (τα υπόλοιπα είναι σε ανοιχτά trades): "
                         f"βάλε ποσότητα <code>{q:g}</code> αντί για {t['total']:g}.")
    return lines


def to_html(a: Analysis, show_news: int = 5) -> str:
    cur = load_config()["account"]["currency"]
    e = html.escape
    lines = [
        f"<b>{e(a.asset.symbol)}</b> ({'Crypto' if a.asset.kind == 'crypto' else 'Μετοχή'}) · {a.timeframe}",
        f"Τιμή: <b>{fmt_price(a.price)}</b>",
        "",
        f"<b>{bias_text(a)}</b>",
        f"Σκορ: <b>{a.score:+.0f}</b>/100 · Βεβαιότητα: {a.confidence}",
        "",
        "<b>📊 Τι βλέπω:</b>",
    ]
    for f in a.factors:
        icon = "🟢" if f.score > 0.1 else "🔴" if f.score < -0.1 else "⚪"
        lines.append(f"{icon} <i>{e(f.name)}</i>: {e(f.note)}")

    lv = a.levels
    if lv["resistance"]:
        lines.append("\n<b>Αντιστάσεις:</b> " + ", ".join(fmt_price(l) for l, _ in lv["resistance"]))
    if lv["support"]:
        lines.append("<b>Στηρίξεις:</b> " + ", ".join(fmt_price(l) for l, _ in lv["support"]))

    p = a.plan
    if p:
        lines += [
            "",
            f"<b>🎯 Ενδεικτικό πλάνο ({p.side})</b>",
            f"Είσοδος: <b>{fmt_price(p.entry)}</b>" + (f" (ή σε pullback ~{fmt_price(p.alt_entry)})" if p.alt_entry else ""),
            f"Stop loss: <b>{fmt_price(p.stop_loss)}</b> ({abs(p.stop_loss - p.entry) / p.entry * 100:.1f}%)",
        ]
        if p.trend_atr is not None:
            lines.append(f"Έξοδος: <b>trailing stop</b> {p.trend_atr:g} ATR κάτω από το υψηλότερο σημείο, "
                         f"χωρίς take profit")
        else:
            lines += [
                f"Take profit 1: <b>{fmt_price(p.tp1)}</b> (R:R 1:{p.rr_tp1:.1f}): κλείσε ~50% εδώ, μετακίνησε το SL στην είσοδο",
                f"Take profit 2: <b>{fmt_price(p.tp2)}</b> (R:R 1:{p.rr_tp2:.1f})",
            ]
        lines += [
            "",
            "<b>💰 Risk management</b>",
            f"Μέγεθος θέσης: <b>{p.qty:.6g}</b> μονάδες ≈ {p.position_value:,.2f} {cur}",
            f"Ρίσκο αν χτυπήσει το SL: <b>{p.risk_amount:,.2f} {cur}</b>",
        ]
        for n in p.notes:
            lines.append(f"• {e(n)}")
        lines += ticket_lines(a)

    lines += events_lines(a.events)

    if a.fear_greed and a.asset.kind == "crypto":
        lines.append(f"\n😨 Fear & Greed: {a.fear_greed['value']}/100 ({e(a.fear_greed['label'])})")

    if a.news and show_news:
        lines.append("\n<b>📰 Πρόσφατες ειδήσεις:</b>")
        for n in a.news[:show_news]:
            icon = "🟢" if n.sentiment > 0.15 else "🔴" if n.sentiment < -0.15 else "⚪"
            when = datetime.fromtimestamp(n.published).strftime("%d/%m %H:%M")
            lines.append(f'{icon} <a href="{e(n.link)}">{e(n.title)}</a> <i>({e(n.source)}, {when})</i>')
    lines += market_news_lines(a.market_news, min(show_news, 3))

    for w in a.warnings:
        lines.append(f"\n⚠️ {e(w)}")
    lines.append(f"\n<i>{e(DISCLAIMER)}</i>")
    return "\n".join(lines)


def html_to_text(s: str) -> str:
    s = re.sub(r'<a href="[^"]*">', "", s)
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def to_text(a: Analysis, show_news: int = 5) -> str:
    return html_to_text(to_html(a, show_news))




# ---------- Backtest ----------

STATUS_TEXT = {"TP2": "TP2 (πλήρης στόχος)", "TRAIL": "TP1 και μετά trailing stop", "TP1": "TP1 και μετά στην είσοδο",
               "STOP": "Trailing stop (με κέρδος ή μικρότερη ζημιά)", "SL": "Stop loss", "EXPIRED": "Έληξε (30 μέρες)"}


def _stats_line(s: dict) -> str:
    if not s["n"]:
        return "κανένα trade"
    return f"{s['n']} trades · win {s['win_rate']:.0f}% · {s['total_r']:+.1f}R (Μ.Ο. {s['avg_r']:+.2f}R)"


def _summary_lines(s: dict) -> list[str]:
    pf = "∞" if s["profit_factor"] == float("inf") else f"{s['profit_factor']:.2f}"
    return [
        f"Win rate: <b>{s['win_rate']:.0f}%</b>",
        f"Αποτέλεσμα: <b>{s['total_r']:+.1f}R</b> · Μ.Ο. <b>{s['avg_r']:+.2f}R</b> ανά trade",
        f"Profit factor: <b>{pf}</b> (κέρδη ÷ ζημιές, πάνω από 1.3 = καλό)",
        f"Max drawdown: <b>{s['max_dd_r']:.1f}R</b> · Χειρότερο σερί: <b>{s['loss_streak']}</b> χαμένα στη σειρά",
    ]


def _breakdown_lines(trades: list, old: list, new: list, old_label: str, new_label: str) -> list[str]:
    so, sn = stats(old), stats(new)
    lines = [
        "",
        "<b>Παλιά vs νέα δεδομένα</b>",
        f"• Παλιά {old_label}: {_stats_line(so)}",
        f"• Νέα {new_label}: {_stats_line(sn)}",
        "<i>Αν τα νέα είναι πολύ χειρότερα από τα παλιά, η στρατηγική ίσως έχει χάσει το πλεονέκτημά της.</i>",
        "",
        "<b>Ανά πλευρά</b>",
    ]
    for side in ("LONG", "SHORT"):
        lines.append(f"• {side}: {_stats_line(stats([t for t in trades if t.side == side]))}")
    lines.append("\n<b>Ανά δύναμη σκορ</b>")
    threshold = load_config()["analysis"]["min_score_for_signal"]
    for lo, hi in [(0, 40), (40, 60), (60, 101)]:
        sub = [t for t in trades if lo <= abs(t.score) < hi]
        lines.append(f"• |σκορ| {max(lo, threshold)}-{hi - 1 if hi < 101 else 100}: {_stats_line(stats(sub))}")
    lines.append("\n<b>Πώς έκλεισαν</b>")
    for st in STATUS_TEXT:
        n = sum(t.status == st for t in trades)
        if n:
            lines.append(f"• {STATUS_TEXT[st]}: {n}")
    return lines


def _conclusion_lines(s: dict, s_new: dict) -> list[str]:
    lines = ["\n<b>📝 Συμπέρασμα</b>"]
    if s["n"] < 30:
        lines.append("Πολύ λίγα trades για να βγει συμπέρασμα. Δοκίμασε μεγαλύτερο διάστημα "
                     "(π.χ. <code>/backtest AAPL 1d 3650</code>) ή όλη τη watchlist (<code>/backtest all</code>).")
        return lines
    if s["avg_r"] > 0.1 and s["profit_factor"] >= 1.3:
        lines.append("Η στρατηγική ήταν κερδοφόρα. Αυτό δεν εγγυάται το μέλλον, αλλά είναι καλό σημάδι. "
                     "Επιβεβαίωσέ το με paper trading.")
    elif s["avg_r"] > 0:
        lines.append("Οριακά θετικό αποτέλεσμα. Μικρές αλλαγές στην αγορά μπορεί να το κάνουν αρνητικό. "
                     "Προσοχή, και πρώτα paper trading.")
    else:
        lines.append("Η στρατηγική <b>έχασε</b>. Μην ακολουθείς τα σήματά της εδώ χωρίς άλλη επιβεβαίωση.")
    if s["avg_r"] > 0 and s_new["n"] and s_new["avg_r"] <= 0:
        lines.append("⚠️ Το κέρδος ήρθε από τα παλιά δεδομένα. Στο πιο πρόσφατο διάστημα η στρατηγική "
                     "<b>δεν</b> κέρδισε.")
    return lines


def _footer_lines(fee_pct: float, fine_note: str) -> list[str]:
    return [
        "",
        f"<i>{fine_note}Υπολογίζεται προμήθεια {fee_pct:g}% σε είσοδο και έξοδο. Όταν SL και TP χτυπούν "
        f"στο ίδιο κερί, υποθέτουμε το χειρότερο. Τα παλιά αποτελέσματα δεν εγγυώνται τα μελλοντικά.</i>",
        f"\n<i>{html.escape(DISCLAIMER)}</i>",
    ]


def _fine_note(trades: list, fine_tf: str | None) -> str:
    n = sum(t.fine for t in trades)
    if not trades or not n:
        return ""
    return f"Τα SL/TP ελέγχθηκαν σε κεριά {fine_tf} ({n} από {len(trades)} trades). "


def _sources_line(with_fg: bool) -> str:
    if with_fg:
        return "<i>Τεχνική ανάλυση + Fear &amp; Greed. Οι ειδήσεις δεν μπαίνουν (δεν υπάρχει ιστορικό τους).</i>"
    return "<i>Μόνο τεχνική ανάλυση: οι ειδήσεις δεν μπαίνουν (δεν υπάρχει ιστορικό τους).</i>"


def backtest_html(r) -> str:
    """Αναφορά backtest για ένα σύμβολο (r = backtest.Result)."""
    e = html.escape
    cur = load_config()["account"]["currency"]
    s = r.stats()
    years = (r.end - r.start).days / 365
    lines = [
        f"<b>🧪 Backtest {e(r.asset.symbol)} · {r.timeframe}</b>",
        f"Περίοδος: {r.start:%d/%m/%Y} έως {r.end:%d/%m/%Y} ({years:.1f} χρόνια, {r.bars} κεριά)",
        _sources_line(r.with_fg),
        "",
    ]
    if not s["n"]:
        lines.append("Δεν βγήκε κανένα σήμα σε αυτό το διάστημα.")
    else:
        final = r.final_balance
        old = [t for t in r.trades if t.opened < r.split_at]
        new = [t for t in r.trades if t.opened >= r.split_at]
        lines.append(f"Trades: <b>{s['n']}</b> (LONG {sum(t.side == 'LONG' for t in r.trades)} · "
                     f"SHORT {sum(t.side == 'SHORT' for t in r.trades)})")
        lines += _summary_lines(s)
        lines += [
            f"Πτώση κεφαλαίου (max): <b>{r.max_dd_pct:.1f}%</b>",
            "",
            f"💰 Κεφάλαιο με {r.risk_pct:g}% ρίσκο ανά trade: {r.balance:,.0f} → <b>{final:,.0f} {cur}</b> "
            f"({(final / r.balance - 1) * 100:+.1f}%)",
            f"📦 Buy &amp; hold στο ίδιο διάστημα: <b>{r.buy_hold_pct:+.1f}%</b>",
        ]
        lines += _breakdown_lines(r.trades, old, new, f"({r.start:%m/%Y} έως {r.split_at:%m/%Y})",
                                  f"({r.split_at:%m/%Y} έως {r.end:%m/%Y})")
        lines += _conclusion_lines(s, r.stats(new))
        if s["n"] >= 30 and final / r.balance - 1 < r.buy_hold_pct / 100:
            lines.append("Το απλό buy &amp; hold απέδωσε περισσότερο σε αυτό το διάστημα.")

    if r.open_trade:
        o = r.open_trade
        lines.append(f"\n⏳ Ανοιχτό τώρα: {o['side']} από {fmt_price(o['entry'])} "
                     f"(αρχικό SL {fmt_price(o['stop_loss'])}"
                     + (f", TP1 {fmt_price(o['tp1'])})" if o.get("tp1") is not None else ")"))
        if "stop_now" in o:
            lines.append(f"   Τώρα {fmt_price(o['last'])} ({o['r_now']:+.1f}R, δεν μετράει στα αποτελέσματα πάνω) · "
                         f"stop τώρα {fmt_price(o['stop_now'])}")
    lines += _footer_lines(r.fee_pct, _fine_note(r.trades, r.fine_tf))
    return "\n".join(lines)


def backtest_many_html(results: list, errors: list) -> str:
    """Συνολική αναφορά backtest για πολλά σύμβολα (π.χ. όλη η watchlist)."""
    e = html.escape
    if not results:
        return "❌ Δεν έτρεξε κανένα backtest.\n" + "\n".join(f"{e(s)}: {e(err)}" for s, err in errors)
    tfs = sorted({r.timeframe for r in results})
    trades = [t for r in results for t in r.trades]
    old = [t for r in results for t in r.trades if t.opened < r.split_at]
    new = [t for r in results for t in r.trades if t.opened >= r.split_at]
    s = stats(trades)
    start, end = min(r.start for r in results), max(r.end for r in results)
    lines = [
        f"<b>🧪 Backtest {len(results)} συμβόλων · {', '.join(tfs)}</b>",
        f"Περίοδος: έως {(end - start).days / 365:.1f} χρόνια ({start:%m/%Y} έως {end:%m/%Y})",
        _sources_line(any(r.with_fg for r in results)),
        "",
        "<b>Ανά σύμβολο</b>",
    ]
    for r in sorted(results, key=lambda r: -r.stats()["total_r"] if r.trades else 0):
        rs = r.stats()
        icon = "⚪" if not rs["n"] else "🟢" if rs["total_r"] > 0 else "🔴"
        lines.append(f"{icon} <b>{e(r.asset.symbol)}</b> ({(r.end - r.start).days / 365:.1f} χρ.): "
                     f"{_stats_line(rs)} · B&amp;H {r.buy_hold_pct:+.0f}%")
    for sym, err in errors:
        lines.append(f"❌ {e(sym)}: {e(err)[:80]}")

    if s["n"]:
        lines += ["", f"<b>Σύνολο: {s['n']} trades</b>"]
        lines += _summary_lines(s)
        lines += _breakdown_lines(trades, old, new, "(πρώτα 2/3 κάθε περιόδου)", "(τελευταίο 1/3)")
        lines += _conclusion_lines(s, stats(new))
        lines.append("<i>Το σύνολο σε R αθροίζει όλα τα σύμβολα, σαν να είχες μία θέση ανοιχτή σε καθένα.</i>")
    note = next((_fine_note(trades, r.fine_tf) for r in results if r.fine_tf), "")
    lines += _footer_lines(results[0].fee_pct, note)
    return "\n".join(lines)
