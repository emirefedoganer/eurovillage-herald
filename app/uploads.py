"""Shared upload validation + storage routing for all admin file uploads
(article/author images, newspaper PDF editions and covers).

Every upload call site in app.py goes through here so that file-type/size
validation, filename sanitization and the "R2 if configured, else local
disk" decision live in exactly one place instead of being repeated per
route. See storage.py for the R2 client itself.
"""
import os
import re
import sys
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

import runtime_env
import storage

ALLOWED_IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
ALLOWED_PDF_EXT = {"pdf"}

MAX_IMAGE_MB = float(os.environ.get("MAX_IMAGE_UPLOAD_MB", "8"))
MAX_PDF_MB = float(os.environ.get("MAX_PDF_UPLOAD_MB", "40"))

IMAGE_CONTENT_TYPES = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "webp": "image/webp", "gif": "image/gif",
}


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
