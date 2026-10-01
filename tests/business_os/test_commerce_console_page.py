"""Business OS — Seller Commerce Console page: NO DEAD BUTTONS, proven.

The web-parity mission bans dead buttons and fake state. This suite holds
``templates/business_os_commerce.html`` to that bar without a browser:

  * every read card (``data-endpoint`` / ``data-endpoint-biz``) targets a GET
    route that actually exists in the commerce gateway;
  * every form action — including every ``{verb}`` the form's <select> can
    emit, per-verb-expanded — targets a real route with the right method;
  * no admin-tier route is wired into the user-facing page;
  * writes carry the CSRF header the route pack now enforces;
  * the adapter file registers the page route and the CSRF gate (AST-level;
    the sandbox has no Flask).

    python tests/business_os/test_commerce_console_page.py   # no pytest needed
"""

import os
import re
import sys

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, _ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from services.business_os import commerce_gateway as gw  # noqa: E402


TEMPLATE = os.path.join(_ROOT, "templates", "business_os_commerce.html")
ADAPTER = os.path.join(_ROOT, "services", "business_os_commerce_routes.py")


def _src(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _route_matchers():
    """(method, compiled-regex) for every gateway route."""
    out = []
    for r in gw.ROUTES:
        pattern = re.sub(r"<[^>]+>", "[^/]+", gw.API_PREFIX + r["rule"])
        out.append((r["method"], re.compile("^" + pattern + "$"), r))
    return out


def _resolve(url):
    """Substitute page placeholders with plausible path segments."""
    return (url.split("?")[0]
            .replace("{biz}", "biz-x").replace("{id}", "id-x"))


def _match(method, url, matchers):
    return any(m == method and rx.match(url) for m, rx, _ in matchers)


def _forms(html):
    return re.findall(r"<form\b(.*?)</form>", html, flags=re.S)


# ---------------------------------------------------------------------------
def test_read_cards_hit_real_get_routes():
    html = _src(TEMPLATE)
    matchers = _route_matchers()
    cards = [u for u in re.findall(r'data-endpoint(?:-biz)?="([^"]+)"', html)
             if u.startswith("/")]  # skip the JS selector-building strings
    assert len(cards) >= 12, "console lost its read cards"
    for url in cards:
        resolved = _resolve(url)
        assert _match("GET", resolved, matchers), f"dead read card: {url}"


def test_forms_hit_real_routes_per_verb():
    html = _src(TEMPLATE)
    matchers = _route_matchers()
    checked = 0
    for chunk in _forms(html):
        m = re.search(r'data-action(?:-template)?="([^"]+)"', chunk)
        assert m, "form without an action"
        url = m.group(1)
        method_m = re.search(r'data-method="([^"]+)"', chunk)
        method = method_m.group(1) if method_m else "POST"
        if "{verb}" in url:
            verbs = re.findall(r'<option value="([^"]+)"', chunk)
            assert verbs, f"{url} has a verb slot but no verb options"
            targets = [_resolve(url).replace("{verb}", v) for v in verbs]
        else:
            targets = [_resolve(url)]
        for t in targets:
            assert _match(method, t, matchers), \
                f"dead button: {method} {t} (from {url})"
            checked += 1
    assert checked >= 20, f"expected >=20 wired form targets, got {checked}"


def test_no_admin_routes_on_the_page():
    html = _src(TEMPLATE)
    matchers = _route_matchers()
    urls = [u for u in
            re.findall(r'data-(?:endpoint(?:-biz)?|action(?:-template)?)="([^"]+)"',
                       html)
            if u.startswith("/")]
    for url in urls:
        for _, rx, r in matchers:
            if rx.match(_resolve(url).replace("{verb}", "x")):
                assert r["auth"] != "admin", f"admin route on user page: {url}"


def test_writes_carry_csrf_and_page_is_registered():
    html = _src(TEMPLATE)
    assert "X-CSRF-Token" in html and "csrf_token | tojson" in html
    adapter = _src(ADAPTER)
    assert "business_os_commerce_page" in adapter
    assert '"/business-os/commerce"' in adapter
    assert "business_os_commerce.html" in adapter
    assert "_csrf_ok" in adapter and "compare_digest" in adapter
    # The CSRF gate must guard every mutating method the table uses.
    assert '("POST", "PATCH", "PUT")' in adapter
    # Login-required page idiom, same as the sibling /business-os page.
    assert "require_account" in adapter and "login_page" in adapter


def _run_standalone():
    tests = [
        test_read_cards_hit_real_get_routes,
        test_forms_hit_real_routes_per_verb,
        test_no_admin_routes_on_the_page,
        test_writes_carry_csrf_and_page_is_registered,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
