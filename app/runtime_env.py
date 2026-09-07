"""Single source of truth for whether this process is running in production,
used by turnstile.py/storage.py/uploads.py to decide how strict to be about
missing/broken Cloudflare configuration (see those modules for the actual
behavior). Read directly from the environment with no dependency on app.py,
so it can be imported by any module without a circular import.

Explicit override: set APP_ENV=production (or "prod") / APP_ENV=development
(or "dev"/"local"). If APP_ENV is unset, production is inferred from
SERVER_NAME being set -- the same signal app.py already uses to distinguish
a real deployment from local development (it's what turns on the
admin.<domain> subdomain routing, HTTPS-only cookies, etc).
"""
import os

_raw = os.environ.get("APP_ENV", "").strip().lower()

if _raw in ("production", "prod"):
    IS_PRODUCTION = True
elif _raw in ("development", "dev", "local"):
    IS_PRODUCTION = False
else:
    IS_PRODUCTION = bool(os.environ.get("SERVER_NAME", "").strip())
