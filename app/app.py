import os
import re
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import Flask, Blueprint, render_template, request, redirect, url_for, session, abort, flash, jsonify, send_file, Response, g
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from werkzeug.security import check_password_hash, generate_password_hash

import ads
import cf_access
import editorial_tz
import store
import games_engine as ge
import games_export
import ratelimit
import storage
import turnstile
import uploads
from minecraft_service import MinecraftProfileService
from sections import (
    SECTIONS, SECTION_ORDER, section_label, MAIN_SECTIONS, SPECIAL_SECTION_ROUTES,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ARTICLE_IMG_DIR = os.path.join(BASE_DIR, "static", "img", "articles")
ISSUE_PDF_DIR = os.path.join(BASE_DIR, "static", "issues")
AUTHOR_IMG_DIR = os.path.join(BASE_DIR, "static", "img", "authors")
AD_IMG_DIR = os.path.join(BASE_DIR, "static", "img", "ads")
MANAGEMENT_IMG_DIR = os.path.join(BASE_DIR, "static", "img", "management")
# Reader-tip images' local-dev fallback -- deliberately OUTSIDE static/, so
# Flask's automatic /static/<path> route can never serve one of these
# directly. In production these go to R2 under okur-ihbarlari/ instead
# (see uploads.save_tip_image / storage.py's private-object functions).
TIP_IMG_DIR = os.path.join(BASE_DIR, "private_uploads", "okur_ihbarlari")
SECRET_PATH = os.path.join(BASE_DIR, "data", ".secret_key")

TWITTER_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
MINECRAFT_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,16}$")


def sanitize_twitter_handle(raw):
    """Accepts a bare handle or a pasted profile URL; stores only the handle,
    never a full URL. Returns None (not an empty string) when invalid/blank
    so callers can cleanly omit the field rather than store garbage."""
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"^https?://(www\.)?(twitter|x)\.com/", "", raw, flags=re.IGNORECASE)
    raw = raw.lstrip("@").strip()
    if not raw or not TWITTER_HANDLE_RE.match(raw):
        return None
    return raw


def sanitize_minecraft_username(raw):
    if not raw:
        return None
    raw = raw.strip()
    if not MINECRAFT_USERNAME_RE.match(raw):
        return None
    return raw


CONTACT_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def sanitize_contact_email(raw):
    """Public byline contact address -- deliberately separate from the
    account's private login email, so an author can publish a contact
    address without exposing (or being limited to) their login identity."""
    if not raw:
        return None
    raw = raw.strip()
    if not CONTACT_EMAIL_RE.match(raw):
        return None
    return raw


def generate_temp_password():
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))

CONTACT_SUBJECTS = [
    "İhbar",
    "Düzeltme Talebi",
    "Okur Görüşü",
    "Teknik Sorun",
    "Diğer",
]

MESSAGE_STATUS_LABELS = {
    "new": "Yeni", "reviewing": "İnceleniyor", "replied": "Yanıtlandı", "archived": "Arşivlendi",
}
MESSAGE_STATUS_ORDER = ["new", "reviewing", "replied", "archived"]

# In production, set SERVER_NAME (e.g. "eurovillageherald.com") as an environment
# variable. When set, the admin panel is served ONLY from admin.<SERVER_NAME> and is
# completely unreachable from the public site's domain/path. Without it (local dev),
# the admin panel falls back to <host>/admin/ on the same origin so it keeps working
# without any DNS setup. See README.md for deployment instructions.
SERVER_NAME = os.environ.get("SERVER_NAME", "").strip() or None


def get_or_create_secret():
    if os.path.exists(SECRET_PATH):
        with open(SECRET_PATH, "r") as f:
            return f.read().strip()
    key = os.urandom(32).hex()
    with open(SECRET_PATH, "w") as f:
        f.write(key)
    return key


app = Flask(__name__, subdomain_matching=True)
app.secret_key = get_or_create_secret()

# Upper bound on any request body Flask will even start reading (covers the
# largest allowed upload -- either a single PDF edition, or a multi-image
# reader tip submission -- see uploads.py for the per-file-type limits
# actually enforced). Rejects oversized requests before they're fully
# received rather than after.
app.config["MAX_CONTENT_LENGTH"] = int(
    max(uploads.MAX_IMAGE_MB * uploads.MAX_TIP_IMAGES, uploads.MAX_PDF_MB) * 1024 * 1024
) + (1024 * 1024)

# SameSite=Lax on every cookie the app sets (session + the visitor-gate
# cookie in this file) regardless of environment; Secure only in production
# (SERVER_NAME set implies HTTPS -- see PREFERRED_URL_SCHEME above), since a
# Secure cookie can't be set at all over plain-http local development.
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(SERVER_NAME)

if SERVER_NAME:
    app.config["SERVER_NAME"] = SERVER_NAME
    app.config["PREFERRED_URL_SCHEME"] = os.environ.get("PREFERRED_URL_SCHEME", "https")

    class _NormalizeWWWHost:
        """With SERVER_NAME set, Flask's subdomain matching only matches the
        bare domain -- www.<SERVER_NAME> is a distinct, unmatched subdomain
        and 404s on every route. Rather than duplicating every route under
        subdomain="www", rewrite the Host header to the bare domain before
        Werkzeug's routing ever sees it, so both hostnames serve identically."""
        def __init__(self, wsgi_app, canonical_host):
            self.wsgi_app = wsgi_app
            self.www_host = "www." + canonical_host

        def __call__(self, environ, start_response):
            host = environ.get("HTTP_HOST", "")
            if host == self.www_host or host.startswith(self.www_host + ":"):
                environ["HTTP_HOST"] = host.replace("www.", "", 1)
            return self.wsgi_app(environ, start_response)

    app.wsgi_app = _NormalizeWWWHost(app.wsgi_app, SERVER_NAME)

os.makedirs(ARTICLE_IMG_DIR, exist_ok=True)
os.makedirs(ISSUE_PDF_DIR, exist_ok=True)
os.makedirs(AUTHOR_IMG_DIR, exist_ok=True)
os.makedirs(AD_IMG_DIR, exist_ok=True)
os.makedirs(MANAGEMENT_IMG_DIR, exist_ok=True)
os.makedirs(TIP_IMG_DIR, exist_ok=True)

# Available to every template without each view having to pre-resolve authors
# for every article it passes along (cards, river items, related lists...).
app.jinja_env.globals["resolve_authors"] = store.resolve_article_authors
app.jinja_env.globals["article_pullquote"] = store.article_pullquote
app.jinja_env.globals["article_lead_quote"] = store.article_lead_quote


def versioned_static(filename):
    """A static asset URL with a cache-busting query string based on the
    file's own mtime. CSS/JS are served from a fixed URL that never changes
    on its own, so an edge cache (Cloudflare) or a browser has no signal to
    refetch after a deploy -- this forces a new URL (and therefore a real
    fetch) exactly when the file itself actually changes, with zero manual
    cache-purging required on every future deploy."""
    path = os.path.join(app.static_folder, filename)
    try:
        version = int(os.path.getmtime(path))
    except OSError:
        version = 0
    return url_for("static", filename=filename) + f"?v={version}"


app.jinja_env.globals["versioned_static"] = versioned_static


def media_url(value, legacy_subdir, external=False):
    """Renders any stored image/PDF reference regardless of where it lives:
    a full R2 (matbaa.eurovillageherald.com) URL is returned as-is; a bare
    legacy filename (from before R2 was configured, or from a deployment
    that still hasn't set it up) falls back to the local static path exactly
    as it always has. This is what lets old records keep working unchanged
    after R2 is introduced, with no data migration required."""
    if not value:
        return None
    if value.startswith("http://") or value.startswith("https://"):
        return value
    return url_for("static", filename=f"{legacy_subdir}/{value}", _external=external)


app.jinja_env.globals["media_url"] = media_url
app.jinja_env.globals["turnstile_site_key"] = turnstile.SITE_KEY if turnstile.ENABLED else None
app.jinja_env.globals["ad_slot"] = ads.ad_slot
app.jinja_env.globals["max_tip_images"] = uploads.MAX_TIP_IMAGES
app.jinja_env.globals["editorial_timezone_name"] = editorial_tz.EDITORIAL_TIMEZONE_NAME
app.jinja_env.globals["to_editorial_input"] = editorial_tz.utc_iso_to_local_input
app.jinja_env.globals["to_editorial_display"] = editorial_tz.utc_iso_to_local_display

KOSE_YAZISI_LABEL = "Köşe Yazısı"


def effective_kicker(article):
    """The single place that decides what eyebrow text an article card shows.
    A manually-set kicker always wins. Otherwise, Opinion-section pieces are
    labelled Köşe Yazısı when written by a recognized columnist (so that
    distinction survives everywhere a kicker is rendered, not just on the
    article page), and Opinion for everyone else."""
    if article.get("kicker"):
        return article["kicker"]
    if article.get("section") == "gorus":
        authors = store.resolve_article_authors(article)
        if authors and authors[0].get("is_opinion_columnist"):
            return KOSE_YAZISI_LABEL
    return section_label(article.get("section"))


app.jinja_env.globals["effective_kicker"] = effective_kicker


def _resolve_logged_in_user():
    """Single source of truth for 'who is logged in' -- always re-checked
    against the users store (never trusts stale session data alone), so a
    disabled account is kicked out on its very next request."""
    uid = session.get("user_id")
    if not uid:
        return None
    user = store.get_user(uid)
    if not user or user.get("status") != "active":
        return None
    return user


@app.context_processor
def inject_globals():
    user = _resolve_logged_in_user()
    author = store.get_author_by_user_id(user["id"]) if user else None
    return {
        "site": store.load_site(),
        "sections": SECTIONS,
        "section_order": SECTION_ORDER,
        "section_label": section_label,
        "is_admin": bool(user),
        "is_master_admin": bool(user and user["account_role"] == "master_admin"),
        "perms": user_permissions(user),
        "current_user": user,
        "current_author": author,
        "author_preview_json": author_preview_json(),
        "publication_context": getattr(g, "publication_context", "main"),
    }


def author_preview_json():
    """Embedded once per page (see base.html) so the hover-card component
    never has to make a network request -- it's a small, in-memory lookup.
    The author roster is small enough that shipping everyone's preview data
    on every page is cheap; if the newsroom grows very large this should
    switch to a per-page-collected or lazily-fetched-and-cached subset."""
    import json as _json
    previews = []
    for a in store.active_authors():
        p = store.author_preview(a)
        if p["profile_image"]:
            p["profile_image"] = url_for("static", filename="img/authors/" + p["profile_image"])
        previews.append(p)
    return _json.dumps(previews, ensure_ascii=False)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _resolve_logged_in_user()
        if not user:
            session.clear()
            return redirect(url_for("admin.login", next=request.path))
        if user.get("must_change_password") and request.endpoint != "admin.force_change_password":
            return redirect(url_for("admin.force_change_password"))
        return view(*args, **kwargs)
    return wrapped


def master_admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = _resolve_logged_in_user()
        if not user:
            session.clear()
            return redirect(url_for("admin.login", next=request.path))
        if user.get("must_change_password") and request.endpoint != "admin.force_change_password":
            return redirect(url_for("admin.force_change_password"))
        if user["account_role"] != "master_admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


# The fixed menu of permissions a custom role can be granted. Deliberately
# does NOT include author/account management (creating or editing authors,
# resetting passwords, changing roles) -- that stays hardcoded to
# master_admin_required everywhere, never configurable, so no role (however
# it's later edited) can ever grant itself or anyone else that power.
PERMISSION_CHOICES = [
    ("games", "Oyunlar"),
    ("messages", "İletişim Mesajları"),
    ("site_settings", "Site Ayarları"),
    ("audit_log", "Denetim Kaydı"),
]


def user_permissions(user):
    """The set of permission keys this user's role grants. Master Admin is
    an unconditional bypass (not stored as a real permission list) so it
    can never be accidentally narrowed by editing role data."""
    if not user:
        return set()
    if user["account_role"] == "master_admin":
        return {key for key, _ in PERMISSION_CHOICES}
    role = store.get_role(user["account_role"])
    return set(role["permissions"]) if role else set()


def permission_required(perm):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = _resolve_logged_in_user()
            if not user:
                session.clear()
                return redirect(url_for("admin.login", next=request.path))
            if user.get("must_change_password") and request.endpoint != "admin.force_change_password":
                return redirect(url_for("admin.force_change_password"))
            if perm not in user_permissions(user):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


DIFFICULTY_LABELS = {"easy": "Kolay", "medium": "Orta", "hard": "Zor", "expert": "Uzman"}
GAME_STATUS_LABELS = {"draft": "Taslak", "published": "Yayında", "archived": "Arşiv"}


def crossword_public_payload(cw):
    """Strip solutions/answers out before this ever reaches the browser."""
    grid = cw["grid"]
    numbers, _slots = ge.compute_slots(grid)
    public_grid = [
        [
            {"block": cell["block"], "number": numbers.get((r, c))}
            for c, cell in enumerate(row)
        ]
        for r, row in enumerate(grid)
    ]
    clues = [
        {"number": cl["number"], "direction": cl["direction"], "clue": cl["clue"],
         "row": cl["row"], "col": cl["col"], "length": cl["length"]}
        for cl in cw.get("clues", [])
    ]
    return {
        "slug": cw["slug"],
        "title": cw["title"],
        "width": cw["width"],
        "height": cw["height"],
        "grid": public_grid,
        "clues": clues,
        "settings": cw.get("settings", {}),
    }


def get_crossword_playable(slug):
    """Published puzzles are open to everyone; unpublished ones are only
    reachable this way for a logged-in admin previewing before publish."""
    cw = store.get_crossword_by_slug(slug, published_only=True)
    if cw:
        return cw
    if session.get("admin_logged_in"):
        return store.get_crossword_by_slug(slug, published_only=False)
    return None


def get_sudoku_playable(slug):
    sd = store.get_sudoku_by_slug(slug, published_only=True)
    if sd:
        return sd
    if session.get("admin_logged_in"):
        return store.get_sudoku_by_slug(slug, published_only=False)
    return None


def sudoku_public_payload(sd):
    return {
        "slug": sd["slug"],
        "title": sd["title"],
        "starting_grid": sd["starting_grid"],
        "settings": sd.get("settings", {}),
    }


# ---------------------------------------------------------------- public --

@app.route("/")
def home():
    # Only ordinary Herald news (MAIN_SECTIONS) is eligible for the main
    # feed -- Arı Magazin and Oyun Köşesi articles have their own dedicated
    # pages and must never surface here as if they were ordinary news.
    articles = [a for a in store.all_articles_sorted(published_only=True) if a.get("section") in MAIN_SECTIONS]
    lead = next((a for a in articles if a.get("featured") == "lead"), articles[0] if articles else None)
    secondary = [a for a in articles if a is not lead and a.get("featured") == "secondary"][:6]
    rest = [a for a in articles if a is not lead and a not in secondary]
    opinion = store.articles_by_section("gorus", limit=3, published_only=True)
    published_crosswords = store.published_crosswords()
    published_sudokus = store.published_sudokus()
    return render_template(
        "index.html",
        lead=lead,
        secondary=secondary,
        rest=rest[:8],
        opinion=opinion,
        current_crossword=published_crosswords[0] if published_crosswords else None,
        current_sudoku=published_sudokus[0] if published_sudokus else None,
        difficulty_labels=DIFFICULTY_LABELS,
    )


