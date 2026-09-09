"""Email delivery -- subscription confirmations and new-issue
notifications. No provider is fabricated or hard-coded.

If SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM_ADDRESS are
not all set (see .env.example), ENABLED is False and send() logs what
WOULD have been sent (recipient + subject only -- never the body, to
keep logs clean of subscriber content) and returns False. Callers must
treat that as "not sent" and continue safely, never crash.

Deliberately built on stdlib smtplib/email rather than a specific ESP's
own SDK, so that plugging in almost any real SMTP-speaking provider
(Postmark, SendGrid, Mailgun, Amazon SES, a Google Workspace account,
etc.) is purely an environment-variable change -- no code change, no new
dependency.
"""
import os
import smtplib
import ssl
import sys
from email.message import EmailMessage

SMTP_HOST = os.environ.get("SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("SMTP_PORT", "").strip() or "587")
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").strip()
SMTP_FROM_ADDRESS = os.environ.get("SMTP_FROM_ADDRESS", "").strip()
SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").strip().lower() not in ("0", "false", "no")

ENABLED = bool(SMTP_HOST and SMTP_USERNAME and SMTP_PASSWORD and SMTP_FROM_ADDRESS)


def send(to_address, subject, text_body, html_body=None):
    """Returns True only on a real, confirmed send. Never raises --
    a delivery failure is logged and reported back as False so a caller
    (e.g. the publish-notification loop) can keep going for the next
    recipient instead of aborting the whole batch."""
    if not ENABLED:
        print(
            f"[mailer] E-POSTA GÖNDERİLMEDİ (SMTP yapılandırılmamış) -- alıcı: {to_address}, konu: {subject}",
            file=sys.stderr,
        )
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM_ADDRESS
    msg["To"] = to_address
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
        return True
    except Exception as exc:
        print(f"[mailer] E-POSTA GÖNDERME HATASI (alıcı: {to_address}): {exc}", file=sys.stderr)
        return False
