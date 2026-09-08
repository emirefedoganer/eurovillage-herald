"""First-party advertising: slot catalog, placement precedence, and safe
HTML rendering for ad slots.

This is intentionally storage-agnostic Herald business logic layered on
top of store.py's plain CRUD -- so the same shapes (Advertisement,
Placement, slot catalog) could be reused by a future Musikforum site
without dragging in anything Herald-specific beyond the slot *names*
themselves (which are just data, defined once below).

Authorization is NOT handled here. Every route that creates, edits, or
deletes an ad or placement lives in app.py under `master_admin_required`,
exactly like the existing author-management routes -- this module only
answers "what should render", never "who is allowed to change it".
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

from flask import url_for
from markupsafe import Markup, escape

import store
from sections import AD_CONTEXT_KEYS, MAIN_SECTIONS

# ---------------------------------------------------------------- slots --
# Each slot is tagged with the "surface" it renders on (used to group the
# admin Slot dropdown by page type) and, where relevant, which AD_CONTEXT_KEYS
# values are valid for a "section"-scope placement targeting it, and whether
# it supports "content"-scope (a single specific article) placements at all.
#
# These are examples turned real: inspected against the actual templates
# (index.html, article.html, section.html, gazete.html, and the new
# magazin.html) rather than assumed. Two adjustments from a generic slot
# list: article.html has no sidebar in the current design, so no
# `article.sidebar_top` was invented for it; and rather than a bespoke
# `opinion.after_story_3` slot, Opinion is just section.html with
# section="gorus" like any other section listing, so it's targeted via a
# section-scoped placement on the shared `section.sidebar_top` slot instead
# of a one-off slot name -- one mechanism, not two.
SLOTS = {
    "homepage.top": {
        "label": "Anasayfa — Üst Banner", "surface": "homepage",
        "section_contexts": {"homepage"}, "supports_content_scope": False,
    },
    "homepage.after_news_4": {
        "label": "Anasayfa — Öne Çıkan Haberlerden Sonra", "surface": "homepage",
        "section_contexts": {"homepage"}, "supports_content_scope": False,
    },
    "homepage.sidebar_top": {
        "label": "Anasayfa — Kenar Çubuğu (Üst)", "surface": "homepage",
        "section_contexts": {"homepage"}, "supports_content_scope": False,
    },
    "homepage.bottom": {
        "label": "Anasayfa — Alt Banner", "surface": "homepage",
        "section_contexts": {"homepage"}, "supports_content_scope": False,
    },
    "article.after_paragraph_3": {
        "label": "Makale — 3. Paragraftan Sonra", "surface": "article",
        "section_contexts": MAIN_SECTIONS, "supports_content_scope": True,
    },
    "article.bottom": {
        "label": "Makale — Alt Banner", "surface": "article",
        "section_contexts": MAIN_SECTIONS, "supports_content_scope": True,
    },
    "section.sidebar_top": {
        "label": "Bölüm Listesi — Kenar Çubuğu (Politika/Şehir/Kültür/Röportaj/Opinion)", "surface": "section",
        "section_contexts": MAIN_SECTIONS, "supports_content_scope": False,
    },
    "magazine.home.top": {
        "label": "Arı Magazin Anasayfa — Üst Banner", "surface": "magazine_home",
        "section_contexts": {"magazin"}, "supports_content_scope": False,
    },
    "magazine.home.after_story_3": {
        "label": "Arı Magazin Anasayfa — 3. Yazıdan Sonra", "surface": "magazine_home",
        "section_contexts": {"magazin"}, "supports_content_scope": False,
    },
    "magazine.article.after_paragraph_2": {
        "label": "Arı Magazin Makalesi — 2. Paragraftan Sonra", "surface": "magazine_article",
        "section_contexts": {"magazin"}, "supports_content_scope": True,
    },
    "magazine.article.bottom": {
        "label": "Arı Magazin Makalesi — Alt Banner", "surface": "magazine_article",
        "section_contexts": {"magazin"}, "supports_content_scope": True,
    },
    "newspaper_archive.top": {
        "label": "Gazete Arşivi — Üst Banner", "surface": "newspaper_archive",
        "section_contexts": {"gazete"}, "supports_content_scope": False,
    },
}

SURFACE_LABELS = {
    "homepage": "Anasayfa", "article": "Makale (Herald)", "section": "Bölüm Listesi",
    "magazine_home": "Arı Magazin Anasayfa", "magazine_article": "Arı Magazin Makalesi",
    "newspaper_archive": "Gazete Arşivi",
}

CONTEXT_LABELS = {
    "politika": "Politika", "sehir": "Şehir", "kultur": "Kültür", "roportaj": "Röportaj",
    "gorus": "Opinion", "magazin": "Arı Magazin", "homepage": "Anasayfa", "gazete": "Gazete Arşivi",
}

SCOPE_LABELS = {"global": "Site geneli", "section": "Bölüme özel", "content": "Belirli bir makale"}

STATUS_LABELS = {"active": "Aktif", "scheduled": "Zamanlanmış", "expired": "Süresi Doldu", "inactive": "Pasif"}

AD_LABEL = "REKLAM"  # rendered on every ad slot -- see article.html/index.html/etc.

MAX_LENGTHS = {
    "internal_name": 120, "sponsor_name": 80, "headline": 120,
    "body": 400, "cta_text": 40,
}

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


def slot_choices():
    return sorted(SLOTS.items(), key=lambda kv: (kv[1]["surface"], kv[0]))


def valid_section_contexts_for_slot(slot):
    meta = SLOTS.get(slot)
    return meta["section_contexts"] if meta else set()


def slot_supports_content_scope(slot):
    meta = SLOTS.get(slot)
    return bool(meta and meta["supports_content_scope"])


def validate_destination_url(url):
    if not url or not _URL_RE.match(url.strip()):
        return "Hedef URL http:// veya https:// ile başlamalıdır."
    return None


def _now():
    return datetime.now(timezone.utc)


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def ad_effective_status(ad, now=None):
    """The single place that decides what state an ad is in right now --
    used by both the dashboard counts and the slot-resolution logic, so
    they can never disagree about whether an ad is live."""
    if not ad:
        return "inactive"
    if ad.get("status") != "active":
        return "inactive"
    now = now or _now()
    start = _parse_dt(ad.get("start_at"))
    end = _parse_dt(ad.get("end_at"))
    if start and now < start:
        return "scheduled"
    if end and now > end:
        return "expired"
    return "active"


def is_ad_live(ad, now=None):
    return ad_effective_status(ad, now) == "active"


def resolve_ad_for_slot(slot, section=None, content_id=None, now=None):
    """Precedence: a specific-article placement beats a section placement
    beats a global one, for the same slot. Only ever considers placements
    whose ad is CURRENTLY live (see is_ad_live) -- an expired or inactive
    ad at a more specific level does not blank out a slot that a broader
    placement could still fill; it's simply skipped as if it weren't there.
    Returns the winning Advertisement dict, or None if nothing applies."""
    now = now or _now()
    placements = [p for p in store.load_placements() if p["slot"] == slot]

    def live_ad(p):
        ad = store.get_ad(p["ad_id"])
        return ad if ad and is_ad_live(ad, now) else None

    if content_id:
        for p in placements:
            if p["scope"] == "content" and p.get("content_type") == "article" and p.get("content_id") == content_id:
                ad = live_ad(p)
                if ad:
                    return ad

    if section:
        for p in placements:
            if p["scope"] == "section" and p.get("section") == section:
                ad = live_ad(p)
                if ad:
                    return ad

    for p in placements:
        if p["scope"] == "global":
            ad = live_ad(p)
            if ad:
                return ad

    return None


def _resolve_image_url(value):
    """Same logic as app.py's `media_url()` Jinja global, duplicated here
    (rather than imported, to avoid a circular import with app.py) so an ad
    image renders correctly whether it's a full R2 URL or a legacy local
    filename from before R2 was configured."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return url_for("static", filename=f"img/ads/{value}")


