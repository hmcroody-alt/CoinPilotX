"""Every public marketing page promotes the iOS app, and promotes it one way.

"Promote the app throughout the website" is a standing product requirement, and
it is the kind of requirement that decays silently. Nobody deletes a CTA on
purpose; a page gets rewritten, the block does not come back, and the site is
still perfectly functional. There is no error, no failing request, and no test
-- which is why there is one here.

This file protects two different things, and they fail for different reasons.

* `test_every_marketing_page_*` pin the **coverage**. Every public route that a
  visitor can land on cold from search carries a link into the app and a link to
  the store, and both are macro-built.

* `test_the_app_store_id_lives_in_exactly_one_place` and its siblings pin the
  **singularity**. Coverage tests stay green if somebody hand-writes a second
  App Store URL into a template tomorrow, because the first one still renders.
  A second source of truth is not visible from behaviour; it is visible from the
  source. The standing rule is to go through `services/app_links.py` and
  `templates/_app_link_cta.html` and never to hand-write the link, and that rule
  is only real if something checks it.

Two things learned the hard way, both encoded below:

**Attribution fails open.** `APP_LINK_SOURCES` in services/app_links.py is a
closed set of eight *channels* -- email, push, sms, share, qr, web, system,
invite -- and `normalize_source()` replaces anything outside it with "system"
rather than raising. So `source="privacy"` renders a perfectly valid-looking
link that has quietly lost its attribution. That is not hypothetical: these
pages were written that way first, and the rendered href read `pulse_src=system`
while the template read `source="privacy"`. Reading the template cannot catch
this. Only reading the rendered href can, so that is what
`test_the_app_open_links_keep_their_attribution` does.

**An empty check passes.** If `MARKETING_ROUTES` ever shrinks -- a route
renamed, a list edited -- every loop below iterates less and the file gets
*greener*. So the routes are a hardcoded contract checked against the live URL
map, and a route that is no longer registered is a failure rather than one fewer
iteration.

Run: python3 tests/protection/test_web_app_promotion.py
"""

from __future__ import annotations

import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A scratch database, so importing bot cannot touch the working copy's
# coinpilotx.db. Same convention as tests/protection/test_route_auth.py.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(
    prefix="web-app-promotion-", suffix=".db"
)[1]
os.environ.setdefault("FLASK_SECRET_KEY", "web-app-promotion-protection")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot  # noqa: E402
from services import app_links  # noqa: E402


# The contract. Every public surface a visitor can reach cold -- the homepage,
# the two legal pages, and all four spellings of the help page. Support is one
# view behind four routes; all four are listed because a visitor who follows
# /pulse/support from a share link deserves the same page as one who types
# /help, and an alias that silently stopped rendering the CTA would otherwise be
# invisible here.
MARKETING_ROUTES = (
    "/",
    "/privacy",
    "/terms",
    "/help",
    "/pulse/help",
    "/support",
    "/pulse/support",
)

APP_STORE_ID = "id6777591572"

# Where the App Store URL is allowed to be spelled out. Exactly one file.
APP_STORE_URL_HOME = os.path.join("services", "app_links.py")

# What "shipped" means for the rule below: the surfaces that render to a user.
# Deliberately not the whole tree. tests/ and scripts/ also spell the literal
# out -- tests/test_app_intent_fallback_router.py pins it, and two of the
# scripts/*_audit.py files assert it is *absent* from shipped output, which is
# this same rule enforced from another angle. A literal in a test cannot
# silently diverge, because a test that disagrees with the constant fails; a
# literal in a template ships. The distinction is "does a user receive this
# string", not "is this file source code".
#
# Listed as roots rather than as an exclusion list on purpose. An exclusion list
# grows a hole every time a directory is added and nobody remembers to exclude
# it; this way a new shipped directory is simply not covered until it is named,
# which is visible, rather than covered-by-accident-then-quietly-not.
SHIPPED_ROOTS = (
    "templates",
    "static",
    os.path.join("web", "src"),
    os.path.join("mobile-native", "src"),
    "services",
    "bot.py",
)

_ANCHOR_RE = re.compile(r"<a\b[^>]*\bdata-app-link=\"([^\"]*)\"[^>]*>", re.I)
_HREF_RE = re.compile(r"\bhref=\"([^\"]*)\"", re.I)

_CLIENT = bot.webhook_app.test_client()
_RENDERED: dict = {}


def _render(path):
    """GET a marketing route once and cache it. Returns (status, html).

    A template that raises propagates out of the test client here rather than
    becoming a 500, which would surface as an unhandled UndefinedError inside
    whichever test happened to render first -- alphabetical order, so usually a
    CTA test, which reads like a CTA problem. Catching it turns that into a
    clean "this page did not render" from the test whose job that is.
    """
    if path not in _RENDERED:
        try:
            response = _CLIENT.get(path)
            _RENDERED[path] = (response.status_code, response.get_data(as_text=True))
        except Exception as exc:  # noqa: BLE001 - any template error, reported as one
            _RENDERED[path] = (500, f"<render raised {type(exc).__name__}: {exc}>")
    return _RENDERED[path]


