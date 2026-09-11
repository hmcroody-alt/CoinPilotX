"""One PulseSoc: the URL the native app has always published must resolve.

``mobile-native/src/navigation/linking.ts`` registers ``pulse/undx/actions`` as
UndxActionCenter's deep link, and ``nativeRouteActions.ts`` routes that exact
path. Every share sheet, push payload and in-app link naming that URL opens the
Action Center on a phone. On the web it was a 404 -- and a shared PulseSoc link
is opened in a browser more often than anywhere else.

This suite covers the page added for it, and it is deliberately in three
layers, because the three ways this page can be wrong are not visible to the
same kind of assertion:

*Reachability* is Python. The page has to render through ``pulse_social_shell``
rather than becoming yet another standalone template, and the navigation has to
carry it on every surface -- including the two mobile drawers, which are
hand-maintained lists that the desktop rail's catalogue did not reach.

*Copy parity* is Python reading the native screen. The five section titles and
their empty strings are pinned against ``UndxActionCenterScreen.tsx`` itself,
so if native rewords "UNDX has nothing waiting for you." the web fails here
rather than drifting quietly into a second vocabulary for the same product.

*Behaviour* is JavaScript, in ``undx_action_center_harness.js``. What this page
can get wrong -- telling a member their governance queue is empty when the
fetch in fact failed -- happens across a fetch boundary in the browser, and no
amount of asserting on served HTML can see it.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
HARNESS = os.path.join(HERE, "undx_action_center_harness.js")
NATIVE_SCREEN = os.path.join(
    REPO, "mobile-native", "src", "screens", "UndxActionCenterScreen.tsx")

PAGE = "/pulse/undx/actions"

#: A page from the other shell, to prove the navigation carries the new
#: destination everywhere rather than only on the page itself.
SHELL_PAGE = "/pulse/saved"
FEED_PAGE = "/pulse"

_PROBE = r"""
import json, re, sys
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "undx-action-center"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        ("undxprobe", "undxprobe@example.com", "UNDX Probe"),
    )
    conn.commit()
    user_id = cur.lastrowid

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = user_id

RAIL = re.compile(r"<a\b[^>]*class='desktop-rail-link[^']*'[^>]*href='([^']*)'")
DRAWER = re.compile(r"<a\b[^>]*class='drawer-link'[^>]*href='([^']*)'")

report = {"pages": {}, "enabled": bot._business_os_undx_actions_enabled()}
for path in %(paths)r:
    response = client.get(path)
    body = response.get_data(as_text=True)
    report["pages"][path] = {
        "status": response.status_code,
        "rail": RAIL.findall(body),
        "drawer": DRAWER.findall(body),
        "topbar": "pulse-desktop-topbar" in body,
        # Attribute order is fixed by the f-string that emits each panel, so
        # matching the whole tag is exact rather than a substring guess.
        "loading_hidden": "<section class=\"card\" data-undx-loading hidden>" in body,
        "loading_present": "data-undx-loading" in body,
        "error_hidden": 'data-undx-error role="alert" hidden' in body,
        "body_hidden": '<div data-undx-body hidden>' in body,
        "empty_strings": re.findall(r"data-undx-empty=\"([^\"]*)\"", body),
        "sections": re.findall(r"data-undx-section='([^']*)'", body),
        "titles": re.findall(r"<section class='card undx-section'><h2>([^<]*)</h2>", body),
    }

report["client"] = bot.UNDX_ACTION_CENTER_PAGE_JS
sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


