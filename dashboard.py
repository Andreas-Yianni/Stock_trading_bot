"""Web dashboard με διαδραστικά charts.

Εκκίνηση:  streamlit run dashboard.py   (ανοίγει στο http://localhost:8501)
"""
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from tradebot import backtest, broker, engine, events, journal, market, portfolio, replay, report, selfcheck
from tradebot.config import DISCLAIMER, load_config, save_config

st.set_page_config(page_title="Trading Analyst", page_icon="📈", layout="wide")
cfg = load_config()

# ---------- Sidebar ----------
st.sidebar.title("📈 Trading Analyst")
symbol = st.sidebar.text_input("Σύμβολο (π.χ. BTC, ETH, AAPL)", value=cfg["watchlist"][0]).strip()
pick = st.sidebar.selectbox("ή διάλεξε από τη watchlist", ["-"] + cfg["watchlist"])
if pick != "-":
    symbol = pick
tf = st.sidebar.selectbox("Timeframe", market.TIMEFRAMES, index=market.TIMEFRAMES.index(cfg["analysis"]["default_timeframe"]))
with_news = st.sidebar.checkbox("Με ειδήσεις", value=True)

st.sidebar.divider()
st.sidebar.subheader("💰 Λογαριασμός")
bal = st.sidebar.number_input("Κεφάλαιο", value=float(cfg["account"]["balance"]), min_value=0.0, step=100.0)
risk = st.sidebar.slider("Ρίσκο ανά trade (%)", 0.25, 5.0, float(cfg["account"]["risk_per_trade_pct"]), 0.25)
if risk > 2:
    st.sidebar.warning("Πάνω από 2% ρίσκο ανά trade είναι επιθετικό.")
if bal != cfg["account"]["balance"] or risk != cfg["account"]["risk_per_trade_pct"]:
    cfg["account"]["balance"], cfg["account"]["risk_per_trade_pct"] = bal, risk
    save_config(cfg)


@st.cache_data(ttl=300, show_spinner=False)
def run(sym: str, timeframe: str, news_on: bool, _bal: float, _risk: float):
    return engine.analyze(sym, timeframe, with_news=news_on)


tab_an, tab_scan, tab_journal, tab_res, tab_bt = st.tabs(["Ανάλυση", "Σάρωση watchlist", "Paper trading",
                                                           "Αποτελέσματα", "Backtest"])

