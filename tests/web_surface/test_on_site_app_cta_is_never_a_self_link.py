"""An "open this in the app" button on pulsesoc.com must not point at pulsesoc.com.

The reported defect was the Marketplace product page's CTA -- "Prefer the app?
Open this listing in PulseSoc" -- doing nothing at all on a physical iPhone. Not
opening the App Store, not going Home, not erroring. Nothing.

The cause was not signing, entitlements, the association file or the app's
router, all of which were measured and found correct. It was the href. Two
separate builders produced two separate wrong answers for the same reason:

    marketplace_storefront_app_cta  ->  app_first_href      ->  /pulse/marketplace/163
    app_cta macro                   ->  build_app_link      ->  https://pulsesoc.com
                                                                /pulse/marketplace/163
                                                                ?pulse_app=1

The first is a bare anchor to the page the visitor is already on, so a tap
re-renders the same listing -- exactly "nothing happens". The second is the
canonical universal link, which is right from an email and useless here: iOS
does not consult associated domains for a same-domain navigation, so Safari
just follows it, to the same page again.

Neither is a typo. Both helpers are correct at their real jobs -- `app_first_href`
answers "where does a *web* link to this destination go", and `product` is
web-first, so its answer is the canonical page by design. The bug is that an
app-opening CTA asked them at all. `open_interstitial_url` is the helper for a
button rendered on-domain, and its own docstring diagnosed this failure before
anyone hit it.

So the constraint, and what this file pins: **no on-site app CTA may resolve to
a pulsesoc.com URL.** It must go through `/open/...`, which is already
`exclude: true` in the shipped association file -- meaning iOS leaves it to
Safari deliberately, the member is offered a real `pulsesoc://` button, and the
fix requires no AASA change and therefore cannot disturb the Stripe onboarding
routes that share that file.

Source-and-unit level, no device. A device proves one build on one iOS version;
these assertions hold for every listing at once. The rendered handoff was
separately verified against the running app: `/open/product/163` returns 200 and
emits `pulsesoc://pulse/marketplace/163` under an iOS user agent, emits no
scheme link under a desktop one, and 404s on a traversal id, a `javascript:`
id, `0`, `-1` and an unknown destination.

Run: python3 -m pytest tests/web_surface/test_on_site_app_cta_is_never_a_self_link.py
"""

from __future__ import annotations

import os
import re

import pytest

from services import app_links

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TEMPLATES = os.path.join(REPO, "templates")
BOT_PY = os.path.join(REPO, "bot.py")
ROUTE_ACTIONS_TS = os.path.join(
    REPO, "mobile-native", "src", "navigation", "nativeRouteActions.ts"
)

#: Templates allowed to use the canonical-universal-link macro. Empty, and that
#: is the point: everything under `templates/` is rendered by Flask and served
#: from pulsesoc.com, so every one of them is same-domain. An off-domain surface
#: (an email body, an SMS) would belong here -- none exists today, and adding one
#: should be a deliberate edit to this list rather than a silent regression.
OFF_DOMAIN_TEMPLATES: set[str] = set()


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _templates():
    for root, _dirs, files in os.walk(TEMPLATES):
        for name in files:
            if name.endswith(".html"):
                full = os.path.join(root, name)
                yield os.path.relpath(full, TEMPLATES), _read(full)


def test_the_macro_that_builds_a_same_domain_link_is_unused_on_site():
    """Default-deny on the wrong macro, not a one-time cleanup of six callers.

    `app_cta` still exists and is still correct for an off-domain surface, so
    deleting it would be wrong. What must not happen is a seventh on-site page
    reaching for it, which is how the first six got there.
    """
    offenders = []
    for rel, body in _templates():
        if rel == "_app_link_cta.html" or rel in OFF_DOMAIN_TEMPLATES:
            continue
        # `app_open_cta(` contains `app_cta(` only if matched loosely, so anchor
        # on a word boundary that a preceding `_` cannot satisfy.
        for match in re.finditer(r"(?<![\w_])app_cta\s*\(", body):
            line = body[: match.start()].count("\n") + 1
            offenders.append(f"  templates/{rel}:{line}")
    assert not offenders, (
        "these templates build an app CTA with `app_cta`, which emits the "
        "canonical https://pulsesoc.com/... universal link. They are served "
        "from pulsesoc.com, so that link is same-domain: iOS will not offer "
        "the app and the tap just reloads the page.\n"
        + "\n".join(sorted(offenders))
        + "\n\nUse `app_open_cta` instead, or add the file to "
        "OFF_DOMAIN_TEMPLATES if it is genuinely rendered off-domain."
    )


