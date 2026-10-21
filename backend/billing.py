"""SaaS billing for Klipin — permanent prepaid credits with Stripe + Midtrans.

Model (ported from the KlipAja/YouClip.id playbook for the Indonesian
market):
  - Credits are PREPAID and PERMANENT: buy a pack once, the credits never
    expire. No monthly subscription, no rollover math.
  - Every new user gets 1 FREE clip (1 credit) — friction-free trial with
    no card required, same as KlipAja/YouClip.id.
  - A credit is reserved when a process job starts, committed on success
    and released on failure — the balance is never double-spent, and a
    failed render automatically refunds the credit (this is marketed).
  - Payments go through Stripe Checkout (cards) or Midtrans Snap (QRIS /
    Indonesian bank transfers / e-wallet). Both use plain httpx — no SDK.
  - Everything is gated by BILLING_ENABLED. When it is unset/false the app
    runs fully free (local-first posture) and the API returns enabled=false.
"""

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

import httpx

import db

BILLING_ENABLED = os.environ.get("BILLING_ENABLED", "").strip().lower() in (
    "1", "true", "yes", "on")
MOCK_PAYMENTS_ENABLED = os.environ.get(
    "MOCK_PAYMENTS_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on")

# Comma-separated emails that bypass credit spend entirely (founders / staff).
# Admin users render freely — no credit reserved, no monthly cap, no duration cap.
ADMIN_EMAILS = {
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "").split(",")
    if e.strip()
}

def is_admin_email(email: str | None) -> bool:
    """True when the email is on the admin allowlist (founder free access)."""
    return bool(email) and str(email).strip().lower() in ADMIN_EMAILS

# ── Free-tier entitlement (single source of truth) ──────────────────────────
# A user who has never purchased a credit pack gets a metered free allowance:
# FREE_MONTHLY_LIMIT generations per calendar month (no credit spend), each
# capped at FREE_MAX_DURATION seconds and producing at most FREE_OUTPUT_CLIPS
# clips. Owning any permanent credit (a purchase) moves the account onto the
# permanent-credit model, which supersedes the free allowance entirely.
#
# There is deliberately NO separate "one-time trial credit": the monthly
# allowance IS the free tier. Any UI copy must describe these same numbers.
FREE_MONTHLY_LIMIT = 3
FREE_MAX_DURATION = 1800  # 30 minutes
FREE_OUTPUT_CLIPS = 3
# Users own FREE_TIER_PURCHASE_THRESHOLD or fewer credits => free tier.
FREE_TIER_PURCHASE_THRESHOLD = 0

# Maximum clips produced from one source. Enforced server-side so a client
# cannot unlock paid output simply by submitting a larger render_count.
PAID_OUTPUT_CLIPS = 6

# Prepaid credit packs (Rp — prices match the Pricing page). Credits are
# permanent: they never expire, unlike the old monthly subscription plans.
PACKS = {
    "mulai": {
        "id": "mulai",
        "name": "Mulai",
        "price_idr": 19000,
        "credits": 10,
        "features": ["10 kredit permanen", "Hingga 6 klip per video",
                     "Hanya render kandidat AI score >90", "Semua mode layout",
                     "Subtitle karaoke + emoji", "Watermark Klipin"],
    },
    "kreator": {
        "id": "kreator",
        "name": "Kreator",
        "price_idr": 49000,
        "credits": 30,
        "features": ["30 kredit permanen", "Hingga 6 klip per video",
                     "Hanya render kandidat AI score >90", "Semua fitur Mulai",
                     "Editor cut bebas", "Clean & SFX & face swap",
                     "Tanpa watermark", "Export semua aspect"],
    },
    "studio": {
        "id": "studio",
        "name": "Studio",
        "price_idr": 149000,
        "credits": 100,
        "features": ["100 kredit permanen", "Hingga 6 klip per video",
                     "Hanya render kandidat AI score >90", "Semua fitur Kreator",
                     "Auto-publish scheduler", "Brand template",
                     "Prioritas render"],
    },
}
# ── Mock Payment Gateway helpers ────────────────────────────────────
# Konfigurasi untuk testing tanpa real Stripe/Midtrans. Transaksi mock
# disimpan dalam JSON file (backend/.mock_tx.json). Status tracking & kredit
# diatur lewat endpoint di app.py: /api/payment/mock/*. Endpoint itu HANYA
# aktif ketika MOCK_PAYMENTS_ENABLED menyala DAN proses bukan environment
# produksi (lihat mock_payments_enabled) — supaya tidak bisa mencetak kredit
# gratis di server live.
MOCK_TX_FILE = Path(__file__).with_name(".mock_tx.json")