# ---------- Ανάλυση ----------
with tab_an:
    try:
        with st.spinner(f"Αναλύω {symbol.upper()}..."):
            a = run(symbol, tf, with_news, bal, risk)
    except Exception as e:
        st.error(str(e))
        st.stop()

    color = {"LONG": "green", "SHORT": "red", "NEUTRAL": "gray"}[a.bias]
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"{a.asset.symbol} · {tf}", report.fmt_price(a.price))
    c2.markdown(f"### :{color}[{a.bias}]")
    c3.metric("Σκορ", f"{a.score:+.0f} / 100")
    c4.metric("Βεβαιότητα", a.confidence)
    st.caption(report.bias_text(a))

    df = a.df.tail(200)
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, row_heights=[0.6, 0.2, 0.2], vertical_spacing=0.03)
    fig.add_trace(go.Candlestick(x=df.index, open=df.open, high=df.high, low=df.low, close=df.close, name="Τιμή"), 1, 1)
    for col, c in [("ema20", "#2b8cbe"), ("ema50", "#f39c12"), ("ema200", "#8e44ad")]:
        fig.add_trace(go.Scatter(x=df.index, y=df[col], name=col.upper(), line=dict(color=c, width=1)), 1, 1)
    for lvl, _ in a.levels["support"]:
        fig.add_hline(y=lvl, line=dict(color="green", dash="dot", width=1), row=1, col=1)
    for lvl, _ in a.levels["resistance"]:
        fig.add_hline(y=lvl, line=dict(color="red", dash="dot", width=1), row=1, col=1)
    if a.plan:
        for lvl, name, c in [(a.plan.entry, "Είσοδος", "#2980b9"), (a.plan.stop_loss, "SL", "#e74c3c"),
                             (a.plan.tp1, "TP1", "#2ecc71"), (a.plan.tp2, "TP2", "#16a085")]:
            if lvl is not None:
                fig.add_hline(y=lvl, line=dict(color=c, width=1.5), annotation_text=name, row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df.rsi, name="RSI", line=dict(color="#555")), 2, 1)
    fig.add_hline(y=70, line=dict(color="red", dash="dot", width=1), row=2, col=1)
    fig.add_hline(y=30, line=dict(color="green", dash="dot", width=1), row=2, col=1)
    fig.add_trace(go.Bar(x=df.index, y=df.macd_hist, name="MACD hist",
                         marker_color=["#2ecc71" if v > 0 else "#e74c3c" for v in df.macd_hist]), 3, 1)
    fig.update_layout(height=750, xaxis_rangeslider_visible=False, margin=dict(l=10, r=10, t=10, b=10),
                      legend=dict(orientation="h", y=1.02))
    st.plotly_chart(fig, width="stretch")

    left, right = st.columns(2)
    with left:
        st.subheader("📊 Τι βλέπω")
        for f in a.factors:
            icon = "🟢" if f.score > 0.1 else "🔴" if f.score < -0.1 else "⚪"
            st.markdown(f"{icon} **{f.name}**: {f.note}")
        st.markdown("**Αντιστάσεις:** " + (", ".join(report.fmt_price(l) for l, _ in a.levels["resistance"]) or "-"))
        st.markdown("**Στηρίξεις:** " + (", ".join(report.fmt_price(l) for l, _ in a.levels["support"]) or "-"))
    with right:
        st.subheader("🎯 Ενδεικτικό πλάνο")
        p = a.plan
        if p:
            cur = cfg["account"]["currency"]
            st.table(pd.DataFrame({
                "": ["Πλευρά", "Είσοδος", "Stop loss", *(["Take profit 1", "Take profit 2"] if p.tp1 is not None
                                                          else ["Έξοδος", "Take profit"]),
                     "Ποσότητα", "Αξία θέσης", "Ρίσκο"],
                "Τιμή": [p.side, report.fmt_price(p.entry) + (f" (pullback ~{report.fmt_price(p.alt_entry)})" if p.alt_entry else ""),
                         f"{report.fmt_price(p.stop_loss)} ({abs(p.stop_loss - p.entry) / p.entry * 100:.1f}%)",
                         *([f"{report.fmt_price(p.tp1)} (1:{p.rr_tp1:.1f})", f"{report.fmt_price(p.tp2)} (1:{p.rr_tp2:.1f})"]
                           if p.tp1 is not None else [f"Trailing stop {p.trend_atr:g} ATR", "Κανένα (μένει όσο κρατά η τάση)"]),
                         f"{p.qty:.6g}", f"{p.position_value:,.2f} {cur}", f"{p.risk_amount:,.2f} {cur}"],
            }).set_index(""))
            for n in p.notes:
                st.markdown(f"• {n}")
            if st.button("📒 Κατέγραψε στο paper trading"):
                st.success("Καταγράφηκε." if journal.record(a) else "Υπάρχει ήδη ανοιχτό σήμα για αυτό.")
        else:
            st.info("Δεν υπάρχει αρκετά καθαρό σήμα. Η αναμονή είναι κι αυτή θέση.")

    if a.fear_greed:
        st.markdown(f"😨 **Fear & Greed:** {a.fear_greed['value']}/100 ({a.fear_greed['label']})")
    if a.news:
        st.subheader("📰 Ειδήσεις")
        for n in a.news:
            icon = "🟢" if n.sentiment > 0.15 else "🔴" if n.sentiment < -0.15 else "⚪"
            when = datetime.fromtimestamp(n.published).strftime("%d/%m %H:%M")
            st.markdown(f"{icon} [{n.title}]({n.link}) · *{n.source}, {when}*")
    if a.events:
        st.subheader("📅 Επερχόμενα γεγονότα")
        for w in events.risk_warnings(a.events):
            st.warning(w)
        st.dataframe(pd.DataFrame([{
            "Πότε": x.when_local(), "Γεγονός": x.title, "Πρόβλεψη": x.forecast, "Προηγούμενο": x.previous,
        } for x in a.events]), width="stretch", hide_index=True)
    if a.market_news:
        st.subheader("🌍 Γενικές ειδήσεις αγοράς")
        st.caption("Fed, πληθωρισμός, δασμοί κ.λπ. Επηρεάζουν όλη την αγορά. Δεν μπαίνουν στο σκορ.")
        for n in a.market_news[:8]:
            when = datetime.fromtimestamp(n.published).strftime("%d/%m %H:%M")
            st.markdown(f"• [{n.title}]({n.link}) · *{n.source}, {when}*")
    for w in a.warnings:
        st.warning(w)
    st.caption(DISCLAIMER)

