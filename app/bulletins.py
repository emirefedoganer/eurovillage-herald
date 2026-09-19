"""Editorial bulletin/newsletter CMS.

A bulletin is always created as a DRAFT and never sends itself --
send_campaign() is the only function that actually enqueues real
subscriber mail, and every route that calls it requires an explicit,
separate confirmation step (see app.py). Saving/updating a draft never
sends anything.

Kinds: new_issue, weekly_digest, popular_stories, editorial_selection,
breaking_news, ari_magazin, custom -- all share the same record shape
(a subject/preheader, an optional intro, an ordered list of article
slugs with an optional lead, and a target subscriber preference). The
NEW_ISSUE kind is the one exception that can be created AND sent
automatically (see create_new_issue_bulletin_if_needed(), called from
app.py's issue-publish hook) -- every other kind is entirely
editor-initiated through the admin UI.

Article/issue content is resolved fresh at render time from the slugs
stored on the bulletin, never baked in at creation time -- if an editor
fixes a typo in an article's dek between drafting and sending, the
bulletin reflects it.

"Most read" suggestions (see app/analytics.py:top_article_slugs) are
exactly that -- suggestions. Nothing here ever auto-sends based on
analytics; an editor always explicitly selects/reorders/excludes before
any send, including for POPULAR_STORIES bulletins.
"""
import uuid
from datetime import datetime, timezone

import mailer
import store

BULLETIN_KINDS = [
    ("new_issue", "Yeni Sayı"),
    ("weekly_digest", "Haftalık Özet"),
    ("popular_stories", "Çok Okunan Haberler"),
    ("editorial_selection", "Editörün Seçtikleri"),
    ("breaking_news", "Son Dakika"),
    ("ari_magazin", "Arı Magazin"),
    ("custom", "Özel"),
]
BULLETIN_KIND_LABELS = dict(BULLETIN_KINDS)

# ---------------------------------------------------------------------
# The ONE mapping between a bulletin type and the subscriber audience it
# may target. The audiences are the EXISTING subscription preference
# categories (subscriptions.PREFERENCE_CHOICES) -- no second audience
# system. `allowed` has one entry for every type except "custom", which is
# the only type that genuinely supports free targeting.
#
# editorial_selection ("Editörün Seçtikleri") has no preference category of
# its own and this project deliberately does not invent one; it is a
# curated round-up, so it goes to the weekly-digest audience.
KIND_AUDIENCE = {
    "new_issue": {"default": "new_issue", "allowed": ("new_issue",)},
    "weekly_digest": {"default": "weekly_digest", "allowed": ("weekly_digest",)},
    "popular_stories": {"default": "popular_stories", "allowed": ("popular_stories",)},
    "editorial_selection": {"default": "weekly_digest", "allowed": ("weekly_digest",)},
    "breaking_news": {"default": "breaking_news", "allowed": ("breaking_news",)},
    "ari_magazin": {"default": "ari_magazin", "allowed": ("ari_magazin",)},
    "custom": {"default": "new_issue",
               "allowed": ("new_issue", "weekly_digest", "popular_stories", "breaking_news", "ari_magazin")},
}


class BulletinAudienceError(ValueError):
    pass


def audience_map():
    """JSON-serialisable copy of KIND_AUDIENCE for the form's JavaScript."""
    return {k: {"default": v["default"], "allowed": list(v["allowed"]), "custom": len(v["allowed"]) > 1}
            for k, v in KIND_AUDIENCE.items()}


def default_audience(kind):
    return KIND_AUDIENCE.get(kind, KIND_AUDIENCE["custom"])["default"]


def resolve_audience(kind, requested=None):
    """The audience to store for `kind`. A blank request means "the type's
    default"; a request outside the type's allowed set is REJECTED, never
    silently kept or rewritten."""
    spec = KIND_AUDIENCE.get(kind)
    if spec is None:
        raise BulletinAudienceError(f"Bilinmeyen bülten türü: {kind}")
    if not requested:
        return spec["default"]
    if requested not in spec["allowed"]:
        raise BulletinAudienceError(
            f"'{BULLETIN_KIND_LABELS.get(kind, kind)}' türü için bu hedef kitle uygun değil.")
    return requested


