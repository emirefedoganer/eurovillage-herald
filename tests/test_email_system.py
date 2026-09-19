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
import unittest.mock

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
    write("site_control.json", {})


REDIRECTED_PATHS = {
    "ARTICLES_PATH": "articles.json", "ISSUES_PATH": "issues.json", "SITE_PATH": "site.json",
    "MESSAGES_PATH": "messages.json", "CROSSWORDS_PATH": "crosswords.json",
    "SUDOKUS_PATH": "sudokus.json", "USERS_PATH": "users.json", "AUTHORS_PATH": "authors.json",
    "AUDIT_LOG_PATH": "audit_log.json", "ROLES_PATH": "roles.json", "ADS_PATH": "ads.json",
    "AD_PLACEMENTS_PATH": "ad_placements.json", "MANAGEMENT_PATH": "newspaper_management.json",
    "ISSUE_SUBSCRIPTIONS_PATH": "issue_subscriptions.json", "ISSUE_ANALYTICS_PATH": "issue_analytics.json",
    "EDITORIAL_DRAFTS_PATH": "editorial_drafts.json", "EMAIL_OUTBOX_PATH": "email_outbox.json",
    "BULLETINS_PATH": "bulletins.json", "ARTICLE_VIEWS_PATH": "article_views.json",
    "SITE_CONTROL_PATH": "site_control.json",
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
        ok, error, message_id, permanent = mailer.send_transactional_email("reader@example.com", "Subj", "body text")
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertIsNone(message_id)
        self.assertFalse(permanent)

    def test_fake_bulletin_send_succeeds_without_network(self):
        import mailer
        ok, error, message_id, permanent = mailer.send_bulletin_email(
            "reader@example.com", "Subj", "body text", "<p>body</p>")
        self.assertTrue(ok)

    def test_sender_identities_never_exposes_api_key(self):
        import mailer
        identities = mailer.sender_identities()
        dumped = json.dumps(identities)
        self.assertNotIn("EMAIL_API_KEY", dumped)
        self.assertNotIn(mailer.EMAIL_API_KEY or "unset-marker", dumped) if mailer.EMAIL_API_KEY else None
        self.assertNotIn("password", dumped.lower())


class ResendBackendTests(EmailSystemTestCase):
    """The Resend transport, exercised entirely via unittest.mock.patch on
    resend.Emails.send -- NEVER a real network call to Cloudflare/Resend.

    Context: production logged five straight outbox delivery attempts
    each failing with "HTTP 403 error code: 1010" (Cloudflare: "request
    blocked based on the client's browser signature"), and nothing ever
    reached the Resend dashboard -- meaning Cloudflare's edge in front of
    api.resend.com blocked the request before Resend's own API ever saw
    it. The prior implementation spoke to the correct, official endpoint
    (https://api.resend.com/emails) but via raw urllib.request with no
    explicit User-Agent, which defaults to "Python-urllib/<version>" --
    a well-known Cloudflare bot-management trigger. Switching to the
    official SDK (which sends "resend-python:<version>" via `requests`,
    see resend/request.py) is the actual fix; these tests both prove the
    SDK is genuinely being used (not just imported) and cover the
    resulting success/permanent-failure/temporary-failure behavior.

    mailer.BACKEND is monkeypatched to "resend" for the duration of each
    test and restored in tearDown, the same pattern
    TurnstileSubscriptionTests already uses for turnstile.ENABLED."""

    def setUp(self):
        super().setUp()
        import mailer
        self._mailer = mailer
        self._orig_backend = mailer.BACKEND
        mailer.BACKEND = "resend"

    def tearDown(self):
        self._mailer.BACKEND = self._orig_backend

    # ---- regression: the SDK is actually used, not urllib -------------

    def test_official_endpoint_is_configured(self):
        import resend
        self.assertEqual(resend.api_url, "https://api.resend.com")

    def test_mailer_module_does_not_import_urllib(self):
        """The previous, broken implementation's smoking gun: no explicit
        User-Agent on a raw urllib.request call. Asserting the import
        statement itself is gone (rather than just that resend.Emails.send
        is called) makes sure nobody quietly reintroduces a second,
        parallel raw-HTTP path for some future case. Checked against the
        actual import lines, not a bare substring of the whole source --
        this module's docstring deliberately explains the old urllib bug
        in prose, which would otherwise trip a naive "urllib" not-in-source
        check on the module's own explanation of what was fixed."""
        import mailer
        with open(mailer.__file__, encoding="utf-8") as f:
            lines = [line.strip() for line in f]
        code_import_lines = [line for line in lines if line.startswith("import ") or line.startswith("from ")]
        self.assertFalse(any("urllib" in line for line in code_import_lines), code_import_lines)
        self.assertIn("import resend", code_import_lines)

    def test_email_api_key_env_var_is_not_renamed(self):
        """Requirement: EMAIL_API_KEY stays the Railway variable name --
        never RESEND_API_KEY -- even though the resend package itself
        defaults to reading RESEND_API_KEY from the environment. Checked
        against the actual os.environ.get(...) call, not a bare substring
        of the whole source -- this module's comments deliberately
        mention RESEND_API_KEY once, in prose, to explain precisely why
        it's NOT used."""
        import mailer
        with open(mailer.__file__, encoding="utf-8") as f:
            lines = f.readlines()
        env_read_lines = [line for line in lines if "os.environ.get(" in line]
        self.assertTrue(any('os.environ.get("EMAIL_API_KEY"' in line for line in env_read_lines))
        self.assertFalse(any('os.environ.get("RESEND_API_KEY"' in line for line in env_read_lines))

    def test_email_api_key_value_is_assigned_onto_the_sdk(self):
        import mailer
        import resend
        self.assertEqual(resend.api_key, mailer.EMAIL_API_KEY)

    def test_send_actually_invokes_the_official_sdk_call(self):
        """Not just 'the module is imported' -- the real code path calls
        resend.Emails.send(), the SDK's own documented entry point."""
        import mailer
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_mocked_12345"}) as mock_send:
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text body", html_body="<p>hi</p>",
            )
        mock_send.assert_called_once()
        called_params = mock_send.call_args[0][0]
        self.assertEqual(called_params["to"], ["reader@example.com"])
        self.assertEqual(called_params["subject"], "Subj")
        self.assertEqual(called_params["html"], "<p>hi</p>")
        self.assertTrue(ok)
        self.assertIsNone(error)
        self.assertEqual(message_id, "re_mocked_12345")
        self.assertFalse(permanent)

    # ---- success: the real message id is returned and stored ----------

    def test_successful_send_returns_real_resend_message_id(self):
        import mailer
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_abc123"}):
            ok, error, message_id, permanent = mailer.send_bulletin_email(
                "reader@example.com", "Subj", "text body")
        self.assertTrue(ok)
        self.assertEqual(message_id, "re_abc123")

    def test_successful_send_stores_message_id_on_the_outbox_job(self):
        import outbox
        outbox.enqueue("transactional", "reader@example.com", "Subj", "text")
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_stored_999"}):
            outbox.process_outbox(limit=10)
        job = store.load_email_outbox()[0]
        self.assertEqual(job["status"], "sent")
        self.assertEqual(job["provider_message_id"], "re_stored_999")

    # ---- permanent (4xx) failures: not retried indefinitely ------------

    def test_invalid_api_key_is_classified_permanent(self):
        import mailer
        import resend
        exc = resend.exceptions.InvalidApiKeyError(message="API key is invalid", error_type="invalid_api_key", code=403)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertFalse(ok)
        self.assertTrue(permanent)
        self.assertIsNone(message_id)

    def test_invalid_api_key_fails_the_outbox_job_on_first_attempt(self):
        """Requirement 14: a permanent 4xx must not be retried indefinitely
        -- specifically, it should fail on attempt ONE, not after burning
        through MAX_ATTEMPTS like a genuinely temporary failure would."""
        import outbox
        import resend
        outbox.enqueue("transactional", "reader@example.com", "Subj", "text")
        exc = resend.exceptions.InvalidApiKeyError(message="API key is invalid", error_type="invalid_api_key", code=403)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            outbox.process_outbox(limit=10)
        job = store.load_email_outbox()[0]
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["retry_count"], 1)
        self.assertLess(job["retry_count"], outbox.MAX_ATTEMPTS)

    def test_missing_api_key_is_classified_permanent(self):
        import mailer
        import resend
        exc = resend.exceptions.MissingApiKeyError(message="Missing API key", error_type="missing_api_key", code=401)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertTrue(permanent)

    def test_validation_error_eg_unverified_domain_is_classified_permanent(self):
        """Resend surfaces an unverified sending domain as a
        validation_error -- covered here as the task's explicit
        'unverified domains' example of a condition retrying can't fix."""
        import mailer
        import resend
        exc = resend.exceptions.ValidationError(
            message="The noreply@eurovillageherald.com domain is not verified.",
            error_type="validation_error", code=403,
        )
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertTrue(permanent)
        self.assertIn("not verified", error)

    def test_missing_required_fields_is_classified_permanent(self):
        import mailer
        import resend
        exc = resend.exceptions.MissingRequiredFieldsError(
            message="Missing `to` field.", error_type="missing_required_field", code=422)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertTrue(permanent)

    # ---- temporary failures: still retried ------------------------------

    def test_rate_limit_is_classified_temporary(self):
        import mailer
        import resend
        exc = resend.exceptions.RateLimitError(message="Too many requests", error_type="rate_limit_exceeded", code=429)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertFalse(ok)
        self.assertFalse(permanent)

    def test_server_error_is_classified_temporary(self):
        import mailer
        import resend
        exc = resend.exceptions.ApplicationError(message="Internal error", error_type="application_error", code=500)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertFalse(permanent)

    def test_ambiguous_non_json_403_stays_temporary_not_permanent(self):
        """The safety carve-out this backend relies on: if a 403 ever
        comes back WITHOUT a parseable Resend JSON body (error_type
        "application_error" is what the SDK assigns when it can't parse
        one -- e.g. an edge/proxy response rather than a confirmed
        rejection from Resend's own API, which is exactly the shape the
        original Cloudflare-1010 block would have taken had it reached
        this far), it must NOT be treated as a confirmed permanent
        rejection -- only a genuinely Resend-classified error type is."""
        import mailer
        import resend
        exc = resend.exceptions.ResendError(
            code=403, error_type="application_error",
            message="Expected JSON response but got: text/html", suggested_action="",
        )
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertFalse(ok)
        self.assertFalse(permanent)

    def test_temporary_failure_keeps_job_queued_for_retry(self):
        import outbox
        import resend
        outbox.enqueue("transactional", "reader@example.com", "Subj", "text")
        exc = resend.exceptions.RateLimitError(message="Too many requests", error_type="rate_limit_exceeded", code=429)
        with unittest.mock.patch("resend.Emails.send", side_effect=exc):
            outbox.process_outbox(limit=10)
        job = store.load_email_outbox()[0]
        self.assertEqual(job["status"], "queued")
        self.assertEqual(job["retry_count"], 1)

    def test_unrecognized_exception_defaults_to_temporary(self):
        """Anything outside the SDK's own exception hierarchy (a bug, an
        unexpected local error) must never be treated as a confirmed
        permanent rejection -- fail safe by staying retryable."""
        import mailer
        with unittest.mock.patch("resend.Emails.send", side_effect=RuntimeError("unexpected")):
            ok, error, message_id, permanent = mailer.send_transactional_email(
                "reader@example.com", "Subj", "text")
        self.assertFalse(ok)
        self.assertFalse(permanent)

    # ---- secrets never leak through error handling ----------------------

    def test_resend_error_never_exposes_the_api_key(self):
        import mailer
        import resend
        original_key = resend.api_key
        try:
            resend.api_key = "re_super_secret_test_key_value"
            exc = resend.exceptions.InvalidApiKeyError(
                message="API key is invalid", error_type="invalid_api_key", code=403)
            with unittest.mock.patch("resend.Emails.send", side_effect=exc):
                ok, error, message_id, permanent = mailer.send_transactional_email(
                    "reader@example.com", "Subj", "text")
            self.assertNotIn("re_super_secret_test_key_value", error)
        finally:
            resend.api_key = original_key

    # ---- preserved flows: subscription/contact/bulletin all still work --

    def test_subscription_confirmation_flow_works_through_resend(self):
        # Deliberately NOT follow_redirects=True: subscribe_submit()
        # redirects to /gazete, whose route opportunistically drains the
        # outbox (_process_due_issues() -> outbox.process_outbox()) --
        # following that redirect would send the enqueued job through
        # process_outbox() before this test's mock is even in scope,
        # making a REAL network call to Resend with no configured API
        # key. Keeping the whole request+drain inside one mock context
        # (belt and suspenders with not following the redirect) is what
        # actually guarantees no real network call ever happens here.
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_sub_confirm"}):
            r = self.client.post("/abone-ol", data={
                "email": "resendflow@example.com", "pref_new_issue": "1", "privacy_ack": "1",
            })
            self.assertEqual(r.status_code, 302)
            import outbox
            outbox.process_outbox(limit=10)
        jobs = self.load("email_outbox.json")
        self.assertEqual(jobs[0]["status"], "sent")
        self.assertEqual(jobs[0]["provider_message_id"], "re_sub_confirm")

    def test_contact_form_acknowledgement_flow_works_through_resend(self):
        # /iletisim's own redirect target (GET /iletisim) never drains the
        # outbox, unlike /abone-ol's -- but the whole request is still
        # kept inside the mock context as a matter of course, so nothing
        # here could ever make a real network call even if that changed.
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_contact_ack"}):
            self.client.post("/iletisim", data={
                "email": "resendcontact@example.com", "category": "Diğer", "message": "Test via Resend.",
            })
            import outbox
            outbox.process_outbox(limit=10)
        jobs = self.load("email_outbox.json")
        self.assertEqual(jobs[0]["status"], "sent")
        self.assertEqual(jobs[0]["provider_message_id"], "re_contact_ack")

    def test_bulletin_send_works_through_resend(self):
        import subscriptions
        import bulletins
        s, t = subscriptions.subscribe("resendbulletin@example.com", {"new_issue": True})
        subscriptions.confirm(t)
        b = bulletins.create_draft("custom", "editor@test.com", "T", "S", target_preference="new_issue")
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_bulletin_1"}):
            bulletins.send_campaign(b["id"], "editor@test.com", lambda bul, sub: ("Subj", "text", "<p>x</p>"))
            import outbox
            outbox.process_outbox(limit=10)
        jobs = self.load("email_outbox.json")
        self.assertEqual(jobs[0]["status"], "sent")
        self.assertEqual(jobs[0]["provider_message_id"], "re_bulletin_1")

    def test_test_send_still_works_through_resend(self):
        import bulletins
        import outbox
        b = bulletins.create_draft("custom", "editor@test.com", "T", "S")
        bulletins.send_test(b["id"], ["tester@example.com"], lambda bul, sub: ("Subj", "text", "<p>x</p>"))
        with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_test_send"}):
            outbox.process_outbox(limit=10)
        jobs = self.load("email_outbox.json")
        self.assertEqual(jobs[0]["status"], "sent")
        self.assertEqual(store.get_bulletin(b["id"])["status"], "draft")

    def test_no_network_module_is_ever_touched_by_this_class(self):
        """Belt-and-suspenders: every test in this class mocks
        resend.Emails.send directly, so urllib/requests/socket are never
        exercised -- verified here by patching resend's own HTTP client
        to raise if it's ever actually invoked, then running a normal
        mocked send and confirming that patch was never triggered."""
        import mailer
        import resend
        with unittest.mock.patch.object(
            resend.default_http_client, "request",
            side_effect=AssertionError("a real HTTP client was invoked during a mocked test"),
        ):
            with unittest.mock.patch("resend.Emails.send", return_value={"id": "re_ok"}):
                ok, error, message_id, permanent = mailer.send_transactional_email(
                    "reader@example.com", "Subj", "text")
        self.assertTrue(ok)


