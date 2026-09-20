"""Stripe Connect onboarding must return the seller to a page PulseSoc serves.

A Connect account link carries two URLs the seller is sent to *after* they have
typed their legal name, date of birth and bank account into Stripe:
``return_url`` on success and ``refresh_url`` when the link expires. Neither is
validated by Stripe and neither is exercised by any Connect call PulseSoc can
make in development — Connect is not enabled and there is no ``sk_test_`` key —
so a URL naming a route that does not exist fails only in production, only for
a real seller, and only at the single moment that seller has the most at stake.

That is exactly what happened: the rewards claim path sent sellers to
``/pulse/rewards``, which is not a rule in the URL map. There is no catch-all
and no 404 handler, so the seller landed on a bare Werkzeug 404.

These tests capture the URLs the app *actually* hands the payment provider —
patched at the ``create_onboarding_link`` boundary, so the assertion is on
observed arguments, not on source text — and resolve each one against
``bot.app.url_map``. The last test makes the coverage exhaustive: if someone
adds a third onboarding call site, it fails until that site is driven here too.

No live Stripe call is possible in this environment, so these are mocks at the
provider boundary. They prove the URL PulseSoc builds, not Stripe's handling of
it.

    .venv/bin/python -m pytest tests/test_connect_onboarding_return_urls.py
"""

import ast
import os
import tempfile
from urllib.parse import urlsplit

import pytest

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="connect_return_urls_"), "test.db")

import bot  # noqa: E402
from services import payment_provider  # noqa: E402

BASE = "https://pulsesoc.com"
_BOT_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bot.py")

#: Every ``create_onboarding_link`` call site in bot.py, and the test helper
#: below that drives it. Asserted to be exhaustive by
#: ``test_every_onboarding_call_site_in_bot_py_is_covered_here``.
COVERED_CALL_SITES = {"api_pulse_rewards_claim", "api_pulse_payouts_connect"}


# --------------------------------------------------------------------------
# Resolving a URL the way Flask's router would
# --------------------------------------------------------------------------

def resolve(url):
    """``(endpoint, args)`` for ``url``'s path, or raise like a real request.

    ``MapAdapter.match`` is the same routing Flask runs per request, so a path
    that raises ``NotFound`` here is a path that 404s in production.
    """
    parts = urlsplit(str(url))
    adapter = bot.app.url_map.bind(
        parts.netloc or "pulsesoc.com", url_scheme=parts.scheme or "https")
    return adapter.match(parts.path or "/", method="GET")


def test_the_resolver_can_actually_tell_a_dead_path_from_a_live_one():
    """Negative control. Without this the assertions below could be vacuous."""
    from werkzeug.exceptions import NotFound

    assert resolve(f"{BASE}/pulse/merchant/payouts")[0] == "pulse_merchant_payouts_page"
    with pytest.raises(NotFound):
        # The original bug, kept as the control: nothing serves this.
        resolve(f"{BASE}/pulse/rewards")


def test_the_app_has_no_catch_all_or_404_handler_to_soften_a_dead_return_url():
    """Why a wrong return_url is a bare 404 and not a friendly redirect."""
    from werkzeug.exceptions import NotFound

    with pytest.raises(NotFound):
        resolve(f"{BASE}/pulse/this-route-was-never-registered")
    assert 404 not in (bot.app.error_handler_spec.get(None) or {})


# --------------------------------------------------------------------------
# What the routes actually hand the provider
# --------------------------------------------------------------------------

@pytest.fixture
def onboarding_links(monkeypatch):
    """Record every ``create_onboarding_link`` call instead of calling Stripe.

    Patched on the provider module itself, which is the object both call sites
    reach (``bot.payment_provider`` is the same module).
    """
    calls = []

    def recorder(provider_account_id, refresh_url="", return_url=""):
        calls.append({
            "provider_account_id": provider_account_id,
            "refresh_url": refresh_url,
            "return_url": return_url,
        })
        return {"ok": True, "url": "https://connect.stripe.com/setup/e/acct_x"}

    monkeypatch.setattr(payment_provider, "create_onboarding_link", recorder)
    monkeypatch.setattr(bot, "APP_BASE_URL", BASE, raising=False)
    return calls


@pytest.fixture
def logged_in(monkeypatch):
    monkeypatch.setattr(bot, "api_account_user", lambda: {"user_id": 4242, "email": "s@x.com"})
    monkeypatch.setattr(bot, "pulse_ads_verify_write", lambda: True)
    monkeypatch.setattr(bot, "pulse_ads_rate_limited", lambda *a, **k: False)


