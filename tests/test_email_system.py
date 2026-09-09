#!/usr/bin/env python3
"""Test suite for the production email / subscription / bulletin / contact
-ticket communication system: the durable outbox, the provider abstraction
(always exercised via the fake/logging backend -- this suite must NEVER
send a real email), double opt-in subscriptions, the bulletin CMS, the
contact-form-to-ticket workflow, and the relevant permission boundaries.

Mirrors tests/test_v101.py's harness (isolated temp data dir + a real
Flask test client, EMAIL_PROVIDER left unset so mailer.py falls back to
its fake backend) rather than sharing it directly, since this suite seeds
a few additional data files (email_outbox.json, bulletins.json,
article_views.json) that v1.0.1's harness doesn't know about.

Run with:
    python3 -m unittest tests.test_email_system -v
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

# Never accidentally pick up a real provider key from the developer's own
# shell environment while running this suite.
os.environ.pop("EMAIL_API_KEY", None)
os.environ["EMAIL_PROVIDER"] = "fake"

import store  # noqa: E402


def _seed_minimal_data(data_dir):
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
        {"id": "custom1", "email": "custom@test.com", "account_role": "custom",
         "status": "active", "password_hash": "x", "must_change_password": False},
        # A maximally-permissioned NON-master-admin role: every current
        # PERMISSION_CHOICES entry granted, PLUS the retired "newspaper"/
        # "bulletins"/"messages" strings a pre-hardening role record might
        # still carry on disk. Used to prove those old strings are inert,
        # not just "not yet granted".
        {"id": "poweruser1", "email": "poweruser@test.com", "account_role": "poweruser",
         "status": "active", "password_hash": "x", "must_change_password": False},
        # A genuine author/contributor account (tied to authors.json's
        # "auth1" below) -- the "ordinary newsroom user" the task asks to
        # verify is denied, distinct from a custom-role admin account.
        {"id": "user1", "email": "author@test.com", "account_role": "author",
         "status": "active", "password_hash": "x", "must_change_password": False},
    ])
    write("roles.json", [
        {"id": "master_admin", "name": "Master Admin", "permissions": [], "system": True},
        {"id": "custom", "name": "Custom", "permissions": [], "system": False},
        {"id": "poweruser", "name": "Power User",
         "permissions": ["games", "site_settings", "audit_log", "newspaper", "bulletins", "messages"],
         "system": False},
    ])
    write("newspaper_management.json", [])
    write("ads.json", [])
    write("ad_placements.json", [])
    write("messages.json", [])
    write("audit_log.json", [])
    write("crosswords.json", [])
    write("sudokus.json", [])
    write("articles.json", [
        {"id": "art1", "slug": "existing-article", "section": "sehir", "title": "Existing Article",
         "kicker": None, "dek": "A dek.", "byline_title": "Muhabir", "date": "2026-01-01",
         "featured": None, "breaking": False, "image": None, "image_caption": None,
         "tags": [], "body": [{"type": "p", "text": "Hello."}], "author_ids": ["auth1"],
         "author": "Test Author", "status": "published", "publish_at": None,
         "issue_id": "sayi-01", "issue_page": 1,
         "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None},
        {"id": "art2", "slug": "second-article", "section": "sehir", "title": "Second Article",
         "kicker": None, "dek": "", "byline_title": "Muhabir", "date": "2026-01-02",
         "featured": None, "breaking": False, "image": None, "image_caption": None,
         "tags": [], "body": [{"type": "p", "text": "Hello again."}], "author_ids": ["auth1"],
         "author": "Test Author", "status": "published", "publish_at": None,
         "issue_id": "sayi-01", "issue_page": 2,
         "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None},
    ])
    write("issues.json", [{
        "id": "sayi-01", "no": 1, "title": "Sayı 1", "date": "2026-01-01",
        "description": "Test issue.", "cover_image": None,
        "pdf": "https://matbaa.eurovillageherald.com/gazeteler/2026/01/sayi-01.pdf",
        "pages": 4, "status": "published", "announcement_sent_at": "2026-01-01T00:00:00Z",
    }])
    write("issue_subscriptions.json", [])
    write("issue_analytics.json", {})
    write("editorial_drafts.json", [])
    write("email_outbox.json", [])
    write("bulletins.json", [])
    write("article_views.json", {})


REDIRECTED_PATHS = {
    "ARTICLES_PATH": "articles.json", "ISSUES_PATH": "issues.json", "SITE_PATH": "site.json",
    "MESSAGES_PATH": "messages.json", "CROSSWORDS_PATH": "crosswords.json",
    "SUDOKUS_PATH": "sudokus.json", "USERS_PATH": "users.json", "AUTHORS_PATH": "authors.json",
    "AUDIT_LOG_PATH": "audit_log.json", "ROLES_PATH": "roles.json", "ADS_PATH": "ads.json",
    "AD_PLACEMENTS_PATH": "ad_placements.json", "MANAGEMENT_PATH": "newspaper_management.json",
    "ISSUE_SUBSCRIPTIONS_PATH": "issue_subscriptions.json", "ISSUE_ANALYTICS_PATH": "issue_analytics.json",
    "EDITORIAL_DRAFTS_PATH": "editorial_drafts.json", "EMAIL_OUTBOX_PATH": "email_outbox.json",
    "BULLETINS_PATH": "bulletins.json", "ARTICLE_VIEWS_PATH": "article_views.json",
}


class EmailSystemTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="eh_email_test_")
        _seed_minimal_data(cls.tmp_dir)
        for const_name, filename in REDIRECTED_PATHS.items():
            setattr(store, const_name, os.path.join(cls.tmp_dir, filename))

        import app as appmod  # noqa: E402
        cls.appmod = appmod
        appmod.app.config["TESTING"] = True
        cls.client_factory = appmod.app.test_client

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def setUp(self):
        _seed_minimal_data(self.tmp_dir)
        self.client = self.client_factory()
        import ratelimit
        ratelimit._hits.clear()  # the in-memory limiter is process-global; isolate each test

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def load(self, name):
        with open(os.path.join(self.tmp_dir, name), encoding="utf-8") as f:
            return json.load(f)

    def save(self, name, data):
        with open(os.path.join(self.tmp_dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


class MailerFakeBackendTests(EmailSystemTestCase):
    """The single most important safety property of this whole suite:
    nothing here is ever allowed to reach a real provider."""

    def test_default_backend_is_fake(self):
        import mailer
        self.assertEqual(mailer.BACKEND, "fake")

    def test_fake_transactional_send_succeeds_without_network(self):
        import mailer
        ok, error = mailer.send_transactional_email("reader@example.com", "Subj", "body text")
        self.assertTrue(ok)
        self.assertIsNone(error)

    def test_fake_bulletin_send_succeeds_without_network(self):
        import mailer
        ok, error = mailer.send_bulletin_email("reader@example.com", "Subj", "body text", "<p>body</p>")
        self.assertTrue(ok)

    def test_sender_identities_never_exposes_api_key(self):
        import mailer
        identities = mailer.sender_identities()
        dumped = json.dumps(identities)
        self.assertNotIn("EMAIL_API_KEY", dumped)
        self.assertNotIn(mailer.EMAIL_API_KEY or "unset-marker", dumped) if mailer.EMAIL_API_KEY else None
        self.assertNotIn("password", dumped.lower())


class OutboxTests(EmailSystemTestCase):
    def test_enqueue_creates_queued_job(self):
        import outbox
        job = outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        self.assertEqual(job["status"], "queued")
        self.assertEqual(len(store.load_email_outbox()), 1)

    def test_idempotency_key_prevents_duplicate_enqueue(self):
        import outbox
        j1 = outbox.enqueue("transactional", "a@example.com", "Subj", "text", idempotency_key="k1")
        j2 = outbox.enqueue("transactional", "a@example.com", "Subj 2", "text 2", idempotency_key="k1")
        self.assertEqual(j1["id"], j2["id"])
        self.assertEqual(len(store.load_email_outbox()), 1)

    def test_process_outbox_sends_queued_jobs_via_fake_backend(self):
        import outbox
        outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        attempted = outbox.process_outbox(limit=10)
        self.assertEqual(attempted, 1)
        job = store.load_email_outbox()[0]
        self.assertEqual(job["status"], "sent")
        self.assertIsNotNone(job["sent_at"])

    def test_stuck_processing_job_recovered_on_next_process(self):
        import outbox
        job = outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        jobs = store.load_email_outbox()
        jobs[0]["status"] = "processing"  # simulate a crash mid-send
        store.save_email_outbox(jobs)
        outbox.process_outbox(limit=10)
        self.assertEqual(store.load_email_outbox()[0]["status"], "sent")

    def test_failed_job_retried_after_max_attempts_becomes_permanently_failed(self):
        import outbox
        import mailer
        original = mailer.send_transactional_email
        mailer.send_transactional_email = lambda *a, **k: (False, "simulated provider outage")
        try:
            outbox.enqueue("transactional", "a@example.com", "Subj", "text")
            for _ in range(outbox.MAX_ATTEMPTS):
                outbox.process_outbox(limit=10)
            job = store.load_email_outbox()[0]
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["retry_count"], outbox.MAX_ATTEMPTS)
        finally:
            mailer.send_transactional_email = original

    def test_retry_job_only_works_on_genuinely_failed_jobs(self):
        import outbox
        job = outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        self.assertFalse(outbox.retry_job(job["id"]))  # still queued, not failed
        jobs = store.load_email_outbox()
        jobs[0]["status"] = "failed"
        store.save_email_outbox(jobs)
        self.assertTrue(outbox.retry_job(job["id"]))
        self.assertEqual(store.load_email_outbox()[0]["status"], "queued")


class SubscriptionFlowTests(EmailSystemTestCase):
    def test_signup_requires_privacy_ack(self):
        r = self.client.post("/abone-ol", data={"email": "reader@example.com", "pref_new_issue": "1"},
                              follow_redirects=True)
        self.assertEqual(len(self.load("issue_subscriptions.json")), 0)

    def test_signup_with_privacy_ack_creates_pending_and_enqueues_confirm_email(self):
        r = self.client.post("/abone-ol", data={
            "email": "reader@example.com", "pref_new_issue": "1", "privacy_ack": "1",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["status"], "pending")
        self.assertIsNotNone(subs[0]["privacy_notice_version"])
        self.assertIsNotNone(subs[0]["consent_at"])
        outbox_jobs = self.load("email_outbox.json")
        self.assertEqual(len(outbox_jobs), 1)
        self.assertEqual(outbox_jobs[0]["kind"], "transactional")

    def test_duplicate_pending_signup_does_not_create_second_record(self):
        import subscriptions
        subscriptions.subscribe("dup@example.com", {"new_issue": True})
        subscriptions.subscribe("dup@example.com", {"new_issue": True})
        subs = [s for s in self.load("issue_subscriptions.json") if s["email"] == "dup@example.com"]
        self.assertEqual(len(subs), 1)

    def test_expired_confirm_token_rejected(self):
        import subscriptions
        from datetime import datetime, timedelta, timezone
        sub, token = subscriptions.subscribe("expired@example.com", {"new_issue": True})
        subs = self.load("issue_subscriptions.json")
        subs[0]["confirm_token_expires_at"] = (
            datetime.now(timezone.utc) - timedelta(hours=1)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.save("issue_subscriptions.json", subs)
        self.assertIsNone(subscriptions.confirm(token))

    def test_confirm_token_is_single_use(self):
        import subscriptions
        sub, token = subscriptions.subscribe("once@example.com", {"new_issue": True})
        self.assertIsNotNone(subscriptions.confirm(token))
        self.assertIsNone(subscriptions.confirm(token))

    def test_already_confirmed_resubscribe_updates_preferences_without_new_token(self):
        import subscriptions
        sub, token = subscriptions.subscribe("resub@example.com", {"new_issue": True})
        subscriptions.confirm(token)
        sub2, token2 = subscriptions.subscribe("resub@example.com", {"weekly_digest": True})
        self.assertIsNone(token2)
        self.assertTrue(sub2["preferences"]["weekly_digest"])

    def test_unsubscribed_address_can_resubscribe(self):
        import subscriptions
        sub, token = subscriptions.subscribe("winback@example.com", {"new_issue": True})
        confirmed = subscriptions.confirm(token)
        subscriptions.unsubscribe(confirmed["id"])
        sub2, token2 = subscriptions.subscribe("winback@example.com", {"new_issue": True})
        self.assertEqual(sub2["status"], "pending")
        self.assertIsNotNone(token2)

    def test_full_unsubscribe_token_security(self):
        import subscriptions
        sub, token = subscriptions.subscribe("secure@example.com", {"new_issue": True})
        confirmed = subscriptions.confirm(token)
        real_token = subscriptions.unsubscribe_token(self.appmod.app.secret_key, confirmed["id"])
        self.assertIsNone(subscriptions.subscription_id_from_unsubscribe_token(
            self.appmod.app.secret_key, real_token + "tampered"))
        self.assertEqual(subscriptions.subscription_id_from_unsubscribe_token(
            self.appmod.app.secret_key, real_token), confirmed["id"])

    def test_manage_token_not_interchangeable_with_unsubscribe_token(self):
        import subscriptions
        sub, token = subscriptions.subscribe("tokens@example.com", {"new_issue": True})
        confirmed = subscriptions.confirm(token)
        unsub_token = subscriptions.unsubscribe_token(self.appmod.app.secret_key, confirmed["id"])
        self.assertIsNone(subscriptions.subscription_id_from_manage_token(
            self.appmod.app.secret_key, unsub_token))

    def test_category_unsubscribe_leaves_other_preferences_intact(self):
        import subscriptions
        sub, token = subscriptions.subscribe(
            "categories@example.com", {"new_issue": True, "weekly_digest": True})
        confirmed = subscriptions.confirm(token)
        subscriptions.unsubscribe_one(confirmed["id"], "new_issue")
        updated = store.get_subscription_by_id(confirmed["id"])
        self.assertFalse(updated["preferences"]["new_issue"])
        self.assertTrue(updated["preferences"]["weekly_digest"])
        self.assertEqual(updated["status"], "confirmed")

    def test_update_preferences_allows_unchecking_everything(self):
        """The bug caught during implementation: default_if_empty must be
        False here, unlike the signup form -- a reader deliberately going
        quiet on the management page must not have new_issue silently
        forced back on."""
        import subscriptions
        sub, token = subscriptions.subscribe("quiet@example.com", {"new_issue": True})
        confirmed = subscriptions.confirm(token)
        subscriptions.update_preferences(confirmed["id"], {})
        updated = store.get_subscription_by_id(confirmed["id"])
        self.assertFalse(any(updated["preferences"].values()))

    def test_signup_with_nothing_checked_defaults_to_new_issue(self):
        import subscriptions
        sub, _ = subscriptions.subscribe("blank@example.com", {})
        self.assertTrue(sub["preferences"]["new_issue"])

    def test_invalid_email_rejected(self):
        import subscriptions
        sub, token = subscriptions.subscribe("not-an-email", {"new_issue": True})
        self.assertIsNone(sub)


class ContactTicketTests(EmailSystemTestCase):
    def _submit(self, **overrides):
        data = {
            "category": "Diğer", "message": "Test message body.",
        }
        data.update(overrides)
        return self.client.post("/iletisim", data=data, follow_redirects=True)

    def test_submission_creates_ticket_with_reference(self):
        r = self._submit(email="ticket@example.com", name="Reader")
        self.assertEqual(r.status_code, 200)
        messages = self.load("messages.json")
        self.assertEqual(len(messages), 1)
        self.assertRegex(messages[0]["ref"], r"^TEH-\d{4}-\d{4}$")
        self.assertEqual(messages[0]["status"], "new")

    def test_sequential_references_increment_per_year(self):
        self._submit(email="a@example.com")
        self._submit(email="b@example.com")
        messages = self.load("messages.json")
        refs = sorted(m["ref"] for m in messages)
        self.assertNotEqual(refs[0], refs[1])

    def test_submission_with_email_enqueues_acknowledgement(self):
        self._submit(email="ack@example.com")
        outbox_jobs = self.load("email_outbox.json")
        self.assertEqual(len(outbox_jobs), 1)
        self.assertEqual(outbox_jobs[0]["reply_to"], "iletisim@eurovillageherald.com")
        messages = self.load("messages.json")
        self.assertIsNotNone(messages[0]["acknowledgement_sent_at"])

    def test_anonymous_submission_sends_no_acknowledgement(self):
        self._submit()  # no email field at all
        self.assertEqual(len(self.load("email_outbox.json")), 0)
        messages = self.load("messages.json")
        self.assertIsNone(messages[0].get("email"))
        self.assertIsNone(messages[0]["acknowledgement_sent_at"])

    def test_ticket_persists_even_if_email_provider_is_down(self):
        import mailer
        original = mailer.send_transactional_email
        mailer.send_transactional_email = lambda *a, **k: (False, "simulated outage")
        try:
            r = self._submit(email="resilient@example.com")
            self.assertEqual(r.status_code, 200)
            self.assertEqual(len(self.load("messages.json")), 1)
            # the ticket must exist regardless of whether the ack ever sends
        finally:
            mailer.send_transactional_email = original

    def test_status_change_to_waiting_with_notify_sends_email(self):
        self._submit(email="status@example.com")
        mid = self.load("messages.json")[0]["id"]
        self.login_as("master1")
        self.client.post(f"/admin/mesaj/{mid}/durum", data={"status": "waiting", "notify": "1"})
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 2)  # ack + status notice

    def test_status_change_without_notify_checkbox_sends_nothing_extra(self):
        self._submit(email="status2@example.com")
        mid = self.load("messages.json")[0]["id"]
        self.login_as("master1")
        self.client.post(f"/admin/mesaj/{mid}/durum", data={"status": "waiting"})
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 1)  # ack only

    def test_status_change_to_resolved_sends_no_automatic_email(self):
        """resolved has no entry in STATUS_EMAIL_COPY -- resolving is a
        distinct, explicit action (message_send_resolution), not an
        automatic side effect of a status dropdown."""
        self._submit(email="status3@example.com")
        mid = self.load("messages.json")[0]["id"]
        self.login_as("master1")
        self.client.post(f"/admin/mesaj/{mid}/durum", data={"status": "resolved", "notify": "1"})
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 1)  # ack only, no auto-resolution email

    def test_explicit_resolution_send_required(self):
        self._submit(email="resolve@example.com")
        mid = self.load("messages.json")[0]["id"]
        self.login_as("master1")
        r = self.client.post(f"/admin/mesaj/{mid}/cozum", data={"resolution": "Çözüldü, teşekkürler."},
                              follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        messages = self.load("messages.json")
        self.assertEqual(messages[0]["status"], "resolved")
        self.assertIsNotNone(messages[0]["resolution_sent_at"])
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 2)  # ack + resolution

    def test_resolution_requires_ticket_to_have_email(self):
        self._submit()  # anonymous
        mid = self.load("messages.json")[0]["id"]
        self.login_as("master1")
        self.client.post(f"/admin/mesaj/{mid}/cozum", data={"resolution": "x"})
        messages = self.load("messages.json")
        self.assertIsNone(messages[0]["resolution"])

    def test_messages_list_view_filters_by_status(self):
        self._submit(email="open@example.com")
        self.login_as("master1")
        r_open = self.client.get("/admin/mesajlar?view=open")
        r_resolved = self.client.get("/admin/mesajlar?view=resolved")
        self.assertIn(b"open@example.com", r_open.data)
        self.assertNotIn(b"open@example.com", r_resolved.data)


class BulletinCmsTests(EmailSystemTestCase):
    def test_create_draft_never_enqueues_mail(self):
        import bulletins
        bulletins.create_draft("custom", "editor@test.com", "Test Bulletin", "Subject")
        self.assertEqual(len(self.load("email_outbox.json")), 0)

    def test_update_draft_never_enqueues_mail(self):
        import bulletins
        b = bulletins.create_draft("custom", "editor@test.com", "Test Bulletin", "Subject")
        bulletins.update_draft(b["id"], subject="New Subject")
        self.assertEqual(len(self.load("email_outbox.json")), 0)
        self.assertEqual(store.get_bulletin(b["id"])["subject"], "New Subject")

    def test_audience_preview_excludes_pending_and_unsubscribed(self):
        import subscriptions
        import bulletins
        s1, t1 = subscriptions.subscribe("active@example.com", {"new_issue": True})
        subscriptions.confirm(t1)
        subscriptions.subscribe("pending@example.com", {"new_issue": True})
        s3, t3 = subscriptions.subscribe("gone@example.com", {"new_issue": True})
        c3 = subscriptions.confirm(t3)
        subscriptions.unsubscribe(c3["id"])

        b = bulletins.create_draft("custom", "editor@test.com", "T", "S", target_preference="new_issue")
        audience = bulletins.audience_preview(b)
        self.assertEqual(audience["eligible"], 1)
        self.assertEqual(audience["pending_excluded"], 1)
        self.assertEqual(audience["unsubscribed_excluded"], 1)

    def test_send_test_never_touches_real_subscribers_or_bulletin_status(self):
        import bulletins
        b = bulletins.create_draft("custom", "editor@test.com", "T", "S")
        count = bulletins.send_test(b["id"], ["tester@example.com"], lambda bul, sub: ("Subj", "text", "<p>x</p>"))
        self.assertEqual(count, 1)
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 1)
        self.assertTrue(jobs[0]["campaign_id"].startswith("test:"))
        self.assertEqual(store.get_bulletin(b["id"])["status"], "draft")

    def test_send_campaign_queues_one_job_per_eligible_subscriber(self):
        import subscriptions
        import bulletins
        for addr in ("a@example.com", "b@example.com"):
            s, t = subscriptions.subscribe(addr, {"new_issue": True})
            subscriptions.confirm(t)
        b = bulletins.create_draft("custom", "editor@test.com", "T", "S", target_preference="new_issue")
        sent = bulletins.send_campaign(b["id"], "editor@test.com", lambda bul, sub: ("Subj", "text", "<p>x</p>"))
        self.assertEqual(sent["status"], "sent")
        self.assertEqual(sent["recipients_total"], 2)
        self.assertEqual(len(self.load("email_outbox.json")), 2)

    def test_send_campaign_is_not_repeatable_on_an_already_sent_bulletin(self):
        import subscriptions
        import bulletins
        s, t = subscriptions.subscribe("once@example.com", {"new_issue": True})
        subscriptions.confirm(t)
        b = bulletins.create_draft("custom", "editor@test.com", "T", "S", target_preference="new_issue")
        bulletins.send_campaign(b["id"], "editor@test.com", lambda bul, sub: ("Subj", "text", "<p>x</p>"))
        second = bulletins.send_campaign(b["id"], "editor@test.com", lambda bul, sub: ("Subj", "text", "<p>x</p>"))
        self.assertIsNone(second)
        self.assertEqual(len(self.load("email_outbox.json")), 1)

    def test_new_issue_bulletin_auto_creation_is_deduplicated(self):
        import bulletins
        issue = store.get_issue("sayi-01")
        b1 = bulletins.create_new_issue_bulletin_if_needed(issue)
        b2 = bulletins.create_new_issue_bulletin_if_needed(issue)
        self.assertEqual(b1["id"], b2["id"])
        self.assertEqual(len(self.load("bulletins.json")), 1)

    def test_new_issue_bulletin_defaults_to_every_linked_published_article(self):
        import bulletins
        issue = store.get_issue("sayi-01")
        b = bulletins.create_new_issue_bulletin_if_needed(issue)
        self.assertEqual(set(b["article_slugs"]), {"existing-article", "second-article"})

    def test_resolve_articles_skips_deleted_or_unpublished_slugs(self):
        import bulletins
        b = bulletins.create_draft("custom", "editor@test.com", "T", "S",
                                    article_slugs=["existing-article", "does-not-exist"],
                                    lead_article_slug="existing-article")
        lead, others = bulletins.resolve_articles(b)
        self.assertEqual(lead["slug"], "existing-article")
        self.assertEqual(others, [])


class IssuePublicationBulletinTests(EmailSystemTestCase):
    def test_publishing_an_issue_triggers_exactly_one_bulletin_send(self):
        import subscriptions
        s, t = subscriptions.subscribe("issuefan@example.com", {"new_issue": True})
        subscriptions.confirm(t)
        issues = self.load("issues.json")
        issues.append({
            "id": "sayi-02", "no": 2, "title": "Sayı 2", "date": "2026-02-01",
            "description": "", "cover_image": None, "pdf": "https://matbaa.eurovillageherald.com/x.pdf",
            "pages": None, "status": "published", "announcement_sent_at": None,
        })
        self.save("issues.json", issues)
        self.client.get("/gazete")
        self.client.get("/gazete")  # a second load must not duplicate the campaign
        bulletins_data = [b for b in self.load("bulletins.json") if b.get("source_issue_id") == "sayi-02"]
        self.assertEqual(len(bulletins_data), 1)
        self.assertEqual(bulletins_data[0]["status"], "sent")
        jobs = [j for j in self.load("email_outbox.json") if j.get("campaign_id") == bulletins_data[0]["id"]]
        self.assertEqual(len(jobs), 1)

    def test_issue_publication_succeeds_even_if_email_provider_is_down(self):
        import mailer
        original = mailer.send_bulletin_email
        mailer.send_bulletin_email = lambda *a, **k: (False, "simulated outage")
        try:
            issues = self.load("issues.json")
            issues.append({
                "id": "sayi-03", "no": 3, "title": "Sayı 3", "date": "2026-03-01",
                "description": "", "cover_image": None, "pdf": "https://matbaa.eurovillageherald.com/x.pdf",
                "pages": None, "status": "published", "announcement_sent_at": None,
            })
            self.save("issues.json", issues)
            r = self.client.get("/gazete")
            self.assertEqual(r.status_code, 200)
            updated = next(i for i in self.load("issues.json") if i["id"] == "sayi-03")
            self.assertIsNotNone(updated["announcement_sent_at"])
        finally:
            mailer.send_bulletin_email = original


class PermissionBoundaryTests(EmailSystemTestCase):
    """Legacy name kept for the two tests that predate the Master-Admin
    hardening pass; the exhaustive per-route matrix lives in
    MasterAdminAuthorizationTests below."""

    def test_internal_cron_endpoint_404s_when_token_unset(self):
        r = self.client.post("/internal/e-posta/isle")
        self.assertEqual(r.status_code, 404)

    def test_master_admin_always_has_bulletin_access(self):
        self.login_as("master1")
        r = self.client.get("/admin/bultenler")
        self.assertEqual(r.status_code, 200)


class MasterAdminAuthorizationTests(EmailSystemTestCase):
    """The hardening pass this class exists for: every privileged surface
    introduced or extended by the communication system (newspaper/issue
    administration, bulletin/email administration, subscriber
    administration, contact/ticket administration) must be reachable ONLY
    by an account whose account_role is literally "master_admin" --
    never via a delegable permission, however that permission is named or
    however a role record happens to be configured on disk.

    Four non-master-admin principals are exercised against every route:
      - "poweruser1": every CURRENT PERMISSION_CHOICES entry granted, plus
        the three RETIRED permission strings ("newspaper"/"bulletins"/
        "messages") a role saved before this hardening pass might still
        carry -- proves those strings are inert, not just unassigned.
      - "custom1": a role with zero permissions at all.
      - "user1": a genuine author/contributor account (account_role
        "author", tied to a real author profile) -- the ordinary
        newsroom-user case, distinct from a custom admin role.
      - no login at all -- must be redirected to the login page, never
        shown the protected page or its data.

    A master_admin GET must not be blocked (some also assert real content
    to catch accidental over-restriction); master_admin's ability to
    actually perform each POST action is already exercised in depth by
    BulletinCmsTests/ContactTicketTests/AdminTemplateRenderTests/
    IssuePublicationBulletinTests elsewhere in this file, so POST rows
    here focus on the authorization boundary itself.
    """

    DENIED_PRINCIPALS = ("poweruser1", "custom1", "user1")

    def _setup_fixtures(self):
        import subscriptions
        import bulletins
        import drafts as drafts_mod

        s, t = subscriptions.subscribe("authtest@example.com", {"new_issue": True})
        subscriptions.confirm(t)

        bulletin = bulletins.create_draft("custom", "editor@test.com", "Auth Test Bulletin", "Subject",
                                           article_slugs=["existing-article"],
                                           lead_article_slug="existing-article")

        issue = store.get_issue("sayi-01")
        draft = drafts_mod.generate_issue_announcement_if_needed(issue)

        self.client.post("/iletisim", data={"email": "tickettest@example.com", "category": "Diğer",
                                             "message": "Auth test ticket."})
        message = self.load("messages.json")[0]

        return {
            "issue_id": "sayi-01",
            "bulletin_id": bulletin["id"],
            "draft_id": draft["id"] if draft else "no-draft",
            "mid": message["id"],
            "idx": 0,
        }

    def _protected_routes(self, ids):
        """(method, path) pairs for every route this hardening pass makes
        master_admin_required. GET-only where a route is read-only;
        state-changing routes are POST and deliberately exercised with a
        real target id so a 403 isn't masked by an incidental 404."""
        return [
            # -- newspaper / issue administration --
            ("GET", "/admin/sayilar"),
            ("GET", "/admin/sayi/yeni"),
            ("POST", "/admin/sayi/yeni"),
            ("GET", f"/admin/sayi/{ids['issue_id']}/duzenle"),
            ("POST", f"/admin/sayi/{ids['issue_id']}/duzenle"),
            ("POST", f"/admin/sayi/{ids['issue_id']}/sil"),
            ("POST", f"/admin/sayi/{ids['issue_id']}/onizleme/olustur"),
            ("POST", f"/admin/sayi/{ids['issue_id']}/onizleme/iptal"),
            ("GET", "/admin/gazete-analitik"),
            ("GET", f"/admin/sayi/{ids['issue_id']}/analitik"),
            ("GET", "/admin/taslaklar"),
            ("GET", f"/admin/taslaklar/{ids['draft_id']}"),
            ("POST", f"/admin/taslaklar/{ids['draft_id']}/kullanildi"),
            ("POST", f"/admin/taslaklar/{ids['draft_id']}/reddet"),
            # -- bulletin / email administration --
            ("GET", "/admin/bultenler"),
            ("GET", "/admin/bultenler/yeni"),
            ("POST", "/admin/bultenler/yeni"),
            ("GET", f"/admin/bultenler/{ids['bulletin_id']}/duzenle"),
            ("POST", f"/admin/bultenler/{ids['bulletin_id']}/duzenle"),
            ("GET", f"/admin/bultenler/{ids['bulletin_id']}/onizle"),
            ("POST", f"/admin/bultenler/{ids['bulletin_id']}/test-gonder"),
            ("GET", f"/admin/bultenler/{ids['bulletin_id']}/gonder-onayla"),
            ("POST", f"/admin/bultenler/{ids['bulletin_id']}/gonder"),
            ("POST", f"/admin/bultenler/{ids['bulletin_id']}/iptal"),
            ("GET", "/admin/eposta-sistemi"),
            ("POST", "/admin/eposta-sistemi/yeniden-dene/does-not-exist"),
            ("POST", "/admin/eposta-sistemi/kuyruk-isle"),
            # -- subscriber administration --
            ("GET", "/admin/aboneler"),
            # -- contact / ticket administration --
            ("GET", "/admin/mesajlar"),
            ("POST", f"/admin/mesaj/{ids['mid']}/durum"),
            ("POST", f"/admin/mesaj/{ids['mid']}/cozum"),
            (
                "GET",
                f"/admin/mesaj/{ids['mid']}/gorsel/{ids['idx']}",
            ),
            ("POST", f"/admin/mesaj/{ids['mid']}/sil"),
        ]

    def _call(self, method, path):
        if method == "GET":
            return self.client.get(path)
        return self.client.post(path, data={})

    def test_master_admin_is_never_blocked(self):
        ids = self._setup_fixtures()
        self.login_as("master1")
        for method, path in self._protected_routes(ids):
            r = self._call(method, path)
            self.assertNotEqual(r.status_code, 403, f"master_admin got 403 on {method} {path}")

    def test_poweruser_role_with_every_current_and_legacy_permission_is_denied(self):
        """The critical case: a role granted every real permission choice
        PLUS the retired "newspaper"/"bulletins"/"messages" strings must
        still be denied -- proves the old flags are dead, not dormant."""
        ids = self._setup_fixtures()
        self.login_as("poweruser1")
        for method, path in self._protected_routes(ids):
            r = self._call(method, path)
            self.assertEqual(r.status_code, 403, f"poweruser got past auth on {method} {path}")

    def test_zero_permission_custom_role_is_denied(self):
        ids = self._setup_fixtures()
        self.login_as("custom1")
        for method, path in self._protected_routes(ids):
            r = self._call(method, path)
            self.assertEqual(r.status_code, 403, f"custom role got past auth on {method} {path}")

    def test_author_contributor_is_denied(self):
        ids = self._setup_fixtures()
        self.login_as("user1")
        for method, path in self._protected_routes(ids):
            r = self._call(method, path)
            self.assertEqual(r.status_code, 403, f"author got past auth on {method} {path}")

    def test_unauthenticated_is_redirected_to_login_not_shown_content(self):
        ids = self._setup_fixtures()
        for method, path in self._protected_routes(ids):
            r = self._call(method, path)
            self.assertIn(r.status_code, (302, 401, 403), f"unauthenticated got {r.status_code} on {method} {path}")
            if r.status_code == 302:
                self.assertIn("/admin/login", r.headers.get("Location", ""), f"{method} {path}")

    def test_sensitive_admin_nav_hidden_for_non_master_admin(self):
        labels = ["Bültenler", "E-posta Sistemi", "Gazete Sayıları", "İletişim / Talepler", "Aboneler"]
        for user_id in ("poweruser1", "custom1", "user1"):
            self.login_as(user_id)
            r = self.client.get("/admin/")
            body = r.data.decode("utf-8")
            for label in labels:
                self.assertNotIn(label, body, f"{user_id} sees a privileged nav link: {label}")

    def test_subscriber_data_never_leaked_to_non_master_admin(self):
        import subscriptions
        subscriptions.subscribe("secretsubscriber@example.com", {"new_issue": True})
        for user_id in ("poweruser1", "custom1", "user1"):
            self.login_as(user_id)
            r = self.client.get("/admin/aboneler")
            self.assertEqual(r.status_code, 403)
            self.assertNotIn(b"secretsubscriber@example.com", r.data)

    def test_bulletin_audience_data_never_leaked_to_non_master_admin(self):
        import bulletins
        b = bulletins.create_draft("custom", "editor@test.com", "Leak Test", "Subj")
        for user_id in ("poweruser1", "custom1", "user1"):
            self.login_as(user_id)
            r = self.client.get(f"/admin/bultenler/{b['id']}/gonder-onayla")
            self.assertEqual(r.status_code, 403)

    def test_email_system_internals_never_leaked_to_non_master_admin(self):
        import outbox
        outbox.enqueue("transactional", "internal@example.com", "Subj", "text")
        for user_id in ("poweruser1", "custom1", "user1"):
            self.login_as(user_id)
            r = self.client.get("/admin/eposta-sistemi")
            self.assertEqual(r.status_code, 403)
            self.assertNotIn(b"internal@example.com", r.data)

    def test_private_ticket_and_attachment_never_leaked_to_non_master_admin(self):
        self.client.post("/iletisim", data={"email": "private@example.com", "category": "Diğer",
                                             "message": "Confidential tip content."})
        for user_id in ("poweruser1", "custom1", "user1"):
            self.login_as(user_id)
            r = self.client.get("/admin/mesajlar")
            self.assertEqual(r.status_code, 403)
            self.assertNotIn(b"private@example.com", r.data)
            self.assertNotIn(b"Confidential tip content.", r.data)


