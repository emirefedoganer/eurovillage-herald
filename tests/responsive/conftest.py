"""Fixtures for the Playwright-based responsive smoke tests.

Unlike tests/test_v101.py and tests/test_email_system.py (which drive the
app in-process via Flask's own test client), Playwright needs a REAL
running server to point a real browser at. This starts one in a
background thread, on an isolated temp data directory seeded the same
way the existing unittest suites already do (reusing
tests.test_email_system's _seed_minimal_data rather than duplicating that
seed logic), with a known test admin password so the admin-panel
responsive tests can actually log in -- never touching the real
app/data/*.json files.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

import pytest
from werkzeug.security import generate_password_hash
from werkzeug.serving import make_server

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
APP_DIR = os.path.join(REPO_ROOT, "app")
TESTS_DIR = os.path.join(REPO_ROOT, "tests")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, TESTS_DIR)

os.environ.setdefault("EMAIL_PROVIDER", "")
os.environ.pop("EMAIL_API_KEY", None)
os.environ.pop("TURNSTILE_SITE_KEY", None)
os.environ.pop("TURNSTILE_SECRET_KEY", None)
os.environ.pop("SERVER_NAME", None)

TEST_ADMIN_EMAIL = "master@test.com"
TEST_ADMIN_PASSWORD = "ResponsiveTest!123"


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server():
    import store
    from test_email_system import _seed_minimal_data  # noqa: E402

    tmp_dir = tempfile.mkdtemp(prefix="eh_responsive_")
    _seed_minimal_data(tmp_dir)

    # Redirect EVERY store.*_PATH constant into the temp dir, discovered
    # by introspection rather than a hand-maintained list -- covers any
    # data file (including site_control.json, which the shared unittest
    # seed helper doesn't write; a missing file there just means "no
    # record yet", which store.load_site_control() already defaults
    # safely to Live mode for) without this fixture silently drifting out
    # of sync with store.py as new *_PATH constants are added.
    path_consts = [name for name in vars(store) if name.endswith("_PATH")]
    for const_name in path_consts:
        filename = os.path.basename(getattr(store, const_name))
        setattr(store, const_name, os.path.join(tmp_dir, filename))

    # Give the seeded master admin a REAL, known password hash (the
    # shared unittest fixture uses a placeholder "x" hash, since the
    # Flask test client's session_transaction() bypasses the login form
    # entirely -- Playwright has to actually submit it).
    users_path = os.path.join(tmp_dir, "users.json")
    with open(users_path, encoding="utf-8") as f:
        users = json.load(f)
    for u in users:
        if u["email"] == TEST_ADMIN_EMAIL:
            u["password_hash"] = generate_password_hash(TEST_ADMIN_PASSWORD, method="pbkdf2:sha256")
    with open(users_path, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    import app as appmod  # noqa: E402
    appmod.app.config["TESTING"] = True

    port = _free_port()
    server = make_server("127.0.0.1", port, appmod.app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{port}"
    for _ in range(50):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.1)

    yield {"base_url": base_url, "tmp_dir": tmp_dir, "app": appmod.app}

    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def admin_page(live_server, page):
    """A Playwright page already logged in as the seeded master admin."""
    page.goto(live_server["base_url"] + "/admin/login")
    page.fill('.login-card input[name="email"]', TEST_ADMIN_EMAIL)
    page.fill('.login-card input[name="password"]', TEST_ADMIN_PASSWORD)
    page.click('.login-card button[type="submit"]')
    page.wait_for_load_state("networkidle")
    return page
