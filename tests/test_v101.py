#!/usr/bin/env python3
"""Test suite for the v1.0.1 newspaper-platform release: issue metadata/
workflow, scheduling, preview, article<->issue association, analytics,
subscriptions, and a handful of pre-existing-behavior regression checks.

Runs entirely against a temporary, isolated data directory -- never reads
or writes the real app/data/*.json files, so it's safe to run repeatedly
against a real checkout (including one with real production-shaped data)
without ever touching it.

Uses only the standard library (unittest) -- no new dependency.

Run with:
    python3 -m unittest tests.test_v101 -v
from the repository root, or:
    python3 tests/test_v101.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

import store  # noqa: E402


def _seed_minimal_data(data_dir):
    """The smallest set of records inject_globals()/base.html need to
    render any page at all, plus one existing (already-published) article
    and issue so backward-compatibility checks have something real to
    check against."""
    def write(name, content):
        with open(os.path.join(data_dir, name), "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)

    write("site.json", {
        "name": "Test Herald", "tagline": "test", "legal": "", "motto": "",
        "copyright": "", "disclaimer": [], "eurovillage_site_url": "https://example.com",
        "credit_name": "x", "credit_url": "https://example.com", "leadership": [],
        "contact": {"intro_title": "", "intro_text": "", "note": "", "subjects": ["Diğer"]},
    })
    write("authors.json", [{
        "id": "auth1", "user_id": "user1", "slug": "test-author", "display_name": "Test Author",
        "status": "active", "editorial_role": None, "profile_image": None, "cover_image": None,
        "short_bio": None, "full_bio": None, "twitter_handle": None, "minecraft_username": None,
        "is_opinion_columnist": False, "contact_email": None, "slug_history": [],
    }])
    write("users.json", [
        {"id": "master1", "email": "master@test.com", "account_role": "master_admin",
         "status": "active", "password_hash": "x", "must_change_password": False},
        {"id": "user1", "email": "author@test.com", "account_role": "author",
         "status": "active", "password_hash": "x", "must_change_password": False},
        {"id": "custom1", "email": "custom@test.com", "account_role": "custom",
         "status": "active", "password_hash": "x", "must_change_password": False},
    ])
    write("roles.json", [
        {"id": "master_admin", "name": "Master Admin", "permissions": [], "system": True},
        {"id": "author", "name": "Yazar", "permissions": [], "system": True},
        {"id": "custom", "name": "Custom", "permissions": [], "system": False},
    ])
    write("newspaper_management.json", [])
    write("ads.json", [])
    write("ad_placements.json", [])
    write("messages.json", [])
    write("audit_log.json", [])
    write("crosswords.json", [])
    write("sudokus.json", [])
    write("articles.json", [{
        "id": "art1", "slug": "existing-article", "section": "sehir", "title": "Existing Article",
        "kicker": None, "dek": "", "byline_title": "Muhabir", "date": "2026-01-01",
        "featured": None, "breaking": False, "image": None, "image_caption": None,
        "tags": [], "body": [{"type": "p", "text": "Hello."}], "author_ids": ["auth1"],
        "author": "Test Author", "status": "published", "publish_at": None,
        "issue_id": None, "issue_page": None,
        "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None,
    }])
    write("issues.json", [{
        "id": "sayi-01", "no": 1, "title": "Sayı 1", "date": "2026-01-01",
        "description": "Test issue.", "cover_image": None, "pdf": "https://matbaa.eurovillageherald.com/gazeteler/2026/01/sayi-01.pdf",
        "pages": 4,
    }])
    write("issue_subscriptions.json", [])
    write("issue_analytics.json", {})
    write("editorial_drafts.json", [])
    write("email_outbox.json", [])
    write("bulletins.json", [])
    write("article_views.json", {})


class V101TestCase(unittest.TestCase):
    """Base class: fresh isolated data dir + a real Flask test client per
    test, imported once at class setup (cheap: app.py's module-level work
    is idempotent and safe to do once)."""

    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="eh_v101_test_")
        _seed_minimal_data(cls.tmp_dir)

        for name in (
            "ARTICLES_PATH", "ISSUES_PATH", "SITE_PATH", "MESSAGES_PATH", "CROSSWORDS_PATH",
            "SUDOKUS_PATH", "USERS_PATH", "AUTHORS_PATH", "AUDIT_LOG_PATH", "ROLES_PATH",
            "ADS_PATH", "AD_PLACEMENTS_PATH", "MANAGEMENT_PATH", "ISSUE_SUBSCRIPTIONS_PATH",
            "ISSUE_ANALYTICS_PATH", "EDITORIAL_DRAFTS_PATH", "EMAIL_OUTBOX_PATH",
            "BULLETINS_PATH", "ARTICLE_VIEWS_PATH",
        ):
            filename = name.replace("_PATH", "").lower()
            filename = {
                "audit_log": "audit_log.json", "ad_placements": "ad_placements.json",
                "management": "newspaper_management.json",
                "issue_subscriptions": "issue_subscriptions.json",
                "issue_analytics": "issue_analytics.json",
                "editorial_drafts": "editorial_drafts.json",
            }.get(filename, filename + ".json")
            setattr(store, name, os.path.join(cls.tmp_dir, filename))

        import app as appmod  # noqa: E402
        cls.appmod = appmod
        appmod.app.config["TESTING"] = True
        cls.client_factory = appmod.app.test_client

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def setUp(self):
        # Fresh data snapshot per test so tests can't leak state into each
        # other (each reseeds the same tmp_dir's files).
        _seed_minimal_data(self.tmp_dir)
        self.client = self.client_factory()

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def load(self, name):
        with open(os.path.join(self.tmp_dir, name), encoding="utf-8") as f:
            return json.load(f)

    def save(self, name, data):
        with open(os.path.join(self.tmp_dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class IssueVisibilityTests(V101TestCase):
    def test_published_issue_is_public(self):
        r = self.client.get("/gazete/sayi-01")
        self.assertEqual(r.status_code, 200)

    def test_draft_issue_is_not_public(self):
        issues = self.load("issues.json")
        issues.append({"id": "draft-issue", "no": 2, "title": "Draft", "date": "2026-02-01",
                        "description": "", "cover_image": None, "pdf": "https://matbaa.eurovillageherald.com/x.pdf",
                        "pages": None, "status": "draft"})
        self.save("issues.json", issues)
        r = self.client.get("/gazete/draft-issue")
        self.assertEqual(r.status_code, 404)

    def test_in_review_and_ready_are_not_public(self):
        for status in ("in_review", "ready"):
            issues = self.load("issues.json")
            iid = f"issue-{status}"
            issues.append({"id": iid, "no": 3, "title": status, "date": "2026-02-01",
                            "description": "", "cover_image": None,
                            "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                            "status": status})
            self.save("issues.json", issues)
            r = self.client.get(f"/gazete/{iid}")
            self.assertEqual(r.status_code, 404, f"status={status} leaked publicly")

    def test_archived_issue_remains_public(self):
        """Deliberately different from games' 'archived' -- see store.py's
        ISSUE_PUBLIC_STATUSES comment: the newspaper archive's whole point
        is being a permanent public archive."""
        issues = self.load("issues.json")
        issues.append({"id": "archived-issue", "no": 4, "title": "Old", "date": "2020-01-01",
                        "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "archived"})
        self.save("issues.json", issues)
        r = self.client.get("/gazete/archived-issue")
        self.assertEqual(r.status_code, 200)

    def test_gazete_archive_only_lists_public_issues(self):
        issues = self.load("issues.json")
        issues.append({"id": "hidden-draft", "no": 5, "title": "Hidden Draft Title Unique",
                        "date": "2026-02-01", "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "draft"})
        self.save("issues.json", issues)
        r = self.client.get("/gazete")
        self.assertNotIn(b"Hidden Draft Title Unique", r.data)


class ScheduledPublishingTests(V101TestCase):
    def test_scheduled_future_issue_not_public(self):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        issues = self.load("issues.json")
        issues.append({"id": "future-issue", "no": 6, "title": "Future", "date": "2026-03-01",
                        "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "scheduled", "publish_at": future})
        self.save("issues.json", issues)
        r = self.client.get("/gazete/future-issue")
        self.assertEqual(r.status_code, 404)

    def test_scheduled_past_issue_self_heals_to_published(self):
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        issues = self.load("issues.json")
        issues.append({"id": "past-issue", "no": 7, "title": "Past", "date": "2026-01-01",
                        "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "scheduled", "publish_at": past})
        self.save("issues.json", issues)
        r = self.client.get("/gazete/past-issue")
        self.assertEqual(r.status_code, 200)
        updated = next(i for i in self.load("issues.json") if i["id"] == "past-issue")
        self.assertEqual(updated["status"], "published")


class PreviewAuthorizationTests(V101TestCase):
    def test_preview_token_grants_access_to_draft(self):
        issues = self.load("issues.json")
        token = "test-preview-token-abc"
        issues.append({"id": "preview-issue", "no": 8, "title": "Preview Me", "date": "2026-02-01",
                        "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "draft", "preview_token_hash": store.hash_preview_token(token),
                        "preview_token_expires_at": None})
        self.save("issues.json", issues)
        r = self.client.get(f"/gazete/onizleme/{token}")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"noindex", r.data)

    def test_wrong_preview_token_rejected(self):
        r = self.client.get("/gazete/onizleme/not-a-real-token")
        self.assertEqual(r.status_code, 404)

    def test_expired_preview_token_rejected(self):
        from datetime import datetime, timedelta, timezone
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        issues = self.load("issues.json")
        token = "expired-token-xyz"
        issues.append({"id": "expired-preview", "no": 9, "title": "Expired", "date": "2026-02-01",
                        "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "draft", "preview_token_hash": store.hash_preview_token(token),
                        "preview_token_expires_at": past})
        self.save("issues.json", issues)
        r = self.client.get(f"/gazete/onizleme/{token}")
        self.assertEqual(r.status_code, 404)

    def test_admin_routes_require_master_admin(self):
        self.login_as("custom1")  # role "custom" has no permissions at all
        for path in ("/admin/sayilar", "/admin/sayi/yeni", "/admin/gazete-analitik",
                     "/admin/taslaklar", "/admin/aboneler"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 403, f"{path} should require master admin")

    def test_master_admin_always_has_newspaper_access(self):
        self.login_as("master1")
        r = self.client.get("/admin/sayilar")
        self.assertEqual(r.status_code, 200)

    def test_granting_legacy_newspaper_permission_to_custom_role_no_longer_grants_access(self):
        """Post-hardening: issue/newspaper administration was retired from
        PERMISSION_CHOICES and hardcoded to master_admin_required (see
        app.py's PERMISSION_CHOICES comment). A role record that still
        carries the old "newspaper" string (e.g. saved before this change)
        must be inert, not a live grant."""
        roles = self.load("roles.json")
        for role in roles:
            if role["id"] == "custom":
                role["permissions"] = ["newspaper"]
        self.save("roles.json", roles)
        self.login_as("custom1")
        r = self.client.get("/admin/sayilar")
        self.assertEqual(r.status_code, 403)
        # subscribers were already master_admin-only before this change
        r2 = self.client.get("/admin/aboneler")
        self.assertEqual(r2.status_code, 403)


class ArticleIssueAssociationTests(V101TestCase):
    def test_article_shows_linked_issue_and_page(self):
        articles = self.load("articles.json")
        articles[0]["issue_id"] = "sayi-01"
        articles[0]["issue_page"] = 3
        self.save("articles.json", articles)
        r = self.client.get("/makale/existing-article")
        self.assertEqual(r.status_code, 200)
        self.assertIn("BU HABER GAZETEDE".encode(), r.data)
        self.assertIn(b"s. 3", r.data)

    def test_issue_page_shows_bu_sayidan(self):
        articles = self.load("articles.json")
        articles[0]["issue_id"] = "sayi-01"
        articles[0]["issue_page"] = 2
        self.save("articles.json", articles)
        r = self.client.get("/gazete/sayi-01")
        self.assertIn("Bu Sayıdan".encode(), r.data)
        self.assertIn(b"Existing Article", r.data)

    def test_article_linked_to_unpublished_issue_hides_the_box(self):
        articles = self.load("articles.json")
        articles[0]["issue_id"] = "sayi-01"
        articles[0]["issue_page"] = 1
        self.save("articles.json", articles)
        issues = self.load("issues.json")
        issues[0]["status"] = "draft"
        self.save("issues.json", issues)
        r = self.client.get("/makale/existing-article")
        self.assertNotIn("BU HABER GAZETEDE".encode(), r.data)

    def test_spoofed_issue_id_is_dropped_on_article_create(self):
        self.login_as("master1")
        r = self.client.post("/admin/makale/yeni", data={
            "section": "sehir", "title": "Spoofed Test", "dek": "", "byline_title": "Muhabir",
            "date": "2026-04-01", "body": "Text.", "issue_id": "does-not-exist",
            "issue_page": "5", "publish_state": "published",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        articles = self.load("articles.json")
        created = next(a for a in articles if a["title"] == "Spoofed Test")
        self.assertIsNone(created["issue_id"])
        self.assertIsNone(created["issue_page"])


class AnalyticsTests(V101TestCase):
    def test_open_event_recorded_and_referrer_reduced_to_host(self):
        r = self.client.post("/api/gazete/sayi-01/analitik/acilis", json={
            "reader_id": "reader-abc", "device": "mobile", "referrer": "https://google.com/search?q=secret",
        })
        self.assertEqual(r.status_code, 200)
        data = self.load("issue_analytics.json")
        self.assertEqual(data["sayi-01"]["opens"], 1)
        self.assertEqual(data["sayi-01"]["sessions"], 1)
        self.assertIn("google.com", data["sayi-01"]["referrers"])
        # the query string must never be persisted
        dumped = json.dumps(data)
        self.assertNotIn("secret", dumped)

    def test_repeat_open_same_reader_increments_sessions_not_opens(self):
        for _ in range(3):
            self.client.post("/api/gazete/sayi-01/analitik/acilis", json={"reader_id": "same-reader"})
        data = self.load("issue_analytics.json")
        self.assertEqual(data["sayi-01"]["opens"], 1)
        self.assertEqual(data["sayi-01"]["sessions"], 3)

    def test_analytics_rejected_for_nonexistent_issue(self):
        r = self.client.post("/api/gazete/does-not-exist/analitik/acilis", json={"reader_id": "x"})
        self.assertEqual(r.status_code, 404)

    def test_analytics_rejected_for_unpublished_issue(self):
        issues = self.load("issues.json")
        issues.append({"id": "hidden-issue", "no": 10, "title": "Hidden", "date": "2026-02-01",
                        "description": "", "cover_image": None, "pdf": "x", "pages": None, "status": "draft"})
        self.save("issues.json", issues)
        r = self.client.post("/api/gazete/hidden-issue/analitik/acilis", json={"reader_id": "x"})
        self.assertEqual(r.status_code, 404)

    def test_session_summary_and_download_and_share(self):
        self.client.post("/api/gazete/sayi-01/analitik/session_summary", json={"max_page": 3, "total_pages": 4})
        self.client.post("/api/gazete/sayi-01/analitik/download", json={})
        self.client.post("/api/gazete/sayi-01/analitik/share", json={})
        data = self.load("issue_analytics.json")["sayi-01"]
        self.assertEqual(data["total_pages_viewed"], 3)
        self.assertEqual(data["downloads"], 1)
        self.assertEqual(data["share_actions"], 1)
        self.assertEqual(data["sessions_reached_last_page"], 0)  # 3 < 4


class SubscriptionTests(V101TestCase):
    def test_subscribe_creates_pending_record(self):
        r = self.client.post("/abone-ol", data={"email": "reader@example.com", "pref_new_issue": "1",
                                                 "privacy_ack": "1"},
                              follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["status"], "pending")
        self.assertIsNotNone(subs[0]["confirm_token_hash"])

    def test_invalid_email_rejected(self):
        self.client.post("/abone-ol", data={"email": "not-an-email", "pref_new_issue": "1",
                                             "privacy_ack": "1"},
                          follow_redirects=True)
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(len(subs), 0)

    def test_missing_privacy_consent_is_rejected(self):
        """A KVKK-consent checkbox was added on top of v1.0.1's original
        subscribe form -- see app/subscriptions.py's CURRENT_PRIVACY_NOTICE_VERSION
        and app.py's subscribe_submit(). Submitting without it must not
        create a subscription."""
        self.client.post("/abone-ol", data={"email": "noconsent@example.com", "pref_new_issue": "1"},
                          follow_redirects=True)
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(len(subs), 0)

    def test_confirm_and_unsubscribe_flow(self):
        import subscriptions
        sub, token = subscriptions.subscribe("flow@example.com", {"new_issue": True})
        self.assertEqual(sub["status"], "pending")
        confirmed = subscriptions.confirm(token)
        self.assertEqual(confirmed["status"], "confirmed")
        self.assertIn(confirmed["email"], [s["email"] for s in store.confirmed_subscribers("new_issue")])
        # token is single-use
        self.assertIsNone(subscriptions.confirm(token))
        unsub_token = subscriptions.unsubscribe_token(self.appmod.app.secret_key, confirmed["id"])
        r = self.client.post(f"/abone-ol/cik/{unsub_token}")
        self.assertEqual(r.status_code, 200)
        final = next(s for s in self.load("issue_subscriptions.json") if s["id"] == confirmed["id"])
        self.assertEqual(final["status"], "unsubscribed")

    def test_tampered_unsubscribe_token_rejected(self):
        r = self.client.get("/abone-ol/cik/not-a-real-signed-token")
        self.assertEqual(r.status_code, 404)


class DuplicateNotificationPreventionTests(V101TestCase):
    def test_preexisting_published_issue_does_not_regenerate_draft_on_load(self):
        """A pre-existing published issue (announcement_sent_at backfilled
        immediately by load_issues()) must not spawn an announcement draft
        just because the archive page was loaded."""
        self.client.get("/gazete")
        drafts = self.load("editorial_drafts.json")
        self.assertEqual(len(drafts), 0)

    def test_freshly_published_issue_generates_exactly_one_draft_even_if_loaded_twice(self):
        self.login_as("master1")
        issues = self.load("issues.json")
        issues.append({"id": "new-pub", "no": 11, "title": "Freshly Published", "date": "2026-05-01",
                        "description": "", "cover_image": None,
                        "pdf": "https://matbaa.eurovillageherald.com/x.pdf", "pages": None,
                        "status": "published", "announcement_sent_at": None})
        self.save("issues.json", issues)
        self.client.get("/gazete")
        self.client.get("/gazete")  # a second load must not create a second draft
        drafts = self.load("editorial_drafts.json")
        matching = [d for d in drafts if d.get("issue_id") == "new-pub"]
        self.assertEqual(len(matching), 1)
        updated = next(i for i in self.load("issues.json") if i["id"] == "new-pub")
        self.assertIsNotNone(updated["announcement_sent_at"])


class MediaUrlBackwardCompatibilityTests(V101TestCase):
    def test_media_url_passes_through_r2_url_unchanged(self):
        with self.appmod.app.test_request_context("/"):
            value = self.appmod.media_url("https://matbaa.eurovillageherald.com/x.jpg", "img/articles")
            self.assertEqual(value, "https://matbaa.eurovillageherald.com/x.jpg")

    def test_media_url_resolves_legacy_bare_filename_locally(self):
        with self.appmod.app.test_request_context("/"):
            value = self.appmod.media_url("legacy.jpg", "img/articles")
            self.assertTrue(value.startswith("/static/img/articles/"))

    def test_media_url_none_for_empty(self):
        with self.appmod.app.test_request_context("/"):
            self.assertIsNone(self.appmod.media_url(None, "img/articles"))
            self.assertIsNone(self.appmod.media_url("", "img/articles"))


class ExistingBehaviorRegressionTests(V101TestCase):
    """Lighter-touch checks that pre-existing functionality this release
    did not intend to change still works -- article scheduling/preview,
    ads listing, public route health. Full R2 upload-path correctness has
    its own dedicated audit (see the migration-tool test suite); this is
    not a duplicate of that."""

    def test_existing_published_article_still_public(self):
        r = self.client.get("/makale/existing-article")
        self.assertEqual(r.status_code, 200)

    def test_article_scheduling_still_gates_visibility(self):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        articles = self.load("articles.json")
        articles.append({
            "id": "art2", "slug": "future-article", "section": "sehir", "title": "Future",
            "kicker": None, "dek": "", "byline_title": "Muhabir", "date": "2026-06-01",
            "featured": None, "breaking": False, "image": None, "image_caption": None,
            "tags": [], "body": [], "author_ids": [], "author": "x", "status": "scheduled",
            "publish_at": future, "issue_id": None, "issue_page": None,
            "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None,
        })
        self.save("articles.json", articles)
        r = self.client.get("/makale/future-article")
        self.assertEqual(r.status_code, 404)

    def test_ads_dashboard_still_reachable_by_master_admin(self):
        self.login_as("master1")
        r = self.client.get("/admin/reklam")
        self.assertEqual(r.status_code, 200)

    def test_homepage_and_section_pages_still_render(self):
        for path in ("/", "/magazin", "/oyun-kosesi", "/gazete-yonetimi", "/yazarlar"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
