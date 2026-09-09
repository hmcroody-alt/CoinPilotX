"""One PulseSoc: the two web shells must render the same navigation.

The site is served by two shells. ``pulse_page_html`` renders nine feed routes
with a desktop top bar, a search field, an account menu and a left rail.
``pulse_social_shell`` renders the other ninety-six -- Saved, Settings, Premium,
Notifications, Messenger and the rest -- and used to render a seven-link row
instead. Above 900px the drawer holding the remaining links is ``display:none``
and its only opener lives inside the mobile top bar, which is also hidden, so
twenty destinations were unreachable on a desktop browser. Two products on one
domain, and the smaller one was what most pages got.

Both shells now read ``bot.pulse_shell_rail_items``. This suite exists to keep
that true, because nothing about the arrangement is self-enforcing: the shells
are f-strings a thousand lines apart in a 120k-line module, and the natural way
to add a destination is to edit the one in front of you.

## What the CSS half is doing here

Three of the regressions this suite pins were not in Python at all. The rail's
links were hidden by ``display: none`` in ``pulse_home_os.css``, re-enabled by an
allowlist of nine hardcoded hrefs -- so a destination added in ``bot.py`` was in
the DOM, laid out at zero height, with nothing failing anywhere. Separately, two
stylesheets painted a permanent highlight on fixed links (Home in the top bar,
Premium in the rail), which read as a selected state and was a lie on ninety-five
pages out of ninety-six.

A Python-only suite cannot see any of that. It is asserted against the stylesheet
text, which is coarse, but the alternative is a suite that certifies a navigation
the member cannot see.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
BOT = os.path.join(REPO, "bot.py")
CSS = os.path.join(REPO, "static", "css")

#: Pages served by ``pulse_social_shell``. Deliberately spread across
#: subsystems, because the shell branches on some of them -- the intro block and
#: the side column differ between Premium and Settings.
SHELL_PAGES = ("/pulse/saved", "/pulse/settings", "/pulse/premium",
               "/pulse/notifications", "/pulse/collections")

#: Served by ``pulse_page_html``, the other shell.
FEED_PAGE = "/pulse"

#: Served by neither: ``templates/pulse_messages_v2.html``, rendered directly.
#: This suite found it by asserting the shell chrome on it and getting nothing
#: back -- not a 404, a 200 with no navigation of any kind. See the test at the
#: bottom of the file for what is and is not being claimed about it.
TEMPLATE_PAGE = "/pulse/messages"

_PROBE = r"""
import json, re, sys
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "shell-nav-parity"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        ("shellnav", "shellnav@example.com", "Shell Nav"),
    )
    conn.commit()
    user_id = cur.lastrowid

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = user_id

# Anchors are matched by tag rather than counted as substrings. An earlier
# version of this check counted `body.count("desktop-rail-link")` and was fooled
# by a stylesheet rule of that name embedded in the page, reporting a
# discrepancy between two rails that were in fact identical.
LINK = re.compile(r"<a\b([^>]*class='desktop-rail-link[^']*'[^>]*)>")
HREF = re.compile(r"href='([^']*)'")

def rail(body):
    out = []
    for attrs in LINK.findall(body):
        href = HREF.search(attrs)
        out.append({"href": href.group(1) if href else "",
                    "current": "aria-current" in attrs})
    return out