def test_the_storefront_cta_does_not_ask_the_web_link_helper():
    """The specific regression, named, because it reads as correct.

    `app_first_href` is a legitimate helper being asked the wrong question. A
    future edit restoring it here would look like a simplification.
    """
    body = _read(BOT_PY)
    match = re.search(
        r"def marketplace_storefront_app_cta\(.*?\n(?=\S|\ndef )", body, re.S
    )
    assert match, "marketplace_storefront_app_cta is gone; re-point this test"
    func = match.group(0)
    assert "open_interstitial_url" in func, (
        "marketplace_storefront_app_cta no longer builds its href with "
        "open_interstitial_url. This is the CTA the defect was reported "
        "against -- 'Prefer the app? Open this listing in PulseSoc'."
    )
    assert "app_first_href(" not in func, (
        "marketplace_storefront_app_cta is back to app_first_href. That helper "
        "answers 'where does the WEB link go', and for the web-first `product` "
        "destination the answer is /pulse/marketplace/<id> -- the page this "
        "button is rendered on. The CTA becomes an anchor to itself, which is "
        "the original bug and presents as a button that does nothing."
    )


@pytest.mark.parametrize(
    "destination,resource_id,expected",
    [
        ("product", 163, "/open/product/163?pulse_src=web"),
        ("marketplace", None, "/open/marketplace?pulse_src=web"),
        ("home", None, "/open/home?pulse_src=web"),
    ],
)
def test_the_on_site_helper_emits_a_relative_open_path(
    destination, resource_id, expected
):
    built = app_links.open_interstitial_url(destination, resource_id, "web")
    assert built == expected
    # The assertion that actually encodes the constraint: whatever the shape,
    # it must not address the site it is rendered on.
    assert "pulsesoc.com" not in built
    assert app_links.APP_INTENT_PARAM not in built
    assert built.startswith("/open/")


@pytest.mark.parametrize(
    "resource_id",
    ["163/../admin", "javascript:alert(1)", "0", "-1", "", "  ", "abc", "1;2"],
)
def test_a_hostile_resource_id_cannot_reach_a_path(resource_id):
    """Section 45: the id is untrusted input that ends up in a URL path.

    Rejected at build time, so a bad id costs the button rather than shipping a
    link. `/open/...` 404s on all of these independently; this is the earlier of
    the two gates.
    """
    with pytest.raises(app_links.AppLinkError):
        app_links.open_interstitial_url("product", resource_id, "web")


def test_the_scheme_url_matches_what_the_shipped_router_parses():
    """The end of the chain, and the only part a source test can still get wrong.

    Every assertion above is about the web. None of them would notice the
    interstitial handing iOS a `pulsesoc://` path the app's own router does not
    recognise -- which fails exactly like the original bug, one step later.
    """
    path = app_links.resolve_destination_path("product", 163)
    scheme = app_links.app_scheme_url(path)
    assert scheme == "pulsesoc://pulse/marketplace/163", scheme

    native = _read(ROUTE_ACTIONS_TS)
    pattern = re.search(
        r"\^\\/pulse\\/marketplace\\/\(\[1-9\]\\d\*\)\\/\?\$", native
    )
    assert pattern, (
        "the marketplace-detail route regex in nativeRouteActions.ts changed "
        "shape. Re-derive the expectation here from the new one rather than "
        "loosening this assertion -- the point is that the two agree."
    )
    assert re.match(r"^/pulse/marketplace/([1-9]\d*)/?$", path), (
        f"the interstitial would hand iOS {path!r}, which the app's own route "
        "matcher does not accept. The app would open on whatever its fallback "
        "is, not on this listing."
    )