def _elapse_backoff():
    """Simulate the retry backoff window having passed."""
    jobs = store.load_email_outbox()
    for j in jobs:
        j["next_attempt_at"] = None
    store.save_email_outbox(jobs)


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
        mailer.send_transactional_email = lambda *a, **k: (False, "simulated provider outage", None, False)
        try:
            outbox.enqueue("transactional", "a@example.com", "Subj", "text")
            for _ in range(outbox.MAX_ATTEMPTS):
                outbox.process_outbox(limit=10)
                _elapse_backoff()
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
        mailer.send_transactional_email = lambda *a, **k: (False, "simulated outage", None, False)
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
        mailer.send_bulletin_email = lambda *a, **k: (False, "simulated outage", None, False)
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

    def test_privacy_notice_has_no_development_placeholders(self):
        r = self.client.get("/gizlilik")
        body = r.data.decode("utf-8")
        for marker in ("YASAL İNCELEME GEREKLİ", "yalnızca geliştirme amaçlı", "yer tutucu"):
            self.assertNotIn(marker, body)
        self.assertIn("iletisim@eurovillageherald.com", body)
        self.assertIn("Resend", body)

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
        mailer.send_transactional_email = lambda *a, **k: (False, "outage", None, False)
        job = outbox.enqueue("transactional", "a@example.com", "Subj", "text")
        for _ in range(outbox.MAX_ATTEMPTS):
            outbox.process_outbox(limit=10)
            _elapse_backoff()
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


