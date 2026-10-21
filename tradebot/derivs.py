"""Δεδομένα παραγώγων για crypto από τα δημόσια futures του Binance (χωρίς λογαριασμό).

Funding rate: κάθε 8 ώρες όσοι έχουν LONG πληρώνουν όσους έχουν SHORT (ή αντίστροφα αν είναι αρνητικό).
Πολύ θετικό funding = «όλοι» είναι LONG με δανεικά: η αγορά είναι υπερφορτωμένη προς τα πάνω.
Μελέτη (20 σύμβολα, 4h/1d): το funding ΔΕΝ προέβλεπε σταθερά την επόμενη κίνηση (άλλαζε πρόσημο
ανάμεσα σε παλιά και νέα δεδομένα), γι' αυτό δεν μπαίνει στο σκορ. Το open interest δεν μπορεί να
ελεγχθεί: το Binance δίνει μόνο ~30 μέρες ιστορικό.
"""
import pandas as pd
import requests

from .market import Asset

FAPI = "https://fapi.binance.com"


def _pair(asset: Asset) -> str:
    return f"{asset.symbol}USDT"


def funding_history(asset: Asset, since: pd.Timestamp | None = None) -> pd.Series:
    """Ιστορικό funding rate (ανά 8ωρο), με index τη στιγμή που πληρώθηκε."""
    start = int((since or pd.Timestamp("2019-09-01", tz="UTC")).timestamp() * 1000)
    rows = []
    for _ in range(100):  # σελίδες των 1000 (≈ 11 μήνες η καθεμία)
        batch = requests.get(f"{FAPI}/fapi/v1/fundingRate", timeout=15,
                             params={"symbol": _pair(asset), "startTime": start, "limit": 1000}).json()
        if not isinstance(batch, list) or not batch:
            break
        rows += batch
        start = batch[-1]["fundingTime"] + 1
        if len(batch) < 1000:
            break
    if not rows:
        raise ValueError(f"Δεν υπάρχει funding για {_pair(asset)}")
    s = pd.Series([float(r["fundingRate"]) for r in rows],
                  index=pd.to_datetime([r["fundingTime"] for r in rows], unit="ms", utc=True))
    return s[~s.index.duplicated()].sort_index()