def _app_links(html):
    """Every macro-built app link on a page, as {destination: href}.

    Keyed on `data-app-link`, which only `templates/_app_link_cta.html` emits.
    That is deliberate: a hand-written `<a href="https://apps.apple.com/...">`
    would be invisible to this extractor, so the coverage tests below cannot be
    satisfied by the thing the singularity tests forbid.
    """
    found = {}
    for match in _ANCHOR_RE.finditer(html):
        href = _HREF_RE.search(match.group(0))
        if href:
            # Jinja escapes `&` in attribute values; compare against the URL as
            # a browser would read it, not as the byte stream spells it.
            found[match.group(1)] = href.group(1).replace("&amp;", "&")
    return found


def _shipped_files():
    """Every file under SHIPPED_ROOTS that could plausibly spell a URL."""
    skip = {".git", "node_modules", "venv", ".venv", "__pycache__", "build", "dist"}
    wanted = (".py", ".html", ".js", ".jsx", ".ts", ".tsx", ".css", ".json")
    for root in SHIPPED_ROOTS:
        target = os.path.join(ROOT, root)
        if os.path.isfile(target):
            yield target
            continue
        for folder, folders, files in os.walk(target):
            folders[:] = [d for d in folders if d not in skip and not d.startswith(".")]
            for name in files:
                if name.endswith(wanted):
                    yield os.path.join(folder, name)


# --- The list itself is a claim ------------------------------------------


def test_the_route_list_is_not_empty():
    """An empty contract makes every loop below pass by doing nothing."""
    assert len(MARKETING_ROUTES) == 7, MARKETING_ROUTES


def test_every_marketing_route_is_actually_registered():
    """A renamed route must fail here rather than quietly drop out of the loop."""
    registered = {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}
    missing = [path for path in MARKETING_ROUTES if path not in registered]
    assert not missing, (
        f"{missing} are in this file's contract but not in the URL map. Either "
        f"the routes moved and this list needs updating, or a route pack failed "
        f"to register -- both of which would otherwise read as 'nothing to "
        f"check' rather than as a failure."
    )


def test_every_marketing_page_renders():
    """Also the guard for a macro imported without `with context`.

    Jinja imports are context-less by default, so an import missing
    `with context` leaves the macros unable to see `app_link` /
    `app_store_url`. Mutation testing settled what that actually does: it
    raises UndefinedError, it does not render a broken page. So this is where
    that mistake lands -- the page does not render at all.
    """
    for path in MARKETING_ROUTES:
        status, html = _render(path)
        assert status == 200, f"{path} returned {status}: {html[:200]}"
        assert html.strip(), f"{path} rendered empty"


def test_no_marketing_page_leaks_an_unrendered_template():
    """Nothing ships with template syntax still in it.

    Narrower than it looks, and deliberately kept after mutation testing showed
    what it does *not* catch: a missing `with context` raises rather than
    leaking, so that mistake is caught by the render test above, not here. What
    is left for this test is the quieter family -- a `{% raw %}` that was never
    closed, an escaped brace in copy, a macro call written as text -- all of
    which render 200 and put the mistake in front of the visitor.
    """
    for path in MARKETING_ROUTES:
        _, html = _render(path)
        for leak in ("{{", "{%", "Undefined"):
            assert leak not in html, f"{path} leaked {leak!r} into the response"


# --- Coverage -------------------------------------------------------------


def test_every_marketing_page_carries_an_app_open_cta():
    for path in MARKETING_ROUTES:
        _, html = _render(path)
        links = _app_links(html)
        opening = {dest: href for dest, href in links.items() if dest != "app-store"}
        assert opening, (
            f"{path} has no macro-built app-opening CTA. The standing "
            f"requirement is that the website promotes the app throughout; a "
            f"page that renders fine while promoting nothing is exactly the "
            f"silent decay this test exists to catch."
        )


def test_every_marketing_page_carries_an_app_store_link():
    for path in MARKETING_ROUTES:
        _, html = _render(path)
        assert "app-store" in _app_links(html), (
            f"{path} has no App Store badge. The opening CTA is for a visitor "
            f"who already has the app; this one is for the visitor who does not, "
            f"which on a page reached from search is most of them."
        )


def test_the_app_store_href_is_the_canonical_url():
    """Every badge resolves through app_links, not through a template literal."""
    canonical = app_links.app_store_url()
    assert APP_STORE_ID in canonical, canonical
    for path in MARKETING_ROUTES:
        _, html = _render(path)
        assert _app_links(html)["app-store"] == canonical, path


