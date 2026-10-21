# 📈 Trading Analyst Bot

[![CI](https://github.com/Andreas-Yianni/Stock_trading_bot/actions/workflows/ci.yml/badge.svg)](https://github.com/Andreas-Yianni/Stock_trading_bot/actions/workflows/ci.yml)

Αναλύει μετοχές και crypto (τεχνική ανάλυση + ειδήσεις) και προτείνει **ενδεικτικά** σημεία εισόδου, stop loss, take profit και μέγεθος θέσης. **Δεν κάνει trades.**

> ⚠️ Καμία ανάλυση δεν προβλέπει την αγορά με σιγουριά. Χρησιμοποίησέ το για paper trading πρώτα.

## Εγκατάσταση (μία φορά)

Άνοιξε PowerShell μέσα στον φάκελο `Trading bot`:

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env
```

## 1) Γρήγορη ανάλυση από το terminal

```powershell
.\.venv\Scripts\python cli.py BTC          # crypto, timeframe από το config (4h)
.\.venv\Scripts\python cli.py AAPL 1d      # μετοχή, ημερήσιο
.\.venv\Scripts\python cli.py --review     # αποτελέσματα paper trading
.\.venv\Scripts\python cli.py BTC 4h --backtest            # πώς θα είχε πάει η στρατηγική τα τελευταία 3 χρόνια
.\.venv\Scripts\python cli.py AAPL 1d --backtest --days 1000
.\.venv\Scripts\python cli.py all 4h --backtest            # backtest σε όλη τη watchlist
```

## 2) Dashboard (στον browser)

```powershell
.\.venv\Scripts\streamlit run dashboard.py
```
Ανοίγει στο http://localhost:8501 με chart, ανάλυση, ειδήσεις, σάρωση watchlist και ημερολόγιο.

## 3) Telegram bot (στο κινητό)

1. Στο Telegram βρες τον **@BotFather**, στείλε `/newbot` και ακολούθησε τα βήματα. Θα σου δώσει ένα **token**.
2. Βάλε το token στο `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC...`
3. Τρέξε: `.\.venv\Scripts\python telegram_bot.py`
4. Στείλε `/start` στο bot σου. Θα σου απαντήσει με το **chat id** σου.
5. Βάλε το chat id στο `.env` (`TELEGRAM_CHAT_ID=...`) και ξανατρέξε το bot. Από εδώ και πέρα μόνο εσύ μπορείς να το χρησιμοποιείς και θα σου στέλνει **ειδοποιήσεις** όταν κάποιο σύμβολο της watchlist βγάλει νέο σήμα.

Εντολές: `/a BTC`, `/a TSLA 1d`, `/news ETH`, `/scan`, `/watch SOL`, `/unwatch SOL`, `/balance 2000`, `/risk 1`, `/stats`, `/backtest BTC 4h`, `/backtest all`, `/macro`, `/trades`, `/close`, `/cancel`, `/mystats`

> Το bot δουλεύει μόνο όσο είναι ανοιχτό το παράθυρο και ο υπολογιστής.

**Πιο εύκολα:** διπλό κλικ στο `start_bot.bat` (Telegram bot) ή στο `start_dashboard.bat` (dashboard).

## Ρυθμίσεις: `config.yaml`

- `account.balance`: το κεφάλαιό σου
- `account.risk_per_trade_pct`: % ρίσκο ανά trade (προτείνεται 0.5-1%)
- `watchlist`: τα σύμβολα που παρακολουθεί
- `alerts.scan_every_minutes`: κάθε πόσο σαρώνει
- `backtest.fee_pct`: προμήθεια ανά συναλλαγή που αφαιρείται στο backtest

## Πώς να διαβάζεις την ανάλυση

| Όρος | Τι σημαίνει |
|---|---|
| **LONG** | Αγοράζεις, περιμένοντας άνοδο |
| **SHORT** | Ποντάρεις σε πτώση. Για αρχάριους: απλά **μην αγοράσεις** |
| **Stop loss (SL)** | Η τιμή όπου κλείνεις με μικρή ζημιά. **Πάντα** να το βάζεις |
| **Trailing stop** | Το stop που ανεβαίνει μαζί με την τιμή (6 ATR κάτω από το υψηλότερο σημείο) και δεν κατεβαίνει ποτέ. Η προεπιλογή (`analysis.exit_mode: trend`): χωρίς take profit, η θέση κλείνει μόνο στο stop |
| **TP1 / TP2** | Στόχοι κέρδους των παλιών κανόνων (`exit_mode: fixed`). Στο TP1 κλείνεις το μισό και πας το SL στην είσοδο |
| **R:R 1:2** | Ρισκάρεις 1 για να κερδίσεις 2 |
| **Σκορ** | -100 (πολύ bearish) έως +100 (πολύ bullish). Κάτω από ±30 = καμία θέση |
| **RSI** | <30 υπερπουλημένο. Πάνω από 70 σημαίνει δυνατό momentum (στο backtest η τιμή συνέχιζε να ανεβαίνει) |
| **EMA200** | Πάνω από αυτόν = μακροπρόθεσμα ανοδική τάση |

## Επόμενα βήματα

Δες το [PLAN.md](PLAN.md) (AI ανάλυση με Claude, X/Twitter, backtesting κ.λπ.).