def _probe(flag):
    workdir = tempfile.mkdtemp(prefix="undx-actions-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "undx.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    if flag is None:
        env.pop("BUSINESS_OS_UNDX_ACTIONS", None)
    else:
        env["BUSINESS_OS_UNDX_ACTIONS"] = flag
    code = _PROBE % {"repo": REPO, "paths": [PAGE, SHELL_PAGE, FEED_PAGE]}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


@pytest.fixture(scope="module")
def enabled():
    """The app as production runs it: ``BUSINESS_OS_UNDX_ACTIONS`` is on.

    Importing ``bot`` binds ``DATABASE_URL`` for the whole pytest process and
    cannot be undone, which is why every web-surface suite pays for a
    subprocess rather than importing the module.
    """
    return _probe("true")


@pytest.fixture(scope="module")
def disabled():
    """The same app with the flag off, which is what a fresh checkout gets."""
    return _probe(None)


# --- reachability -----------------------------------------------------------

def test_the_native_deep_link_resolves_on_the_web(enabled):
    """The whole point: ``/pulse/undx/actions`` is a page, not a 404."""
    assert enabled["pages"][PAGE]["status"] == 200, enabled["pages"][PAGE]["status"]


def test_the_page_is_rendered_by_the_shell_and_not_by_a_new_one(enabled):
    """PulseSoc already has four web shells; this must not be the fifth.

    ``templates/pulse_messages_v2.html`` and
    ``templates/pulsesoc_intelligence_center.html`` are both full pages with
    their own chrome, which is how Messenger ended up with no navigation and
    the Intelligence Center with a seven-link bar of its own. A new page that
    renders its own ``<html>`` would be the same mistake a third time.
    """
    page = enabled["pages"][PAGE]
    assert page["topbar"], "the Action Center renders without the shell top bar"
    assert page["rail"], "the Action Center renders without the shell rail"


def test_every_shell_offers_the_destination(enabled):
    """A page nothing links to is a page nobody finds.

    Checked on the other two shells rather than on the Action Center itself,
    because a rail that lists a destination only while you are already standing
    on it is not navigation.
    """
    for path in (SHELL_PAGE, FEED_PAGE):
        assert PAGE in enabled["pages"][path]["rail"], (
            f"{path}'s rail does not offer {PAGE}: {enabled['pages'][path]['rail']}")


def test_the_phone_drawers_offer_it_too(enabled):
    """Above 900px the drawer is hidden and the rail is the navigation. Below
    it, the rail is hidden and the drawer is -- and the two drawers are
    hand-written lists that predate ``pulse_shell_rail_items``.

    So a destination added to the catalogue reached desktop and stopped at the
    phone, which is the surface most people use. This is the assertion that
    keeps ``pulse_shell_drawer_extras`` doing its job; without it the page
    added above would be desktop-only and every check in this file would still
    pass.
    """
    for path in (SHELL_PAGE, FEED_PAGE):
        drawer = enabled["pages"][path]["drawer"]
        assert drawer, f"{path} rendered no drawer at all"
        assert PAGE in drawer, f"{path}'s drawer does not offer {PAGE}: {drawer}"


def test_the_flag_being_off_hides_the_page_and_the_link_together(disabled):
    """The backend dark-404s every ``/api/business-os/undx`` route when the flag
    is off -- it declines to confirm the feature exists. A page that rendered
    anyway would answer the question the 404 refuses to, and a rail that linked
    a 404 would be worse than either.

    Both halves are asserted together because getting one right and the other
    wrong is the likely failure: the page is gated at the top of the view, the
    link is gated inside a list of twenty-three others.
    """
    assert disabled["enabled"] is False, "the probe did not actually clear the flag"
    assert disabled["pages"][PAGE]["status"] == 404, disabled["pages"][PAGE]["status"]
    for path in (SHELL_PAGE, FEED_PAGE):
        assert PAGE not in disabled["pages"][path]["rail"], f"{path}'s rail links a dark 404"
        assert PAGE not in disabled["pages"][path]["drawer"], f"{path}'s drawer links a dark 404"


# --- copy parity ------------------------------------------------------------

def _native_source():
    with open(NATIVE_SCREEN, encoding="utf-8") as handle:
        return handle.read()


def test_the_sections_are_the_native_screen_s_sections(enabled):
    """Same product, same five sections, in the same order.

    Read out of ``UndxActionCenterScreen.tsx`` rather than repeated here, so
    this cannot pass by agreeing with a copy of native that native has since
    moved on from.
    """
    native = re.findall(r'<Section title="([^"]+)" empty="([^"]+)">', _native_source())
    assert len(native) == 5, f"the native screen no longer has five sections: {native}"

    page = enabled["pages"][PAGE]
    assert page["titles"] == [title for title, _empty in native], (
        f"web: {page['titles']}\nnative: {[t for t, _ in native]}")


def test_an_empty_section_says_what_native_says(enabled):
    """Five distinct sentences, not one shared "Nothing here yet".

    The distinctions carry information a member acts on -- "No decisions
    returned yet. Run evaluation after requests are recorded." tells you the
    queue is waiting on a step, which "No decisions." does not -- so they are
    pinned rather than paraphrased.
    """
    native = [empty for _title, empty in
              re.findall(r'<Section title="([^"]+)" empty="([^"]+)">', _native_source())]
    assert enabled["pages"][PAGE]["empty_strings"] == native, (
        f"web: {enabled['pages'][PAGE]['empty_strings']}\nnative: {native}")


def test_the_confirmations_key_is_deliberately_not_rendered(enabled):
    """``engine.action_center`` returns ``confirmations`` and neither client
    shows it. That is the correct answer, not an oversight.

    Adding a sixth section here would be web-only surface -- exactly what this
    program exists to remove -- so this pins the absence, and names why, rather
    than leaving the next reader to wonder whether it was forgotten.
    """
    assert "confirmations" not in enabled["pages"][PAGE]["sections"], (
        "a Confirmations section appeared on the web with no native counterpart")


# --- first paint ------------------------------------------------------------

def test_nothing_is_claimed_before_anything_is_known(enabled):
    """The served HTML must not already contain a visible answer.

    Every count and every empty sentence on this page is a claim about server
    state that no fetch has yet returned. Shipping them visible means the first
    frame reads "UNDX has nothing waiting for you." before the request has even
    left the browser, and then quietly changes its mind -- which is the same
    lie as the native screen's, just briefer.
    """
    page = enabled["pages"][PAGE]
    assert page["loading_present"], "no loading state was rendered at all"
    assert not page["loading_hidden"], "the loading panel ships hidden"
    assert page["error_hidden"], "the error panel ships visible"
    assert page["body_hidden"], (
        "the sections ship visible, so their empty copy is on screen "
        "before the first fetch resolves")


# --- behaviour --------------------------------------------------------------

def test_the_client_behaves(enabled, tmp_path):
    """Run the page's JavaScript against a stub DOM and stub responses.

    The checks live in ``undx_action_center_harness.js``; this test's job is to
    hand it the client the server actually serves rather than a copy. The
    client is read back off ``bot.UNDX_ACTION_CENTER_PAGE_JS`` in the probe for
    that reason -- extracting it from the rendered page would work too, but it
    would also silently keep passing if the page stopped including it.
    """
    client = tmp_path / "undx_client.js"
    client.write_text(enabled["client"], encoding="utf-8")
    proc = subprocess.run(["node", str(HARNESS), str(client)],
                          capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stderr or proc.stdout