def test_the_app_open_links_keep_their_attribution():
    """`source=` fails open, so the rendered href is the only honest witness.

    An unrecognised source is replaced with "system" rather than refused, so a
    template can read `source="privacy"` and ship a link that lost its
    attribution. Reading the template would pass. Reading the href does not.
    """
    for path in MARKETING_ROUTES:
        _, html = _render(path)
        for dest, href in _app_links(html).items():
            if dest == "app-store":
                continue
            assert "pulse_src=" in href, f"{path}: {dest} carries no attribution"
            assert "pulse_src=system" not in href, (
                f"{path}: {dest} resolved to pulse_src=system. That is what "
                f"normalize_source() emits for a source outside APP_LINK_SOURCES "
                f"-- the link works and the attribution is gone. Use one of the "
                f"eight channels; per-page attribution needs its own parameter."
            )


# --- Singularity ----------------------------------------------------------


def test_the_app_store_id_lives_in_exactly_one_place():
    """One hand-written App Store URL is the whole point of app_links.py.

    Not a style preference. `app_store_url()` honours PULSESOC_APP_STORE_URL and
    validates it against the apps.apple.com prefix before trusting it; a literal
    baked into a template ignores both, so the site would ship two links that
    disagree the moment the configured one is used.
    """
    spelled = []
    scanned = 0
    for path in _shipped_files():
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                contents = handle.read()
        except OSError:
            continue
        scanned += 1
        if APP_STORE_ID in contents:
            spelled.append(os.path.relpath(path, ROOT))

    # A walk that reads nothing finds no violations, which is the same shape of
    # false green this whole file is written against.
    assert scanned > 100, (
        f"only {scanned} shipped files were scanned. SHIPPED_ROOTS is probably "
        f"pointing at directories that no longer exist, and a scan that reads "
        f"nothing reports no violations."
    )
    assert spelled, (
        f"{APP_STORE_ID} does not appear anywhere under {list(SHIPPED_ROOTS)}. "
        f"The App Store link is supposed to be defined in "
        f"{APP_STORE_URL_HOME}; finding it nowhere means this test is no "
        f"longer reading what it thinks it is."
    )
    assert spelled == [APP_STORE_URL_HOME], (
        f"the App Store URL is spelled out in {spelled}. It belongs in "
        f"{APP_STORE_URL_HOME} alone -- templates go through the "
        f"`app_store_badge()` macro."
    )


def test_no_template_builds_an_app_link_by_hand():
    """The deep-link query string is assembled in one place too."""
    offenders = []
    for folder, _, files in os.walk(os.path.join(ROOT, "templates")):
        for name in files:
            if name == "_app_link_cta.html" or not name.endswith(".html"):
                continue
            path = os.path.join(folder, name)
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                if "pulse_app=1" in handle.read():
                    offenders.append(os.path.relpath(path, ROOT))
    assert not offenders, (
        f"{offenders} hand-build an app deep link. `app_link()` validates the "
        f"destination against what the released binary actually resolves; a "
        f"hand-written query string skips that check, which is how a button "
        f"reading 'Open this listing' ships pointing at Home."
    )


# --- Anti-vacuity ---------------------------------------------------------


def test_the_extractor_can_tell_a_promoted_page_from_a_bare_one():
    """Without this, every coverage test above could be passing on a bug.

    `_app_links` returning something truthy for any input at all would make the
    whole file vacuous, and the failure would look exactly like success.
    """
    assert _app_links("") == {}
    assert _app_links("<p>no links here</p>") == {}

    # A hand-written store link is deliberately NOT counted as promotion, so
    # the coverage tests cannot be satisfied by the thing the singularity
    # tests forbid.
    bare = f'<a href="https://apps.apple.com/us/app/pulsesoc/{APP_STORE_ID}">Get it</a>'
    assert _app_links(bare) == {}

    macro_built = (
        '<a class="button primary" href="https://pulsesoc.com/pulse?pulse_app=1'
        '&amp;pulse_src=web" data-app-link="home">Open PulseSoc</a>'
    )
    assert _app_links(macro_built) == {
        "home": "https://pulsesoc.com/pulse?pulse_app=1&pulse_src=web"
    }


def test_the_pages_under_test_are_not_all_the_same_page():
    """Seven routes, but /help and /support share a view.

    If every route in the contract collapsed onto one response -- a catch-all
    swallowing the list, say -- the coverage above would be one assertion
    wearing seven hats.
    """
    bodies = {_render(path)[1] for path in MARKETING_ROUTES}
    assert len(bodies) >= 3, (
        f"{len(MARKETING_ROUTES)} routes produced {len(bodies)} distinct "
        f"responses. Expected at least three: the homepage, the two legal "
        f"pages, and the support view."
    )


if __name__ == "__main__":
    # The suite runner executes this file as a script and fails it for
    # reporting zero checks, so it has to be runnable both ways. Zero-argument
    # tests, no fixtures -- see the note in tests/protection/test_route_auth.py.
    import pathlib as _pathlib

    sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
