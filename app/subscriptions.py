"""Public subscription system. Initial use case is "notify me when a new
issue is published"; the `preferences` dict is designed so more
categories (breaking news, weekly digest, Arı Magazin) can be added
later purely as more keys -- no schema change, no migration.

Double opt-in: signing up creates a "pending" record. Only a "confirmed"
subscriber (one who clicked the emailed confirmation link) is ever
notified -- see store.confirmed_subscribers(). Actual email delivery is
a separate concern (see mailer.py); this module only manages the
subscription record and its tokens.

Unsubscribing needs no login and no separate "are you sure" step: the
link in every email decodes to a subscription id via a signed,
stateless token (itsdangerous, same mechanism already used for the
Turnstile gate cookie in app.py) -- nothing is stored for it, so the
exact same link can be safely re-embedded in every future email without
ever persisting a second long-lived plaintext secret per subscriber.
"""
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from itsdangerous import URLSafeSerializer, BadSignature

import store

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PREFERENCE_CHOICES = [
    ("new_issue", "Yeni gazete sayısı yayımlandığında"),
    ("breaking_news", "Son dakika haberleri"),
    ("weekly_digest", "Haftalık özet"),
    ("ari_magazin", "Arı Magazin yeni içerik"),
]
DEFAULT_PREFERENCES = {key: False for key, _ in PREFERENCE_CHOICES}

CONFIRM_TOKEN_TTL_HOURS = 48
UNSUBSCRIBE_SALT = "issue-unsubscribe"


def validate_email(email):
    email = (email or "").strip()
    if not email or len(email) > 200 or not EMAIL_RE.match(email):
        return None
    return email.lower()


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _serializer(secret_key):
    return URLSafeSerializer(secret_key, salt=UNSUBSCRIBE_SALT)


def unsubscribe_token(secret_key, subscription_id):
    return _serializer(secret_key).dumps({"id": subscription_id})


def subscription_id_from_unsubscribe_token(secret_key, token):
    try:
        data = _serializer(secret_key).loads(token)
        return data.get("id")
    except BadSignature:
        return None


def _normalize_preferences(preferences):
    prefs = dict(DEFAULT_PREFERENCES)
    for key, _ in PREFERENCE_CHOICES:
        if key in (preferences or {}):
            prefs[key] = bool(preferences[key])
    if not any(prefs.values()):
        prefs["new_issue"] = True  # the form always offers at least this; defensive fallback only
    return prefs


def subscribe(email, preferences):
    """Creates or refreshes a pending subscription. Returns (subscription,
    confirm_token) -- confirm_token is the plaintext, single-use token for
    the confirmation email link; only its hash is ever stored. An
    already-confirmed email re-submitting the form gets its preferences
    updated with no new confirmation email (avoids re-sending an
    unsolicited-looking email to someone already subscribed) -- callers
    should show the same generic "check your email" message in both
    cases, so this form can never be used to probe which addresses are
    already subscribed."""
    email = validate_email(email)
    if not email:
        return None, None

    prefs = _normalize_preferences(preferences)
    subs = store.load_subscriptions()
    existing = next((s for s in subs if s["email"].strip().lower() == email), None)

    if existing and existing.get("status") == "confirmed":
        existing["preferences"] = prefs
        existing["updated_at"] = _iso(_now())
        store.save_subscriptions(subs)
        return existing, None

    token = secrets.token_urlsafe(32)
    token_hash = store.hash_preview_token(token)
    expires_at = _iso(_now() + timedelta(hours=CONFIRM_TOKEN_TTL_HOURS))

    if existing:
        existing["preferences"] = prefs
        existing["confirm_token_hash"] = token_hash
        existing["confirm_token_expires_at"] = expires_at
        existing["updated_at"] = _iso(_now())
        store.save_subscriptions(subs)
        return existing, token

    record = {
        "id": uuid.uuid4().hex[:12],
        "email": email,
        "preferences": prefs,
        "status": "pending",
        "confirm_token_hash": token_hash,
        "confirm_token_expires_at": expires_at,
        "created_at": _iso(_now()),
        "updated_at": _iso(_now()),
        "confirmed_at": None,
        "unsubscribed_at": None,
    }
    subs.append(record)
    store.save_subscriptions(subs)
    return record, token


def confirm(token):
    """Verifies a confirm-email token and flips the subscription to
    confirmed. Returns the updated record, or None for a missing/expired/
    already-used token (indistinguishable to the caller, same pattern as
    article/issue preview tokens)."""
    sub = store.get_subscription_by_confirm_token(token)
    if not sub:
        return None
    subs = store.load_subscriptions()
    idx = next(i for i, s in enumerate(subs) if s["id"] == sub["id"])
    subs[idx]["status"] = "confirmed"
    subs[idx]["confirmed_at"] = _iso(_now())
    subs[idx]["confirm_token_hash"] = None
    subs[idx]["confirm_token_expires_at"] = None
    subs[idx]["updated_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return subs[idx]


def unsubscribe(subscription_id):
    """Idempotent -- unsubscribing an already-unsubscribed or unknown id
    is a no-op (not an error), so a stale/reused link never leaks whether
    a subscription still exists."""
    subs = store.load_subscriptions()
    idx = next((i for i, s in enumerate(subs) if s["id"] == subscription_id), None)
    if idx is None or subs[idx]["status"] == "unsubscribed":
        return False
    subs[idx]["status"] = "unsubscribed"
    subs[idx]["unsubscribed_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return True
