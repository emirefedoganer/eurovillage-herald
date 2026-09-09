import json
import os
import re
import unicodedata
import uuid

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
ADS_PATH = os.path.join(DATA_DIR, "ads.json")
AD_PLACEMENTS_PATH = os.path.join(DATA_DIR, "ad_placements.json")
MANAGEMENT_PATH = os.path.join(DATA_DIR, "newspaper_management.json")
ISSUE_SUBSCRIPTIONS_PATH = os.path.join(DATA_DIR, "issue_subscriptions.json")
ISSUE_ANALYTICS_PATH = os.path.join(DATA_DIR, "issue_analytics.json")
EDITORIAL_DRAFTS_PATH = os.path.join(DATA_DIR, "editorial_drafts.json")

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
    """Loads articles, self-healing two things on the way in:

    1. A missing `id` -- articles historically only had `slug` as an
       identifier, but slugs are mutable (editing an article's title
       regenerates it). Ad placements and preview links need a stable
       reference that survives a slug change.
    2. A missing `status` -- defaults every pre-existing article to
       "published" (their only meaningful prior state), and flips any
       "scheduled" article whose publish_at has passed to "published".

    That second point is the entire restart-safety story for scheduled
    publishing: there is no timer, in-memory or otherwise. Every single
    read re-evaluates "is publish_at due yet?" against the wall clock and
    persists the flip the moment it's true, so a Railway restart at 08:55
    for an article scheduled at 09:00 changes nothing -- the very next
    request after 09:00 (from anyone, on any route that loads articles)
    normalizes it. This runs on every load but only ever writes when
    something actually changed, so it's a no-op scan the rest of the time.
    """
    articles = _load(ARTICLES_PATH, [])
    changed = False
    now = _utcnow()
    for a in articles:
        if not a.get("id"):
            a["id"] = uuid.uuid4().hex[:10]
            changed = True
        if not a.get("status"):
            a["status"] = "published"
            changed = True
        if a.get("status") == "scheduled":
            publish_at = _parse_iso(a.get("publish_at"))
            if publish_at and publish_at <= now:
                a["status"] = "published"
                changed = True
    if changed:
        _save(ARTICLES_PATH, articles)
    return articles


def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)


def _parse_iso(value):
    from datetime import datetime
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def is_article_public(article, now=None):
    """The single place that decides whether an article is visible to an
    ordinary visitor right now. "published" always is; "scheduled" is only
    once its publish_at has passed (in practice load_articles() already
    normalizes a due "scheduled" article to "published" on every read, so
    this mainly matters for the brief window on the same read where the
    normalization loop and a filter loop haven't both run yet -- see
    public_articles()). "draft" never is."""
    status = article.get("status", "published")
    if status == "published":
        return True
    if status == "scheduled":
        publish_at = _parse_iso(article.get("publish_at"))
        return bool(publish_at and publish_at <= (now or _utcnow()))
    return False


def public_articles(articles):
    """Filters an already-loaded article list down to what a visitor may
    see. Callers on the public side pass their list through this; admin
    callers don't, so editors keep seeing drafts/scheduled items to manage."""
    now = _utcnow()
    return [a for a in articles if is_article_public(a, now)]


def save_articles(articles):
    _save(ARTICLES_PATH, articles)


# --------------------------------------------------------------- issues --
# Newspaper issues, extended from a bare PDF record into a structured
# publishing entity -- same self-healing/restart-safe pattern already used
# for articles (see load_articles() above): every existing field
# (id/no/title/date/description/cover_image/pdf/pages) is left exactly as
# it was, new fields are only ever ADDED with a safe default, and a
# "scheduled" issue whose publish_at has passed flips to "published" on
# every single read -- no timer, no background job, no in-process
# scheduler that wouldn't survive a Railway restart.