@app.route("/bolum/<section>")
def section_page(section):
    # Magazin/Oyun have their own dedicated, differently-branded pages --
    # redirect rather than also serving them through the generic section
    # listing (which would show them with plain Herald branding and be a
    # second, inconsistent URL for the same content).
    if section in SPECIAL_SECTION_ROUTES:
        return redirect(url_for(SPECIAL_SECTION_ROUTES[section]), code=301)
    if section not in MAIN_SECTIONS:
        abort(404)
    articles = store.articles_by_section(section, published_only=True)
    return render_template("section.html", section=section, articles=articles)


def _render_article(article, is_preview=False):
    """Shared by the real article page and the token-based preview route --
    `related` always goes through published_only=True regardless of which
    caller this is, so a preview of a draft never leaks OTHER unpublished
    articles into its "related" rail."""
    related = store.articles_by_section(article["section"], exclude_slug=article["slug"], limit=4, published_only=True)
    article_authors = store.resolve_article_authors(article)
    columnist = article_authors[0] if (
        article["section"] == "gorus" and article_authors
        and article_authors[0].get("id") and article_authors[0].get("is_opinion_columnist")
    ) else None
    if article["section"] == "magazin":
        g.publication_context = "ari"
    return render_template("article.html", article=article, related=related, article_authors=article_authors,
                            columnist=columnist, is_preview=is_preview)


@app.route("/makale/<slug>")
def article_page(slug):
    article = store.get_article(slug, published_only=True)
    if not article:
        abort(404)
    return _render_article(article)


@app.route("/preview/<token>")
def article_preview(token):
    """Lets someone without a Herald account view one specific unpublished
    (draft or scheduled) article, via a high-entropy token that is never
    guessable and never grants any admin capability -- store.py only ever
    matches it against a sha256 hash, never a stored plaintext. A token
    for a published article still works (harmless -- it's already public),
    but there's normally no reason to generate one for that case."""
    article = store.get_article_by_preview_token(token)
    if not article:
        abort(404)
    return _render_article(article, is_preview=True)


@app.route("/oyun-kosesi")
def oyun_kosesi():
    articles = store.articles_by_section("oyun", published_only=True)
    crosswords = store.published_crosswords()[:4]
    sudokus = store.published_sudokus()[:4]
    return render_template(
        "oyun_kosesi.html", articles=articles, crosswords=crosswords, sudokus=sudokus,
        difficulty_labels=DIFFICULTY_LABELS,
    )


@app.route("/oyun-kosesi/arsiv")
def games_archive():
    game_type = request.args.get("tur", "")
    difficulty = request.args.get("zorluk", "")
    year = request.args.get("yil", "")

    crosswords = store.published_crosswords() if game_type in ("", "bulmaca") else []
    sudokus = store.published_sudokus() if game_type in ("", "sudoku") else []

    def keep(g):
        if difficulty and g.get("difficulty") != difficulty:
            return False
        if year and not (g.get("publication_date") or "").startswith(year):
            return False
        return True

    crosswords = [c for c in crosswords if keep(c)]
    sudokus = [s for s in sudokus if keep(s)]

    years = sorted({
        (g.get("publication_date") or "")[:4]
        for g in store.published_crosswords() + store.published_sudokus()
        if g.get("publication_date")
    }, reverse=True)

    return render_template(
        "games/archive.html", crosswords=crosswords, sudokus=sudokus,
        difficulty_labels=DIFFICULTY_LABELS, years=years,
        filter_type=game_type, filter_difficulty=difficulty, filter_year=year,
    )


@app.route("/oyun-kosesi/bulmaca/<slug>")
def crossword_play(slug):
    cw = get_crossword_playable(slug)
    if not cw:
        abort(404)
    payload = crossword_public_payload(cw)
    return render_template("games/crossword_play.html", crossword=cw, payload=payload,
                            difficulty_labels=DIFFICULTY_LABELS)


@app.route("/oyun-kosesi/bulmaca/<slug>/kontrol", methods=["POST"])
def crossword_check(slug):
    cw = get_crossword_playable(slug)
    if not cw:
        abort(404)
    data = request.get_json(silent=True) or {}
    mode = data.get("mode")
    grid = cw["grid"]
    height, width = len(grid), len(grid[0]) if grid else 0

    def solution_at(r, c):
        if 0 <= r < height and 0 <= c < width and not grid[r][c]["block"]:
            return grid[r][c]["solution"]
        return None

    if mode == "letter":
        r, c, val = data.get("row"), data.get("col"), (data.get("value") or "").upper()
        sol = solution_at(r, c)
        if sol is None:
            return jsonify({"error": "invalid cell"}), 400
        return jsonify({"correct": val == sol})

    if mode == "word":
        cells = data.get("cells") or []
        values = data.get("values") or []
        results = []
        for i, (r, c) in enumerate(cells):
            sol = solution_at(r, c)
            val = (values[i] if i < len(values) else "").upper()
            results.append(bool(sol) and val == sol)
        return jsonify({"results": results, "correct": all(results) and len(results) > 0})

    if mode == "puzzle":
        entries = data.get("cells") or {}
        results = {}
        all_correct = True
        all_filled = True
        for r in range(height):
            for c in range(width):
                sol = solution_at(r, c)
                if sol is None:
                    continue
                key = f"{r},{c}"
                val = (entries.get(key) or "").upper()
                if not val:
                    all_filled = False
                    all_correct = False
                    results[key] = None
                else:
                    ok = val == sol
                    results[key] = ok
                    if not ok:
                        all_correct = False
        return jsonify({"results": results, "completed": all_filled and all_correct})

    if mode == "reveal":
        if not cw.get("settings", {}).get("reveal_answer", True):
            return jsonify({"error": "reveal disabled"}), 403
        number, direction = data.get("number"), data.get("direction")
        clue = next((cl for cl in cw.get("clues", []) if cl["number"] == number and cl["direction"] == direction), None)
        if not clue:
            return jsonify({"error": "invalid slot"}), 400
        return jsonify({"answer": clue["answer"]})

    return jsonify({"error": "invalid mode"}), 400


@app.route("/oyun-kosesi/sudoku/<slug>")
def sudoku_play(slug):
    sd = get_sudoku_playable(slug)
    if not sd:
        abort(404)
    payload = sudoku_public_payload(sd)
    return render_template("games/sudoku_play.html", sudoku=sd, payload=payload,
                            difficulty_labels=DIFFICULTY_LABELS)


@app.route("/oyun-kosesi/sudoku/<slug>/kontrol", methods=["POST"])
def sudoku_check(slug):
    sd = get_sudoku_playable(slug)
    if not sd:
        abort(404)
    data = request.get_json(silent=True) or {}
    grid = data.get("grid")
    if not isinstance(grid, list) or len(grid) != 9:
        return jsonify({"error": "invalid grid"}), 400
    solution = sd["solution_grid"]
    results = []
    full = True
    correct = True
    for r in range(9):
        row_res = []
        for c in range(9):
            v = grid[r][c] if r < len(grid) and c < len(grid[r]) else 0
            if not v:
                full = False
                row_res.append(None)
                correct = False
                continue
            ok = v == solution[r][c]
            row_res.append(ok)
            if not ok:
                correct = False
        results.append(row_res)
    return jsonify({"results": results, "completed": full and correct})


@app.route("/oyun-kosesi/sudoku/<slug>/ipucu", methods=["POST"])
def sudoku_hint(slug):
    sd = get_sudoku_playable(slug)
    if not sd:
        abort(404)
    if not sd.get("settings", {}).get("hints_enabled", True):
        return jsonify({"error": "hints disabled"}), 403
    data = request.get_json(silent=True) or {}
    grid = data.get("grid") or [[0] * 9 for _ in range(9)]
    solution = sd["solution_grid"]
    starting = sd["starting_grid"]
    candidates = []
    for r in range(9):
        for c in range(9):
            if starting[r][c] != 0:
                continue
            v = grid[r][c] if r < len(grid) and c < len(grid[r]) else 0
            if v != solution[r][c]:
                candidates.append((r, c))
    if not candidates:
        return jsonify({"row": None})
    import random as _random
    r, c = _random.choice(candidates)
    return jsonify({"row": r, "col": c, "value": solution[r][c]})


@app.route("/magazin")
def magazin():
    g.publication_context = "ari"
    # This was the actual bug: the route never queried the store, so it
    # always rendered a static "no content yet" placeholder regardless of
    # how many articles had section="magazin" -- they were never broken,
    # just never looked up here.
    articles = store.articles_by_section("magazin", published_only=True)
    lead = next((a for a in articles if a.get("featured") == "lead"), articles[0] if articles else None)
    rest = [a for a in articles if a is not lead]
    return render_template("magazin.html", lead=lead, rest=rest)


@app.route("/hakkimizda")
def hakkimizda():
    return render_template("hakkimizda.html")


@app.route("/yazarlar")
def yazarlar():
    """The public writer directory -- deliberately independent of Gazete
    Yönetimi (see kurumsal() below): every active author appears here
    regardless of masthead membership, and this list is generated straight
    from the live author store, so a profile edit/deactivation is reflected
    immediately with no separate data to keep in sync."""
    authors = sorted(store.active_authors(), key=lambda a: a["display_name"].lower())
    return render_template("yazarlar.html", authors=authors)


@app.route("/gazete-yonetimi")
def kurumsal():
    authors_by_id = {a["id"]: a for a in store.load_authors()}
    entries = store.active_management_entries()
    return render_template("kurumsal.html", entries=entries, authors_by_id=authors_by_id)


@app.route("/gazete")
def gazete():
    issues = sorted(store.load_issues(), key=lambda i: i["date"], reverse=True)
    return render_template("gazete.html", issues=issues)


@app.route("/gazete/<issue_id>")
def gazete_oku(issue_id):
    issue = store.get_issue(issue_id)
    if not issue:
        abort(404)
    others = [i for i in store.load_issues() if i["id"] != issue_id]
    return render_template("gazete_oku.html", issue=issue, others=others)


@app.route("/ara")
def search():
    q = request.args.get("q", "").strip().lower()
    results = []
    if q:
        for a in store.all_articles_sorted(published_only=True):
            haystack = " ".join([
                a.get("title", ""), a.get("dek", ""), " ".join(a.get("tags", []))
            ]).lower()
            if q in haystack:
                results.append(a)
    return render_template("search.html", q=q, results=results)


