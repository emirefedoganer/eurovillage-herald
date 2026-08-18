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


def section_label(slug):
    return SECTIONS.get(slug, {}).get("label", slug)
