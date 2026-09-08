"""Shared upload validation + storage routing for all admin file uploads
(article/author images, newspaper PDF editions and covers).

Every upload call site in app.py goes through here so that file-type/size
validation, filename sanitization and the "R2 if configured, else local
disk" decision live in exactly one place instead of being repeated per
route. See storage.py for the R2 client itself.
"""
import io
import os
import re
import sys
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

import runtime_env
import storage

try:
    from PIL import Image
except ImportError:
    Image = None

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
ALLOWED_PDF_EXT = {"pdf"}

MAX_IMAGE_MB = float(os.environ.get("MAX_IMAGE_UPLOAD_MB", "8"))
MAX_PDF_MB = float(os.environ.get("MAX_PDF_UPLOAD_MB", "40"))

IMAGE_CONTENT_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif",
}

# Reader-submitted tip images specifically: a narrower, safety-first raster
# allowlist (no GIF, and deliberately no SVG -- an XML format Pillow can't
# even decode, which rejects it for free). Decided by actually decoding the
# bytes with Pillow, not by trusting the extension or a client-sent MIME
# type, and always re-encoded before storage -- see validate_and_reencode_
# tip_image() -- which is what strips EXIF/GPS metadata as a side effect of
# a plain re-save.
TIP_ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
TIP_FORMAT_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
TIP_FORMAT_CONTENT_TYPE = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
MAX_TIP_IMAGES = int(os.environ.get("MAX_TIP_IMAGES", "4"))


def ext_of(filename):
    if not filename or "." not in filename:
        return None
    return filename.rsplit(".", 1)[1].lower()


def _file_size(file):
    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()
    file.stream.seek(0)
    return size


def validate_image(file):
    """Returns an error message (in Turkish, ready to flash) if `file` isn't
    an acceptable image upload, else None. Does not treat a missing file as
    an error -- callers decide whether an image is required."""
    if not file or not file.filename:
        return None
    ext = ext_of(file.filename)
    if ext not in ALLOWED_IMAGE_EXT:
        return "Desteklenmeyen görsel türü. İzin verilenler: " + ", ".join(sorted(ALLOWED_IMAGE_EXT))
    if _file_size(file) > MAX_IMAGE_MB * 1024 * 1024:
        return f"Görsel çok büyük (limit {MAX_IMAGE_MB:g} MB)."
    return None


def validate_pdf(file):
    if not file or not file.filename:
        return "Lütfen bir PDF dosyası seçin."
    ext = ext_of(file.filename)
    if ext not in ALLOWED_PDF_EXT:
        return "Yalnızca PDF dosyaları kabul edilir."
    if _file_size(file) > MAX_PDF_MB * 1024 * 1024:
        return f"PDF çok büyük (limit {MAX_PDF_MB:g} MB)."
    return None


_R2_MISCONFIGURED_MSG = (
    "R2 depolama yanlış yapılandırılmış (bazı R2_* ortam değişkenleri eksik). "
    "Üretimde bu durumda yükleme yerel diske düşürülmez -- lütfen ortam "
    "değişkenlerini düzeltin ve tekrar deneyin."
)
_R2_REQUIRED_BUT_MISSING_MSG = (
    "R2 depolama zorunlu kılınmış (R2_REQUIRED=true) ama yapılandırılmamış. "
    "Yükleme reddedildi."
)
_R2_UPLOAD_FAILED_MSG = (
    "R2'ye yükleme başarısız oldu. Lütfen tekrar deneyin; sorun sürerse "
    "yöneticinize bildirin."
)


def _store(file, object_key, local_dir, local_filename, content_type):
    """The actual write: R2 when configured, local disk otherwise. Returns
    (value, error) -- `value` is what to persist in the JSON record (a full
    https:// URL for R2, or a bare filename for local disk, rendered by
    either path via the `media_url()` Jinja helper in app.py), `error` is a
    ready-to-flash Turkish message if the upload could not be completed at
    all (in which case `value` is None and the caller must not save
    anything).

    Local-disk fallback only ever happens when R2 is simply not configured
    yet AND (in production) that hasn't been explicitly forbidden via
    R2_REQUIRED. A genuinely broken config (PARTIALLY_CONFIGURED) or an
    actual R2 API failure never falls back -- both are reported as a clean
    error instead of silently writing to local disk."""
    if storage.ENABLED:
        try:
            return storage.upload_fileobj(file, object_key, content_type=content_type), None
        except Exception as exc:
            print(f"[uploads] R2 yükleme hatası ({object_key}): {exc}", file=sys.stderr)
            return None, _R2_UPLOAD_FAILED_MSG

    if runtime_env.IS_PRODUCTION:
        if storage.PARTIALLY_CONFIGURED:
            return None, _R2_MISCONFIGURED_MSG
        if storage.REQUIRED:
            return None, _R2_REQUIRED_BUT_MISSING_MSG

    local_filename = secure_filename(local_filename)
    file.save(os.path.join(local_dir, local_filename))
    return local_filename, None