def _load_mock_txs() -> dict:
    try:
        data = MOCK_TX_FILE.read_text(encoding="utf-8")
        return json.loads(data) if data else {}
    except Exception:
        return {}

def _save_mock_txs(txs: dict) -> None:
    MOCK_TX_FILE.write_text(json.dumps(txs, indent=2), encoding="utf-8")


# Alias so old callers (and the API shape) keep working.
PLANS = PACKS


def enabled() -> bool:
    return BILLING_ENABLED


def _is_production_env() -> bool:
    """True when the process is explicitly running in a production/live env."""
    for key in ("KLIPIN_ENV", "APP_ENV", "ENVIRONMENT", "ENV"):
        if os.environ.get(key, "").strip().lower() in ("production", "prod", "live"):
            return True
    return False


def mock_payments_enabled() -> bool:
    """Whether the isolated staging payment simulator is available.

    Hard-disabled in production regardless of MOCK_PAYMENTS_ENABLED so the
    self-approve endpoint can never mint free credits on a live server.
    """
    if _is_production_env():
        return False
    return MOCK_PAYMENTS_ENABLED


def balance(user_id: str) -> dict:
    """Permanent credit balance — never expires, never resets monthly."""
    used = _used_credits(user_id)
    left = db.credits_balance(user_id)
    pack_id = db.highest_paid_pack(user_id)
    free_tier = is_free_tier(user_id)
    start, end = _month_bounds()
    free_used_this_month = db.usage_committed_in_range(user_id, start, end)
    return {
        "plan_id": pack_id,
        "plan_name": PACKS.get(pack_id, {}).get("name", "Gratis"),
        "credits_total": db.credits_purchased(user_id),
        "credits_used": used,
        "credits_left": left,
        "free_tier": free_tier,
        # Free-quota state is only meaningful for a free-tier account. Derived
        # from real monthly usage (not a sticky flag) so the UI matches the
        # numbers the server actually enforces in _reserve_free.
        "free_used": bool(free_tier)
        and free_used_this_month >= FREE_MONTHLY_LIMIT,
        "free_used_this_month": free_used_this_month,
        "free_monthly_limit": FREE_MONTHLY_LIMIT,
        "period_start": None,
        "period_end": None,
    }


def _used_credits(user_id: str) -> int:
    """Lifetime committed credits (permanent model — no month bounds)."""
    return db.usage_total(user_id, 0, time.time() + 1e9)