ISSUE_STATUSES = ["draft", "in_review", "ready", "scheduled", "published", "archived"]
# "archived" is deliberately public, unlike games' "archived" (which hides
# the item entirely): the newspaper archive page's whole purpose is being
# a permanent public archive of past issues, so retiring an issue from
# "current" doesn't mean hiding it -- there's no real-world newspaper
# where last year's issue vanishes from the archive.
ISSUE_PUBLIC_STATUSES = {"published", "archived"}
ISSUE_TYPES = ["regular", "special", "election_special"]


def _iso_from_date(date_str):
    """Best-effort 'YYYY-MM-DD' -> a UTC-midnight ISO timestamp, used only
    to backfill created_at/published_at for issues that predate those
    fields (so they get a real, sortable timestamp instead of None)."""
    if not date_str:
        return None
    from datetime import datetime, timezone
    try:
        dt = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def load_issues():
    issues = _load(ISSUES_PATH, [])
    changed = False
    now = _utcnow()
    for i in issues:
        just_flipped = False
        if not i.get("slug"):
            # The existing `id` is already unique and URL-safe (e.g.
            # "sayi-01-secim-ozel") -- reusing it as the slug means every
            # existing /gazete/<id> link keeps working unchanged.
            i["slug"] = i["id"]
            changed = True
        if not i.get("status"):
            i["status"] = "published"  # every pre-existing issue was already public
            changed = True
        if not i.get("issue_type"):
            i["issue_type"] = "regular"
            changed = True
        if "editor_note" not in i:
            i["editor_note"] = ""  # internal-only, never rendered publicly
            changed = True
        if "publish_at" not in i:
            i["publish_at"] = None
            changed = True
        if not i.get("created_at"):
            i["created_at"] = _iso_from_date(i.get("date")) or _audit_timestamp()
            changed = True
        if not i.get("updated_at"):
            i["updated_at"] = i["created_at"]
            changed = True
        if not i.get("published_at") and i.get("status") in ISSUE_PUBLIC_STATUSES:
            i["published_at"] = _iso_from_date(i.get("date")) or i["created_at"]
            changed = True
        for key in ("preview_token_hash", "preview_token_expires_at", "preview_token_created_at"):
            if key not in i:
                i[key] = None
                changed = True
        if "file_size" not in i:
            # Populated at upload time going forward (see uploads.py); not
            # worth a live R2 HEAD request just to backfill this for
            # pre-existing issues -- that would put network I/O in a read
            # path that runs on every page load.
            i["file_size"] = None
            changed = True
        if i.get("status") == "scheduled":
            publish_at = _parse_iso(i.get("publish_at"))
            if publish_at and publish_at <= now:
                i["status"] = "published"
                i["published_at"] = _audit_timestamp()
                just_flipped = True
                changed = True
        if "announcement_sent_at" not in i:
            # A pre-existing issue that was already published/archived
            # before this field existed must NOT retroactively fire a
            # "new issue" notification/draft the first time this code
            # runs -- so it's immediately marked as already-handled. An
            # issue that just auto-flipped from scheduled THIS pass is
            # left None instead, so app.py's publish-hook (which treats
            # "published with announcement_sent_at still None" as "fire
            # the hook, then stamp this field") picks it up on the very
            # next relevant request.
            if i.get("status") in ISSUE_PUBLIC_STATUSES and not just_flipped:
                i["announcement_sent_at"] = i.get("published_at") or i.get("created_at")
            else:
                i["announcement_sent_at"] = None
            changed = True
    if changed:
        _save(ISSUES_PATH, issues)
    return issues


def save_issues(issues):
    _save(ISSUES_PATH, issues)


def is_issue_public(issue, now=None):
    """The single place that decides whether an issue is visible to an
    ordinary visitor right now -- mirrors is_article_public()."""
    status = issue.get("status", "published")
    if status in ISSUE_PUBLIC_STATUSES:
        return True
    if status == "scheduled":
        publish_at = _parse_iso(issue.get("publish_at"))
        return bool(publish_at and publish_at <= (now or _utcnow()))
    return False


def public_issues(issues):
    now = _utcnow()
    return [i for i in issues if is_issue_public(i, now)]