class PrivacyPageTests(EmailSystemTestCase):
    def test_privacy_notice_page_loads(self):
        r = self.client.get("/gizlilik")
        self.assertEqual(r.status_code, 200)

    def test_contact_form_does_not_auto_subscribe(self):
        self.client.post("/iletisim", data={"email": "notasub@example.com", "category": "Diğer", "message": "hi"},
                          follow_redirects=True)
        self.assertEqual(len(self.load("issue_subscriptions.json")), 0)

    def test_contact_form_has_no_newsletter_consent_checkbox(self):
        """Contact and newsletter consent must never be merged into one
        checkbox -- the contact form must not even offer/require a
        newsletter-flavored consent control."""
        r = self.client.get("/iletisim")
        body = r.data.decode("utf-8")
        self.assertNotIn('name="pref_new_issue"', body)
        self.assertNotIn('name="privacy_ack"', body)

    def test_privacy_notice_explains_fictional_context(self):
        r = self.client.get("/gizlilik")
        body = r.data.decode("utf-8")
        for phrase in ("kurgusal", "Minecraft", "Eurovillage"):
            self.assertIn(phrase, body)

    def test_privacy_notice_does_not_falsely_claim_no_data_is_collected(self):
        r = self.client.get("/gizlilik")
        body = r.data.decode("utf-8")
        # The page must not contain a blanket "we collect nothing" claim.
        self.assertNotIn("hiçbir veri toplamıyoruz", body)
        self.assertNotIn("hiçbir kişisel veri toplanmaz", body)
        self.assertNotIn("veri toplamıyoruz", body)
        # It must instead explicitly acknowledge the real categories.
        self.assertIn("e-posta adresi", body)
        self.assertIn("İletişim formu", body)

    def test_privacy_notice_discloses_turnstile(self):
        r = self.client.get("/gizlilik")
        body = r.data.decode("utf-8")
        self.assertIn("Turnstile", body)
        self.assertIn("Cloudflare", body)

    def test_privacy_notice_keeps_legal_review_placeholders(self):
        r = self.client.get("/gizlilik")
        body = r.data.decode("utf-8")
        self.assertIn("YASAL İNCELEME GEREKLİ", body)

    def test_privacy_notice_analytics_wording_matches_actual_implementation(self):
        r = self.client.get("/gizlilik")
        body = r.data.decode("utf-8")
        # Must describe what analytics.py actually records...
        self.assertIn("cihaz kategorisi", body)
        self.assertIn("alan adı", body)
        self.assertIn("okuyucu belirteci", body)
        # ...and must explicitly deny (not merely omit) invasive collection
        # it does not actually do.
        self.assertIn("saklamaz", body)  # "IP adresinizi ... saklamaz"
        self.assertNotIn("parmak izi çıkarır", body)


