"""Every URL the app hands out has to land on a page, or a shared link 404s.

``mobile-native/src/navigation/linking.ts`` lists ``https://pulsesoc.com`` among
its ``prefixes``. That makes the linking config bidirectional in a way that
matters here: the app not only *opens* those paths, it *mints* them, so every
path in it is simultaneously a native deep link and a URL the web server is
expected to answer. And a PulseSoc link is opened in a browser more often than
anywhere else.

There is no catch-all rule and no 404 handler on this app, so the failure mode is
not a branded "page moved" screen. It is whatever happens to match. The one this
suite was written for: the root ``Saved`` screen is declared as bare ``saved``,
and ``/saved`` fell through to ``/<slug>`` -- the SEO topic-page rule -- which
answers anything it does not recognise with nine bytes of plain-text "Not found".

What makes that worth a permanent guard is that it read as covered from the
outside. ``docs/parity/PULSESOC_WEB_NATIVE_PARITY_MATRIX.md`` scores ``/saved``
as PARITY, because it is generated from the booted ``url_map`` and ``/<slug>``
genuinely *matches* ``/saved``. A rule existing is not the same as a page being
served, and nothing that stops at the url_map can tell the two apart. The same
blind spot covers every one of the sixteen destinations the matrix calls
REDIRECT_ONLY: a redirect that lands on a page is a fine web-specific
equivalent, a redirect that lands on a login loop or a dead rule is dead
navigation, and the verdict is identical for both.

So this asks the only question that distinguishes them, and asks it of a booted
server: follow the chain and see what a member actually receives. All 83 concrete
destinations, not just the ones some other report flagged, because the point is
to catch the next one.

The destination list is derived, never pinned. ``extract_native_surfaces.py``
reads ``masterNavigation.ts`` and ``linking.ts`` -- the app's own declarations --
so a destination added to the app tomorrow is checked here without anyone
remembering to add it. A pinned list would go stale in exactly the direction that
hides gaps.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
EXTRACTOR = os.path.join(REPO, "scripts", "parity", "extract_native_surfaces.py")

#: Feature flags switched on for the probe rather than exempting the routes they
#: gate. ``/pulse/undx/actions`` answers a deliberate dark 404 when
#: ``BUSINESS_OS_UNDX_ACTIONS`` is unset -- "matching every
#: /api/business-os/undx/* route rather than explaining to a stranger that a
#: feature they cannot reach exists". Exempting the path would stop checking
#: whether the page still renders at all; turning the flag on keeps it covered
#: and still leaves the gate itself asserted by
#: ``test_undx_action_center_page.py``.
PROBE_FLAGS = {"BUSINESS_OS_UNDX_ACTIONS": "1"}

#: A destination the app declares and the site must serve. Asserted to be in the
#: derived set, so an extractor that silently starts returning nothing -- a
#: renamed const in ``masterNavigation.ts`` is enough -- fails here instead of
#: turning this whole suite into 0 checks that pass.
SENTINEL_DESTINATIONS = ("/pulse", "/pulse/reels", "/pulse/saved", "/saved")

_PROBE = r"""
import json, sys, time
sys.path.insert(0, %(repo)r)
import bot
from urllib.parse import urlsplit

app = bot.webhook_app
app.config["SECRET_KEY"] = "native-destinations"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
        ("destwalk", "destwalk@example.com", "Destination Walk"),
    )
    conn.commit()
    user_id = cur.lastrowid

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = session["user_id"] = user_id

report = {"destinations": {}}
for path in %(targets)r:
    current, seen, chain = path, set(), []
    terminal = None
    # Eight hops is far more than any real alias chain here (the longest is two)
    # and still terminates rather than hanging the suite on a redirect cycle.
    for _ in range(8):
        if current in seen:
            terminal = {"status": "LOOP", "path": current}
            break
        seen.add(current)
        try:
            response = client.get(current)
        except Exception as exc:
            terminal = {"status": "RAISED", "path": current, "error": repr(exc)[:300]}
            break
        chain.append([current, response.status_code])
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") or ""
            if location.startswith("http"):
                split = urlsplit(location)
                location = split.path + (("?" + split.query) if split.query else "")
            if not location:
                terminal = {"status": "NO_LOCATION", "path": current}
                break
            current = location
            continue
        body = response.get_data(as_text=True)
        head = body[:400].lower()
        terminal = {
            "status": response.status_code,
            "path": current,
            "bytes": len(body),
            "html": "<html" in head or "<!doctype" in head,
        }
        break
    report["destinations"][path] = {"chain": chain, "terminal": terminal}

# `/<slug>` is the SEO topic-page rule, not a catch-all. Recorded from inside the
# app so the claim this suite rests on is checked rather than assumed.
unknown = client.get("/this-slug-does-not-exist-%%d" %% int(time.time()))
report["unknown_slug"] = {"status": unknown.status_code,
                          "bytes": len(unknown.get_data(as_text=True))}

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