def _month_bounds() -> tuple:
    """First and last second (epoch, UTC) of the current calendar month."""
    import calendar
    now = time.gmtime()
    start = calendar.timegm((now.tm_year, now.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    days = calendar.monthrange(now.tm_year, now.tm_mon)[1]
    end = calendar.timegm((now.tm_year, now.tm_mon, days, 23, 59, 59, 0, 0, -1))
    return start, end


def is_free_tier(user_id: str) -> bool:
    """True until the user owns a permanent credit (has purchased a pack)."""
    return db.credits_purchased(user_id) <= FREE_TIER_PURCHASE_THRESHOLD


def output_clip_limit(user_id: str, user: dict | None = None) -> int:
    """Return the authoritative per-source output limit for an account."""
    if not enabled() or (user is not None and is_admin_email(user.get("email"))):
        return PAID_OUTPUT_CLIPS
    return FREE_OUTPUT_CLIPS if is_free_tier(user_id) else PAID_OUTPUT_CLIPS


def free_monthly_usage(user_id: str) -> dict:
    """Free-tier metering for a user: how many generations this month and
    which limits apply. Purchasing credits lifts both caps (credits govern)."""
    start, end = _month_bounds()
    committed = db.usage_committed_in_range(user_id, start, end)
    return {
        "used_this_month": committed,
        "monthly_limit": FREE_MONTHLY_LIMIT,
        "hard_cap": FREE_MONTHLY_LIMIT,
        "max_duration": FREE_MAX_DURATION,
    }


def reserve(user_id: str, job_id: str, amount: int = 1,
            user: dict | None = None) -> dict:
    """Reserve a generation before a job runs. Returns {ok, error?, balance}.

    Free users get FREE_MONTHLY_LIMIT generations per calendar month (no
    credit spend, capped per-video at FREE_MAX_DURATION). Paid users spend
    permanent prepaid credits instead. Admin emails bypass everything.
    """
    if user is not None and is_admin_email(user.get("email")):
        return {"ok": True, "balance": balance(user_id), "admin": True}
    if is_free_tier(user_id):
        return _reserve_free(user_id, job_id)
    b = balance(user_id)
    if b["credits_left"] < amount:
        return {
            "ok": False,
            "error": "Kredit tidak cukup — beli paket untuk melanjutkan",
            "balance": b,
        }
    db.usage_log(user_id, job_id, amount, state="reserved")
    return {"ok": True, "balance": balance(user_id)}


def _reserve_free(user_id: str, job_id: str) -> dict:
    """Reserve one of the free monthly generations (no credit spend)."""
    start, end = _month_bounds()
    used = db.usage_committed_in_range(user_id, start, end)
    in_flight = db.usage_reserved_in_range(user_id, start, end)
    if used + in_flight >= FREE_MONTHLY_LIMIT:
        return {
            "ok": False,
            "error": (f"Batas gratis {FREE_MONTHLY_LIMIT}x per bulan tercapai — "
                      "langganan premium untuk generate tanpa batas"),
            "balance": balance(user_id),
        }
    db.usage_log(user_id, job_id, 0, state="reserved")
    return {"ok": True, "balance": balance(user_id)}


def commit(user_id: str, job_id: str) -> None:
    db.usage_set_state(user_id, job_id, state="committed")


def release(user_id: str, job_id: str) -> None:
    """Release a reservation (job failed) — the credit goes back."""
    db.usage_set_state(user_id, job_id, state="released")


def credit_paid(user_id: str, pack_id: str, provider: str,
                external_id: str) -> dict:
    """Apply a confirmed payment: add the pack's credits permanently."""
    pack = PACKS.get(pack_id)
    if not pack:
        raise ValueError(f"Unknown pack: {pack_id}")
    db.apply_paid_pack_once(
        user_id, provider, external_id, pack_id,
        amount_idr=pack["price_idr"], credits=pack["credits"])
    return balance(user_id)


def requires_klipin_watermark(user_id: str) -> bool:
    """Free and Mulai outputs carry branding; Kreator/Studio do not."""
    return db.highest_paid_pack(user_id) not in ("kreator", "studio")


# ─── Stripe (raw httpx) ─────────────────────────────────────────────────────

def _stripe_key() -> str:
    return os.environ.get("STRIPE_SECRET_KEY", "")


def stripe_checkout(user_id: str, pack_id: str) -> dict:
    key = _stripe_key()
    pack = PACKS.get(pack_id)
    if not pack:
        raise ValueError("Unknown pack")
    if not key:
        raise RuntimeError("STRIPE_SECRET_KEY not set")
    success = f"{os.environ.get('APP_URL', 'http://localhost:5173')}/billing?status=success"
    cancel = f"{os.environ.get('APP_URL', 'http://localhost:5173')}/billing?status=cancel"
    data = {
        "mode": "payment",
        "success_url": success,
        "cancel_url": cancel,
        "line_items[0][price_data][currency]": "idr",
        "line_items[0][price_data][product_data][name]":
            f"Klipin {pack['name']} — {pack['credits']} kredit permanen",
        "line_items[0][price_data][unit_amount]": str(pack["price_idr"]),
        "line_items[0][quantity]": "1",
        "metadata[pack_id]": pack_id,
        "metadata[user_id]": user_id,
    }
    with httpx.Client(timeout=20) as client:
        r = client.post(
            "https://api.stripe.com/v1/checkout/sessions",
            data=data, auth=(key, ""),
        )
    if r.status_code != 200:
        raise RuntimeError(f"Stripe checkout failed: {r.text[:300]}")
    return {"provider": "stripe", "url": r.json()["url"]}


def stripe_verify_webhook(payload: bytes, sig_header: str) -> dict | None:
    secret = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    if not secret:
        return None
    # Stripe v1 signatures: t=...,v1=...
    parts = {}
    for item in sig_header.split(","):
        k, _, v = item.partition("=")
        parts[k.strip()] = v.strip()
    # Reject stale/future signatures so a captured header cannot be replayed
    # later (Stripe's own default tolerance is 5 minutes). Idempotency on
    # external_id is a second line of defence, not a substitute for this.
    try:
        ts = int(parts.get("t", ""))
    except (TypeError, ValueError):
        return None
    try:
        tolerance = int(os.environ.get("STRIPE_WEBHOOK_TOLERANCE", "300"))
    except ValueError:
        tolerance = 300
    if tolerance > 0 and abs(time.time() - ts) > tolerance:
        return None
    signed = f"{parts.get('t', '')}.{payload.decode()}"
    expected = hmac.new(secret.encode(), signed.encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parts.get("v1", "")):
        return None
    return json.loads(payload)


# ─── Midtrans (raw httpx) ───────────────────────────────────────────────────

def _midtrans_key() -> str:
    return os.environ.get("MIDTRANS_SERVER_KEY", "")


def _midtrans_base() -> str:
    if os.environ.get("MIDTRANS_ENV", "").lower() == "production":
        return "https://app.midtrans.com"
    return "https://app.sandbox.midtrans.com"


def midtrans_charge(user_id: str, pack_id: str) -> dict:
    key = _midtrans_key()
    pack = PACKS.get(pack_id)
    if not pack:
        raise ValueError("Unknown pack")
    if not key:
        raise RuntimeError("MIDTRANS_SERVER_KEY not set")
    order_id = f"KLIP-{_str_secs()}"
    payload = {
        "transaction_details": {
            "order_id": order_id,
            "gross_amount": pack["price_idr"],
        },
        "item_details": [{
            "id": f"pack-{pack_id}",
            "price": pack["price_idr"],
            "quantity": 1,
            "name": f"Klipin {pack['name']} — {pack['credits']} kredit permanen",
        }],
        "customer_details": {"user_id": user_id},
        "enabled_payments": ["qris", "bank_transfer", "gopay", "shopeepay"],
        "credit_card": {"secure": True},
        "user_id": user_id,
    }
    with httpx.Client(timeout=20) as client:
        r = client.post(
            f"{_midtrans_base()}/snap/v1/transactions",
            json=payload,
            auth=(key, ""),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
    if r.status_code != 201:
        raise RuntimeError(f"Midtrans failed: {r.text[:300]}")
    data = r.json()
    db.record_payment(user_id, "midtrans", order_id, pack_id,
                      amount_idr=pack["price_idr"], status="pending")
    return {"provider": "midtrans", "token": data["token"],
            "redirect_url": data.get("redirect_url", "")}


def midtrans_verify_webhook(payload: bytes) -> dict | None:
    """Midtrans notification: check signature_key == sha512(order_id +
    status_code + gross_amount + server_key)."""
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    key = _midtrans_key()
    if not key:
        return None
    order_id = str(data.get("order_id", ""))
    status_code = str(data.get("status_code", ""))
    gross = str(data.get("gross_amount", ""))
    sig = data.get("signature_key", "")
    expected = hashlib.sha512(
        f"{order_id}{status_code}{gross}{key}".encode()).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None
    if data.get("transaction_status") not in ("settlement", "capture"):
        return None
    return data


def _str_secs() -> str:
    return f"{int(time.time())}.{int(time.time() % 1 * 1000000)}"
