"""Γρήγορη ανάλυση από το terminal.

Παραδείγματα:
    python cli.py BTC
    python cli.py AAPL 1d
    python cli.py ETH 1h --no-news
    python cli.py --review       (έλεγχος αποτελεσμάτων paper trading)
    python cli.py BTC 4h --backtest            (πώς θα είχε πάει η στρατηγική στο παρελθόν)
    python cli.py AAPL 1d --backtest --days 1000
    python cli.py all 4h --backtest            (backtest σε όλη τη watchlist)
    python cli.py --selfcheck                  (αυτοέλεγχος: τωρινοί κανόνες vs εναλλακτικές vs SPY)
"""
import argparse
import sys

from tradebot import backtest, engine, journal, report, selfcheck
from tradebot.config import load_config

sys.stdout.reconfigure(encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description="Ανάλυση μετοχών / crypto")
    ap.add_argument("symbol", nargs="?", help="π.χ. BTC, ETH, AAPL, TSLA")
    ap.add_argument("timeframe", nargs="?", help="15m, 1h, 4h, 1d, 1w")
    ap.add_argument("--no-news", action="store_true", help="χωρίς ειδήσεις (πιο γρήγορο)")
    ap.add_argument("--review", action="store_true", help="έλεγχος & στατιστικά σημάτων")
    ap.add_argument("--backtest", action="store_true", help="δοκιμή της στρατηγικής σε ιστορικά δεδομένα")
    ap.add_argument("--days", type=int, help="πόσες μέρες πίσω για το backtest")
    ap.add_argument("--selfcheck", action="store_true", help="αυτοέλεγχος κανόνων με τα νεότερα δεδομένα")
    args = ap.parse_args()

    if args.selfcheck:
        print("Αυτοέλεγχος: backtest των κανόνων και των εναλλακτικών στη watchlist (μερικά λεπτά)...")
        print(report.html_to_text(selfcheck.to_html(selfcheck.run())))
        return

    if args.review:
        n = journal.evaluate_open()
        s = journal.stats()
        print(f"Έκλεισαν {n} σήματα τώρα. Ανοιχτά: {s['open']}, κλειστά: {s['closed']}")
        if s["closed"]:
            print(f"Win rate: {s['win_rate']:.0f}% · Σύνολο: {s['total_r']:+.2f}R · Μέσος όρος: {s['avg_r']:+.2f}R")
        return
    if args.backtest and (not args.symbol or args.symbol.upper() == "ALL"):
        symbols = load_config()["watchlist"]
        print(f"Backtest σε {len(symbols)} σύμβολα: {', '.join(symbols)}... (μπορεί να πάρει μερικά λεπτά)")
        results, errors = backtest.run_many(symbols, args.timeframe, args.days)
        print(report.html_to_text(report.backtest_many_html(results, errors)))
        return
    if not args.symbol:
        ap.print_help()
        return

    if args.backtest:
        print(f"Backtest {args.symbol.upper()}... (μπορεί να πάρει λίγο χρόνο)")
        print(report.html_to_text(report.backtest_html(backtest.run(args.symbol, args.timeframe, args.days))))
        return

    a = engine.analyze(args.symbol, args.timeframe, with_news=not args.no_news)
    print(report.to_text(a))
    if journal.record(a):
        print("\n(Το σήμα καταγράφηκε στο ημερολόγιο paper trading)")


if __name__ == "__main__":
    main()