class AdminTemplateRenderTests(EmailSystemTestCase):
    """py_compile only catches Python syntax errors -- it says nothing
    about a Jinja template referencing an undefined variable or a bad
    url_for(), which surfaces only as a 500 at request time. These tests
    exist purely to actually render every new admin template at least
    once (GET, and where relevant a real create->edit->preview->send
    -confirm chain) so a template bug can't slip through unnoticed."""

    def test_messages_list_all_views_render(self):
        self.login_as("master1")
        for view in ("open", "resolved", "all"):
            r = self.client.get(f"/admin/mesajlar?view={view}")
            self.assertEqual(r.status_code, 200, view)

    def test_subscribers_list_renders_with_consent_columns(self):
        import subscriptions
        subscriptions.subscribe("consenttest@example.com", {"new_issue": True})
        self.login_as("master1")
        r = self.client.get("/admin/aboneler")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"consenttest@example.com", r.data)

    def test_bulletin_new_form_renders_for_every_kind(self):
        import bulletins
        self.login_as("master1")
        for kind, _ in bulletins.BULLETIN_KINDS:
            r = self.client.get(f"/admin/bultenler/yeni?kind={kind}")
            self.assertEqual(r.status_code, 200, kind)

    def test_bulletin_new_form_renders_with_popular_stories_suggestions(self):
        import analytics
        analytics.record_article_view("existing-article")
        self.login_as("master1")
        r = self.client.get("/admin/bultenler/yeni?kind=popular_stories")
        self.assertEqual(r.status_code, 200)

    def test_bulletin_create_edit_preview_and_send_confirm_chain_renders(self):
        self.login_as("master1")
        r = self.client.post("/admin/bultenler/yeni", data={
            "kind": "custom", "internal_title": "Test Bulletin", "subject": "Subject Line",
            "preheader": "Preheader", "target_preference": "new_issue", "intro_text": "Intro.",
            "article_slugs": ["existing-article"], "lead_article_slug": "existing-article",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        bulletin_id = self.load("bulletins.json")[0]["id"]

        r = self.client.get(f"/admin/bultenler/{bulletin_id}/duzenle")
        self.assertEqual(r.status_code, 200)

        r = self.client.get(f"/admin/bultenler/{bulletin_id}/onizle")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"Subject Line", r.data)

        r = self.client.get(f"/admin/bultenler/{bulletin_id}/gonder-onayla")
        self.assertEqual(r.status_code, 200)

    def test_bulletin_test_send_via_http_enqueues_marked_job(self):
        self.login_as("master1")
        self.client.post("/admin/bultenler/yeni", data={
            "kind": "custom", "internal_title": "Test Bulletin", "subject": "Subject Line",
            "target_preference": "new_issue", "article_slugs": ["existing-article"],
            "lead_article_slug": "existing-article",
        })
        bulletin_id = self.load("bulletins.json")[0]["id"]
        r = self.client.post(f"/admin/bultenler/{bulletin_id}/test-gonder",
                              data={"test_addresses": "test1@example.com, test2@example.com"},
                              follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 2)
        self.assertTrue(all(j["subject"].startswith("[TEST]") for j in jobs))
        self.assertEqual(self.load("bulletins.json")[0]["status"], "draft")

    def test_bulletin_real_send_via_http_reaches_eligible_subscribers_only(self):
        import subscriptions
        s, t = subscriptions.subscribe("realrecipient@example.com", {"new_issue": True})
        subscriptions.confirm(t)
        subscriptions.subscribe("stillpending@example.com", {"new_issue": True})  # excluded

        self.login_as("master1")
        self.client.post("/admin/bultenler/yeni", data={
            "kind": "custom", "internal_title": "Real Send", "subject": "Real Subject",
            "target_preference": "new_issue", "article_slugs": ["existing-article"],
            "lead_article_slug": "existing-article",
        })
        bulletin_id = self.load("bulletins.json")[0]["id"]
        r = self.client.post(f"/admin/bultenler/{bulletin_id}/gonder", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        bulletin = self.load("bulletins.json")[0]
        self.assertEqual(bulletin["status"], "sent")
        self.assertEqual(bulletin["recipients_total"], 1)
        jobs = self.load("email_outbox.json")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["to"], "realrecipient@example.com")

    def test_bulletin_cancel_via_http(self):
        self.login_as("master1")
        self.client.post("/admin/bultenler/yeni", data={
            "kind": "custom", "internal_title": "To Cancel", "subject": "Subj",
            "target_preference": "new_issue",
        })
        bulletin_id = self.load("bulletins.json")[0]["id"]
        self.client.post(f"/admin/bultenler/{bulletin_id}/duzenle", data={
            "internal_title": "To Cancel", "subject": "Subj", "target_preference": "new_issue",
            "schedule_choice": "scheduled", "scheduled_at": "2099-01-01T10:00",
        })
        self.assertEqual(self.load("bulletins.json")[0]["status"], "scheduled")
        r = self.client.post(f"/admin/bultenler/{bulletin_id}/iptal", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.load("bulletins.json")[0]["status"], "cancelled")

    def test_email_system_health_page_renders(self):
        import outbox
        outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        self.login_as("master1")
        r = self.client.get("/admin/eposta-sistemi")
        self.assertEqual(r.status_code, 200)

    def test_email_system_health_never_exposes_api_key_in_html(self):
        import os as _os
        _os.environ["EMAIL_API_KEY"] = "super-secret-key-value"
        try:
            self.login_as("master1")
            r = self.client.get("/admin/eposta-sistemi")
            self.assertNotIn(b"super-secret-key-value", r.data)
        finally:
            _os.environ.pop("EMAIL_API_KEY", None)

    def test_email_process_now_and_retry_actions(self):
        import outbox
        import mailer
        original = mailer.send_transactional_email
        mailer.send_transactional_email = lambda *a, **k: (False, "outage")
        job = outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        for _ in range(outbox.MAX_ATTEMPTS):
            outbox.process_outbox(limit=10)
        mailer.send_transactional_email = original
        self.login_as("master1")
        r = self.client.get("/admin/eposta-sistemi")
        self.assertIn(b"a@example.com", r.data)
        r = self.client.post(f"/admin/eposta-sistemi/yeniden-dene/{job['id']}", follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.load("email_outbox.json")[0]["status"], "sent")

    def test_subscription_manage_page_renders_and_updates(self):
        import subscriptions
        s, t = subscriptions.subscribe("manage@example.com", {"new_issue": True})
        confirmed = subscriptions.confirm(t)
        token = subscriptions.manage_token(self.appmod.app.secret_key, confirmed["id"])
        r = self.client.get(f"/abone-ol/tercihler/{token}")
        self.assertEqual(r.status_code, 200)
        r = self.client.post(f"/abone-ol/tercihler/{token}", data={"pref_weekly_digest": "1"},
                              follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        updated = store.get_subscription_by_id(confirmed["id"])
        self.assertTrue(updated["preferences"]["weekly_digest"])
        self.assertFalse(updated["preferences"]["new_issue"])

    def test_unsubscribe_page_with_category_query_param_renders(self):
        import subscriptions
        s, t = subscriptions.subscribe("catpage@example.com", {"new_issue": True, "weekly_digest": True})
        confirmed = subscriptions.confirm(t)
        token = subscriptions.unsubscribe_token(self.appmod.app.secret_key, confirmed["id"])
        r = self.client.get(f"/abone-ol/cik/{token}?kategori=weekly_digest")
        self.assertEqual(r.status_code, 200)


class TurnstileSubscriptionTests(EmailSystemTestCase):
    """The subscribe form already reuses the project's one Turnstile
    helper (app/turnstile.py) via turnstile.check() inside
    subscribe_submit() -- the same pattern the contact form and the
    site-wide visitor gate already use. These tests turn Turnstile
    "on" for the duration of each test by patching turnstile.ENABLED and
    turnstile.verify_form (never a live network call to Cloudflare), and
    always restore both afterward so no test bleeds into another."""

    def setUp(self):
        super().setUp()
        import turnstile
        self._turnstile = turnstile
        self._orig_enabled = turnstile.ENABLED
        self._orig_verify_form = turnstile.verify_form
        self._orig_secret_key = turnstile.SECRET_KEY

    def tearDown(self):
        self._turnstile.ENABLED = self._orig_enabled
        self._turnstile.verify_form = self._orig_verify_form
        self._turnstile.SECRET_KEY = self._orig_secret_key

    def _enable_turnstile(self, verify_result):
        """verify_result: True/False for a deterministic outcome, or an
        exception instance to simulate a network/provider failure.

        turnstile.ENABLED also gates the unrelated site-wide visitor gate
        (app.py's enforce_visitor_gate()) -- that is correct production
        behavior (one flag, one Cloudflare setup), but it means turning
        Turnstile "on" for this test would otherwise make every request
        bounce off the gate page before ever reaching /abone-ol. Plant a
        valid gate cookie so these tests isolate the SUBSCRIBE FORM's own
        verification, which is what they're actually testing."""
        self._turnstile.ENABLED = True

        def fake_verify_form(form, remote_ip=None):
            if isinstance(verify_result, Exception):
                raise verify_result
            return verify_result
        self._turnstile.verify_form = fake_verify_form

        token = self.appmod._gate_serializer().dumps({"v": 1})
        self.client.set_cookie(self.appmod.GATE_COOKIE_NAME, token, domain="localhost")

    def _subscribe(self, email="turnstiletest@example.com", token="fake-turnstile-token"):
        data = {"email": email, "pref_new_issue": "1", "privacy_ack": "1"}
        if token is not None:
            data["cf-turnstile-response"] = token
        return self.client.post("/abone-ol", data=data, follow_redirects=True)

    def test_valid_turnstile_creates_pending_subscription(self):
        self._enable_turnstile(True)
        r = self._subscribe()
        self.assertEqual(r.status_code, 200)
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(len(subs), 1)
        self.assertEqual(subs[0]["status"], "pending")
        self.assertEqual(len(self.load("email_outbox.json")), 1)

    def test_invalid_turnstile_creates_no_subscription(self):
        self._enable_turnstile(False)
        r = self._subscribe()
        self.assertEqual(r.status_code, 200)  # safe redirect back, not a crash
        self.assertEqual(len(self.load("issue_subscriptions.json")), 0)
        self.assertEqual(len(self.load("email_outbox.json")), 0)

    def test_missing_token_is_rejected(self):
        # Deliberately does NOT use _enable_turnstile()'s blanket mock
        # (which would "verify" true regardless of the token) -- this
        # exercises the REAL turnstile.verify()'s "not token: return
        # False" short-circuit, so a missing token is rejected even
        # before any network call would be made.
        self._turnstile.ENABLED = True
        gate_token = self.appmod._gate_serializer().dumps({"v": 1})
        self.client.set_cookie(self.appmod.GATE_COOKIE_NAME, gate_token, domain="localhost")
        r = self._subscribe(token=None)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(self.load("issue_subscriptions.json")), 0)

    def test_provider_network_failure_fails_closed_safely(self):
        self._turnstile.ENABLED = True
        token = self.appmod._gate_serializer().dumps({"v": 1})
        self.client.set_cookie(self.appmod.GATE_COOKIE_NAME, token, domain="localhost")

        # turnstile.verify() itself catches network errors internally and
        # returns False -- exercise that real path (not the mocked
        # verify_form used elsewhere) to prove the actual fail-closed
        # behavior, not just a mock returning False.
        import urllib.request
        original_urlopen = urllib.request.urlopen

        def failing_urlopen(*a, **k):
            raise OSError("simulated network failure")
        urllib.request.urlopen = failing_urlopen
        try:
            self._turnstile.SECRET_KEY = "fake-secret-for-test"
            r = self._subscribe()
            self.assertEqual(r.status_code, 200)
            self.assertEqual(len(self.load("issue_subscriptions.json")), 0)
        finally:
            urllib.request.urlopen = original_urlopen

    def test_no_confirmation_email_queued_after_failed_turnstile(self):
        self._enable_turnstile(False)
        self._subscribe()
        self.assertEqual(len(self.load("email_outbox.json")), 0)

    def test_failed_turnstile_does_not_corrupt_an_existing_pending_record(self):
        import subscriptions
        sub, token = subscriptions.subscribe("existing@example.com", {"new_issue": True})
        before = store.get_subscription_by_id(sub["id"])

        self._enable_turnstile(False)
        self._subscribe(email="existing@example.com")

        after = store.get_subscription_by_id(sub["id"])
        self.assertEqual(before, after)

    def test_rate_limiting_still_applies_on_top_of_turnstile(self):
        self._enable_turnstile(True)
        for _ in range(5):
            self._subscribe(email=f"burst{_}@example.com")
        r = self._subscribe(email="oneMore@example.com")
        # the 6th attempt within the window must be rate-limited regardless
        # of a valid Turnstile token
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(len(subs), 5)

    def test_successful_turnstile_still_requires_double_opt_in(self):
        """Turnstile success must never itself confirm a subscription --
        double opt-in (clicking the emailed link) remains mandatory."""
        self._enable_turnstile(True)
        self._subscribe(email="doubleoptin@example.com")
        subs = self.load("issue_subscriptions.json")
        self.assertEqual(subs[0]["status"], "pending")
        self.assertNotIn(subs[0]["email"], [s["email"] for s in store.confirmed_subscribers("new_issue")])

    def test_turnstile_secret_never_appears_in_rendered_html(self):
        self._turnstile.SECRET_KEY = "super-secret-turnstile-key-value"
        r = self.client.get("/gazete")
        self.assertNotIn(b"super-secret-turnstile-key-value", r.data)

    def test_turnstile_site_key_appears_when_configured(self):
        # Jinja caches compiled templates keyed by name; a template already
        # rendered once by an earlier test has its `environment.globals`
        # baked into that cached Template object, so mutating the globals
        # dict afterward silently has no effect unless the cache is
        # cleared -- this mirrors how a real deploy only ever sets this
        # once at startup (before anything is compiled), never mid-process.
        globals_dict = self.appmod.app.jinja_env.globals
        original = globals_dict.get("turnstile_site_key")
        globals_dict["turnstile_site_key"] = "public-site-key-abc"
        self.appmod.app.jinja_env.cache.clear()
        try:
            r = self.client.get("/gazete")
            self.assertIn(b"public-site-key-abc", r.data)
            self.assertIn(b"cf-turnstile", r.data)
        finally:
            globals_dict["turnstile_site_key"] = original
            self.appmod.app.jinja_env.cache.clear()

    def test_disabled_turnstile_does_not_render_widget(self):
        globals_dict = self.appmod.app.jinja_env.globals
        original = globals_dict.get("turnstile_site_key")
        globals_dict["turnstile_site_key"] = None
        self.appmod.app.jinja_env.cache.clear()
        try:
            r = self.client.get("/gazete")
            self.assertNotIn(b"cf-turnstile", r.data)
        finally:
            globals_dict["turnstile_site_key"] = original
            self.appmod.app.jinja_env.cache.clear()


if __name__ == "__main__":
    unittest.main(verbosity=2)
