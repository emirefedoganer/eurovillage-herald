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
import hashlib
import hmac
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from itsdangerous import URLSafeSerializer, BadSignature

import store

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

PREFERENCE_CHOICES = [
    ("new_issue", "Yeni gazete sayısı yayımlandığında"),
    ("weekly_digest", "Haftalık özet"),
    ("popular_stories", "Çok okunan haberler"),
    ("breaking_news", "Son dakika haberleri"),
    ("ari_magazin", "Arı Magazin yeni içerik"),
]
PREFERENCE_LABELS = dict(PREFERENCE_CHOICES)
DEFAULT_PREFERENCES = {key: False for key, _ in PREFERENCE_CHOICES}

CONFIRM_TOKEN_TTL_HOURS = 48
UNSUBSCRIBE_SALT = "issue-unsubscribe"

# Explicit subscription states. "confirmed" is this project's pre-existing
# name for what the spec calls "active" -- kept as-is (a rename would touch
# every call site and test for no functional gain); "bounced"/"complained"/
# "suppressed" are new. Every one of these EXCEPT "confirmed" is excluded
# from store.confirmed_subscribers() and therefore from every future
# campaign send -- see also outbox.py's per-send eligibility recheck,
# which re-verifies status at the moment a queued bulletin job is actually
# sent (not just when it was enqueued).
STATUS_LABELS = {
    "pending": "Onay Bekliyor",
    "confirmed": "Onaylı",
    "unsubscribed": "Ayrılmış",
    "bounced": "Geri Döndü (Bounced)",
    "complained": "Şikayet Bildirildi",
    "suppressed": "Yönetici Tarafından Engellendi",
}
# Blocked states: excluded from every campaign, cannot be re-opened by the
# public form, and are never downgraded by an unsubscribe click.
BLOCKED_STATUSES = ("bounced", "complained", "suppressed")
RECOVERABLE_STATUSES = BLOCKED_STATUSES  # legacy name
# What an audited admin action may clear. A complaint (the reader marked us
# as spam) is permanent -- deliberately NOT clearable.
CLEARABLE_STATUSES = ("bounced", "suppressed")

# Bumped whenever the privacy/KVKK notice shown at signup materially
# changes. Recorded on every subscription record so a future change to
# this text never silently rewrites what an existing subscriber actually
# saw and agreed to. This records which version of the INFORMATION TEXT
# was presented -- a separate concept from *which* mailing categories a
# reader opted into (the `preferences` dict). The single source of truth
# for this value; app.py's /gizlilik page imports it directly rather than
# defining its own copy.
CURRENT_PRIVACY_NOTICE_VERSION = "2026-09-v3"


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


def _opaque_token(secret_key, salt, subscription_id):
    """Opaque, non-decodable, per-subscriber token: an HMAC of the internal
    id under the app secret. Nothing (id, email) can be read back out of
    the URL, and it needs no storage -- it is recomputed to look a
    subscriber up (see _subscription_id_from_opaque)."""
    key = secret_key.encode() if isinstance(secret_key, str) else secret_key
    return hmac.new(key, f"{salt}:{subscription_id}".encode(), hashlib.sha256).hexdigest()[:40]


def _subscription_id_from_opaque(secret_key, salt, token):
    if not token or "." in token:
        return None
    for s in store.load_subscriptions():
        if hmac.compare_digest(_opaque_token(secret_key, salt, s["id"]), token):
            return s["id"]
    return None


def unsubscribe_token(secret_key, subscription_id):
    return _opaque_token(secret_key, UNSUBSCRIBE_SALT, subscription_id)


def subscription_id_from_unsubscribe_token(secret_key, token):
    found = _subscription_id_from_opaque(secret_key, UNSUBSCRIBE_SALT, token)
    if found:
        return found
    try:  # legacy signed tokens already sitting in previously sent emails
        return _serializer(secret_key).loads(token).get("id")
    except BadSignature:
        return None


MANAGE_SALT = "issue-manage-preferences"


def _manage_serializer(secret_key):
    return URLSafeSerializer(secret_key, salt=MANAGE_SALT)


def manage_token(secret_key, subscription_id):
    """Separate salt from the unsubscribe token so the two links are never
    interchangeable."""
    return _opaque_token(secret_key, MANAGE_SALT, subscription_id)


def subscription_id_from_manage_token(secret_key, token):
    found = _subscription_id_from_opaque(secret_key, MANAGE_SALT, token)
    if found:
        return found
    try:
        return _manage_serializer(secret_key).loads(token).get("id")
    except BadSignature:
        return None


