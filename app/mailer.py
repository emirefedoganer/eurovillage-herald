"""Outgoing email: provider abstraction + the three sending identities.

Three conceptually different identities (never conflated):

  - TRANSACTIONAL (EMAIL_TRANSACTIONAL_FROM, default
    "The Eurovillage Herald <noreply@eurovillageherald.com>"): subscription
    confirmations, contact acknowledgements, ticket resolutions. A SENDING
    identity, not a monitored inbox -- contact-flavored transactional mail
    carries Reply-To: EMAIL_CONTACT_REPLY_TO (default
    iletisim@eurovillageherald.com), the real, existing Zoho-hosted human
    mailbox. This module never creates, modifies, or synchronizes that
    mailbox -- it only ever sets a Reply-To header pointing at it.
  - BULLETIN (EMAIL_BULLETIN_FROM, default
    "The Eurovillage Herald <bulletin@news.eurovillageherald.com>"):
    editorial newsletters/campaigns. Also a sending-only identity.

Provider abstraction: EMAIL_PROVIDER selects the backend --
  - unset / anything unrecognized -> FakeBackend: logs recipient + subject
    only (never body/HTML, keeps logs free of subscriber content) and
    reports success, so the rest of the application (outbox, bulletin
    sending, tests) can be exercised end to end with zero configuration
    and without ever making a network call or sending real mail.
  - "smtp" -> stdlib smtplib against SMTP_HOST/PORT/USERNAME/PASSWORD.
    Works with nearly any real SMTP-speaking provider or mailbox.
  - "resend" -> the official `resend` Python SDK (see requirements.txt).

    A previous version of this backend spoke to
    https://api.resend.com/emails directly via urllib.request. That is
    the correct, documented endpoint -- the bug was HOW it was called,
    not where: urllib.request with no explicit User-Agent sends
    "Python-urllib/<version>" by default, a signature Cloudflare's bot
    management in front of api.resend.com reliably fingerprints and
    blocks with error 1010 ("blocked based on the client's browser
    signature") before the request ever reaches Resend's own API --
    which is exactly why nothing showed up in the Resend dashboard even
    though the outbox recorded five failed attempts. The official SDK
    sends "User-Agent: resend-python:<version>" (see
    resend/request.py's Request.__get_headers()) via `requests`, a
    signature Resend's own Cloudflare configuration is tuned to allow.
    Switching transports fixes the block; the endpoint URL itself
    (https://api.resend.com/emails) was never wrong.

Callers should use send_transactional_email()/send_bulletin_email() --
never construct a message, pick a From address, or call a provider SDK
directly -- so no route or template ever hard-codes a provider call or
gets a sending identity wrong. Nothing in this module is durable: a
caller that needs retries/exactly-once semantics across a process
restart should go through app/outbox.py, which calls these functions per
attempt and uses the `permanent` flag below to stop retrying a failure
retrying can never fix.
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage
from email.utils import parseaddr

import resend

EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "").strip().lower()

DEFAULT_TRANSACTIONAL_FROM = "The Eurovillage Herald <noreply@eurovillageherald.com>"
DEFAULT_BULLETIN_FROM = "The Eurovillage Herald <bulletin@news.eurovillageherald.com>"
DEFAULT_CONTACT_REPLY_TO = "iletisim@eurovillageherald.com"

EMAIL_TRANSACTIONAL_FROM = os.environ.get("EMAIL_TRANSACTIONAL_FROM", "").strip() or DEFAULT_TRANSACTIONAL_FROM
EMAIL_BULLETIN_FROM = os.environ.get("EMAIL_BULLETIN_FROM", "").strip() or DEFAULT_BULLETIN_FROM
EMAIL_CONTACT_REPLY_TO = os.environ.get("EMAIL_CONTACT_REPLY_TO", "").strip() or DEFAULT_CONTACT_REPLY_TO

# Deliberately EMAIL_API_KEY, not RESEND_API_KEY -- the provider-agnostic
# name this module has always used, so the config interface doesn't change
# depending on which backend is selected. The `resend` package itself
# defaults to reading RESEND_API_KEY from the environment at import time
# (see resend/__init__.py); we never rely on that and instead assign
# EMAIL_API_KEY's value onto resend.api_key ourselves, once, below --
# EMAIL_API_KEY is the only environment variable Railway needs to set.
EMAIL_API_KEY = os.environ.get("EMAIL_API_KEY", "").strip()
resend.api_key = EMAIL_API_KEY
# Matches this module's previous 15s urllib timeout.
resend.default_http_client = resend.RequestsClient(timeout=15)

# SMTP backend config (used only when EMAIL_PROVIDER=smtp)
SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "").strip() or "587")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").strip().lower() not in ("0", "false", "no")


def _backend_name():
    if EMAIL_PROVIDER == "resend" and EMAIL_API_KEY:
        return "resend"
    if EMAIL_PROVIDER == "smtp" and SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD:
        return "smtp"
    return "fake"


BACKEND = _backend_name()
ENABLED = BACKEND != "fake"


def _extract_address(identity):
    """'Name <addr@x.com>' -> 'addr@x.com'; a bare address passes through."""
    return parseaddr(identity)[1] or identity


# Structured record of what the fake backend "sent" (recipient, From,
# subject, Reply-To -- never bodies), bounded so it can't grow forever.
# Lets tests assert on the exact headers instead of parsing stderr.
FAKE_SENT = []
_FAKE_SENT_MAX = 200


def _send_via_fake(to_address, from_identity, subject, text_body, html_body, reply_to, headers=None):
    FAKE_SENT.append({"to": to_address, "from": from_identity, "subject": subject, "reply_to": reply_to,
                      "headers": dict(headers or {})})
    del FAKE_SENT[:-_FAKE_SENT_MAX]
    print(
        f"[mailer:fake] GÖNDERİLMEDİ (gerçek sağlayıcı yapılandırılmamış) -- "
        f"from={from_identity} to={to_address} subject={subject!r} reply_to={reply_to}",
        file=sys.stderr,
    )
    return True, None, None, False


def _send_via_smtp(to_address, from_identity, subject, text_body, html_body, reply_to, headers=None):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_identity
    msg["To"] = to_address
    if reply_to:
        msg["Reply-To"] = reply_to
    for name, value in (headers or {}).items():
        msg[name] = value
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")
    try:
        if SMTP_USE_TLS:
            context = ssl.create_default_context()
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
                server.starttls(context=context)
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=10, context=context) as server:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
                server.send_message(msg)
        # smtplib doesn't hand back a message id, and its failure modes
        # (auth vs. transient connection issues) aren't reliably
        # distinguishable without deeper per-provider inspection -- SMTP
        # keeps its pre-existing always-retryable-until-MAX_ATTEMPTS
        # behavior unchanged.
        return True, None, None, False
    except Exception as exc:
        return False, str(exc), None, False


# Resend error subtypes that mean "this will never succeed by retrying":
# a bad/missing API key, or a request Resend's API itself rejected as
# malformed (a validation error covers, among other things, an unverified
# sending domain). See resend/exceptions.py for the full hierarchy --
# every one of these is raised only when Resend's API returned real,
# parseable JSON identifying the problem, never for an ambiguous/
# infrastructure-layer response.
_PERMANENT_RESEND_ERRORS = (
    resend.exceptions.MissingApiKeyError,
    resend.exceptions.InvalidApiKeyError,
    resend.exceptions.ValidationError,
    resend.exceptions.MissingRequiredFieldsError,
)


def _is_permanent_resend_error(exc):
    """True only for a CONFIRMED non-retryable rejection from Resend's own
    API (bad credentials, unverified domain, malformed payload) -- never
    for a rate limit, a server error, or a response the SDK couldn't even
    parse as JSON (error_type "application_error", the SDK's catch-all for
    a non-JSON body -- e.g. an intermediary/edge response rather than a
    confirmed answer from Resend itself). Those stay retryable, matching
    the "genuinely temporary network, 429, and 5xx failures" retry
    contract this function's caller (outbox.process_outbox) relies on."""
    if isinstance(exc, _PERMANENT_RESEND_ERRORS):
        return True
    if not isinstance(exc, resend.exceptions.ResendError):
        return False
    try:
        code = int(exc.code)
    except (TypeError, ValueError):
        return False
    return 400 <= code < 500 and code != 429 and exc.error_type != "application_error"


