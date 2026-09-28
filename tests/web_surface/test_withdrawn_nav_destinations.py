"""A withdrawn destination must be withdrawn everywhere, or it is not withdrawn.

The site offered two video surfaces side by side. Reels is the one it presents
now; ``/pulse/videos`` is no longer offered as a place to go. The decision is
recorded once in ``services/pulse_web_navigation`` and every nav builder filters
through it.

What makes this worth a test is the arithmetic of the thing. Web navigation is
assembled in six independent, hand-written lists -- the desktop top bar, the
universal dock, the feed shell's nav and its drawer, the social shell's nav, and
the arena shell -- plus loose anchors in page bodies. A withdrawal applied to
five of six is not a smaller version of the change; it is the visibly broken
state where the tab is gone from the desktop header and still sitting in the
phone dock. So the assertion is made against *rendered pages*, not against the
source lists: a seventh list added later, by someone who never reads this file,
fails here.

The inverse failure matters as much and is asserted too. Withdrawing the tab must
not delete the product:

* ``/pulse/videos`` still answers. ``/pulse/videos/<id>`` permalinks are handed
  out by the video pages themselves and have already been shared, and there is
  no catch-all rule and no 404 handler on this app -- removing the route turns
  every one of those links into a bare 404.
* A member who has uploaded videos still has a way to reach them, from the
  creator tools. A withdrawal that leaves their own content unreachable is not a
  navigation change, it is data loss with the bytes still on disk.
* The phone dock still renders. Filtering a shared list is exactly the kind of
  edit that empties a nav wholesale, and the dock is hidden above 900px, so a
  desktop-only check would not see it go.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

import pytest

from services import pulse_web_navigation
from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

#: Pages that carry navigation, spread across all three shells: the feed shell
#: (``pulse_page_html``), the social shell (``pulse_social_shell``) and the roast
#: shell, which wraps another response and keeps its own button row.
NAV_PAGES = ("/pulse", "/pulse/saved", "/pulse/settings", "/pulse/notifications",
             "/pulse/roast-battle")

#: Where a member's own videos stay reachable from.
CREATOR_TOOLS = "/dashboard/creator/videos"

#: Withdrawn, but must still answer.
WITHDRAWN_PAGE = "/pulse/videos"

_PROBE = r"""
import json, re, sys
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "withdrawn-nav"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        ("withdrawnnav", "withdrawnnav@example.com", "Withdrawn Nav"),
    )
    conn.commit()
    user_id = cur.lastrowid

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = user_id

# Both quoting styles: this codebase builds markup with single-quoted attributes
# in f-strings and double-quoted ones in templates, and a regex that knows only
# one of them reads a page full of links as a page with none.
HREF = re.compile(r'''href=["']([^"']*)["']''')