class NewsletterVisibilityTests(EmailSystemTestCase):
    """The subscription backend (double opt-in, Turnstile, rate limiting,
    preferences) already existed and is exercised in depth elsewhere in
    this file -- these tests only check that it's actually reachable from
    the public site, and that every entry point posts to the SAME
    subscribe_submit() endpoint rather than a second, competing form."""

    def test_main_nav_does_not_have_a_subscribe_link(self):
        """Newsletter subscription must never appear in the public nav bar
        or header -- only in the editorial placements below (homepage
        right column, article end, its own landing page, optional
        Standby form)."""
        r = self.client.get("/")
        body = r.data.decode("utf-8")
        nav_start = body.index('<nav class="mainnav">')
        nav_end = body.index('</nav>', nav_start)
        self.assertNotIn("Abone Ol", body[nav_start:nav_end])
        drawer_start = body.index('id="mobile-drawer"')
        drawer_end = body.index('</nav>', drawer_start)
        self.assertNotIn("Abone Ol", body[drawer_start:drawer_end])

    def test_footer_has_a_subscribe_link_on_an_unrelated_page(self):
        r = self.client.get("/hakkimizda")
        body = r.data.decode("utf-8")
        self.assertIn('href="/bultene-abone-ol"', body)

    def test_standalone_subscribe_landing_page_renders_the_real_form(self):
        r = self.client.get("/bultene-abone-ol")
        self.assertEqual(r.status_code, 200)
        body = r.data.decode("utf-8")
        self.assertIn(f'action="/abone-ol"', body)
        self.assertIn('name="email"', body)
        self.assertIn('name="privacy_ack"', body)

    def test_homepage_has_a_dedicated_subscribe_section(self):
        r = self.client.get("/")
        body = r.data.decode("utf-8")
        self.assertIn('class="subscribe-widget', body)
        self.assertIn('action="/abone-ol"', body)

    def test_article_page_has_a_compact_subscribe_cta(self):
        r = self.client.get("/makale/existing-article")
        body = r.data.decode("utf-8")
        self.assertIn("article-subscribe-cta", body)
        self.assertIn("subscribe-widget-compact", body)
        self.assertIn('action="/abone-ol"', body)

    def test_every_entry_point_posts_to_the_one_real_subscribe_endpoint(self):
        """No competing/duplicate subscription implementation -- every
        instance of the form, wherever it's embedded, targets the exact
        same route."""
        for path in ("/", "/makale/existing-article", "/gazete", "/bultene-abone-ol"):
            r = self.client.get(path)
            body = r.data.decode("utf-8")
            form_count = body.count('action="/abone-ol"')
            self.assertGreaterEqual(form_count, 1, path)

    def test_gazete_page_still_has_its_existing_widget_unchanged(self):
        r = self.client.get("/gazete")
        body = r.data.decode("utf-8")
        self.assertIn('action="/abone-ol"', body)
        self.assertIn('name="privacy_ack"', body)

    def test_standby_page_has_no_subscribe_form_by_default(self):
        import store
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "standby"
        store.save_site_control(control)
        try:
            r = self.client.get("/")
            self.assertEqual(r.status_code, 503)
            body = r.data.decode("utf-8")
            # Checked against the actual form markup, not a bare
            # substring -- the "standby-subscribe" CSS rules themselves
            # are always present in <style>, whether or not the form's
            # HTML is ever rendered.
            self.assertNotIn('class="standby-subscribe"', body)
            self.assertNotIn('action="/abone-ol"', body)
        finally:
            control["mode"] = "live"
            store.save_site_control(control)

    def test_standby_page_shows_subscribe_form_when_enabled(self):
        import store
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "standby"
        control["standby_show_subscribe_form"] = True
        store.save_site_control(control)
        try:
            r = self.client.get("/")
            self.assertEqual(r.status_code, 503)
            body = r.data.decode("utf-8")
            self.assertIn('class="standby-subscribe"', body)
            self.assertIn('action="/abone-ol"', body)
        finally:
            control["mode"] = "live"
            control["standby_show_subscribe_form"] = False
            store.save_site_control(control)

    def test_standby_subscribe_form_actually_creates_a_pending_subscription(self):
        """The form is exempted from the standby gate specifically so it
        actually works -- not just visible, functional."""
        import store
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "standby"
        control["standby_show_subscribe_form"] = True
        store.save_site_control(control)
        try:
            r = self.client.post("/abone-ol", data={
                "email": "standbysignup@example.com", "pref_new_issue": "1", "privacy_ack": "1",
            })
            self.assertEqual(r.status_code, 302)
            subs = [s for s in store.load_subscriptions() if s["email"] == "standbysignup@example.com"]
            self.assertEqual(len(subs), 1)
            self.assertEqual(subs[0]["status"], "pending")
        finally:
            control["mode"] = "live"
            control["standby_show_subscribe_form"] = False
            store.save_site_control(control)

    def test_subscribe_endpoint_stays_gated_during_standby_when_form_disabled(self):
        import store
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "standby"
        control["standby_show_subscribe_form"] = False
        store.save_site_control(control)
        try:
            r = self.client.post("/abone-ol", data={
                "email": "shouldnotsignup@example.com", "pref_new_issue": "1", "privacy_ack": "1",
            })
            self.assertEqual(r.status_code, 503)
            subs = [s for s in store.load_subscriptions() if s["email"] == "shouldnotsignup@example.com"]
            self.assertEqual(len(subs), 0)
        finally:
            control["mode"] = "live"
            store.save_site_control(control)


