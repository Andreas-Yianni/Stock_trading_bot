"""Telegram bot: στέλνεις εντολές από το κινητό και παίρνεις ανάλυση + chart + ειδοποιήσεις.

Εκκίνηση:  python telegram_bot.py
"""
import asyncio
import html
import json
import logging
import time
from datetime import time as dtime, timezone
from logging.handlers import RotatingFileHandler

import httpx
import pandas as pd
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes
from telegram.request import HTTPXRequest

from tradebot import autoscan, backtest, broker, chart, engine, events, journal, market, news, portfolio, report, selfcheck
from tradebot.config import DATA_DIR, DISCLAIMER, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, load_config, save_config

# Console + data/bot.log (rotated: 5 files × 2 MB), so a crash/stop can be investigated afterwards.
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO,
                    handlers=[logging.StreamHandler(),
                              RotatingFileHandler(DATA_DIR / "bot.log", maxBytes=2_000_000, backupCount=5,
                                                  encoding="utf-8")])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot")

NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
HELP = """<b>📊 Ανάλυση</b>
/a SYMBOL [TF]: ανάλυση με chart και δελτίο εντολής (π.χ. <code>/a BTC</code>, <code>/a NVDA 1d</code>)
/news SYMBOL: ειδήσεις του συμβόλου
/ai SYMBOL: η γνώμη του AI (Gemini) με ΟΛΕΣ τις ειδήσεις που διάβασε και τι σκέφτηκε για καθεμία
/macro: σημαντικά γεγονότα της εβδομάδας (CPI, Fed...) και γενικές ειδήσεις
/scan: γρήγορη σάρωση της watchlist

<b>📒 Τα δικά σου paper trades</b>
Πάτα «✅ Το πήρα» κάτω από ένα σήμα όταν ανοίξεις την εντολή στην πλατφόρμα.
/trades: ανοιχτά trades και τρέχον κέρδος/ζημιά
/close ID [ΤΙΜΗ]: το έκλεισες μόνος σου (χωρίς τιμή = τρέχουσα)
/cancel ID: τελικά δεν το άνοιξες
/mystats: αποτελέσματα και κεφάλαιο

<b>🤖 Αυτόματο paper trading (Alpaca)</b>
/alpaca: λογαριασμός, θέσεις, αποτελέσματα, έλεγχος εκτέλεσης έναντι backtest
/auto on · /auto off: άνοιγμα/κλείσιμο των αυτόματων trades (τα ανοιχτά κρατάνε το stop τους)
/history [N]: τα τελευταία κλειστά trades (αυτόματα και δικά σου)
/week: η εβδομαδιαία αναφορά (έρχεται και μόνη της κάθε Σάββατο)

/stop SYMBOL: πώς βγαίνει το trailing stop βήμα-βήμα, για να το ελέγχεις μόνος σου
   (αν δεν πάτησες «Το πήρα»: <code>/stop AMZN 245.47 19/10</code> = αρχικό stop και μέρα εισόδου)

<b>⚙️ Ρυθμίσεις</b>
/balance ΠΟΣΟ: κεφάλαιο (ίδιο με τον paper λογαριασμό)
/risk ΠΟΣΟΣΤΟ: % ρίσκο ανά trade (προτείνεται 1)
/watchlist · /watch SYMBOL · /unwatch SYMBOL: η λίστα που παρακολουθεί

<b>🧪 Έλεγχος στρατηγικής</b>
/stats: πώς πάνε ΟΛΑ τα σήματα του bot
/backtest SYMBOL [TF] [ΜΕΡΕΣ]: πώς θα είχε πάει στο παρελθόν (π.χ. <code>/backtest BTC 4h</code>)
/backtest all [TF]: backtest σε όλη τη watchlist
/selfcheck: αυτοέλεγχος (γίνεται και μόνος του την 1η κάθε μήνα): ξανατρέχει τα τεστ με τα νέα δεδομένα,
συγκρίνει τους κανόνες με εναλλακτικές και με το SPY, και προτείνει αλλαγή μόνο αν είναι σταθερά καλύτερη

Timeframes: 1d (προεπιλογή), 4h, 1w. Μετοχές: μόνο 1d."""

# Το μενού που εμφανίζεται όταν πατάς «/» στο Telegram
MENU = [
    ("a", "Ανάλυση, π.χ. /a BTC ή /a NVDA 1d"),
    ("ai", "Γνώμη AI και οι ειδήσεις που διάβασε, π.χ. /ai NVDA"),
    ("trades", "Τα ανοιχτά paper trades μου"),
    ("mystats", "Αποτελέσματα και κεφάλαιο"),
    ("stop", "Υπολογισμός trailing stop, π.χ. /stop AMZN"),
    ("alpaca", "Αυτόματο paper trading: κατάσταση"),
    ("history", "Τα τελευταία κλειστά trades"),
    ("week", "Εβδομαδιαία αναφορά"),
    ("auto", "Αυτόματα trades: /auto on ή /auto off"),
    ("macro", "Γεγονότα εβδομάδας και ειδήσεις αγοράς"),
    ("scan", "Γρήγορη σάρωση της watchlist"),
    ("news", "Ειδήσεις, π.χ. /news ETH"),
    ("close", "Έκλεισα trade, π.χ. /close 3 81500"),
    ("cancel", "Δεν άνοιξα το trade, π.χ. /cancel 3"),
    ("balance", "Όρισε κεφάλαιο, π.χ. /balance 10000"),
    ("risk", "Ρίσκο ανά trade %, π.χ. /risk 1"),
    ("watchlist", "Η λίστα που παρακολουθεί το bot"),
    ("watch", "Πρόσθεσε σύμβολο, π.χ. /watch SOL"),
    ("unwatch", "Αφαίρεσε σύμβολο"),
    ("stats", "Πώς πάνε όλα τα σήματα του bot"),
    ("backtest", "Backtest, π.χ. /backtest BTC 4h"),
    ("selfcheck", "Αυτοέλεγχος κανόνων με τα νέα δεδομένα"),
    ("help", "Όλες οι εντολές"),
]


async def set_menu(app: Application):
    try:
        await app.bot.set_my_commands([BotCommand(c, d) for c, d in MENU])
    except Exception as e:   # μόνο το μενού «/»: δεν αξίζει να σταματήσει το bot γι' αυτό
        log.warning("set_my_commands failed: %s", e)


def _request(pool: int) -> HTTPXRequest:
    """Πιο ανεκτικά timeouts (20s αντί για 5s) και 3 νέες προσπάθειες όταν αποτύχει η σύνδεση, ώστε ένα
    στιγμιαίο πρόβλημα δικτύου (π.χ. το Wi-Fi που ξαναπιάνει) να μη ρίχνει το bot."""
    return HTTPXRequest(connection_pool_size=pool, connect_timeout=20, read_timeout=30, write_timeout=30,
                        pool_timeout=10, httpx_kwargs={"transport": httpx.AsyncHTTPTransport(retries=3)})



def authorized(update: Update) -> bool:
    return not TELEGRAM_CHAT_ID or str(update.effective_chat.id) == TELEGRAM_CHAT_ID


async def reply(update: Update, text: str, markup=None):
    await update.effective_message.reply_text(text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW,
                                              reply_markup=markup)


# Σήματα που περιμένουν «✅ Το πήρα» (κλειδί = αριθμός στο κουμπί). Χάνονται αν γίνει restart.
pending: dict[str, tuple] = {}