report = {"pages": {}}
for path in %(paths)r:
    response = client.get(path)
    body = response.get_data(as_text=True)
    report["pages"][path] = {
        "status": response.status_code,
        "hrefs": sorted(set(HREF.findall(body))),
        "bottom_nav": "mobile-bottom-nav" in body,
        "bytes": len(body),
    }

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def nav_probe():
    """Boot the app once, in a child process, and collect every link it renders.

    Importing ``bot`` binds ``DATABASE_URL`` for the whole pytest process and
    runs ``init_db()`` at module scope, which is why every web-surface suite pays
    for a subprocess rather than importing the module directly.
    """
    workdir = tempfile.mkdtemp(prefix="withdrawn-nav-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "nav.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    paths = list(NAV_PAGES) + [CREATOR_TOOLS, WITHDRAWN_PAGE]
    code = _PROBE % {"repo": REPO, "paths": paths}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


def test_no_page_offers_a_withdrawn_destination(nav_probe):
    """The point of the whole change, asserted against what a browser receives.

    Checked page by page rather than in aggregate, because the aggregate form
    reports "somewhere on the site" and the thing that actually goes wrong is one
    of six lists.
    """
    for path in NAV_PAGES:
        page = nav_probe["pages"][path]
        assert page["status"] == 200, f"{path} -> {page['status']}"
        offered = [href for href in page["hrefs"]
                   if pulse_web_navigation.is_withdrawn(href)]
        assert not offered, (
            f"{path} still offers withdrawn destinations {offered}. Web navigation "
            "is assembled in six independent lists; filter this one through "
            "services.pulse_web_navigation.visible() like the others."
        )


def test_reels_is_still_offered(nav_probe):
    """Otherwise the guard above passes on a page that lost both video surfaces.

    Reels is what the withdrawal leaves in place. A filter with a bug in it --
    too broad a prefix match, an empty list -- satisfies "Videos is gone" and
    fails the product, so the surviving destination is pinned.
    """
    for path in NAV_PAGES:
        hrefs = nav_probe["pages"][path]["hrefs"]
        assert "/pulse/reels" in hrefs, f"{path} stopped offering Reels"


def test_the_withdrawn_route_still_answers(nav_probe):
    """Withdrawing is not removing.

    ``/pulse/videos/<id>`` permalinks are already in circulation. There is no
    catch-all rule and no 404 handler here, so deleting the route would turn every
    shared link into a bare 404 rather than into a redirect to Reels.
    """
    page = nav_probe["pages"][WITHDRAWN_PAGE]
    assert page["status"] == 200, f"{WITHDRAWN_PAGE} -> {page['status']}"
    assert page["bytes"] > 2000, (
        f"{WITHDRAWN_PAGE} answers 200 with only {page['bytes']} bytes, which is a "
        "shell with no page in it"
    )


def test_a_members_own_videos_stay_reachable(nav_probe):
    """Hiding the tab must not orphan content people uploaded.

    The creator tools are the way back in. If this link goes too, a member with
    videos has no route to them from anywhere in the product -- the rows are still
    in the database and the pages still render, and nothing on the site will ever
    show them to their owner again.
    """
    page = nav_probe["pages"][CREATOR_TOOLS]
    assert page["status"] == 200, f"{CREATOR_TOOLS} -> {page['status']}"
    assert any(pulse_web_navigation.is_withdrawn(href) for href in page["hrefs"]), (
        f"{CREATOR_TOOLS} no longer links to a member's own videos, so uploaded "
        "video content is unreachable from anywhere on the site"
    )


def test_the_phone_navigation_survived(nav_probe):
    """The dock is hidden above 900px, so no desktop check would notice it go.

    Every nav list on this site is shared between widths and switched by media
    query, so the failure mode of filtering a shared list is not "desktop is
    wrong" but "the dock disappeared on phones".
    """
    for path in ("/pulse", "/pulse/saved", "/pulse/settings", "/pulse/notifications"):
        assert nav_probe["pages"][path]["bottom_nav"], f"{path} lost the phone dock"


def test_a_destination_is_recognised_by_its_url_not_its_label():
    """Relabelling or query-stringing an entry must not smuggle it back in.

    Follows ``services.app_promotion.is_app_first_href``: the question is asked of
    the URL. A nav list that spells it "Watch", or appends ``?tab=all``, is the
    same invitation to the same place.
    """
    for href in ("/pulse/videos", "/pulse/videos/", "/pulse/videos?tab=all",
                 "/pulse/videos#top"):
        assert pulse_web_navigation.is_withdrawn(href), href
    for href in ("/pulse/reels", "/pulse", "/pulse/videos-archive", "", None):
        assert not pulse_web_navigation.is_withdrawn(href), href

    kept = pulse_web_navigation.visible(
        [("Home", "/pulse"), ("Watch", "/pulse/videos?tab=all"), ("Reels", "/pulse/reels")])
    assert kept == [("Home", "/pulse"), ("Reels", "/pulse/reels")]

    # Three-tuples with an icon are what the dock uses; the href index is the same.
    kept = pulse_web_navigation.visible(
        [("Reels", "/pulse/reels", "▶"), ("Videos", "/pulse/videos", "▣")])
    assert kept == [("Reels", "/pulse/reels", "▶")]