def _native_destinations() -> tuple[list[str], list[str]]:
    """Concrete and parametric destinations, read from the app's own source."""
    proc = subprocess.run([sys.executable, EXTRACTOR], cwd=REPO,
                          capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        pytest.fail("extract_native_surfaces.py failed:\n" + proc.stderr[-4000:])
    surfaces = json.loads(proc.stdout)
    routes = {entry["route"] for entry in surfaces["master_navigation"]}
    routes |= {"/" + path for path in surfaces["links"].values()}
    # A parametric destination has no single URL to fetch: `/pulse/groups/:slug`
    # needs a group that exists, which is a fixture question rather than a
    # reachability one. They are counted, not skipped silently.
    concrete = sorted(r for r in routes if ":" not in r)
    parametric = sorted(r for r in routes if ":" in r)
    return concrete, parametric


@pytest.fixture(scope="module")
def destination_walk():
    """Boot once and follow every concrete destination to its terminus.

    Importing ``bot`` binds ``DATABASE_URL`` process-wide and runs ``init_db()``
    at module scope, which is why this is a subprocess rather than an import.
    """
    concrete, parametric = _native_destinations()
    workdir = tempfile.mkdtemp(prefix="native-destinations-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "destinations.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    env.update(PROBE_FLAGS)
    code = _PROBE % {"repo": REPO, "targets": concrete}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    report = parse_report(proc.stdout, proc.stderr)
    report["concrete"] = concrete
    report["parametric"] = parametric
    return report


def test_the_destination_list_is_really_derived(destination_walk):
    """Guard the guard: an empty derivation would pass every other test here.

    ``extract_native_surfaces.py`` finds destinations with regexes over
    TypeScript. A rename in ``masterNavigation.ts`` that the regex stops matching
    does not raise -- it returns fewer rows, and a suite that iterates them gets
    quieter rather than redder.
    """
    concrete = destination_walk["concrete"]
    assert len(concrete) >= 70, (
        f"only {len(concrete)} concrete destinations were extracted from the "
        "native app; the extractor's regexes have probably stopped matching "
        "masterNavigation.ts or linking.ts, which makes every other test in "
        "this file vacuous"
    )
    missing = [d for d in SENTINEL_DESTINATIONS if d not in concrete]
    assert not missing, (
        f"{missing} are declared by the native app but did not come out of the "
        "extractor, so this suite is no longer checking what it claims to"
    )


def test_every_native_destination_lands_on_a_page(destination_walk):
    """The whole point, asserted against what a browser actually receives.

    Reported in full rather than failing on the first one, because these come in
    families -- a redirect helper that starts returning ``None`` takes out every
    alias at once, and a one-at-a-time failure hides the shape of that.
    """
    broken = []
    for path in destination_walk["concrete"]:
        walked = destination_walk["destinations"][path]
        terminal = walked["terminal"] or {}
        if terminal.get("status") == 200 and terminal.get("html"):
            continue
        broken.append(
            f"  {path} -> terminus {terminal.get('status')} at "
            f"{terminal.get('path')} ({terminal.get('bytes', '?')} bytes, "
            f"html={terminal.get('html')}) via {walked['chain']}"
        )
    assert not broken, (
        "the native app publishes these URLs and the web does not serve a page "
        "at them. There is no catch-all rule and no 404 handler, so a shared "
        "link to one of these gets whatever happens to match:\n"
        + "\n".join(broken)
    )


def test_the_slug_rule_is_not_a_catch_all(destination_walk):
    """Why a url_map match does not mean a page.

    ``/<slug>`` matches any single-segment path, which is what made the matrix
    score ``/saved`` as PARITY. It serves SEO topic pages and answers anything
    else with a bare 404, so a destination that "matches a rule" can still be a
    dead link. If this ever becomes a real catch-all, the reasoning above stops
    holding and this suite should be re-read rather than trusted.
    """
    unknown = destination_walk["unknown_slug"]
    assert unknown["status"] == 404, (
        "/<slug> now answers an unrecognised path with "
        f"{unknown['status']}. That changes what a url_map match implies: this "
        "suite exists because matching /<slug> did not mean a page was served."
    )
    assert unknown["bytes"] < 1000, (
        f"/<slug> served {unknown['bytes']} bytes for an unknown path -- if it "
        "has grown a real branded 404 page, say so here and in the matrix."
    )


def test_parametric_destinations_are_accounted_for(destination_walk):
    """Not checked here, and recorded as such rather than quietly dropped.

    ``/pulse/groups/:groupSlug`` needs a group that exists before it can 200, so
    reachability for these is a fixture question. This test exists so the
    exclusion is a visible number: if the app ever expresses most of its
    destinations parametrically, the walk above shrinks and someone should notice
    from here rather than from a gap in production.
    """
    parametric = destination_walk["parametric"]
    concrete = destination_walk["concrete"]
    assert len(parametric) < len(concrete) / 2, (
        f"{len(parametric)} parametric vs {len(concrete)} concrete destinations. "
        "This suite only walks concrete ones, so it now covers a minority of "
        "what the app publishes; parametric coverage needs fixtures."
    )
