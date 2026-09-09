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
  - "resend" -> Resend's HTTP API (https://api.resend.com/emails) via
    urllib.request -- no SDK dependency added for one HTTP POST.

Callers should use send_transactional_email()/send_bulletin_email() --
never construct a message or pick a From address directly -- so no route
or template ever hard-codes a provider call or gets a sending identity
wrong. Nothing in this module is durable: a caller that needs retries/
exactly-once semantics across a process restart should go through
app/outbox.py, which calls these functions per attempt.
"""
import json
import os
import re
import smtplib
import ssl
import sys
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import parseaddr

EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "").strip().lower()

DEFAULT_TRANSACTIONAL_FROM = "The Eurovillage Herald <noreply@eurovillageherald.com>"
DEFAULT_BULLETIN_FROM = "The Eurovillage Herald <bulletin@news.eurovillageherald.com>"
DEFAULT_CONTACT_REPLY_TO = "iletisim@eurovillageherald.com"

EMAIL_TRANSACTIONAL_FROM = os.environ.get("EMAIL_TRANSACTIONAL_FROM", "").strip() or DEFAULT_TRANSACTIONAL_FROM
EMAIL_BULLETIN_FROM = os.environ.get("EMAIL_BULLETIN_FROM", "").strip() or DEFAULT_BULLETIN_FROM
EMAIL_CONTACT_REPLY_TO = os.environ.get("EMAIL_CONTACT_REPLY_TO", "").strip() or DEFAULT_CONTACT_REPLY_TO

EMAIL_API_KEY = os.environ.get("EMAIL_API_KEY", "").strip()

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


def _send_via_fake(to_address, from_identity, subject, text_body, html_body, reply_to):
    print(
        f"[mailer:fake] GÖNDERİLMEDİ (gerçek sağlayıcı yapılandırılmamış) -- "
        f"from={from_identity} to={to_address} subject={subject!r} reply_to={reply_to}",
        file=sys.stderr,
    )
    return True, None


def _send_via_smtp(to_address, from_identity, subject, text_body, html_body, reply_to):
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = from_identity
    msg["To"] = to_address
    if reply_to:
        msg["Reply-To"] = reply_to
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
        return True, None
    except Exception as exc:
        return False, str(exc)


def _send_via_resend(to_address, from_identity, subject, text_body, html_body, reply_to):
    payload = {
        "from": from_identity,
        "to": [to_address],
        "subject": subject,
        "text": text_body,
    }
    if html_body:
        payload["html"] = html_body
    if reply_to:
        payload["reply_to"] = [reply_to]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.resend.com/emails",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {EMAIL_API_KEY}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            resp.read()
            return True, None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        return False, f"HTTP {exc.code}: {detail}"
    except Exception as exc:
        return False, str(exc)


_BACKENDS = {"fake": _send_via_fake, "smtp": _send_via_smtp, "resend": _send_via_resend}


def _send(from_identity, to_address, subject, text_body, html_body, reply_to):
    """Returns (ok: bool, error: str|None). Never raises."""
    if not to_address or "@" not in to_address:
        return False, "invalid recipient address"
    try:
        return _BACKENDS[BACKEND](to_address, from_identity, subject, text_body, html_body, reply_to)
    except Exception as exc:
        return False, str(exc)


def send_transactional_email(to_address, subject, text_body, html_body=None, reply_to=None):
    """Subscription confirmations, contact acknowledgements/resolutions,
    etc. Always sent from EMAIL_TRANSACTIONAL_FROM. Pass reply_to
    explicitly for contact-flavored mail (normally
    mailer.EMAIL_CONTACT_REPLY_TO) -- not set by default, since not every
    transactional email should route replies to the contact mailbox."""
    return _send(EMAIL_TRANSACTIONAL_FROM, to_address, subject, text_body, html_body, reply_to)


def send_bulletin_email(to_address, subject, text_body, html_body=None):
    """Editorial newsletters/campaigns. Always sent from
    EMAIL_BULLETIN_FROM. No Reply-To is set -- bulletin@ is not a
    monitored inbox and readers should use the unsubscribe/preferences
    link, not a reply, to manage their subscription."""
    return _send(EMAIL_BULLETIN_FROM, to_address, subject, text_body, html_body, None)


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
