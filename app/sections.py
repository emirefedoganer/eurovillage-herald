SECTIONS = {
    "politika": {"label": "Politika", "order": 1},
    "sehir": {"label": "Şehir", "order": 2},
    "kultur": {"label": "Kültür", "order": 3},
    "roportaj": {"label": "Röportaj", "order": 4},
    "gorus": {"label": "Opinion", "order": 5},
    "oyun": {"label": "Oyun Köşesi", "order": 6},
    "magazin": {"label": "Magazin", "order": 7},
}

SECTION_ORDER = [s for s, _ in sorted(SECTIONS.items(), key=lambda kv: kv[1]["order"])]

# The ordinary Herald news sections: eligible for the main homepage feed and
# reachable via the generic /bolum/<section> listing route. "oyun" and
# "magazin" are deliberately excluded -- they're regular sections in the
# data model (an article can have section="magazin"), but editorially they
# are NOT ordinary Herald news: they have their own dedicated top-level
# pages (/oyun-kosesi, /magazin) with their own branding, and an article
# assigned to either must not also surface as a normal Herald homepage/
# section-listing story. This is the single place that distinction is
# encoded, so nothing else needs a hardcoded `!= "magazin"` check.
MAIN_SECTIONS = {"politika", "sehir", "kultur", "roportaj", "gorus"}

# Routes /bolum/<section> redirects to instead of listing generically, for
# sections that have their own dedicated page.
SPECIAL_SECTION_ROUTES = {"magazin": "magazin", "oyun": "oyun_kosesi"}

# The unified set of "context keys" a section-scoped ad placement can target.
# This is deliberately broader than MAIN_SECTIONS: besides the five ordinary
# news sections, it also covers the site's other standalone surfaces
# (the homepage, Arı Magazin's own home, and the PDF archive) so that ad
# targeting has one consistent vocabulary instead of a separate scheme per
# surface. See ads.py.
AD_CONTEXT_KEYS = MAIN_SECTIONS | {"magazin", "homepage", "gazete"}


def section_label(slug):
    return SECTIONS.get(slug, {}).get("label", slug)
