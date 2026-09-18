#!/usr/bin/env python3
"""Test suite for the redesigned advertising placement system: the slot
registry, the targeting/selection algorithm (specificity, device
targeting, priority, weight, deduplication, per-page cap), scheduling
against the editorial timezone, and legacy slot-identifier migration.

Mirrors tests/test_v101.py's harness (isolated temp data dir, never
touches the real app/data/*.json files).

Run with:
    python3 -m unittest tests.test_ads -v
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "app")
sys.path.insert(0, APP_DIR)

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
    write("authors.json", [])
    write("users.json", [
        {"id": "master1", "email": "master@test.com", "account_role": "master_admin",
         "status": "active", "password_hash": "x", "must_change_password": False},
    ])
    write("roles.json", [{"id": "master_admin", "name": "Master Admin", "permissions": [], "system": True}])
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
         "tags": [], "body": [{"type": "p", "text": "P1."}, {"type": "p", "text": "P2."},
                               {"type": "p", "text": "P3."}, {"type": "p", "text": "P4."}],
         "author_ids": [], "author": "Test Author", "status": "published", "publish_at": None,
         "issue_id": None, "issue_page": None,
         "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None},
        {"id": "art2", "slug": "second-article", "section": "kultur", "title": "Second Article",
         "kicker": None, "dek": "", "byline_title": "Muhabir", "date": "2026-01-02",
         "featured": None, "breaking": False, "image": None, "image_caption": None,
         "tags": [], "body": [{"type": "p", "text": "Hello."}], "author_ids": [],
         "author": "Test Author", "status": "published", "publish_at": None,
         "issue_id": None, "issue_page": None,
         "preview_token_hash": None, "preview_token_expires_at": None, "preview_token_created_at": None},
    ])
    write("issues.json", [])
    write("issue_subscriptions.json", [])
    write("issue_analytics.json", {})
    write("editorial_drafts.json", [])
    write("email_outbox.json", [])
    write("bulletins.json", [])
    write("article_views.json", {})
    write("site_control.json", {})


REDIRECTED_SUFFIXES = None  # populated by introspection in setUpClass


class AdsTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = tempfile.mkdtemp(prefix="eh_ads_test_")
        _seed_minimal_data(cls.tmp_dir)
        for const_name in [n for n in vars(store) if n.endswith("_PATH")]:
            filename = os.path.basename(getattr(store, const_name))
            setattr(store, const_name, os.path.join(cls.tmp_dir, filename))

        import app as appmod  # noqa: E402
        import ads  # noqa: E402
        cls.appmod = appmod
        cls.ads = ads
        appmod.app.config["TESTING"] = True
        cls.client_factory = appmod.app.test_client

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def setUp(self):
        _seed_minimal_data(self.tmp_dir)
        self.client = self.client_factory()

    def load(self, name):
        with open(os.path.join(self.tmp_dir, name), encoding="utf-8") as f:
            return json.load(f)

    def save(self, name, data):
        with open(os.path.join(self.tmp_dir, name), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # ---- fixture helpers ----

    def make_ad(self, **overrides):
        ad = {
            "id": self.ads.new_ad_id(), "internal_name": "Test Ad", "sponsor_name": "Sponsor",
            "headline": "Headline", "body": None, "cta_text": None, "alt_text": None,
            "destination_url": "https://example.com", "priority": 0, "weight": 1,
            "status": "active", "start_at": None, "end_at": None, "image": None,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "created_by": "master@test.com", "updated_by": "master@test.com",
        }
        ad.update(overrides)
        ads_list = store.load_ads()
        ads_list.append(ad)
        store.save_ads(ads_list)
        return ad

    def make_placement(self, ad_id, slot, scope="global", section=None, content_id=None,
                        device_target="all", mobile_fallback=None):
        placement = {
            "id": self.ads.new_placement_id(), "ad_id": ad_id, "slot": slot, "scope": scope,
            "section": section, "content_type": "article" if scope == "content" else None,
            "content_id": content_id, "device_target": device_target, "mobile_fallback": mobile_fallback,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
            "created_by": "master@test.com",
        }
        placements = store.load_placements()
        placements.append(placement)
        store.save_placements(placements)
        return placement


class SlotRegistryTests(AdsTestCase):
    def test_every_slot_has_required_metadata(self):
        for key, meta in self.ads.SLOTS.items():
            for field in ("label", "description", "page_types", "section_contexts",
                          "supports_content_scope", "aspect_ratio", "desktop_behavior",
                          "tablet_behavior", "mobile_behavior"):
                self.assertIn(field, meta, f"{key} missing {field}")
            self.assertTrue(meta["label"], key)
            self.assertTrue(meta["description"], key)

    def test_article_sidebar_slot_exists_with_mobile_fallback_choices(self):
        meta = self.ads.SLOTS.get("article_sidebar")
        self.assertIsNotNone(meta)
        self.assertIn("after_content", meta["mobile_fallback_choices"])
        self.assertIn("hide", meta["mobile_fallback_choices"])

    def test_legacy_dotted_slot_names_resolve_to_current_keys(self):
        self.assertEqual(self.ads._normalize_slot("homepage.top"), "homepage_top")
        self.assertEqual(self.ads._normalize_slot("article.bottom"), "article_bottom")
        self.assertTrue(self.ads.is_known_slot("homepage.top"))

    def test_genuinely_unknown_slot_is_not_known(self):
        self.assertFalse(self.ads.is_known_slot("this.slot.never.existed"))
        self.assertIsNone(self.ads.slot_meta("this.slot.never.existed"))


class LegacyMigrationTests(AdsTestCase):
    def test_placement_with_legacy_dotted_slot_still_resolves(self):
        ad = self.make_ad()
        self.make_placement(ad["id"], "homepage.top", scope="global")
        resolved = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        self.assertEqual(resolved["id"], ad["id"])
        # also resolves when asked by the OLD name directly
        resolved2 = self.ads.resolve_ad_for_slot("homepage.top", device="desktop")
        self.assertEqual(resolved2["id"], ad["id"])

    def test_genuinely_unknown_legacy_placement_is_reported_not_crashed(self):
        ad = self.make_ad()
        self.make_placement(ad["id"], "some.retired.slot.nobody.remembers", scope="global")
        # must not raise, and must simply resolve to nothing for any real slot
        resolved = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        self.assertIsNone(resolved)
        stats = self.ads.dashboard_stats()
        self.assertIn("some.retired.slot.nobody.remembers", stats["unknown_slots"])

    def test_existing_ads_and_placements_are_never_silently_dropped(self):
        """A placement referencing an unknown slot still EXISTS in storage
        (just doesn't render anywhere) -- the migration never deletes
        data, only stops matching it to a page."""
        ad = self.make_ad()
        self.make_placement(ad["id"], "totally.unknown", scope="global")
        self.assertEqual(len(store.load_ads()), 1)
        self.assertEqual(len(store.load_placements()), 1)


class SchedulingAndTimezoneTests(AdsTestCase):
    def test_ad_without_dates_is_active_when_status_active(self):
        ad = self.make_ad(status="active")
        self.assertEqual(self.ads.ad_effective_status(ad), "active")

    def test_ad_with_future_start_is_scheduled(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ad = self.make_ad(start_at=future)
        self.assertEqual(self.ads.ad_effective_status(ad), "scheduled")

    def test_ad_with_past_end_is_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ad = self.make_ad(end_at=past)
        self.assertEqual(self.ads.ad_effective_status(ad), "expired")

    def test_inactive_status_wins_regardless_of_dates(self):
        ad = self.make_ad(status="inactive", start_at=None, end_at=None)
        self.assertEqual(self.ads.ad_effective_status(ad), "inactive")

    def test_admin_form_interprets_dates_in_editorial_timezone(self):
        """The admin ad form's start_at/end_at inputs are interpreted as
        EDITORIAL_TIMEZONE wall-clock time (see editorial_tz.py), then
        stored in UTC -- exactly like article/issue scheduling. A local
        wall-clock time entered here must round-trip to the correct UTC
        instant."""
        import editorial_tz
        local_input = "2026-06-01T12:00:00"
        utc_iso = editorial_tz.local_input_to_utc_iso(local_input)
        self.assertTrue(utc_iso.endswith("Z"))
        # Interpreting it back must reproduce the same local wall-clock time.
        back = editorial_tz.utc_iso_to_local_input(utc_iso)
        self.assertTrue(back.startswith("2026-06-01T12:00"))

    def test_scheduled_ad_not_yet_started_does_not_resolve(self):
        future = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ad = self.make_ad(start_at=future)
        self.make_placement(ad["id"], "homepage_top", scope="global")
        self.assertIsNone(self.ads.resolve_ad_for_slot("homepage_top", device="desktop"))

    def test_expired_ad_does_not_resolve(self):
        past = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ad = self.make_ad(end_at=past)
        self.make_placement(ad["id"], "homepage_top", scope="global")
        self.assertIsNone(self.ads.resolve_ad_for_slot("homepage_top", device="desktop"))

    def test_inactive_ad_does_not_resolve(self):
        ad = self.make_ad(status="inactive")
        self.make_placement(ad["id"], "homepage_top", scope="global")
        self.assertIsNone(self.ads.resolve_ad_for_slot("homepage_top", device="desktop"))


class TargetingSpecificityTests(AdsTestCase):
    def test_global_placement_resolves_with_no_section_or_article(self):
        ad = self.make_ad()
        self.make_placement(ad["id"], "article_bottom", scope="global")
        resolved = self.ads.resolve_ad_for_slot("article_bottom", section="sehir", device="desktop")
        self.assertEqual(resolved["id"], ad["id"])

    def test_section_placement_beats_global_for_matching_section(self):
        global_ad = self.make_ad(internal_name="Global")
        section_ad = self.make_ad(internal_name="Section")
        self.make_placement(global_ad["id"], "article_bottom", scope="global")
        self.make_placement(section_ad["id"], "article_bottom", scope="section", section="sehir")
        resolved = self.ads.resolve_ad_for_slot("article_bottom", section="sehir", device="desktop")
        self.assertEqual(resolved["id"], section_ad["id"])

    def test_section_placement_does_not_apply_to_other_sections(self):
        section_ad = self.make_ad(internal_name="Section")
        self.make_placement(section_ad["id"], "article_bottom", scope="section", section="sehir")
        resolved = self.ads.resolve_ad_for_slot("article_bottom", section="kultur", device="desktop")
        self.assertIsNone(resolved)

    def test_content_placement_beats_section_and_global(self):
        global_ad = self.make_ad(internal_name="Global")
        section_ad = self.make_ad(internal_name="Section")
        article_ad = self.make_ad(internal_name="Article")
        self.make_placement(global_ad["id"], "article_bottom", scope="global")
        self.make_placement(section_ad["id"], "article_bottom", scope="section", section="sehir")
        self.make_placement(article_ad["id"], "article_bottom", scope="content", content_id="art1")
        resolved = self.ads.resolve_ad_for_slot("article_bottom", section="sehir", content_id="art1", device="desktop")
        self.assertEqual(resolved["id"], article_ad["id"])

    def test_article_specific_ad_does_not_leak_onto_other_articles_or_homepage(self):
        article_ad = self.make_ad(internal_name="Article-specific")
        self.make_placement(article_ad["id"], "article_sidebar", scope="content", content_id="art1")
        # a different article, same slot
        other = self.ads.resolve_ad_for_slot("article_sidebar", section="sehir", content_id="art2", device="desktop")
        self.assertIsNone(other)
        # a totally different slot (homepage) must never pick it up either,
        # since resolve_ad_for_slot only ever looks at placements for the
        # slot it was actually asked about
        home = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        self.assertIsNone(home)


class PriorityWeightTests(AdsTestCase):
    def test_higher_priority_ad_wins_within_same_tier(self):
        low = self.make_ad(internal_name="Low", priority=0)
        high = self.make_ad(internal_name="High", priority=10)
        self.make_placement(low["id"], "homepage_top", scope="global")
        self.make_placement(high["id"], "homepage_top", scope="global")
        resolved = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        self.assertEqual(resolved["id"], high["id"])

    def test_weighted_rotation_is_deterministic_for_the_same_day(self):
        a = self.make_ad(internal_name="A", weight=1)
        b = self.make_ad(internal_name="B", weight=1)
        self.make_placement(a["id"], "homepage_top", scope="global")
        self.make_placement(b["id"], "homepage_top", scope="global")
        first = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        second = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        self.assertEqual(first["id"], second["id"], "same slot, same day must resolve the same way")

    def test_weight_is_explainable_via_deterministic_choice_helper(self):
        heavy = self.make_ad(internal_name="Heavy", weight=100)
        light = self.make_ad(internal_name="Light", weight=1)
        chosen_p, chosen_ad = self.ads._deterministic_choice(
            [({}, heavy), ({}, light)], salt="test-salt-fixed")
        # Not asserting WHICH one wins (that depends on the day's seed),
        # only that the function is pure/deterministic for a fixed salt+day.
        chosen_p2, chosen_ad2 = self.ads._deterministic_choice(
            [({}, heavy), ({}, light)], salt="test-salt-fixed")
        self.assertEqual(chosen_ad["id"], chosen_ad2["id"])


class DeviceTargetingTests(AdsTestCase):
    def test_device_specific_placement_only_matches_its_device(self):
        mobile_ad = self.make_ad(internal_name="Mobile")
        self.make_placement(mobile_ad["id"], "homepage_top", scope="global", device_target="mobile")
        self.assertIsNone(self.ads.resolve_ad_for_slot("homepage_top", device="desktop"))
        resolved = self.ads.resolve_ad_for_slot("homepage_top", device="mobile")
        self.assertEqual(resolved["id"], mobile_ad["id"])

    def test_device_specific_placement_beats_all_devices_placement(self):
        general_ad = self.make_ad(internal_name="General")
        mobile_ad = self.make_ad(internal_name="MobileSpecific")
        self.make_placement(general_ad["id"], "homepage_top", scope="global", device_target="all")
        self.make_placement(mobile_ad["id"], "homepage_top", scope="global", device_target="mobile")
        resolved = self.ads.resolve_ad_for_slot("homepage_top", device="mobile")
        self.assertEqual(resolved["id"], mobile_ad["id"])
        resolved_desktop = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
        self.assertEqual(resolved_desktop["id"], general_ad["id"])

    def test_detect_device_classifies_common_user_agents(self):
        self.assertEqual(self.ads.detect_device("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)"), "mobile")
        self.assertEqual(self.ads.detect_device("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X)"), "tablet")
        self.assertEqual(self.ads.detect_device("Mozilla/5.0 (Windows NT 10.0; Win64; x64)"), "desktop")
        self.assertEqual(self.ads.detect_device(""), "desktop")

    def test_ad_slot_marks_hide_fallback_with_css_class(self):
        with self.appmod.app.test_request_context("/"):
            ad = self.make_ad(internal_name="SidebarAd")
            self.make_placement(ad["id"], "article_sidebar", scope="content", content_id="art1",
                                 mobile_fallback="hide")
            html = self.ads.ad_slot("article_sidebar", section="sehir", content_id="art1")
            self.assertIn("ad-slot-hide-narrow", str(html))

    def test_ad_slot_after_content_fallback_has_no_hide_class(self):
        with self.appmod.app.test_request_context("/"):
            ad = self.make_ad(internal_name="SidebarAd2")
            self.make_placement(ad["id"], "article_sidebar", scope="content", content_id="art1",
                                 mobile_fallback="after_content")
            html = self.ads.ad_slot("article_sidebar", section="sehir", content_id="art1")
            self.assertNotIn("ad-slot-hide-narrow", str(html))


class DeduplicationAndMaxPerPageTests(AdsTestCase):
    def test_same_ad_not_shown_twice_on_one_page(self):
        with self.appmod.app.test_request_context("/"):
            ad = self.make_ad(internal_name="Repeatable")
            self.make_placement(ad["id"], "article_after_paragraph_3", scope="content", content_id="art1")
            self.make_placement(ad["id"], "article_bottom", scope="content", content_id="art1")
            first = self.ads.resolve_ad_for_slot("article_after_paragraph_3", content_id="art1", device="desktop")
            second = self.ads.resolve_ad_for_slot("article_bottom", content_id="art1", device="desktop")
            self.assertEqual(first["id"], ad["id"])
            self.assertIsNone(second, "the same ad must not fill a second slot on the same page render")

    def test_dedup_falls_through_to_next_best_candidate(self):
        with self.appmod.app.test_request_context("/"):
            shared = self.make_ad(internal_name="Shared", priority=10)
            fallback = self.make_ad(internal_name="Fallback", priority=0)
            self.make_placement(shared["id"], "article_after_paragraph_3", scope="content", content_id="art1")
            self.make_placement(shared["id"], "article_bottom", scope="global")
            self.make_placement(fallback["id"], "article_bottom", scope="global")
            first = self.ads.resolve_ad_for_slot("article_after_paragraph_3", content_id="art1", device="desktop")
            second = self.ads.resolve_ad_for_slot("article_bottom", content_id="art1", device="desktop")
            self.assertEqual(first["id"], shared["id"])
            self.assertEqual(second["id"], fallback["id"], "should fall through to the non-deduped alternative")

    def test_max_ads_per_page_is_enforced(self):
        with self.appmod.app.test_request_context("/"):
            original_max = self.ads.MAX_ADS_PER_PAGE
            self.ads.MAX_ADS_PER_PAGE = 2
            try:
                slots = ["homepage_top", "homepage_after_news_4", "homepage_sidebar_top", "homepage_bottom"]
                for i, slot in enumerate(slots):
                    ad = self.make_ad(internal_name=f"Ad{i}")
                    self.make_placement(ad["id"], slot, scope="global")
                shown = [self.ads.resolve_ad_for_slot(slot, device="desktop") for slot in slots]
                shown_count = sum(1 for s in shown if s is not None)
                self.assertEqual(shown_count, 2)
            finally:
                self.ads.MAX_ADS_PER_PAGE = original_max

    def test_state_resets_between_requests(self):
        """Dedup/max-per-page state must never leak from one page view
        into the next -- each request gets a fresh budget."""
        ad = self.make_ad(internal_name="Fresh")
        self.make_placement(ad["id"], "homepage_top", scope="global")
        with self.appmod.app.test_request_context("/"):
            first = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
            self.assertEqual(first["id"], ad["id"])
        with self.appmod.app.test_request_context("/"):
            second = self.ads.resolve_ad_for_slot("homepage_top", device="desktop")
            self.assertEqual(second["id"], ad["id"], "a fresh request must be able to show the same ad again")


class RenderingSafetyTests(AdsTestCase):
    def test_render_ad_html_escapes_fields(self):
        ad = self.make_ad(headline="<script>alert(1)</script>", sponsor_name="<b>Sponsor</b>")
        html = str(self.ads.render_ad_html(ad))
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_render_ad_html_rejects_non_http_destination(self):
        ad = self.make_ad(destination_url="javascript:alert(1)")
        html = str(self.ads.render_ad_html(ad))
        self.assertNotIn("javascript:alert", html)
        self.assertIn('href="#"', html)

    def test_ad_slot_returns_empty_markup_when_nothing_applies(self):
        with self.appmod.app.test_request_context("/"):
            result = self.ads.ad_slot("homepage_top")
            self.assertEqual(str(result), "")


class ArticlePageSidebarIntegrationTests(AdsTestCase):
    def test_article_page_renders_without_sidebar_grid_when_no_ad_configured(self):
        r = self.client.get("/makale/existing-article")
        self.assertEqual(r.status_code, 200)
        body = r.data.decode("utf-8")
        self.assertIn("article-layout--full", body)
        self.assertNotIn('class="article-sidebar"', body)

    def test_article_page_renders_sidebar_when_ad_configured(self):
        ad = self.make_ad(internal_name="SidebarAd")
        self.make_placement(ad["id"], "article_sidebar", scope="content", content_id="art1")
        r = self.client.get("/makale/existing-article")
        self.assertEqual(r.status_code, 200)
        body = r.data.decode("utf-8")
        self.assertIn('class="article-sidebar"', body)
        self.assertNotIn("article-layout--full", body)

    def test_admin_placement_form_exposes_slot_metadata_for_preview(self):
        self.client.post("/admin/login", data={"email": "master@test.com", "password": "wrong"})
        with self.client.session_transaction() as sess:
            sess["user_id"] = "master1"
        r = self.client.get("/admin/reklam/yerlesimler/yeni")
        self.assertEqual(r.status_code, 200)
        body = r.data.decode("utf-8")
        self.assertIn("ad-slots-data", body)
        self.assertIn("article_sidebar", body)


class AdminCrudRoundTripTests(AdsTestCase):
    """End-to-end through the real HTTP routes (not just direct module
    calls) -- proves the new weight/alt_text/device_target/mobile_fallback
    fields actually round-trip through the admin forms into storage."""

    def login(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = "master1"

    def test_create_ad_with_weight_and_alt_text(self):
        self.login()
        r = self.client.post("/admin/reklam/ilanlar/yeni", data={
            "internal_name": "Round Trip Ad", "sponsor_name": "Sponsor", "headline": "Headline",
            "destination_url": "https://example.com", "priority": "5", "weight": "3",
            "alt_text": "Erişilebilirlik metni", "status": "active",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        created = next(a for a in store.load_ads() if a["internal_name"] == "Round Trip Ad")
        self.assertEqual(created["priority"], 5)
        self.assertEqual(created["weight"], 3)
        self.assertEqual(created["alt_text"], "Erişilebilirlik metni")

    def test_create_placement_with_device_target_and_mobile_fallback(self):
        self.login()
        ad = self.make_ad(internal_name="Sidebar Ad")
        r = self.client.post("/admin/reklam/yerlesimler/yeni", data={
            "ad_id": ad["id"], "slot": "article_sidebar", "scope": "content",
            "content_id": "art1", "device_target": "desktop", "mobile_fallback": "hide",
        }, follow_redirects=True)
        self.assertEqual(r.status_code, 200)
        placement = next(p for p in store.load_placements() if p["ad_id"] == ad["id"])
        self.assertEqual(placement["device_target"], "desktop")
        self.assertEqual(placement["mobile_fallback"], "hide")

    def test_two_placements_same_target_different_device_do_not_conflict(self):
        self.login()
        desktop_ad = self.make_ad(internal_name="Desktop Creative")
        mobile_ad = self.make_ad(internal_name="Mobile Creative")
        self.client.post("/admin/reklam/yerlesimler/yeni", data={
            "ad_id": desktop_ad["id"], "slot": "homepage_top", "scope": "global", "device_target": "desktop",
        })
        self.client.post("/admin/reklam/yerlesimler/yeni", data={
            "ad_id": mobile_ad["id"], "slot": "homepage_top", "scope": "global", "device_target": "mobile",
        })
        placements = [p for p in store.load_placements() if p["slot"] == "homepage_top"]
        self.assertEqual(len(placements), 2, "a desktop-only and a mobile-only placement must coexist")

    def test_invalid_mobile_fallback_choice_is_rejected(self):
        self.login()
        ad = self.make_ad()
        r = self.client.post("/admin/reklam/yerlesimler/yeni", data={
            "ad_id": ad["id"], "slot": "article_sidebar", "scope": "content",
            "content_id": "art1", "device_target": "all", "mobile_fallback": "not-a-real-choice",
        })
        self.assertEqual(r.status_code, 200)  # re-rendered form, not a redirect
        self.assertEqual(len(store.load_placements()), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