def save_article_image(file, slug, section, local_dir):
    """Article/magazine cover image. Object key mirrors the article slug so
    re-uploading (editing an article's image) simply overwrites the same
    key, matching the previous local-disk behavior."""
    err = validate_image(file)
    if err or not file or not file.filename:
        return None, err
    ext = ext_of(file.filename)
    folder = "gorseller/dergi" if section == "magazin" else "gorseller/haberler"
    object_key = f"{folder}/{slug}.{ext}"
    return _store(file, object_key, local_dir, f"{slug}.{ext}", IMAGE_CONTENT_TYPES.get(ext))


def save_author_image(file, author_id, subdir_prefix, local_dir):
    err = validate_image(file)
    if err or not file or not file.filename:
        return None, err
    ext = ext_of(file.filename)
    unique = uuid.uuid4().hex[:6]
    fname = f"{author_id}-{subdir_prefix}-{unique}.{ext}"
    object_key = f"gorseller/yazarlar/{fname}"
    return _store(file, object_key, local_dir, fname, IMAGE_CONTENT_TYPES.get(ext))


def save_ad_image(file, ad_id, local_dir):
    """Advertisement creative image. Keyed by ad id + a fresh uuid suffix
    (rather than overwriting a fixed key like article images do) so that
    replacing an ad's image doesn't require also deleting the old R2
    object before the new one becomes visible everywhere the ad renders --
    the caller (app.py) explicitly deletes the previous stored value via
    delete_stored() after a successful new upload, same pattern as author
    images."""
    err = validate_image(file)
    if err or not file or not file.filename:
        return None, err
    ext = ext_of(file.filename)
    unique = uuid.uuid4().hex[:6]
    fname = f"{ad_id}-{unique}.{ext}"
    object_key = f"gorseller/reklamlar/{fname}"
    return _store(file, object_key, local_dir, fname, IMAGE_CONTENT_TYPES.get(ext))


def save_management_image(file, entry_id, local_dir):
    """Optional photo override for a Gazete Yönetimi entry -- independent
    of that person's author profile photo (they may not have one, or the
    masthead may deliberately want a different picture)."""
    err = validate_image(file)
    if err or not file or not file.filename:
        return None, err
    ext = ext_of(file.filename)
    unique = uuid.uuid4().hex[:6]
    fname = f"{entry_id}-{unique}.{ext}"
    object_key = f"gorseller/yonetim/{fname}"
    return _store(file, object_key, local_dir, fname, IMAGE_CONTENT_TYPES.get(ext))


def issue_date_parts(date_str):
    try:
        dt = datetime.strptime((date_str or "")[:10], "%Y-%m-%d")
    except ValueError:
        dt = datetime.utcnow()
    return dt.year, dt.month


def save_issue_pdf(file, issue_id, date_str, local_dir):
    err = validate_pdf(file)
    if err:
        return None, err
    year, month = issue_date_parts(date_str)
    fname = f"{issue_id}.pdf"
    object_key = f"gazeteler/{year:04d}/{month:02d}/{fname}"
    return _store(file, object_key, local_dir, fname, "application/pdf")


def save_issue_cover(file, issue_id, date_str, local_dir):
    err = validate_image(file)
    if err or not file or not file.filename:
        return None, err
    ext = ext_of(file.filename)
    year, month = issue_date_parts(date_str)
    fname = f"{issue_id}-kapak.{ext}"
    object_key = f"gazeteler/{year:04d}/{month:02d}/{fname}"
    return _store(file, object_key, local_dir, fname, IMAGE_CONTENT_TYPES.get(ext))


def delete_stored(value):
    """Removes the R2 object behind `value` if it points at our own bucket;
    a legacy local filename is silently ignored (nothing to clean up)."""
    if value and re.match(r"^https?://", value):
        storage.delete_url_if_ours(value)


# ------------------------------------------------------- reader tip images --
# Deliberately separate from every function above: these are private,
# untrusted, visitor-submitted images, stored under their own R2 prefix
# (okur-ihbarlari/), never assigned a public URL, and never routed through
# _store()/storage.upload_fileobj (the public-media path). See storage.py's
# upload_private_fileobj/get_private_object and app.py's authenticated
# admin proxy route for how they're written and read back.

