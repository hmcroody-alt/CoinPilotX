"""Links a member hands to someone else open the app, and ignore the Host header.

Two endpoints built the URL a member shares with `request.host_url`. That is
request input: the link handed to the recipient inherited whatever Host header
the *sharing* request happened to carry. It also produced a plain web URL, so a
shared reel or a group invite opened the website even for a recipient who
already had PulseSoc installed -- the exact defect this mission exists to fix.

Note that a happy-path assertion alone would pass against the old code too: in
a test client the host already is the canonical one, so `request.host_url`
produced the right answer by accident. The request-independence section below
explains how that is pinned down instead, and why the obvious way of doing it
(sending a spoofed Host) does not work here.

This module sets DATABASE_URL at import time, so it must run in its own pytest
process -- batching it with another test file that does the same gives one of
them the other's database.

Run: python3 -m pytest tests/test_share_link_app_intent.py
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "share-link-app-intent-tests")

import bot  # noqa: E402
from services import app_links  # noqa: E402


# enforce_https 301s anything that does not look like it arrived over TLS.
HTTPS = {"X-Forwarded-Proto": "https"}


@pytest.fixture(scope="module")
def seeded():
    """A signed-in member with one reel and one group."""
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
        (user_id, "A reel to share.", "public", "approved"),
    )
    post_id = cur.lastrowid
    cur.execute(
        "INSERT INTO pulse_reels (post_id, user_id, caption) VALUES (?,?,?)",
        (post_id, user_id, "A caption"),
    )
    reel_id = cur.lastrowid
    cur.execute(
        "INSERT INTO pulse_groups (name, slug, owner_user_id) VALUES (?,?,?)",
        ("Ada Fans", "ada-fans", user_id),
    )
    group_id = cur.lastrowid
    conn.commit()

    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return {
        "client": client,
        "user_id": user_id,
        "reel_id": reel_id,
        "group_id": group_id,
    }


def share_reel(seeded, headers=None):
    response = seeded["client"].post(
        f"/api/pulse/reels/{seeded['reel_id']}/share",
        headers={**HTTPS, **(headers or {})},
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.get_json()["share_url"]


def invite_group(seeded, path=None, headers=None):
    response = seeded["client"].post(
        path or f"/api/pulse/groups/{seeded['group_id']}/invite-link",
        headers={**HTTPS, **(headers or {})},
    )
    assert response.status_code == 200, response.get_data(as_text=True)[:400]
    return response.get_json()["invite_url"]


# ---------------------------------------------------------------------------
# The shared link opens the app
# ---------------------------------------------------------------------------


def test_a_shared_reel_link_is_an_app_intent_link(seeded):
    url = share_reel(seeded)
    assert url == app_links.build_app_link("reel", seeded["reel_id"], source="share")
    assert f"{app_links.APP_INTENT_PARAM}=1" in url
    assert f"{app_links.APP_SOURCE_PARAM}=share" in url


def test_a_group_invite_link_is_an_app_intent_link(seeded):
    url = invite_group(seeded)
    assert url.startswith(f"{app_links.CANONICAL_APP_ORIGIN}/pulse/groups/ada-fans?")
    assert f"{app_links.APP_INTENT_PARAM}=1" in url
    assert f"{app_links.APP_SOURCE_PARAM}=invite" in url


def test_the_shared_links_resolve_to_the_thing_they_name(seeded):
    # A link that opens the app but lands on Home is the §1 lie this mission
    # is about. Both must resolve to their own destination.
    assert app_links.match_destination("/pulse/reels/1").key == "reel"
    assert app_links.match_destination("/pulse/groups/ada-fans").key == "group"
    assert f"/pulse/reels/{seeded['reel_id']}?" in share_reel(seeded)


# ---------------------------------------------------------------------------
# The request cannot reach the link
# ---------------------------------------------------------------------------
#
# Asserting this by sending a spoofed Host through the test client does not
# work and is worth writing down. Changing `Host` moves the cookie domain, so
# the session cookie stops being sent and the endpoint 401s before it ever
# builds a link. Sending `X-Forwarded-Host` instead keeps the cookie but proves
# nothing: this app does not install ProxyFix, so a forwarded host never
# reaches `request.host_url` and such a test passes against the old code too.
#
# So the invariant is asserted where it actually lives: the builder does not
# consult the request, and neither endpoint asks it to.


def test_the_builder_produces_a_shared_link_with_no_request_at_all():
    # Outside a request context entirely. The old `request.host_url` expression
    # raised RuntimeError here, which is the point: the canonical origin is a
    # constant, not something inherited from whoever happened to be sharing.
    assert app_links.build_app_link("reel", 7, source="share").startswith(
        f"{app_links.CANONICAL_APP_ORIGIN}/pulse/reels/7?"
    )
    assert app_links.build_app_link("group", "ada-fans", source="invite").startswith(
        f"{app_links.CANONICAL_APP_ORIGIN}/pulse/groups/ada-fans?"
    )


def executable_source(function):
    """Function source with comments and docstrings removed.

    Both endpoints carry a comment naming `request.host_url` to explain what
    they stopped doing, so a plain substring search would match the explanation
    of the fix rather than the defect. Tokenizing is used instead of stripping
    that one sentence, so rewording a comment cannot break the test.
    """
    import ast
    import inspect
    import textwrap

    return ast.unparse(ast.parse(textwrap.dedent(inspect.getsource(function))))


@pytest.mark.parametrize(
    "endpoint",
    [bot.api_pulse_reel_share_by_id, bot.api_pulse_group_invite_link],
)
def test_no_share_endpoint_derives_its_link_from_the_request(endpoint):
    # Both of these built the shared URL from request.host_url, so the link a
    # member handed a friend carried whatever Host header the sharing request
    # arrived with.
    source = executable_source(endpoint)
    assert "request.host_url" not in source
    assert "app_links.build_app_link(" in source


def test_the_comment_stripping_does_not_hide_a_real_regression():
    # Guards the test above: if executable_source over-stripped, the assertion
    # would pass no matter what the endpoint did.
    def example():
        # request.host_url in a comment
        return app_links.build_app_link("reel", 1)

    def regressed():
        return request.host_url + "/pulse/reels/1"

    assert "request.host_url" not in executable_source(example)
    assert "request.host_url" in executable_source(regressed)


def test_the_slug_route_and_the_id_route_agree(seeded):
    # The slug route delegates to the id route, so they must not drift apart.
    by_id = invite_group(seeded)
    by_slug = invite_group(seeded, path="/api/pulse/groups/ada-fans/invite-link")
    assert by_id == by_slug


# ---------------------------------------------------------------------------
# What must NOT change
# ---------------------------------------------------------------------------


def test_sharing_still_counts_the_share(seeded):
    # The endpoint's original job. A link fix that silently stopped incrementing
    # the counter would be a regression nobody notices until analytics is wrong.
    conn = bot.db()
    cur = conn.cursor()

    def count():
        cur.execute(
            "SELECT COALESCE(share_count,0) FROM pulse_posts WHERE id="
            "(SELECT post_id FROM pulse_reels WHERE id=?)",
            (seeded["reel_id"],),
        )
        return cur.fetchone()[0]

    before = count()
    share_reel(seeded)
    assert count() == before + 1
    conn.close()


def test_sharing_still_requires_being_signed_in(seeded):
    anonymous = bot.webhook_app.test_client()
    assert anonymous.post(
        f"/api/pulse/reels/{seeded['reel_id']}/share", headers=HTTPS
    ).status_code == 401


def test_an_unknown_reel_is_still_a_404(seeded):
    assert seeded["client"].post(
        "/api/pulse/reels/99999999/share", headers=HTTPS
    ).status_code == 404


# ---------------------------------------------------------------------------
# Anti-vacuity
# ---------------------------------------------------------------------------


def test_mutation_the_link_authority_is_what_marks_the_shared_reel(seeded, monkeypatch):
    assert f"{app_links.APP_INTENT_PARAM}=1" in share_reel(seeded)

    monkeypatch.setattr(
        bot.app_links,
        "build_app_link",
        lambda dest, rid=None, *a, **k: f"https://pulsesoc.com/pulse/reels/{rid}",
    )
    # With the authority neutered the marker disappears, proving the assertion
    # above measures the builder rather than a hardcoded query string.
    assert app_links.APP_INTENT_PARAM not in share_reel(seeded)


def test_a_group_whose_slug_the_builder_rejects_still_gets_an_invite(seeded):
    # The fallback path: an unbuildable slug costs the slug, not the invite.
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO pulse_groups (name, slug, owner_user_id) VALUES (?,?,?)",
        ("Odd", "not/a/valid/slug", seeded["user_id"]),
    )
    odd_id = cur.lastrowid
    conn.commit()
    conn.close()

    with pytest.raises(app_links.AppLinkError):
        app_links.build_app_link("group", "not/a/valid/slug", source="invite")

    url = invite_group(seeded, path=f"/api/pulse/groups/{odd_id}/invite-link")
    assert url == app_links.build_app_link("group", odd_id, source="invite")
