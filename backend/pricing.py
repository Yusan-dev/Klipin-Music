"""Global pricing — multi-currency display for credit packs.

Single source of truth for prices is billing.PACKS (IDR). This module
converts display prices into the visitor's currency. Checkout itself is
still charged in IDR (Stripe/Midtrans), so display rates are informational.

Rates are IDR per unit of foreign currency. Override via env
KLIPIN_FX_USD=16500 etc. — useful when rates move before a deploy.
"""
import os

from billing import PACKS

# IDR per 1 unit of currency (approximate; informational display only).
FX_IDR = {
    "IDR": 1.0,
    "USD": 16500.0,
    "EUR": 17800.0,
    "GBP": 21000.0,
    "SGD": 12300.0,
    "MYR": 3500.0,
    "INR": 198.0,
    "PHP": 285.0,
    "BRL": 3050.0,
}

SYMBOLS = {
    "IDR": "Rp", "USD": "$", "EUR": "€", "GBP": "£", "SGD": "S$",
    "MYR": "RM", "INR": "₹", "PHP": "₱", "BRL": "R$",
}


def _rate(cur: str) -> float:
    env = os.environ.get(f"KLIPIN_FX_{cur.upper()}")
    if env:
        try:
            r = float(env)
            if r > 0:
                return r
        except ValueError:
            pass
    return FX_IDR.get(cur.upper(), FX_IDR["USD"])


def currencies() -> list[str]:
    return list(FX_IDR.keys())


def _display_price(price_idr: float, cur: str) -> str:
    """Foreign price, rounded UP to 2 decimals (never show more than IDR)."""
    rate = _rate(cur)
    if cur == "IDR":
        return f"Rp{int(round(price_idr / 1000))}rb"
    value = price_idr / rate
    value = round(value + 0.005, 2)  # ceil-ish to 2dp
    sym = SYMBOLS.get(cur, cur)
    if value == int(value):
        return f"{sym}{int(value)}"
    return f"{sym}{value:.2f}"


def packs_in_currency(cur: str) -> dict:
    cur = (cur or "USD").upper()
    if cur not in FX_IDR:
        cur = "USD"
    return {
        "currency": cur,
        "base_currency": "IDR",
        "checkout_currency": "IDR",
        "currencies": currencies(),
        "packs": [
            {
                "id": p["id"],
                "name": p["name"],
                "credits": p["credits"],
                "price_idr": p["price_idr"],
                "price": _display_price(p["price_idr"], cur),
                "price_per_credit": _display_price(
                    p["price_idr"] / p["credits"], cur),
            }
            for p in PACKS.values()
        ],
    }