# ---------- Σάρωση ----------
with tab_scan:
    st.write("Γρήγορη τεχνική σάρωση όλης της watchlist (χωρίς ειδήσεις).")
    scan_tf = st.selectbox("Timeframe σάρωσης", market.TIMEFRAMES, index=2, key="scan_tf")
    if st.button("▶️ Σάρωση"):
        rows = []
        prog = st.progress(0.0)
        for i, sym in enumerate(cfg["watchlist"]):
            try:
                r = run(sym, scan_tf, False, bal, risk)
                rows.append({"Σύμβολο": r.asset.symbol, "Τιμή": report.fmt_price(r.price), "Σήμα": r.bias,
                             "Σκορ": round(r.score), "RSI": round(r.df.rsi.iloc[-1]),
                             "SL": report.fmt_price(r.plan.stop_loss) if r.plan else "",
                             "TP1": report.fmt_price(r.plan.tp1) if r.plan else ""})
            except Exception as e:
                rows.append({"Σύμβολο": sym, "Σήμα": f"Σφάλμα: {e}"})
            prog.progress((i + 1) / len(cfg["watchlist"]))
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    new = st.text_input("Πρόσθεσε σύμβολο στη watchlist")
    if st.button("➕ Πρόσθεση") and new:
        cfg["watchlist"].append(new.strip().upper())
        save_config(cfg)
        st.rerun()

# ---------- Paper trading ----------
with tab_journal:
    if st.button("🔄 Έλεγχος αποτελεσμάτων"):
        n = journal.evaluate_open()
        st.success(f"Ενημερώθηκαν {n} σήματα.")
    s = journal.stats()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Ανοιχτά", s["open"])
    c2.metric("Κλειστά", s["closed"])
    if s["closed"]:
        c3.metric("Win rate", f"{s['win_rate']:.0f}%")
        c4.metric("Σύνολο R", f"{s['total_r']:+.2f}")
    st.caption("R = πόσες φορές το ρίσκο σου κέρδισες/έχασες. Χρειάζονται 30+ κλειστά σήματα για να βγάλεις συμπέρασμα.")
    sig = journal.all_signals()
    if sig:
        st.dataframe(pd.DataFrame(sig), width="stretch", hide_index=True)

