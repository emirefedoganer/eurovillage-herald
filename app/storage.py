"""Cloudflare R2 (S3-compatible) object storage client.

Fully optional in LOCAL DEVELOPMENT: if the R2_* environment variables
aren't set, ENABLED is False and uploads.py falls back to saving on local
disk exactly as before. In production (see runtime_env.py) that same
"not configured" state instead prints a loud startup warning rather than
silently degrading, and a genuinely *broken* config (some but not all
R2_* vars set -- almost always a typo) is never silently treated as
"disabled" -- see PARTIALLY_CONFIGURED below and the enforcement in
uploads.py, which is what actually rejects uploads instead of writing them
to local disk when that happens.

Never import boto3 credentials or bucket details into templates/JS --
this module only ever hands back a public HTTPS URL, never a signed
upload URL or the credentials themselves.
"""
import os
import sys

import runtime_env

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID", "").strip()
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID", "").strip()
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY", "").strip()
R2_BUCKET_NAME = os.environ.get("R2_BUCKET_NAME", "").strip()
R2_PUBLIC_BASE_URL = os.environ.get("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
R2_ENDPOINT_URL = (
    os.environ.get("R2_ENDPOINT_URL", "").strip().rstrip("/")
    or (f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com" if R2_ACCOUNT_ID else "")
)

_R2_VALUES = {
    "R2_ACCOUNT_ID": R2_ACCOUNT_ID, "R2_ACCESS_KEY_ID": R2_ACCESS_KEY_ID,
    "R2_SECRET_ACCESS_KEY": R2_SECRET_ACCESS_KEY, "R2_BUCKET_NAME": R2_BUCKET_NAME,
    "R2_PUBLIC_BASE_URL": R2_PUBLIC_BASE_URL,
}

ENABLED = bool(
    R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME
    and R2_ENDPOINT_URL and R2_PUBLIC_BASE_URL
)

# Some but not all R2_* vars are set. There's no legitimate reason for this
# -- it's always a typo or an incomplete copy-paste of env vars -- so it's
# tracked separately from "not configured at all" and, in production,
# uploads.py rejects uploads outright rather than treating it as "R2 is
# just off" and quietly writing to local disk instead.
PARTIALLY_CONFIGURED = (not ENABLED) and any(_R2_VALUES.values())

# Explicit opt-in: once R2 is fully set up and you want a hard guarantee
# that uploads NEVER silently fall back to local disk again (not even
# during a future outage or misconfiguration), set R2_REQUIRED=true.
# Defaults to False so production keeps working via local-disk fallback
# during the period before R2 has been configured at all.
REQUIRED = os.environ.get("R2_REQUIRED", "").strip().lower() in ("1", "true", "yes")


def _startup_warning(lines):
    bar = "!" * 78
    print("\n" + bar + "\n" + "\n".join(f"! {line}" for line in lines) + "\n" + bar + "\n", file=sys.stderr)


if runtime_env.IS_PRODUCTION:
    if PARTIALLY_CONFIGURED:
        _missing = [k for k, v in _R2_VALUES.items() if not v]
        _startup_warning([
            "R2 YAPILANDIRMA HATASI: Bazı R2_* ortam değişkenleri tanımlı, bazıları değil.",
            f"Eksik olanlar: {', '.join(_missing)}.",
            "Bu neredeyse kesin bir yapılandırma hatasıdır (bkz. .env.example).",
            "Üretimde bu durumda yerel diske SESSİZCE düşülmez -- düzeltilene kadar",
            "medya yüklemeleri açık bir hatayla reddedilir.",
        ])
    elif not ENABLED:
        if REQUIRED:
            _startup_warning([
                "R2_REQUIRED=true ayarlanmış ama R2 hiç yapılandırılmamış.",
                "Üretimde düzeltilene kadar tüm medya yüklemeleri reddedilecek.",
            ])
        else:
            _startup_warning([
                "R2 depolama yapılandırılmamış -- üretimde medya şu an yerel diske",
                "(Railway'in kalıcı diskine) kaydediliyor. Cloudflare R2 kurulumunu",
                "tamamlayınca R2_* değişkenlerini ekleyin (bkz. CLOUDFLARE_SETUP.md).",
                "Kurulumdan sonra sessiz düşmeyi tamamen engellemek isterseniz",
                "R2_REQUIRED=true ayarlayabilirsiniz.",
            ])

_client = None


def _get_client():
    global _client
    if _client is None:
        import boto3
        from botocore.client import Config
        _client = boto3.client(
            "s3",
            endpoint_url=R2_ENDPOINT_URL,
            aws_access_key_id=R2_ACCESS_KEY_ID,
            aws_secret_access_key=R2_SECRET_ACCESS_KEY,
            config=Config(signature_version="s3v4"),
            region_name="auto",
        )
    return _client


def public_url(object_key):
    return f"{R2_PUBLIC_BASE_URL}/{object_key}"


def key_from_url(url):
    """The inverse of public_url() -- returns the object key if `url` points
    at our own R2 public domain, else None (e.g. it's a legacy local
    filename or some other host). Used to clean up the old object when a
    file is replaced or removed."""
    if not url or not R2_PUBLIC_BASE_URL:
        return None
    prefix = R2_PUBLIC_BASE_URL + "/"
    if url.startswith(prefix):
        return url[len(prefix):]
    return None


def upload_fileobj(fileobj, object_key, content_type=None,
                    cache_control="public, max-age=31536000, immutable"):
    """Streams a file-like object into the bucket and returns its public
    URL. Raises RuntimeError if R2 isn't configured -- callers should check
    ENABLED first and use the local-disk fallback instead."""
    if not ENABLED:
        raise RuntimeError("R2 depolama yapılandırılmamış.")
    extra = {"CacheControl": cache_control}
    if content_type:
        extra["ContentType"] = content_type
    try:
        fileobj.seek(0)
    except (AttributeError, OSError):
        pass
    _get_client().upload_fileobj(fileobj, R2_BUCKET_NAME, object_key, ExtraArgs=extra)
    return public_url(object_key)


def delete_object(object_key):
    """Best-effort delete -- never raises, since a failed cleanup of an old
    object should never block the request that's replacing it."""
    if not ENABLED or not object_key:
        return
    try:
        _get_client().delete_object(Bucket=R2_BUCKET_NAME, Key=object_key)
    except Exception:
        pass


def delete_url_if_ours(url):
    key = key_from_url(url)
    if key:
        delete_object(key)