class ReplyToEndToEndTests(EmailSystemTestCase):
    def _drain(self):
        import outbox
        outbox.process_outbox(limit=50)

    def test_resend_sdk_payload_carries_reply_to_and_untouched_from(self):
        import mailer
        import outbox
        captured = {}
        orig_send, orig_backends = mailer.resend.Emails.send, dict(mailer._BACKENDS)
        mailer.resend.Emails.send = staticmethod(lambda params: captured.update(params) or {"id": "msg_1"})
        mailer._BACKENDS["resend"] = mailer._send_via_resend
        orig_backend = mailer.BACKEND
        mailer.BACKEND = "resend"
        try:
            outbox.enqueue("transactional", "r@example.com", "S", "t", reply_to=mailer.EMAIL_CONTACT_REPLY_TO)
            self._drain()
        finally:
            mailer.resend.Emails.send = orig_send
            mailer.BACKEND = orig_backend
        self.assertEqual(captured["reply_to"], ["iletisim@eurovillageherald.com"])
        self.assertEqual(captured["from"], mailer.EMAIL_TRANSACTIONAL_FROM)
        self.assertNotIn("iletisim", captured["from"])
        self.assertEqual(store.load_email_outbox()[0]["provider_message_id"], "msg_1")

    def test_reply_to_survives_outbox_storage_and_retry(self):
        import mailer
        import outbox
        orig = mailer.send_transactional_email
        seen = []
        def flaky(to, subject, text, html=None, reply_to=None):
            seen.append(reply_to)
            return (len(seen) > 1, "boom", None, False)
        mailer.send_transactional_email = flaky
        try:
            outbox.enqueue("transactional", "r@example.com", "S", "t", reply_to="iletisim@eurovillageherald.com")
            self._drain()
            self.assertEqual(store.load_email_outbox()[0]["status"], "queued")
            self.assertIsNotNone(store.load_email_outbox()[0]["next_attempt_at"])
            _elapse_backoff()
            self._drain()
        finally:
            mailer.send_transactional_email = orig
        self.assertEqual(seen, ["iletisim@eurovillageherald.com"] * 2)

    def test_subscription_emails_and_fake_backend_expose_reply_to(self):
        import mailer
        del mailer.FAKE_SENT[:]
        self.client.post("/abone-ol", data={"email": "rt@example.com", "privacy_ack": "1", "pref_new_issue": "1"})
        self._drain()
        sent = [m for m in mailer.FAKE_SENT if m["to"] == "rt@example.com"]
        self.assertTrue(sent)
        self.assertEqual(sent[0]["reply_to"], "iletisim@eurovillageherald.com")
        self.assertEqual(sent[0]["from"], mailer.EMAIL_TRANSACTIONAL_FROM)

    def test_bulletin_sends_use_bulletin_from_with_contact_reply_to(self):
        import mailer
        import outbox
        del mailer.FAKE_SENT[:]
        outbox.enqueue("bulletin", "b@example.com", "S", "t")
        self._drain()
        self.assertEqual(mailer.FAKE_SENT[-1]["from"], mailer.EMAIL_BULLETIN_FROM)
        self.assertEqual(mailer.FAKE_SENT[-1]["reply_to"], "iletisim@eurovillageherald.com")