def all_issues_sorted(published_only=False):
    items = load_issues()
    if published_only:
        items = public_issues(items)
    items.sort(key=lambda i: i.get("date", ""), reverse=True)
    return items


def get_issue_by_preview_token(token):
    """Mirrors get_article_by_preview_token() exactly -- same hash
    function, same expiry semantics, same "missing/wrong/expired/revoked
    are indistinguishable" behavior. Ignores publish status by design:
    previewing a draft/scheduled issue by its token is the entire point."""
    if not token:
        return None
    token_hash = hash_preview_token(token)
    now = _utcnow()
    for i in load_issues():
        if i.get("preview_token_hash") == token_hash:
            expires_at = _parse_iso(i.get("preview_token_expires_at"))
            if expires_at and expires_at <= now:
                return None
            return i
    return None


def load_site():
    return _load(SITE_PATH, {})


def save_site(site):
    _save(SITE_PATH, site)


def get_article(slug, published_only=False):
    for a in load_articles():
        if a["slug"] == slug:
            if published_only and not is_article_public(a):
                return None
            return a
    return None


def get_article_by_id(article_id):
    """Looks an article up by its stable `id` rather than its (mutable)
    slug -- used by ad placements targeting a specific article, and by the
    Placements admin UI to display which article a placement points at.
    Deliberately ignores publish status: this is an internal lookup, never
    a public one (ad placements are configured by master_admin regardless
    of the target article's current status)."""
    if not article_id:
        return None
    for a in load_articles():
        if a.get("id") == article_id:
            return a
    return None


def get_article_by_preview_token(token):
    """Looks up the one article (if any) whose current preview token hash
    matches `token`, and only if that token hasn't expired. Deliberately
    ignores publish status -- previewing a draft/scheduled article by its
    token is the entire point. Returns None for a missing, wrong, expired,
    or revoked token, indistinguishable from each other to the caller (so
    the preview route can't be used to probe which case it is)."""
    if not token:
        return None
    token_hash = hash_preview_token(token)
    now = _utcnow()
    for a in load_articles():
        if a.get("preview_token_hash") == token_hash:
            expires_at = _parse_iso(a.get("preview_token_expires_at"))
            if expires_at and expires_at <= now:
                return None
            return a
    return None


def hash_preview_token(token):
    import hashlib
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def articles_by_section(section, exclude_slug=None, limit=None, published_only=False):
    items = [a for a in load_articles() if a["section"] == section and a["slug"] != exclude_slug]
    if published_only:
        items = public_articles(items)
    items.sort(key=lambda a: a["date"], reverse=True)
    if limit:
        items = items[:limit]
    return items


def all_articles_sorted(published_only=False):
    items = load_articles()
    if published_only:
        items = public_articles(items)
    items.sort(key=lambda a: a["date"], reverse=True)
    return items


def get_issue(issue_id, published_only=False):
    """Looks up by id OR slug -- in practice the same value for every
    issue today (see load_issues()'s self-heal), kept as two checks so a
    future issue with a slug that legitimately diverges from its id still
    resolves via either one."""
    for i in load_issues():
        if i["id"] == issue_id or i.get("slug") == issue_id:
            if published_only and not is_issue_public(i):
                return None
            return i
    return None


def load_messages():
    """Self-heals messages written before the tip/category/status extension
    -- gives every record a category, status, images list, and follow-up
    flag if it doesn't already have one, so an old plain contact message
    (or one already sitting in production before this deploy) still
    displays and behaves correctly rather than raising on a missing key."""
    messages = _load(MESSAGES_PATH, [])
    changed = False
    for m in messages:
        if "status" not in m:
            m["status"] = "new"
            changed = True
        if "category" not in m:
            m["category"] = m.get("subject", "Diğer")
            changed = True
        if "images" not in m:
            m["images"] = []
            changed = True
        if "follow_up_consent" not in m:
            m["follow_up_consent"] = False
            changed = True
        if "id" not in m:
            m["id"] = uuid.uuid4().hex[:10]
            changed = True
    if changed:
        _save(MESSAGES_PATH, messages)
    return messages