def render_ad_html(ad):
    """A single, safe, responsive ad card. Every ad-authored field is
    explicitly escaped even though only master_admin can write them --
    defense in depth against a compromised admin account or a pasted
    payload, not just a trust boundary check."""
    if not ad:
        return Markup("")

    headline = escape(ad.get("headline") or "")
    sponsor = escape(ad.get("sponsor_name") or "")
    body = escape(ad.get("body") or "")
    cta = escape(ad.get("cta_text") or "")
    dest = ad.get("destination_url") or "#"
    if not _URL_RE.match(dest):
        dest = "#"  # never render a non-http(s) href, however it got stored
    dest = escape(dest)
    image = _resolve_image_url(ad.get("image"))

    img_html = ""
    if image:
        img_html = (
            f'<div class="ad-slot-media"><img src="{escape(image)}" alt="{headline}" loading="lazy"></div>'
        )

    body_html = f'<div class="ad-sub">{body}</div>' if body else ""
    cta_html = f'<span class="ad-cta">{cta} →</span>' if cta else ""

    return Markup(
        f'<div class="ad-box ad-slot">'
        f'<span class="ad-label">{AD_LABEL}</span>'
        f'<a class="ad-slot-link" href="{dest}" target="_blank" rel="sponsored noopener noreferrer">'
        f'{img_html}'
        f'<div class="ad-body">'
        f'<div class="ad-title">{headline}</div>'
        f'{body_html}'
        f'<div class="ad-handle">{sponsor}</div>'
        f'{cta_html}'
        f'</div>'
        f'</a>'
        f'</div>'
    )


def ad_slot(slot, section=None, content_id=None):
    """The template-facing entry point -- registered as the Jinja global
    `ad_slot()`. Resolves the winning placement for this (slot, context)
    and renders it, or returns an empty string (never a placeholder box)
    when nothing applies, so an empty slot leaves no visual footprint."""
    ad = resolve_ad_for_slot(slot, section=section, content_id=content_id)
    return render_ad_html(ad)


# ------------------------------------------------------------- CRUD glue --

def new_ad_id():
    return uuid.uuid4().hex[:10]


def new_placement_id():
    return uuid.uuid4().hex[:10]


def dashboard_stats(now=None):
    now = now or _now()
    ads = store.load_ads()
    placements = store.load_placements()
    soon = now + timedelta(days=7)
    counts = {"active": 0, "scheduled": 0, "expired": 0, "inactive": 0}
    expiring_soon = []
    for ad in ads:
        status = ad_effective_status(ad, now)
        counts[status] += 1
        end = _parse_dt(ad.get("end_at"))
        if status == "active" and end and now <= end <= soon:
            expiring_soon.append(ad)
    expiring_soon.sort(key=lambda a: a.get("end_at") or "")
    return {
        "total_ads": len(ads),
        "counts": counts,
        "total_placements": len(placements),
        "expiring_soon": expiring_soon,
    }
