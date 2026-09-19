"""Browser test of the bulletin form's type -> audience synchronisation."""
import json
import os
from datetime import datetime, timezone

DATA = os.path.join(os.path.dirname(__file__), "..", "..", "app", "data")


def _seed():
    import store
    import subscriptions
    for i, pref in enumerate(("new_issue", "breaking_news", "breaking_news")):
        s, t = subscriptions.subscribe(f"form{i}@example.com", {pref: True})
        subscriptions.confirm(t)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    store.save_article_views({"existing-article": {today: 9}, "second-article": {today: 4}})


def test_type_change_selects_audience_updates_estimate_and_ranks(admin_page, live_server):
    _seed()
    page = admin_page
    page.goto(live_server["base_url"] + "/admin/bultenler/yeni?kind=custom")
    page.wait_for_load_state("networkidle")
    aud = page.locator("#target_preference")
    assert aud.locator("option").count() == 5
    aud.select_option("ari_magazin")                                    # a custom override is allowed here
    page.select_option("#kind", "breaking_news")
    assert aud.input_value() == "breaking_news"                         # stale override NOT kept
    assert aud.locator("option").count() == 1
    page.wait_for_function("document.getElementById('audience-count').textContent === '2'")
    page.select_option("#kind", "new_issue")
    assert aud.input_value() == "new_issue"
    page.wait_for_function("document.getElementById('audience-count').textContent === '1'")
    page.select_option("#kind", "popular_stories")
    assert aud.input_value() == "popular_stories"
    assert page.locator("#popular-panel").is_visible()
    assert page.locator("#generic-articles").is_hidden()
    items = page.locator("#popular-list li")
    assert items.count() == 2
    assert "9 görüntülenme" in items.nth(0).inner_text()
    items.nth(1).locator("[data-move='-1']").click()                    # reorder
    assert "4 görüntülenme" in page.locator("#popular-list li").nth(0).inner_text()
    page.locator("#popular-list li").nth(0).locator("[data-remove]").click()   # remove
    assert page.locator("#popular-list li").count() == 1
    page.click("#popular-refresh")                                       # explicit refresh restores the ranking
    page.wait_for_function("document.querySelectorAll('#popular-list li').length === 2")
    page.select_option("#kind", "custom")
    assert aud.locator("option").count() == 5
    assert page.locator("#popular-panel").is_hidden()
    # hidden panel's inputs must not be submitted
    assert page.locator("#popular-panel input[name='article_slugs']:not([disabled])").count() == 0


def test_capture_popular_form(admin_page, live_server):
    _seed()
    admin_page.set_viewport_size({"width": 1024, "height": 900})
    admin_page.goto(live_server["base_url"] + "/admin/bultenler/yeni?kind=popular_stories")
    admin_page.wait_for_load_state("networkidle")
    os.makedirs(os.path.join(os.path.dirname(__file__), "screenshots"), exist_ok=True)
    admin_page.screenshot(path=os.path.join(os.path.dirname(__file__), "screenshots", "bulletin_form_popular.png"), full_page=True)
