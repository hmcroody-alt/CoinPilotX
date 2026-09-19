"""Which Stripe a deployment is talking to, and what it is allowed to conclude.

Three call sites inferred this independently before this module existed, and all
three shared one blind spot: a key matching neither ``sk_test_`` nor ``sk_live_``
was reported as "not configured". A restricted live key is exactly that shape.
Most of this file is about that case, because it is the only one where being
wrong moves real money.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import stripe_mode  # noqa: E402


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch):
    for name in stripe_mode.TEST_MODE_REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _key(monkeypatch, value):
    monkeypatch.setenv(stripe_mode.SECRET_KEY_ENV_VAR, value)


# --- reading the key ---------------------------------------------------------

def test_no_key_at_all_is_unconfigured(monkeypatch):
    assert stripe_mode.mode() == stripe_mode.UNCONFIGURED


def test_a_blank_key_is_unconfigured_not_unrecognized(monkeypatch):
    # An empty Railway variable is absence, not a key nobody can classify.
    _key(monkeypatch, "   ")
    assert stripe_mode.mode() == stripe_mode.UNCONFIGURED


@pytest.mark.parametrize("value", ["sk_test_abc123", "rk_test_abc123"])
def test_a_test_key_is_test_mode(monkeypatch, value):
    _key(monkeypatch, value)
    assert stripe_mode.mode() == stripe_mode.TEST
    assert stripe_mode.is_test_mode()


@pytest.mark.parametrize("value", ["sk_live_abc123", "rk_live_abc123"])
def test_a_live_key_is_live_mode(monkeypatch, value):
    """Including the restricted form. ``rk_live_`` is a live key, and the
    inference this module replaced called it "not_configured"."""
    _key(monkeypatch, value)
    assert stripe_mode.mode() == stripe_mode.LIVE
    assert not stripe_mode.is_test_mode()


@pytest.mark.parametrize("value", ["abc123", "sk_abc123", "SK_TEST_ABC", "pk_test_abc"])
def test_a_key_nobody_can_classify_is_its_own_answer(monkeypatch, value):
    """Not test, and not "unconfigured" either.

    The uppercase spelling is in here deliberately: Stripe's prefixes are
    lowercase, so a key that only matches case-insensitively is not a key this
    module has any business calling a test key.
    """
    _key(monkeypatch, value)
    assert stripe_mode.mode() == stripe_mode.UNRECOGNIZED
    assert not stripe_mode.is_test_mode()


# --- what may be concluded from it -------------------------------------------

def test_an_unreadable_key_is_assumed_to_move_real_money(monkeypatch):
    # The asymmetry this module exists for. A test run refused because of an
    # unreadable key costs an afternoon; a live transfer believed to be a test
    # does not come back.
    _key(monkeypatch, "definitely-not-a-stripe-key")
    assert stripe_mode.may_move_real_money()


def test_no_key_moves_no_money(monkeypatch):
    assert not stripe_mode.may_move_real_money()


def test_a_test_key_moves_no_real_money(monkeypatch):
    _key(monkeypatch, "sk_test_abc")
    assert not stripe_mode.may_move_real_money()


# --- the credential handoff --------------------------------------------------

def test_an_empty_deployment_names_every_variable_it_needs(monkeypatch):
    assert stripe_mode.missing_test_mode_variables() == list(
        stripe_mode.TEST_MODE_REQUIRED_ENV_VARS)
    assert not stripe_mode.test_mode_ready()


def test_a_live_key_counts_as_missing_for_a_test_run(monkeypatch):
    """Set to the wrong thing is a different problem from unset, and the same
    amount of not-ready. Reporting it as present would be the misleading half."""
    _key(monkeypatch, "sk_live_abc")
    assert stripe_mode.SECRET_KEY_ENV_VAR in stripe_mode.missing_test_mode_variables()


def test_the_secret_key_is_named_once_not_twice(monkeypatch):
    # It is missing for two reasons at once; it is still one variable to set.
    _key(monkeypatch, "sk_live_abc")
    missing = stripe_mode.missing_test_mode_variables()
    assert missing.count(stripe_mode.SECRET_KEY_ENV_VAR) == 1


def test_a_fully_configured_test_deployment_is_ready(monkeypatch):
    _key(monkeypatch, "sk_test_abc")
    monkeypatch.setenv(stripe_mode.PUBLISHABLE_KEY_ENV_VAR, "pk_test_abc")
    monkeypatch.setenv(stripe_mode.WEBHOOK_SECRET_ENV_VAR, "whsec_abc")
    monkeypatch.setenv(stripe_mode.CONNECT_CLIENT_ID_ENV_VAR, "ca_abc")
    assert stripe_mode.missing_test_mode_variables() == []
    assert stripe_mode.test_mode_ready()


def test_connect_onboarding_is_required_for_a_test_run(monkeypatch):
    """Onboarding a test seller is part of the path under test, not an extra."""
    _key(monkeypatch, "sk_test_abc")
    monkeypatch.setenv(stripe_mode.PUBLISHABLE_KEY_ENV_VAR, "pk_test_abc")
    monkeypatch.setenv(stripe_mode.WEBHOOK_SECRET_ENV_VAR, "whsec_abc")
    assert stripe_mode.missing_test_mode_variables() == [
        stripe_mode.CONNECT_CLIENT_ID_ENV_VAR]


# --- the status surface ------------------------------------------------------

def test_status_never_carries_key_material(monkeypatch):
    """An admin surface is the wrong place to make a credential guessable —
    including by length or prefix, not just by value."""
    secret = "sk_test_thisisthesecretvalue"
    _key(monkeypatch, secret)
    rendered = repr(stripe_mode.status())
    assert secret not in rendered
    assert "thisisthesecretvalue" not in rendered
    assert str(len(secret)) not in rendered


def test_status_says_which_variables_are_present_without_their_values(monkeypatch):
    _key(monkeypatch, "sk_test_abc")
    status = stripe_mode.status()
    assert status["configured"][stripe_mode.SECRET_KEY_ENV_VAR] is True
    assert status["configured"][stripe_mode.CONNECT_CLIENT_ID_ENV_VAR] is False
    assert status["mode"] == stripe_mode.TEST
    assert status["test_mode_ready"] is False


# --- the mode is read per call, not per process ------------------------------

def test_the_mode_is_read_per_call(monkeypatch):
    """A worker that booted before a variable was set must not act on the value
    it booted with. Import-time capture is the bug this pins."""
    _key(monkeypatch, "sk_test_abc")
    assert stripe_mode.mode() == stripe_mode.TEST
    _key(monkeypatch, "sk_live_abc")
    assert stripe_mode.mode() == stripe_mode.LIVE


# --- the consumers -----------------------------------------------------------

def test_the_payout_worker_will_not_pay_into_an_unreadable_stripe(monkeypatch):
    """The owner's three switches record that a payout run was authorised. They
    cannot record that the owner knew which Stripe it would reach."""
    from services import marketplace_payout_worker as worker

    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    _key(monkeypatch, "not-a-recognisable-key")
    assert worker._mutation_preconditions() != ""


def test_the_provider_status_asks_rather_than_re_deriving(monkeypatch):
    from services import payment_provider

    _key(monkeypatch, "rk_live_abc")
    # The copy that lived in provider_status called this "not_configured".
    assert payment_provider.provider_status()["mode"] == stripe_mode.LIVE
