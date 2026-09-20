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
    first = seen["idempotency_key"]
    payment_provider.create_connected_account({"user_id": 7, "email": "s@x.com"}, "merchant")

    # Two taps in the same second must not mint two Connect accounts.
    assert seen["idempotency_key"] == first
    assert first.startswith("connect-account:7:merchant:")
    assert seen["type"] == "express"


def test_a_retry_after_the_seller_changed_their_email_is_not_refused_by_stripe():
    """The key is remembered for 24h; a repeat with new parameters is an error.

    Stripe replays a key only when the parameters match — otherwise it raises
    ``IdempotencyError``, which this route surfaces to the seller as a problem
    on PulseSoc's side. A seller who starts onboarding, fixes their email and
    tries again the same day is exactly that case, and it is not an error: the
    connected account was never created, so there is nothing to protect.
    Asserted on the key function so no live Stripe call is needed.
    """
    key = payment_provider._account_idempotency_key
    assert key("7", "merchant", "old@x.com") != key("7", "merchant", "new@x.com")
    # ...while everything that must still collide, still does.
    assert key("7", "merchant", "s@x.com") == key("7", "merchant", "s@x.com")
    assert key("7", "merchant", "") != key("8", "merchant", "")
    assert key("7", "merchant", "") != key("7", "teacher", "")


def test_the_idempotency_key_does_not_carry_the_sellers_email_in_the_clear():
    """Keys are echoed in Stripe's dashboard and request logs."""
    key = payment_provider._account_idempotency_key("7", "merchant", "seller@example.com")
    assert "seller@example.com" not in key
    assert "seller" not in key.split(":")[-1]
    assert key.startswith("connect-account:7:merchant:")


def test_a_new_account_cannot_use_stripe_automatic_payouts_and_pulsesoc_payouts_at_once(stripe_key, monkeypatch):
    """PulseSoc calls ``Payout.create`` itself, so Stripe must not also schedule.

    Express accounts default to Stripe's *automatic* payout schedule. With
    separate charges and transfers, the worker moves the seller's cut with
    ``Transfer.create`` and then ``Payout.create`` against the connected
    account — an automatic schedule would sweep that same balance on Stripe's
    own timetable, so both would pay the seller for one sale. The account has to
    be born manual; there is no later call that fixes an account minted wrong.
    """
    seen = {}

    def fake_create(**kwargs):
        seen.update(kwargs)
        return _account()

    monkeypatch.setattr(stripe.Account, "create", staticmethod(fake_create))
    payment_provider.create_connected_account({"user_id": 7, "email": "s@x.com"}, "merchant")

    # Asserted on the kwargs Stripe was actually handed, not on the source text.
    assert seen["settings"]["payouts"]["schedule"]["interval"] == "manual"


def test_the_manual_schedule_is_a_shape_stripe_15_accepts(stripe_key, monkeypatch):
    """Guards the nesting, which is the one way to send this and be ignored.

    A flat ``payout_schedule=`` or a ``settings.payouts.schedule.interval``
    string would be accepted by a mock and rejected (or silently dropped) by the
    API, leaving the account on the automatic default. Checked against the
    installed SDK's own parameter type.
    """
    from stripe.params._account_create_params import (
        AccountCreateParamsSettings,
        AccountCreateParamsSettingsPayouts,
        AccountCreateParamsSettingsPayoutsSchedule,
    )

    seen = {}
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: seen.update(kw) or _account()),
    )
    payment_provider.create_connected_account({"user_id": 7}, "merchant")

    assert set(seen["settings"]).issubset(AccountCreateParamsSettings.__annotations__)
    assert set(seen["settings"]["payouts"]).issubset(AccountCreateParamsSettingsPayouts.__annotations__)
    schedule = seen["settings"]["payouts"]["schedule"]
    assert set(schedule).issubset(AccountCreateParamsSettingsPayoutsSchedule.__annotations__)
    assert schedule["interval"] in {"daily", "manual", "monthly", "weekly"}