def validate_and_reencode_tip_image(file):
    """Decodes the upload with Pillow -- rejecting anything Pillow can't
    parse as one of TIP_ALLOWED_FORMATS, which is what actually rejects
    SVG/HTML/executables/mislabeled files by real content, not just by
    extension -- then re-encodes it to a fresh buffer. Returns
    (buffer, ext, content_type, error); re-encoding is also what strips
    EXIF/GPS, since Pillow never carries EXIF over on a plain re-save."""
    if not file or not file.filename:
        return None, None, None, None
    if Image is None:
        return None, None, None, "Görsel işleme şu anda kullanılamıyor."
    if _file_size(file) > MAX_IMAGE_MB * 1024 * 1024:
        return None, None, None, f"Görsel çok büyük (limit {MAX_IMAGE_MB:g} MB)."

    try:
        file.stream.seek(0)
        Image.open(file.stream).verify()
        file.stream.seek(0)
        img = Image.open(file.stream)
        img.load()
    except Exception:
        return None, None, None, "Desteklenmeyen veya bozuk görsel dosyası."

    fmt = (img.format or "").upper()
    if fmt not in TIP_ALLOWED_FORMATS:
        return None, None, None, "Yalnızca JPEG, PNG veya WebP görseller kabul edilir."

    if fmt == "JPEG" and img.mode not in ("RGB", "L"):
        img = img.convert("RGB")

    buffer = io.BytesIO()
    save_kwargs = {"quality": 88} if fmt in ("JPEG", "WEBP") else {}
    img.save(buffer, format=fmt, **save_kwargs)
    buffer.seek(0)
    return buffer, TIP_FORMAT_EXT[fmt], TIP_FORMAT_CONTENT_TYPE[fmt], None


def save_tip_image(file, local_dir):
    """Validates + re-encodes a reader-submitted tip image (see above),
    then stores it under okur-ihbarlari/YYYY/MM/<uuid>.<ext> -- a
    cryptographically random key, never the submitter's filename. Returns
    (object_key, content_type, sanitized_original_filename, error). The
    caller must never turn `object_key` into a public URL -- read it back
    only via storage.get_private_object() / the admin proxy route."""
    buffer, ext, content_type, error = validate_and_reencode_tip_image(file)
    original_name = secure_filename(file.filename)[:120] if file and file.filename else None
    if error or not buffer:
        return None, None, original_name, error

    now = datetime.utcnow()
    object_key = f"okur-ihbarlari/{now.year:04d}/{now.month:02d}/{uuid.uuid4().hex}.{ext}"

    if storage.ENABLED:
        # Once R2 is on at all, a reader tip is EITHER stored in the
        # dedicated private bucket OR the submission fails cleanly --
        # never the public media bucket, in any environment. This is
        # deliberately not conditioned on IS_PRODUCTION: falling back to
        # public storage would defeat the entire point of this feature
        # anywhere, not just in production.
        if not storage.PRIVATE_BUCKET_CONFIGURED:
            print(
                f"[uploads] okur ihbarı reddedildi: R2_PRIVATE_BUCKET_NAME tanımlı değil "
                f"(nesne herkese açık bucket'a yazılmayacaktı: {object_key})", file=sys.stderr,
            )
            return None, None, original_name, "Görsel depolama şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin."
        try:
            storage.upload_private_fileobj(buffer, object_key, content_type=content_type)
        except Exception as exc:
            print(f"[uploads] okur ihbarı yükleme hatası ({object_key}): {exc}", file=sys.stderr)
            return None, None, original_name, "Görsel yüklenemedi. Lütfen tekrar deneyin."
        return object_key, content_type, original_name, None

    if runtime_env.IS_PRODUCTION and (storage.PARTIALLY_CONFIGURED or storage.REQUIRED):
        return None, None, original_name, "Görsel depolama şu anda kullanılamıyor. Lütfen daha sonra tekrar deneyin."

    # Local-dev fallback -- only reachable when R2 isn't configured at all
    # (storage.ENABLED is False). Intentionally OUTSIDE app/static/, so
    # Flask's automatic static route can never accidentally serve these
    # publicly. Read back the same way via read_tip_image() below.
    local_path = os.path.join(local_dir, *object_key.split("/")[1:])
    os.makedirs(os.path.dirname(local_path), exist_ok=True)
    with open(local_path, "wb") as f:
        f.write(buffer.read())
    return object_key, content_type, original_name, None


def read_tip_image(object_key, local_dir):
    """Returns (file_like_or_bytes, content_type) for a stored tip image,
    trying R2 first (if configured) then the local-dev fallback directory.
    (None, None) if not found anywhere. Used only by the authenticated
    admin proxy route in app.py -- never reachable by an unauthenticated
    request."""
    if not object_key:
        return None, None
    if storage.ENABLED:
        return storage.get_private_object(object_key)
    local_path = os.path.join(local_dir, *object_key.split("/")[1:])
    if os.path.exists(local_path):
        ext = ext_of(local_path)
        return open(local_path, "rb"), IMAGE_CONTENT_TYPES.get(ext, "application/octet-stream")
    return None, None


def delete_tip_image(object_key, local_dir):
    if not object_key:
        return
    if storage.ENABLED:
        storage.delete_private_object(object_key)
        return
    local_path = os.path.join(local_dir, *object_key.split("/")[1:])
    try:
        os.remove(local_path)
    except OSError:
        pass