class SubscriberStatesAndWebhookTests(EmailSystemTestCase):
    def _confirmed(self, email="w@example.com"):
        import subscriptions
        sub, token = subscriptions.subscribe(email, {"new_issue": True})
        subscriptions.confirm(token)
        return store.get_subscription_by_email(email)

    def _sign(self, body, secret="whsec_" + "dGVzdC1zZWNyZXQ="):
        import base64, hashlib, hmac, time
        ts = str(int(time.time()))
        key = base64.b64decode(secret.removeprefix("whsec_"))
        sig = base64.b64encode(hmac.new(key, f"msg_1.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
        return {"svix-id": "msg_1", "svix-timestamp": ts, "svix-signature": f"v1,{sig}",
                "Content-Type": "application/json"}

    def setUp(self):
        super().setUp()
        self.appmod.RESEND_WEBHOOK_SECRET = "whsec_dGVzdC1zZWNyZXQ="

    def tearDown(self):
        self.appmod.RESEND_WEBHOOK_SECRET = ""

    def test_bounce_and_complaint_webhooks_exclude_from_campaigns_and_are_idempotent(self):
        sub = self._confirmed("bo@example.com")
        body = json.dumps({"type": "email.bounced", "data": {"to": ["Bo@Example.com"], "email_id": "x"}}).encode()
        for _ in range(2):
            r = self.client.post("/internal/webhooks/resend", data=body, headers=self._sign(body))
            self.assertEqual(r.status_code, 200)
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "bounced")
        self.assertEqual(store.confirmed_subscribers("new_issue"), [])
        sub2 = self._confirmed("co@example.com")
        body = json.dumps({"type": "email.complained", "data": {"to": ["co@example.com"]}}).encode()
        self.client.post("/internal/webhooks/resend", data=body, headers=self._sign(body))
        self.assertEqual(store.get_subscription_by_id(sub2["id"])["status"], "complained")

    def test_unsigned_or_badly_signed_webhook_rejected(self):
        sub = self._confirmed("ns@example.com")
        body = json.dumps({"type": "email.bounced", "data": {"to": ["ns@example.com"]}}).encode()
        r = self.client.post("/internal/webhooks/resend", data=body)
        self.assertEqual(r.status_code, 400)
        bad = self._sign(body); bad["svix-signature"] = "v1,AAAA"
        self.assertEqual(self.client.post("/internal/webhooks/resend", data=body, headers=bad).status_code, 400)
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "confirmed")

    def test_webhook_404s_when_secret_unset(self):
        self.appmod.RESEND_WEBHOOK_SECRET = ""
        self.assertEqual(self.client.post("/internal/webhooks/resend", data=b"{}").status_code, 404)

    def test_send_time_eligibility_recheck_skips_unsubscribed_recipient(self):
        import outbox, subscriptions, mailer
        sub = self._confirmed("el@example.com")
        del mailer.FAKE_SENT[:]
        outbox.enqueue("bulletin", "el@example.com", "S", "t", subscriber_id=sub["id"], target_preference="new_issue")
        subscriptions.unsubscribe(sub["id"])  # between enqueue and send
        outbox.process_outbox(limit=10)
        self.assertEqual(store.load_email_outbox()[0]["status"], "skipped")
        self.assertEqual(mailer.FAKE_SENT, [])

    def test_public_form_cannot_reopen_a_bounced_or_suppressed_address(self):
        import subscriptions
        sub = self._confirmed("pb@example.com")
        subscriptions.record_bounce("pb@example.com")
        again, token = subscriptions.subscribe("pb@example.com", {"new_issue": True})
        self.assertIsNone(token)
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "bounced")

    def test_clear_suppression_sends_nothing_and_does_not_reenable(self):
        import subscriptions, mailer
        sub = self._confirmed("re@example.com")
        subscriptions.record_bounce("re@example.com", "mailbox gone")
        del mailer.FAKE_SENT[:]
        cleared = subscriptions.clear_suppression(sub["id"], "admin@x", "owner confirmed address works")
        self.assertEqual(cleared["status"], "unsubscribed")
        self.assertEqual(store.confirmed_subscribers("new_issue"), [])
        self.assertEqual(mailer.FAKE_SENT, [])
        self.assertEqual(cleared["suppression_cleared_by"], "admin@x")
        # only the owner's own public-form + double opt-in re-enables it
        again, token = subscriptions.subscribe("re@example.com", {"new_issue": True})
        self.assertEqual(again["status"], "pending")
        subscriptions.confirm(token)
        self.assertEqual(len(store.confirmed_subscribers("new_issue")), 1)

    def test_complaint_can_never_be_cleared_and_reason_is_required(self):
        import subscriptions
        sub = self._confirmed("cp@example.com")
        subscriptions.record_complaint("cp@example.com")
        self.assertIsNone(subscriptions.clear_suppression(sub["id"], "a", "please"))
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "complained")
        sub2 = self._confirmed("sp@example.com")
        subscriptions.suppress(sub2["id"], "a")
        self.assertIsNone(subscriptions.clear_suppression(sub2["id"], "a", "  "))

    def test_admin_clear_requires_ack_writes_audit_and_sends_no_mail(self):
        import subscriptions, mailer
        sub = self._confirmed("au@example.com")
        subscriptions.suppress(sub["id"], "a")
        self.login_as("master1")
        del mailer.FAKE_SENT[:]
        url = f"/admin/aboneler/{sub['id']}/engeli-kaldir"
        self.client.post(url, data={"reason": "ok"})
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "suppressed")
        self.client.post(url, data={"reason": "ok", "understood": "1"})
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "unsubscribed")
        self.assertEqual(mailer.FAKE_SENT, [])
        self.assertTrue(any(e.get("action") == "subscriber_suppression_cleared" for e in store.load_audit_log()))

    def test_unsubscribe_does_not_downgrade_blocked_states(self):
        import subscriptions
        sub = self._confirmed("ub@example.com")
        subscriptions.record_complaint("ub@example.com")
        subscriptions.unsubscribe(sub["id"])
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "complained")

    def test_transient_bounce_does_not_suppress(self):
        sub = self._confirmed("tb@example.com")
        body = json.dumps({"type": "email.bounced", "data": {"to": ["tb@example.com"],
                           "bounce": {"type": "Transient", "message": "full"}}}).encode()
        self.client.post("/internal/webhooks/resend", data=body, headers=self._sign(body))
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "confirmed")

    def test_eligibility_matrix_across_all_states(self):
        import subscriptions
        ids = {}
        for name in ("confirmed", "pending", "unsubscribed", "bounced", "complained", "suppressed"):
            sub, token = subscriptions.subscribe(f"{name}@example.com", {"new_issue": True})
            if name != "pending":
                subscriptions.confirm(token)
            ids[name] = sub["id"]
        subscriptions.unsubscribe(ids["unsubscribed"])
        subscriptions.record_bounce("bounced@example.com")
        subscriptions.record_complaint("complained@example.com")
        subscriptions.suppress(ids["suppressed"], "a")
        for name, sid in ids.items():
            self.assertEqual(store.is_subscriber_eligible(sid, "new_issue"), name == "confirmed", name)

    def test_admin_subscriber_page_renders_new_states(self):
        import subscriptions
        self._confirmed("ad@example.com")
        subscriptions.record_bounce("ad@example.com")
        self.login_as("master1")
        r = self.client.get("/admin/aboneler")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Engeli kaldır".encode(), r.data)


