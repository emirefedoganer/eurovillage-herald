"""Cloudflare Turnstile verification -- shared by the site-wide visitor
gate and any public form (contact form now, more later).

ENABLED is False whenever the site/secret keys aren't set, so the gate and
every form-protection call site quietly no-op in LOCAL DEVELOPMENT without
any extra configuration. In production (see runtime_env.py), the same
missing configuration instead prints a loud, impossible-to-miss startup
warning -- so a deploy that forgot to set the Turnstile keys doesn't just
silently run unprotected -- and can be turned into a hard startup failure
by setting TURNSTILE_REQUIRED=true once you want that guarantee.

Verification of the widget token is ALWAYS done server-side against
Cloudflare's Siteverify endpoint; nothing here trusts the client.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

import runtime_env

SITE_KEY = os.environ.get("TURNSTILE_SITE_KEY", "").strip()
SECRET_KEY = os.environ.get("TURNSTILE_SECRET_KEY", "").strip()
ENABLED = bool(SITE_KEY and SECRET_KEY)

# Explicit opt-in: once you've configured Turnstile and want a hard
# guarantee the app refuses to boot without it (rather than just warning),
# set TURNSTILE_REQUIRED=true. Defaults to False so an existing production
# deployment that hasn't set up Turnstile yet still starts (loudly warned,
# but running) instead of going down.
REQUIRED = os.environ.get("TURNSTILE_REQUIRED", "").strip().lower() in ("1", "true", "yes")

VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
FIELD_NAME = "cf-turnstile-response"


def _startup_warning(lines):
    bar = "!" * 78
    print("\n" + bar + "\n" + "\n".join(f"! {line}" for line in lines) + "\n" + bar + "\n", file=sys.stderr)


if runtime_env.IS_PRODUCTION and not ENABLED:
    _startup_warning([
        "TURNSTILE YAPILANDIRILMAMIŞ (TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY eksik).",
        "Bu bir ÜRETİM ortamı (APP_ENV=production ya da SERVER_NAME tanımlı) ama",
        "ziyaretçi doğrulama katmanı VE form korumaları (İletişim formu dahil)",
        "şu an TAMAMEN DEVRE DIŞI.",
        "Bu kasıtlıysa (Cloudflare kurulumu henüz tamamlanmadıysa) yok sayabilirsiniz.",
        "Değilse TURNSTILE_SITE_KEY / TURNSTILE_SECRET_KEY ekleyin",
        "(bkz. .env.example, CLOUDFLARE_SETUP.md).",
    ])
    if REQUIRED:
        raise RuntimeError(
            "TURNSTILE_REQUIRED=true ayarlanmış ama TURNSTILE_SITE_KEY/TURNSTILE_SECRET_KEY "
            "eksik -- üretimde Turnstile zorunlu kılındığından uygulama başlatılamıyor."
        )


def verify(token, remote_ip=None, timeout=5):
    """Verifies a Turnstile response token with Cloudflare. Fails closed:
    missing config, missing token, a network error, or an explicit failure
    from Cloudflare all return False."""
    if not ENABLED or not token:
        return False
    payload = {"secret": SECRET_KEY, "response": token}
    if remote_ip:
        payload["remoteip"] = remote_ip
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(VERIFY_URL, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            result = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return False
    return bool(result.get("success"))


def verify_form(form, remote_ip=None):
    """Convenience for a standard `<form>` POST carrying the widget's
    default field name -- the pattern every new protected form should use."""
    return verify(form.get(FIELD_NAME, ""), remote_ip=remote_ip)


def check(errors, form, remote_ip=None, message="Doğrulama başarısız oldu. Lütfen tekrar deneyin."):
    """Appends `message` to `errors` (in place) when Turnstile is enabled
    and the form's token doesn't verify. No-ops when Turnstile isn't
    configured, so a form keeps working before Cloudflare is set up."""
    if ENABLED and not verify_form(form, remote_ip=remote_ip):
        errors.append(message)
