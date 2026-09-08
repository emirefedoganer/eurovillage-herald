"""Editorial-facing timezone conversion for scheduling UIs (article
publication, advertising start/end dates).

Storage stays UTC everywhere -- store.py's/ads.py's scheduling logic,
restart-safety, and comparisons against "now" are all completely
unaffected and untouched by this module. This only converts at the two
edges: what an editor types into a datetime-local field (assumed to be in
EDITORIAL_TIMEZONE) on the way in, and what's shown back to them (in
EDITORIAL_TIMEZONE) on the way out. Preview-link expiry is deliberately
NOT converted here -- it's a security token lifetime, not an editorial
publication time, so it keeps using plain UTC datetime-local semantics.
"""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

EDITORIAL_TIMEZONE_NAME = os.environ.get("EDITORIAL_TIMEZONE", "UTC").strip() or "UTC"

try:
    EDITORIAL_TZ = ZoneInfo(EDITORIAL_TIMEZONE_NAME)
except Exception:
    EDITORIAL_TZ = ZoneInfo("UTC")
    EDITORIAL_TIMEZONE_NAME = "UTC"

UTC = ZoneInfo("UTC")


def local_input_to_utc_iso(value):
    """A "YYYY-MM-DDTHH:MM"(:SS) datetime-local form value, interpreted as
    EDITORIAL_TZ wall-clock time, converted to a UTC ISO8601 string ending
    in Z (the exact shape store.py/ads.py already expect and compare
    against). None for empty/unparseable input."""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) == 16:
        value += ":00"
    try:
        naive = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=EDITORIAL_TZ).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_iso_to_local_input(value):
    """Inverse: a stored UTC ISO string -> "YYYY-MM-DDTHH:MM" for a
    datetime-local field's `value` attribute, in EDITORIAL_TZ wall-clock
    time. Empty string (never None) for missing/unparseable input, since
    this is meant to go straight into a template attribute.

    Some admin forms redisplay this same field from request.form after a
    validation error, so this may also be handed an already-local,
    timezone-naive "YYYY-MM-DDTHH:MM" value the editor just typed -- that
    case is passed through unchanged rather than mis-interpreted as UTC
    (which would silently shift it)."""
    dt = _parse(value)
    if not dt:
        return ""
    if dt.tzinfo is None:
        return dt.strftime("%Y-%m-%dT%H:%M")
    return dt.astimezone(EDITORIAL_TZ).strftime("%Y-%m-%dT%H:%M")


def utc_iso_to_local_display(value, fmt="%Y-%m-%d %H:%M"):
    """Inverse for read-only display (dashboard badges, flash messages,
    preview banners) -- same conversion, human-readable format. Stored
    values are always UTC-aware by the time they reach here in practice,
    but a naive value is still handled safely (see utc_iso_to_local_input)."""
    dt = _parse(value)
    if not dt:
        return ""
    if dt.tzinfo is None:
        return dt.strftime(fmt)
    return dt.astimezone(EDITORIAL_TZ).strftime(fmt)


def _parse(value):
    if not value:
        return None
    value = value.strip()
    if len(value) == 16:
        value += ":00"
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