# --------------------------------------------------------------------------
# The idempotency key must identify one seller, or no call happens
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "user",
    [
        pytest.param({}, id="absent"),
        pytest.param({"user_id": ""}, id="empty"),
        pytest.param({"user_id": None}, id="none"),
        pytest.param({"user_id": 0}, id="zero"),
        pytest.param({"user_id": "   "}, id="whitespace"),
        pytest.param({"user_id": "undefined"}, id="non_numeric"),
    ],
)
def test_an_unusable_user_id_never_reaches_stripe(stripe_key, monkeypatch, user):
    """Fail closed: no id, no call — never a key two sellers could share.

    ``connect-account::merchant`` is a *valid* idempotency key, so Stripe would
    not reject it; it would replay the first seller's cached response and hand
    that seller's connected account to everyone after them.
    """
    calls = []
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: calls.append(kw) or _account()),
    )

    result = payment_provider.create_connected_account(user, "merchant")

    assert calls == []
    assert result["ok"] is False
    assert result["code"] == payment_provider.CONNECT_IDENTITY_CODE
    assert json.dumps(result)


def test_an_empty_seller_type_never_reaches_stripe(stripe_key, monkeypatch):
    calls = []
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: calls.append(kw) or _account()),
    )

    result = payment_provider.create_connected_account({"user_id": 7}, "")

    assert calls == []
    assert result["ok"] is False
    assert result["code"] == payment_provider.CONNECT_IDENTITY_CODE


def test_the_refusal_is_shaped_like_every_other_connect_failure(stripe_key, monkeypatch):
    """The route reads these six keys off any failed Connect result."""
    monkeypatch.setattr(stripe.Account, "create", staticmethod(lambda **kw: _account()))

    refusal = payment_provider.create_connected_account({}, "merchant")
    provider_failure = payment_provider.connect_failure(_platform_error(), "account_create")

    assert set(refusal) == set(provider_failure)
    assert refusal["retryable"] is False
    assert set(refusal["provider_error"]) == {"type", "code", "param"}
    # Nothing was called, so there is no provider error to fingerprint.
    assert refusal["provider_error"]["type"] == ""
    assert int(refusal["http_status"]) == 400


def test_two_sellers_with_no_id_do_not_collapse_onto_one_idempotency_key(stripe_key, monkeypatch):
    """The actual harm: one Connect account silently shared by many sellers."""
    keys = []
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: keys.append(kw.get("idempotency_key")) or _account()),
    )

    payment_provider.create_connected_account({"email": "a@x.com"}, "merchant")
    payment_provider.create_connected_account({"email": "b@x.com"}, "merchant")

    assert keys == []
    assert "connect-account::merchant" not in keys


def test_the_idempotency_key_still_names_the_seller_on_the_normal_path(stripe_key, monkeypatch):
    """The fail-closed guard must not have narrowed the working path."""
    keys = []
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: keys.append(kw.get("idempotency_key")) or _account()),
    )

    assert payment_provider.create_connected_account({"user_id": 7}, "merchant")["ok"] is True
    assert payment_provider.create_connected_account({"user_id": "8"}, "teacher")["ok"] is True

    assert [k.rsplit(":", 1)[0] for k in keys] == ["connect-account:7:merchant", "connect-account:8:teacher"]
    assert keys[0] != keys[1]


def test_the_seller_id_in_metadata_matches_the_one_in_the_key(stripe_key, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        stripe.Account, "create",
        staticmethod(lambda **kw: seen.update(kw) or _account()),
    )

    payment_provider.create_connected_account({"user_id": "9"}, "merchant")

    assert seen["metadata"]["user_id"] == "9"
    assert seen["idempotency_key"].split(":")[1] == seen["metadata"]["user_id"]


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
    # `disabled_reason` is Stripe's, and Stripe puts it on `requirements` — as
    # this file's own `_account()` fixture has always done. This assertion used
    # to read `== ""`, which passed because the extraction looked for a
    # top-level key that Stripe does not send. It pinned the absence rather than
    # the value, so the single field that separates "Stripe wants a document"
    # from "Stripe has stopped this account" read empty for every account.
    assert status["disabled_reason"] == "requirements.past_due"
    assert status["onboarding_status"] == "restricted"
    assert status["requirements"]["currently_due"] == ["external_account", "individual.id_number"]
    # Flattened alongside the nested copy so a caller choosing what to tell a
    # seller does not have to know which nesting a field arrived in.
    assert status["currently_due"] == ["external_account", "individual.id_number"]
    assert status["past_due"] == []


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
