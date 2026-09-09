"""Editorial draft generation -- issue announcements, newsletter intros,
social copy, SEO descriptions, "highlights" summaries, push/email copy.

IMPORTANT -- nothing generated here is ever published automatically.
Every function below returns/stores a draft with status "draft"; turning
one into something live (a real announcement article, an actual sent
newsletter) always requires an explicit human action in the admin panel
(see the /admin/taslaklar routes in app.py). Nothing in this module ever
calls anything that publishes or sends.

Generation backend: template-based by default -- deterministic, no
external calls, so this works correctly with zero configuration and
produces the same result every time (useful for tests too). If a real AI
provider is ever wanted, set AI_DRAFT_PROVIDER and implement its branch
in _generate_text() below; no fabricated SDK or API key is assumed to
exist here, and the rest of the module (dedup, storage, the "always a
draft" guarantee) does not need to change either way.
"""
import os
import uuid
from datetime import datetime, timezone

import store

AI_PROVIDER = os.environ.get("AI_DRAFT_PROVIDER", "").strip().lower()
# No provider is actually wired up -- this only records operator intent
# so a future integration has a single, obvious flag to branch on.
AI_ENABLED = bool(AI_PROVIDER)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_draft(kind, issue_id, title, body, dedup_key):
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": kind,
        "issue_id": issue_id,
        "title": title,
        "body": body,
        "status": "draft",
        "generated_by": "ai" if AI_ENABLED else "template",
        "dedup_key": dedup_key,
        "created_at": _now_iso(),
        "reviewed_by": None,
        "reviewed_at": None,
        "published_at": None,
    }


def _issue_announcement_sections(issue):
    """Deterministic, template-based copy for every requested surface.
    Kept as one function (rather than one call per surface) since an
    editor reviewing this wants to see -- and edit -- all of it together
    before anything goes out; splitting these into separate draft records
    would just mean more clicking through near-identical review screens."""
    title = issue.get("title") or f"Sayı {issue.get('no')}"
    no = issue.get("no")
    date = issue.get("date", "")
    description = (issue.get("description") or "").strip()

    announcement = (
        f"The Eurovillage Herald'ın yeni sayısı yayında: Sayı {no} — {title} artık okurlarımızla buluştu.\n\n"
        + (description + "\n\n" if description else "")
        + "Gazetenin tam PDF halini gazete arşivinden okuyabilirsiniz."
    )
    newsletter_intro = (
        f"Merhaba,\n\nThe Eurovillage Herald'ın {date} tarihli yeni sayısı ({title}) şimdi yayında. "
        + (description + " " if description else "")
        + "Aşağıdaki bağlantıdan okuyabilirsiniz."
    )
    social_copy = f"📰 Yeni sayı yayında: {title} (Sayı {no}). Gazete arşivinden okuyun."
    seo_description = (
        (description[:157] + "...") if len(description) > 160
        else (description or f"{title} — The Eurovillage Herald, Sayı {no}.")
    )
    highlights = description or f"{title} sayısının öne çıkanlarını gazete arşivinde bulabilirsiniz."
    push_copy = f"Yeni sayı: {title}"

    return "\n\n".join([
        "== Duyuru Metni ==", announcement,
        "== Bülten Girişi ==", newsletter_intro,
        "== Sosyal Medya Metni ==", social_copy,
        "== SEO Açıklaması ==", seo_description,
        "== Bu Sayıdan Öne Çıkanlar ==", highlights,
        "== Bildirim/Push Metni ==", push_copy,
    ])


def generate_issue_announcement(issue):
    title = issue.get("title") or f"Sayı {issue.get('no')}"
    body = _issue_announcement_sections(issue)
    dedup_key = f"issue_announcement:{issue['id']}"
    return _new_draft("issue_announcement", issue["id"], f"Yeni Sayı Duyurusu — {title}", body, dedup_key)


def generate_issue_announcement_if_needed(issue):
    """Creates the announcement draft exactly once per issue. Safe to call
    repeatedly (e.g. the publish-hook runs again after a redeploy retries
    a half-finished publish transition) -- store.draft_exists() is
    checked first, so a second call is always a no-op."""
    dedup_key = f"issue_announcement:{issue['id']}"
    if store.draft_exists(dedup_key):
        return None
    draft = generate_issue_announcement(issue)
    drafts = store.load_drafts()
    drafts.append(draft)
    store.save_drafts(drafts)
    return draft