def drive_rewards_claim(monkeypatch):
    """POST the rewards claim in the state that opens lazy Connect onboarding."""
    from services.business_os.rewards import engine as rewards_engine

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_return_url_tests_only")
    monkeypatch.setattr(bot, "pulse_seller_connect_state", lambda *a, **k: {})
    monkeypatch.setattr(rewards_engine, "get_reward",
                        lambda **kw: {"id": 1, "user_id": "4242"})
    monkeypatch.setattr(
        rewards_engine, "disburse_cash_reward",
        lambda *a, **k: {
            "needs_onboarding": True,
            "connect_state": {"connected_account_id": "acct_reward_seller"},
            "reward": {"id": 1},
        })
    return bot.app.test_client().post("/api/pulse/rewards/1/claim", json={})


def drive_payouts_connect(monkeypatch, seller_type):
    """POST the marketplace payout onboarding for one seller type."""
    monkeypatch.setattr(bot, "STRIPE_SECRET_KEY", "sk_test_return_url_tests_only")
    # Each lane is stubbed at the authority that lane actually consults. The
    # merchant lane asks `seller_access_refusal`, which returns a response when
    # it refuses and `None` when it does not, so an approved seller is `None`
    # here. Stubbing the retired `approved_marketplace_seller_for_user` instead
    # would leave this test green against a route that had stopped checking.
    monkeypatch.setattr(bot, "seller_access_refusal", lambda *a, **k: None)
    monkeypatch.setattr(bot, "approved_teacher_for_user", lambda *a, **k: True)
    monkeypatch.setattr(bot, "seller_payout_account",
                        lambda *a, **k: {"connected_account_id": "acct_marketplace_seller"})
    return bot.app.test_client().post(
        "/api/pulse/payouts/connect", json={"seller_type": seller_type})


def test_a_reward_claim_returns_the_seller_to_a_page_that_exists(
        monkeypatch, logged_in, onboarding_links):
    response = drive_rewards_claim(monkeypatch)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(onboarding_links) == 1, "the claim never opened an onboarding link"
    call = onboarding_links[0]
    # Both legs, not just return_url: an expired link sends the seller to
    # refresh_url, and a 404 there strands them just as completely.
    assert resolve(call["return_url"])
    assert resolve(call["refresh_url"])


def test_a_reward_claim_lands_the_seller_on_the_connect_status_page(
        monkeypatch, logged_in, onboarding_links):
    """The destination is the page for the account this path actually creates.

    The rewards claim mints a *merchant* Connect account, so the page that reads
    that account's onboarding state is where a returning seller learns whether
    Stripe accepted them.
    """
    drive_rewards_claim(monkeypatch)

    endpoints = {resolve(onboarding_links[0][leg])[0]
                 for leg in ("return_url", "refresh_url")}
    assert endpoints == {"pulse_merchant_payouts_page"}


@pytest.mark.parametrize("seller_type,endpoint", [
    ("merchant", "pulse_merchant_payouts_page"),
    ("teacher", "pulse_teacher_payouts_page"),
])
def test_marketplace_payout_onboarding_returns_to_a_page_that_exists(
        monkeypatch, logged_in, onboarding_links, seller_type, endpoint):
    """The sibling call site, both of its interpolated seller types."""
    response = drive_payouts_connect(monkeypatch, seller_type)

    assert response.status_code == 200, response.get_data(as_text=True)
    assert len(onboarding_links) == 1
    call = onboarding_links[0]
    assert resolve(call["return_url"])[0] == endpoint
    assert resolve(call["refresh_url"])[0] == endpoint


def test_the_providers_own_default_urls_are_also_served():
    """``create_onboarding_link`` falls back to these when a caller passes none."""
    assert resolve(f"{BASE}/payments/success")[0] == "pulse_payment_success_page"
    assert resolve(f"{BASE}/payments/cancel")[0] == "pulse_payment_cancel_page"


# --------------------------------------------------------------------------
# Coverage gate — this is the part that catches the *next* one
# --------------------------------------------------------------------------

def _onboarding_call_sites():
    """Enclosing function name of every ``create_onboarding_link`` call in bot.py."""
    with open(_BOT_PY, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    sites = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "create_onboarding_link":
                sites.add(fn.name)
    return sites


def test_every_onboarding_call_site_in_bot_py_is_covered_here():
    """A new onboarding link is a new chance to 404 a seller mid-signup.

    This does not assert the URLs are correct — the tests above do that, by
    resolving what the route hands the provider. It asserts that every site is
    reached by one of them, so a third call site cannot ship untested.
    """
    found = _onboarding_call_sites()
    assert found, "no create_onboarding_link call sites found — did bot.py move?"
    assert found == COVERED_CALL_SITES, (
        "Stripe onboarding call sites in bot.py changed: "
        f"{sorted(found ^ COVERED_CALL_SITES)}. Drive the new one through a "
        "test above and resolve its return_url/refresh_url before adding it "
        "to COVERED_CALL_SITES."
    )