def took_button(a) -> InlineKeyboardMarkup | None:
    if not a.plan or not portfolio.ticket(a.asset, a.plan)["total"]:
        return None
    key = str(len(pending) + 1)
    pending[key] = (a.asset, a.timeframe, a.plan, a.score)
    return InlineKeyboardMarkup([[InlineKeyboardButton("✅ Το πήρα (paper trade)", callback_data=f"took:{key}")]])


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = update.effective_chat.id
    if not TELEGRAM_CHAT_ID:
        await reply(update, f"Το chat id σου είναι <code>{cid}</code>.\n"
                            f"Βάλ' το στο .env ως TELEGRAM_CHAT_ID και κάνε restart το bot, "
                            f"για να μπορείς να το χρησιμοποιείς μόνο εσύ.\n\n{HELP}")
    elif authorized(update):
        await reply(update, "Γεια! 👋\n\n" + HELP)


async def analyze_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not ctx.args:
        await reply(update, "Γράψε π.χ. <code>/a BTC</code> ή <code>/a TSLA 1d</code>")
        return
    symbol, tf = ctx.args[0], (ctx.args[1] if len(ctx.args) > 1 else None)
    await reply(update, f"⏳ Αναλύω {html.escape(symbol.upper())}...")
    try:
        a = await asyncio.to_thread(engine.analyze, symbol, tf)
        png = await asyncio.to_thread(chart.render_png, a)
    except Exception as e:
        await reply(update, f"❌ {html.escape(str(e))}")
        return
    await update.effective_message.reply_photo(png)
    await reply(update, report.to_html(a), took_button(a))
    if journal.record(a):
        await reply(update, "<i>📒 Καταγράφηκε στο paper trading ημερολόγιο.</i>")