def is_compatible(kind, audience):
    return audience in KIND_AUDIENCE.get(kind, {}).get("allowed", ())


POPULAR_MIN_ITEMS = 3
POPULAR_LIMIT = 10


def popular_ranking(limit=POPULAR_LIMIT):
    """Suggested articles for a "Çok Okunan Haberler" bulletin.

    Metric: first-party article page views (analytics.article_view_totals),
    summed over the last analytics.POPULAR_PERIOD_DAYS (7) UTC calendar days
    including today. Only articles a visitor can open right now (published,
    not scheduled for later, not removed -- store.public_articles) are
    ranked; unknown/removed slugs are dropped and each article appears once.
    Order: views descending, then slug A-Z (deterministic). When fewer than
    POPULAR_MIN_ITEMS articles have views, a warning is returned and the
    ranking is NOT padded -- nothing is invented. Audience is decided by
    subscription preferences alone; analytics never infers interests."""
    import analytics
    from datetime import datetime, timezone
    totals = analytics.article_view_totals()
    public = {a["slug"]: a for a in store.public_articles(store.load_articles())}
    seen_ids, items = set(), []
    for slug, views in sorted(totals.items(), key=lambda kv: (-kv[1], kv[0])):
        art = public.get(slug)
        if not art or art.get("id") in seen_ids:
            continue
        seen_ids.add(art.get("id"))
        items.append({"slug": slug, "title": art.get("title", slug), "section": art.get("section", ""), "views": views})
        if len(items) >= limit:
            break
    warning = None
    if len(items) < POPULAR_MIN_ITEMS:
        warning = (f"Yeterli okunma verisi yok (son {analytics.POPULAR_PERIOD_DAYS} günde yalnızca "
                   f"{len(items)} yayımlanmış haber görüntülenmiş). Sıralama uydurulmadı; haberleri elle seçin.")
    return {"items": items, "period_days": analytics.POPULAR_PERIOD_DAYS,
            "metric": "Haber sayfası görüntülenme sayısı (ilk taraf analitik)",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "warning": warning}


def snapshot_from_ranking(ranking, slugs):
    """What is stored on the draft: the approved counts as they were when
    the editor pulled them, so later analytics never change a saved/
    scheduled bulletin."""
    counts = {i["slug"]: i["views"] for i in ranking["items"]}
    return {"metric": ranking["metric"], "period_days": ranking["period_days"],
            "generated_at": ranking["generated_at"], "counts": {s: counts[s] for s in slugs if s in counts}}


BULLETIN_STATUSES = ["draft", "scheduled", "sent", "cancelled"]


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_draft(kind, actor_email, internal_title, subject, preheader="", target_preference=None,
                  issue_id=None, intro_text="", lead_article_slug=None, article_slugs=None):
    if kind not in BULLETIN_KIND_LABELS:
        kind = "custom"
    target_preference = resolve_audience(kind, target_preference)   # raises BulletinAudienceError
    bulletin = {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "internal_title": internal_title or BULLETIN_KIND_LABELS[kind],
        "subject": subject or internal_title or BULLETIN_KIND_LABELS[kind],
        "preheader": preheader or "",
        "target_preference": target_preference,
        "issue_id": issue_id,
        "intro_text": intro_text or "",
        "lead_article_slug": lead_article_slug,
        "article_slugs": list(article_slugs or []),
        "status": "draft",
        "scheduled_at": None,
        "sent_at": None,
        "cancelled_at": None,
        "recipients_total": 0,
        "created_by": actor_email,
        "created_at": _now_iso(),
        "updated_at": _now_iso(),
        # source_issue_id (distinct from issue_id, which an editor could
        # theoretically change on a custom bulletin) is what
        # create_new_issue_bulletin_if_needed()'s dedup check uses --
        # never cleared, never edited.
        "source_issue_id": issue_id if kind == "new_issue" else None,
        "analytics_snapshot": None,
    }
    bulletins = store.load_bulletins()
    bulletins.append(bulletin)
    store.save_bulletins(bulletins)
    return bulletin


