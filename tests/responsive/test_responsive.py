"""Responsive/overflow smoke tests, driven by Playwright against a real
running instance of the app (see conftest.py's live_server fixture).

Run with (from the repo root, with the venv activated):
    .venv/bin/python3 -m pytest tests/responsive -v

A page-wide overflow check (documentElement.scrollWidth vs. window
.innerWidth) is necessary but not sufficient on its own -- see this
file's *_visual tests for a few pages the task's screenshot specifically
flagged (the admin nav), checked more deeply than a bare overflow number.
"""
import os

import pytest

VIEWPORTS = [
    (320, 700, "320w"),
    (375, 812, "375w"),
    (390, 844, "390w"),
    (768, 1024, "768w"),
    (1024, 768, "1024w"),
    (1280, 900, "1280w"),
    (1440, 900, "1440w"),
]

PUBLIC_PAGES = [
    ("/", "home"),
    ("/bolum/sehir", "section"),
    ("/makale/existing-article", "article"),
    ("/magazin", "ari_magazin"),
    ("/ara?q=test", "search"),
    ("/gazete", "gazete_archive"),
    ("/gazete/sayi-01", "gazete_reader"),
    ("/hakkimizda", "hakkimizda"),
    ("/iletisim", "iletisim"),
    ("/yazarlar", "yazarlar"),
    ("/profil/test-author", "author_profile"),
    ("/oyun-kosesi", "oyun_kosesi"),
    ("/gazete-yonetimi", "kurumsal"),
    ("/gizlilik", "gizlilik"),
]

ADMIN_PAGES = [
    ("/admin/", "dashboard"),
    ("/admin/sayilar", "issues"),
    ("/admin/bultenler", "bulletins"),
    ("/admin/eposta-sistemi", "email_system"),
    ("/admin/mesajlar", "messages"),
    ("/admin/aboneler", "subscribers"),
    ("/admin/reklam", "ads_dashboard"),
    ("/admin/reklam/ilanlar", "ads_list"),
    ("/admin/reklam/ilanlar/yeni", "ad_form"),
    ("/admin/roller", "roles"),
    ("/admin/yazarlar", "authors"),
    ("/admin/site-ayarlari", "site_settings"),
    ("/admin/denetim-kaydi", "audit_log"),
    ("/admin/site-kontrolu", "site_control"),
    ("/admin/gazete-yonetimi", "management"),
    ("/admin/oyunlar", "games_overview"),
]

SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "screenshots")
os.makedirs(SCREENSHOT_DIR, exist_ok=True)


def _assert_no_overflow(page, label):
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - window.innerWidth"
    )
    assert overflow <= 1, f"{label}: page is {overflow}px wider than the viewport (unintended horizontal scroll)"


@pytest.mark.parametrize("path,name", PUBLIC_PAGES)
@pytest.mark.parametrize("width,height,vp_label", VIEWPORTS)
def test_public_page_has_no_horizontal_overflow(live_server, page, path, name, width, height, vp_label):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server["base_url"] + path)
    page.wait_for_load_state("networkidle")
    _assert_no_overflow(page, f"{name} @ {vp_label}")


@pytest.mark.parametrize("width,height,vp_label", [
    (901, 700, "901w"), (1024, 768, "1024w"), (1150, 800, "1150w"),
    (1180, 800, "1180w"), (1280, 900, "1280w"), (1440, 900, "1440w"),
])
def test_main_nav_never_overflows_its_own_row(live_server, page, width, height, vp_label):
    """A component-scoped check the page-level overflow assertion above
    structurally CANNOT catch: .mainnav-inner has its own
    overflow-x:auto, so a page-level scrollWidth check stays green even
    when the nav row itself is too narrow for its contents and silently
    clips the search box (exactly the real bug caught by eye during this
    pass -- see nav_links()/mainnav-search's comments in style.css)."""
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server["base_url"] + "/")
    page.wait_for_load_state("networkidle")
    deficit = page.evaluate(
        "document.querySelector('.mainnav-inner').scrollWidth - "
        "document.querySelector('.mainnav-inner').clientWidth"
    )
    assert deficit <= 1, f"main nav is {deficit}px narrower than its contents need @ {vp_label}"