async def ai_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Όλη η «εικόνα» του AI: κάθε είδηση που διάβασε, τι επίδραση είδε και τι θα έκανε."""
    if not authorized(update):
        return
    if not ctx.args:
        await reply(update, "Γράψε π.χ. <code>/ai NVDA</code>")
        return
    symbol, tf = ctx.args[0], (ctx.args[1] if len(ctx.args) > 1 else None)
    await reply(update, f"⏳ Ρωτάω το AI για {html.escape(symbol.upper())}...")
    try:
        a = await asyncio.to_thread(engine.analyze, symbol, tf)
    except Exception as e:
        await reply(update, f"❌ {html.escape(str(e))}")
        return
    for part in report.split_message(report.ai_html(a)):
        await reply(update, part)


async def news_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update) or not ctx.args:
        return
    asset = market.resolve(ctx.args[0])
    items = await asyncio.to_thread(news.get_news, asset)
    if not items:
        await reply(update, "Δεν βρέθηκαν πρόσφατες ειδήσεις.")
        return
    s = news.news_score(items)
    lines = [f"<b>📰 {html.escape(asset.symbol)}</b>: κλίμα {s:+.2f} (-1 αρνητικό, +1 θετικό)\n"]
    for n in items[:10]:
        icon = "🟢" if n.sentiment > 0.15 else "🔴" if n.sentiment < -0.15 else "⚪"
        lines.append(f'{icon} <a href="{html.escape(n.link)}">{html.escape(n.title)}</a>')
    await reply(update, "\n".join(lines))


async def scan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    cfg = load_config()
    tf = cfg["alerts"]["timeframe"]
    await reply(update, f"⏳ Σαρώνω {len(cfg['watchlist'])} σύμβολα ({tf})...")
    lines = [f"<b>Σάρωση watchlist · {tf}</b>"]
    for sym in cfg["watchlist"]:
        try:
            a = await asyncio.to_thread(engine.analyze, sym, tf, False)
            icon = {"LONG": "🟢", "SHORT": "🔴", "NEUTRAL": "⚪"}[a.bias]
            lines.append(f"{icon} <b>{a.asset.symbol}</b> {report.fmt_price(a.price)} · σκορ {a.score:+.0f}")
        except Exception as e:
            lines.append(f"❌ {sym}: {html.escape(str(e))[:80]}")
    lines.append("\nΓια λεπτομέρειες: /a SYMBOL")
    await reply(update, "\n".join(lines))


async def watchlist_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if authorized(update):
        await reply(update, "👀 " + ", ".join(load_config()["watchlist"]))


async def watch_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update) or not ctx.args:
        return
    cfg = load_config()
    sym = ctx.args[0].upper()
    if sym not in cfg["watchlist"]:
        cfg["watchlist"].append(sym)
        save_config(cfg)
    await reply(update, f"✅ Προστέθηκε το {html.escape(sym)}")


async def unwatch_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update) or not ctx.args:
        return
    cfg = load_config()
    sym = ctx.args[0].upper()
    cfg["watchlist"] = [s for s in cfg["watchlist"] if s != sym]
    save_config(cfg)
    await reply(update, f"🗑 Αφαιρέθηκε το {html.escape(sym)}")


async def set_account(update: Update, ctx: ContextTypes.DEFAULT_TYPE, key: str, label: str):
    if not authorized(update) or not ctx.args:
        return
    try:
        value = float(ctx.args[0].replace(",", "."))
    except ValueError:
        await reply(update, "Δώσε αριθμό.")
        return
    if key == "risk_per_trade_pct" and value > 3:
        await reply(update, "⚠️ Πάνω από 2-3% ρίσκο ανά trade είναι επικίνδυνο. Λίγα χαμένα trades στη σειρά θα σε πονέσουν πολύ.")
    cfg = load_config()
    cfg["account"][key] = value
    save_config(cfg)
    await reply(update, f"✅ {label}: {value:g}")


async def balance_cmd(update, ctx):
    await set_account(update, ctx, "balance", "Κεφάλαιο")


async def risk_cmd(update, ctx):
    await set_account(update, ctx, "risk_per_trade_pct", "Ρίσκο ανά trade (%)")


async def stats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await asyncio.to_thread(journal.evaluate_open)
    s = journal.stats()
    text = f"<b>📒 Paper trading</b>\nΑνοιχτά σήματα: {s['open']}\nΚλειστά: {s['closed']}"
    if s["closed"]:
        text += (f"\nWin rate: {s['win_rate']:.0f}%\nΣύνολο: {s['total_r']:+.2f}R · Μ.Ο.: {s['avg_r']:+.2f}R"
                 f"\n\n<i>R = πόσες φορές το ρίσκο σου κέρδισες/έχασες. Θετικό σύνολο σε 30+ σήματα = καλό σημάδι.</i>")
    await reply(update, text)


async def backtest_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not ctx.args:
        await reply(update, "Γράψε π.χ. <code>/backtest BTC</code>, <code>/backtest ETH 1h</code>, "
                            "<code>/backtest AAPL 1d 1000</code> (μέρες) ή <code>/backtest all</code> (όλη η watchlist)")
        return
    symbol = ctx.args[0]
    tf = ctx.args[1] if len(ctx.args) > 1 else None
    days = None
    if len(ctx.args) > 2:
        if not ctx.args[2].isdigit():
            await reply(update, "Οι μέρες πρέπει να είναι αριθμός, π.χ. <code>/backtest BTC 4h 365</code>")
            return
        days = int(ctx.args[2])
    if symbol.lower() == "all":
        symbols = load_config()["watchlist"]
        await reply(update, f"⏳ Backtest σε {len(symbols)} σύμβολα... (μπορεί να πάρει μερικά λεπτά)")
        results, errors = await asyncio.to_thread(backtest.run_many, symbols, tf, days)
        await reply(update, report.backtest_many_html(results, errors))
        return
    await reply(update, f"⏳ Backtest {html.escape(symbol.upper())}... (μπορεί να πάρει έως 1 λεπτό)")
    try:
        r = await asyncio.to_thread(backtest.run, symbol, tf, days)
        if r.trades:
            png = await asyncio.to_thread(chart.backtest_png, r)
            await update.effective_message.reply_photo(png)
    except Exception as e:
        await reply(update, f"❌ {html.escape(str(e))}")
        return
    await reply(update, report.backtest_html(r))


async def macro_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    evs = [e for e in await asyncio.to_thread(events.macro_events) if e.hours_left() > -1]
    items = await asyncio.to_thread(news.market_news)
    await reply(update, report.macro_html(evs, items))


def _selfcheck_markup(out: dict) -> InlineKeyboardMarkup | None:
    if not out["suggest"]:
        return None
    name = selfcheck.CANDIDATES[out["suggest"]][0]
    return InlineKeyboardMarkup([[InlineKeyboardButton(f"✅ Εφάρμοσε: {name}"[:60],
                                                       callback_data=f"apply:{out['suggest']}")]])


async def selfcheck_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await reply(update, "⏳ Αυτοέλεγχος: backtest των κανόνων και των εναλλακτικών στη watchlist (2-5 λεπτά)...")
    try:
        out = await asyncio.to_thread(selfcheck.run)
    except Exception as e:
        return await reply(update, f"❌ {html.escape(str(e))}")
    await reply(update, selfcheck.to_html(out), _selfcheck_markup(out))


async def selfcheck_job(ctx: ContextTypes.DEFAULT_TYPE):
    """Κάθε μήνα: ο ίδιος αυτοέλεγχος, αυτόματα."""
    if not TELEGRAM_CHAT_ID:
        return
    try:
        out = await asyncio.to_thread(selfcheck.run)
    except Exception as e:
        log.warning("selfcheck failed: %s", e)
        return
    await ctx.bot.send_message(TELEGRAM_CHAT_ID, selfcheck.to_html(out), parse_mode=ParseMode.HTML,
                               reply_markup=_selfcheck_markup(out))


async def apply_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not authorized(update):
        return await q.answer()
    key = q.data.split(":", 1)[1]
    if key not in selfcheck.CANDIDATES:
        return await q.answer("Άγνωστη αλλαγή", show_alert=True)
    name = await asyncio.to_thread(selfcheck.apply, key)
    await q.answer("Εφαρμόστηκε!")
    await q.edit_message_reply_markup(None)
    await reply(update, f"⚙️ Εφαρμόστηκε: <b>{html.escape(name)}</b>. Ισχύει από την επόμενη σάρωση. "
                        f"Τα ανοιχτά trades συνεχίζουν με τους κανόνες με τους οποίους άνοιξαν.")


# Γεγονότα για τα οποία έχει ήδη σταλεί ειδοποίηση
alerted_events: set[str] = set()


async def event_alerts(ctx: ContextTypes.DEFAULT_TYPE):
    """Ειδοποίηση ~1 ώρα πριν από μεγάλο γεγονός των ΗΠΑ και την παραμονή των earnings της watchlist."""
    if not TELEGRAM_CHAT_ID:
        return
    upcoming = [e for e in await asyncio.to_thread(events.macro_events) if 0 < e.hours_left() <= 1.5]
    for sym in load_config()["watchlist"]:
        er = await asyncio.to_thread(events.earnings, market.resolve(sym))
        if er and 0 < er.hours_left() <= 30:
            upcoming.append(er)
    new = [e for e in upcoming if e.key not in alerted_events]
    if not new:
        return
    alerted_events.update(e.key for e in new)
    text = "\n".join(["<b>⏰ Έρχεται σημαντικό γεγονός</b>"] + report.events_lines(new, warn=False)[2:] + [
        "",
        "Γύρω από αυτά τα γεγονότα η τιμή μπορεί να κινηθεί απότομα και προς τις δύο κατευθύνσεις. "
        "Αν δεν έχεις θέση, περίμενε να «κάτσει» η αγορά. Αν έχεις, βεβαιώσου ότι έχεις stop loss.",
    ])
    await ctx.bot.send_message(TELEGRAM_CHAT_ID, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


LAST_BIAS_FILE = DATA_DIR / "last_bias.json"


def _load_last_bias() -> dict[str, str]:
    try:
        return json.loads(LAST_BIAS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


async def scheduled_scan(ctx: ContextTypes.DEFAULT_TYPE):
    """Σαρώνει τη watchlist: πλήρης αναφορά για κάθε ΝΕΟ LONG/SHORT σήμα. Στο 1d τρέχει κάθε πρωί
    (μετά το κλείσιμο της Wall Street) και στέλνει και σύντομη λίστα με όλα τα τρέχοντα σήματα."""
    if not TELEGRAM_CHAT_ID:
        return
    cfg = load_config()
    tf = cfg["alerts"]["timeframe"]
    daily = tf in ("1d", "1w")
    last_bias = _load_last_bias()
    summary = []
    for sym in cfg["watchlist"]:
        try:
            a = await asyncio.to_thread(engine.analyze, sym, tf)
        except Exception as e:
            log.warning("scan %s failed: %s", sym, e)
            continue
        prev = last_bias.get(a.asset.symbol)
        last_bias[a.asset.symbol] = a.bias
        new = a.bias != "NEUTRAL" and prev is not None and prev != a.bias
        if a.bias != "NEUTRAL":
            icon = "🟢" if a.bias == "LONG" else "🔴"
            summary.append(f"{icon} <b>{a.asset.symbol}</b> {a.bias} · σκορ {a.score:+.0f}" + (" · 🆕" if new else ""))
        if new:
            png = await asyncio.to_thread(chart.render_png, a)
            await ctx.bot.send_photo(TELEGRAM_CHAT_ID, png, caption=f"🔔 Νέο σήμα: {a.asset.symbol} {a.bias}")
            await ctx.bot.send_message(TELEGRAM_CHAT_ID, report.to_html(a, show_news=3), reply_markup=took_button(a),
                                       parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)
            journal.record(a)
    LAST_BIAS_FILE.write_text(json.dumps(last_bias), encoding="utf-8")
    # Αυτόματα paper trades: μόνο στην κανονική πρωινή σάρωση (κλεισμένα ημερήσια κεριά), ποτέ μετά από restart
    auto_lines = await _auto_scan(ctx) if daily and not ctx.job.data and broker.configured() else []
    await _broker_sync(ctx)
    events, raised = await asyncio.to_thread(portfolio.update_trend_stops)
    for kind, t in events:
        await ctx.bot.send_message(TELEGRAM_CHAT_ID, trade_event_text(kind, t), parse_mode=ParseMode.HTML)
    for t, old, new in raised:
        if t["broker"]:
            try:
                await asyncio.to_thread(broker.set_stop, t, new)
                text = (f"🤖📈 <b>#{t['id']} {t['symbol']}: ανέβασα μόνος μου το stop στο Alpaca</b> σε "
                        f"<code>{portfolio.plain(new)}</code> (από {portfolio.plain(old)}).")
            except Exception as e:
                text = (f"⚠️ <b>#{t['id']} {t['symbol']}: δεν μπόρεσα να αλλάξω το stop στο Alpaca</b> "
                        f"({html.escape(str(e))}). Άλλαξέ το εσύ σε <code>{portfolio.plain(new)}</code>.")
            await ctx.bot.send_message(TELEGRAM_CHAT_ID, text, parse_mode=ParseMode.HTML)
            continue
        await ctx.bot.send_message(
            TELEGRAM_CHAT_ID,
            f"📈 <b>#{t['id']} {t['symbol']}: ανέβασε το Stop Loss</b> σε <code>{portfolio.plain(new)}</code> "
            f"(από {portfolio.plain(old)}).\nΣτην πλατφόρμα: άλλαξε το Stop Loss της εντολής. Το stop "
            f"ακολουθεί την τάση {t['trend_atr']:g} ATR κάτω από το υψηλότερο σημείο και δεν κατεβαίνει ποτέ.",
            parse_mode=ParseMode.HTML)
    if daily and not ctx.job.data:  # όχι στην αρχική «σιωπηλή» σάρωση μετά από restart
        text = "<b>☀️ Πρωινή σάρωση (1d)</b>\n" + ("\n".join(summary) if summary else "Κανένα σήμα σήμερα: αναμονή.")
        if auto_lines:
            text += "\n\n" + "\n".join(auto_lines)
        text += (f"\n\nΓια λεπτομέρειες και δελτίο εντολής: <code>/a SYMBOL</code>. "
                 f"Η αγορά ανοίγει {_us_open_greek()} ώρα Ελλάδας.")
        await ctx.bot.send_message(TELEGRAM_CHAT_ID, text, parse_mode=ParseMode.HTML)
    await asyncio.to_thread(journal.evaluate_open)


def _trade_line(t: dict) -> str:
    if t["trend_atr"]:
        return (f"{'🤖 ' if t['broker'] else ''}<b>#{t['id']} {t['symbol']} {t['side']}</b> · "
                f"είσοδος {portfolio.plain(t['entry'])}{'' if t['filled'] or not t['broker'] else ' (αναμονή εκτέλεσης)'} · "
                f"SL {portfolio.plain(t['stop_loss'])} (ανεβαίνει) · χωρίς TP · ποσότητα {portfolio.remaining_qty(t):g}")
    stop = t["entry"] if t["tp1_done"] else t["stop_loss"]
    target = t["tp2"] if t["tp1_done"] else t["tp1"]
    return (f"<b>#{t['id']} {t['symbol']} {t['side']}</b> · είσοδος {portfolio.plain(t['entry'])} · "
            f"SL {portfolio.plain(stop)} · TP {portfolio.plain(target)} · ποσότητα {portfolio.remaining_qty(t):g}"
            + (" · <i>TP1 ✔</i>" if t["tp1_done"] else ""))


async def took_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not authorized(update):
        return await q.answer()
    item = pending.pop(q.data.split(":", 1)[1], None)
    if not item:
        return await q.answer("Το σήμα έληξε (έγινε restart). Ζήτα νέα ανάλυση με /a.", show_alert=True)
    asset, tf, plan, score = item
    try:
        tid = await asyncio.to_thread(portfolio.open_trade, asset, tf, plan, score)
    except Exception as e:
        return await q.answer(str(e), show_alert=True)
    what = ("Κάθε πρωί θα σου λέω αν πρέπει να ανεβάσεις το Stop Loss, θα σε ειδοποιήσω όταν κλείσει και θα "
            "ενημερώσω το κεφάλαιο." if plan.trend_atr is not None else
            "Θα σε ειδοποιήσω όταν πιαστεί TP ή SL και θα ενημερώσω το κεφάλαιο.")
    await q.answer("Καταγράφηκε!")
    await q.edit_message_reply_markup(None)
    await reply(update, f"📒 Καταγράφηκε το trade <b>#{tid}</b>.\n{_trade_line(portfolio.get(tid))}\n\n"
                        f"{what} Αν τελικά δεν το άνοιξες: <code>/cancel {tid}</code>")


async def _price(t: dict) -> float | None:
    try:
        sym = f"{t['symbol']}/USDT" if t["kind"] == "crypto" else f"{t['symbol']}.US"
        df = await asyncio.to_thread(market.fetch_ohlcv, market.resolve(sym), "15m")
        return float(df["close"].iloc[-1])
    except Exception:
        return None


async def trades_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await check_trades(ctx)
    rows = portfolio.open_trades()
    if not rows:
        return await reply(update, "Δεν έχεις ανοιχτά paper trades. Πάτα «✅ Το πήρα» κάτω από ένα σήμα.")
    cur = load_config()["account"]["currency"]
    lines = ["<b>📂 Ανοιχτά paper trades</b>"]
    for t in rows:
        px = await _price(t)
        lines.append(_trade_line(t))
        if px:
            open_pnl = (px - t["entry"]) * portfolio.remaining_qty(t) * (1 if t["side"] == "LONG" else -1)
            lines.append(f"   τώρα {portfolio.plain(px)} · ανοιχτό {open_pnl:+,.2f} {cur}"
                         + (f" · ήδη κλεισμένο {t['pnl']:+,.2f} {cur}" if t["tp1_done"] else ""))
    await reply(update, "\n".join(lines))


async def close_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not ctx.args or not ctx.args[0].lstrip("#").isdigit():
        return await reply(update, "Γράψε π.χ. <code>/close 3 81500</code> (ή <code>/close 3</code> για την τρέχουσα τιμή)")
    tid = int(ctx.args[0].lstrip("#"))
    t = portfolio.get(tid)
    if not t:
        return await reply(update, f"Δεν βρέθηκε το #{tid}")
    try:
        if t["broker"]:   # αυτόματο trade: κλείνει στο Alpaca, στην τρέχουσα τιμή
            price = await asyncio.to_thread(broker.close_position, t)
        else:
            price = float(ctx.args[1].replace(",", ".")) if len(ctx.args) > 1 else await _price(t)
        r = await asyncio.to_thread(portfolio.close, tid, price)
    except Exception as e:
        return await reply(update, f"❌ {html.escape(str(e))}")
    cur = load_config()["account"]["currency"]
    if t["broker"]:
        return await reply(update, f"🤖 Έκλεισα το #{tid} στο Alpaca (paper) στο ~{portfolio.plain(price)}: "
                                   f"<b>{r['pnl']:+,.2f}$</b>. Το stop του ακυρώθηκε. Λογαριασμός: /alpaca")
    await reply(update, f"✅ Έκλεισε το #{tid} στο {portfolio.plain(price)}: {r['gain']:+,.2f} {cur} "
                        f"(σύνολο trade {r['pnl']:+,.2f} {cur})\nΚεφάλαιο: {r['balance_before']:,.2f} → "
                        f"<b>{r['balance_after']:,.2f} {cur}</b>")


async def cancel_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update) or not ctx.args or not ctx.args[0].lstrip("#").isdigit():
        return
    tid = int(ctx.args[0].lstrip("#"))
    t = portfolio.get(tid)
    if t and t["broker"]:
        return await reply(update, f"Το #{tid} είναι αυτόματο trade στο Alpaca: για να το κλείσεις γράψε "
                                   f"<code>/close {tid}</code>.")
    try:
        portfolio.cancel(tid)
        await reply(update, f"🗑 Ακυρώθηκε το #{tid} (δεν μετράει στα αποτελέσματα).")
    except Exception as e:
        await reply(update, f"❌ {html.escape(str(e))}")


async def stop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Δείχνει τον υπολογισμό του trailing stop, ώστε ο χρήστης να μπορεί να τον κάνει και μόνος του."""
    if not authorized(update):
        return
    usage = ("Γράψε <code>/stop AMZN</code> (για trade που καταχώρησες με «Το πήρα»), ή "
             "<code>/stop AMZN 245.47 19/10</code> (τωρινό stop και μέρα εισόδου).")
    if not ctx.args:
        return await reply(update, usage)
    sym = ctx.args[0].upper()
    k = float(load_config()["analysis"].get("trend_atr", 6))
    t = next((x for x in portfolio.open_trades() if x["symbol"] == sym and x["trend_atr"]), None)
    try:
        if len(ctx.args) >= 3:
            stop = float(ctx.args[1].replace(",", "."))
            d, m = (int(x) for x in ctx.args[2].split("/")[:2])
            now = pd.Timestamp.now(tz="America/New_York")
            opened = pd.Timestamp(year=now.year if (m, d) <= (now.month, now.day) else now.year - 1,
                                  month=m, day=d, tz="America/New_York")
            asset, tf, side = market.resolve(sym), "1d", "LONG"
        elif t:
            asset, tf, side, stop, k = (portfolio._asset(t), t["timeframe"], t["side"], t["stop_loss"],
                                        t["trend_atr"])
            opened = pd.Timestamp(t["opened_at"])
        else:
            return await reply(update, f"Δεν βρήκα ανοιχτό trade {html.escape(sym)} στο bot. " + usage)
        st = await asyncio.to_thread(portfolio.stop_steps, asset, tf, side, opened, stop, k)
    except Exception as e:
        return await reply(update, f"❌ {html.escape(str(e))}\n{usage}")
    if not st:
        return await reply(update, "Δεν έχει κλείσει ακόμα κανένα κερί μετά την είσοδο: το stop μένει ίδιο.")
    p = portfolio.plain
    raise_it = st["new"] > st["stop"] + 1e-9
    lines = [
        f"<b>🧮 Trailing stop {html.escape(sym)}</b> ({tf}, {st['bars']} κλεισμένα κεριά από την είσοδο)",
        f"1. Υψηλότερο high από την είσοδο: <b>{p(st['extreme'])}</b> ({st['extreme_at']:%d/%m})",
        f"2. ATR(14) στο τελευταίο κλεισμένο κερί ({st['atr_at']:%d/%m}): <b>{p(st['atr'])}</b>",
        f"3. {p(st['extreme'])} − {st['k']:g} × {p(st['atr'])} = <b>{p(st['level'])}</b>",
        f"4. Τωρινό stop: {p(st['stop'])}",
        "",
        (f"👉 <b>Ανέβασε το Stop Loss σε {p(st['new'])}</b> (το 3 είναι πάνω από το 4)." if raise_it else
         f"👉 <b>Μένει {p(st['stop'])}</b>: το 3 είναι κάτω από το τωρινό stop, και το stop δεν κατεβαίνει ποτέ."),
        f"Τελευταίο κλείσιμο: {p(st['close'])}.",
        "<i>Στο TradingView: chart 1D, Indicators → «Average True Range» (14). Ίδια νούμερα.</i>",
    ]
    if t and raise_it and len(ctx.args) < 3:
        lines.append("<i>Το bot θα το καταγράψει μόνο του στην πρωινή σάρωση.</i>")
    await reply(update, "\n".join(lines))


