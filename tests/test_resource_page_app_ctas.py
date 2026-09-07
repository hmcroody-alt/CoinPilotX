"""The inline-HTML resource pages carry a truthful, contextual app link.

Most PulseSoc resource pages are built as f-strings inside bot.py rather than as
Jinja templates, so the shared `_app_link_cta.html` macro cannot reach them.
They go through `bot.app_cta_html` instead, and these tests render the real
pages against a real schema to prove the button is actually there and actually
points at the resource being viewed.

A compile check is not enough here. An f-string will happily compile with the
CTA interpolated into a CSS block or swallowed by a `{{ }}` escape, and the page
would still return 200 with no button on it.

This module sets DATABASE_URL at import time, so it must run in its own pytest
process -- batching it with another test file that does the same gives one of
them the other's database.

Run: python3 -m pytest tests/test_resource_page_app_ctas.py
"""

import os
import re
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
# Without a stable key the session cookie set below cannot be read back.
os.environ.setdefault("FLASK_SECRET_KEY", "resource-page-app-cta-tests")

import bot  # noqa: E402
from services import app_links  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}

CTA_RE = re.compile(r"<a[^>]*data-app-link='([^']+)'[^>]*>([^<]*)</a>")
HREF_RE = re.compile(r"href='([^']*)'")


@pytest.fixture(scope="module")
def seeded():
    """A signed-in member with one public post and one reel."""
    with bot.webhook_app.app_context():
        bot.init_db()

    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
        ("adalovelace", "ada@example.com", "x"),
    )
    user_id = cur.lastrowid
    cur.execute(
        "INSERT INTO pulse_posts (user_id, body, visibility, moderation_status) "
        "VALUES (?,?,?,?)",
        (user_id, "Hello from the integration render.", "public", "approved"),
    )
    post_id = cur.lastrowid
    cur.execute(
        "INSERT INTO pulse_reels (post_id, user_id, caption) VALUES (?,?,?)",
        (post_id, user_id, "A caption"),
    )
    reel_id = cur.lastrowid
    conn.commit()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return {
        "client": client,
        "user_id": user_id,
        "post_id": post_id,
        "reel_id": reel_id,
    }


def ctas(html):
    """{destination: (href, label)} for every app CTA on a rendered page."""
    found = {}
    for match in CTA_RE.finditer(html):
        href = HREF_RE.search(match.group(0))
        found[match.group(1)] = (
            (href.group(1) if href else "").replace("&amp;", "&"),
            match.group(2).strip(),
        )
    return found


def render(seeded, path):
    response = seeded["client"].get(path, headers=HTTPS)
    assert response.status_code == 200, f"{path} returned HTTP {response.status_code}"
    return response.get_data(as_text=True)


# ---------------------------------------------------------------------------
# The contextual link points at the resource actually being viewed
# ---------------------------------------------------------------------------


def test_the_post_page_links_to_that_post(seeded):
    post_id = seeded["post_id"]
    href, label = ctas(render(seeded, f"/pulse/post/{post_id}"))["post"]
    assert href == app_links.build_app_link("post", post_id, source="web")
    assert f"/pulse/post/{post_id}?" in href
    # The wording rule: a button that says "this post" must open this post.
    assert label == "Open this post in PulseSoc"


def test_the_reel_page_links_to_that_reel(seeded):
    reel_id = seeded["reel_id"]
    href, label = ctas(render(seeded, f"/pulse/reels/{reel_id}"))["reel"]
    assert href == app_links.build_app_link("reel", reel_id, source="web")
    assert label == "Open this reel in PulseSoc"


def test_the_profile_page_links_to_that_profile(seeded):
    html = render(seeded, f"/pulse/profile/{seeded['user_id']}")
    href, label = ctas(html)["profile"]
    assert f"{app_links.CANONICAL_APP_ORIGIN}/pulse/profile/" in href
    assert label == "Open this profile in PulseSoc"


@pytest.mark.parametrize("page", ["post", "reel", "profile"])
def test_every_resource_cta_is_marked_and_canonical(seeded, page):
    path = {
        "post": f"/pulse/post/{seeded['post_id']}",
        "reel": f"/pulse/reels/{seeded['reel_id']}",
        "profile": f"/pulse/profile/{seeded['user_id']}",
    }[page]
    href, _ = ctas(render(seeded, path))[page]
    assert href.startswith(f"{app_links.CANONICAL_APP_ORIGIN}/pulse/")
    assert f"{app_links.APP_INTENT_PARAM}=1" in href
    assert f"{app_links.APP_SOURCE_PARAM}=web" in href


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


def test_the_post_page_keeps_its_web_navigation(seeded):
    html = render(seeded, f"/pulse/post/{seeded['post_id']}")
    assert "href='/pulse'>Back to PulseSoc</a>" in html
    assert "href='/pulse/my-posts'>My Posts</a>" in html


def test_no_resource_page_marks_its_own_internal_links(seeded):
    # Only the deliberate CTA carries the marker. If it leaked onto the ordinary
    # in-site links, every click would bounce a web reader to the App Store.
    for path in (
        f"/pulse/post/{seeded['post_id']}",
        f"/pulse/reels/{seeded['reel_id']}",
        f"/pulse/profile/{seeded['user_id']}",
    ):
        html = render(seeded, path)
        marked = re.findall(rf"href='(/[^']*{app_links.APP_INTENT_PARAM}[^']*)'", html)
        assert marked == [], f"{path} marked a relative link: {marked}"


def test_resource_pages_have_no_duplicate_element_ids(seeded):
    import collections

    for path in (
        f"/pulse/post/{seeded['post_id']}",
        f"/pulse/reels/{seeded['reel_id']}",
    ):
        html = render(seeded, path)
        counts = collections.Counter(re.findall(r"\sid='([^']+)'", html))
        dupes = [key for key, count in counts.items() if count > 1]
        assert dupes == [], f"{path} has duplicate ids: {dupes}"


# ---------------------------------------------------------------------------
# The helper itself
# ---------------------------------------------------------------------------


def test_a_bad_id_costs_the_button_not_the_page():
    # On a resource page the CTA is an addition, so an unbuildable link must
    # degrade to nothing rather than 500 a page a member is trying to read.
    assert bot.app_cta_html("post", "../../etc/passwd") == ""
    assert bot.app_cta_html("not_a_destination") == ""
    assert bot.app_cta_html("post", None) == ""


def test_the_helper_escapes_what_it_interpolates():
    html = bot.app_cta_html("home", label="Quote ' and <script>")
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_mutation_the_helper_is_what_puts_the_cta_on_the_page(seeded, monkeypatch):
    # If the post page hand-wrote its app link, neutering the shared helper
    # would leave the button in place and every assertion above would be
    # measuring a hardcoded string instead of the canonical builder.
    post_id = seeded["post_id"]
    assert "post" in ctas(render(seeded, f"/pulse/post/{post_id}"))

    monkeypatch.setattr(bot, "app_cta_html", lambda *a, **k: "")
    assert ctas(render(seeded, f"/pulse/post/{post_id}")) == {}