@pytest.mark.parametrize("path,name", ADMIN_PAGES)
@pytest.mark.parametrize("width,height,vp_label", VIEWPORTS)
def test_admin_page_has_no_horizontal_overflow(admin_page, live_server, path, name, width, height, vp_label):
    admin_page.set_viewport_size({"width": width, "height": height})
    admin_page.goto(live_server["base_url"] + path)
    admin_page.wait_for_load_state("networkidle")
    _assert_no_overflow(admin_page, f"admin/{name} @ {vp_label}")


def test_standby_page_has_no_overflow_and_correct_status(live_server, page):
    import store
    control = dict(getattr(store, "DEFAULT_SITE_CONTROL", {}))
    control.update({
        "mode": "standby", "standby_kind": "maintenance",
        "standby_title": "Bakımdayız", "standby_message": "Kısa süre sonra döneceğiz.",
    })
    store.save_site_control(control)
    try:
        for width, height, vp_label in VIEWPORTS:
            page.set_viewport_size({"width": width, "height": height})
            response = page.goto(live_server["base_url"] + "/")
            assert response.status == 503, f"standby page should be 503, got {response.status}"
            _assert_no_overflow(page, f"standby @ {vp_label}")
    finally:
        control["mode"] = "live"
        store.save_site_control(control)


def test_admin_panel_reachable_while_site_is_in_standby(live_server, admin_page):
    """The one behavior this whole feature exists to guarantee: Standby
    must never lock administrators out of the admin panel."""
    import store
    control = dict(getattr(store, "DEFAULT_SITE_CONTROL", {}))
    control["mode"] = "standby"
    store.save_site_control(control)
    try:
        response = admin_page.goto(live_server["base_url"] + "/admin/")
        assert response.status == 200
        assert "admin" in admin_page.url
    finally:
        control["mode"] = "live"
        store.save_site_control(control)


# ---- deeper, non-overflow-number checks for what the task's screenshot
# actually flagged: the admin nav's operability and legibility, not just
# "the page doesn't scroll sideways". ----

@pytest.mark.parametrize("width,height,vp_label", [(375, 812, "375w"), (768, 1024, "768w"), (1440, 900, "1440w")])
def test_admin_nav_groups_are_operable(admin_page, live_server, width, height, vp_label):
    admin_page.set_viewport_size({"width": width, "height": height})
    admin_page.goto(live_server["base_url"] + "/admin/")
    admin_page.wait_for_load_state("networkidle")

    nav = admin_page.locator("nav.admin-subnav")
    assert nav.count() == 1

    # The top-level "İçerik" link must always be present and clickable.
    icerik = nav.get_by_role("link", name="İçerik")
    assert icerik.count() == 1

    # A dropdown group ("Yönetim") must be closed by default, open when
    # its summary is clicked, and reveal real, clickable links -- proving
    # the redesigned nav is a working disclosure widget, not just markup
    # that happens to not overflow. Matched via the <summary>'s own exact
    # text (not the group's full descendant text, which would also match
    # the unrelated "Gazete" group -- its "Gazete Yönetimi" link text
    # contains "Yönetim" as a substring).
    summary = nav.locator(".admin-nav-group > summary", has_text="Yönetim")
    assert summary.count() == 1
    group = summary.locator("xpath=..")
    assert group.get_attribute("open") is None
    summary.click()
    admin_page.wait_for_timeout(100)
    assert group.get_attribute("open") is not None
    roles_link = group.get_by_role("link", name="Roller")
    assert roles_link.is_visible()

    # Every item inside the open group must sit within the viewport --
    # a floating dropdown that renders off-screen would defeat the point
    # of making it collapsible in the first place.
    box = roles_link.bounding_box()
    assert box is not None
    assert box["x"] >= 0
    assert box["x"] + box["width"] <= width + 1


def test_master_only_admin_url_denied_without_a_session(live_server, page):
    """This nav redesign is purely presentational -- authorization is
    still enforced by app.py's master_admin_required on every one of
    these routes (see tests/test_email_system.py's
    MasterAdminAuthorizationTests for the exhaustive per-role matrix).
    This is a lightweight regression check, through the real HTTP server
    rather than the Flask test client, that a request with no session
    cookie at all is denied rather than served."""
    response = page.request.get(live_server["base_url"] + "/admin/roller", max_redirects=0)
    assert response.status in (302, 401, 403)
