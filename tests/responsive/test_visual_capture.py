"""Captures full-page screenshots of the key public/admin surfaces at the
representative widths into tests/responsive/screenshots/ (git-ignored)
for human visual inspection, and asserts the mechanical parts (no
page-level overflow, key component visible)."""
import os

import pytest

from test_responsive import _assert_no_overflow

SHOTS = os.path.join(os.path.dirname(__file__), "screenshots")
WIDTHS = [(320, 700), (390, 844), (768, 1024), (1024, 768), (1280, 900), (1440, 900)]


def _shot(page, name, width):
    os.makedirs(SHOTS, exist_ok=True)
    page.screenshot(path=os.path.join(SHOTS, f"{name}_{width}.png"), full_page=True)


def _links():
    import store
    import subscriptions
    from app import app  # noqa: F401  (secret key)
    import app as appmod
    sub, token = subscriptions.subscribe("visual@example.com", {"new_issue": True})
    subscriptions.confirm(token)
    sub = store.get_subscription_by_email("visual@example.com")
    key = appmod.app.secret_key
    return (f"/abone-ol/tercihler/{subscriptions.manage_token(key, sub['id'])}",
            f"/abone-ol/cik/{subscriptions.unsubscribe_token(key, sub['id'])}")


@pytest.mark.parametrize("width,height", WIDTHS)
def test_capture_public_pages(live_server, page, width, height):
    manage, unsub = _links()
    pages = [("home", "/"), ("article", "/makale/existing-article"), ("reader", "/gazete/sayi-01"),
             ("subscribe", "/bultene-abone-ol"), ("manage", manage), ("unsubscribe", unsub)]
    page.set_viewport_size({"width": width, "height": height})
    for name, path in pages:
        page.goto(live_server["base_url"] + path)
        page.wait_for_load_state("networkidle")
        _assert_no_overflow(page, f"{name} @ {width}")
        _shot(page, name, width)
    page.goto(live_server["base_url"] + "/")
    assert page.locator(".sidebar-block-subscribe form[action='/abone-ol']").count() == 1
    page.goto(live_server["base_url"] + "/gazete/sayi-01")
    assert page.locator("#pdfPrev").is_visible() and page.locator("#pdfZoomIn").is_visible()


@pytest.mark.parametrize("width,height", WIDTHS)
def test_capture_standby_with_subscribe_form(live_server, page, width, height):
    import store
    control = dict(getattr(store, "DEFAULT_SITE_CONTROL", {}))
    control.update({"mode": "standby", "standby_kind": "maintenance", "standby_title": "Bakımdayız",
                    "standby_message": "Kısa süre sonra döneceğiz.", "standby_show_subscribe_form": True})
    store.save_site_control(control)
    try:
        page.set_viewport_size({"width": width, "height": height})
        page.goto(live_server["base_url"] + "/")
        assert page.locator("form[action='/abone-ol']").is_visible()
        _assert_no_overflow(page, f"standby-sub @ {width}")
        _shot(page, "standby_subscribe", width)
    finally:
        control["mode"] = "live"
        store.save_site_control(control)


@pytest.mark.parametrize("width,height", WIDTHS)
def test_capture_admin_email_and_nav(admin_page, live_server, width, height):
    admin_page.set_viewport_size({"width": width, "height": height})
    admin_page.goto(live_server["base_url"] + "/admin/eposta-sistemi")
    admin_page.wait_for_load_state("networkidle")
    _assert_no_overflow(admin_page, f"admin email @ {width}")
    _shot(admin_page, "admin_email", width)


@pytest.mark.parametrize("width,height", [(390, 844), (1024, 768), (1440, 900)])
def test_capture_public_nav_states(live_server, page, width, height):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(live_server["base_url"] + "/")
    page.wait_for_load_state("networkidle")
    _shot(page, "nav_closed", width)
    burger = page.locator(".hamburger-btn")
    if burger.is_visible():
        burger.click()
        _shot(page, "nav_drawer_open", width)


@pytest.mark.parametrize("width,height", [(768, 1024), (1024, 768), (1440, 900)])
def test_admin_dropdown_does_not_cover_content_on_load(admin_page, live_server, width, height):
    """Found by eye: the active section's floating dropdown started open and
    covered the first panel of /admin/eposta-sistemi at 1024w."""
    admin_page.set_viewport_size({"width": width, "height": height})
    admin_page.goto(live_server["base_url"] + "/admin/eposta-sistemi")
    admin_page.wait_for_load_state("networkidle")
    assert admin_page.locator(".admin-nav-group[open]").count() == 0
    assert admin_page.locator(".admin-nav-group > summary.active").count() == 1