class BulletinFooterAndLinksTests(EmailSystemTestCase):
    def _bulletin(self):
        import bulletins
        return bulletins.create_draft("custom", "a@x", "Baslik", "Konu", target_preference="new_issue")

    def _real_subscriber(self, email="fw@example.com"):
        import subscriptions
        sub, token = subscriptions.subscribe(email, {"new_issue": True})
        subscriptions.confirm(token)
        return store.get_subscription_by_email(email)

    def _render(self, subscriber):
        with self.appmod.app.test_request_context("/", base_url="https://www.eurovillageherald.com"):
            return self.appmod._render_bulletin(self._bulletin(), subscriber)

    def test_test_bulletin_has_forward_cta_and_no_placeholders(self):
        subject, text, html, headers = self._render({"id": "test", "email": "t@example.com"})
        for body in (text, html):
            self.assertIn("Bu e-posta size yönlendirildi mi? Hemen bültenimize abone olun.", body)
            self.assertIn("https://www.eurovillageherald.com/bultene-abone-ol", body)
        self.assertIn("Hemen Bültenimize Abone Olun", html)
        self.assertNotIn('href="#"', html)
        self.assertNotIn("Tercihlerimi Yönet", html)
        self.assertIsNone(headers)

    def test_real_bulletin_has_personal_links_headers_and_no_forbidden_urls(self):
        sub = self._real_subscriber()
        subject, text, html, headers = self._render(sub)
        for body in (text, html):
            self.assertIn("/abone-ol/tercihler/", body)
            self.assertIn("/abone-ol/cik/", body)
            self.assertIn("/bultene-abone-ol", body)
            self.assertNotIn(sub["email"], body)
            self.assertNotIn(sub["id"], body)
            for bad in ("zoho", "zm/#", "javascript:", "railway.app", 'href="#"'):
                self.assertNotIn(bad, body.lower())
        self.assertIn("Tercihlerimi Yönet", html)
        self.assertIn("Abonelikten Çık", html)
        self.assertTrue(headers["List-Unsubscribe"].startswith("<https://www.eurovillageherald.com/abone-ol/cik/"))
        self.assertEqual(headers["List-Unsubscribe-Post"], "List-Unsubscribe=One-Click")
        # the forward CTA link carries no subscriber token
        cta = "https://www.eurovillageherald.com/bultene-abone-ol"
        self.assertNotIn(cta + "/", html)

    def test_tokens_are_opaque_and_subscriber_specific(self):
        import subscriptions
        a, b = self._real_subscriber("a1@example.com"), self._real_subscriber("b1@example.com")
        key = self.appmod.app.secret_key
        ta, tb = subscriptions.manage_token(key, a["id"]), subscriptions.manage_token(key, b["id"])
        self.assertNotEqual(ta, tb)
        self.assertNotIn(a["id"], ta)
        self.assertEqual(subscriptions.subscription_id_from_manage_token(key, ta), a["id"])
        self.assertIsNone(subscriptions.subscription_id_from_manage_token(key, subscriptions.unsubscribe_token(key, a["id"])))
        self.assertIsNone(subscriptions.subscription_id_from_manage_token(key, "0" * 40))

    def test_manage_page_shows_all_categories_and_changes_apply_to_eligibility(self):
        import subscriptions
        sub = self._real_subscriber()
        other = self._real_subscriber("other@example.com")
        token = subscriptions.manage_token(self.appmod.app.secret_key, sub["id"])
        r = self.client.get(f"/abone-ol/tercihler/{token}")
        self.assertEqual(r.status_code, 200)
        for _key, label in subscriptions.PREFERENCE_CHOICES:
            self.assertIn(label.encode(), r.data)
        self.assertNotIn(b"/abone-ol/tercihler/" + sub["id"].encode(), r.data)
        r = self.client.post(f"/abone-ol/tercihler/{token}", data={"pref_breaking_news": "1"}, follow_redirects=True)
        self.assertIn("Tercihleriniz güncellendi".encode(), r.data)
        self.assertFalse(store.is_subscriber_eligible(sub["id"], "new_issue"))
        self.assertTrue(store.is_subscriber_eligible(sub["id"], "breaking_news"))
        self.assertTrue(store.is_subscriber_eligible(other["id"], "new_issue"))  # untouched

    def test_unsubscribe_get_is_safe_post_is_direct_idempotent_and_leaks_nothing(self):
        import subscriptions
        sub = self._real_subscriber()
        token = subscriptions.unsubscribe_token(self.appmod.app.secret_key, sub["id"])
        r = self.client.get(f"/abone-ol/cik/{token}")  # link-preview bot / scanner
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(sub["email"].encode(), r.data)
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "confirmed")
        for _ in range(2):  # one-click POST, idempotent
            r = self.client.post(f"/abone-ol/cik/{token}", data={"List-Unsubscribe": "One-Click"})
            self.assertEqual(r.status_code, 200)
            self.assertIn("tamamlandı".encode(), r.data)
        self.assertEqual(store.get_subscription_by_id(sub["id"])["status"], "unsubscribed")
        self.assertEqual(store.confirmed_subscribers("new_issue"), [])
        self.assertNotIn(sub["email"].encode(), self.client.get(f"/abone-ol/cik/{token}").data)
        self.assertEqual(self.client.get("/abone-ol/cik/" + "f" * 40).status_code, 404)
        self.assertEqual(self.client.post("/abone-ol/cik/garbage").status_code, 404)

    def test_reply_to_and_headers_survive_outbox_retry_resend_payload_and_fake(self):
        import bulletins, mailer, outbox
        sub = self._real_subscriber()
        bulletin = self._bulletin()

        def render(b, s):
            with self.appmod.app.test_request_context("/", base_url="https://www.eurovillageherald.com"):
                return self.appmod._render_bulletin(b, s)

        bulletins.send_test(bulletin["id"], ["tester@example.com"], render)
        bulletins.send_campaign(bulletin["id"], "a@x", render)
        jobs = store.load_email_outbox()
        self.assertEqual(len(jobs), 2)
        for j in jobs:  # serialized in the durable outbox
            self.assertEqual(j["reply_to"], "iletisim@eurovillageherald.com")
        real = next(j for j in jobs if j["subscriber_id"])
        self.assertIn("List-Unsubscribe", real["headers"])

        captured = []
        orig_send, orig_backend = mailer.resend.Emails.send, mailer.BACKEND
        calls = {"n": 0}
        def flaky(params):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("temporary")
            captured.append(dict(params))
            return {"id": "m"}
        mailer.resend.Emails.send = staticmethod(flaky)
        mailer.BACKEND = "resend"
        try:
            outbox.process_outbox(limit=10)   # first job fails -> retry queued
            _elapse_backoff()
            outbox.process_outbox(limit=10)   # retry + remaining
        finally:
            mailer.resend.Emails.send = orig_send
            mailer.BACKEND = orig_backend
        self.assertEqual(len(captured), 2)
        for p in captured:
            self.assertEqual(p["reply_to"], ["iletisim@eurovillageherald.com"])
            self.assertEqual(p["from"], mailer.EMAIL_BULLETIN_FROM)
        self.assertTrue(any("List-Unsubscribe-Post" in p.get("headers", {}) for p in captured))

        del mailer.FAKE_SENT[:]
        outbox.enqueue("bulletin", "z@example.com", "S", "t", reply_to=mailer.EMAIL_CONTACT_REPLY_TO)
        outbox.process_outbox(limit=10)
        self.assertEqual(mailer.FAKE_SENT[-1]["reply_to"], "iletisim@eurovillageherald.com")
        self.assertEqual(mailer.FAKE_SENT[-1]["from"], mailer.EMAIL_BULLETIN_FROM)