def update_draft(bulletin_id, **fields):
    """Only a draft or a not-yet-fired scheduled bulletin can be edited --
    a sent/cancelled one is a historical record."""
    bulletins = store.load_bulletins()
    idx = next((i for i, b in enumerate(bulletins) if b["id"] == bulletin_id), None)
    if idx is None or bulletins[idx]["status"] not in ("draft", "scheduled"):
        return None
    new_kind = fields.get("kind", bulletins[idx]["kind"])
    if new_kind not in BULLETIN_KIND_LABELS:
        raise BulletinAudienceError(f"Bilinmeyen bülten türü: {new_kind}")
    # The audience is always re-resolved against the (possibly new) type.
    fields["target_preference"] = resolve_audience(new_kind, fields.get("target_preference"))
    allowed = {
        "kind", "analytics_snapshot", "internal_title", "subject", "preheader", "target_preference", "issue_id",
        "intro_text", "lead_article_slug", "article_slugs", "scheduled_at",
    }
    for key, value in fields.items():
        if key in allowed:
            bulletins[idx][key] = value
    bulletins[idx]["updated_at"] = _now_iso()
    if "scheduled_at" in fields:
        bulletins[idx]["status"] = "scheduled" if fields["scheduled_at"] else "draft"
    store.save_bulletins(bulletins)
    return bulletins[idx]


def cancel(bulletin_id):
    bulletins = store.load_bulletins()
    idx = next((i for i, b in enumerate(bulletins) if b["id"] == bulletin_id), None)
    if idx is None or bulletins[idx]["status"] != "scheduled":
        return False
    bulletins[idx]["status"] = "cancelled"
    bulletins[idx]["cancelled_at"] = _now_iso()
    store.save_bulletins(bulletins)
    return True


def resolve_articles(bulletin):
    """Ordered list of full article dicts for this bulletin's
    article_slugs, resolved fresh -- a slug whose article was since
    deleted/unpublished is silently skipped rather than breaking the
    render. Returns (lead_article_or_None, other_articles)."""
    all_articles = {a["slug"]: a for a in store.public_articles(store.load_articles())}
    ordered = [all_articles[s] for s in bulletin.get("article_slugs") or [] if s in all_articles]
    lead = all_articles.get(bulletin.get("lead_article_slug"))
    others = [a for a in ordered if not lead or a["slug"] != lead["slug"]]
    return lead, others


def eligible_recipients(target_preference):
    return store.confirmed_subscribers(target_preference)


def audience_preview(bulletin):
    """Counts for the pre-send safety screen -- see app.py's send
    confirmation route. Never used to decide who actually gets mail;
    send_campaign() re-resolves the live list at send time."""
    recipients = eligible_recipients(bulletin["target_preference"])
    all_subs = store.load_subscriptions()
    return {
        "compatible": is_compatible(bulletin["kind"], bulletin["target_preference"]),
        "eligible": len(recipients),
        "pending_excluded": sum(1 for s in all_subs if s.get("status") == "pending"),
        "unsubscribed_excluded": sum(1 for s in all_subs if s.get("status") == "unsubscribed"),
    }


def _bulletin_dedup_key(bulletin_id, subscription_id):
    return f"bulletin:{bulletin_id}:{subscription_id}"


def _render(render_fn, bulletin, subscriber):
    """render_fn returns (subject, text, html[, headers])."""
    result = render_fn(bulletin, subscriber)
    return result[0], result[1], result[2], (result[3] if len(result) > 3 else None)