def _resend_error_detail(exc):
    """A safe, loggable one-line summary of a ResendError -- built only
    from the SDK's own exception fields (code/error_type/message), which
    never include the API key (see resend/exceptions.py: none of these
    classes ever accept or echo back the Authorization header/key)."""
    code = getattr(exc, "code", "unknown")
    error_type = getattr(exc, "error_type", "unknown")
    message = (getattr(exc, "message", "") or str(exc)).strip()[:300]
    return f"Resend API error {code} ({error_type}): {message}"


def _send_via_resend(to_address, from_identity, subject, text_body, html_body, reply_to, headers=None):
    params = {"from": from_identity, "to": [to_address], "subject": subject, "text": text_body}
    if html_body:
        params["html"] = html_body
    if reply_to:
        params["reply_to"] = [reply_to]
    if headers:
        params["headers"] = dict(headers)
    try:
        result = resend.Emails.send(params)
        return True, None, result.get("id"), False
    except resend.exceptions.ResendError as exc:
        return False, _resend_error_detail(exc), None, _is_permanent_resend_error(exc)
    except Exception as exc:
        # Anything outside the SDK's own error hierarchy (e.g. a genuine
        # network-level failure the SDK's HTTP client re-raised) is
        # treated as temporary/retryable -- never permanently give up on
        # a condition this code doesn't specifically recognize.
        return False, str(exc)[:300], None, False