report = {"pages": {}}
for path in %(paths)r:
    response = client.get(path)
    body = response.get_data(as_text=True)
    report["pages"][path] = {
        "status": response.status_code,
        "rail": rail(body),
        "topbar": "pulse-desktop-topbar" in body,
        "search": "data-pulse-search-input" in body,
        "bottom_nav": "mobile-bottom-nav" in body,
    }
    # The top bar has two halves: a visible row of six primary destinations and
    # an "Apps" dropdown holding the other twenty. They are captured separately
    # because every page this suite visits lives in the dropdown, so a check
    # that merged them would pass while the visible row marked nothing at all.
    nav_open = body.find("pulse-desktop-nav")
    apps_open = body.find("pulse-desktop-more-menu", nav_open if nav_open >= 0 else 0)
    nav_close = body.find("</div>", apps_open if apps_open >= 0 else 0)
    marked = lambda chunk: re.findall(r"<a aria-current='page' href='([^']*)'", chunk)
    report["pages"][path]["topbar_primary_current"] = (
        marked(body[nav_open:apps_open]) if nav_open >= 0 and apps_open > nav_open else [])
    report["pages"][path]["topbar_apps_current"] = (
        marked(body[apps_open:nav_close]) if apps_open >= 0 and nav_close > apps_open else [])

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def nav_probe():
    """Boot the app once, in a child process, and report what each shell renders.

    Importing ``bot`` binds ``DATABASE_URL`` for the whole pytest process and
    cannot be undone, which is why every web-surface suite pays for a subprocess
    rather than importing the module directly.
    """
    workdir = tempfile.mkdtemp(prefix="shell-nav-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "nav.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {"repo": REPO,
                     "paths": list(SHELL_PAGES) + [FEED_PAGE, TEMPLATE_PAGE]}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


def _hrefs(page):
    return [link["href"] for link in page["rail"]]


def test_both_shells_render_the_same_destinations(nav_probe):
    """The whole point. Not "both have a rail" -- the same rail.

    A weaker assertion (each shell renders *some* links) would pass the exact
    state this change exists to remove, where the feed had twenty-three and
    everything else had seven.
    """
    feed = _hrefs(nav_probe["pages"][FEED_PAGE])
    assert feed, "the feed rendered no rail at all"
    for path in SHELL_PAGES:
        page = _hrefs(nav_probe["pages"][path])
        assert page == feed, (
            f"{path} renders a different navigation from {FEED_PAGE}.\n"
            f"only on {path}: {sorted(set(page) - set(feed))}\n"
            f"only on the feed: {sorted(set(feed) - set(page))}"
        )


def test_every_shell_page_carries_the_desktop_chrome(nav_probe):
    """A rail with no top bar is half the fix: no search, no account menu."""
    for path in SHELL_PAGES:
        page = nav_probe["pages"][path]
        assert page["status"] == 200, f"{path} -> {page['status']}"
        assert page["topbar"], f"{path} shipped no desktop top bar"
        assert page["search"], f"{path} shipped no search field"


def test_the_phone_navigation_survived(nav_probe):
    """The desktop chrome is additive.

    Everything here renders at every width and is switched by media query, so
    the failure mode of this change is not "desktop is wrong" but "the bottom
    nav disappeared on phones", which no desktop check would notice.
    """
    for path in SHELL_PAGES:
        assert nav_probe["pages"][path]["bottom_nav"], f"{path} lost the mobile bottom nav"


def test_exactly_one_destination_is_marked_current(nav_probe):
    """Zero is the state this replaces -- every page looked identical.

    Two would be worse than zero: prefix matching is what lets a detail page
    light its section, and it is also what would light both ``/pulse/settings``
    and a hypothetical ``/pulse/settings-legacy`` if the matcher used a bare
    ``startswith``.
    """
    for path in SHELL_PAGES + (FEED_PAGE,):
        page = nav_probe["pages"][path]
        current = [link["href"] for link in page["rail"] if link["current"]]
        assert len(current) == 1, f"{path} marked {current} as current"
        assert current[0].split("#")[0] == path, f"{path} marked {current[0]}"


def test_the_top_bar_marks_the_page_you_are_on(nav_probe):
    """It used to mark Home, always, from a stylesheet.

    That was survivable while the bar only appeared on the feed, where Home was
    the current page. It renders on ninety-six more pages now.

    Both halves of the bar are checked. Asserting only that *something* in the
    bar carried the marking let a mutation that stripped the primary row pass,
    because Saved, Settings, Premium, Notifications and Collections all live in
    the Apps dropdown -- the visible row could go dark with the suite green.
    """
    feed = nav_probe["pages"][FEED_PAGE]
    assert feed["topbar_primary_current"] == ["/pulse"], (
        f"the visible top-bar row did not mark Home on the feed: "
        f"{feed['topbar_primary_current']}"
    )
    for path in SHELL_PAGES:
        page = nav_probe["pages"][path]
        assert page["topbar_apps_current"] == [path], (
            f"{path}: the Apps panel marked {page['topbar_apps_current']}"
        )
        assert not page["topbar_primary_current"], (
            f"{path}: the visible row marked {page['topbar_primary_current']}, but "
            f"this page is not one of the six primary destinations"
        )


def test_messenger_is_a_third_surface_and_is_not_silently_counted_as_parity(nav_probe):
    """Unifying two shells left a third one standing. This records that.

    ``/pulse/messages`` renders ``templates/pulse_messages_v2.html`` directly, so
    it has no rail, no top bar and no bottom nav -- on any width. It answers 200,
    which is why a census that counts status codes reads it as served: the URL
    resolves, the product is there, and the navigation is not.

    Whether that is a defect is a product question. A full-screen conversation
    view suppressing chrome is exactly what the native app does, and this shell
    already suppresses the rail on immersive surfaces for that reason. What is
    not defensible is having no way out, so that is what is asserted. The
    remaining gap -- that Messenger is a third shell rather than the second one
    in immersive mode -- is left open deliberately and named here so it is not
    mistaken for parity later.
    """
    page = nav_probe["pages"][TEMPLATE_PAGE]
    assert page["status"] == 200
    assert not page["rail"], (
        "Messenger grew a rail; if it was moved onto pulse_social_shell then it "
        "belongs in SHELL_PAGES and this test should go"
    )
    with open(os.path.join(REPO, "templates", "pulse_messages_v2.html"),
              encoding="utf-8") as handle:
        template = handle.read()
    assert 'href="/pulse"' in template, (
        "Messenger renders no navigation of its own and no link back to PulseSoc, "
        "which strands a member on a full-screen page with only the back button"
    )


def _css(name):
    with open(os.path.join(CSS, name), encoding="utf-8") as handle:
        return handle.read()


def test_no_stylesheet_hides_rail_links_by_href():
    """The nine-href allowlist, and any successor to it.

    ``.desktop-rail-link { display: none }`` plus an allowlist of hrefs made the
    rail a second, invisible navigation: the markup offered everything and CSS
    showed nine. Nothing failed when the two disagreed -- the link was in the
    DOM at zero height. Restoring that pattern would silently undo this change,
    so the shape is refused rather than the specific nine.
    """
    for name in ("pulse_home_os.css", "pulse_desktop_feed.css"):
        source = _css(name)
        offenders = re.findall(r"\.desktop-rail-link\[href=[^\]]*\][^{]*\{[^}]*display\s*:\s*grid",
                               source)
        assert not offenders, (
            f"{name} re-enables rail links by href, which means something above "
            f"it is hiding them: {offenders}"
        )


def test_no_stylesheet_paints_a_permanent_selected_state():
    """Selection is a fact about the request, not about a URL.

    Home in the top bar and Premium in the rail were both lit unconditionally by
    href-keyed rules. On the feed that was accidentally true; everywhere else it
    told a member they were on a page they were not on. Both now key on
    ``aria-current``, which the shell sets from the request path.
    """
    assert '.pulse-desktop-nav > a[href="/pulse"]' not in _css("pulse_home_os.css"), (
        "the top bar lights Home from a stylesheet again; it must key on aria-current"
    )
    # Comments are stripped first. An earlier version of this assertion matched
    # the explanatory comment above the rule, which quotes the very selector it
    # is explaining, and reported the unscoped form that no longer exists.
    design = re.sub(r"/\*.*?\*/", "", _css("pulse_design_system.css"), flags=re.S)
    selectors = re.findall(r'a\[href="/pulse/premium"\][^\s,{]*', design)
    assert selectors, "the premium call-to-action rule is gone entirely; expected it scoped"
    unscoped = [s for s in selectors if ":not(.desktop-rail-link)" not in s]
    assert not unscoped, (
        f"the gold premium treatment matches navigation links again ({unscoped}), so "
        f"the rail renders Premium as a permanently lit call to action"
    )


def test_the_rail_can_grow_past_the_fold():
    """``content-visibility: auto`` on the shared card class silently truncated it.

    The card is a feed optimisation; applied to a twenty-three item rail whose
    height exceeds the viewport, it skipped everything past the first screenful
    and sized the card to the intrinsic-size placeholder. Six links rendered.
    The rail is scrollable, so length is not a layout problem -- but only if the
    content is laid out in the first place.
    """
    with open(BOT, encoding="utf-8") as handle:
        source = handle.read()
    assert ".pulse-shell-rail .desktop-rail-card" in source, (
        "the social shell no longer overrides the shared rail card, so it inherits "
        "the feed's content-visibility and truncates the rail to one screenful"
    )
    assert "content-visibility:visible" in source, (
        "the content-visibility override is gone; the rail will render only the "
        "links that fit above the fold, with no error anywhere"
    )


def test_the_shell_frame_does_not_reuse_the_feeds_class_names():
    """``.pulse-desktop-center > .layout { display: block !important }``.

    The feed moves its side column into a rail of its own, so it flattens the
    inner two-column layout and hides the aside. Correct for the feed; on a
    settings page it collapsed the right-hand column into the main one. The
    shell carries ``pulse-shell-frame``/``pulse-shell-center`` so that rule --
    and the one hiding ``.pulse-desktop-left`` between 1024px and 1279px --
    cannot reach it.
    """
    with open(BOT, encoding="utf-8") as handle:
        source = handle.read()
    shell = source.split("def pulse_social_shell", 1)
    assert len(shell) == 2, "pulse_social_shell was renamed; this suite needs updating"
    body = shell[1]
    for feed_class in ("pulse-desktop-layout", "pulse-desktop-center", "pulse-desktop-left"):
        assert f'class="{feed_class}"' not in body and f"class='{feed_class}'" not in body, (
            f"pulse_social_shell renders {feed_class!r}, which the feed stylesheet "
            f"styles on feed-specific assumptions"
        )
