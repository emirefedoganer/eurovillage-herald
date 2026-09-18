"""First-party advertising: slot catalog, targeting/selection algorithm,
and safe HTML rendering for ad slots.

This is intentionally storage-agnostic Herald business logic layered on
top of store.py's plain CRUD -- so the same shapes (Advertisement,
Placement, slot catalog) could be reused by a future Musikforum site
without dragging in anything Herald-specific beyond the slot *names*
themselves (which are just data, defined once below).

Authorization is NOT handled here. Every route that creates, edits, or
deletes an ad or placement lives in app.py under `master_admin_required`,
exactly like the existing author-management routes -- this module only
answers "what should render", never "who is allowed to change it".

Slot registry
--------------
Each entry in SLOTS is the single source of truth an admin's "Makalenin
kenarında" dropdown choice maps to -- a stable internal identifier
(the dict key, e.g. "article_sidebar") an admin never has to type, type
a route name for, or otherwise know exists as code. Every entry documents
where it renders, which page types/scopes it's compatible with, its
creative's aspect ratio, and its desktop/tablet/mobile behavior including
an explicit mobile fallback -- all surfaced in the admin placement form's
preview panel (see admin/placement_form.html), not just implied by code.

Targeting/selection algorithm
------------------------------
See resolve_ad_for_slot()'s docstring for the full specificity order and
tie-breaking rules. Summary: exact article > section > site-wide, and
-- new in this pass -- within a tier, a device-specific placement beats
an "all devices" one, ties broken by priority then a deterministic
(not random) weighted rotation, with same-page deduplication and a
configurable per-page cap.
"""
import hashlib
import re
import uuid
from datetime import date, datetime, timedelta, timezone