def save_messages(messages):
    _save(MESSAGES_PATH, messages)


def get_message(mid):
    return _by_id(load_messages(), mid)


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


def articles_by_author(author_id, exclude_slug=None, published_only=False):
    items = [
        a for a in load_articles()
        if author_id in (a.get("author_ids") or []) and a.get("slug") != exclude_slug
    ]
    if published_only:
        items = public_articles(items)
    items.sort(key=lambda a: a["date"], reverse=True)
    return items


def articles_by_issue(issue_id, published_only=False):
    """Powers 'Bu Sayıdan' on the issue reader page. Sorted by the
    article's newspaper page number where set (articles without one --
    the association is entirely optional -- sort after those that have
    one, then fall back to date)."""
    items = [a for a in load_articles() if a.get("issue_id") == issue_id]
    if published_only:
        items = public_articles(items)
    items.sort(key=lambda a: (a.get("issue_page") is None, a.get("issue_page") or 0, a.get("date", "")))
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


# -------------------------------------------------------------------- ads --
# Advertisements and their placements are kept in two separate files, same
# split as User/AuthorProfile: an Advertisement is "what to show" (creative,
# sponsor, schedule); a Placement is "where to show it" (slot + scope). A
# placement never embeds a copy of the ad -- it only references ad_id, so
# editing an ad's creative once updates it everywhere it's placed.

def load_ads():
    return _load(ADS_PATH, [])


def save_ads(ads):
    _save(ADS_PATH, ads)


def get_ad(ad_id):
    return _by_id(load_ads(), ad_id)


def load_placements():
    return _load(AD_PLACEMENTS_PATH, [])


def save_placements(placements):
    _save(AD_PLACEMENTS_PATH, placements)


def get_placement(placement_id):
    return _by_id(load_placements(), placement_id)


def placements_for_ad(ad_id):
    """Every placement currently using this ad -- powers the admin "where is
    this advertisement used" view."""
    return [p for p in load_placements() if p["ad_id"] == ad_id]


def find_placement(slot, scope, section=None, content_type=None, content_id=None):
    """Looks up an existing placement occupying the same (slot, scope,
    target) combination. Used to enforce "at most one placement per slot
    per target" -- saving a new placement for an already-occupied
    combination replaces it instead of creating a second, competing one."""
    for p in load_placements():
        if p["slot"] != slot or p["scope"] != scope:
            continue
        if scope == "section" and p.get("section") == section:
            return p
        if scope == "content" and p.get("content_type") == content_type and p.get("content_id") == content_id:
            return p
        if scope == "global":
            return p
    return None


# --------------------------------------------------- newspaper management --
# Gazete Yönetimi (the public masthead/leadership page) is a deliberately
# separate concept from the author system: being a writer, editor, or
# admin never automatically puts someone here -- a master_admin has to add
# them explicitly. An entry may optionally point at an existing author
# profile (linked_author_id) purely for display (photo/link reuse); it is
# never the other way around, and deleting an entry here never touches the
# author/user records it happens to reference.

def load_management():
    """Self-seeds the two senior entries that predate this structured model
    (previously free-text in site.json's `leadership`) the first time this
    file doesn't exist yet, so a fresh deploy of this feature never starts
    with an empty masthead or silently drops them."""
    if not os.path.exists(MANAGEMENT_PATH):
        seeded = _seed_management_entries()
        _save(MANAGEMENT_PATH, seeded)
        return seeded
    return _load(MANAGEMENT_PATH, [])


def _seed_management_entries():
    site = load_site()
    legacy_by_name = {p["name"]: p for p in (site.get("leadership") or [])}
    authors_by_name = {a["display_name"]: a for a in load_authors()}
    now = _audit_timestamp()
    seeds = [("Emir the Composer", "Genel Yayın Yönetmeni"), ("Duke of Akbadain", "Okur İlişkileri")]
    entries = []
    for order, (name, role_title) in enumerate(seeds, start=1):
        author = authors_by_name.get(name)
        entries.append({
            "id": uuid.uuid4().hex[:10],
            "linked_author_id": author["id"] if author else None,
            "linked_user_id": author.get("user_id") if author else None,
            "display_name": name,
            "role_title": role_title,
            "bio": legacy_by_name.get(name, {}).get("bio", ""),
            "display_order": order,
            "active": True,
            "image_override": None,
            "created_at": now, "updated_at": now,
            "created_by": "system", "updated_by": "system",
        })
    return entries