@app.route("/iletisim", methods=["GET", "POST"])
def iletisim():
    contact_settings = store.load_site().get("contact") or {}
    subjects = contact_settings.get("subjects") or CONTACT_SUBJECTS
    authors_by_id = {a["id"]: a for a in store.active_authors()}
    management_entries = store.active_management_entries()

    if request.method == "POST":
        # Name and email are deliberately optional -- a reader submitting a
        # sensitive tip may reasonably want to stay anonymous. Consent to
        # follow-up is tracked separately and is never implied by simply
        # supplying contact info.
        name = request.form.get("name", "").strip()[:120] or None
        email = request.form.get("email", "").strip()[:200] or None
        category = request.form.get("category", "").strip()
        message = request.form.get("message", "").strip()[:4000]
        follow_up_consent = bool(request.form.get("follow_up_consent")) and bool(email)

        errors = []
        if not ratelimit.allow(f"contact:{request.remote_addr}", max_hits=5, window_seconds=600):
            errors.append("Çok fazla mesaj gönderildi. Lütfen bir süre sonra tekrar deneyin.")
        if email and "@" not in email:
            errors.append("Geçerli bir e-posta adresi girin ya da e-posta alanını boş bırakın.")
        if category not in subjects:
            errors.append("Lütfen bir kategori seçin.")
        if not message:
            errors.append("Mesaj alanı zorunludur.")
        turnstile.check(errors, request.form, remote_ip=request.remote_addr)

        images_meta = []
        files = [f for f in request.files.getlist("images") if f and f.filename][:uploads.MAX_TIP_IMAGES]
        if len(request.files.getlist("images")) > uploads.MAX_TIP_IMAGES:
            errors.append(f"En fazla {uploads.MAX_TIP_IMAGES} görsel ekleyebilirsiniz.")
        if not errors:
            for f in files:
                object_key, content_type, original_name, upload_error = uploads.save_tip_image(f, TIP_IMG_DIR)
                if upload_error:
                    errors.append(upload_error)
                    continue
                if object_key:
                    images_meta.append({
                        "key": object_key, "content_type": content_type, "original_filename": original_name,
                    })

        if errors:
            for uploaded in images_meta:
                uploads.delete_tip_image(uploaded["key"], TIP_IMG_DIR)
            for e in errors:
                flash(e, "error")
            return render_template("iletisim.html", subjects=subjects, contact=contact_settings,
                                    authors_by_id=authors_by_id, management_entries=management_entries,
                                    form=request.form)

        messages = store.load_messages()
        messages.append({
            "id": uuid.uuid4().hex[:10],
            "name": name,
            "email": email,
            "category": category,
            "subject": category,  # kept for compatibility with any older reader of this field
            "message": message,
            "follow_up_consent": follow_up_consent,
            "images": images_meta,
            "status": "new",
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        store.save_messages(messages)
        flash("Mesajınız için teşekkürler! Okur İlişkileri departmanımız en kısa sürede inceleyecektir.", "success")
        return redirect(url_for("iletisim"))

    return render_template("iletisim.html", subjects=subjects, contact=contact_settings,
                            authors_by_id=authors_by_id, management_entries=management_entries, form={})


@app.route("/profil/<slug>")
def author_profile(slug):
    author, is_redirect = store.get_author_by_slug(slug)
    if not author:
        abort(404)
    if is_redirect:
        return redirect(url_for("author_profile", slug=author["slug"]), code=301)

    articles = store.articles_by_author(author["id"], published_only=True)
    gorus_articles = [a for a in articles if a["section"] == "gorus"]
    ari = [a for a in articles if a["section"] == "magazin"]
    news = [a for a in articles if a["section"] not in ("gorus", "magazin")]

    # A recognized columnist's opinion pieces are their Köşe Yazıları and are
    # prioritized first on their own profile; everyone else's opinion pieces
    # are plain Opinion content. The two are mutually exclusive per author.
    if author.get("is_opinion_columnist") and gorus_articles:
        kose_yazilari, opinion = gorus_articles, []
    else:
        kose_yazilari, opinion = [], gorus_articles

    minecraft = None
    if author.get("minecraft_username"):
        minecraft = MinecraftProfileService.get_profile(author["minecraft_username"])

    return render_template(
        "profil.html", author=author, news=news, opinion=opinion,
        kose_yazilari=kose_yazilari, ari=ari,
        minecraft=minecraft, total_count=len(articles),
    )


@app.errorhandler(404)
def not_found(e):
    return render_template("404.html"), 404


@app.errorhandler(403)
def forbidden(e):
    return render_template("403.html"), 403


# ------------------------------------------------------- visitor gate (Turnstile) --
# A once-per-visit(-ish) Cloudflare Turnstile check in front of the public
# site only -- never the admin panel, which Cloudflare Access protects
# separately (see cf_access.py / README). Entirely inactive unless
# TURNSTILE_SITE_KEY + TURNSTILE_SECRET_KEY are set, so it never affects
# local development or a deployment that hasn't configured Turnstile yet.

GATE_COOKIE_NAME = "eh_verified"
GATE_COOKIE_SALT = "turnstile-gate"
GATE_COOKIE_MAX_AGE = int(os.environ.get("TURNSTILE_COOKIE_DAYS", "14")) * 86400


def _gate_serializer():
    return URLSafeTimedSerializer(app.secret_key, salt=GATE_COOKIE_SALT)


def _gate_is_verified():
    cookie = request.cookies.get(GATE_COOKIE_NAME)
    if not cookie:
        return False
    try:
        _gate_serializer().loads(cookie, max_age=GATE_COOKIE_MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def _gate_set_cookie(resp):
    token = _gate_serializer().dumps({"v": 1})
    resp.set_cookie(
        GATE_COOKIE_NAME, token,
        max_age=GATE_COOKIE_MAX_AGE,
        httponly=True,
        secure=bool(SERVER_NAME),
        samesite="Lax",
    )
    return resp


def _gate_safe_next(raw):
    """Only ever redirect back to a same-site relative path -- rejects
    absolute/protocol-relative URLs to avoid an open-redirect via `next`."""
    if raw and raw.startswith("/") and not raw.startswith("//") and "\\" not in raw:
        return raw
    return "/"


def _gate_current_path():
    path = request.path
    if request.query_string:
        path += "?" + request.query_string.decode("utf-8", "ignore")
    return path


@app.before_request
def enforce_visitor_gate():
    if not turnstile.ENABLED:
        return None
    if request.blueprint == "admin":
        return None
    if request.endpoint in ("static", "gate_verify") or request.endpoint is None:
        return None
    if _gate_is_verified():
        return None
    return render_template(
        "gate.html", site_key=turnstile.SITE_KEY, next=_gate_current_path(), error=False,
    ), 200


@app.route("/dogrulama/kontrol", methods=["POST"])
def gate_verify():
    next_url = _gate_safe_next(request.form.get("next", ""))
    if not ratelimit.allow(f"gate:{request.remote_addr}", max_hits=20, window_seconds=300):
        return render_template("gate.html", site_key=turnstile.SITE_KEY, next=next_url, error=True), 429
    token = request.form.get(turnstile.FIELD_NAME, "")
    if not turnstile.verify(token, remote_ip=request.remote_addr):
        return render_template("gate.html", site_key=turnstile.SITE_KEY, next=next_url, error=True), 401
    resp = redirect(next_url)
    return _gate_set_cookie(resp)


# ----------------------------------------------------------------- admin --
# Isolated in its own Blueprint so it can be mounted on a separate subdomain
# (admin.<SERVER_NAME>) in production instead of living under the public site.

admin_bp = Blueprint(
    "admin",
    __name__,
    subdomain="admin" if SERVER_NAME else None,
    url_prefix=None if SERVER_NAME else "/admin",
    template_folder="templates/admin",
)


@admin_bp.before_request
def verify_cloudflare_access():
    """Defense-in-depth only: Cloudflare Access (configured in the Cloudflare
    dashboard, not here) is what actually blocks unauthorized requests to
    admin.eurovillageherald.com before they ever reach this app. When
    CF_ACCESS_TEAM_DOMAIN/CF_ACCESS_AUD are set we additionally verify the
    signed identity Access attaches to the request and reject anything
    without one -- but this never grants any authorization by itself; the
    Herald's own login_required/master_admin_required/permission_required
    checks below still decide what a signed-in account may do."""
    if cf_access.ENABLED and not cf_access.verified_identity(request):
        abort(403)


@admin_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not ratelimit.allow(f"login:{request.remote_addr}", max_hits=10, window_seconds=300):
            flash("Çok fazla giriş denemesi yapıldı. Lütfen birkaç dakika sonra tekrar deneyin.", "error")
            return render_template("admin/login.html"), 429
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        user = store.get_user_by_email(email)
        # Deliberately generic error for wrong password / unknown email / disabled
        # account alike, so a login attempt can't be used to probe account status.
        if user and user.get("status") == "active" and check_password_hash(user.get("password_hash", ""), password):
            session.clear()
            session["user_id"] = user["id"]
            cf_identity = cf_access.verified_identity(request)
            audit_meta = {"cf_access_email": cf_identity} if cf_identity else None
            store.append_audit(user["email"], "login", user["email"], audit_meta)
            if user.get("must_change_password"):
                return redirect(url_for("admin.force_change_password"))
            next_url = request.args.get("next") or url_for("admin.dashboard")
            return redirect(next_url)
        flash("E-posta veya şifre hatalı.", "error")
    return render_template("admin/login.html")


@admin_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


@admin_bp.route("/")
@login_required
def dashboard():
    user = _resolve_logged_in_user()
    if user["account_role"] != "master_admin":
        author = store.get_author_by_user_id(user["id"])
        my_articles = store.articles_by_author(author["id"]) if author else []
        return render_template("admin/author_dashboard.html", author=author, articles=my_articles, active="dashboard")

    articles = store.all_articles_sorted()
    issues = sorted(store.load_issues(), key=lambda i: i["date"], reverse=True)
    message_count = len(store.load_messages())
    return render_template("admin/dashboard.html", articles=articles, issues=issues,
                            message_count=message_count, active="dashboard")


MESSAGES_PER_PAGE = 10


@admin_bp.route("/mesajlar")
@permission_required("messages")
def messages_list():
    all_messages = store.all_messages_sorted()
    total = len(all_messages)
    page_count = max(1, -(-total // MESSAGES_PER_PAGE))  # ceil division
    page = request.args.get("sayfa", 1, type=int)
    page = min(max(page, 1), page_count)
    start = (page - 1) * MESSAGES_PER_PAGE
    messages = all_messages[start:start + MESSAGES_PER_PAGE]
    return render_template("admin/messages_list.html", messages=messages, total=total,
                            page=page, page_count=page_count, status_labels=MESSAGE_STATUS_LABELS,
                            active="messages")


@admin_bp.route("/mesaj/<mid>/sil", methods=["POST"])
@permission_required("messages")
def message_delete(mid):
    messages = store.load_messages()
    message = next((m for m in messages if m["id"] == mid), None)
    if message:
        for img in message.get("images") or []:
            uploads.delete_tip_image(img["key"], TIP_IMG_DIR)
    messages = [m for m in messages if m["id"] != mid]
    store.save_messages(messages)
    flash("Mesaj silindi.", "success")
    return redirect(url_for("admin.messages_list", sayfa=request.form.get("sayfa", 1)))


@admin_bp.route("/mesaj/<mid>/durum", methods=["POST"])
@permission_required("messages")
def message_set_status(mid):
    """The reader-message status workflow (Yeni/İnceleniyor/Yanıtlandı/
    Arşivlendi) -- reuses the message's own `status` field rather than a
    second parallel state system; never deletes the message."""
    actor = _resolve_logged_in_user()
    messages = store.load_messages()
    idx = next((i for i, m in enumerate(messages) if m["id"] == mid), None)
    if idx is None:
        abort(404)
    new_status = request.form.get("status", "")
    if new_status not in MESSAGE_STATUS_LABELS:
        abort(400)
    messages[idx]["status"] = new_status
    messages[idx]["status_updated_at"] = _now_iso()
    messages[idx]["status_updated_by"] = actor["email"]
    store.save_messages(messages)
    store.append_audit(actor["email"], "reader_message_status_changed", mid, {"status": new_status})
    flash("Mesaj durumu güncellendi.", "success")
    return redirect(url_for("admin.messages_list", sayfa=request.form.get("sayfa", 1)))


@admin_bp.route("/mesaj/<mid>/gorsel/<int:idx>")
@permission_required("messages")
def message_image(mid, idx):
    """The ONLY way a reader-tip image is ever shown to anyone: fetched
    server-side (from R2 or the local-dev fallback) and streamed through
    this authenticated, permission-checked route. No public URL for these
    objects is ever generated or handed to a browser -- see uploads.py /
    storage.py's private-object functions for the read path itself, and
    their docstrings for what this can and cannot guarantee about the
    underlying storage."""
    message = store.get_message(mid)
    images = (message or {}).get("images") or []
    if not message or idx < 0 or idx >= len(images):
        abort(404)
    object_key = images[idx]["key"]
    body, content_type = uploads.read_tip_image(object_key, TIP_IMG_DIR)
    if not body:
        abort(404)
    data = body.read() if hasattr(body, "read") else body
    return Response(data, mimetype=content_type or "application/octet-stream",
                     headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


def text_to_body(body_raw):
    body = []
    for block in body_raw.replace("\r\n", "\n").split("\n\n"):
        block = block.strip()
        if not block:
            continue
        if block.startswith("## "):
            body.append({"type": "h3", "text": block[3:].strip()})
        elif block.startswith("> "):
            text = block[2:].strip()
            attribution = None
            if " — " in text:
                text, attribution = text.rsplit(" — ", 1)
            body.append({"type": "pullquote", "text": text.strip(), "attribution": attribution})
        elif block.startswith("Q: ") and "\nA: " in block:
            q, a = block.split("\nA: ", 1)
            body.append({"type": "qa", "q": q[3:].strip(), "a": a.strip()})
        elif block.startswith("- "):
            items = [line[2:].strip() for line in block.split("\n") if line.strip().startswith("- ")]
            body.append({"type": "list", "items": items})
        else:
            body.append({"type": "p", "text": block})
    return body


def body_to_text(body):
    parts = []
    for block in body or []:
        t = block.get("type")
        if t == "h3":
            parts.append("## " + block.get("text", ""))
        elif t == "pullquote":
            line = "> " + block.get("text", "")
            if block.get("attribution"):
                line += " — " + block["attribution"]
            parts.append(line)
        elif t == "qa":
            parts.append("Q: " + block.get("q", "") + "\nA: " + block.get("a", ""))
        elif t == "list":
            parts.append("\n".join("- " + item for item in block.get("items", [])))
        else:
            parts.append(block.get("text", ""))
    return "\n\n".join(parts)


def _article_form_to_dict(form, existing=None):
    body = text_to_body(form.get("body", ""))
    tags = [t.strip() for t in form.get("tags", "").split(",") if t.strip()]

    data = {
        "section": form.get("section"),
        "kicker": form.get("kicker", "").strip(),
        "title": form.get("title", "").strip(),
        "dek": form.get("dek", "").strip(),
        "byline_title": form.get("byline_title", "Muhabir").strip() or "Muhabir",
        "date": form.get("date") or (existing["date"] if existing else ""),
        "featured": form.get("featured") or None,
        "breaking": bool(form.get("breaking")),
        "image_caption": form.get("image_caption", "").strip() or None,
        "tags": tags,
        "body": body,
    }
    _apply_publish_state(data, form, existing)
    return data


PUBLISH_STATE_LABELS = {"draft": "Taslak", "scheduled": "Zamanlanmış", "published": "Yayında"}


def _apply_publish_state(data, form, existing=None):
    """Draft / Scheduled / Published from the article form's single
    publish_state select. A "scheduled" choice with no usable publish_at
    falls back to Draft rather than silently going live immediately --
    an editor who picks "Scheduled" and forgets the date/time almost
    certainly did not mean "publish this right now"."""
    choice = form.get("publish_state", "published")
    if choice == "draft":
        data["status"] = "draft"
        data["publish_at"] = None
    elif choice == "scheduled":
        # The form field is a plain datetime-local value with no timezone
        # of its own -- interpreted as EDITORIAL_TIMEZONE wall-clock time
        # (shown next to the field), converted to UTC for storage here.
        publish_at = editorial_tz.local_input_to_utc_iso(form.get("publish_at", ""))
        if publish_at:
            data["status"] = "scheduled"
            data["publish_at"] = publish_at
        else:
            data["status"] = "draft"
            data["publish_at"] = None
    else:
        data["status"] = "published"
        data["publish_at"] = None
    # A publish schedule change/cancellation is intentionally invalidating
    # a stale preview link is NOT required by itself -- the token still
    # only ever grants a look at this one article regardless of its
    # status -- so existing preview_token fields are preserved here and
    # only ever touched by the dedicated preview-link routes below.
    if existing:
        data["preview_token_hash"] = existing.get("preview_token_hash")
        data["preview_token_expires_at"] = existing.get("preview_token_expires_at")
        data["preview_token_created_at"] = existing.get("preview_token_created_at")
    else:
        data["preview_token_hash"] = None
        data["preview_token_expires_at"] = None
        data["preview_token_created_at"] = None


def _can_edit_article(user, article):
    if user["account_role"] == "master_admin":
        return True
    author = store.get_author_by_user_id(user["id"])
    return bool(author and author["id"] in (article.get("author_ids") or []))


@admin_bp.route("/makale/yeni", methods=["GET", "POST"])
@login_required
def article_new():
    user = _resolve_logged_in_user()
    current_author = store.get_author_by_user_id(user["id"])
    is_master = user["account_role"] == "master_admin"

    if request.method == "POST":
        articles = store.load_articles()
        existing_slugs = {a["slug"] for a in articles}
        data = _article_form_to_dict(request.form)
        if not data["date"]:
            from datetime import date as _date
            data["date"] = _date.today().isoformat()
        slug = store.unique_slug(data["title"] or "makale", existing_slugs)
        data["slug"] = slug
        data["id"] = uuid.uuid4().hex[:10]
        data["image"] = None

        if is_master:
            author_ids = [aid for aid in request.form.getlist("author_ids") if store.get_author(aid)]
        else:
            if not current_author:
                flash("Hesabınıza bağlı bir yazar profili bulunamadı.", "error")
                return redirect(url_for("admin.dashboard"))
            author_ids = [current_author["id"]]
        data["author_ids"] = author_ids
        data["author"] = ", ".join(store.get_author(aid)["display_name"] for aid in author_ids) if author_ids else "Eurovillage Herald"

        file = request.files.get("image_file")
        if file and file.filename:
            image_value, upload_error = uploads.save_article_image(file, slug, data.get("section"), ARTICLE_IMG_DIR)
            if upload_error:
                flash(upload_error, "error")
            else:
                data["image"] = image_value

        articles.append(data)
        store.save_articles(articles)
        if data["status"] == "scheduled":
            store.append_audit(user["email"], "article_scheduled", data["title"], {"publish_at": data["publish_at"]})
        flash(_publish_flash_message(data), "success")
        return redirect(url_for("admin.dashboard"))

    authors = store.active_authors() if is_master else ([current_author] if current_author else [])
    return render_template("admin/edit_article.html", article=None, sections=SECTIONS, body_text="",
                            authors=authors, is_master=is_master, publish_state_labels=PUBLISH_STATE_LABELS)


@admin_bp.route("/makale/<slug>/duzenle", methods=["GET", "POST"])
@login_required
def article_edit(slug):
    user = _resolve_logged_in_user()
    articles = store.load_articles()
    idx = next((i for i, a in enumerate(articles) if a["slug"] == slug), None)
    if idx is None:
        abort(404)
    article = articles[idx]
    if not _can_edit_article(user, article):
        abort(403)
    is_master = user["account_role"] == "master_admin"

    if request.method == "POST":
        existing_slugs = {a["slug"] for a in articles}
        data = _article_form_to_dict(request.form, existing=article)
        new_title = data["title"] or article["title"]
        new_slug = store.unique_slug(new_title, existing_slugs, current_slug=slug)
        data["slug"] = new_slug
        data["id"] = article.get("id") or uuid.uuid4().hex[:10]
        data["image"] = article.get("image")
        data["gallery"] = article.get("gallery")

        if is_master:
            author_ids = [aid for aid in request.form.getlist("author_ids") if store.get_author(aid)]
            data["author_ids"] = author_ids
        else:
            # Authors can edit their own article's content but not reassign bylines.
            data["author_ids"] = article.get("author_ids") or []
        data["author"] = ", ".join(
            store.get_author(aid)["display_name"] for aid in data["author_ids"] if store.get_author(aid)
        ) or article.get("author", "Eurovillage Herald")

        file = request.files.get("image_file")
        if file and file.filename:
            image_value, upload_error = uploads.save_article_image(file, new_slug, data.get("section"), ARTICLE_IMG_DIR)
            if upload_error:
                flash(upload_error, "error")
            else:
                uploads.delete_stored(article.get("image"))
                data["image"] = image_value
        elif request.form.get("remove_image"):
            uploads.delete_stored(article.get("image"))
            data["image"] = None

        articles[idx] = data
        store.save_articles(articles)
        _audit_schedule_change(user["email"], article, data)
        flash(_publish_flash_message(data, updated=True), "success")
        return redirect(url_for("admin.dashboard"))

    authors = store.active_authors() if is_master else store.resolve_article_authors(article)
    return render_template("admin/edit_article.html", article=article, sections=SECTIONS,
                            body_text=body_to_text(article.get("body")), authors=authors, is_master=is_master,
                            publish_state_labels=PUBLISH_STATE_LABELS)


def _publish_flash_message(data, updated=False):
    verb = "güncellendi" if updated else "kaydedildi"
    if data["status"] == "draft":
        return f"Makale taslak olarak {verb}."
    if data["status"] == "scheduled":
        when = editorial_tz.utc_iso_to_local_display(data.get("publish_at"))
        return f"Makale {when} ({editorial_tz.EDITORIAL_TIMEZONE_NAME}) tarihinde otomatik yayımlanacak şekilde zamanlandı."
    return f"Makale yayımlandı." if not updated else "Makale güncellendi ve yayında."


def _audit_schedule_change(actor_email, before, after):
    """Logs only the scheduling-relevant transitions, onto the existing
    editorial audit log -- not every routine article edit (which has never
    been audited here and isn't part of what was asked)."""
    was_scheduled = before.get("status") == "scheduled"
    now_scheduled = after.get("status") == "scheduled"
    if not was_scheduled and now_scheduled:
        store.append_audit(actor_email, "article_scheduled", after["title"], {"publish_at": after.get("publish_at")})
    elif was_scheduled and now_scheduled and before.get("publish_at") != after.get("publish_at"):
        store.append_audit(actor_email, "article_schedule_modified", after["title"],
                            {"from": before.get("publish_at"), "to": after.get("publish_at")})
    elif was_scheduled and not now_scheduled:
        store.append_audit(actor_email, "article_schedule_cancelled", after["title"], {"new_status": after["status"]})


@admin_bp.route("/makale/<slug>/sil", methods=["POST"])
@login_required
def article_delete(slug):
    user = _resolve_logged_in_user()
    articles = store.load_articles()
    article = next((a for a in articles if a["slug"] == slug), None)
    if not article:
        abort(404)
    if not _can_edit_article(user, article):
        abort(403)
    uploads.delete_stored(article.get("image"))
    articles = [a for a in articles if a["slug"] != slug]
    store.save_articles(articles)
    flash("Makale silindi.", "success")
    return redirect(url_for("admin.dashboard"))


PREVIEW_EXPIRY_CHOICES = {"24h": "24 saat", "7d": "7 gün", "custom": "Özel", "none": "Süresiz"}


def _preview_expires_at(choice, custom_value):
    """Turns a PREVIEW_EXPIRY_CHOICES key into a stored expires_at, or None
    for no expiry. An unusable "custom" value falls back to 24h rather than
    silently granting a link that never expires."""
    now = datetime.now(timezone.utc)
    if choice == "24h":
        return (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    if choice == "7d":
        return (now + timedelta(days=7)).isoformat().replace("+00:00", "Z")
    if choice == "custom":
        normalized = _normalize_dt_input(custom_value)
        return normalized or (now + timedelta(hours=24)).isoformat().replace("+00:00", "Z")
    return None  # "none" -- no expiry


@admin_bp.route("/makale/<slug>/onizleme/olustur", methods=["POST"])
@login_required
def article_preview_generate(slug):
    user = _resolve_logged_in_user()
    articles = store.load_articles()
    idx = next((i for i, a in enumerate(articles) if a["slug"] == slug), None)
    if idx is None:
        abort(404)
    article = articles[idx]
    if not _can_edit_article(user, article):
        abort(403)

    token = secrets.token_urlsafe(32)
    expires_at = _preview_expires_at(request.form.get("expiry", "24h"), request.form.get("custom_expiry", ""))

    articles[idx]["preview_token_hash"] = store.hash_preview_token(token)
    articles[idx]["preview_token_expires_at"] = expires_at
    articles[idx]["preview_token_created_at"] = _now_iso()
    store.save_articles(articles)
    store.append_audit(user["email"], "article_preview_link_generated", article["title"],
                        {"expires_at": expires_at})

    preview_url = url_for("article_preview", token=token, _external=True)
    flash(
        "Önizleme bağlantısı oluşturuldu — bu bağlantı yalnızca şimdi gösterilir, "
        f"kaydedin: {preview_url}",
        "success",
    )
    return redirect(url_for("admin.article_edit", slug=slug))


@admin_bp.route("/makale/<slug>/onizleme/iptal", methods=["POST"])
@login_required
def article_preview_revoke(slug):
    user = _resolve_logged_in_user()
    articles = store.load_articles()
    idx = next((i for i, a in enumerate(articles) if a["slug"] == slug), None)
    if idx is None:
        abort(404)
    article = articles[idx]
    if not _can_edit_article(user, article):
        abort(403)
    articles[idx]["preview_token_hash"] = None
    articles[idx]["preview_token_expires_at"] = None
    articles[idx]["preview_token_created_at"] = None
    store.save_articles(articles)
    store.append_audit(user["email"], "article_preview_link_revoked", article["title"])
    flash("Önizleme bağlantısı iptal edildi.", "success")
    return redirect(url_for("admin.article_edit", slug=slug))


@admin_bp.route("/sayi/yeni", methods=["GET", "POST"])
@master_admin_required
def issue_new():
    if request.method == "POST":
        issues = store.load_issues()
        title = request.form.get("title", "").strip()
        no = request.form.get("no", "").strip()
        date = request.form.get("date", "").strip()
        description = request.form.get("description", "").strip()

        pdf_file = request.files.get("pdf_file")
        pdf_error = uploads.validate_pdf(pdf_file)
        if pdf_error:
            flash(pdf_error, "error")
            return render_template("admin/edit_issue.html", issue=None)

        issue_id = store.slugify(title or f"sayi-{no}")
        existing_ids = {i["id"] for i in issues}
        base_id = issue_id
        n = 2
        while issue_id in existing_ids:
            issue_id = f"{base_id}-{n}"
            n += 1

        pdf_value, pdf_error = uploads.save_issue_pdf(pdf_file, issue_id, date, ISSUE_PDF_DIR)
        if pdf_error:
            flash(pdf_error, "error")
            return render_template("admin/edit_issue.html", issue=None)

        cover_image = None
        cover_file = request.files.get("cover_file")
        if cover_file and cover_file.filename:
            cover_image, cover_error = uploads.save_issue_cover(cover_file, issue_id, date, ARTICLE_IMG_DIR)
            if cover_error:
                flash(cover_error, "error")

        issues.append({
            "id": issue_id,
            "no": int(no) if no.isdigit() else len(issues) + 1,
            "title": title,
            "date": date,
            "description": description,
            "cover_image": cover_image,
            "pdf": pdf_value,
            "pages": None,
        })
        store.save_issues(issues)
        flash("Yeni sayı yüklendi.", "success")
        return redirect(url_for("admin.dashboard"))

    return render_template("admin/edit_issue.html", issue=None)


@admin_bp.route("/sayi/<issue_id>/sil", methods=["POST"])
@master_admin_required
def issue_delete(issue_id):
    issues = store.load_issues()
    issue = next((i for i in issues if i["id"] == issue_id), None)
    if issue:
        uploads.delete_stored(issue.get("pdf"))
        uploads.delete_stored(issue.get("cover_image"))
    issues = [i for i in issues if i["id"] != issue_id]
    store.save_issues(issues)
    flash("Sayı silindi.", "success")
    return redirect(url_for("admin.dashboard"))


def _set_user_password(user_id, new_password, must_change=False):
    users = store.load_users()
    idx = next((i for i, u in enumerate(users) if u["id"] == user_id), None)
    if idx is None:
        return False
    users[idx]["password_hash"] = generate_password_hash(new_password, method="pbkdf2:sha256")
    users[idx]["must_change_password"] = must_change
    users[idx]["updated_at"] = _now_iso()
    store.save_users(users)
    return True


@admin_bp.route("/sifre", methods=["GET", "POST"])
@login_required
def change_password():
    user = _resolve_logged_in_user()
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if not check_password_hash(user.get("password_hash", ""), current):
            flash("Mevcut şifre yanlış.", "error")
        elif len(new) < 8:
            flash("Yeni şifre en az 8 karakter olmalı.", "error")
        elif new != confirm:
            flash("Yeni şifreler eşleşmiyor.", "error")
        else:
            _set_user_password(user["id"], new)
            flash("Şifre güncellendi.", "success")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/change_password.html")


@admin_bp.route("/sifre-belirle", methods=["GET", "POST"])
@login_required
def force_change_password():
    user = _resolve_logged_in_user()
    if not user.get("must_change_password"):
        return redirect(url_for("admin.dashboard"))
    if request.method == "POST":
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        if len(new) < 8:
            flash("Yeni şifre en az 8 karakter olmalı.", "error")
        elif new != confirm:
            flash("Şifreler eşleşmiyor.", "error")
        else:
            _set_user_password(user["id"], new, must_change=False)
            store.append_audit(user["email"], "password_set_self", user["email"])
            flash("Şifreniz belirlendi. Hoş geldiniz!", "success")
            return redirect(url_for("admin.dashboard"))
    return render_template("admin/force_change_password.html")


# --------------------------------------------------------- admin: games --

GRID_SIZE_CHOICES = [9, 11, 13, 15, 17, 19, 21]
DIFFICULTY_CHOICES = ["easy", "medium", "hard", "expert"]


def _now_iso():
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _default_crossword_settings():
    return {
        "allow_check_letter": True,
        "allow_check_word": True,
        "allow_check_puzzle": True,
        "allow_hints": True,
        "reveal_answer": True,
        "timer_enabled": True,
    }


def _default_sudoku_settings():
    return {
        "error_checking": "on_check",
        "hints_enabled": True,
        "max_hints": 3,
        "notes_enabled": True,
        "timer_enabled": True,
        "conflict_highlighting": True,
        "mistake_counter": True,
    }


def _game_meta_from_form(form, kind):
    data = {
        "title": form.get("title", "").strip(),
        "publication_date": form.get("publication_date", "").strip(),
        "issue": form.get("issue", "").strip(),
        "difficulty": form.get("difficulty") if form.get("difficulty") in DIFFICULTY_CHOICES else "medium",
        "description": form.get("description", "").strip(),
    }
    if kind == "crossword":
        data["author"] = form.get("author", "Emir the Composer").strip() or "Emir the Composer"
    return data


@admin_bp.route("/oyunlar")
@permission_required("games")
def games_overview():
    crosswords = store.all_crosswords_sorted()
    sudokus = store.all_sudokus_sorted()
    return render_template(
        "admin/games_overview.html",
        crosswords=crosswords, sudokus=sudokus,
        difficulty_labels=DIFFICULTY_LABELS, status_labels=GAME_STATUS_LABELS, active="games",
    )


# ---- crosswords -----------------------------------------------------------

@admin_bp.route("/oyunlar/bulmaca")
@permission_required("games")
def crossword_list():
    crosswords = store.all_crosswords_sorted()
    return render_template("admin/crossword_list.html", crosswords=crosswords,
                            difficulty_labels=DIFFICULTY_LABELS, status_labels=GAME_STATUS_LABELS)


@admin_bp.route("/oyunlar/bulmaca/yeni", methods=["GET", "POST"])
@permission_required("games")
def crossword_new():
    if request.method == "POST":
        crosswords = store.load_crosswords()
        existing_slugs = {c["slug"] for c in crosswords}
        meta = _game_meta_from_form(request.form, "crossword")
        try:
            width = int(request.form.get("width", 13))
            height = int(request.form.get("height", 13))
        except ValueError:
            width = height = 13
        width = max(3, min(width, 31))
        height = max(3, min(height, 31))

        now = _now_iso()
        doc = {
            "id": uuid.uuid4().hex[:10],
            "title": meta["title"] or "Yeni Çapraz Bulmaca",
            "slug": store.unique_slug(meta["title"] or "capraz-bulmaca", existing_slugs),
            "publication_date": meta["publication_date"],
            "issue": meta["issue"],
            "difficulty": meta["difficulty"],
            "status": "draft",
            "description": meta["description"],
            "author": meta["author"],
            "width": width,
            "height": height,
            "grid": ge.empty_grid(width, height),
            "clues": [],
            "settings": _default_crossword_settings(),
            "created_at": now,
            "updated_at": now,
        }
        crosswords.append(doc)
        store.save_crosswords(crosswords)
        flash("Bulmaca taslağı oluşturuldu — şimdi ızgarayı doldurun.", "success")
        return redirect(url_for("admin.crossword_edit", cid=doc["id"]))

    return render_template("admin/crossword_new.html", grid_choices=GRID_SIZE_CHOICES,
                            difficulty_choices=DIFFICULTY_CHOICES)


@admin_bp.route("/oyunlar/bulmaca/olustur", methods=["GET", "POST"])
@permission_required("games")
def crossword_generate():
    if request.method == "POST":
        raw = request.form.get("entries", "")
        entries = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or "|" not in line:
                continue
            answer, clue = line.split("|", 1)
            entries.append((answer.strip(), clue.strip()))

        if not entries:
            flash("En az bir 'CEVAP | İpucu' satırı girin.", "error")
            return render_template("admin/crossword_generate.html")

        grid, placements, unplaced = ge.generate_crossword(entries)
        numbers, slots = ge.compute_slots(grid)
        clues = ge.merge_clues([], slots)
        clue_by_answer = {p["answer"]: p["clue"] for p in placements}
        for cl in clues:
            if not cl["clue"]:
                cl["clue"] = clue_by_answer.get(cl["answer"], "")

        crosswords = store.load_crosswords()
        existing_slugs = {c["slug"] for c in crosswords}
        title = request.form.get("title", "").strip() or "Otomatik Oluşturulan Bulmaca"
        now = _now_iso()
        doc = {
            "id": uuid.uuid4().hex[:10],
            "title": title,
            "slug": store.unique_slug(title, existing_slugs),
            "publication_date": "",
            "issue": "",
            "difficulty": "medium",
            "status": "draft",
            "description": "",
            "author": "Emir the Composer",
            "width": len(grid[0]) if grid else 0,
            "height": len(grid),
            "grid": grid,
            "clues": clues,
            "settings": _default_crossword_settings(),
            "created_at": now,
            "updated_at": now,
        }
        crosswords.append(doc)
        store.save_crosswords(crosswords)

        if unplaced:
            flash(
                "Şu kelimeler otomatik olarak yerleştirilemedi, ızgarayı elle düzenleyerek "
                "ekleyebilirsiniz: " + ", ".join(unplaced), "error",
            )
        flash(f"{len(placements)} kelime yerleştirildi. Bulmacayı gözden geçirip yayımlayabilirsiniz.", "success")
        return redirect(url_for("admin.crossword_edit", cid=doc["id"]))

    return render_template("admin/crossword_generate.html")


@admin_bp.route("/oyunlar/bulmaca/<cid>/duzenle", methods=["GET", "POST"])
@permission_required("games")
def crossword_edit(cid):
    crosswords = store.load_crosswords()
    idx = next((i for i, c in enumerate(crosswords) if c["id"] == cid), None)
    if idx is None:
        abort(404)
    cw = crosswords[idx]

    if request.method == "POST":
        existing_slugs = {c["slug"] for c in crosswords}
        meta = _game_meta_from_form(request.form, "crossword")
        cw["title"] = meta["title"] or cw["title"]
        cw["slug"] = store.unique_slug(cw["title"], existing_slugs, current_slug=cw["slug"])
        cw["publication_date"] = meta["publication_date"]
        cw["issue"] = meta["issue"]
        cw["difficulty"] = meta["difficulty"]
        cw["description"] = meta["description"]
        cw["author"] = meta["author"]
        cw["settings"] = {
            "allow_check_letter": bool(request.form.get("allow_check_letter")),
            "allow_check_word": bool(request.form.get("allow_check_word")),
            "allow_check_puzzle": bool(request.form.get("allow_check_puzzle")),
            "allow_hints": bool(request.form.get("allow_hints")),
            "reveal_answer": bool(request.form.get("reveal_answer")),
            "timer_enabled": bool(request.form.get("timer_enabled")),
        }
        cw["updated_at"] = _now_iso()
        crosswords[idx] = cw
        store.save_crosswords(crosswords)
        flash("Bulmaca bilgileri güncellendi.", "success")
        return redirect(url_for("admin.crossword_edit", cid=cid))

    numbers, slots = ge.compute_slots(cw["grid"])
    return render_template("admin/crossword_builder.html", crossword=cw,
                            difficulty_choices=DIFFICULTY_CHOICES, slot_count=len(slots))


@admin_bp.route("/oyunlar/bulmaca/<cid>/izgara", methods=["POST"])
@permission_required("games")
def crossword_save_grid(cid):
    cw = store.get_crossword(cid)
    if not cw:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(silent=True) or {}
    width, height = data.get("width"), data.get("height")
    raw_grid = data.get("grid")
    raw_clues = data.get("clues") or []

    if not isinstance(raw_grid, list) or not isinstance(width, int) or not isinstance(height, int):
        return jsonify({"error": "invalid payload"}), 400
    if len(raw_grid) != height or any(len(row) != width for row in raw_grid):
        return jsonify({"error": "grid size mismatch"}), 400

    grid = []
    for row in raw_grid:
        out_row = []
        for cell in row:
            block = bool(cell.get("block"))
            solution = "" if block else "".join(ch for ch in str(cell.get("solution", "")).upper() if ch.isalpha())[:1]
            out_row.append({"block": block, "solution": solution})
        grid.append(out_row)

    numbers, slots = ge.compute_slots(grid)
    existing_by_key = {(cl.get("number"), cl.get("direction")): cl.get("clue", "") for cl in raw_clues}
    clues = []
    for slot in slots:
        key = (slot["number"], slot["direction"])
        clues.append({
            "number": slot["number"], "direction": slot["direction"], "row": slot["row"], "col": slot["col"],
            "length": slot["length"], "answer": slot["answer"], "clue": existing_by_key.get(key, ""),
        })

    problems = ge.grid_conflicts(grid)

    crosswords = store.load_crosswords()
    idx = next((i for i, c in enumerate(crosswords) if c["id"] == cid), None)
    crosswords[idx]["width"] = width
    crosswords[idx]["height"] = height
    crosswords[idx]["grid"] = grid
    crosswords[idx]["clues"] = clues
    crosswords[idx]["updated_at"] = _now_iso()
    store.save_crosswords(crosswords)

    empty_clues = [f"{cl['number']} {cl['direction']}" for cl in clues if not cl["clue"].strip()]
    return jsonify({
        "ok": True, "slot_count": len(slots), "conflicts": problems, "empty_clues": empty_clues,
    })


@admin_bp.route("/oyunlar/bulmaca/<cid>/durum", methods=["POST"])
@permission_required("games")
def crossword_set_status(cid):
    crosswords = store.load_crosswords()
    idx = next((i for i, c in enumerate(crosswords) if c["id"] == cid), None)
    if idx is None:
        abort(404)
    new_status = request.form.get("status")
    if new_status not in ("draft", "published", "archived"):
        abort(400)

    if new_status == "published":
        cw = crosswords[idx]
        problems = []
        if ge.grid_conflicts(cw["grid"]):
            problems.append("Izgarada tek başına kalmış (bağlantısız) harf kareleri var.")
        numbers, slots = ge.compute_slots(cw["grid"])
        if not slots:
            problems.append("Izgarada henüz hiç kelime yok.")
        for slot in slots:
            if " " in slot["answer"] or not slot["answer"]:
                problems.append(f"{slot['number']} {slot['direction']}: bazı kareler boş bırakılmış.")
        clue_map = {(cl["number"], cl["direction"]): cl["clue"] for cl in cw.get("clues", [])}
        for slot in slots:
            if not (clue_map.get((slot["number"], slot["direction"]) ) or "").strip():
                problems.append(f"{slot['number']} {slot['direction']} için ipucu metni eksik.")
        if problems:
            for p in problems:
                flash(p, "error")
            flash("Bulmaca yayımlanamadı, önce yukarıdaki sorunları giderin.", "error")
            return redirect(url_for("admin.crossword_edit", cid=cid))

    crosswords[idx]["status"] = new_status
    crosswords[idx]["updated_at"] = _now_iso()
    store.save_crosswords(crosswords)
    flash(f"Bulmaca durumu güncellendi: {GAME_STATUS_LABELS.get(new_status, new_status)}.", "success")
    return redirect(request.referrer or url_for("admin.crossword_list"))


@admin_bp.route("/oyunlar/bulmaca/<cid>/kopyala", methods=["POST"])
@permission_required("games")
def crossword_duplicate(cid):
    crosswords = store.load_crosswords()
    original = next((c for c in crosswords if c["id"] == cid), None)
    if not original:
        abort(404)
    import copy
    clone = copy.deepcopy(original)
    clone["id"] = uuid.uuid4().hex[:10]
    clone["title"] = original["title"] + " (Kopya)"
    existing_slugs = {c["slug"] for c in crosswords}
    clone["slug"] = store.unique_slug(clone["title"], existing_slugs)
    clone["status"] = "draft"
    now = _now_iso()
    clone["created_at"] = now
    clone["updated_at"] = now
    crosswords.append(clone)
    store.save_crosswords(crosswords)
    flash("Bulmaca kopyalandı.", "success")
    return redirect(url_for("admin.crossword_edit", cid=clone["id"]))


@admin_bp.route("/oyunlar/bulmaca/<cid>/sil", methods=["POST"])
@permission_required("games")
def crossword_delete(cid):
    crosswords = store.load_crosswords()
    crosswords = [c for c in crosswords if c["id"] != cid]
    store.save_crosswords(crosswords)
    flash("Bulmaca silindi.", "success")
    return redirect(url_for("admin.crossword_list"))


@admin_bp.route("/oyunlar/bulmaca/<cid>/onizle")
@permission_required("games")
def crossword_preview(cid):
    cw = store.get_crossword(cid)
    if not cw:
        abort(404)
    payload = crossword_public_payload(cw)
    return render_template("games/crossword_play.html", crossword=cw, payload=payload,
                            difficulty_labels=DIFFICULTY_LABELS, preview=True)


@admin_bp.route("/oyunlar/bulmaca/<cid>/png")
@permission_required("games")
def crossword_png(cid):
    cw = store.get_crossword(cid)
    if not cw:
        abort(404)
    buf = games_export.build_crossword_png(cw)
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"{cw['slug']}.png")


# ---- sudoku ----------------------------------------------------------------

def _parse_sudoku_grid_from_form(form, prefix="cell"):
    grid = [[0] * 9 for _ in range(9)]
    for r in range(9):
        for c in range(9):
            raw = form.get(f"{prefix}_{r}_{c}", "").strip()
            if raw.isdigit() and 1 <= int(raw) <= 9:
                grid[r][c] = int(raw)
    return grid


@admin_bp.route("/oyunlar/sudoku")
@permission_required("games")
def sudoku_list():
    sudokus = store.all_sudokus_sorted()
    return render_template("admin/sudoku_list.html", sudokus=sudokus,
                            difficulty_labels=DIFFICULTY_LABELS, status_labels=GAME_STATUS_LABELS)


@admin_bp.route("/oyunlar/sudoku/yeni", methods=["GET", "POST"])
@permission_required("games")
def sudoku_new():
    if request.method == "POST":
        meta = _game_meta_from_form(request.form, "sudoku")
        grid = _parse_sudoku_grid_from_form(request.form)
        result = ge.validate_manual_sudoku(grid)

        sudokus = store.load_sudokus()
        existing_slugs = {s["slug"] for s in sudokus}
        title = meta["title"] or "Yeni Sudoku"
        now = _now_iso()
        doc = {
            "id": uuid.uuid4().hex[:10],
            "title": title,
            "slug": store.unique_slug(title, existing_slugs),
            "publication_date": meta["publication_date"],
            "issue": meta["issue"],
            "difficulty": meta["difficulty"],
            "status": "draft",
            "description": meta["description"],
            "starting_grid": grid,
            "solution_grid": result["solution"],
            "settings": _default_sudoku_settings(),
            "created_at": now,
            "updated_at": now,
        }
        sudokus.append(doc)
        store.save_sudokus(sudokus)

        if not result["ok"]:
            _flash_sudoku_validation(result)
        else:
            flash("Sudoku oluşturuldu ve doğrulandı (tek çözümü var).", "success")
        return redirect(url_for("admin.sudoku_edit", sid=doc["id"]))

    return render_template("admin/sudoku_new.html", difficulty_choices=DIFFICULTY_CHOICES,
                            empty_grid=[[0] * 9 for _ in range(9)])


def _flash_sudoku_validation(result):
    reason = result.get("reason")
    if reason == "conflict":
        flash("Girilen rakamlarda satır/sütun/kutu çakışması var — bunlar düzeltilmeden yayımlanamaz.", "error")
    elif reason == "no_solution":
        flash("Bu başlangıç durumunun geçerli bir çözümü yok — bulmaca çözülemez durumda.", "error")
    elif reason == "multiple_solutions":
        flash("Bu bulmacanın birden fazla geçerli çözümü var — tek çözümlü olması için daha fazla ipucu (verilen rakam) ekleyin.", "error")


@admin_bp.route("/oyunlar/sudoku/olustur", methods=["GET", "POST"])
@permission_required("games")
def sudoku_generate():
    if request.method == "POST":
        difficulty = request.form.get("difficulty")
        if difficulty not in DIFFICULTY_CHOICES:
            difficulty = "medium"
        puzzle, solution = ge.generate_sudoku(difficulty)

        sudokus = store.load_sudokus()
        existing_slugs = {s["slug"] for s in sudokus}
        title = request.form.get("title", "").strip() or f"Otomatik Sudoku ({DIFFICULTY_LABELS[difficulty]})"
        now = _now_iso()
        doc = {
            "id": uuid.uuid4().hex[:10],
            "title": title,
            "slug": store.unique_slug(title, existing_slugs),
            "publication_date": "",
            "issue": "",
            "difficulty": difficulty,
            "status": "draft",
            "description": "",
            "starting_grid": puzzle,
            "solution_grid": solution,
            "settings": _default_sudoku_settings(),
            "created_at": now,
            "updated_at": now,
        }
        sudokus.append(doc)
        store.save_sudokus(sudokus)
        givens = sum(1 for r in range(9) for c in range(9) if puzzle[r][c] != 0)
        flash(f"{DIFFICULTY_LABELS[difficulty]} zorlukta, {givens} verilen rakamlı, tek çözümlü bir sudoku üretildi.", "success")
        return redirect(url_for("admin.sudoku_edit", sid=doc["id"]))

    return render_template("admin/sudoku_generate.html", difficulty_choices=DIFFICULTY_CHOICES,
                            difficulty_labels=DIFFICULTY_LABELS)


@admin_bp.route("/oyunlar/sudoku/<sid>/duzenle", methods=["GET", "POST"])
@permission_required("games")
def sudoku_edit(sid):
    sudokus = store.load_sudokus()
    idx = next((i for i, s in enumerate(sudokus) if s["id"] == sid), None)
    if idx is None:
        abort(404)
    sd = sudokus[idx]

    if request.method == "POST":
        form_kind = request.form.get("form_kind", "grid")
        existing_slugs = {s["slug"] for s in sudokus}

        if form_kind == "meta":
            meta = _game_meta_from_form(request.form, "sudoku")
            sd["title"] = meta["title"] or sd["title"]
            sd["slug"] = store.unique_slug(sd["title"], existing_slugs, current_slug=sd["slug"])
            sd["publication_date"] = meta["publication_date"]
            sd["issue"] = meta["issue"]
            sd["difficulty"] = meta["difficulty"]
            sd["description"] = meta["description"]
            sd["settings"] = {
                "error_checking": request.form.get("error_checking") if request.form.get("error_checking") in
                    ("immediate", "on_check", "disabled") else "on_check",
                "hints_enabled": bool(request.form.get("hints_enabled")),
                "max_hints": max(0, min(int(request.form.get("max_hints", 3) or 0), 20)),
                "notes_enabled": bool(request.form.get("notes_enabled")),
                "timer_enabled": bool(request.form.get("timer_enabled")),
                "conflict_highlighting": bool(request.form.get("conflict_highlighting")),
                "mistake_counter": bool(request.form.get("mistake_counter")),
            }
            flash("Sudoku bilgileri güncellendi.", "success")
        else:
            grid = _parse_sudoku_grid_from_form(request.form)
            result = ge.validate_manual_sudoku(grid)
            sd["starting_grid"] = grid
            sd["solution_grid"] = result["solution"]
            if result["ok"]:
                flash("Bulmaca doğrulandı: tek geçerli çözümü var.", "success")
            else:
                _flash_sudoku_validation(result)

        sd["updated_at"] = _now_iso()
        sudokus[idx] = sd
        store.save_sudokus(sudokus)
        return redirect(url_for("admin.sudoku_edit", sid=sid))

    return render_template("admin/sudoku_builder.html", sudoku=sd, difficulty_choices=DIFFICULTY_CHOICES)


@admin_bp.route("/oyunlar/sudoku/<sid>/durum", methods=["POST"])
@permission_required("games")
def sudoku_set_status(sid):
    sudokus = store.load_sudokus()
    idx = next((i for i, s in enumerate(sudokus) if s["id"] == sid), None)
    if idx is None:
        abort(404)
    new_status = request.form.get("status")
    if new_status not in ("draft", "published", "archived"):
        abort(400)

    if new_status == "published":
        sd = sudokus[idx]
        result = ge.validate_manual_sudoku(sd["starting_grid"])
        if not result["ok"] or sd.get("solution_grid") is None:
            _flash_sudoku_validation(result)
            flash("Sudoku yayımlanamadı — geçerli, tek çözümlü bir bulmaca olmalı.", "error")
            return redirect(url_for("admin.sudoku_edit", sid=sid))
        sudokus[idx]["solution_grid"] = result["solution"]

    sudokus[idx]["status"] = new_status
    sudokus[idx]["updated_at"] = _now_iso()
    store.save_sudokus(sudokus)
    flash(f"Sudoku durumu güncellendi: {GAME_STATUS_LABELS.get(new_status, new_status)}.", "success")
    return redirect(request.referrer or url_for("admin.sudoku_list"))


@admin_bp.route("/oyunlar/sudoku/<sid>/kopyala", methods=["POST"])
@permission_required("games")
def sudoku_duplicate(sid):
    sudokus = store.load_sudokus()
    original = next((s for s in sudokus if s["id"] == sid), None)
    if not original:
        abort(404)
    import copy
    clone = copy.deepcopy(original)
    clone["id"] = uuid.uuid4().hex[:10]
    clone["title"] = original["title"] + " (Kopya)"
    existing_slugs = {s["slug"] for s in sudokus}
    clone["slug"] = store.unique_slug(clone["title"], existing_slugs)
    clone["status"] = "draft"
    now = _now_iso()
    clone["created_at"] = now
    clone["updated_at"] = now
    sudokus.append(clone)
    store.save_sudokus(sudokus)
    flash("Sudoku kopyalandı.", "success")
    return redirect(url_for("admin.sudoku_edit", sid=clone["id"]))


@admin_bp.route("/oyunlar/sudoku/<sid>/sil", methods=["POST"])
@permission_required("games")
def sudoku_delete(sid):
    sudokus = store.load_sudokus()
    sudokus = [s for s in sudokus if s["id"] != sid]
    store.save_sudokus(sudokus)
    flash("Sudoku silindi.", "success")
    return redirect(url_for("admin.sudoku_list"))


@admin_bp.route("/oyunlar/sudoku/<sid>/onizle")
@permission_required("games")
def sudoku_preview(sid):
    sd = store.get_sudoku(sid)
    if not sd:
        abort(404)
    payload = sudoku_public_payload(sd)
    return render_template("games/sudoku_play.html", sudoku=sd, payload=payload,
                            difficulty_labels=DIFFICULTY_LABELS, preview=True)


@admin_bp.route("/oyunlar/sudoku/<sid>/png")
@permission_required("games")
def sudoku_png(sid):
    sd = store.get_sudoku(sid)
    if not sd:
        abort(404)
    solved = request.args.get("solved") == "1"
    buf = games_export.build_sudoku_png(sd, solved=solved)
    suffix = "-cozum" if solved else ""
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"{sd['slug']}{suffix}.png")


# --------------------------------------------------------- admin: authors --

EDITORIAL_ROLE_CHOICES = [
    "Muhabir", "Editör", "Genel Yayın Yönetmeni", "Opinion Columnist",
    "Köşe Yazarı", "Katkıda Bulunan", "Fotoğraf Editörü", "Görsel Tasarım",
]


def _save_author_image(author, field_name, subdir_prefix):
    file = request.files.get(field_name)
    if not (file and file.filename):
        return None
    value, error = uploads.save_author_image(file, author["id"], subdir_prefix, AUTHOR_IMG_DIR)
    if error:
        flash(error, "error")
        return None
    uploads.delete_stored(author.get(field_name))
    return value


def _apply_self_service_fields(author, form, files):
    """The whitelist of fields a non-master author is allowed to touch on
    their own profile. Deliberately does not read slug/editorial_role/
    is_opinion_columnist/status from the form at all -- so there is no
    field an author could add to a spoofed request to escalate anything."""
    author["short_bio"] = form.get("short_bio", "").strip()[:280]
    author["full_bio"] = form.get("full_bio", "").strip()[:4000]

    twitter = sanitize_twitter_handle(form.get("twitter_handle", ""))
    author["twitter_handle"] = twitter
    mc = sanitize_minecraft_username(form.get("minecraft_username", ""))
    if form.get("minecraft_username", "").strip() and not mc:
        flash("Minecraft kullanıcı adı geçersiz görünüyor (3-16 karakter, harf/rakam/alt çizgi) — kaydedilmedi.", "error")
    else:
        author["minecraft_username"] = mc

    contact_email = sanitize_contact_email(form.get("contact_email", ""))
    if form.get("contact_email", "").strip() and not contact_email:
        flash("Halka açık iletişim e-postası geçersiz görünüyor — kaydedilmedi.", "error")
    else:
        author["contact_email"] = contact_email

    new_profile_img = _save_author_image(author, "profile_image", "profil")
    if new_profile_img:
        author["profile_image"] = new_profile_img
    elif form.get("remove_profile_image"):
        author["profile_image"] = None

    new_cover_img = _save_author_image(author, "cover_image", "kapak")
    if new_cover_img:
        author["cover_image"] = new_cover_img
    elif form.get("remove_cover_image"):
        author["cover_image"] = None

    author["updated_at"] = _now_iso()
    return author


@admin_bp.route("/profilim", methods=["GET", "POST"])
@login_required
def my_profile():
    user = _resolve_logged_in_user()
    author = store.get_author_by_user_id(user["id"])
    if not author:
        flash("Hesabınıza bağlı bir yazar profili bulunmuyor. Bir Master Admin'in sizin için bir profil oluşturması gerekiyor.", "error")
        return render_template("admin/my_profile.html", author=None, active="my_profile")

    if request.method == "POST":
        authors = store.load_authors()
        idx = next(i for i, a in enumerate(authors) if a["id"] == author["id"])
        authors[idx] = _apply_self_service_fields(authors[idx], request.form, request.files)
        store.save_authors(authors)
        flash("Profiliniz güncellendi.", "success")
        return redirect(url_for("admin.my_profile"))

    return render_template("admin/my_profile.html", author=author, active="my_profile")


@admin_bp.route("/yazarlar")
@master_admin_required
def authors_list():
    authors = store.load_authors()
    users_by_id = {u["id"]: u for u in store.load_users()}
    rows = []
    for a in authors:
        u = users_by_id.get(a.get("user_id"))
        rows.append({
            "author": a,
            "user": u,
            "article_count": len(store.articles_by_author(a["id"])),
        })
    rows.sort(key=lambda r: r["author"]["display_name"])
    roles_by_id = {r["id"]: r["name"] for r in store.load_roles()}
    return render_template("admin/authors_list.html", rows=rows, roles_by_id=roles_by_id, active="authors")


@admin_bp.route("/yazarlar/yeni", methods=["GET", "POST"])
@master_admin_required
def author_new():
    actor = _resolve_logged_in_user()
    if request.method == "POST":
        display_name = request.form.get("display_name", "").strip()
        email = request.form.get("email", "").strip().lower()
        errors = []
        if not display_name:
            errors.append("Görünen ad zorunludur.")
        if not email or "@" not in email:
            errors.append("Geçerli bir e-posta girin.")
        elif store.get_user_by_email(email):
            errors.append("Bu e-posta zaten kullanımda.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/author_new.html", editorial_roles=EDITORIAL_ROLE_CHOICES, roles=store.load_roles(), form=request.form, active="authors")

        now = _now_iso()
        temp_password = generate_temp_password()
        new_user = {
            "id": uuid.uuid4().hex[:10],
            "email": email,
            "password_hash": generate_password_hash(temp_password, method="pbkdf2:sha256"),
            "account_role": request.form.get("account_role") if request.form.get("account_role") in {r["id"] for r in store.load_roles()} else "author",
            "status": "active",
            "must_change_password": True,
            "created_at": now,
            "updated_at": now,
        }
        users = store.load_users()
        users.append(new_user)
        store.save_users(users)

        authors = store.load_authors()
        existing_slugs = {a["slug"] for a in authors}
        slug = store.unique_author_slug(request.form.get("slug") or display_name, existing_slugs)
        new_author = {
            "id": uuid.uuid4().hex[:10],
            "user_id": new_user["id"],
            "display_name": display_name,
            "slug": slug,
            "slug_history": [],
            "profile_image": None,
            "cover_image": None,
            "short_bio": request.form.get("short_bio", "").strip()[:280],
            "full_bio": request.form.get("full_bio", "").strip()[:4000],
            "editorial_role": request.form.get("editorial_role", "").strip(),
            "is_opinion_columnist": bool(request.form.get("is_opinion_columnist")),
            "twitter_handle": sanitize_twitter_handle(request.form.get("twitter_handle", "")),
            "minecraft_username": sanitize_minecraft_username(request.form.get("minecraft_username", "")),
            "contact_email": sanitize_contact_email(request.form.get("contact_email", "")),
            "join_date": request.form.get("join_date") or now[:10],
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        new_author["profile_image"] = _save_author_image(new_author, "profile_image", "profil")
        new_author["cover_image"] = _save_author_image(new_author, "cover_image", "kapak")
        authors.append(new_author)
        store.save_authors(authors)

        store.append_audit(actor["email"], "author_created", f"{display_name} <{email}>",
                            {"account_role": new_user["account_role"]})

        return render_template("admin/author_created.html", author=new_author, user=new_user, temp_password=temp_password)

    return render_template("admin/author_new.html", editorial_roles=EDITORIAL_ROLE_CHOICES, roles=store.load_roles(), form={}, active="authors")


@admin_bp.route("/yazarlar/<aid>/duzenle", methods=["GET", "POST"])
@master_admin_required
def author_edit_master(aid):
    actor = _resolve_logged_in_user()
    authors = store.load_authors()
    idx = next((i for i, a in enumerate(authors) if a["id"] == aid), None)
    if idx is None:
        abort(404)
    author = authors[idx]
    linked_user = store.get_user(author.get("user_id"))

    if request.method == "POST":
        form = request.form
        display_name = form.get("display_name", "").strip() or author["display_name"]
        author["display_name"] = display_name

        new_slug_input = form.get("slug", "").strip()
        existing_slugs = {a["slug"] for a in authors if a["id"] != aid}
        new_slug = store.unique_author_slug(new_slug_input or display_name, existing_slugs, current_slug=author["slug"])
        if new_slug != author["slug"]:
            history = author.get("slug_history") or []
            if author["slug"] not in history:
                history.append(author["slug"])
            author["slug_history"] = history
            author["slug"] = new_slug

        author["editorial_role"] = form.get("editorial_role", "").strip()
        author["is_opinion_columnist"] = bool(form.get("is_opinion_columnist"))
        author["short_bio"] = form.get("short_bio", "").strip()[:280]
        author["full_bio"] = form.get("full_bio", "").strip()[:4000]
        author["twitter_handle"] = sanitize_twitter_handle(form.get("twitter_handle", ""))
        author["minecraft_username"] = sanitize_minecraft_username(form.get("minecraft_username", ""))
        author["contact_email"] = sanitize_contact_email(form.get("contact_email", ""))
        author["join_date"] = form.get("join_date") or author.get("join_date")

        new_profile_img = _save_author_image(author, "profile_image", "profil")
        if new_profile_img:
            author["profile_image"] = new_profile_img
        elif form.get("remove_profile_image"):
            author["profile_image"] = None
        new_cover_img = _save_author_image(author, "cover_image", "kapak")
        if new_cover_img:
            author["cover_image"] = new_cover_img
        elif form.get("remove_cover_image"):
            author["cover_image"] = None

        author["updated_at"] = _now_iso()

        # Linked account fields (email / account role) -- Master Admin only,
        # and guarded against locking out the last active Master Admin.
        if linked_user:
            users = store.load_users()
            uidx = next(i for i, u in enumerate(users) if u["id"] == linked_user["id"])
            new_email = form.get("email", "").strip().lower()
            if new_email and new_email != linked_user["email"]:
                existing = store.get_user_by_email(new_email)
                if existing and existing["id"] != linked_user["id"]:
                    flash("Bu e-posta başka bir hesap tarafından kullanılıyor — e-posta değiştirilmedi.", "error")
                else:
                    store.append_audit(actor["email"], "email_changed", linked_user["email"], {"new_email": new_email})
                    users[uidx]["email"] = new_email

            new_role = form.get("account_role")
            if new_role in {r["id"] for r in store.load_roles()} and new_role != users[uidx]["account_role"]:
                if users[uidx]["account_role"] == "master_admin" and new_role != "master_admin" \
                        and store.count_active_master_admins(exclude_id=users[uidx]["id"]) < 1:
                    flash("Son aktif Master Admin'in rolü değiştirilemez.", "error")
                else:
                    store.append_audit(actor["email"], "role_changed", users[uidx]["email"],
                                        {"from": users[uidx]["account_role"], "to": new_role})
                    users[uidx]["account_role"] = new_role

            users[uidx]["updated_at"] = _now_iso()
            store.save_users(users)

        authors[idx] = author
        store.save_authors(authors)
        store.append_audit(actor["email"], "profile_edited_by_admin", author["display_name"])
        flash("Yazar profili güncellendi.", "success")
        return redirect(url_for("admin.author_edit_master", aid=aid))

    return render_template("admin/author_edit_master.html", author=author, linked_user=linked_user,
                            editorial_roles=EDITORIAL_ROLE_CHOICES, roles=store.load_roles(), active="authors",
                            article_count=len(store.articles_by_author(aid)))


@admin_bp.route("/yazarlar/<aid>/sifre-sifirla", methods=["POST"])
@master_admin_required
def author_reset_password(aid):
    actor = _resolve_logged_in_user()
    author = store.get_author(aid)
    if not author:
        abort(404)
    linked_user = store.get_user(author.get("user_id"))
    if not linked_user:
        flash("Bu yazarın bağlı bir hesabı yok.", "error")
        return redirect(url_for("admin.author_edit_master", aid=aid))

    temp_password = generate_temp_password()
    _set_user_password(linked_user["id"], temp_password, must_change=True)
    store.append_audit(actor["email"], "password_reset_initiated", linked_user["email"])
    return render_template("admin/author_created.html", author=author, user=linked_user,
                            temp_password=temp_password, reset=True)


@admin_bp.route("/yazarlar/<aid>/hesap-durumu", methods=["POST"])
@master_admin_required
def author_set_account_status(aid):
    actor = _resolve_logged_in_user()
    author = store.get_author(aid)
    if not author:
        abort(404)
    linked_user = store.get_user(author.get("user_id"))
    if not linked_user:
        flash("Bu yazarın bağlı bir hesabı yok.", "error")
        return redirect(url_for("admin.author_edit_master", aid=aid))

    new_status = request.form.get("status")
    if new_status not in ("active", "disabled"):
        abort(400)

    if new_status == "disabled" and linked_user["account_role"] == "master_admin" \
            and store.count_active_master_admins(exclude_id=linked_user["id"]) < 1:
        flash("Son aktif Master Admin devre dışı bırakılamaz.", "error")
        return redirect(url_for("admin.author_edit_master", aid=aid))

    users = store.load_users()
    uidx = next(i for i, u in enumerate(users) if u["id"] == linked_user["id"])
    users[uidx]["status"] = new_status
    users[uidx]["updated_at"] = _now_iso()
    store.save_users(users)
    store.append_audit(actor["email"], "account_" + new_status, linked_user["email"])
    flash("Hesap durumu güncellendi: " + ("Devre Dışı" if new_status == "disabled" else "Aktif"), "success")
    return redirect(url_for("admin.author_edit_master", aid=aid))


@admin_bp.route("/yazarlar/<aid>/arsivle", methods=["POST"])
@master_admin_required
def author_toggle_archive(aid):
    actor = _resolve_logged_in_user()
    authors = store.load_authors()
    idx = next((i for i, a in enumerate(authors) if a["id"] == aid), None)
    if idx is None:
        abort(404)
    new_status = "archived" if authors[idx].get("status") != "archived" else "active"
    authors[idx]["status"] = new_status
    authors[idx]["updated_at"] = _now_iso()
    store.save_authors(authors)
    store.append_audit(actor["email"], "author_" + new_status, authors[idx]["display_name"])
    flash("Yazar " + ("arşivlendi." if new_status == "archived" else "arşivden çıkarıldı."), "success")
    return redirect(url_for("admin.authors_list"))


@admin_bp.route("/yazarlar/<aid>/sil", methods=["POST"])
@master_admin_required
def author_delete(aid):
    actor = _resolve_logged_in_user()
    authors = store.load_authors()
    author = next((a for a in authors if a["id"] == aid), None)
    if not author:
        abort(404)

    article_count = len(store.articles_by_author(aid))
    if article_count > 0:
        flash(
            f"'{author['display_name']}' silinemedi: {article_count} yayımlanmış makale bu yazara bağlı. "
            "Önce makaleleri başka bir yazara aktarın ya da bu yazarı Arşivle.", "error",
        )
        return redirect(url_for("admin.authors_list"))

    linked_user_id = author.get("user_id")
    if linked_user_id:
        users = store.load_users()
        uidx = next((i for i, u in enumerate(users) if u["id"] == linked_user_id), None)
        if uidx is not None:
            if users[uidx]["account_role"] == "master_admin" and store.count_active_master_admins(exclude_id=linked_user_id) < 1:
                flash("Son aktif Master Admin'e bağlı yazar silinemez.", "error")
                return redirect(url_for("admin.authors_list"))
            users[uidx]["status"] = "disabled"
            users[uidx]["updated_at"] = _now_iso()
            store.save_users(users)

    authors = [a for a in authors if a["id"] != aid]
    store.save_authors(authors)
    store.append_audit(actor["email"], "author_deleted", author["display_name"])
    flash(f"'{author['display_name']}' silindi.", "success")
    return redirect(url_for("admin.authors_list"))


@admin_bp.route("/denetim-kaydi")
@permission_required("audit_log")
def audit_log_view():
    return render_template("admin/audit_log.html", entries=store.recent_audit_log(), active="audit")


# ------------------------------------------------ admin: newspaper management --
# Gazete Yönetimi (the public masthead) -- master_admin only, deliberately
# NOT a permission_required() custom-role permission. Being a writer,
# editor, or even holding another admin permission never implies newspaper-
# management membership; only a master_admin may add someone here, exactly
# like author/role management above.

def _management_form_to_dict(form, existing=None):
    errors = []
    display_name = form.get("display_name", "").strip()[:120]
    role_title = form.get("role_title", "").strip()[:120]
    bio = form.get("bio", "").strip()[:2000]
    linked_author_id = form.get("linked_author_id") or None
    if linked_author_id and not store.get_author(linked_author_id):
        linked_author_id = None

    if not display_name:
        errors.append("Ad Soyad zorunludur.")
    if not role_title:
        errors.append("Gazete Yönetimi unvanı zorunludur.")

    try:
        display_order = int(form.get("display_order", "0") or "0")
    except ValueError:
        display_order = 0

    linked_author = store.get_author(linked_author_id) if linked_author_id else None
    data = {
        "linked_author_id": linked_author_id,
        "linked_user_id": linked_author.get("user_id") if linked_author else None,
        "display_name": display_name,
        "role_title": role_title,
        "bio": bio,
        "display_order": display_order,
        # Active/inactive is deliberately not a form field here -- it's
        # toggled only via the dedicated /durum route below, so editing an
        # entry's name/role/bio can never accidentally flip it off.
        "active": existing.get("active", True) if existing else True,
    }
    return data, errors


@admin_bp.route("/gazete-yonetimi")
@master_admin_required
def management_list():
    entries = sorted(store.load_management(), key=lambda e: e.get("display_order", 0))
    authors_by_id = {a["id"]: a for a in store.load_authors()}
    return render_template("admin/management_list.html", entries=entries, authors_by_id=authors_by_id, active="management")


@admin_bp.route("/gazete-yonetimi/yeni", methods=["GET", "POST"])
@master_admin_required
def management_new():
    actor = _resolve_logged_in_user()
    authors = store.load_authors()
    if request.method == "POST":
        data, errors = _management_form_to_dict(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/management_form.html", entry=None, form=request.form,
                                    authors=authors, active="management")

        now = _now_iso()
        data.update({
            "id": uuid.uuid4().hex[:10], "image_override": None,
            "created_at": now, "updated_at": now,
            "created_by": actor["email"], "updated_by": actor["email"],
        })

        file = request.files.get("image_file")
        if file and file.filename:
            image_value, upload_error = uploads.save_management_image(file, data["id"], MANAGEMENT_IMG_DIR)
            if upload_error:
                flash(upload_error, "error")
            else:
                data["image_override"] = image_value

        entries = store.load_management()
        entries.append(data)
        store.save_management(entries)
        store.append_audit(actor["email"], "management_member_added", data["display_name"], {"role_title": data["role_title"]})
        flash("Gazete Yönetimi üyesi eklendi.", "success")
        return redirect(url_for("admin.management_list"))

    return render_template("admin/management_form.html", entry=None, form={}, authors=authors, active="management")


@admin_bp.route("/gazete-yonetimi/<mid>/duzenle", methods=["GET", "POST"])
@master_admin_required
def management_edit(mid):
    actor = _resolve_logged_in_user()
    entries = store.load_management()
    idx = next((i for i, e in enumerate(entries) if e["id"] == mid), None)
    if idx is None:
        abort(404)
    entry = entries[idx]
    authors = store.load_authors()

    if request.method == "POST":
        data, errors = _management_form_to_dict(request.form, existing=entry)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/management_form.html", entry=entry, form=request.form,
                                    authors=authors, active="management")

        data["id"] = mid
        data["image_override"] = entry.get("image_override")
        data["created_at"] = entry.get("created_at")
        data["created_by"] = entry.get("created_by")
        data["updated_at"] = _now_iso()
        data["updated_by"] = actor["email"]

        file = request.files.get("image_file")
        if file and file.filename:
            image_value, upload_error = uploads.save_management_image(file, mid, MANAGEMENT_IMG_DIR)
            if upload_error:
                flash(upload_error, "error")
            else:
                uploads.delete_stored(entry.get("image_override"))
                data["image_override"] = image_value
        elif request.form.get("remove_image"):
            uploads.delete_stored(entry.get("image_override"))
            data["image_override"] = None

        entries[idx] = data
        store.save_management(entries)
        store.append_audit(actor["email"], "management_member_edited", data["display_name"], {"role_title": data["role_title"]})
        flash("Gazete Yönetimi üyesi güncellendi.", "success")
        return redirect(url_for("admin.management_list"))

    return render_template("admin/management_form.html", entry=entry, form=entry, authors=authors, active="management")


@admin_bp.route("/gazete-yonetimi/<mid>/durum", methods=["POST"])
@master_admin_required
def management_toggle_active(mid):
    actor = _resolve_logged_in_user()
    entries = store.load_management()
    idx = next((i for i, e in enumerate(entries) if e["id"] == mid), None)
    if idx is None:
        abort(404)
    entries[idx]["active"] = not entries[idx].get("active")
    entries[idx]["updated_at"] = _now_iso()
    entries[idx]["updated_by"] = actor["email"]
    store.save_management(entries)
    action = "management_member_activated" if entries[idx]["active"] else "management_member_deactivated"
    store.append_audit(actor["email"], action, entries[idx]["display_name"])
    flash("Durum güncellendi.", "success")
    return redirect(url_for("admin.management_list"))


@admin_bp.route("/gazete-yonetimi/<mid>/sil", methods=["POST"])
@master_admin_required
def management_delete(mid):
    """Removes the masthead entry only -- never touches the linked author
    profile, its user account, or writer permissions. Those live in
    completely separate files and are never written by this route."""
    actor = _resolve_logged_in_user()
    entries = store.load_management()
    entry = next((e for e in entries if e["id"] == mid), None)
    if not entry:
        abort(404)
    uploads.delete_stored(entry.get("image_override"))
    entries = [e for e in entries if e["id"] != mid]
    store.save_management(entries)
    store.append_audit(actor["email"], "management_member_removed", entry["display_name"])
    flash("Gazete Yönetimi üyesi kaldırıldı (hesabı/yazar profili etkilenmedi).", "success")
    return redirect(url_for("admin.management_list"))


@admin_bp.route("/gazete-yonetimi/<mid>/yukari", methods=["POST"])
@master_admin_required
def management_move_up(mid):
    return _management_reorder(mid, -1)


@admin_bp.route("/gazete-yonetimi/<mid>/asagi", methods=["POST"])
@master_admin_required
def management_move_down(mid):
    return _management_reorder(mid, 1)


def _management_reorder(mid, direction):
    actor = _resolve_logged_in_user()
    entries = sorted(store.load_management(), key=lambda e: e.get("display_order", 0))
    idx = next((i for i, e in enumerate(entries) if e["id"] == mid), None)
    if idx is None:
        abort(404)
    swap_idx = idx + direction
    if 0 <= swap_idx < len(entries):
        entries[idx]["display_order"], entries[swap_idx]["display_order"] = (
            entries[swap_idx]["display_order"], entries[idx]["display_order"],
        )
        store.save_management(entries)
        store.append_audit(actor["email"], "management_reordered", entries[idx]["display_name"])
    return redirect(url_for("admin.management_list"))


# --------------------------------------------------------- admin: settings --

@admin_bp.route("/site-ayarlari")
@permission_required("site_settings")
def site_settings():
    site = store.load_site()
    return render_template("admin/site_settings.html", site=site,
                            contact=site.get("contact") or {}, active="site_settings")


@admin_bp.route("/site-ayarlari/yayin-bilgileri", methods=["POST"])
@permission_required("site_settings")
def site_settings_publication():
    actor = _resolve_logged_in_user()
    site = store.load_site()
    site["issue_label"] = request.form.get("issue_label", "").strip() or site.get("issue_label", "")
    try:
        site["issue_no"] = int(request.form.get("issue_no", "").strip())
    except ValueError:
        pass
    site["imtiyaz_sahibi"] = request.form.get("imtiyaz_sahibi", "").strip()
    site["genel_yayin_yonetmeni"] = request.form.get("genel_yayin_yonetmeni", "").strip()
    site["editor"] = request.form.get("editor", "").strip()
    site["gorsel_tasarim"] = request.form.get("gorsel_tasarim", "").strip()
    site["okur_iliskileri"] = request.form.get("okur_iliskileri", "").strip()
    store.save_site(site)
    store.append_audit(actor["email"], "site_settings_updated", "Yayın Bilgileri")
    flash("Yayın bilgileri güncellendi.", "success")
    return redirect(url_for("admin.site_settings"))


@admin_bp.route("/site-ayarlari/iletisim", methods=["POST"])
@permission_required("site_settings")
def site_settings_contact():
    actor = _resolve_logged_in_user()
    site = store.load_site()
    contact = site.get("contact") or {}
    contact["intro_title"] = request.form.get("intro_title", "").strip() or "Bize Ulaşın"
    contact["intro_text"] = request.form.get("intro_text", "").strip()
    contact["note"] = request.form.get("note", "").strip()
    subjects = [s.strip() for s in request.form.get("subjects", "").splitlines() if s.strip()]
    contact["subjects"] = subjects or CONTACT_SUBJECTS
    site["contact"] = contact
    store.save_site(site)
    store.append_audit(actor["email"], "site_settings_updated", "İletişim Bilgileri")
    flash("İletişim bilgileri güncellendi.", "success")
    return redirect(url_for("admin.site_settings"))


# ----------------------------------------------------------- admin: roles --
# Role management is itself master_admin_required only, and always will be --
# a role editable by anyone other than Master Admin could otherwise grant
# itself more permissions.

@admin_bp.route("/roller")
@master_admin_required
def roles_list():
    roles = store.load_roles()
    counts = {}
    for u in store.load_users():
        counts[u["account_role"]] = counts.get(u["account_role"], 0) + 1
    return render_template("admin/roles_list.html", roles=roles, counts=counts,
                            permission_choices=PERMISSION_CHOICES, active="roles")


@admin_bp.route("/roller/yeni", methods=["GET", "POST"])
@master_admin_required
def role_new():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Rol adı zorunludur.", "error")
            return render_template("admin/role_form.html", role=None, form=request.form,
                                    permission_choices=PERMISSION_CHOICES, active="roles")

        roles = store.load_roles()
        rid = store.unique_role_id(name, {r["id"] for r in roles})
        perms = [key for key, _ in PERMISSION_CHOICES if request.form.get(f"perm_{key}")]
        roles.append({"id": rid, "name": name, "permissions": perms, "system": False})
        store.save_roles(roles)

        actor = _resolve_logged_in_user()
        store.append_audit(actor["email"], "role_created", name, {"permissions": perms})
        flash("Rol oluşturuldu.", "success")
        return redirect(url_for("admin.roles_list"))

    return render_template("admin/role_form.html", role=None, form={},
                            permission_choices=PERMISSION_CHOICES, active="roles")


@admin_bp.route("/roller/<rid>/duzenle", methods=["GET", "POST"])
@master_admin_required
def role_edit(rid):
    roles = store.load_roles()
    idx = next((i for i, r in enumerate(roles) if r["id"] == rid), None)
    if idx is None:
        abort(404)
    role = roles[idx]
    if role.get("system"):
        abort(403)

    if request.method == "POST":
        name = request.form.get("name", "").strip() or role["name"]
        perms = [key for key, _ in PERMISSION_CHOICES if request.form.get(f"perm_{key}")]
        role["name"] = name
        role["permissions"] = perms
        roles[idx] = role
        store.save_roles(roles)

        actor = _resolve_logged_in_user()
        store.append_audit(actor["email"], "role_updated", name, {"permissions": perms})
        flash("Rol güncellendi.", "success")
        return redirect(url_for("admin.roles_list"))

    return render_template("admin/role_form.html", role=role, form=role,
                            permission_choices=PERMISSION_CHOICES, active="roles")


@admin_bp.route("/roller/<rid>/sil", methods=["POST"])
@master_admin_required
def role_delete(rid):
    roles = store.load_roles()
    role = next((r for r in roles if r["id"] == rid), None)
    if not role or role.get("system"):
        abort(403)

    in_use = [u for u in store.load_users() if u["account_role"] == rid]
    if in_use:
        flash(f"Bu rol {len(in_use)} hesap tarafından kullanılıyor — silmeden önce bu hesapları başka bir role taşıyın.", "error")
        return redirect(url_for("admin.roles_list"))

    roles = [r for r in roles if r["id"] != rid]
    store.save_roles(roles)
    actor = _resolve_logged_in_user()
    store.append_audit(actor["email"], "role_deleted", role["name"])
    flash("Rol silindi.", "success")
    return redirect(url_for("admin.roles_list"))


# -------------------------------------------------------- admin: advertising --
# Every route below is master_admin_required -- deliberately not a
# permission_required() custom-role permission, for the same reason author
# management is hardcoded to master_admin: this is a capability no role
# should ever be able to grant itself or anyone else by editing role data.
# Cloudflare Access (in front of admin.eurovillageherald.com) and Herald
# login (login_required, folded into master_admin_required) both already
# ran before this decorator even runs -- this is the third, final check in
# that chain, and it's the one that actually decides authorization.

def _ad_form_to_dict(form):
    errors = []
    internal_name = form.get("internal_name", "").strip()[:ads.MAX_LENGTHS["internal_name"]]
    sponsor_name = form.get("sponsor_name", "").strip()[:ads.MAX_LENGTHS["sponsor_name"]]
    headline = form.get("headline", "").strip()[:ads.MAX_LENGTHS["headline"]]
    body = form.get("body", "").strip()[:ads.MAX_LENGTHS["body"]]
    cta_text = form.get("cta_text", "").strip()[:ads.MAX_LENGTHS["cta_text"]]
    destination_url = form.get("destination_url", "").strip()

    if not internal_name:
        errors.append("Dahili isim zorunludur.")
    if not sponsor_name:
        errors.append("Sponsor / reklam veren adı zorunludur.")
    if not headline:
        errors.append("Başlık zorunludur.")
    url_error = ads.validate_destination_url(destination_url)
    if url_error:
        errors.append(url_error)

    try:
        priority = int(form.get("priority", "0") or "0")
    except ValueError:
        priority = 0

    data = {
        "internal_name": internal_name,
        "sponsor_name": sponsor_name,
        "headline": headline,
        "body": body or None,
        "cta_text": cta_text or None,
        "destination_url": destination_url,
        "priority": priority,
        "status": "active" if form.get("status") == "active" else "inactive",
        # Same EDITORIAL_TIMEZONE interpretation as article scheduling --
        # see editorial_tz.py. Stored in UTC either way.
        "start_at": editorial_tz.local_input_to_utc_iso(form.get("start_at", "")),
        "end_at": editorial_tz.local_input_to_utc_iso(form.get("end_at", "")),
    }
    return data, errors


def _normalize_dt_input(value):
    """`<input type="datetime-local">` submits "YYYY-MM-DDTHH:MM" with no
    timezone. This app has no per-admin timezone concept anywhere else
    either, so -- consistent with that -- the value is stored treated as
    UTC by simply appending a Z, rather than building out real timezone
    handling for a single-admin-scale tool."""
    value = (value or "").strip()
    if not value:
        return None
    if len(value) == 16:
        value += ":00"
    if not value.endswith("Z") and "+" not in value:
        value += "Z"
    return value


@admin_bp.route("/reklam")
@master_admin_required
def ads_dashboard():
    stats = ads.dashboard_stats()
    return render_template("admin/ads_dashboard.html", stats=stats,
                            status_labels=ads.STATUS_LABELS, active="ads")


@admin_bp.route("/reklam/ilanlar")
@master_admin_required
def ads_list():
    all_ads = sorted(store.load_ads(), key=lambda a: a.get("updated_at") or "", reverse=True)
    placement_counts = {}
    for p in store.load_placements():
        placement_counts[p["ad_id"]] = placement_counts.get(p["ad_id"], 0) + 1
    return render_template(
        "admin/ads_list.html", ads_list=all_ads, placement_counts=placement_counts,
        status_labels=ads.STATUS_LABELS, ad_status=ads.ad_effective_status, active="ads",
    )


@admin_bp.route("/reklam/ilanlar/yeni", methods=["GET", "POST"])
@master_admin_required
def ad_new():
    actor = _resolve_logged_in_user()
    if request.method == "POST":
        data, errors = _ad_form_to_dict(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/ad_form.html", ad=None, form=request.form, active="ads")

        now = _now_iso()
        ad_id = ads.new_ad_id()
        data.update({
            "id": ad_id, "image": None,
            "created_at": now, "updated_at": now,
            "created_by": actor["email"], "updated_by": actor["email"],
        })

        file = request.files.get("image_file")
        if file and file.filename:
            image_value, upload_error = uploads.save_ad_image(file, ad_id, AD_IMG_DIR)
            if upload_error:
                flash(upload_error, "error")
            else:
                data["image"] = image_value

        all_ads = store.load_ads()
        all_ads.append(data)
        store.save_ads(all_ads)
        store.append_audit(actor["email"], "ad_created", data["internal_name"])
        flash("Reklam oluşturuldu.", "success")
        return redirect(url_for("admin.ads_list"))

    return render_template("admin/ad_form.html", ad=None, form={}, active="ads")


@admin_bp.route("/reklam/ilanlar/<aid>/duzenle", methods=["GET", "POST"])
@master_admin_required
def ad_edit(aid):
    actor = _resolve_logged_in_user()
    all_ads = store.load_ads()
    idx = next((i for i, a in enumerate(all_ads) if a["id"] == aid), None)
    if idx is None:
        abort(404)
    ad = all_ads[idx]

    if request.method == "POST":
        data, errors = _ad_form_to_dict(request.form)
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/ad_form.html", ad=ad, form=request.form, active="ads")

        data["id"] = aid
        data["image"] = ad.get("image")
        data["created_at"] = ad.get("created_at")
        data["created_by"] = ad.get("created_by")
        data["updated_at"] = _now_iso()
        data["updated_by"] = actor["email"]

        file = request.files.get("image_file")
        if file and file.filename:
            image_value, upload_error = uploads.save_ad_image(file, aid, AD_IMG_DIR)
            if upload_error:
                flash(upload_error, "error")
            else:
                uploads.delete_stored(ad.get("image"))
                data["image"] = image_value
        elif request.form.get("remove_image"):
            uploads.delete_stored(ad.get("image"))
            data["image"] = None

        all_ads[idx] = data
        store.save_ads(all_ads)
        store.append_audit(actor["email"], "ad_updated", data["internal_name"])
        flash("Reklam güncellendi.", "success")
        return redirect(url_for("admin.ads_list"))

    return render_template("admin/ad_form.html", ad=ad, form=ad, active="ads")


@admin_bp.route("/reklam/ilanlar/<aid>/durum", methods=["POST"])
@master_admin_required
def ad_toggle_status(aid):
    actor = _resolve_logged_in_user()
    all_ads = store.load_ads()
    idx = next((i for i, a in enumerate(all_ads) if a["id"] == aid), None)
    if idx is None:
        abort(404)
    new_status = "active" if all_ads[idx].get("status") != "active" else "inactive"
    all_ads[idx]["status"] = new_status
    all_ads[idx]["updated_at"] = _now_iso()
    all_ads[idx]["updated_by"] = actor["email"]
    store.save_ads(all_ads)
    store.append_audit(actor["email"], "ad_status_changed", all_ads[idx]["internal_name"], {"status": new_status})
    flash("Reklam durumu güncellendi.", "success")
    return redirect(url_for("admin.ads_list"))


@admin_bp.route("/reklam/ilanlar/<aid>/sil", methods=["POST"])
@master_admin_required
def ad_delete(aid):
    actor = _resolve_logged_in_user()
    all_ads = store.load_ads()
    ad = next((a for a in all_ads if a["id"] == aid), None)
    if not ad:
        abort(404)
    uploads.delete_stored(ad.get("image"))
    all_ads = [a for a in all_ads if a["id"] != aid]
    store.save_ads(all_ads)
    remaining_placements = [p for p in store.load_placements() if p["ad_id"] != aid]
    store.save_placements(remaining_placements)
    store.append_audit(actor["email"], "ad_deleted", ad["internal_name"])
    flash("Reklam ve ona bağlı tüm yerleşimler silindi.", "success")
    return redirect(url_for("admin.ads_list"))


@admin_bp.route("/reklam/ilanlar/<aid>/onizle")
@master_admin_required
def ad_preview(aid):
    ad = store.get_ad(aid)
    if not ad:
        abort(404)
    return render_template("admin/ad_preview.html", ad=ad, ad_html=ads.render_ad_html(ad),
                            status=ads.ad_effective_status(ad), status_labels=ads.STATUS_LABELS, active="ads")


@admin_bp.route("/reklam/yerlesimler")
@master_admin_required
def placements_list():
    ad_filter = request.args.get("ad_id") or None
    placements = store.load_placements()
    if ad_filter:
        placements = [p for p in placements if p["ad_id"] == ad_filter]
    placements.sort(key=lambda p: p.get("created_at") or "", reverse=True)

    ads_by_id = {a["id"]: a for a in store.load_ads()}
    rows = []
    for p in placements:
        ad = ads_by_id.get(p["ad_id"])
        target_label = "Site geneli"
        if p["scope"] == "section":
            target_label = ads.CONTEXT_LABELS.get(p.get("section"), p.get("section"))
        elif p["scope"] == "content":
            article = store.get_article_by_id(p.get("content_id"))
            target_label = article["title"] if article else "(silinmiş makale)"
        rows.append({
            "placement": p,
            "ad": ad,
            "slot_label": ads.SLOTS.get(p["slot"], {}).get("label", p["slot"]),
            "scope_label": ads.SCOPE_LABELS.get(p["scope"], p["scope"]),
            "target_label": target_label,
        })

    filtered_ad = ads_by_id.get(ad_filter) if ad_filter else None
    return render_template("admin/placements_list.html", rows=rows, filtered_ad=filtered_ad, active="ads")


@admin_bp.route("/reklam/yerlesimler/yeni", methods=["GET", "POST"])
@master_admin_required
def placement_new():
    actor = _resolve_logged_in_user()
    all_ads = sorted(store.load_ads(), key=lambda a: a["internal_name"].lower())
    slot_choices = ads.slot_choices()
    articles = store.all_articles_sorted()

    if request.method == "POST":
        ad_id = request.form.get("ad_id", "")
        slot = request.form.get("slot", "")
        scope = request.form.get("scope", "")
        section = request.form.get("section") or None
        content_id = request.form.get("content_id") or None

        errors = []
        if not store.get_ad(ad_id):
            errors.append("Geçerli bir reklam seçin.")
        if slot not in ads.SLOTS:
            errors.append("Geçerli bir slot seçin.")
        if scope not in ("global", "section", "content"):
            errors.append("Geçerli bir kapsam seçin.")
        elif scope == "section":
            if section not in ads.valid_section_contexts_for_slot(slot):
                errors.append("Seçilen bölüm bu slot için geçerli değil.")
        elif scope == "content":
            if not ads.slot_supports_content_scope(slot):
                errors.append("Bu slot belirli bir sayfaya özel yerleşimi desteklemiyor.")
            elif not store.get_article_by_id(content_id):
                errors.append("Geçerli bir makale seçin.")

        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("admin/placement_form.html", placement=None, form=request.form,
                                    all_ads=all_ads, slot_choices=slot_choices, articles=articles,
                                    surface_labels=ads.SURFACE_LABELS, context_labels=ads.CONTEXT_LABELS,
                                    active="ads")

        now = _now_iso()
        existing = store.find_placement(
            slot, scope, section=section, content_type="article" if scope == "content" else None,
            content_id=content_id if scope == "content" else None,
        )
        placements = store.load_placements()
        record = {
            "id": existing["id"] if existing else ads.new_placement_id(),
            "ad_id": ad_id,
            "slot": slot,
            "scope": scope,
            "section": section if scope == "section" else None,
            "content_type": "article" if scope == "content" else None,
            "content_id": content_id if scope == "content" else None,
            "created_at": existing["created_at"] if existing else now,
            "updated_at": now,
            "created_by": existing["created_by"] if existing else actor["email"],
        }
        if existing:
            placements = [record if p["id"] == existing["id"] else p for p in placements]
            store.append_audit(actor["email"], "ad_placement_replaced", store.get_ad(ad_id)["internal_name"])
            flash("Bu konumda zaten bir reklam vardı — yeni reklamla değiştirildi.", "success")
        else:
            placements.append(record)
            store.append_audit(actor["email"], "ad_placement_created", store.get_ad(ad_id)["internal_name"])
            flash("Yerleşim oluşturuldu.", "success")
        store.save_placements(placements)
        return redirect(url_for("admin.placements_list"))

    return render_template("admin/placement_form.html", placement=None, form={}, all_ads=all_ads,
                            slot_choices=slot_choices, articles=articles,
                            surface_labels=ads.SURFACE_LABELS, context_labels=ads.CONTEXT_LABELS, active="ads")


@admin_bp.route("/reklam/yerlesimler/<pid>/sil", methods=["POST"])
@master_admin_required
def placement_delete(pid):
    actor = _resolve_logged_in_user()
    placement = store.get_placement(pid)
    if not placement:
        abort(404)
    placements = [p for p in store.load_placements() if p["id"] != pid]
    store.save_placements(placements)
    ad = store.get_ad(placement["ad_id"])
    store.append_audit(actor["email"], "ad_placement_deleted", ad["internal_name"] if ad else placement["ad_id"])
    flash("Yerleşim kaldırıldı.", "success")
    return redirect(url_for("admin.placements_list"))


app.register_blueprint(admin_bp)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5050))
    app.run(host="127.0.0.1", port=port, debug=True)
