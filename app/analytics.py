"""First-party, privacy-conscious newspaper analytics.

What IS collected:
  - A per-browser, randomly generated reader_id (crypto.randomUUID(),
    kept in the browser's own localStorage -- see gazete_oku.html)
    identifies a BROWSER, never a person: it's never linked to an email,
    account, or IP address, and the reader can reset it any time by
    clearing site data.
  - issue_id, a coarse device category (mobile/tablet/desktop, from a
    simple viewport-width check -- not a fingerprint), and the referrer's
    HOST ONLY (e.g. "google.com", never the full URL/query string).
  - Page-turn behavior is summarized ONCE per reading session (highest
    page reached, total page count), sent as a single beacon when the
    reader leaves the page -- not a stream of per-page-turn events.

What is explicitly NOT collected or stored:
  - IP addresses are never written to disk anywhere in this module. A
    request's remote address is used only transiently, in-memory, by the
    existing rate limiter (ratelimit.py) to blunt abuse -- exactly like
    the login/contact-form limits already in this app -- and is never
    persisted.
  - No cross-site tracking, no fingerprinting, no third-party analytics
    script or pixel, no cookies set by this system.

Storage model: one compact AGGREGATE record per issue (see
store.load_issue_analytics/save_issue_analytics), not a raw, unbounded
per-event log -- consistent with every other JSON-file write in this
app, this is a read-modify-write with no cross-process locking, which is
an accepted trade-off at this project's scale (same trade-off already
documented in ratelimit.py) -- a concurrent burst of requests could
under-count by a handful of events, never crash or corrupt the file
(store._save() is still atomic per write).
"""
import re

import store

DEVICE_CATEGORIES = {"mobile", "tablet", "desktop"}


def _blank_issue_stats():
    return {
        "opens": 0, "sessions": 0, "downloads": 0, "share_actions": 0, "cta_clicks": 0,
        "total_pages_viewed": 0, "sessions_with_page_data": 0, "sessions_reached_last_page": 0,
        "reader_ids": [], "referrers": {}, "devices": {},
    }


def _clean_referrer_host(referrer):
    """Host only -- e.g. 'eurovillageherald.com' or 'google.com' -- never
    the path or query string. 'direct' for no referrer, 'diger' (other)
    for anything unparseable; never stores the raw referrer value, which
    could otherwise embed a token or query-string PII from the linking
    page."""
    if not referrer:
        return "direct"
    match = re.match(r"^https?://([^/]+)", referrer.strip())
    if not match:
        return "diger"
    host = re.sub(r"^www\.", "", match.group(1).lower())
    return host[:80]


def _device_category(value):
    value = (value or "").strip().lower()
    return value if value in DEVICE_CATEGORIES else "unknown"


def record_open(issue_id, reader_id, device=None, referrer=None):
    """One reading session started. `reader_id` is only ever used to
    dedupe 'opens' (unique readers) from 'sessions' (every open,
    including repeats) -- it is stored as an opaque token, nothing else
    is ever derived from it."""
    data = store.load_issue_analytics()
    stats = data.setdefault(issue_id, _blank_issue_stats())
    stats.setdefault("reader_ids", [])
    stats["sessions"] = stats.get("sessions", 0) + 1
    if reader_id and reader_id not in stats["reader_ids"]:
        stats["reader_ids"].append(reader_id)
        stats["opens"] = len(stats["reader_ids"])
    host = _clean_referrer_host(referrer)
    stats.setdefault("referrers", {})
    stats["referrers"][host] = stats["referrers"].get(host, 0) + 1
    dev = _device_category(device)
    stats.setdefault("devices", {})
    stats["devices"][dev] = stats["devices"].get(dev, 0) + 1
    store.save_issue_analytics(data)


def record_session_summary(issue_id, max_page, total_pages):
    """One beacon per reading session (sent on page close), not per page
    turn -- see gazete_oku.html. Silently ignored if the numbers look
    nonsensical (a probing/malformed request) rather than corrupting the
    aggregate with a negative or absurd value."""
    if not isinstance(max_page, int) or max_page < 0 or max_page > 100000:
        return
    data = store.load_issue_analytics()
    stats = data.setdefault(issue_id, _blank_issue_stats())
    stats["total_pages_viewed"] = stats.get("total_pages_viewed", 0) + max_page
    stats["sessions_with_page_data"] = stats.get("sessions_with_page_data", 0) + 1
    if total_pages and max_page >= total_pages:
        stats["sessions_reached_last_page"] = stats.get("sessions_reached_last_page", 0) + 1
    store.save_issue_analytics(data)


_EVENT_FIELDS = {"download": "downloads", "share": "share_actions", "cta_click": "cta_clicks"}


def record_event(issue_id, event_type):
    """Simple atomic +1 counters for discrete one-shot actions: a PDF
    download, a share action, or a 'Gazetede Oku' click from an article."""
    field = _EVENT_FIELDS.get(event_type)
    if not field:
        return
    data = store.load_issue_analytics()
    stats = data.setdefault(issue_id, _blank_issue_stats())
    stats[field] = stats.get(field, 0) + 1
    store.save_issue_analytics(data)


def issue_summary(issue_id):
    """Derived, display-ready stats for one issue's admin analytics card."""
    stats = store.load_issue_analytics().get(issue_id) or _blank_issue_stats()
    with_pages = stats.get("sessions_with_page_data", 0)
    avg_pages = round(stats.get("total_pages_viewed", 0) / with_pages, 1) if with_pages else 0.0
    completion_rate = round(100 * stats.get("sessions_reached_last_page", 0) / with_pages) if with_pages else 0
    return {
        "opens": stats.get("opens", 0),
        "sessions": stats.get("sessions", 0),
        "downloads": stats.get("downloads", 0),
        "share_actions": stats.get("share_actions", 0),
        "cta_clicks": stats.get("cta_clicks", 0),
        "avg_pages_viewed": avg_pages,
        "completion_rate_pct": completion_rate,
        "top_referrers": sorted(stats.get("referrers", {}).items(), key=lambda kv: -kv[1])[:5],
        "devices": stats.get("devices", {}),
    }


def all_issue_summaries():
    """issue_id -> summary for every issue with at least one recorded
    event -- powers the newspaper-wide analytics overview."""
    return {issue_id: issue_summary(issue_id) for issue_id in store.load_issue_analytics()}


# ------------------------------------------------------------- article views --
# Minimal, popularity-only counting -- a per-slug, per-day integer, never
# anything per-visitor (no reader_id here at all). This exists purely to
# let the bulletin CMS SUGGEST "most read this week" stories -- it is a
# suggestion input, never a decision-maker: see app/bulletins.py, which
# always leaves inclusion/exclusion/order/lead to the editor.

def record_article_view(slug):
    if not slug:
        return
    from datetime import datetime, timezone
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = store.load_article_views()
    per_day = data.setdefault(slug, {})
    per_day[day] = per_day.get(day, 0) + 1
    store.save_article_views(data)


def top_article_slugs(days=7, limit=10):
    """[(slug, view_count), ...] sorted by views over the last `days`
    days, most-viewed first. Empty if there's no view data yet (e.g. a
    fresh deploy) -- callers must fall back to manual selection in that
    case, never treat this as an error."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    data = store.load_article_views()
    totals = {}
    for slug, per_day in data.items():
        total = sum(count for day, count in per_day.items() if day >= cutoff_str)
        if total:
            totals[slug] = total
    return sorted(totals.items(), key=lambda kv: -kv[1])[:limit]