async def mystats_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await check_trades(ctx)
    s = portfolio.stats()
    cur = load_config()["account"]["currency"]
    text = (f"<b>📒 Τα δικά σου paper trades</b>\nΚεφάλαιο: <b>{s['balance']:,.2f} {cur}</b>\n"
            f"Ανοιχτά: {s['open']} · Κλειστά: {s['closed']}")
    if s["closed"]:
        text += (f"\nΚερδισμένα: {s['wins']}/{s['closed']} ({100 * s['wins'] / s['closed']:.0f}%)"
                 f"\nΑποτέλεσμα: <b>{s['pnl']:+,.2f} {cur}</b> · {s['total_r']:+.2f}R (Μ.Ο. {s['avg_r']:+.2f}R)"
                 f"\n\n<i>Κρίνε τη στρατηγική μετά από 30+ κλειστά trades, όχι νωρίτερα.</i>")
    await reply(update, text)


def trade_event_text(kind: str, t: dict) -> str:
    cur = load_config()["account"]["currency"]
    bal = f"Κεφάλαιο: {t['balance_before']:,.2f} → <b>{t['balance_after']:,.2f} {cur}</b>"
    head = f"#{t['id']} {t['symbol']} {t['side']}"
    if kind == "TP1":
        return (f"🎯 <b>{head}: πιάστηκε το TP1</b> στο {portfolio.plain(t['tp1'])}. Έκλεισε η Εντολή 1: "
                f"{t['gain']:+,.2f} {cur}.\n👉 <b>Τώρα στην πλατφόρμα άλλαξε το Stop Loss της Εντολής 2 σε "
                f"<code>{portfolio.plain(t['entry'])}</code></b> (την είσοδο).\n{bal}")
    if kind == "SL":
        return (f"🔴 <b>{head}: χτύπησε το Stop Loss</b> στο {portfolio.plain(t['exit_price'])}. "
                f"Ζημιά {t['pnl']:+,.2f} {cur}.\n{bal}\n<i>Είναι μέρος του παιχνιδιού: το SL σε προστάτεψε "
                f"από μεγαλύτερη ζημιά.</i>")
    if kind == "STOP":
        icon, word = ("🟢", "κέρδος") if t["pnl"] > 0 else ("⚪", "αποτέλεσμα")
        return (f"{icon} <b>{head}: έκλεισε στο trailing stop</b> στο {portfolio.plain(t['exit_price'])}. "
                f"Συνολικό {word}: {t['pnl']:+,.2f} {cur}.\n{bal}\n<i>Η τάση τελείωσε· το stop κράτησε "
                f"το μεγαλύτερο μέρος της κίνησης.</i>")
    if kind == "BE":
        return (f"⚪ <b>{head}: η Εντολή 2 έκλεισε στην είσοδο</b>. Συνολικά για το trade: "
                f"{t['pnl']:+,.2f} {cur} (από το TP1).\n{bal}")
    return (f"🟢 <b>{head}: πιάστηκε ο στόχος</b> στο {portfolio.plain(t['exit_price'])}! "
            f"Συνολικά {t['pnl']:+,.2f} {cur}.\n{bal}")


