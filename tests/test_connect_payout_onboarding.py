"""Stripe Connect payout onboarding — the boundary that answered a seller twice.

Tapping "Set up payouts" on a physical device produced two stacked sentences and
no account. Two independent defects were behind it:

1. The live platform had never been signed up for Connect, so
   ``stripe.Account.create`` was rejected before it ever reached an account.
   That is a dashboard blocker, but the *code* answered it with "Please try
   again." — advice that can never come true.
2. ``services/payment_provider.py`` still used ``obj.get(...)`` and
   ``dict(obj)`` on Stripe resources. Under the pinned stripe 15.1.0 those raise
   ``AttributeError: get`` and ``KeyError: 0``, so every Connect call would have
   failed even with Connect enabled.

These tests exercise the second against the real installed SDK rather than a
hand-rolled double, because the whole defect is a property of that SDK version.
"""

import json

import pytest
import stripe

from services import payment_provider
from services.marketplace_payment_errors import stripe_response_dict, stripe_response_value


def _account(**overrides):
    """A real stripe 15 resource object, not a dict double."""
    data = {
        "id": "acct_1TESTseller",
        "object": "account",
        "type": "express",
        "charges_enabled": False,
        "payouts_enabled": False,
        "details_submitted": False,
        "requirements": {
            "currently_due": ["external_account", "individual.id_number"],
            "disabled_reason": "requirements.past_due",
        },
    }
    data.update(overrides)
    return stripe.Account.construct_from(data, "sk_test_x")


def _platform_error():
    return stripe.error.InvalidRequestError(
        "You can only create new accounts if you've signed up for Connect, "
        "which you can do at https://dashboard.stripe.com/connect.",
        None,
        http_status=400,
    )


@pytest.fixture
def stripe_key(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_boundary")
    monkeypatch.setenv("APP_BASE_URL", "https://pulsesoc.com")


# --------------------------------------------------------------------------
# The SDK boundary itself
# --------------------------------------------------------------------------

def test_stripe_15_resources_still_break_the_old_accessors():
    """The premise. If this ever fails the SDK changed and the rest is moot."""
    account = _account()
    with pytest.raises(AttributeError):
        account.get("id")
    with pytest.raises(KeyError):
        dict(account)


def test_stripe_response_dict_flattens_a_resource_object():
    plain = stripe_response_dict(_account())
    assert isinstance(plain, dict)
    assert plain["id"] == "acct_1TESTseller"
    assert plain["object"] == "account"


def test_stripe_response_dict_flattens_nested_resources():
    requirements = stripe_response_dict(stripe_response_value(_account(), "requirements", {}))
    assert type(requirements) is dict
    assert requirements["currently_due"] == ["external_account", "individual.id_number"]


def test_stripe_response_dict_output_is_json_serialisable():
    # record_account_snapshot stores this and jsonify hands it to the client.
    json.dumps(stripe_response_dict(_account()))


def test_stripe_response_dict_passes_plain_mappings_through():
    assert stripe_response_dict({"id": "acct_plain"}) == {"id": "acct_plain"}


def test_stripe_response_dict_answers_non_objects_with_an_empty_dict():
    assert stripe_response_dict(None) == {}
    assert stripe_response_dict("acct_str") == {}


# --------------------------------------------------------------------------
# Account creation
# --------------------------------------------------------------------------

def test_create_connected_account_reads_a_resource_object(stripe_key, monkeypatch):
    monkeypatch.setattr(stripe.Account, "create", staticmethod(lambda **kw: _account()))

    result = payment_provider.create_connected_account({"user_id": 7, "email": "s@x.com"}, "merchant")

    assert result["ok"] is True
    assert result["provider_account_id"] == "acct_1TESTseller"
    assert result["account"]["object"] == "account"


def test_create_connected_account_is_idempotent_per_user_and_seller_type(stripe_key, monkeypatch):
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        return _account()

    monkeypatch.setattr(stripe.Account, "create", staticmethod(fake_create))
    payment_provider.create_connected_account({"user_id": 7, "email": "s@x.com"}, "merchant")

    # Two taps in the same second must not mint two Connect accounts.
    assert seen["idempotency_key"] == "connect-account:7:merchant"
    assert seen["type"] == "express"


def test_create_connected_account_without_a_key_is_setup_required(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    result = payment_provider.create_connected_account({"user_id": 7}, "merchant")
    assert result["ok"] is False
    assert result["status"] == "setup_required"


def test_platform_not_signed_up_for_connect_is_its_own_code(stripe_key, monkeypatch):
    monkeypatch.setattr(stripe.Account, "create", staticmethod(lambda **kw: (_ for _ in ()).throw(_platform_error())))

    result = payment_provider.create_connected_account({"user_id": 7}, "merchant")

    assert result["ok"] is False
    assert result["code"] == payment_provider.CONNECT_PLATFORM_CODE
    assert result["http_status"] == 503


def test_platform_blocker_is_not_reported_as_retryable(stripe_key, monkeypatch):
    monkeypatch.setattr(stripe.Account, "create", staticmethod(lambda **kw: (_ for _ in ()).throw(_platform_error())))

    result = payment_provider.create_connected_account({"user_id": 7}, "merchant")

    # Every retry fails identically until PulseSoc enables Connect, so the copy
    # must not send the seller round the loop the owner was sent round.
    assert result["retryable"] is False
    assert "try again" not in result["message"].lower()


def test_no_connect_failure_leaks_the_providers_own_message(stripe_key, monkeypatch):
    monkeypatch.setattr(stripe.Account, "create", staticmethod(lambda **kw: (_ for _ in ()).throw(_platform_error())))

    result = payment_provider.create_connected_account({"user_id": 7}, "merchant")

    body = json.dumps(result)
    assert "dashboard.stripe.com" not in body
    assert "signed up for Connect" not in body
    # The non-sensitive fingerprint is still there, so the failing stage is
    # visible without a log dive.
    assert result["provider_error"]["type"] == "InvalidRequestError"


def test_a_generic_invalid_request_keeps_the_configuration_class(stripe_key, monkeypatch):
    bad = stripe.error.InvalidRequestError("No such account: 'acct_nope'", "account", http_status=400)
    monkeypatch.setattr(stripe.Account, "create", staticmethod(lambda **kw: (_ for _ in ()).throw(bad)))

    result = payment_provider.create_connected_account({"user_id": 7}, "merchant")

    assert result["code"] == "PAYMENT_CONFIGURATION_ERROR"
    assert result["code"] != payment_provider.CONNECT_PLATFORM_CODE


def test_a_connection_failure_stays_retryable(stripe_key, monkeypatch):
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: (_ for _ in ()).throw(stripe.error.APIConnectionError("dropped"))),
    )

    result = payment_provider.create_connected_account({"user_id": 7}, "merchant")

    assert result["code"] == "NETWORK_ERROR"
    assert result["retryable"] is True