# ---------- Backtest ----------
with tab_bt:
    st.write("Πώς θα είχε πάει η στρατηγική στο παρελθόν, με τους ίδιους κανόνες "
             "(τεχνική ανάλυση + Fear & Greed, χωρίς ειδήσεις).")
    bt_all = st.checkbox(f"Όλη η watchlist ({', '.join(cfg['watchlist'])})", key="bt_all")
    if not bt_all:
        st.caption(f"Σύμβολο: **{symbol.upper()}** (αλλάζει από την αριστερή στήλη)")
    b1, b2 = st.columns(2)
    bt_tf = b1.selectbox("Timeframe", market.TIMEFRAMES, index=market.TIMEFRAMES.index(tf), key="bt_tf")
    bt_days = b2.number_input("Μέρες πίσω", min_value=30, max_value=3650,
                              value=backtest.DEFAULT_DAYS[bt_tf], step=30, key=f"bt_days_{bt_tf}")
    if st.button("▶️ Τρέξε backtest"):
        st.session_state.pop("bt", None)
        st.session_state.pop("bt_many", None)
        if bt_all:
            prog = st.progress(0.0, text="Τρέχει το backtest σε όλη τη watchlist... (μερικά λεπτά)")
            st.session_state["bt_many"] = backtest.run_many(cfg["watchlist"], bt_tf, int(bt_days), prog.progress)
            prog.empty()
        else:
            try:
                with st.spinner("Τρέχει το backtest... (έως 1 λεπτό)"):
                    st.session_state["bt"] = backtest.run(symbol, bt_tf, int(bt_days))
            except Exception as e:
                st.error(str(e))
    many = st.session_state.get("bt_many")
    if many:
        results, errors = many
        trades = [t for res in results for t in res.trades]
        s = backtest.stats(trades)
        if s["n"]:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Trades", s["n"])
            c2.metric("Win rate", f"{s['win_rate']:.0f}%")
            c3.metric("Σύνολο R", f"{s['total_r']:+.1f}", f"Μ.Ο. {s['avg_r']:+.2f}R")
            c4.metric("Profit factor", f"{s['profit_factor']:.2f}")
            cum = pd.Series([t.r for t in sorted(trades, key=lambda t: t.closed)],
                            index=[t.closed for t in sorted(trades, key=lambda t: t.closed)]).cumsum()
            fig = go.Figure(go.Scatter(x=cum.index, y=cum.values, name="Σύνολο R",
                                       line=dict(color="#2980b9", width=2, shape="hv")))
            fig.update_layout(height=350, margin=dict(l=10, r=10, t=10, b=10), yaxis_title="Αθροιστικό R")
            st.plotly_chart(fig, width="stretch")
        st.markdown(report.backtest_many_html(results, errors).replace("\n", "  \n"), unsafe_allow_html=True)
    r = st.session_state.get("bt")
    if r:
        s = r.stats()
        st.subheader(f"{r.asset.symbol} · {r.timeframe} · {r.start:%d/%m/%Y} έως {r.end:%d/%m/%Y}")
        if s["n"]:
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Trades", s["n"])
            c2.metric("Win rate", f"{s['win_rate']:.0f}%")
            c3.metric("Σύνολο R", f"{s['total_r']:+.1f}", f"Μ.Ο. {s['avg_r']:+.2f}R")
            c4.metric("Κεφάλαιο", f"{r.final_balance:,.0f}", f"{(r.final_balance / r.balance - 1) * 100:+.1f}%")
            c5.metric("Buy & hold", f"{r.buy_hold_pct:+.1f}%")
        sub_chart, sub_replay, sub_report = st.tabs(["📈 Trades στο chart", "▶️ Replay", "📋 Αναφορά"])

        with sub_chart:
            if not r.trades:
                st.info("Δεν βγήκε κανένα trade.")
            else:
                bar = market.TF_DELTA[r.timeframe]
                labels = [f"#{n + 1} · {t.opened - bar:%d/%m/%Y %H:%M} · {t.side} · {t.status} · {t.r:+.2f}R"
                          for n, t in enumerate(r.trades)]
                pick_t = st.selectbox("Διάλεξε trade για να το δεις από κοντά (ή κάνε zoom με το ποντίκι)",
                                      ["Τελευταίες 90 μέρες"] + labels, key="bt_pick")
                sel = r.trades[labels.index(pick_t)] if pick_t in labels else None

                fig = go.Figure(go.Candlestick(x=r.df.index, open=r.df.open, high=r.df.high, low=r.df.low,
                                               close=r.df.close, name="Τιμή", hoverinfo="x+y"))
                for col, c in [("ema20", "#2b8cbe"), ("ema50", "#f39c12"), ("ema200", "#8e44ad")]:
                    fig.add_trace(go.Scatter(x=r.df.index, y=r.df[col], name=col.upper(),
                                             line=dict(color=c, width=1), hoverinfo="skip"))
                for won, color, name in [(True, "#27ae60", "Έξοδος με κέρδος"), (False, "#e74c3c", "Έξοδος με ζημιά")]:
                    group = [t for t in r.trades if (t.r > 0) == won]
                    xs, ys = [], []
                    for t in group:
                        xs += [t.opened - bar, t.exit_at, None]
                        ys += [t.entry, t.exit_price, None]
                    fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=color, width=1, dash="dot"),
                                             hoverinfo="skip", showlegend=False))
                    fig.add_trace(go.Scatter(
                        x=[t.exit_at for t in group], y=[t.exit_price for t in group], mode="markers", name=name,
                        marker=dict(color=color, size=9, line=dict(color="white", width=1)),
                        hovertext=[f"{t.status} · {t.r:+.2f}R" for t in group], hoverinfo="text"))
                for side, symbol_, color in [("LONG", "triangle-up", "#2980b9"), ("SHORT", "triangle-down", "#e67e22")]:
                    group = [t for t in r.trades if t.side == side]
                    fig.add_trace(go.Scatter(
                        x=[t.opened - bar for t in group], y=[t.entry for t in group], mode="markers",
                        name=f"Είσοδος {side}", marker=dict(symbol=symbol_, color=color, size=12,
                                                             line=dict(color="white", width=1)),
                        hovertext=[f"{side} · σκορ {t.score:+.0f}<br>Είσοδος {report.fmt_price(t.entry)}<br>"
                                   f"SL {report.fmt_price(t.stop_loss)}"
                                   + (f" · TP1 {report.fmt_price(t.tp1)}" if t.tp1 is not None else "") for t in group],
                        hoverinfo="text"))
                if sel:
                    x0, x1 = sel.opened - bar, sel.exit_at
                    for y, c, name in [(sel.entry, "#2980b9", "Είσοδος"), (sel.stop_loss, "#e74c3c", "SL"),
                                       (sel.tp1, "#27ae60", "TP1"), (sel.tp2, "#16a085", "TP2")]:
                        if y is None:
                            continue
                        fig.add_shape(type="line", x0=x0, x1=x1, y0=y, y1=y, line=dict(color=c, width=1.5))
                        fig.add_annotation(x=x1, y=y, text=f"{name} {report.fmt_price(y)}", showarrow=False,
                                           xanchor="left", font=dict(color=c, size=11))
                    pad = (x1 - x0) * 0.6 + bar * 30
                    rng = [x0 - pad, x1 + pad]
                else:
                    rng = [r.end - pd.Timedelta(days=90), r.end + bar]
                view = r.df[(r.df.index >= rng[0]) & (r.df.index <= rng[1])]
                yr = [view.low.min() * 0.98, view.high.max() * 1.02] if len(view) else None
                if sel and yr:
                    far = sel.tp2 if sel.tp2 is not None else sel.exit_price
                    yr = [min(yr[0], sel.stop_loss, far) * 0.99, max(yr[1], sel.stop_loss, far) * 1.01]
                fig.update_layout(height=650, xaxis_rangeslider_visible=True, xaxis_range=rng, yaxis_range=yr,
                                  margin=dict(l=10, r=60, t=10, b=10), legend=dict(orientation="h", y=1.04))
                st.plotly_chart(fig, width="stretch")
                st.caption("▲ είσοδος LONG · ▼ είσοδος SHORT · ● έξοδος (πράσινο κέρδος, κόκκινο ζημιά). "
                           "Σύρε τη μπάρα κάτω από το chart ή κάνε zoom με το ποντίκι για άλλη περίοδο.")
                if sel:
                    st.markdown(f"**Γιατί μπήκε {sel.side} (σκορ {sel.score:+.0f}):**")
                    for reason in sel.reasons:
                        st.markdown(f"- {reason}")
                    st.markdown(f"**Πώς έκλεισε:** {report.STATUS_TEXT[sel.status]} → **{sel.r:+.2f}R**")

        with sub_replay:
            if not r.trades:
                st.info("Δεν βγήκε κανένα trade.")
            else:
                st.caption("Πάτα ▶️ Play (ή Space): τα κεριά εμφανίζονται ένα-ένα. Με «Παύση σε νέο trade» σταματάει "
                           "σε κάθε είσοδο, για να διαβάσεις δεξιά γιατί μπήκε.")
                st.iframe(replay.html(r), height=720)

        with sub_report:
            st.markdown(report.backtest_html(r).replace("\n", "  \n"), unsafe_allow_html=True)
            if r.trades:
                st.dataframe(pd.DataFrame([{
                    "Άνοιγμα": t.opened, "Κλείσιμο": t.closed, "Πλευρά": t.side, "Σκορ": round(t.score),
                    "Είσοδος": report.fmt_price(t.entry), "SL": report.fmt_price(t.stop_loss),
                    "TP1": report.fmt_price(t.tp1) if t.tp1 is not None else "-",
                    "Έξοδος": report.fmt_price(t.exit_price) if t.exit_price else "-", "Έκβαση": t.status,
                    "R": round(t.r, 2),
                } for t in r.trades]), width="stretch", hide_index=True)