async def check_trades(ctx: ContextTypes.DEFAULT_TYPE):
    """Κλείνει αυτόματα ό,τι χτύπησε TP/SL και σε ειδοποιεί τι να κάνεις στην πλατφόρμα."""
    if not TELEGRAM_CHAT_ID:
        return
    for kind, t in await asyncio.to_thread(portfolio.check_all):
        await ctx.bot.send_message(TELEGRAM_CHAT_ID, trade_event_text(kind, t), parse_mode=ParseMode.HTML)
    await _broker_sync(ctx)


def _us_open_greek() -> str:
    """Τι ώρα ανοίγει σήμερα η Wall Street (09:30 Νέα Υόρκη) σε ώρα Ελλάδας: 16:30, αλλά 15:30 όταν οι δύο
    χώρες αλλάζουν ώρα σε διαφορετικές Κυριακές (τέλη Οκτωβρίου, Μάρτιος)."""
    ny = pd.Timestamp.now(tz="America/New_York").normalize() + pd.Timedelta(hours=9, minutes=30)
    return ny.tz_convert("Europe/Athens").strftime("%H:%M")


def _r(x) -> str:
    return "-" if x is None else f"{x:+.2f}R"


async def _send(ctx, text: str):
    await ctx.bot.send_message(TELEGRAM_CHAT_ID, text, parse_mode=ParseMode.HTML, link_preview_options=NO_PREVIEW)