def save_management(entries):
    _save(MANAGEMENT_PATH, entries)


def get_management_entry(entry_id):
    return _by_id(load_management(), entry_id)


def active_management_entries():
    return sorted(
        [e for e in load_management() if e.get("active")],
        key=lambda e: e.get("display_order", 0),
    )


# --------------------------------------------------------- issue subscriptions --
# "Let me know when a new issue is published." Double opt-in: a new
# signup is "pending" until the confirm link is clicked, and only
# "confirmed" subscribers are ever notified. Two separate tokens per
# record -- confirm (short-lived, single use) and unsubscribe (long-lived,
# unrelated to whether the subscription is even confirmed yet) -- neither
# is ever stored in plaintext, same hash-only pattern as article/issue
# preview tokens (see hash_preview_token()).

def load_subscriptions():
    return _load(ISSUE_SUBSCRIPTIONS_PATH, [])


def save_subscriptions(subs):
    _save(ISSUE_SUBSCRIPTIONS_PATH, subs)


def get_subscription_by_email(email):
    if not email:
        return None
    email = email.strip().lower()
    for s in load_subscriptions():
        if s["email"].strip().lower() == email:
            return s
    return None


def get_subscription_by_id(sub_id):
    return _by_id(load_subscriptions(), sub_id)


def get_subscription_by_confirm_token(token):
    if not token:
        return None
    token_hash = hash_preview_token(token)
    now = _utcnow()
    for s in load_subscriptions():
        if s.get("confirm_token_hash") == token_hash:
            expires_at = _parse_iso(s.get("confirm_token_expires_at"))
            if expires_at and expires_at <= now:
                return None
            return s
    return None


def confirmed_subscribers(preference_key):
    """Confirmed, still-subscribed emails opted into `preference_key`
    (e.g. 'new_issue'). Used by the publish-notification hook -- never
    returns anything for a pending or unsubscribed record."""
    return [
        s for s in load_subscriptions()
        if s.get("status") == "confirmed" and (s.get("preferences") or {}).get(preference_key)
    ]


# ------------------------------------------------------------ issue analytics --
# Aggregate-only, privacy-conscious counters per issue -- see app/analytics.py
# for the recording/derived-stats logic. Deliberately NOT a raw per-event
# log (which would grow unbounded in a flat JSON file with no real
# forensic value for a small newsroom); every event updates one compact
# per-issue counter record instead.

def load_issue_analytics():
    return _load(ISSUE_ANALYTICS_PATH, {})


def save_issue_analytics(data):
    _save(ISSUE_ANALYTICS_PATH, data)


# --------------------------------------------------------------- editorial drafts --
# AI/automation-prepared drafts (issue announcements, newsletter intros,
# social copy, ...). See app/drafts.py for generation. These are ALWAYS
# created with status "draft" and require an explicit human "Yayımla"
# action in the admin panel -- nothing in this module or drafts.py ever
# flips a draft to "published" on its own.

def load_drafts():
    return _load(EDITORIAL_DRAFTS_PATH, [])


def save_drafts(drafts):
    _save(EDITORIAL_DRAFTS_PATH, drafts)


def get_draft(draft_id):
    return _by_id(load_drafts(), draft_id)


def draft_exists(dedup_key):
    """True if a draft with this dedup_key was already generated --
    dedup_key is normally f'{kind}:{issue_id}', so re-running the
    publish-hook logic (e.g. after a redeploy retries a half-finished
    request) never creates a second copy of the same announcement draft."""
    return any(d.get("dedup_key") == dedup_key for d in load_drafts())
