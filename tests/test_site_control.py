#!/usr/bin/env python3
"""Test suite for the centralized Site Control feature: the public site's
Live / Standby / Redirect switch (see app/app.py's enforce_site_control()
and the admin.site_control_* routes, and app/store.py's
load_site_control()/save_site_control()).

Mirrors tests/test_email_system.py's harness (isolated temp data dir + a
real Flask test client) rather than sharing it, for the same isolation
reason that file gives for not sharing tests/test_v101.py's.

Run with:
    python3 -m unittest tests.test_site_control -v
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
        {"id": "poweruser1", "email": "poweruser@test.com", "account_role": "poweruser",
         "status": "active", "password_hash": "x", "must_change_password": False},
        {"id": "user1", "email": "author@test.com", "account_role": "author",
         "status": "active", "password_hash": "x", "must_change_password": False},
    ])
    write("roles.json", [
        {"id": "master_admin", "name": "Master Admin", "permissions": [], "system": True},
        {"id": "custom", "name": "Custom", "permissions": [], "system": False},
        {"id": "poweruser", "name": "Power User",
         "permissions": ["games", "site_settings", "audit_log"], "system": False},
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
        "kicker": None, "dek": "A dek.", "byline_title": "Muhabir", "date": "2026-01-01",
        "featured": None, "breaking": False, "image": None, "image_caption": None,
        "tags": [], "body": [{"type": "p", "text": "Hello."}], "author_ids": ["auth1"],
        "author": "Test Author", "status": "published", "publish_at": None,
        "issue_id": "sayi-01", "issue_page": 1,
        "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None,
    }])
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
    # Deliberately NOT writing site_control.json -- a fresh install/test
    # run must never have one, and load_site_control() must default to
    # "live" in exactly that state.


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


class SiteControlTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="eh_site_control_test_")
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
        # site_control.json may have been created by a previous test's
        # save_site_control() call -- remove it so each test starts from
        # the genuine "no record yet" state unless it sets one up itself.
        control_path = os.path.join(self.tmp_dir, "site_control.json")
        if os.path.exists(control_path):
            os.remove(control_path)
        self.client = self.client_factory()
        import ratelimit
        ratelimit._hits.clear()

    def login_as(self, user_id):
        with self.client.session_transaction() as sess:
            sess["user_id"] = user_id

    def activate_standby(self, **overrides):
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "standby"
        control.update(overrides)
        store.save_site_control(control)
        return control

    def activate_redirect(self, url, **overrides):
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "redirect"
        control["redirect_url"] = url
        control.update(overrides)
        store.save_site_control(control)
        return control


class DefaultLiveModeTests(SiteControlTestCase):
    def test_no_settings_record_defaults_to_live(self):
        control = store.load_site_control()
        self.assertEqual(control["mode"], "live")

    def test_public_pages_work_normally_with_no_record(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        r = self.client.get("/makale/existing-article")
        self.assertEqual(r.status_code, 200)

    def test_corrupt_settings_file_fails_safe_to_live(self):
        with open(os.path.join(self.tmp_dir, "site_control.json"), "w") as f:
            f.write("{not valid json")
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)


class StandbyModeTests(SiteControlTestCase):
    def test_public_html_page_returns_503_standby(self):
        self.activate_standby(standby_title="Bakımdayız", standby_description="Test açıklaması.")
        r = self.client.get("/")
        self.assertEqual(r.status_code, 503)
        self.assertIn(b"Bak\xc4\xb1mday\xc4\xb1z", r.data)
        self.assertIn(b'noindex, nofollow', r.data)

    def test_multiple_public_routes_all_show_standby(self):
        self.activate_standby()
        for path in ("/", "/makale/existing-article", "/gazete", "/hakkimizda", "/iletisim"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 503, path)

    def test_admin_routes_remain_accessible(self):
        self.activate_standby()
        self.login_as("master1")
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 200)

    def test_admin_login_page_remains_accessible_unauthenticated(self):
        self.activate_standby()
        r = self.client.get("/admin/login")
        self.assertEqual(r.status_code, 200)

    def test_static_assets_still_load(self):
        self.activate_standby()
        r = self.client.get("/static/css/style.css")
        self.assertEqual(r.status_code, 200)

    def test_healthz_still_returns_200(self):
        self.activate_standby()
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)

    def test_json_api_endpoint_gets_json_503_not_html(self):
        self.activate_standby()
        r = self.client.post("/api/gazete/sayi-01/analitik/acilis", json={"reader_id": "x"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.content_type, "application/json")
        data = r.get_json()
        self.assertFalse(data["ok"])

    def test_logged_in_admin_bypasses_standby_on_public_site(self):
        self.activate_standby()
        self.login_as("master1")
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_returning_to_live_restores_site_immediately(self):
        self.activate_standby()
        r = self.client.get("/")
        self.assertEqual(r.status_code, 503)
        control = store.load_site_control()
        control["mode"] = "live"
        store.save_site_control(control)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)

    def test_reopen_at_sets_retry_after_header(self):
        from datetime import datetime, timedelta, timezone
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.activate_standby(standby_reopen_at=future)
        r = self.client.get("/")
        self.assertEqual(r.status_code, 503)
        self.assertIn("Retry-After", r.headers)
        self.assertGreater(int(r.headers["Retry-After"]), 0)

    def test_custom_standby_title_is_escaped_not_executed(self):
        self.activate_standby(
            standby_message_type="custom",
            standby_title="<script>alert(1)</script>",
            standby_description="<img src=x onerror=alert(2)>",
        )
        r = self.client.get("/")
        body = r.data.decode("utf-8")
        self.assertNotIn("<script>alert(1)</script>", body)
        self.assertNotIn("<img src=x onerror=alert(2)>", body)
        self.assertIn("&lt;script&gt;", body)


class RedirectModeTests(SiteControlTestCase):
    def test_public_page_temporarily_redirects(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "https://www.eurovillagebb.com/")

    def test_redirect_is_not_permanent(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        r = self.client.get("/", follow_redirects=False)
        self.assertNotEqual(r.status_code, 301)

    def test_admin_routes_never_redirected(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        self.login_as("master1")
        r = self.client.get("/admin/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)

    def test_login_route_never_redirected_externally(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        r = self.client.get("/admin/login", follow_redirects=False)
        self.assertEqual(r.status_code, 200)

    def test_static_assets_never_redirected(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        r = self.client.get("/static/css/style.css", follow_redirects=False)
        self.assertEqual(r.status_code, 200)

    def test_json_endpoint_not_redirected_gets_json_503(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        r = self.client.post("/api/gazete/sayi-01/analitik/acilis", json={"reader_id": "x"})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(r.content_type, "application/json")

    def test_logged_in_admin_bypasses_redirect_on_public_site(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        self.login_as("master1")
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)

    def test_empty_redirect_url_falls_back_to_standby_message(self):
        control = dict(store.DEFAULT_SITE_CONTROL)
        control["mode"] = "redirect"
        control["redirect_url"] = ""
        store.save_site_control(control)
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 503)

    def test_dangerous_scheme_never_persisted_or_used(self):
        import app as appmod
        url, error = appmod.validate_redirect_url("javascript:alert(1)")
        self.assertIsNone(url)
        self.assertIsNotNone(error)

    def test_cannot_redirect_to_own_host(self):
        import app as appmod
        with appmod.app.test_request_context("/", base_url="http://localhost"):
            url, error = appmod.validate_redirect_url("http://localhost/somewhere")
            self.assertIsNone(url)
            self.assertIsNotNone(error)

    def test_mutual_redirect_loop_is_broken_on_bounce_back(self):
        self.activate_redirect("https://www.eurovillagebb.com/")
        r1 = self.client.get("/", follow_redirects=False)
        self.assertEqual(r1.status_code, 302)
        # The test client persists cookies across requests -- a second
        # request now carries the bounce-detection cookie the first
        # response set, simulating the browser landing right back here.
        r2 = self.client.get("/", follow_redirects=False)
        self.assertEqual(r2.status_code, 503)
        self.assertNotIn("Location", r2.headers)


class RedirectDestinationChangeTests(SiteControlTestCase):
    def test_redirect_destination_changeable_via_admin_panel(self):
        # A logged-in admin session bypasses Standby/Redirect on the public
        # site by design (see _site_control_bypassed()) -- use a SEPARATE,
        # never-authenticated client (fresh each time, so the previous
        # redirect's bounce-loop-detection cookie doesn't interfere) to
        # observe what an actual visitor would see, while the admin
        # session drives the settings change.
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/yonlendirme",
                          data={"redirect_url": "https://a.example.com/", "action": "activate"})
        r = self.appmod.app.test_client().get("/", follow_redirects=False)
        self.assertEqual(r.headers["Location"], "https://a.example.com/")

        self.client.post("/admin/site-kontrolu/yonlendirme",
                          data={"redirect_url": "https://b.example.com/", "action": "activate"})
        r = self.appmod.app.test_client().get("/", follow_redirects=False)
        self.assertEqual(r.headers["Location"], "https://b.example.com/")

    def test_activating_with_empty_url_is_rejected_and_keeps_previous_state(self):
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/yonlendirme",
                          data={"redirect_url": "https://a.example.com/", "action": "activate"})
        self.client.post("/admin/site-kontrolu/yonlendirme", data={"redirect_url": "", "action": "activate"})
        control = store.load_site_control()
        self.assertEqual(control["mode"], "redirect")
        self.assertEqual(control["redirect_url"], "https://a.example.com/")

    def test_activating_with_dangerous_scheme_is_rejected(self):
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/yonlendirme",
                          data={"redirect_url": "javascript:alert(1)", "action": "activate"})
        control = store.load_site_control()
        self.assertEqual(control["mode"], "live")

    def test_saved_url_becomes_a_preset(self):
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/yonlendirme",
                          data={"redirect_url": "https://preset.example.com/", "action": "save"})
        control = store.load_site_control()
        self.assertIn("https://preset.example.com/", control["redirect_presets"])
        # "save" alone (no "activate") must not flip the mode
        self.assertEqual(control["mode"], "live")

    def test_going_live_deactivates_redirect_immediately(self):
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/yonlendirme",
                          data={"redirect_url": "https://a.example.com/", "action": "activate"})
        self.client.post("/admin/site-kontrolu/canli")
        self.assertEqual(store.load_site_control()["mode"], "live")
        r = self.appmod.app.test_client().get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 200)


class AdminTemplateRenderTests(SiteControlTestCase):
    """`assertNotEqual(status, 403)` (used by the authorization tests) would
    also pass on a 500 from a broken template -- these tests assert a real
    200 with expected content, so a Jinja error in site_control.html or
    the standby preview can't slip through unnoticed."""

    def test_site_control_page_renders_in_every_mode(self):
        self.login_as("master1")
        for mode_setup in (None, "standby", "redirect"):
            if mode_setup == "standby":
                self.activate_standby()
            elif mode_setup == "redirect":
                self.activate_redirect("https://www.eurovillagebb.com/")
            r = self.client.get("/admin/site-kontrolu")
            self.assertEqual(r.status_code, 200, mode_setup)
            self.assertIn(b"Site Kontrol\xc3\xbc", r.data)

    def test_dashboard_shows_site_status_panel(self):
        self.login_as("master1")
        r = self.client.get("/admin/")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Genel Site Durumu".encode(), r.data)

    def test_admin_nav_shows_warning_banner_when_not_live(self):
        self.activate_standby()
        self.login_as("master1")
        r = self.client.get("/admin/")
        self.assertIn("BEKLEMEDE".encode(), r.data)

    def test_admin_nav_shows_no_banner_when_live(self):
        self.login_as("master1")
        r = self.client.get("/admin/")
        self.assertNotIn("BEKLEMEDE".encode(), r.data)
        self.assertNotIn("YÖNLENDİRİLİYOR".encode(), r.data)

    def test_standby_preview_from_saved_settings_renders(self):
        self.activate_standby(standby_title="Kayıtlı Başlık")
        self.login_as("master1")
        r = self.client.get("/admin/site-kontrolu/onizleme/beklemede")
        self.assertEqual(r.status_code, 200)
        self.assertIn("Kayıtlı Başlık".encode(), r.data)

    def test_standby_preview_from_unsaved_form_values_renders(self):
        self.login_as("master1")
        r = self.client.get("/admin/site-kontrolu/onizleme/beklemede", query_string={
            "standby_message_type": "custom",
            "standby_title": "Henüz Kaydedilmemiş Başlık",
            "standby_description": "Deneme açıklaması.",
        })
        self.assertEqual(r.status_code, 200)
        self.assertIn("Henüz Kaydedilmemiş Başlık".encode(), r.data)
        # and it must NOT have been persisted just by previewing it
        self.assertNotEqual(store.load_site_control().get("standby_title"), "Henüz Kaydedilmemiş Başlık")

    def test_standby_preview_never_renders_a_dangerous_button_scheme(self):
        """A javascript:/data: URL in the UNSAVED preview form must never
        reach the rendered <a href> -- _resolve_standby_content() must
        re-validate it even though it came straight from request.values
        with no save step in between (see _resolve_standby_content's
        docstring)."""
        self.login_as("master1")
        r = self.client.get("/admin/site-kontrolu/onizleme/beklemede", query_string={
            "standby_message_type": "custom",
            "standby_title": "Test",
            "standby_button_label": "Devam Et",
            "standby_button_url": "javascript:alert(document.cookie)",
        })
        self.assertEqual(r.status_code, 200)
        self.assertNotIn(b"javascript:", r.data)

    def test_saved_standby_button_url_rejects_dangerous_scheme(self):
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/beklemede", data={
            "standby_message_type": "custom", "standby_title": "Test",
            "standby_button_url": "javascript:alert(1)", "action": "save",
        })
        control = store.load_site_control()
        self.assertNotEqual(control.get("standby_button_url"), "javascript:alert(1)")

    def test_standby_save_prefills_preset_text_when_blank(self):
        self.login_as("master1")
        self.client.post("/admin/site-kontrolu/beklemede", data={
            "standby_message_type": "unavailable", "standby_title": "", "standby_description": "",
            "action": "save",
        })
        control = store.load_site_control()
        self.assertEqual(control["standby_title"], "Geçici Olarak Kullanılamıyor")

    def test_custom_type_requires_a_title(self):
        self.login_as("master1")
        r = self.client.post("/admin/site-kontrolu/beklemede", data={
            "standby_message_type": "custom", "standby_title": "", "action": "activate",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(store.load_site_control()["mode"], "live")


class AuthorizationTests(SiteControlTestCase):
    ROUTES = [
        ("GET", "/admin/site-kontrolu"),
        ("POST", "/admin/site-kontrolu/canli"),
        ("POST", "/admin/site-kontrolu/beklemede"),
        ("POST", "/admin/site-kontrolu/yonlendirme"),
    ]

    def test_master_admin_can_access_every_route(self):
        self.login_as("master1")
        for method, path in self.ROUTES:
            r = self.client.get(path) if method == "GET" else self.client.post(path, data={})
            self.assertNotEqual(r.status_code, 403, f"{method} {path}")

    def test_poweruser_role_denied(self):
        self.login_as("poweruser1")
        for method, path in self.ROUTES:
            r = self.client.get(path) if method == "GET" else self.client.post(path, data={})
            self.assertEqual(r.status_code, 403, f"{method} {path}")

    def test_custom_role_denied(self):
        self.login_as("custom1")
        for method, path in self.ROUTES:
            r = self.client.get(path) if method == "GET" else self.client.post(path, data={})
            self.assertEqual(r.status_code, 403, f"{method} {path}")

    def test_author_denied(self):
        self.login_as("user1")
        for method, path in self.ROUTES:
            r = self.client.get(path) if method == "GET" else self.client.post(path, data={})
            self.assertEqual(r.status_code, 403, f"{method} {path}")

    def test_unauthenticated_denied(self):
        for method, path in self.ROUTES:
            r = self.client.get(path) if method == "GET" else self.client.post(path, data={})
            # master_admin_required redirects an unauthenticated request to
            # login rather than 403ing it directly -- either way, the
            # settings page/action itself must never be served.
            self.assertIn(r.status_code, (302, 401, 403), f"{method} {path}")
            if r.status_code == 302:
                self.assertIn("/admin/login", r.headers.get("Location", ""), f"{method} {path}")

    def test_unauthorized_post_does_not_change_mode(self):
        self.login_as("custom1")
        self.client.post("/admin/site-kontrolu/beklemede", data={"action": "activate"})
        self.assertEqual(store.load_site_control()["mode"], "live")


class ApiStylePathClassificationTests(SiteControlTestCase):
    def test_api_prefixed_paths_are_json(self):
        import app as appmod
        self.assertTrue(appmod._is_api_style_path("/api/gazete/sayi-01/analitik/acilis"))

    def test_kontrol_and_ipucu_suffixes_are_json(self):
        import app as appmod
        self.assertTrue(appmod._is_api_style_path("/oyun-kosesi/bulmaca/x/kontrol"))
        self.assertTrue(appmod._is_api_style_path("/oyun-kosesi/sudoku/x/ipucu"))

    def test_ordinary_page_is_not_json(self):
        import app as appmod
        self.assertFalse(appmod._is_api_style_path("/gazete"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