async def _check_health(ctx, acc: dict | None = None) -> bool:
    """Δικλείδα για λάθη: ειδοποίηση (μία φορά τη μέρα) ή παύση των αυτόματων trades. True = έγινε παύση."""
    try:
        problems = await asyncio.to_thread(broker.health, acc)
    except Exception as e:
        log.warning("broker health failed: %s", e)
        return False
    stopped = False
    for level, msg in problems:
        if level == "pause":
            if broker.enabled():
                await asyncio.to_thread(broker.pause, msg)
                await _send(ctx, f"🛑 <b>🤖 Σταμάτησα τα αυτόματα trades:</b> {html.escape(msg)}.\n"
                                 f"Οι ανοιχτές θέσεις κρατάνε το stop τους και συνεχίζω να το ανεβάζω. Κοίταξε τι "
                                 f"έγινε (/alpaca, και στο app.alpaca.markets) και μετά <code>/auto on</code> για να συνεχίσει.")
            stopped = True
        elif broker.once_today(msg):
            await _send(ctx, f"⚠️ 🤖 {html.escape(msg)}")
    return stopped


async def _auto_scan(ctx) -> list[str]:
    """Πρωινή αυτόματη σάρωση των ~100 μετοχών για το Alpaca. Επιστρέφει γραμμές για την πρωινή αναφορά."""
    try:
        acc = await asyncio.to_thread(broker.account)
        await asyncio.to_thread(broker.snapshot, acc)
        if not broker.enabled():
            ps = broker.paused()
            return [f"🤖 Αυτόματα trades σε <b>παύση</b>: {html.escape(ps['reason'])}. "
                    f"Όταν το δεις: <code>/auto on</code>"] if ps else []
        if await _check_health(ctx, acc):
            return ["🤖 Αυτόματα trades σε παύση (δες το μήνυμα 🛑)."]
        res = await asyncio.to_thread(autoscan.run)
    except Exception as e:
        log.warning("autoscan failed: %s", e)
        await _send(ctx, f"⚠️ 🤖 Η αυτόματη σάρωση απέτυχε: {html.escape(str(e))}")
        return []
    if res.get("already"):
        return []
    bar = pd.Timestamp(res["bar"]).strftime("%d/%m") if res["bar"] else "-"
    lines = [f"<b>🤖 Αυτόματα (Alpaca paper)</b>: {res['scanned']} μετοχές, κεριά της {bar}, "
             f"{res['signals']} νέα σήματα LONG"]
    for t in res["opened"]:
        risk = (t["plan_entry"] - t["sl0"]) * t["qty1"]
        lines.append(f"✅ #{t['id']} <b>{t['symbol']}</b>: Buy {t['qty1']:g} Market στο άνοιγμα · Stop "
                     f"<code>{portfolio.plain(t['stop_loss'])}</code> · ρίσκο ≈ {risk:,.0f}$")
    if res["skipped"]:
        sk = [f"{sym} ({why})" for sym, why in res["skipped"][:8]]
        more = len(res["skipped"]) - len(sk)
        lines.append("Παραλείφθηκαν: " + html.escape(", ".join(sk)) + (f" και {more} ακόμα" if more > 0 else ""))
    if res["errors"]:
        lines.append(f"<i>Χωρίς δεδομένα σήμερα: {html.escape(', '.join(res['errors'][:10]))}</i>")
    return lines


BROKER_TEXT = {
    "FILLED": "🤖✅ <b>#{id} {symbol}: εκτελέστηκε</b> στο {entry} ({qty} μετοχές). Stop Loss {stop}.",
    "CANCELLED": "🤖 #{id} {symbol}: η εντολή δεν εκτελέστηκε ({why}), ακυρώθηκε.",
    "GAP": "🤖⚪ <b>#{id} {symbol}: άνοιξε κάτω από το stop</b>, έκλεισα αμέσως στο {exit} ({pnl}$).",
    "SL": "🤖🔴 <b>#{id} {symbol}: χτύπησε το Stop Loss</b> στο {exit}. Ζημιά {pnl}$ ({r}).",
    "STOP": "🤖🟢 <b>#{id} {symbol}: έκλεισε στο trailing stop</b> στο {exit}. Αποτέλεσμα {pnl}$ ({r}).",
}


def _cmp_line(kind: str, c: dict | None) -> str:
    """Τι λέει το backtest για το ίδιο σήμα (έλεγχος εκτέλεσης)."""
    if not c:
        return ""
    if kind == "FILLED" and c["fill_diff_r"] is not None:
        return (f"\n📐 Backtest: είσοδος στο {portfolio.plain(c['bt_fill'])} · διαφορά {_r(c['fill_diff_r'])}"
                + (" ⚠️" if abs(c["fill_diff_r"]) > 0.3 else ""))
    if kind in ("SL", "STOP"):
        if c["bt_status"] in (None, "SKIP"):
            return ("\n⚠️ 📐 Στο backtest το ίδιο σήμα " + ("είναι ακόμα ανοιχτό" if c["bt_status"] is None else
                    "δεν θα είχε μπει") + ": η εκτέλεση διέφερε, θα το δεις και στο /alpaca.")
        flag = " ⚠️ μεγάλη διαφορά" if c["diff_r"] is not None and abs(c["diff_r"]) > 0.5 else ""
        return f"\n📐 Backtest για το ίδιο σήμα: {_r(c['bt_r'])} · διαφορά {_r(c['diff_r'])}{flag}"
    return ""


async def _broker_sync(ctx):
    if not broker.configured():
        return
    try:
        evs = await asyncio.to_thread(broker.sync)
    except Exception as e:
        log.warning("broker sync failed: %s", e)
        return
    for kind, t in evs:
        text = BROKER_TEXT[kind].format(
            id=t["id"], symbol=t["symbol"], entry=portfolio.plain(t["entry"]), qty=f"{t['qty1']:g}",
            stop=portfolio.plain(t["stop_loss"]), why=t.get("why", ""), r=_r(portfolio.plan_r(t)) if kind in ("SL", "STOP") else "",
            exit=portfolio.plain(t["exit_price"]) if t.get("exit_price") else "-", pnl=f"{t.get('pnl') or 0:+,.2f}")
        await _send(ctx, text + _cmp_line(kind, t.get("cmp")))
    if broker.enabled() or any(t["broker"] for t in portfolio.open_trades()):
        await _check_health(ctx)


def _since_start(hist: dict) -> tuple[str, float, float | None] | None:
    """(από πότε, % του λογαριασμού Alpaca, % του SPY στο ίδιο διάστημα) από τα ημερήσια στιγμιότυπα."""
    if len(hist) < 2:
        return None
    d0, e0 = next(iter(hist.items()))
    e1 = list(hist.values())[-1]
    spy = None
    try:
        c = market.fetch_ohlcv(market.resolve("SPY.US"), "1d")["close"]
        c.index = c.index.tz_convert("America/New_York").normalize().tz_localize(None)
        before = c[c.index < pd.Timestamp(d0)]
        if len(before):
            spy = 100 * (c.iloc[-1] / before.iloc[-1] - 1)
    except Exception:
        pass
    return d0, 100 * (e1 / e0 - 1), spy