def send_campaign(bulletin_id, actor_email, render_fn):
    """The only function that queues real subscriber mail. `render_fn`
    is called once per recipient as render_fn(bulletin, subscriber) ->
    (subject, text_body, html_body) -- kept as an injected callable
    (rather than importing app.py's Jinja rendering here) to avoid a
    circular import between this module and app.py.

    Idempotent per (bulletin, subscriber): calling this twice for the
    same bulletin (a double-click, a retried request) enqueues each
    recipient's job at most once -- see outbox.enqueue()'s
    idempotency_key handling."""
    bulletins = store.load_bulletins()
    idx = next((i for i, b in enumerate(bulletins) if b["id"] == bulletin_id), None)
    if idx is None or bulletins[idx]["status"] not in ("draft", "scheduled"):
        return None
    bulletin = bulletins[idx]
    if not is_compatible(bulletin["kind"], bulletin["target_preference"]):
        raise BulletinAudienceError("Bülten türü ile hedef kitle uyumsuz; göndermeden önce düzeltin.")

    import outbox  # local import: outbox -> mailer only, no cycle back to bulletins
    recipients = eligible_recipients(bulletin["target_preference"])
    for sub in recipients:
        subject, text_body, html_body, headers = _render(render_fn, bulletin, sub)
        # subscriber_id + target_preference let outbox.process_outbox()
        # re-check eligibility at the moment this job is actually SENT,
        # not just now at enqueue time -- see store.is_subscriber_eligible().
        outbox.enqueue(
            "bulletin", sub["email"], subject, text_body, html_body=html_body,
            campaign_id=bulletin["id"], idempotency_key=_bulletin_dedup_key(bulletin["id"], sub["id"]),
            subscriber_id=sub["id"], target_preference=bulletin["target_preference"],
            reply_to=mailer.EMAIL_CONTACT_REPLY_TO, headers=headers,
        )

    bulletins[idx]["status"] = "sent"
    bulletins[idx]["sent_at"] = _now_iso()
    bulletins[idx]["sent_by"] = actor_email
    bulletins[idx]["recipients_total"] = len(recipients)
    store.save_bulletins(bulletins)
    return bulletins[idx]


def send_test(bulletin_id, test_addresses, render_fn):
    """Sends to explicitly provided addresses only -- never touches the
    real subscriber list and never changes the bulletin's status, so a
    test can never accidentally "use up" or complete a real campaign."""
    bulletin = store.get_bulletin(bulletin_id)
    if not bulletin:
        return 0
    import outbox
    fake_recipient = {"email": None, "id": "test"}
    sent = 0
    for addr in test_addresses:
        fake_recipient["email"] = addr
        subject, text_body, html_body, headers = _render(render_fn, bulletin, fake_recipient)
        subject = f"[TEST] {subject}"
        outbox.enqueue("bulletin", addr, subject, text_body, html_body=html_body,
                        campaign_id=f"test:{bulletin_id}", reply_to=mailer.EMAIL_CONTACT_REPLY_TO,
                        headers=headers)
        sent += 1
    return sent


def process_scheduled_bulletins(render_fn):
    """Timestamp-driven, restart-safe -- same pattern as
    app.py's _process_due_issues(). Called opportunistically from routes
    that already touch bulletins/the public site."""
    now = _now_iso()
    for b in store.load_bulletins():
        if b["status"] == "scheduled" and b.get("scheduled_at") and b["scheduled_at"] <= now:
            try:
                send_campaign(b["id"], "scheduled", render_fn)
            except BulletinAudienceError:
                continue  # never sent to a mismatched audience; stays scheduled until an editor fixes it


def create_new_issue_bulletin_if_needed(issue):
    """Auto-creates (but does NOT send -- see app.py's publish hook,
    which sends it via the outbox right after) a NEW_ISSUE bulletin the
    first time a given issue is published, using every article
    associated with that issue (in their existing page order) as a
    sensible default selection. Deduplicated on source_issue_id, so a
    retried publish-hook call never creates a second bulletin for the
    same issue."""
    existing = next((b for b in store.load_bulletins() if b.get("source_issue_id") == issue["id"]), None)
    if existing:
        return existing
    articles = store.articles_by_issue(issue["id"], published_only=True)
    slugs = [a["slug"] for a in articles]
    title = issue.get("title") or f"Sayı {issue.get('no')}"
    return create_draft(
        "new_issue", "system", f"Yeni Sayı — {title}", f"Yeni Sayı Yayında: {title}",
        preheader=issue.get("description", "")[:120],
        target_preference="new_issue", issue_id=issue["id"],
        intro_text=issue.get("description", ""), lead_article_slug=(slugs[0] if slugs else None),
        article_slugs=slugs,
    )
