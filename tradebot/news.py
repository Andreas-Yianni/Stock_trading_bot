"""Ειδήσεις από δωρεάν πηγές (RSS) + ανάλυση συναισθήματος (sentiment) με VADER."""
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import requests
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from . import market
from .cache import cached
from .market import Asset

# Λέξεις της αγοράς που το γενικό VADER δεν καταλαβαίνει σωστά
FINANCE_LEXICON = {
    "bullish": 2.5, "bearish": -2.5, "surge": 2.0, "surges": 2.0, "soar": 2.2, "soars": 2.2,
    "rally": 1.8, "rallies": 1.8, "jump": 1.5, "jumps": 1.5, "gain": 1.2, "gains": 1.2,
    "beat": 1.5, "beats": 1.5, "upgrade": 2.0, "upgraded": 2.0, "outperform": 1.8,
    "record": 1.2, "breakout": 1.5, "approval": 1.8, "approved": 1.8, "inflows": 1.5,
    "plunge": -2.5, "plunges": -2.5, "crash": -3.0, "crashes": -3.0, "tumble": -2.0,
    "tumbles": -2.0, "slump": -2.0, "drop": -1.3, "drops": -1.3, "fall": -1.2, "falls": -1.2,
    "miss": -1.5, "misses": -1.5, "downgrade": -2.0, "downgraded": -2.0, "underperform": -1.8,
    "lawsuit": -1.8, "probe": -1.5, "hack": -2.8, "hacked": -2.8, "exploit": -2.5,
    "liquidation": -2.0, "liquidations": -2.0, "outflows": -1.5, "selloff": -2.2,
    "sell-off": -2.2, "bankruptcy": -3.0, "fraud": -3.0, "recession": -2.0, "layoffs": -1.5,
    "slides": -1.5, "slide": -1.5, "sinks": -1.8, "dips": -1.0, "slips": -1.0, "hike": -1.0,
    "climbs": 1.3, "rises": 1.2, "rebounds": 1.5, "rebound": 1.5, "recovers": 1.3, "adoption": 1.2,
}

_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update(FINANCE_LEXICON)

CRYPTO_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]
# Γενικές ειδήσεις αγοράς/οικονομίας (Fed, πληθωρισμός, δασμοί...) που επηρεάζουν όλα τα σύμβολα
MARKET_FEEDS = [
    ("CNBC", "https://www.cnbc.com/id/20910258/device/rss/rss.html"),         # Economy
    ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),        # Top news
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    (None, "https://news.google.com/rss/search?q=%22Federal+Reserve%22+OR+inflation+OR+tariffs+OR+%22stock+market%22"
           "+OR+recession+when:1d&hl=en-US&gl=US&ceid=US:en"),
]
HEADERS = {"User-Agent": "Mozilla/5.0 (trading-analysis-bot)"}


@dataclass
class NewsItem:
    title: str
    link: str
    source: str
    published: float  # unix time
    sentiment: float  # -1 (πολύ αρνητικό) έως +1 (πολύ θετικό)


def _parse(url: str) -> list:
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        return feedparser.parse(resp.content).entries
    except requests.RequestException:
        return []


def _ts(entry) -> float:
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return time.mktime(t) if t else time.time()


def get_news(asset: Asset, max_age_hours: int = 72, limit: int = 15) -> list[NewsItem]:
    if asset.kind == "crypto":
        query = f"{asset.name} crypto" if asset.name != asset.symbol else f"{asset.symbol} crypto"
    else:
        name = market.company_name(asset.symbol)
        query = f"{name} {asset.symbol} stock" if name != asset.symbol else f"{asset.symbol} stock"
    urls = [f"https://news.google.com/rss/search?q={quote_plus(query)}+when:3d&hl=en-US&gl=US&ceid=US:en"]
    if asset.kind == "stock":
        urls.append(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={asset.symbol}&region=US&lang=en-US")
    else:
        urls += CRYPTO_FEEDS

    keywords = {asset.symbol.lower(), asset.name.lower()}
    if asset.kind == "stock":
        keywords.add(market.company_name(asset.symbol).split()[0].lower())
    cutoff = time.time() - max_age_hours * 3600
    seen, items = set(), []
    for url in urls:
        general_feed = url in CRYPTO_FEEDS or "yahoo.com" in url
        for e in _parse(url):
            title = e.get("title", "").strip()
            source = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else None
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]  # Το Google News προσθέτει " - Πηγή" στον τίτλο
            if not title or title.lower() in seen:
                continue
            if general_feed and not any(k in title.lower() for k in keywords):
                continue
            ts = _ts(e)
            if ts < cutoff:
                continue
            seen.add(title.lower())
            items.append(NewsItem(
                title=title,
                link=e.get("link", ""),
                source=source or url.split("/")[2],
                published=ts,
                sentiment=_analyzer.polarity_scores(title)["compound"],
            ))
    items.sort(key=lambda n: n.published, reverse=True)
    return items[:limit]


def _fetch_market_news(max_age_hours: int, limit: int) -> list[NewsItem]:
    cutoff = time.time() - max_age_hours * 3600
    seen, items = set(), []
    for name, url in MARKET_FEEDS:
        for e in _parse(url):
            title = e.get("title", "").strip()
            source = e.get("source", {}).get("title") if isinstance(e.get("source"), dict) else name
            if source and title.endswith(f" - {source}"):
                title = title[: -len(source) - 3]
            ts = _ts(e)
            if not title or title.lower() in seen or ts < cutoff:
                continue
            seen.add(title.lower())
            items.append(NewsItem(title, e.get("link", ""), source or url.split("/")[2], ts,
                                  _analyzer.polarity_scores(title)["compound"]))
    items.sort(key=lambda n: n.published, reverse=True)
    return items[:limit]


def market_news(max_age_hours: int = 24, limit: int = 20) -> list[NewsItem]:
    """Γενικές ειδήσεις αγοράς του τελευταίου 24ώρου (cache 15 λεπτά)."""
    return cached("market_news", lambda: _fetch_market_news(max_age_hours, limit), 900)


def news_score(items: list[NewsItem]) -> float | None:
    """Μέσο sentiment, με μεγαλύτερο βάρος στις πιο πρόσφατες ειδήσεις. None αν δεν υπάρχουν."""
    if not items:
        return None
    now = time.time()
    weights = [1 / (1 + (now - n.published) / 86400) for n in items]
    return sum(w * n.sentiment for w, n in zip(weights, items)) / sum(weights)


def fear_greed_history() -> pd.DataFrame:
    """Όλο το ιστορικό του Fear & Greed (μία τιμή τη μέρα, από το 2018). Για το backtest."""
    data = requests.get("https://api.alternative.me/fng/?limit=0", timeout=20).json()["data"]
    df = pd.DataFrame({
        "ts": pd.to_datetime([int(d["timestamp"]) for d in data], unit="s", utc=True),
        "value": [int(d["value"]) for d in data],
        "label": [d["value_classification"] for d in data],
    })
    return df.set_index("ts").sort_index()


def fear_greed() -> dict | None:
    """Crypto Fear & Greed Index (0 = ακραίος φόβος, 100 = ακραία απληστία)."""
    try:
        d = requests.get("https://api.alternative.me/fng/", timeout=10).json()["data"][0]
        return {"value": int(d["value"]), "label": d["value_classification"]}
    except Exception:
        return None