from flask import g, has_request_context, request, url_for
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
# (index.html, article.html, section.html, gazete.html, magazin.html)
# rather than assumed. Two adjustments from a generic slot list: Opinion
# is just section.html with section="gorus" like any other section
# listing, so it's targeted via a section-scoped placement on the shared
# `section.sidebar_top` slot instead of a one-off slot name -- one
# mechanism, not two. And `article_sidebar` (added this pass) is a real
# CSS grid column next to the article body (see article.html /
# .article-layout), not a cosmetic label on the old paragraph-3 slot --
# see its `mobile_fallback` below for what happens once that column no
# longer exists on a narrower viewport.
SLOTS = {
    "homepage_top": {
        "label": "Anasayfa — Üst Banner", "surface": "homepage",
        "description": "Anasayfanın en üstünde, manşet haberden önce görünen tam genişlikte banner.",
        "page_types": ["homepage"], "section_contexts": {"homepage"}, "supports_content_scope": False,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, sayfanın üst kısmında sabit konum.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "homepage_after_news_4": {
        "label": "Anasayfa — Öne Çıkan Haberlerden Sonra", "surface": "homepage",
        "description": "Anasayfadaki öne çıkan haber bloğu ile alt akış arasında, tam genişlikte.",
        "page_types": ["homepage"], "section_contexts": {"homepage"}, "supports_content_scope": False,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, içerik akışının ortasında.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "homepage_sidebar_top": {
        "label": "Anasayfa — Kenar Çubuğu (Üst)", "surface": "homepage",
        "description": "Anasayfanın sağ kenar çubuğunun en üstünde.",
        "page_types": ["homepage"], "section_contexts": {"homepage"}, "supports_content_scope": False,
        "aspect_ratio": "1:1 veya 4:3", "desktop_behavior": "Kenar çubuğunda, içerik akışının yanında.",
        "tablet_behavior": "Kenar çubuğu kalkar; reklam ana içeriğin altına, tam genişlikte taşınır.",
        "mobile_behavior": "Ana içeriğin altına, tam genişlikte taşınır.", "mobile_fallback": "after_content",
    },
    "homepage_bottom": {
        "label": "Anasayfa — Alt Banner", "surface": "homepage",
        "description": "Anasayfanın en altında, tam genişlikte.",
        "page_types": ["homepage"], "section_contexts": {"homepage"}, "supports_content_scope": False,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, sayfa sonu.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "article_sidebar": {
        "label": "Makale — Kenar Çubuğu (Makalenin Yanında)", "surface": "article",
        "description": "Makale gövdesinin yanında, ayrı bir dikey kenar çubuğunda görünür.",
        "page_types": ["article"], "section_contexts": MAIN_SECTIONS, "supports_content_scope": True,
        "aspect_ratio": "1:1 veya 4:3", "desktop_behavior": "Makale metninin sağında, yapışkan olmayan sabit bir sütunda.",
        "tablet_behavior": "Kenar çubuğu sütunu kalkar (yeterli genişlik yok); admin panelinde seçilen mobil davranışa göre gösterilir.",
        "mobile_behavior": "Kenar çubuğu sütunu yok; admin panelinde seçilen mobil davranışa göre gösterilir.",
        "mobile_fallback": "after_content",
        "mobile_fallback_choices": ["after_content", "hide"],
    },
    "article_after_paragraph_3": {
        "label": "Makale — 3. Paragraftan Sonra", "surface": "article",
        "description": "Makale metninin içine, 3. paragraftan hemen sonra gömülür.",
        "page_types": ["article"], "section_contexts": MAIN_SECTIONS, "supports_content_scope": True,
        "aspect_ratio": "16:9", "desktop_behavior": "Metin akışı içinde, tam genişlikte.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "article_bottom": {
        "label": "Makale — Alt Banner", "surface": "article",
        "description": "Makale metninin sonunda, ilgili haberlerden önce.",
        "page_types": ["article"], "section_contexts": MAIN_SECTIONS, "supports_content_scope": True,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, makale sonu.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "section_sidebar_top": {
        "label": "Bölüm Listesi — Kenar Çubuğu (Politika/Şehir/Kültür/Röportaj/Opinion)", "surface": "section",
        "description": "Bir bölümün haber listesi sayfasında, kenar çubuğunun üstünde.",
        "page_types": ["section"], "section_contexts": MAIN_SECTIONS, "supports_content_scope": False,
        "aspect_ratio": "1:1 veya 4:3", "desktop_behavior": "Kenar çubuğunda, liste ile aynı hizada.",
        "tablet_behavior": "Kenar çubuğu kalkar; reklam listenin altına, tam genişlikte taşınır.",
        "mobile_behavior": "Listenin altına, tam genişlikte taşınır.", "mobile_fallback": "after_content",
    },
    "magazine_home_top": {
        "label": "Arı Magazin Anasayfa — Üst Banner", "surface": "magazine_home",
        "description": "Arı Magazin bölüm sayfasının en üstünde.",
        "page_types": ["magazine_home"], "section_contexts": {"magazin"}, "supports_content_scope": False,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, sayfanın üstü.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "magazine_home_after_story_3": {
        "label": "Arı Magazin Anasayfa — 3. Yazıdan Sonra", "surface": "magazine_home",
        "description": "Arı Magazin bölüm sayfasında, 3. yazıdan sonra.",
        "page_types": ["magazine_home"], "section_contexts": {"magazin"}, "supports_content_scope": False,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, içerik akışının ortasında.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "magazine_article_sidebar": {
        "label": "Arı Magazin Makalesi — Kenar Çubuğu", "surface": "magazine_article",
        "description": "Arı Magazin makalesinin yanında, ayrı bir dikey kenar çubuğunda.",
        "page_types": ["magazine_article"], "section_contexts": {"magazin"}, "supports_content_scope": True,
        "aspect_ratio": "1:1 veya 4:3", "desktop_behavior": "Makale metninin sağında, sabit bir sütunda.",
        "tablet_behavior": "Kenar çubuğu sütunu kalkar; admin panelinde seçilen mobil davranışa göre gösterilir.",
        "mobile_behavior": "Kenar çubuğu sütunu yok; admin panelinde seçilen mobil davranışa göre gösterilir.",
        "mobile_fallback": "after_content",
        "mobile_fallback_choices": ["after_content", "hide"],
    },
    "magazine_article_after_paragraph_2": {
        "label": "Arı Magazin Makalesi — 2. Paragraftan Sonra", "surface": "magazine_article",
        "description": "Arı Magazin makalesinin içine, 2. paragraftan hemen sonra gömülür.",
        "page_types": ["magazine_article"], "section_contexts": {"magazin"}, "supports_content_scope": True,
        "aspect_ratio": "16:9", "desktop_behavior": "Metin akışı içinde, tam genişlikte.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "magazine_article_bottom": {
        "label": "Arı Magazin Makalesi — Alt Banner", "surface": "magazine_article",
        "description": "Arı Magazin makalesinin sonunda.",
        "page_types": ["magazine_article"], "section_contexts": {"magazin"}, "supports_content_scope": True,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, makale sonu.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
    "newspaper_archive_top": {
        "label": "Gazete Arşivi — Üst Banner", "surface": "newspaper_archive",
        "description": "Gazete arşivi (PDF sayı listesi) sayfasının en üstünde.",
        "page_types": ["newspaper_archive"], "section_contexts": {"gazete"}, "supports_content_scope": False,
        "aspect_ratio": "16:9", "desktop_behavior": "Tam genişlikte, sayı kartlarından önce.",
        "tablet_behavior": "Aynı konum, genişlik ekrana göre daralır.",
        "mobile_behavior": "Aynı konum, tek sütun genişliğinde.", "mobile_fallback": None,
    },
}

# ---------------------------------------------------- legacy slot migration --
# Slot identifiers were renamed from dotted (`homepage.top`) to underscored
# (`homepage_top`) form when this registry gained the extra per-slot
# metadata above -- underscores read more like a stable code identifier
# (matching e.g. Python/JSON conventions elsewhere in this codebase) than
# a dotted "template path"-looking string, which is exactly the kind of
# implementation detail an admin was never supposed to have to read in
# the first place. LEGACY_SLOT_ALIASES maps every old key to its new one;
# _normalize_slot() is the ONE place that translation happens, called by
# every read path (resolve_ad_for_slot, the admin placements list, slot
# metadata lookups) so an existing placement record on disk keeps working
# and displaying correctly without a data migration script rewriting
# ad_placements.json. A placement is only ever REWRITTEN to the new key
# if an admin re-saves it through the form; until then the old string
# stays in storage and is translated on every read, indefinitely.
LEGACY_SLOT_ALIASES = {
    "homepage.top": "homepage_top",
    "homepage.after_news_4": "homepage_after_news_4",
    "homepage.sidebar_top": "homepage_sidebar_top",
    "homepage.bottom": "homepage_bottom",
    "article.after_paragraph_3": "article_after_paragraph_3",
    "article.bottom": "article_bottom",
    "section.sidebar_top": "section_sidebar_top",
    "magazine.home.top": "magazine_home_top",
    "magazine.home.after_story_3": "magazine_home_after_story_3",
    "magazine.article.after_paragraph_2": "magazine_article_after_paragraph_2",
    "magazine.article.bottom": "magazine_article_bottom",
    "newspaper_archive.top": "newspaper_archive_top",
}


def _normalize_slot(slot):
    """Translates a legacy dotted slot identifier to its current key.
    An identifier that isn't in the current registry AND isn't a known
    legacy alias is returned unchanged -- resolve_ad_for_slot() then finds
    no matching SLOTS entry for it and safely skips it (never crashes),
    and the admin UI shows it labeled with the raw string plus a clear
    "bilinmeyen/eski slot" marker rather than hiding it -- see
    unknown_slot_labels() below and placements_list()'s use of it."""
    if slot in SLOTS:
        return slot
    return LEGACY_SLOT_ALIASES.get(slot, slot)


def is_known_slot(slot):
    return _normalize_slot(slot) in SLOTS


def slot_meta(slot):
    return SLOTS.get(_normalize_slot(slot))


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

DEVICE_CHOICES = [("all", "Tüm cihazlar"), ("desktop", "Yalnızca masaüstü"),
                   ("tablet", "Yalnızca tablet"), ("mobile", "Yalnızca mobil")]
DEVICE_LABELS = dict(DEVICE_CHOICES)

MOBILE_FALLBACK_LABELS = {
    "after_content": "İçeriğin altında göster", "hide": "Gösterme (bu cihazda reklam yok)",
}

AD_LABEL = "REKLAM"  # rendered on every ad slot -- see article.html/index.html/etc.

MAX_LENGTHS = {
    "internal_name": 120, "sponsor_name": 80, "headline": 120,
    "body": 400, "cta_text": 40, "alt_text": 150,
}

# Configurable ceiling on how many real ads (not placeholders -- an empty
# slot never counts) a single page render may show, across every slot on
# that page combined. Deliberately conservative: a newsroom this size has
# a handful of slots per page at most, so this only ever matters as a
# safety net against a future page design that stacks many slots at once.
MAX_ADS_PER_PAGE = 6

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_MOBILE_UA_RE = re.compile(r"iphone|ipod|android.+mobile|windows phone|blackberry", re.IGNORECASE)
_TABLET_UA_RE = re.compile(r"ipad|android(?!.+mobile)|tablet", re.IGNORECASE)


def slot_choices():
    return sorted(SLOTS.items(), key=lambda kv: (kv[1]["surface"], kv[0]))


def valid_section_contexts_for_slot(slot):
    meta = slot_meta(slot)
    return meta["section_contexts"] if meta else set()


def slot_supports_content_scope(slot):
    meta = slot_meta(slot)
    return bool(meta and meta["supports_content_scope"])


def slot_mobile_fallback_choices(slot):
    meta = slot_meta(slot)
    return (meta or {}).get("mobile_fallback_choices") or []


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
    they can never disagree about whether an ad is live. Dates are stored
    in UTC (see app.py's editorial_tz.local_input_to_utc_iso, which
    interprets the admin's input as Europe/Istanbul / the configured
    EDITORIAL_TIMEZONE before converting) -- comparison here is a plain
    UTC-to-UTC comparison, so the timezone interpretation only has to
    happen once, at write time."""
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


def detect_device(user_agent_string=None):
    """A coarse, heuristic device category from the request's User-Agent
    -- "desktop", "tablet", or "mobile". Same spirit as analytics.py's
    client-reported _device_category(): good enough to pick a reasonable
    ad creative/placement, never treated as a precise or spoof-proof
    signal, and never stored anywhere. Falls back to "desktop" when
    running outside a request (a script, a test) or when the UA string is
    empty/unrecognized -- the safest default, since it's this site's most
    common real-world case and never accidentally hides an ad a desktop
    visitor should see."""
    if user_agent_string is None:
        if not has_request_context():
            return "desktop"
        user_agent_string = request.headers.get("User-Agent", "")
    if not user_agent_string:
        return "desktop"
    if _MOBILE_UA_RE.search(user_agent_string):
        return "mobile"
    if _TABLET_UA_RE.search(user_agent_string):
        return "tablet"
    return "desktop"


def _device_matches(placement_device, current_device):
    target = (placement_device or "all").lower()
    return target == "all" or target == current_device


def _page_ad_state():
    """Per-request state (shown-ad ids for deduplication, and a running
    count against MAX_ADS_PER_PAGE) -- lives on Flask's `g`, which is
    already request-scoped and torn down automatically at the end of
    every request, exactly like _resolve_logged_in_user()'s caching
    pattern elsewhere in this app. Falls back to a plain dict outside a
    request context (tests, scripts) so resolve_ad_for_slot() never
    requires a live Flask request just to be called directly."""
    if not has_request_context():
        return {"shown_ad_ids": set(), "count": 0}
    if not hasattr(g, "_ad_page_state"):
        g._ad_page_state = {"shown_ad_ids": set(), "count": 0}
    return g._ad_page_state


def reset_page_ad_state():
    """Only needed by tests that call resolve_ad_for_slot()/ad_slot()
    multiple times outside a real per-request Flask lifecycle and need a
    fresh page's worth of dedup/max-per-page budget each time."""
    if has_request_context():
        g._ad_page_state = {"shown_ad_ids": set(), "count": 0}


def _deterministic_choice(candidates, salt):
    """A documented, explainable rotation -- NOT Python's random module.
    `candidates` is a list of (placement, ad) pairs (see
    _select_from_tier); weighted by each candidate AD's `weight` (an ad
    missing/with an invalid weight defaults to 1), seeded by today's date
    plus `salt` (typically the slot name) so: the same slot on the same
    day always resolves the same way (testable, reproducible, cache-
    friendly), it still rotates day to day, and two different slots don't
    happen to always pick the same ad out of a shared candidate pool just
    because they resolved at the same moment. Returns one (placement, ad)
    pair from `candidates`."""
    if len(candidates) == 1:
        return candidates[0]
    weights = []
    for _p, ad in candidates:
        try:
            w = int(ad.get("weight", 1) or 1)
        except (TypeError, ValueError):
            w = 1
        weights.append(max(w, 1))
    total = sum(weights)
    seed_str = f"{date.today().isoformat()}:{salt}"
    seed = int(hashlib.sha256(seed_str.encode("utf-8")).hexdigest(), 16)
    point = seed % total
    running = 0
    for pair, w in zip(candidates, weights):
        running += w
        if point < running:
            return pair
    return candidates[-1]


def _select_from_tier(placements, device, salt):
    """Given every placement eligible at ONE specificity tier (already
    filtered to currently-live ads by the caller), applies: device
    match (a device-specific placement beats "all devices" if both
    exist), then priority (highest wins), then the deterministic weighted
    rotation above -- skipping any ad already shown elsewhere on this
    page or past the per-page cap, falling through to the next-best
    candidate rather than leaving the slot empty when a perfectly good
    alternative was available. Returns None if nothing in this tier
    survives every filter."""
    state = _page_ad_state()
    if state["count"] >= MAX_ADS_PER_PAGE:
        return None

    eligible = [(p, ad) for p, ad in placements if _device_matches(p.get("device_target"), device)]
    if not eligible:
        return None

    device_specific = [(p, ad) for p, ad in eligible if (p.get("device_target") or "all").lower() != "all"]
    pool = device_specific or eligible

    # Exclude ads already shown on this page, then rank by priority.
    pool = [(p, ad) for p, ad in pool if ad["id"] not in state["shown_ad_ids"]]
    if not pool:
        return None
    max_priority = max(_ad_priority(ad) for _, ad in pool)
    top_tier = [(p, ad) for p, ad in pool if _ad_priority(ad) == max_priority]

    chosen_p, chosen_ad = _deterministic_choice(top_tier, salt)
    return chosen_p, chosen_ad


def _ad_priority(ad):
    try:
        return int(ad.get("priority", 0) or 0)
    except (TypeError, ValueError):
        return 0


def resolve_ad_for_slot(slot, section=None, content_id=None, device=None, now=None):
    """The predictable, testable ad selection algorithm. Specificity
    order (highest wins) for a given slot:

      1. An exact-article (scope="content") placement targeting this
         article.
      2. A section-scoped placement targeting this section.
      3. A site-wide (scope="global") placement.

    The slot itself already encodes page type (e.g. `homepage_top` only
    ever applies to the homepage), so there's no separate "page-type"
    tier to check -- it's baked into which slot a caller even asks for.

    Within a tier, see _select_from_tier(): a device-specific placement
    beats an "all devices" one, ties broken by priority then a
    deterministic (never truly random) weighted rotation. An ad already
    shown elsewhere on this page (dedup) or once MAX_ADS_PER_PAGE is
    reached is skipped in favor of the next-best remaining candidate,
    falling through to a LESS specific tier only if the more specific
    tier has no survivors at all -- so a content-scoped placement that's
    merely deduped never accidentally exposes a broader section/global ad
    the editor didn't intend for that exact spot when a specific one was
    configured; it only happens when nothing else was left to show.

    Only ever considers placements whose ad is CURRENTLY live
    (is_ad_live) -- an expired or inactive ad at a more specific level
    does not blank out a slot a broader placement could still fill; it's
    skipped as if it weren't there.

    Returns the winning Advertisement dict, or None if nothing applies."""
    now = now or _now()
    device = device or detect_device()
    slot = _normalize_slot(slot)
    placements = [p for p in store.load_placements() if _normalize_slot(p["slot"]) == slot]

    def live_pairs(pred):
        pairs = []
        for p in placements:
            if not pred(p):
                continue
            ad = store.get_ad(p["ad_id"])
            if ad and is_ad_live(ad, now):
                pairs.append((p, ad))
        return pairs

    tiers = []
    if content_id:
        tiers.append(live_pairs(lambda p: p["scope"] == "content" and p.get("content_type") == "article"
                                 and p.get("content_id") == content_id))
    if section:
        tiers.append(live_pairs(lambda p: p["scope"] == "section" and p.get("section") == section))
    tiers.append(live_pairs(lambda p: p["scope"] == "global"))

    for tier_pairs in tiers:
        if not tier_pairs:
            continue
        result = _select_from_tier(tier_pairs, device, salt=slot)
        if result:
            chosen_p, chosen_ad = result
            state = _page_ad_state()
            state["shown_ad_ids"].add(chosen_ad["id"])
            state["count"] += 1
            return chosen_ad

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


def render_ad_html(ad, extra_class=""):
    """A single, safe, responsive ad card. Every ad-authored field is
    explicitly escaped even though only master_admin can write them --
    defense in depth against a compromised admin account or a pasted
    payload, not just a trust boundary check. `extra_class` (e.g.
    "ad-slot-hide-mobile" for a sidebar placement whose configured mobile
    fallback is "do not display") is a plain CSS hook -- never used to
    hide content generally, only this specific, admin-chosen per-
    placement behavior."""
    if not ad:
        return Markup("")

    headline = escape(ad.get("headline") or "")
    sponsor = escape(ad.get("sponsor_name") or "")
    body = escape(ad.get("body") or "")
    cta = escape(ad.get("cta_text") or "")
    alt_text = escape(ad.get("alt_text") or ad.get("headline") or "")
    dest = ad.get("destination_url") or "#"
    if not _URL_RE.match(dest):
        dest = "#"  # never render a non-http(s) href, however it got stored
    dest = escape(dest)
    image = _resolve_image_url(ad.get("image"))

    img_html = ""
    if image:
        img_html = (
            f'<div class="ad-slot-media"><img src="{escape(image)}" alt="{alt_text}" loading="lazy"></div>'
        )

    body_html = f'<div class="ad-sub">{body}</div>' if body else ""
    cta_html = f'<span class="ad-cta">{cta} →</span>' if cta else ""
    css_class = f"ad-box ad-slot {escape(extra_class)}".strip()

    return Markup(
        f'<div class="{css_class}">'
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


def ad_slot(slot, section=None, content_id=None, device=None):
    """The template-facing entry point -- registered as the Jinja global
    `ad_slot()`. Resolves the winning placement for this (slot, context)
    and renders it, or returns an empty string (never a placeholder box)
    when nothing applies, so an empty slot leaves no visual footprint."""
    ad = resolve_ad_for_slot(slot, section=section, content_id=content_id, device=device)
    if not ad:
        return Markup("")
    extra_class = ""
    if slot_mobile_fallback_choices(slot):
        placements = [p for p in store.load_placements() if _normalize_slot(p["slot"]) == _normalize_slot(slot)]
        matching = next((p for p in placements if p["ad_id"] == ad["id"]), None)
        if matching and (matching.get("mobile_fallback") or "after_content") == "hide":
            extra_class = "ad-slot-hide-narrow"
    return render_ad_html(ad, extra_class=extra_class)


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
    unknown_slots = sorted({p["slot"] for p in placements if not is_known_slot(p["slot"])})
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
        "unknown_slots": unknown_slots,
    }
