"""Εικόνες chart (PNG) για αποστολή στο Telegram: ανάλυση και backtest."""
import io

import matplotlib

matplotlib.use("Agg")
import mplfinance as mpf
import pandas as pd

from .engine import Analysis
from .market import TF_DELTA


WIN, LOSS, LONG_C, SHORT_C = "#27ae60", "#e74c3c", "#2980b9", "#e67e22"


def _naive(ts):
    return ts.tz_convert(None)


def _draw_trades(ax, r, trades, levels: bool = False):
    """▲ είσοδος LONG, ▼ είσοδος SHORT, ● έξοδος (πράσινο = κέρδος, κόκκινο = ζημιά)."""
    bar = TF_DELTA[r.timeframe]
    for t in trades:
        x0, x1 = _naive(t.opened - bar), _naive(t.exit_at)
        col = WIN if t.r > 0 else LOSS
        ax.plot([x0, x1], [t.entry, t.exit_price], color=col, linewidth=0.8, alpha=0.7)
        if levels:
            ax.hlines([t.stop_loss], x0, x1, colors=LOSS, linestyles=":", linewidth=0.9)
            if t.tp1 is not None:
                ax.hlines([t.tp1, t.tp2], x0, x1, colors=WIN, linestyles=":", linewidth=0.9)
        ax.scatter([x0], [t.entry], marker="^" if t.side == "LONG" else "v", s=42 if levels else 22,
                   color=LONG_C if t.side == "LONG" else SHORT_C, zorder=3, edgecolors="white", linewidths=0.4)
        ax.scatter([x1], [t.exit_price], marker="o", s=26 if levels else 12, color=col, zorder=3,
                   edgecolors="white", linewidths=0.4)


def backtest_png(r, zoom_days: int = 90) -> bytes:
    """Εικόνα backtest: όλα τα trades πάνω στην τιμή, zoom στους τελευταίους μήνες, και το κεφάλαιο."""
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D

    df = r.df.copy()
    df.index = df.index.tz_convert(None)
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(11, 11), gridspec_kw={"height_ratios": [3, 3, 2]})

    ax1.plot(df.index, df["close"], color="#7f8c8d", linewidth=0.8)
    ax1.plot(df.index, df["ema200"], color="#8e44ad", linewidth=0.8, alpha=0.7)
    _draw_trades(ax1, r, r.trades)
    ax1.set_title(f"Backtest {r.asset.symbol} {r.timeframe}: όλα τα trades ({len(r.trades)})")

    cut = r.end - pd.Timedelta(days=zoom_days)
    z = df[df.index >= _naive(cut)]
    ax2.plot(z.index, z["close"], color="#34495e", linewidth=1)
    ax2.plot(z.index, z["ema20"], color="#2b8cbe", linewidth=0.8, alpha=0.8)
    ax2.plot(z.index, z["ema50"], color="#f39c12", linewidth=0.8, alpha=0.8)
    _draw_trades(ax2, r, [t for t in r.trades if t.exit_at >= cut], levels=True)
    ax2.set_xlim(z.index[0], z.index[-1])
    ax2.set_title(f"Zoom στις τελευταίες {zoom_days} μέρες (τελείες: SL κόκκινο, TP πράσινο)")

    eq = r.equity.copy()
    eq.index = eq.index.tz_convert(None)
    bh = r.buy_hold.copy()
    bh.index = bh.index.tz_convert(None)
    ax3.step(eq.index, eq.values, where="post", color=LONG_C, linewidth=1.8, label="Στρατηγική")
    ax3.plot(bh.index, bh.values, color="#95a5a6", linewidth=1, label="Buy & hold")
    ax3.axhline(r.balance, color="#7f8c8d", linewidth=0.6)
    ax3.set_title(f"Κεφάλαιο με {r.risk_pct:g}% ρίσκο ανά trade")
    ax3.legend(loc="upper left")

    legend = [
        Line2D([], [], marker="^", color=LONG_C, linestyle="", label="Είσοδος LONG"),
        Line2D([], [], marker="v", color=SHORT_C, linestyle="", label="Είσοδος SHORT"),
        Line2D([], [], marker="o", color=WIN, linestyle="", label="Έξοδος με κέρδος"),
        Line2D([], [], marker="o", color=LOSS, linestyle="", label="Έξοδος με ζημιά"),
    ]
    ax1.legend(handles=legend, loc="upper left", fontsize=8, ncol=4)
    for ax in (ax1, ax2, ax3):
        ax.grid(alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100)
    plt.close(fig)
    return buf.getvalue()


def render_png(a: Analysis, bars: int = 120) -> bytes:
    df = a.df.tail(bars).copy()
    df.index = df.index.tz_convert(None)
    df = df.rename(columns={"open": "Open", "high": "High", "low": "Low", "close": "Close", "volume": "Volume"})

    adds = [
        mpf.make_addplot(df["ema20"], color="#2b8cbe", width=1),
        mpf.make_addplot(df["ema50"], color="#f39c12", width=1),
        mpf.make_addplot(df["ema200"], color="#8e44ad", width=1.2),
    ]
    lines, colors, styles = [], [], []
    for lvl, _ in a.levels["support"]:
        lines.append(lvl); colors.append("#27ae60"); styles.append(":")
    for lvl, _ in a.levels["resistance"]:
        lines.append(lvl); colors.append("#c0392b"); styles.append(":")
    if a.plan:
        for lvl, col in [(a.plan.entry, "#2980b9"), (a.plan.stop_loss, "#e74c3c"),
                         (a.plan.tp1, "#2ecc71"), (a.plan.tp2, "#16a085")]:
            if lvl is not None:
                lines.append(lvl); colors.append(col); styles.append("-")

    buf = io.BytesIO()
    mpf.plot(
        df, type="candle", style="yahoo", addplot=adds, volume=True,
        hlines=dict(hlines=lines, colors=colors, linestyle=styles, linewidths=1) if lines else None,
        title=f"{a.asset.symbol} {a.timeframe}  |  EMA20 / EMA50 / EMA200",
        figsize=(11, 6.5), tight_layout=True, savefig=dict(fname=buf, format="png", dpi=110),
    )
    return buf.getvalue()