class BulletinAudienceMappingTests(EmailSystemTestCase):
    def _extra_articles(self):
        arts = store.load_articles()
        base = dict(arts[0])
        def mk(i, slug, **kw):
            d = dict(base, id=f"x{i}", slug=slug, title=slug.title(), **kw)
            return d
        arts += [mk(1, "zeta-story"), mk(2, "alpha-story"), mk(3, "draft-story", status="draft"),
                 mk(4, "future-story", status="scheduled", publish_at="2999-01-01T00:00:00Z")]
        store.save_articles(arts)

    def _views(self, mapping):
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        store.save_article_views({slug: {today: n} for slug, n in mapping.items()})

    def test_every_type_has_exactly_the_documented_audience(self):
        import bulletins, subscriptions
        prefs = {k for k, _ in subscriptions.PREFERENCE_CHOICES}
        expected = {"new_issue": "new_issue", "weekly_digest": "weekly_digest", "popular_stories": "popular_stories",
                    "editorial_selection": "weekly_digest", "breaking_news": "breaking_news",
                    "ari_magazin": "ari_magazin", "custom": "new_issue"}
        self.assertEqual({k: bulletins.default_audience(k) for k in bulletins.BULLETIN_KIND_LABELS}, expected)
        for kind, spec in bulletins.KIND_AUDIENCE.items():
            self.assertTrue(set(spec["allowed"]) <= prefs)   # no second audience system
            self.assertEqual(len(spec["allowed"]) > 1, kind == "custom")

    def test_server_rejects_incompatible_type_audience_everywhere(self):
        import bulletins
        with self.assertRaises(bulletins.BulletinAudienceError):
            bulletins.create_draft("popular_stories", "a@x", "T", "S", target_preference="new_issue")
        b = bulletins.create_draft("popular_stories", "a@x", "T", "S")
        self.assertEqual(b["target_preference"], "popular_stories")
        with self.assertRaises(bulletins.BulletinAudienceError):
            bulletins.update_draft(b["id"], target_preference="breaking_news")
        c = bulletins.create_draft("custom", "a@x", "T", "S", target_preference="ari_magazin")
        self.assertEqual(c["target_preference"], "ari_magazin")     # custom may override
        self.login_as("master1")
        r = self.client.post("/admin/bultenler/yeni", data={"kind": "breaking_news", "internal_title": "t", "subject": "s",
                              "target_preference": "new_issue"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual([x for x in store.load_bulletins() if x["kind"] == "breaking_news"], [])
        r = self.client.post(f"/admin/bultenler/{b['id']}/duzenle", data={"kind": "popular_stories", "internal_title": "t",
                              "subject": "s", "target_preference": "new_issue"})
        self.assertEqual(store.get_bulletin(b["id"])["target_preference"], "popular_stories")

    def test_changing_type_on_edit_replaces_the_previous_audience(self):
        import bulletins
        self.login_as("master1")
        b = bulletins.create_draft("custom", "a@x", "T", "S", target_preference="ari_magazin")
        # a stale audience from the old type is REJECTED, nothing is saved
        self.client.post(f"/admin/bultenler/{b['id']}/duzenle", data={
            "kind": "breaking_news", "internal_title": "t", "subject": "s", "target_preference": "ari_magazin"})
        self.assertEqual(store.get_bulletin(b["id"])["kind"], "custom")
        # omitting the audience applies the new type's default instead of keeping the old one
        self.client.post(f"/admin/bultenler/{b['id']}/duzenle", data={
            "kind": "breaking_news", "internal_title": "t", "subject": "s"})
        self.assertEqual(store.get_bulletin(b["id"])["target_preference"], "breaking_news")
        self.assertEqual(store.get_bulletin(b["id"])["kind"], "breaking_news")

    def test_duplicate_applies_the_mapping_not_the_stale_source_audience(self):
        import bulletins
        self.login_as("master1")
        b = bulletins.create_draft("weekly_digest", "a@x", "T", "S")
        bl = store.load_bulletins(); bl[-1]["target_preference"] = "new_issue"; store.save_bulletins(bl)   # stale/legacy
        self.client.post(f"/admin/bultenler/{b['id']}/kopyala")
        copy = [x for x in store.load_bulletins() if x["id"] != b["id"]][-1]
        self.assertEqual(copy["target_preference"], "weekly_digest")

    def test_preview_flags_legacy_mismatch_and_send_is_blocked_scheduled_and_manual(self):
        import bulletins, outbox
        self._extra_articles()
        sub = subscriptions_confirmed = None
        import subscriptions
        s, t = subscriptions.subscribe("m@example.com", {"new_issue": True}); subscriptions.confirm(t)
        b = bulletins.create_draft("popular_stories", "a@x", "T", "S")
        bl = store.load_bulletins(); bl[-1]["target_preference"] = "new_issue"; store.save_bulletins(bl)
        self.login_as("master1")
        r = self.client.get(f"/admin/bultenler/{b['id']}/onizle")
        self.assertIn("UYUMSUZ".encode(), r.data)
        r = self.client.post(f"/admin/bultenler/{b['id']}/gonder", follow_redirects=True)
        self.assertEqual(store.load_email_outbox(), [])
        self.assertEqual(store.get_bulletin(b["id"])["status"], "draft")
        self.assertEqual(self.client.get(f"/admin/bultenler/{b['id']}/gonder-onayla").status_code, 302)
        bl = store.load_bulletins(); bl[-1]["status"] = "scheduled"; bl[-1]["scheduled_at"] = "2000-01-01T00:00:00Z"
        store.save_bulletins(bl)
        bulletins.process_scheduled_bulletins(lambda b_, s_: ("s", "t", "<p>h</p>"))
        self.assertEqual(store.load_email_outbox(), [])

    def test_estimate_endpoint_tracks_type_and_test_send_stays_on_entered_addresses(self):
        import bulletins, subscriptions, mailer
        for i, pref in enumerate(("new_issue", "breaking_news", "breaking_news")):
            s, t = subscriptions.subscribe(f"e{i}@example.com", {pref: True}); subscriptions.confirm(t)
        self.login_as("master1")
        d = self.client.get("/admin/bultenler/hedef-kitle?kind=breaking_news&audience=new_issue").get_json()
        self.assertEqual((d["audience"], d["eligible"]), ("breaking_news", 2))
        d = self.client.get("/admin/bultenler/hedef-kitle?kind=new_issue").get_json()
        self.assertEqual((d["audience"], d["eligible"]), ("new_issue", 1))
        b = bulletins.create_draft("breaking_news", "a@x", "T", "S")
        del mailer.FAKE_SENT[:]
        self.client.post(f"/admin/bultenler/{b['id']}/test-gonder", data={"test_addresses": "only@example.com"})
        outbox = store.load_email_outbox()
        self.assertEqual([j["to"] for j in outbox], ["only@example.com"])
        self.assertIsNone(outbox[0]["subscriber_id"])

    def test_form_renders_type_selector_and_only_allowed_audiences(self):
        self.login_as("master1")
        html = self.client.get("/admin/bultenler/yeni?kind=breaking_news").data.decode()
        self.assertIn('id="kind"', html)
        seg = html[html.index('id="target_preference"'):html.index("audience-estimate")]
        self.assertEqual(seg.count("<option"), 1)
        self.assertIn('value="breaking_news" selected', seg)
        custom = self.client.get("/admin/bultenler/yeni?kind=custom").data.decode()
        seg = custom[custom.index('id="target_preference"'):custom.index("audience-estimate")]
        self.assertEqual(seg.count("<option"), 5)

    def test_popular_ranking_uses_only_public_articles_ranked_deterministically(self):
        import bulletins
        self._extra_articles()
        self._views({"zeta-story": 5, "alpha-story": 5, "existing-article": 9, "draft-story": 100,
                     "future-story": 100, "removed-story": 100})
        r = bulletins.popular_ranking()
        self.assertEqual([i["slug"] for i in r["items"]], ["existing-article", "alpha-story", "zeta-story"])  # tie -> slug A-Z
        self.assertEqual([i["views"] for i in r["items"]], [9, 5, 5])
        self.assertIsNone(r["warning"])
        self.assertEqual(r["period_days"], 7)
        self.assertEqual(r, dict(r, items=bulletins.popular_ranking()["items"]))

    def test_period_boundary_is_seven_utc_days_inclusive_of_today(self):
        import analytics
        from datetime import date, timedelta
        today = date(2026, 9, 19)
        store.save_article_views({"s": {"2026-09-19": 1, "2026-09-13": 2, "2026-09-12": 100}})
        self.assertEqual(analytics.article_view_totals(7, today=today), {"s": 3})

    def test_insufficient_data_warns_and_never_invents_a_ranking(self):
        import bulletins
        self._views({"existing-article": 2})
        r = bulletins.popular_ranking()
        self.assertEqual(len(r["items"]), 1)
        self.assertIn("Yeterli okunma verisi yok", r["warning"])
        store.save_article_views({})
        self.assertEqual(bulletins.popular_ranking()["items"], [])

    def test_saved_selection_is_a_snapshot_and_refresh_changes_nothing_by_itself(self):
        import bulletins
        self._extra_articles()
        self._views({"existing-article": 9, "alpha-story": 5, "zeta-story": 4})
        self.login_as("master1")
        self.client.post("/admin/bultenler/yeni", data={
            "kind": "popular_stories", "internal_title": "t", "subject": "s", "analytics_refreshed": "1",
            "article_slugs": ["alpha-story", "existing-article"]})
        b = store.load_bulletins()[-1]
        self.assertEqual(b["article_slugs"], ["alpha-story", "existing-article"])   # editor's order kept
        self.assertEqual(b["analytics_snapshot"]["counts"], {"alpha-story": 5, "existing-article": 9})
        before = dict(b)
        self._views({"existing-article": 1, "zeta-story": 500})                      # analytics move on
        self.client.get("/admin/bultenler/populer-oneri")                            # "Analitikten Yenile"
        self.assertEqual(store.get_bulletin(b["id"])["analytics_snapshot"], before["analytics_snapshot"])
        self.assertEqual(store.get_bulletin(b["id"])["status"], "draft")
        self.assertEqual(store.load_email_outbox(), [])
        # saving WITHOUT pressing refresh keeps the approved counts
        self.client.post(f"/admin/bultenler/{b['id']}/duzenle", data={
            "kind": "popular_stories", "internal_title": "t2", "subject": "s", "article_slugs": ["existing-article"]})
        self.assertEqual(store.get_bulletin(b["id"])["analytics_snapshot"]["counts"], before["analytics_snapshot"]["counts"])
        # ... and an explicit refresh re-pulls
        self.client.post(f"/admin/bultenler/{b['id']}/duzenle", data={
            "kind": "popular_stories", "internal_title": "t2", "subject": "s", "analytics_refreshed": "1",
            "article_slugs": ["zeta-story"]})
        self.assertEqual(store.get_bulletin(b["id"])["analytics_snapshot"]["counts"], {"zeta-story": 500})

    def test_unpublished_slugs_cannot_be_saved_into_a_bulletin(self):
        self._extra_articles()
        self.login_as("master1")
        self.client.post("/admin/bultenler/yeni", data={
            "kind": "popular_stories", "internal_title": "t", "subject": "s",
            "article_slugs": ["draft-story", "future-story", "nope", "existing-article", "existing-article"]})
        self.assertEqual(store.load_bulletins()[-1]["article_slugs"], ["existing-article"])

    def test_preview_shows_ranked_articles_with_counts(self):
        import bulletins
        self._extra_articles()
        self._views({"existing-article": 9, "alpha-story": 5, "zeta-story": 4})
        b = bulletins.create_draft("popular_stories", "a@x", "T", "S", article_slugs=["alpha-story"])
        self.login_as("master1")
        html = self.client.get(f"/admin/bultenler/{b['id']}/onizle").data.decode()
        self.assertIn("Existing Article — 9", html)
        self.assertIn("son 7 gün", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
