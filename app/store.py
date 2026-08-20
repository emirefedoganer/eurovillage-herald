import json
import os
import re
import unicodedata

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")

ARTICLES_PATH = os.path.join(DATA_DIR, "articles.json")
ISSUES_PATH = os.path.join(DATA_DIR, "issues.json")
SITE_PATH = os.path.join(DATA_DIR, "site.json")
MESSAGES_PATH = os.path.join(DATA_DIR, "messages.json")
CROSSWORDS_PATH = os.path.join(DATA_DIR, "crosswords.json")
SUDOKUS_PATH = os.path.join(DATA_DIR, "sudokus.json")
USERS_PATH = os.path.join(DATA_DIR, "users.json")
AUTHORS_PATH = os.path.join(DATA_DIR, "authors.json")
AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit_log.json")
ROLES_PATH = os.path.join(DATA_DIR, "roles.json")

TR_MAP = str.maketrans({
    "ç": "c", "Ç": "c", "ğ": "g", "Ğ": "g", "ı": "i", "I": "i",
    "İ": "i", "ö": "o", "Ö": "o", "ş": "s", "Ş": "s", "ü": "u", "Ü": "u",
})


def slugify(text):
    text = text.translate(TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text or "makale"


def _load(path, default):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save(path, data):
    tmp_path = path + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def load_articles():
    return _load(ARTICLES_PATH, [])


def save_articles(articles):
    _save(ARTICLES_PATH, articles)


def load_issues():
    return _load(ISSUES_PATH, [])


def save_issues(issues):
    _save(ISSUES_PATH, issues)


def load_site():
    return _load(SITE_PATH, {})


def save_site(site):
    _save(SITE_PATH, site)


def get_article(slug):
    for a in load_articles():
        if a["slug"] == slug:
            return a
    return None


def unique_slug(title, existing_slugs, current_slug=None):
    base = slugify(title)
    slug = base
    n = 2
    while slug in existing_slugs and slug != current_slug:
        slug = f"{base}-{n}"
        n += 1
    return slug


def article_pullquote(article):
    """The article's first pullquote block, if it has one."""
    for block in article.get("body") or []:
        if block.get("type") == "pullquote" and block.get("text"):
            return block
    return None


def article_lead_quote(article):
    """The opening sentence of the article's first paragraph -- used to
    surface a representative 'main quote' in previews (e.g. the homepage
    Opinion rail) without duplicating or authoring text separately; it
    stays sourced directly from the article body."""
    for block in article.get("body") or []:
        if block.get("type") == "p" and block.get("text"):
            text = block["text"].strip()
            match = re.search(r"^.*?[.!?](?=\s|$)", text)
            sentence = match.group(0).strip() if match else text
            return {"text": sentence, "attribution": None}
    return None


def articles_by_section(section, exclude_slug=None, limit=None):
    items = [a for a in load_articles() if a["section"] == section and a["slug"] != exclude_slug]
    items.sort(key=lambda a: a["date"], reverse=True)
    if limit:
        items = items[:limit]
    return items


def all_articles_sorted():
    items = load_articles()
    items.sort(key=lambda a: a["date"], reverse=True)
    return items


def get_issue(issue_id):
    for i in load_issues():
        if i["id"] == issue_id:
            return i
    return None


def load_messages():
    return _load(MESSAGES_PATH, [])


def save_messages(messages):
    _save(MESSAGES_PATH, messages)


def all_messages_sorted():
    items = load_messages()
    items.sort(key=lambda m: m["date"], reverse=True)
    return items


# ------------------------------------------------------------ games: base --

def _sort_games(items):
    items.sort(key=lambda g: (g.get("publication_date") or "", g.get("created_at") or ""), reverse=True)
    return items


def _by_id(items, gid):
    for it in items:
        if it["id"] == gid:
            return it
    return None


def _by_slug(items, slug):
    for it in items:
        if it["slug"] == slug:
            return it
    return None


# ------------------------------------------------------------- crosswords --

def load_crosswords():
    return _load(CROSSWORDS_PATH, [])


def save_crosswords(items):
    _save(CROSSWORDS_PATH, items)


def all_crosswords_sorted():
    return _sort_games(load_crosswords())


def published_crosswords():
    return _sort_games([c for c in load_crosswords() if c.get("status") == "published"])


def get_crossword(cid):
    return _by_id(load_crosswords(), cid)


def get_crossword_by_slug(slug, published_only=False):
    items = load_crosswords()
    c = _by_slug(items, slug)
    if c and published_only and c.get("status") != "published":
        return None
    return c


# ----------------------------------------------------------------- sudoku --

def load_sudokus():
    return _load(SUDOKUS_PATH, [])


def save_sudokus(items):
    _save(SUDOKUS_PATH, items)


def all_sudokus_sorted():
    return _sort_games(load_sudokus())


def published_sudokus():
    return _sort_games([s for s in load_sudokus() if s.get("status") == "published"])


def get_sudoku(sid):
    return _by_id(load_sudokus(), sid)


def get_sudoku_by_slug(slug, published_only=False):
    items = load_sudokus()
    s = _by_slug(items, slug)
    if s and published_only and s.get("status") != "published":
        return None
    return s


# ------------------------------------------------------------------ users --
# Authentication accounts. Kept logically separate from AuthorProfile (below):
# a User is "who can log in and with what permissions"; an AuthorProfile is
# "the public editorial identity". Most authors have exactly one of each,
# linked by user_id, but the two are never merged into a single record so
# that account security concerns (password, role, status) can't leak into
# the publicly-editable profile surface and vice versa.

def load_users():
    return _load(USERS_PATH, [])


def save_users(users):
    _save(USERS_PATH, users)


def get_user(uid):
    return _by_id(load_users(), uid)


def get_user_by_email(email):
    if not email:
        return None
    email = email.strip().lower()
    for u in load_users():
        if u["email"].strip().lower() == email:
            return u
    return None


def count_active_master_admins(exclude_id=None):
    return sum(
        1 for u in load_users()
        if u["account_role"] == "master_admin" and u["status"] == "active" and u["id"] != exclude_id
    )


# --------------------------------------------------------- author profiles --

def load_authors():
    return _load(AUTHORS_PATH, [])


def save_authors(authors):
    _save(AUTHORS_PATH, authors)


def get_author(aid):
    return _by_id(load_authors(), aid)


def get_author_by_user_id(uid):
    for a in load_authors():
        if a.get("user_id") == uid:
            return a
    return None


def get_author_by_slug(slug):
    """Looks up the current slug first, then falls back to any author's
    slug_history so an old profile URL keeps resolving (redirected) after a
    slug change instead of 404ing. Returns (author, is_redirect)."""
    authors = load_authors()
    a = _by_slug(authors, slug)
    if a:
        return a, False
    for a in authors:
        if slug in (a.get("slug_history") or []):
            return a, True
    return None, False


def active_authors():
    return [a for a in load_authors() if a.get("status") == "active"]


def unique_author_slug(name, existing_slugs, current_slug=None):
    return unique_slug(name, existing_slugs, current_slug=current_slug)


# ---------------------------------------------------------------- roles --
# Two system roles (master_admin, author) always exist and aren't editable
# here -- their behavior is load-bearing elsewhere (master-admin lockout
# protection, the default role for new authors). Anything else is a custom
# role a Master Admin created, with an explicit, storable set of granted
# permissions.

def load_roles():
    return _load(ROLES_PATH, [])


def save_roles(roles):
    _save(ROLES_PATH, roles)


def get_role(role_id):
    for r in load_roles():
        if r["id"] == role_id:
            return r
    return None


def unique_role_id(name, existing_ids):
    base = slugify(name)
    rid = base
    n = 2
    while rid in existing_ids:
        rid = f"{base}-{n}"
        n += 1
    return rid


def author_preview(author):
    """Compact, embed-once-per-page payload for hover cards -- deliberately
    excludes anything not meant for a lightweight, repeatedly-rendered
    preview (no full bio, no article lists)."""
    if not author:
        return None
    return {
        "id": author["id"],
        "slug": author["slug"],
        "display_name": author["display_name"],
        "editorial_role": author.get("editorial_role") or None,
        "profile_image": author.get("profile_image") or None,
        "short_bio": author.get("short_bio") or None,
        "twitter_handle": author.get("twitter_handle") or None,
        "minecraft_username": author.get("minecraft_username") or None,
        "is_opinion_columnist": bool(author.get("is_opinion_columnist")),
    }


def resolve_article_authors(article):
    """Returns the list of full AuthorProfile dicts for an article, in order.
    Falls back to a synthetic, non-linked author (using the legacy plain-text
    `author` field) for older content that predates the author system, so
    nothing breaks mid-migration."""
    ids = article.get("author_ids") or []
    authors = []
    for aid in ids:
        a = get_author(aid)
        if a:
            authors.append(a)
    if authors:
        return authors
    legacy_name = article.get("author")
    if legacy_name:
        return [{
            "id": None, "slug": None, "display_name": legacy_name,
            "editorial_role": article.get("byline_title"), "profile_image": None,
            "short_bio": None, "twitter_handle": None, "minecraft_username": None,
            "is_opinion_columnist": False,
        }]
    return []


def articles_by_author(author_id, exclude_slug=None):
    items = [
        a for a in load_articles()
        if author_id in (a.get("author_ids") or []) and a.get("slug") != exclude_slug
    ]
    items.sort(key=lambda a: a["date"], reverse=True)
    return items


# --------------------------------------------------------------- audit log --

def load_audit_log():
    return _load(AUDIT_LOG_PATH, [])


def append_audit(actor_label, action, target_label, meta=None):
    """actor_label / target_label are human-readable strings (e.g. an email
    or display name) captured at the time of the action, not live references
    -- so the log stays meaningful even if the target is later deleted."""
    log = load_audit_log()
    log.append({
        "actor": actor_label,
        "action": action,
        "target": target_label,
        "meta": meta or {},
        "at": _audit_timestamp(),
    })
    log = log[-500:]  # cap growth
    _save(AUDIT_LOG_PATH, log)


def recent_audit_log(limit=100):
    log = load_audit_log()
    return list(reversed(log))[:limit]


def _audit_timestamp():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
