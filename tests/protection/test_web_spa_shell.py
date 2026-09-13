"""The web client's shell, and the one header that makes it safe to serve.

The SPA is a React app: one hashed module, an empty `<div id="root">`, and
whatever the bundle decides to put in it. That shape is only defensible behind
`script-src 'self'`. Without it, any injection anywhere in the product that
reaches this document executes, and a single-page app is a large, long-lived
document.

The obvious place to put a built SPA is `static/app/index.html`, served by the
static handler. That would have been wrong in a way nothing would report:
`add_pwa_headers` returns before setting a Content-Security-Policy for any path
under `/static/`, so the file would ship with **no policy at all**. It would
load, render, and pass every test anyone thought to write. Serving it from a
route is what gives it a CSP, and that is the whole reason the route exists.

The site-wide CSP is not good enough either. It carries `'unsafe-inline'` in
`script-src` because roughly fifteen hundred server-rendered Jinja routes inline
their scripts and cannot stop today. The SPA inlines nothing, so it does not
need the exemption -- but it would inherit it silently, because the site-wide
policy is applied with `setdefault` and a route that sets nothing gets the
default. Inheriting is the default behaviour; not inheriting is the thing that
has to be actively true, which is why it is asserted here.

Three further claims, each protecting something that fails quietly:

- **No inline script in the served document.** The CSP and the document have to
  agree. A stricter header over a document that needs inline script is not
  security, it is a blank screen -- and the failure appears only in a browser
  console, which no CI job reads. Note this is checked on the *response*, after
  the after_request hooks have spliced into it, not on the file on disk.
- **`style-src` still permits inline.** React's `style={{...}}` compiles to a
  `style=` attribute; PulseBackground.tsx has nine of them. Tightening this
  looks like an improvement and deletes the backdrop.
- **iOS sends these URLs to the browser.** `/pulse/*` is claimed by the
  universal-link association, correctly -- every other path under it is a native
  object. This one is the browser client, and `linking.ts` declares no route for
  it. An unresolvable universal link does not fall back to Safari; iOS has
  already opened the app, and the user lands on whatever screen was showing.
"""

import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

SHELL_FILE = os.path.join(ROOT, "static", "app", "index.html")
SPA_ROUTE = "/pulse/app"

# Deep paths as well as the root. Client-side routing means every one of these
# is the same document, and the CSP has to survive the catch-all as well as the
# exact rule -- they are two separate Flask rules on one view.
SPA_URLS = ("/pulse/app", "/pulse/app/feed", "/pulse/app/profile/812")

_APP = None
_RESPONSES = {}


def _app():
    """Boot the real Flask app once. The claims here are about a live response.

    Reading bot.py as text would be cheaper and would prove nothing: the header
    is the product of a view and two after_request hooks that can each rewrite
    it, and the question is what arrives at a browser.
    """
    global _APP
    if _APP is None:
        os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, "coinpilotx.db"))
        import bot

        _APP = bot.webhook_app
    return _APP


def _response(url=SPA_ROUTE):
    if url not in _RESPONSES:
        _RESPONSES[url] = _app().test_client().get(url)
    return _RESPONSES[url]


def _csp(url=SPA_ROUTE):
    header = _response(url).headers.get("Content-Security-Policy")
    assert header, f"{url} ships no Content-Security-Policy at all"
    return {
        part.strip().split(" ")[0]: part.strip()
        for part in header.split(";")
        if part.strip()
    }