# ---------- Αποτελέσματα: τα δικά σου trades (TradingView) και τα αυτόματα (Alpaca paper) ----------
with tab_res:
    st.caption("🤖 = αυτόματα στο Alpaca paper · 👤 = δικά σου στο TradingView. R = αποτέλεσμα σε φορές το ρίσκο "
               "(1R = είσοδος − αρχικό stop), όπως στο backtest.")
    cols = st.columns(2)
    for col, (label, is_broker) in zip(cols, (("🤖 Alpaca paper", True), ("👤 TradingView", False))):
        rows = portfolio.closed_trades(broker=is_broker)
        rs = [portfolio.plan_r(t) or 0 for t in rows]
        with col:
            st.subheader(label)
            a, b, c = st.columns(3)
            a.metric("Κλειστά", len(rows))
            b.metric("Κερδισμένα", f"{100 * sum(t['pnl'] > 0 for t in rows) / len(rows):.0f}%" if rows else "-")
            c.metric("Σύνολο", f"{sum(rs):+.1f}R · {sum(t['pnl'] for t in rows):+,.0f}$" if rows else "-")
    st.info(selfcheck.patience_note(len(portfolio.closed_trades(broker=True))))

    hist = broker.equity_history()
    if len(hist) >= 2:
        eq = pd.Series(hist, dtype=float)
        eq.index = pd.to_datetime(eq.index)
        fig = go.Figure(go.Scatter(x=eq.index, y=100 * (eq / eq.iloc[0] - 1), name="Λογαριασμός Alpaca",
                                   line=dict(color="#2980b9", width=2)))
        try:
            spy = market.fetch_ohlcv(market.resolve("SPY.US"), "1d")["close"]
            spy.index = spy.index.tz_convert("America/New_York").normalize().tz_localize(None)
            spy = spy[spy.index >= eq.index[0] - pd.Timedelta(days=4)]
            fig.add_trace(go.Scatter(x=spy.index, y=100 * (spy / spy.iloc[0] - 1), name="SPY (αγοράζω και κρατάω)",
                                     line=dict(color="#7f8c8d", width=1.5, dash="dot")))
        except Exception:
            pass
        fig.update_layout(height=360, yaxis_title="% από την αρχή", margin=dict(l=10, r=10, t=30, b=10),
                          title="Λογαριασμός Alpaca έναντι SPY", legend=dict(orientation="h", y=1.1))
        st.plotly_chart(fig, width="stretch")
    elif broker.configured():
        st.caption("Το γράφημα «λογαριασμός έναντι SPY» εμφανίζεται μόλις μαζευτούν 2 μέρες (γράφεται κάθε πρωί).")

    trades = portfolio.open_trades() + sorted(portfolio.closed_trades(broker=None),
                                              key=lambda t: t["closed_at"] or "", reverse=True)
    if trades:
        st.dataframe(pd.DataFrame([{
            "": "🤖" if t["broker"] else "👤", "#": t["id"], "Σύμβολο": t["symbol"],
            "Άνοιγμα": pd.Timestamp(t["opened_at"]).strftime("%d/%m/%Y"),
            "Κλείσιμο": pd.Timestamp(t["closed_at"]).strftime("%d/%m/%Y") if t["closed_at"] else "ανοιχτό",
            "Είσοδος": report.fmt_price(t["entry"]), "Stop": report.fmt_price(t["stop_loss"]),
            "Έξοδος": report.fmt_price(t["exit_price"]) if t["exit_price"] else "-",
            "R": round(portfolio.plan_r(t), 2) if t["status"] == "CLOSED" and portfolio.plan_r(t) is not None else None,
            "$": round(t["pnl"], 2) if t["status"] == "CLOSED" else None,
        } for t in trades]), width="stretch", hide_index=True)
    else:
        st.caption("Κανένα trade ακόμα.")

    if broker.configured() and st.button("📐 Έλεγχος εκτέλεσης έναντι backtest"):
        with st.spinner("Ξαναπαίζω κάθε σήμα με τους κανόνες του backtest..."):
            ex = broker.execution_summary()
        if ex["n"]:
            st.metric("Μέση διαφορά πραγματικής εκτέλεσης από το backtest",
                      "-" if ex["avg_diff"] is None else f"{ex['avg_diff']:+.2f}R")
            if ex["outcome_mismatch"]:
                st.warning(f"{ex['outcome_mismatch']} trades έκλεισαν αλλιώς απ' ό,τι στο backtest (εκεί είναι ακόμα "
                           f"ανοιχτά ή δεν θα είχαν μπει): κοίταξέ τα.")
            st.dataframe(pd.DataFrame([{
                "#": r["id"], "Σύμβολο": r["symbol"],
                "Πραγματικό R": None if r["live_r"] is None else round(r["live_r"], 2),
                "Backtest R": None if r["bt_r"] is None else round(r["bt_r"], 2),
                "Backtest": {None: "ακόμα ανοιχτό", "SKIP": "χωρίς είσοδο"}.get(r["bt_status"], r["bt_status"]),
                "Διαφορά": None if r["diff_r"] is None else round(r["diff_r"], 2), "Είσοδος (πραγματική / backtest)":
                    f"{report.fmt_price(r['live_fill'])} / {report.fmt_price(r['bt_fill'])}" if r["live_fill"] else "-",
            } for r in ex["rows"]]), width="stretch", hide_index=True)
        else:
            st.caption("Δεν υπάρχει ακόμα κλειστό αυτόματο trade για σύγκριση.")
    st.caption(DISCLAIMER)