# --------------------------------------------------------------------------
# Account links
# --------------------------------------------------------------------------

def test_create_onboarding_link_reads_a_resource_object(stripe_key, monkeypatch):
    link = stripe.AccountLink.construct_from(
        {"object": "account_link", "url": "https://connect.stripe.com/setup/e/acct_1TESTseller"}, "sk_test_x")
    monkeypatch.setattr(stripe.AccountLink, "create", staticmethod(lambda **kw: link))

    result = payment_provider.create_onboarding_link("acct_1TESTseller", "https://pulsesoc.com/r", "https://pulsesoc.com/r")

    assert result["ok"] is True
    assert result["url"].startswith("https://connect.stripe.com/")


def test_create_onboarding_link_sends_both_urls_to_stripe(stripe_key, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        stripe.AccountLink, "create",
        staticmethod(lambda **kw: seen.update(kw) or stripe.AccountLink.construct_from({"url": "https://x"}, "k")),
    )

    payment_provider.create_onboarding_link("acct_1TESTseller")

    assert seen["type"] == "account_onboarding"
    assert seen["refresh_url"].startswith("https://")
    assert seen["return_url"].startswith("https://")


def test_create_onboarding_link_requires_an_account_id(stripe_key):
    assert payment_provider.create_onboarding_link("")["ok"] is False


def test_create_onboarding_link_classifies_a_stripe_rejection(stripe_key, monkeypatch):
    monkeypatch.setattr(
        stripe.AccountLink, "create",
        staticmethod(lambda **kw: (_ for _ in ()).throw(_platform_error())),
    )

    result = payment_provider.create_onboarding_link("acct_1TESTseller")

    assert result["ok"] is False
    assert result["code"] == payment_provider.CONNECT_PLATFORM_CODE


# --------------------------------------------------------------------------
# Status — the part that must not call an unfinished account "ready"
# --------------------------------------------------------------------------

def test_get_account_status_reports_stripes_own_flags(stripe_key, monkeypatch):
    monkeypatch.setattr(stripe.Account, "retrieve", staticmethod(lambda _id: _account()))

    status = payment_provider.get_account_status("acct_1TESTseller")

    assert status["ok"] is True
    assert status["payouts_enabled"] is False
    assert status["charges_enabled"] is False
    assert status["details_submitted"] is False
    assert status["disabled_reason"] == ""
    assert status["onboarding_status"] == "restricted"
    assert status["requirements"]["currently_due"] == ["external_account", "individual.id_number"]


def test_an_existing_account_id_is_not_by_itself_ready(stripe_key, monkeypatch):
    """The mis-mapping this mission forbids: id present, therefore "set up"."""
    monkeypatch.setattr(stripe.Account, "retrieve", staticmethod(lambda _id: _account()))

    status = payment_provider.get_account_status("acct_1TESTseller")

    assert status["provider_account_id"] == "acct_1TESTseller"
    assert status["onboarding_status"] != "enabled"


def test_get_account_status_reports_enabled_only_when_stripe_does(stripe_key, monkeypatch):
    monkeypatch.setattr(
        stripe.Account, "retrieve",
        staticmethod(lambda _id: _account(charges_enabled=True, payouts_enabled=True, details_submitted=True, requirements={})),
    )

    status = payment_provider.get_account_status("acct_1TESTseller")

    assert status["onboarding_status"] == "enabled"
    assert status["payouts_enabled"] is True


def test_get_account_status_is_consumable_by_the_snapshot_recorder(stripe_key, monkeypatch):
    """``record_account_snapshot`` indexes ``status["account"]`` as a dict."""
    monkeypatch.setattr(stripe.Account, "retrieve", staticmethod(lambda _id: _account()))

    status = payment_provider.get_account_status("acct_1TESTseller")

    assert status["account"]["details_submitted"] is False
    json.dumps(status)


def test_get_account_status_returns_a_descriptor_instead_of_raising(stripe_key, monkeypatch):
    monkeypatch.setattr(
        stripe.Account, "retrieve",
        staticmethod(lambda _id: (_ for _ in ()).throw(stripe.error.APIConnectionError("dropped"))),
    )

    status = payment_provider.get_account_status("acct_1TESTseller")

    # A failed refresh must leave the caller with stale-but-true state, never a 500.
    assert status["ok"] is False
    assert status["code"] == "NETWORK_ERROR"
