"""The email CTA "Set up payments with Stripe" must reach Stripe.

## The incident

An approved seller — store approved, verification passed — opened her approval
email on an iPhone, tapped "Set up payments with Stripe", and landed inside the
PulseSoc app. She never saw Stripe. Card payments stayed off and no money could
reach her.

Nothing threw. The CTA resolved to

    https://pulsesoc.com/pulse/merchant/payouts?pulse_app=1&pulse_src=email

`services/payments_email_templates._seller_approved` builds that button from
``ctx["stripe_onboarding_url"]`` with a fallback, and no caller of the template
has ever set that key — so the fallback was the only value the button ever had.
The fallback was ``/pulse/merchant/payouts``, which is the page Stripe Connect
*returns* a seller to, not where onboarding begins.

Then the path decided the rest. ``/pulse/*`` is claimed by the published
apple-app-site-association, so iOS handed the URL to the app *before making any
HTTP request*. There was no redirect to follow, no server log line to find, and
nothing a backend change to that route could ever have intercepted.

## What these tests hold

Four independent things had to be true at once for her to reach Stripe, and each
is asserted here against observed behaviour rather than source text:

1. The rendered href is the initiation route, not the return page. Asserted by
   rendering the real email and reading the real ``<a href>``.
2. The initiation route is not claimed by any already-installed binary. Asserted
   against the published association file and the link registry — this is what
   makes the emails *already in inboxes* start working on deploy, with no new
   iOS build and no App Store review.
3. Hitting that route as an approved seller ends in a redirect to a Stripe URL.
   Asserted through Flask's test client with the provider patched at its
   boundary, so what is checked is the ``Location`` header the seller's browser
   would actually follow.
4. The route cannot be turned into an IDOR, an open redirect, or a way around
   PulseSoc approval.

No live Stripe call happens here. Production runs live keys, so exercising a
real Connect onboarding to verify a fix would create real financial side
effects; the provider is patched at ``create_connected_account`` and
``create_onboarding_link`` and the assertions are on the arguments and the
resulting redirect.

    .venv/bin/python -m pytest tests/test_seller_stripe_onboarding_entry.py
"""


import os
import re
import tempfile
from urllib.parse import urlsplit

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="seller_onboarding_entry_"), "test.db")

import bot  # noqa: E402
from services import app_links  # noqa: E402
from services import native_app_links  # noqa: E402
from services import payment_provider  # noqa: E402
from services import payments_email_templates as templates  # noqa: E402
from services import seller_payment_onboarding as onboarding  # noqa: E402

SETUP_PATH = "/seller/payments/setup"
STRIPE_LINK = "https://connect.stripe.com/setup/e/acct_test_maria"

#: The exact URL the broken button carried. Kept as a literal because it is the
#: defect, and a test that merely asserts "not the payouts page" would pass again
#: the moment someone reintroduced it with a different query string.
BROKEN_CTA_URL = "https://pulsesoc.com/pulse/merchant/payouts?pulse_app=1&pulse_src=email"


# --------------------------------------------------------------------------
# 1. What the email actually renders
# --------------------------------------------------------------------------