async def alpaca_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    if not broker.configured():
        return await reply(update, "Δεν έχει ρυθμιστεί το Alpaca: βάλε <code>ALPACA_API_KEY</code> και "
                                   "<code>ALPACA_SECRET_KEY</code> (paper) στο .env και κάνε restart το bot.")
    try:
        await _broker_sync(ctx)
        st = await asyncio.to_thread(broker.status)
        since = await asyncio.to_thread(_since_start, broker.equity_history())
        ex = await asyncio.to_thread(broker.execution_summary)
        problems = await asyncio.to_thread(broker.health)
    except Exception as e:
        return await reply(update, f"❌ {html.escape(str(e))}")
    se = st["settings"]
    state = "ΕΝΕΡΓΑ" if st["enabled"] else ("σε ΠΑΥΣΗ" if st["paused"] else "ανενεργά")
    day = 100 * (st["equity"] / st["last_equity"] - 1) if st["last_equity"] else 0.0
    lines = [f"<b>🤖 Alpaca paper</b> · αυτόματα trades: <b>{state}</b>",
             f"Κεφάλαιο: <b>{st['equity']:,.2f}$</b> · μετρητά {st['cash']:,.2f}$ · σήμερα {day:+.2f}%"]
    if st["paused"]:
        lines.append(f"🛑 Παύση: {html.escape(st['paused']['reason'])}")
    if since:
        d0, acc_pct, spy_pct = since
        lines.append(f"Από {pd.Timestamp(d0):%d/%m/%Y}: <b>{acc_pct:+.2f}%</b>"
                     + (f" · SPY στο ίδιο διάστημα {spy_pct:+.2f}%" if spy_pct is not None else ""))
    uni = "οι " + str(len(autoscan.universe())) + " μεγαλύτερες μετοχές του S&P 500" if se["universe"] == "top100" \
        else f"{len(autoscan.universe())} μετοχές"
    lines.append(f"Κανόνες: {uni} · ρίσκο {se['risk_per_trade_pct']:g}% ανά trade · έως {se['max_position_pct']:g}% "
                 f"ανά θέση · έως {se['max_positions']} θέσεις · έως {se['max_new_per_day']} νέες τη μέρα · παύση αν "
                 f"−{se['max_day_loss_pct']:g}% σε μία μέρα")
    trades = {t["symbol"]: t for t in portfolio.open_trades() if t["broker"]}
    for x in st["positions"]:
        t = trades.get(x["symbol"])
        px = float(x["current_price"])
        extra = f" · {_r(portfolio.plan_r(t, px))} · stop {portfolio.plain(t['stop_loss'])}" if t else " · (όχι του bot)"
        lines.append(f"• {x['symbol']}: {float(x['qty']):g} από {float(x['avg_entry_price']):,.2f} · τώρα {px:,.2f} "
                     f"({float(x['unrealized_pl']):+,.2f}$){extra}")
    waiting = [t for t in trades.values() if not t["filled"]]
    if waiting:
        lines.append("Περιμένουν εκτέλεση στο άνοιγμα: " + ", ".join(t["symbol"] for t in waiting))
    s = portfolio.stats(broker=True)
    if s["closed"]:
        lines.append(f"Κλειστά: {s['closed']} · κερδισμένα {s['wins']} · {s['pnl']:+,.2f}$ · Μ.Ο. {s['avg_r']:+.2f}R "
                     f"· /history")
    if ex["n"]:
        lines.append(f"📐 Εκτέλεση έναντι backtest ({ex['n']} trades): μέση διαφορά {_r(ex['avg_diff'])} · "
                     f"μεγαλύτερη {_r(ex['worst'])}" + (f" · {ex['outcome_mismatch']} με άλλη έκβαση ⚠️"
                                                        if ex["outcome_mismatch"] else ""))
    for level, msg in problems:
        lines.append(("🛑 " if level == "pause" else "⚠️ ") + html.escape(msg))
    if not st["enabled"]:
        lines.append("\nΓια να ξεκινήσει: <code>/auto on</code>")
    await reply(update, "\n".join(lines))


def _trade_hist_line(t: dict) -> str:
    who = "🤖" if t["broker"] else "👤"
    o, c = pd.Timestamp(t["opened_at"]), pd.Timestamp(t["closed_at"])
    days = (c - o).days
    icon = "🟢" if t["pnl"] > 0 else "🔴"
    line = (f"{who}{icon} #{t['id']} <b>{t['symbol']}</b> · {o:%d/%m}→{c:%d/%m} ({days} μέρες) · "
            f"{portfolio.plain(t['entry'])}→{portfolio.plain(t['exit_price'] or t['entry'])} · "
            f"{_r(portfolio.plan_r(t))} ({t['pnl']:+,.0f}$)")
    if t["broker"]:
        try:
            cmp = broker.compare(t)
        except Exception:
            cmp = None
        if cmp and cmp["bt_r"] is not None:
            line += f" · backtest {_r(cmp['bt_r'])}"
    return line


def _totals_line(label: str, rows: list[dict]) -> str:
    if not rows:
        return f"{label}: κανένα κλειστό trade"
    rs = [portfolio.plan_r(t) or 0 for t in rows]
    wins = sum(1 for t in rows if t["pnl"] > 0)
    return (f"{label}: {len(rows)} trades · κερδισμένα {wins} ({100 * wins / len(rows):.0f}%) · "
            f"{sum(rs):+.1f}R · {sum(t['pnl'] for t in rows):+,.0f}$")


async def history_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    n = int(ctx.args[0]) if ctx.args and ctx.args[0].isdigit() else 15
    rows = await asyncio.to_thread(portfolio.history, n, None)
    if not rows:
        return await reply(update, "Δεν υπάρχει ακόμα κανένα κλειστό trade.")
    lines = [f"<b>📜 Τα τελευταία {len(rows)} κλειστά trades</b> (🤖 Alpaca · 👤 TradingView)"]
    lines += await asyncio.to_thread(lambda: [_trade_hist_line(t) for t in rows])
    lines += ["", _totals_line("🤖 Σύνολο Alpaca", portfolio.closed_trades(broker=True)),
              _totals_line("👤 Σύνολο TradingView", portfolio.closed_trades(broker=False))]
    await reply(update, "\n".join(lines))


def _week_text() -> str:
    now = pd.Timestamp.now(tz="UTC")
    start = now - pd.Timedelta(days=7)
    lines = [f"<b>📅 Εβδομαδιαία αναφορά</b> ({start:%d/%m} – {now:%d/%m})"]
    if broker.configured():
        try:
            st = broker.status()
            hist = broker.equity_history()
            since = _since_start(hist)
            week_ago = [v for d, v in hist.items() if pd.Timestamp(d, tz="UTC") <= start]
            wk = f" · εβδομάδα {100 * (st['equity'] / week_ago[-1] - 1):+.2f}%" if week_ago else ""
            lines.append(f"\n<b>🤖 Alpaca</b>: κεφάλαιο {st['equity']:,.0f}${wk}"
                         + (f" · από {pd.Timestamp(since[0]):%d/%m} {since[1]:+.2f}%" if since else "")
                         + (f" (SPY {since[2]:+.2f}%)" if since and since[2] is not None else ""))
            if st["paused"]:
                lines.append(f"🛑 Σε παύση: {html.escape(st['paused']['reason'])}")
        except Exception as e:
            lines.append(f"\n🤖 Alpaca: δεν φορτώθηκε ({html.escape(str(e))})")
    closed = [t for t in portfolio.closed_trades(broker=None) if t["closed_at"] and pd.Timestamp(t["closed_at"]) >= start]
    lines.append(f"\n<b>Κλειστά αυτή την εβδομάδα: {len(closed)}</b>")
    lines += [_trade_hist_line(t) for t in sorted(closed, key=lambda t: t["closed_at"])]
    opened = portfolio.open_trades()
    if opened:
        lines.append(f"\n<b>Ανοιχτά: {len(opened)}</b>")
        for t in opened:
            lines.append(("🤖 " if t["broker"] else "👤 ") + f"#{t['id']} {t['symbol']} · από "
                         f"{pd.Timestamp(t['opened_at']):%d/%m} · stop {portfolio.plain(t['stop_loss'])}")
    try:
        ex = broker.execution_summary() if broker.configured() else {"n": 0}
    except Exception:
        ex = {"n": 0}
    if ex["n"]:
        lines.append(f"\n📐 Εκτέλεση έναντι backtest ({ex['n']} trades): μέση διαφορά {_r(ex['avg_diff'])}")
    auto = portfolio.closed_trades(broker=True)
    lines += ["", _totals_line("🤖 Σύνολο Alpaca", auto), _totals_line("👤 Σύνολο TradingView",
                                                                     portfolio.closed_trades(broker=False))]
    lines.append("\n<i>" + html.escape(selfcheck.patience_note(len(auto))) + "</i>")
    lines.append(f"\n<i>{html.escape(DISCLAIMER)}</i>")
    return "\n".join(lines)