def _normalize_preferences(preferences, default_if_empty=True):
    """default_if_empty guards only the SIGNUP form, where a submission
    with nothing checked is almost certainly a form/JS glitch, not a
    deliberate choice (there is nothing to "sign up" for otherwise) --
    it forces new_issue on rather than creating a subscriber who never
    receives anything. It must NOT apply when an already-subscribed
    reader is updating their own preferences on the no-login management
    page (see update_preferences()): deliberately unchecking everything
    there is a legitimate way to go quiet without a full unsubscribe,
    and forcing a category back on would silently override that choice."""
    prefs = dict(DEFAULT_PREFERENCES)
    for key, _ in PREFERENCE_CHOICES:
        if key in (preferences or {}):
            prefs[key] = bool(preferences[key])
    if default_if_empty and not any(prefs.values()):
        prefs["new_issue"] = True
    return prefs


def subscribe(email, preferences, consent_method="web_form"):
    """Creates or refreshes a pending subscription. Returns (subscription,
    confirm_token) -- confirm_token is the plaintext, single-use token for
    the confirmation email link; only its hash is ever stored. An
    already-confirmed email re-submitting the form gets its preferences
    updated with no new confirmation email (avoids re-sending an
    unsolicited-looking email to someone already subscribed) -- callers
    should show the same generic "check your email" message in both
    cases, so this form can never be used to probe which addresses are
    already subscribed.

    Every call records the CURRENT privacy notice version + a timestamp,
    even for an existing subscriber just updating preferences -- an
    explicit action re-affirms it was seen. Never opts an existing
    subscriber into a newly introduced preference category on its own;
    only keys explicitly present and true in `preferences` are set."""
    email = validate_email(email)
    if not email:
        return None, None

    prefs = _normalize_preferences(preferences)
    subs = store.load_subscriptions()
    existing = next((s for s in subs if s["email"].strip().lower() == email), None)
    now = _iso(_now())

    if existing and existing.get("status") in RECOVERABLE_STATUSES:
        # Bounced/complained/suppressed: the public form must not quietly
        # re-open the address (recovery is the deliberate admin action,
        # reactivate_for_resend()). Same generic response to the caller.
        return existing, None

    if existing and existing.get("status") == "confirmed":
        existing["preferences"] = prefs
        existing["updated_at"] = now
        existing["privacy_notice_version"] = CURRENT_PRIVACY_NOTICE_VERSION
        existing["consent_at"] = now
        existing["consent_method"] = consent_method
        store.save_subscriptions(subs)
        return existing, None

    token = secrets.token_urlsafe(32)
    token_hash = store.hash_preview_token(token)
    expires_at = _iso(_now() + timedelta(hours=CONFIRM_TOKEN_TTL_HOURS))

    if existing:
        # Covers both a still-pending signup (re-requesting confirmation)
        # and a previously unsubscribed address signing up again -- either
        # way the record goes back to "pending" until the new link is
        # actually clicked, rather than leaking the old "unsubscribed"
        # status while a confirmation is outstanding.
        existing["status"] = "pending"
        existing["preferences"] = prefs
        existing["confirm_token_hash"] = token_hash
        existing["confirm_token_expires_at"] = expires_at
        existing["updated_at"] = now
        existing["unsubscribed_at"] = None
        existing["privacy_notice_version"] = CURRENT_PRIVACY_NOTICE_VERSION
        existing["consent_at"] = now
        existing["consent_method"] = consent_method
        store.save_subscriptions(subs)
        return existing, token

    record = {
        "id": uuid.uuid4().hex[:12],
        "email": email,
        "preferences": prefs,
        "status": "pending",
        "confirm_token_hash": token_hash,
        "confirm_token_expires_at": expires_at,
        "created_at": now,
        "updated_at": now,
        "confirmed_at": None,
        "unsubscribed_at": None,
        "privacy_notice_version": CURRENT_PRIVACY_NOTICE_VERSION,
        "consent_at": now,
        "consent_method": consent_method,
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
    """Full unsubscribe -- from every optional editorial category at
    once. Idempotent: unsubscribing an already-unsubscribed or unknown id
    is a no-op (not an error), so a stale/reused link never leaks whether
    a subscription still exists."""
    subs = store.load_subscriptions()
    idx = next((i for i, s in enumerate(subs) if s["id"] == subscription_id), None)
    if idx is None or subs[idx]["status"] == "unsubscribed":
        return False
    if subs[idx]["status"] in RECOVERABLE_STATUSES:
        # Already excluded from every campaign; never downgrade a
        # bounce/complaint/suppression record into a plain unsubscribe.
        return True
    subs[idx]["status"] = "unsubscribed"
    subs[idx]["unsubscribed_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return True


def unsubscribe_one(subscription_id, category):
    """Turns off a single preference category without touching the
    subscription's overall status or any other category -- "I don't want
    breaking news anymore, but keep the weekly digest." Returns False for
    an unknown subscription/category or an already-unsubscribed record
    (nothing meaningful to change)."""
    if category not in PREFERENCE_LABELS:
        return False
    subs = store.load_subscriptions()
    idx = next((i for i, s in enumerate(subs) if s["id"] == subscription_id), None)
    if idx is None or subs[idx]["status"] == "unsubscribed":
        return False
    subs[idx].setdefault("preferences", {})[category] = False
    subs[idx]["updated_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return True


def update_preferences(subscription_id, preferences):
    """Used by the no-login preference-management page -- replaces the
    full preference set with whatever the reader just submitted (an
    unchecked box means "off", exactly like the signup form)."""
    subs = store.load_subscriptions()
    idx = next((i for i, s in enumerate(subs) if s["id"] == subscription_id), None)
    if idx is None or subs[idx]["status"] == "unsubscribed":
        return None
    subs[idx]["preferences"] = _normalize_preferences(preferences, default_if_empty=False)
    subs[idx]["updated_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return subs[idx]


def record_bounce(email, reason=""):
    """Called by the Resend webhook handler (see app.py's
    /internal/webhooks/resend) on a `email.bounced` event. Moves the
    matching subscriber out of "confirmed" so store.confirmed_subscribers()
    -- and therefore every future campaign -- stops selecting them.
    Setting the same status twice (a redelivered webhook) is a harmless
    no-op, which is what makes this safe to call without a separate
    dedup/idempotency log. Returns False for an address with no matching
    subscriber (never an error -- Resend's bounce events aren't scoped to
    just bulletin sends)."""
    subs = store.load_subscriptions()
    email = (email or "").strip().lower()
    idx = next((i for i, s in enumerate(subs) if s["email"].strip().lower() == email), None)
    if idx is None:
        return False
    subs[idx]["status"] = "bounced"
    subs[idx]["bounce_reason"] = (reason or "")[:300]
    subs[idx]["bounced_at"] = _iso(_now())
    subs[idx]["updated_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return True


def record_complaint(email):
    """`email.complained` event -- a reader marked a campaign as spam.
    Same idempotency reasoning as record_bounce()."""
    subs = store.load_subscriptions()
    email = (email or "").strip().lower()
    idx = next((i for i, s in enumerate(subs) if s["email"].strip().lower() == email), None)
    if idx is None:
        return False
    subs[idx]["status"] = "complained"
    subs[idx]["complained_at"] = _iso(_now())
    subs[idx]["updated_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return True


def suppress(subscription_id, actor_email, reason=""):
    """Manual admin block -- for an address the team wants to stop
    mailing without an actual bounce/complaint event (e.g. a support
    request). Distinct from `unsubscribe()`: this is an ADMIN action
    against a subscriber's wishes/record, logged with who did it."""
    subs = store.load_subscriptions()
    idx = next((i for i, s in enumerate(subs) if s["id"] == subscription_id), None)
    if idx is None:
        return None
    subs[idx]["status"] = "suppressed"
    subs[idx]["suppressed_at"] = _iso(_now())
    subs[idx]["suppressed_by"] = actor_email
    subs[idx]["suppress_reason"] = (reason or "")[:300]
    subs[idx]["updated_at"] = _iso(_now())
    store.save_subscriptions(subs)
    return subs[idx]


def clear_suppression(subscription_id, actor_email, reason):
    """Explicit, audited recovery step -- deliberately does NOT send
    anything and does NOT re-enable delivery: the record moves to
    "unsubscribed" (still excluded from every campaign). The address only
    becomes eligible again if its owner submits the public subscription
    form themselves and completes double opt-in. A "complained" record can
    never be cleared. Returns the record or None."""
    subs = store.load_subscriptions()
    idx = next((i for i, s in enumerate(subs) if s["id"] == subscription_id), None)
    if idx is None or subs[idx]["status"] not in CLEARABLE_STATUSES or not (reason or "").strip():
        return None
    now = _iso(_now())
    prior = subs[idx]["status"]
    subs[idx]["status"] = "unsubscribed"
    subs[idx]["unsubscribed_at"] = now
    subs[idx]["suppression_cleared_at"] = now
    subs[idx]["suppression_cleared_by"] = actor_email
    subs[idx]["suppression_cleared_from"] = prior
    subs[idx]["suppression_cleared_reason"] = reason.strip()[:300]
    subs[idx]["confirm_token_hash"] = None
    subs[idx]["confirm_token_expires_at"] = None
    subs[idx]["updated_at"] = now
    store.save_subscriptions(subs)
    return subs[idx]