def _ctas(event, context=None):
    """``{link text: href}`` for every anchor in a rendered email."""
    html = templates.render(event, dict(context or {})).get("html") or ""
    found = {}
    for match in re.finditer(r'href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        text = " ".join(re.sub(r"<[^>]+>", " ", match.group(2)).split())
        if text:
            found[text] = match.group(1)
    return found


def test_the_approval_email_sends_the_seller_to_stripe_not_into_the_app():
    """The one assertion the incident is about, on the rendered href."""
    ctas = _ctas(
        "seller_approved",
        {"seller_first_name": "Maria", "store_name": "Goodness of God",
         "seller_application_status": "approved"},
    )
    href = ctas.get("Set up payments with Stripe")
    assert href, f"the approval email lost its Stripe CTA entirely: {sorted(ctas)}"
    assert href != BROKEN_CTA_URL
    assert urlsplit(href).path == SETUP_PATH, href


def test_every_cta_that_promises_stripe_points_at_the_initiation_route():
    """Three emails make that promise, and all three had the same defect.

    A seller whose requirements changed, or whose account was restricted, has to
    get back to Stripe's own pages just as much as a newly approved one. Those
    two CTAs pointed at the app too.
    """
    promises = {
        "seller_approved": "Set up payments with Stripe",
        "stripe_verification_required": "Complete verification",
        "seller_account_restricted": "Resolve with Stripe",
    }
    for event, label in promises.items():
        href = _ctas(event, {"seller_first_name": "Maria"}).get(label)
        assert href, f"{event} lost its {label!r} CTA"
        assert urlsplit(href).path == SETUP_PATH, f"{event}: {href}"


def test_no_email_cta_is_a_relative_href():
    """A mail client has no base URL, so a relative href goes nowhere.

    ``app_links.app_intent_url`` returns web-intent paths untouched, which is
    right for a page and useless in an email. Every CTA under ``/account/``,
    ``/checkout/``, ``/dashboard`` or ``/seller/payments`` had that shape.
    """
    for event in ("seller_approved", "stripe_verification_required",
                  "seller_account_restricted", "payout_failed", "new_paid_order"):
        for label, href in _ctas(event, {"seller_first_name": "Maria"}).items():
            assert not href.startswith("/"), f"{event} / {label}: relative href {href}"
            assert href.startswith(("https://", "mailto:")), f"{event} / {label}: {href}"


def test_the_email_does_not_carry_a_short_lived_stripe_link():
    """Why the CTA is a PulseSoc route and not an AccountLink.

    A Stripe AccountLink is single-use and expires in minutes. Baking one into an
    email means it is usually dead by the time a human opens it, and a seller who
    clicks twice gets an error the second time. The durable route mints a fresh
    link at click time instead.
    """
    href = _ctas("seller_approved", {}).get("Set up payments with Stripe") or ""
    assert "stripe.com" not in href, href


# --------------------------------------------------------------------------
# 2. Why the path keeps the link in the browser
# --------------------------------------------------------------------------

def _ios_claims(path):
    """Whether iOS would hand ``path`` to the app, per the published components.

    Apple evaluates components in order and the **first** match decides, which is
    why the file puts ``/pulse/app`` and ``/pulse/app/*`` above the broader
    ``/pulse/*`` — an exclusion below it would never be reached. Implemented as a
    real matcher rather than a substring check on the JSON, because "the string
    /seller/ is absent" would also be satisfied by a ``/*`` catch-all that claims
    every path on the domain.
    """
    for component in native_app_links.APPLE_LINK_COMPONENTS:
        pattern = str(component.get("/") or "")
        if not pattern:
            continue
        # Apple's `*` matches a run of characters.
        regex = "^" + ".*".join(re.escape(part) for part in pattern.split("*")) + "$"
        if re.match(regex, path):
            return not component.get("exclude")
    return False


def test_no_installed_binary_claims_the_initiation_path():
    """The reason old emails start working without a new iOS build.

    iOS decides whether to open the app from the *published* association file,
    before any HTTP request. If ``/seller/*`` were claimed there, every copy of
    PulseSoc already on a phone would swallow this link and no server-side change
    could stop it — the fix would need an App Store release to reach anyone, and
    the emails already sitting in inboxes could never be repaired.
    """
    assert not _ios_claims(SETUP_PATH), (
        "the association file now claims the payment-setup path, which would "
        "send this link back into the app on every installed build"
    )


def test_the_matcher_above_can_actually_see_a_claimed_path():
    """Negative control. Otherwise `_ios_claims` returning False proves nothing.

    The broken CTA's own path is the honest control: it *is* claimed, and that is
    precisely why the seller ended up in the app.
    """
    assert _ios_claims("/pulse/merchant/payouts")
    assert _ios_claims("/pulse")
    # And the exclusions still work, so the matcher is not simply always True.
    assert not _ios_claims("/pulse/app")
    assert not _ios_claims("/pulse/app/feed")


def test_the_link_registry_agrees_that_the_path_stays_on_the_web():
    """Both declarations must say the same thing or one of them is wrong."""
    assert app_links.is_web_intent_path(SETUP_PATH)
    # And the builder must not decorate it with app-intent markers, which mean
    # the opposite: "the installed app should take this".
    built = app_links.app_intent_url(SETUP_PATH, "email")
    assert "pulse_app" not in built, built


def test_the_control_the_assertion_above_needs():
    """Negative control: the classifier really can say "this is an app link".

    Without this, `is_web_intent_path` returning True for everything would make
    the test above pass vacuously — and the broken CTA's own path is the most
    honest control available, because it *is* an app destination.
    """
    assert not app_links.is_web_intent_path("/pulse/merchant/payouts")
    assert "pulse_app=1" in app_links.app_intent_url("/pulse/merchant/payouts", "email")


# --------------------------------------------------------------------------
# 3. Driving the route
# --------------------------------------------------------------------------

@pytest.fixture
def stripe_boundary(monkeypatch):
    """Patch the provider, and record what it was asked for."""
    calls = {"accounts": [], "links": []}

    def create_account(user, seller_type):
        calls["accounts"].append({"user": dict(user or {}), "seller_type": seller_type})
        return {"ok": True, "provider_account_id": "acct_test_maria"}

    def create_link(provider_account_id, refresh_url="", return_url=""):
        calls["links"].append({
            "provider_account_id": provider_account_id,
            "refresh_url": refresh_url,
            "return_url": return_url,
        })
        return {"ok": True, "url": STRIPE_LINK}

    monkeypatch.setattr(payment_provider, "create_connected_account", create_account)
    monkeypatch.setattr(payment_provider, "create_onboarding_link", create_link)
    monkeypatch.setattr(bot, "STRIPE_SECRET_KEY", "sk_test_onboarding_entry_only")
    monkeypatch.setattr(bot, "APP_BASE_URL", "https://pulsesoc.com", raising=False)
    return calls


@pytest.fixture
def approved_seller(monkeypatch):
    """An approved merchant with no Connect account yet — Maria's exact state."""
    monkeypatch.setattr(bot, "require_account", lambda: {"user_id": 4242, "email": "s@x.com"})
    monkeypatch.setattr(onboarding, "authorize", lambda cur, user_id, seller_type: None)
    monkeypatch.setattr(bot, "seller_payout_account", lambda cur, user_id, seller_type: {})
    return {"user_id": 4242}


def test_an_approved_seller_is_redirected_to_stripe(stripe_boundary, approved_seller):
    """The end of the customer path, asserted on the Location header."""
    response = bot.app.test_client().get(SETUP_PATH)
    assert response.status_code == 303, response.status_code
    assert response.headers["Location"] == STRIPE_LINK
    # Single-use and short-lived, so this hop must never be cached as the
    # seller's destination.
    assert "no-store" in response.headers.get("Cache-Control", "")


def test_the_route_asks_stripe_for_a_link_with_both_legs(stripe_boundary, approved_seller):
    """`refresh_url` and `return_url` describe opposite outcomes."""
    bot.app.test_client().get(SETUP_PATH)
    assert len(stripe_boundary["links"]) == 1
    link = stripe_boundary["links"][0]
    assert link["return_url"] != link["refresh_url"]
    for leg in ("return_url", "refresh_url"):
        assert link[leg].startswith("https://pulsesoc.com/"), link[leg]
        # Nothing about the account may ride in a URL Stripe will put in a
        # browser's address bar and history.
        assert "acct_" not in link[leg], link[leg]
        assert "sk_" not in link[leg], link[leg]


def test_a_second_click_reuses_the_account_instead_of_creating_another(
    stripe_boundary, approved_seller, monkeypatch
):
    """One approved seller, one Connect account.

    A second ``Account.create`` is how a seller ends up with accounts A, B and C
    and money arriving in whichever the payout worker happened to read. Mail
    scanners prefetch links, so the second request is not hypothetical — it
    happens before the human clicks.
    """
    client = bot.app.test_client()
    client.get(SETUP_PATH)
    assert len(stripe_boundary["accounts"]) == 1

    # The row the first request wrote, as the route would now read it back.
    monkeypatch.setattr(
        bot, "seller_payout_account",
        lambda cur, user_id, seller_type: {
            "connected_account_id": "acct_test_maria",
            "onboarding_status": "onboarding_started",
        },
    )
    monkeypatch.setattr(
        payment_provider, "get_account_status",
        lambda account_id: {"ok": True, "provider_account_id": account_id,
                            "charges_enabled": False, "payouts_enabled": False,
                            "details_submitted": False, "requirements": {}},
    )
    response = client.get(SETUP_PATH)
    assert response.status_code == 303
    assert response.headers["Location"] == STRIPE_LINK
    assert len(stripe_boundary["accounts"]) == 1, (
        "a repeat click created a second Stripe account"
    )


def test_a_finished_seller_is_not_sent_back_through_onboarding(
    stripe_boundary, approved_seller, monkeypatch
):
    """Stripe is the authority on whether this account is done, not our column."""
    monkeypatch.setattr(
        bot, "seller_payout_account",
        lambda cur, user_id, seller_type: {"connected_account_id": "acct_test_maria"},
    )
    monkeypatch.setattr(
        payment_provider, "get_account_status",
        lambda account_id: {"ok": True, "provider_account_id": account_id,
                            "charges_enabled": True, "payouts_enabled": True,
                            "details_submitted": True, "requirements": {},
                            "capabilities": {"card_payments": "active",
                                             "transfers": "active"}},
    )
    response = bot.app.test_client().get(SETUP_PATH)
    assert response.status_code == 303
    assert urlsplit(response.headers["Location"]).path == "/pulse/merchant/payouts"
    assert not stripe_boundary["links"], (
        "a completed seller was handed a fresh onboarding link"
    )


def test_an_unauthenticated_click_keeps_the_intent_through_login(monkeypatch):
    """Not Home. She tapped "set up payments" and that is where she must land."""
    monkeypatch.setattr(bot, "require_account", lambda: None)
    response = bot.app.test_client().get(SETUP_PATH)
    assert response.status_code in (301, 302, 303, 307, 308)
    location = response.headers["Location"]
    assert "login" in location, location
    assert SETUP_PATH in location.replace("%2F", "/"), location


def test_an_unapproved_account_is_refused_with_somewhere_to_go(
    stripe_boundary, monkeypatch
):
    """PulseSoc approval and Stripe approval are different axes.

    And a refusal must still be an actionable page — a member dropped on Home
    has been told nothing.
    """
    monkeypatch.setattr(bot, "require_account", lambda: {"user_id": 77, "email": "n@x.com"})
    monkeypatch.setattr(
        onboarding, "authorize",
        lambda cur, user_id, seller_type: {
            "state": onboarding.START_NOT_APPROVED,
            "message": "PulseSoc has not approved this account to sell yet.",
            "http_status": 403,
        },
    )
    monkeypatch.setattr(bot, "seller_payout_account", lambda cur, user_id, seller_type: {})
    response = bot.app.test_client().get(SETUP_PATH)
    assert response.status_code == 403
    body = response.get_data(as_text=True)
    assert "/pulse/merchant/apply" in body
    assert not stripe_boundary["accounts"], (
        "an unapproved account was given a Stripe account anyway"
    )


# --------------------------------------------------------------------------
# 4. Security
# --------------------------------------------------------------------------

def test_a_seller_id_in_the_query_string_cannot_redirect_the_onboarding(
    stripe_boundary, approved_seller
):
    """Onboarding is started for the authenticated account and nothing else."""
    bot.app.test_client().get(f"{SETUP_PATH}?sellerId=1&user_id=1&seller_id=1")
    assert len(stripe_boundary["accounts"]) == 1
    assert stripe_boundary["accounts"][0]["user"]["user_id"] == 4242


def test_an_unknown_seller_type_falls_back_to_the_merchant_lane(
    stripe_boundary, approved_seller
):
    """No unvalidated value from the query string reaches a URL we build."""
    bot.app.test_client().get(f"{SETUP_PATH}?seller_type=../../admin")
    link = stripe_boundary["links"][0]
    assert link["return_url"] == "https://pulsesoc.com/pulse/merchant/payouts/return"
    assert ".." not in link["return_url"]


def test_a_non_stripe_link_is_refused_rather_than_redirected_to(
    stripe_boundary, approved_seller, monkeypatch
):
    """This route hands a URL straight to ``Location:``.

    If the provider ever returned something that was not Stripe's, redirecting to
    it would be an open redirect with an approved seller's session attached.
    """
    monkeypatch.setattr(
        payment_provider, "create_onboarding_link",
        lambda account_id, refresh_url="", return_url="": {
            "ok": True, "url": "https://connect.stripe.com.evil.example/setup"},
    )
    response = bot.app.test_client().get(SETUP_PATH)
    assert response.status_code != 303
    assert "evil.example" not in response.headers.get("Location", "")
    assert "evil.example" not in response.get_data(as_text=True)


def test_the_redirect_guard_accepts_the_hosts_stripe_actually_uses():
    """Negative control for the test above, so it cannot pass by rejecting all."""
    assert onboarding._is_stripe_url("https://connect.stripe.com/setup/e/acct_x")
    assert onboarding._is_stripe_url("https://hosted.stripe.com/x")
    assert onboarding._is_stripe_url("https://stripe.com/x")
    assert not onboarding._is_stripe_url("http://connect.stripe.com/x")
    assert not onboarding._is_stripe_url("https://stripe.com.evil.example/x")
    assert not onboarding._is_stripe_url("//connect.stripe.com/x")


def test_the_correlation_id_carries_no_identity():
    """Funnel lines are read by people with no business knowing which seller."""
    handle = onboarding.correlation_id(4242, "merchant", "2026-09-28T00:00:00")
    assert "4242" not in handle
    assert len(handle) == 12
    # Same attempt, same handle — otherwise the funnel cannot be joined.
    assert handle == onboarding.correlation_id(4242, "merchant", "2026-09-28T00:00:00")
    assert handle != onboarding.correlation_id(4243, "merchant", "2026-09-28T00:00:00")