_BACKENDS = {"fake": _send_via_fake, "smtp": _send_via_smtp, "resend": _send_via_resend}


def _send(from_identity, to_address, subject, text_body, html_body, reply_to, headers=None):
    """Returns (ok, error, message_id, permanent):
      - ok: bool
      - error: str|None -- a safe, loggable message; never the API key
      - message_id: str|None -- Resend's real Message ID on a successful
        Resend send; None for every other backend/outcome
      - permanent: bool -- True only for a confirmed non-retryable failure
        (invalid credentials, unverified domain, malformed payload);
        outbox.process_outbox() stops retrying such a job immediately
        instead of burning through MAX_ATTEMPTS on a config problem no
        retry can fix. Always False for the fake/smtp backends, which
        keep their pre-existing retry-until-MAX_ATTEMPTS behavior.
    Never raises."""
    if not to_address or "@" not in to_address:
        return False, "invalid recipient address", None, True
    try:
        return _BACKENDS[BACKEND](to_address, from_identity, subject, text_body, html_body, reply_to, headers)
    except Exception as exc:
        return False, str(exc), None, False


def send_transactional_email(to_address, subject, text_body, html_body=None, reply_to=None):
    """Subscription confirmations, contact acknowledgements/resolutions,
    etc. Always sent from EMAIL_TRANSACTIONAL_FROM. Pass reply_to
    explicitly for contact-flavored mail (normally
    mailer.EMAIL_CONTACT_REPLY_TO) -- not set by default, since not every
    transactional email should route replies to the contact mailbox.
    Returns (ok, error, message_id, permanent) -- see _send()."""
    return _send(EMAIL_TRANSACTIONAL_FROM, to_address, subject, text_body, html_body, reply_to)


def send_bulletin_email(to_address, subject, text_body, html_body=None, headers=None, reply_to=None):
    """Editorial newsletters/campaigns. Always sent from
    EMAIL_BULLETIN_FROM (never replaced by the contact mailbox), with
    Reply-To defaulting to EMAIL_CONTACT_REPLY_TO so a reader's reply
    reaches the real, monitored inbox. `headers` carries List-Unsubscribe
    /List-Unsubscribe-Post for real subscriber sends.
    Returns (ok, error, message_id, permanent) -- see _send()."""
    return _send(EMAIL_BULLETIN_FROM, to_address, subject, text_body, html_body,
                 reply_to or EMAIL_CONTACT_REPLY_TO, headers)


def sender_identities():
    """Non-secret configuration summary for the admin Email System Health
    view -- never includes EMAIL_API_KEY or SMTP_PASSWORD."""
    return {
        "backend": BACKEND,
        "provider_configured": ENABLED,
        "transactional_from": EMAIL_TRANSACTIONAL_FROM,
        "bulletin_from": EMAIL_BULLETIN_FROM,
        "contact_reply_to": EMAIL_CONTACT_REPLY_TO,
    }