async def week_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    await _broker_sync(ctx)
    await reply(update, await asyncio.to_thread(_week_text))


async def weekly_job(ctx: ContextTypes.DEFAULT_TYPE):
    if not TELEGRAM_CHAT_ID:
        return
    await _broker_sync(ctx)
    await _send(ctx, await asyncio.to_thread(_week_text))


async def auto_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        return
    arg = (ctx.args[0].lower() if ctx.args else "")
    if arg not in ("on", "off"):
        return await reply(update, "Γράψε <code>/auto on</code> ή <code>/auto off</code> (κατάσταση: /alpaca)")
    if arg == "on":
        if not broker.configured():
            return await reply(update, "Πρώτα βάλε τα κλειδιά του Alpaca (paper) στο .env και κάνε restart.")
        try:
            await asyncio.to_thread(broker.account)   # ελέγχει ότι είναι paper λογαριασμός
        except Exception as e:
            return await reply(update, f"❌ {html.escape(str(e))}")
    broker.set_enabled(arg == "on")
    await reply(update, "🤖 Αυτόματα trades <b>ΕΝΕΡΓΑ</b>: σε κάθε νέο σήμα LONG της πρωινής σάρωσης θα στέλνω "
                        "εντολή στο Alpaca paper, με stop που ανεβαίνει μόνο του." if arg == "on" else
                        "🤖 Αυτόματα trades <b>ανενεργά</b>. Οι ανοιχτές θέσεις κρατάνε το stop τους και συνεχίζω "
                        "να το ανεβάζω· για να κλείσεις μία: <code>/close ID</code>.")


def main():
    if not TELEGRAM_BOT_TOKEN:
        raise SystemExit("Λείπει το TELEGRAM_BOT_TOKEN στο αρχείο .env (δες README.md)")
    app = (Application.builder().token(TELEGRAM_BOT_TOKEN).request(_request(64)).get_updates_request(_request(1))
           .post_init(set_menu).build())
    app.add_handler(CommandHandler(["start", "help"], start))
    app.add_handler(CommandHandler(["a", "analyze"], analyze_cmd))
    app.add_handler(CommandHandler("ai", ai_cmd))
    app.add_handler(CommandHandler("news", news_cmd))
    app.add_handler(CommandHandler("scan", scan_cmd))
    app.add_handler(CommandHandler("watchlist", watchlist_cmd))
    app.add_handler(CommandHandler("watch", watch_cmd))
    app.add_handler(CommandHandler("unwatch", unwatch_cmd))
    app.add_handler(CommandHandler("balance", balance_cmd))
    app.add_handler(CommandHandler("risk", risk_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler(["backtest", "bt"], backtest_cmd))
    app.add_handler(CommandHandler("macro", macro_cmd))
    app.add_handler(CommandHandler("trades", trades_cmd))
    app.add_handler(CommandHandler("close", close_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("mystats", mystats_cmd))
    app.add_handler(CommandHandler("stop", stop_cmd))
    app.add_handler(CommandHandler("alpaca", alpaca_cmd))
    app.add_handler(CommandHandler("auto", auto_cmd))
    app.add_handler(CommandHandler("history", history_cmd))
    app.add_handler(CommandHandler("week", week_cmd))
    app.add_handler(CommandHandler("selfcheck", selfcheck_cmd))
    app.add_handler(CallbackQueryHandler(took_cb, pattern=r"^took:"))
    app.add_handler(CallbackQueryHandler(apply_cb, pattern=r"^apply:"))

    alerts = load_config()["alerts"]
    if alerts["timeframe"] in ("1d", "1w"):
        # Ημερήσια κεριά: σάρωση Τρίτη-Σάββατο το πρωί (μετά από κάθε συνεδρίαση Δευτέρας-Παρασκευής)
        hh, mm = (int(x) for x in str(alerts.get("daily_scan_utc", "06:00")).split(":"))
        app.job_queue.run_daily(scheduled_scan, time=dtime(hh, mm, tzinfo=timezone.utc), days=(2, 3, 4, 5, 6))
        app.job_queue.run_once(scheduled_scan, when=10, data="silent")  # μόνο για να θυμάται τα τρέχοντα σήματα
        when_text = f"κάθε πρωί στις {hh:02d}:{mm:02d} UTC"
        # Αυτοέλεγχος την 1η κάθε μήνα, μία ώρα μετά την πρωινή σάρωση
        app.job_queue.run_monthly(selfcheck_job, when=dtime((hh + 1) % 24, mm, tzinfo=timezone.utc), day=1)
        # Εβδομαδιαία αναφορά κάθε Σάββατο (0 = Κυριακή ... 6 = Σάββατο), δύο ώρες μετά την πρωινή σάρωση
        app.job_queue.run_daily(weekly_job, time=dtime((hh + 2) % 24, mm, tzinfo=timezone.utc), days=(6,))
    else:
        minutes = alerts["scan_every_minutes"]
        app.job_queue.run_repeating(scheduled_scan, interval=minutes * 60, first=10)
        when_text = f"κάθε {minutes} λεπτά"
    app.job_queue.run_repeating(event_alerts, interval=15 * 60, first=30)
    app.job_queue.run_repeating(check_trades, interval=10 * 60, first=60)
    log.info("Το bot ξεκίνησε. Σάρωση watchlist (%s) %s.", alerts["timeframe"], when_text)
    # bootstrap_retries=-1: αν το Telegram δεν απαντάει στην εκκίνηση, ξαναδοκιμάζει μέχρι να συνδεθεί
    app.run_polling(bootstrap_retries=-1)


def run_forever(delay: int = 30):
    """Αν το bot σταματήσει από απρόβλεπτο σφάλμα, ξαναξεκινάει μόνο του σε `delay` δευτερόλεπτα, αντί να
    μείνει κλειστό ως το πρωί (χωρίς σάρωση και χωρίς ανέβασμα των stop). Ctrl+C ή κλείσιμο του παραθύρου
    το σταματάνε κανονικά."""
    while True:
        try:
            main()
            return
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception:
            log.exception("Το bot σταμάτησε από σφάλμα. Ξαναξεκινάει μόνο του σε %d δευτερόλεπτα...", delay)
            time.sleep(delay)
            asyncio.set_event_loop(asyncio.new_event_loop())


if __name__ == "__main__":
    run_forever()