def _aasa():
    path = os.path.join(ROOT, "scripts", "web_rebuild", "aasa_health.py")
    spec = importlib.util.spec_from_file_location("aasa_health_spa_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    os.environ.setdefault("PULSESOC_APPLE_TEAM_ID", "A1B2C3D4E5")
    from services.native_app_links import apple_app_site_association

    payload, error = apple_app_site_association()
    assert payload is not None, error
    return module, payload


def test_the_shell_is_served_from_a_route_and_not_out_of_static():
    """The precondition for every other claim in this file.

    `add_pwa_headers` returns before the CSP block for any `/static/` path, so
    a shell served from there has no policy. This asserts the route answers --
    if it 404s, the SPA is being reached some other way and the CSP tests below
    would be describing a document nobody loads.
    """
    for url in SPA_URLS:
        response = _response(url)
        assert response.status_code == 200, (
            f"{url} returned {response.status_code}. The SPA shell must be "
            f"served from a route; /static/ has no CSP."
        )
        assert "text/html" in response.headers.get("Content-Type", "")


def test_script_src_does_not_allow_inline():
    """The exit criterion for this phase, stated as narrowly as it is meant.

    Not `default-src`, not `style-src` -- `script-src`. Inheriting the
    site-wide policy would satisfy "has a CSP" and fail this.
    """
    for url in SPA_URLS:
        directives = _csp(url)
        script_src = directives.get("script-src")
        assert script_src, f"{url}: no script-src, so it falls back to default-src"
        assert "'unsafe-inline'" not in script_src, (
            f"{url}: script-src is {script_src!r}. The SPA has no inline script "
            f"and must not inherit the site-wide exemption that exists for the "
            f"server-rendered Jinja routes."
        )
        assert "'unsafe-eval'" not in script_src, f"{url}: script-src is {script_src!r}"


def test_the_document_contains_no_inline_script():
    """The other half of the claim above, checked after the hooks have run.

    A strict header over a document that needs inline script is a blank screen,
    and the only report is a console message in a browser CI never opens. This
    reads the response body, not the file: three after_request hooks splice
    markup into HTML bodies, and two of them splice `<script>` tags.
    """
    for url in SPA_URLS:
        body = _response(url).get_data(as_text=True)
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", body, flags=re.I)
        assert not inline, (
            f"{url} contains {len(inline)} inline <script> tag(s) that "
            f"script-src 'self' will block: {inline[:3]}"
        )
        handlers = re.findall(r"\son(?:click|load|error|submit)\s*=", body, flags=re.I)
        assert not handlers, f"{url} contains inline event handlers: {handlers[:3]}"


def test_every_script_and_stylesheet_is_same_origin_and_exists():
    """`script-src 'self'` blocks third-party scripts; so must the document.

    Existence is checked too. A hashed asset the shell references but the build
    did not emit is a 404 and a white page, and `no-store` on the shell means
    every visitor gets it -- there is no stale-cache reprieve.
    """
    body = _response().get_data(as_text=True)
    refs = re.findall(r'<script[^>]*\bsrc="([^"]+)"', body, flags=re.I)
    refs += re.findall(r'<link[^>]*\bhref="([^"]+)"', body, flags=re.I)
    assert refs, "no scripts or stylesheets found; the scan is reading nothing"
    for ref in refs:
        assert ref.startswith("/"), (
            f"{ref} is not same-origin. script-src 'self' blocks it and the "
            f"page will not boot."
        )
        local = os.path.join(ROOT, ref.split("?", 1)[0].lstrip("/"))
        if local.startswith(os.path.join(ROOT, "static")):
            assert os.path.exists(local), f"{ref} is referenced but not built"


def test_style_src_still_allows_inline_because_react_needs_it():
    """A negative claim, because the tempting change here breaks the backdrop.

    `style={{...}}` produces a `style=` attribute, governed by `style-src-attr`
    falling back to `style-src`. Removing `'unsafe-inline'` here would blank
    PulseBackground.tsx's nine inline styles, and `style-src-attr` is not
    supported widely enough to tighten this by that route instead.
    """
    style_src = _csp().get("style-src", "")
    assert "'unsafe-inline'" in style_src, (
        f"style-src is {style_src!r}. web/src/components/PulseBackground.tsx "
        f"renders inline style attributes; without this the mesh backdrop "
        f"silently disappears."
    )


def test_the_shell_is_never_cached():
    """It names hashed filenames, so caching it outlives the files it names.

    The guarantee comes from `add_pwa_headers`' /pulse/ family rule, which
    assigns no-store rather than setdefaulting it -- the route sets the same
    header but the hook overwrites it. Worth knowing before moving the mount:
    outside /pulse/ the family rule stops applying and only the route's own
    header is left.
    """
    for url in SPA_URLS:
        cache_control = _response(url).headers.get("Cache-Control", "")
        assert "no-store" in cache_control, (
            f"{url} is served with Cache-Control {cache_control!r}. The shell "
            f"references hashed assets; a cached shell asks for asset hashes "
            f"that the next deploy has already removed."
        )


def test_the_hooks_that_splice_into_html_left_the_spa_alone():
    """Three after_request hooks rewrite HTML bodies. Two must not touch this one.

    `pulse_i18n.js` walks and rewrites text nodes -- against React's first paint,
    over nodes React owns. `pulse_pwa_install.js` binds to server-rendered
    markup that does not exist at `defer` time in a SPA. And the legacy token
    stylesheet is not a duplicate of the SPA's tokens but a conflict: six shared
    names, four with different values, resolved by whichever loads last.

    The favicon/manifest block is the deliberate exception and is asserted
    present below -- the shell's own index.html has none of it.
    """
    body = _response().get_data(as_text=True)
    for unwanted in ("pulse_i18n.js", "pulse_pwa_install.js", "pulsesoc-tokens.css"):
        assert unwanted not in body, (
            f"{unwanted} was injected into the SPA shell. See the "
            f"`spa_isolated` flag in bot.py's add_pwa_headers."
        )


def test_the_spa_still_receives_the_inert_favicon_markup():
    """Anti-vacuity for the test above, and a claim in its own right.

    If the opt-out were implemented as "skip this document entirely", the test
    above would pass for the wrong reason. The shell genuinely lacks a favicon,
    a manifest link and a theme-color, and the block that supplies them is inert
    markup with no script in it.
    """
    body = _response().get_data(as_text=True)
    for wanted in ('rel="manifest"', "pulse-favicon-32", "theme-color"):
        assert wanted in body, (
            f"{wanted} is missing from the SPA shell. The favicon block is the "
            f"one injection the SPA is meant to keep."
        )


def test_the_shell_on_disk_has_no_inline_script_either():
    """Checked separately from the response so the two cannot cover for each other.

    A build config change -- Vite's `inlineDynamicImports`, or a CSS-in-JS
    plugin -- can start emitting inline script into index.html. If only the
    response were checked and a hook happened to strip it, this would pass while
    the artefact was wrong.
    """
    with open(SHELL_FILE, encoding="utf-8") as handle:
        document = handle.read()
    inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", document, flags=re.I)
    assert not inline, (
        f"{SHELL_FILE} contains inline script: {inline[:3]}. The Vite build must "
        f"emit external modules only."
    )


def test_ios_sends_the_web_client_to_the_browser_not_the_app():
    """`/pulse/*` is claimed by the association. This one path is carved out.

    Claiming it would hand iOS a URL `linking.ts` declares no route for, and
    that does not degrade to a web page: iOS has already chosen the app, and
    React Navigation simply fails to resolve it.
    """
    health, payload = _aasa()
    components = health.ordered_components(payload)
    for url in SPA_URLS + ("/pulse/app/",):
        assert not health.opens_in_app(components, url), (
            f"{url} is claimed by the universal-link association. It is the "
            f"browser client and has no native route."
        )


def test_the_carve_out_did_not_take_the_rest_of_pulse_with_it():
    """The other direction. A carve-out that is too wide is the worse failure.

    Deep links are the load-bearing form of app promotion; over-excluding would
    send native objects to the website and look, from the outside, exactly like
    the association being broken.
    """
    health, payload = _aasa()
    components = health.ordered_components(payload)
    for url in ("/pulse", "/pulse/post/812", "/pulse/reels/9", "/pulse/apple-pay"):
        assert health.opens_in_app(components, url), (
            f"{url} must still open the native app"
        )


def test_the_route_is_classified_as_web_intent():
    """Every new path under /pulse/* is classified when it is created.

    An unclassified path defaults to app intent. This registry does not drive
    iOS -- the association does, checked above -- but the two have to agree, and
    the registry is what a human reads.
    """
    from services.app_links import is_web_intent_path

    assert is_web_intent_path(SPA_ROUTE)
    assert is_web_intent_path("/pulse/app/feed")
    assert not is_web_intent_path("/pulse/post/812"), (
        "the classifier now calls a native object a web path; the check above "
        "would pass for any input"
    )


if __name__ == "__main__":
    # The suite runner executes this file as a script and fails it for
    # reporting zero checks, so it has to be runnable both ways. Zero-argument
    # tests, no fixtures -- see the note in tests/protection/test_route_auth.py.
    import pathlib as _pathlib

    sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
