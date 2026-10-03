"""Which Stripe a deployment is talking to, and what it is allowed to conclude.

Three call sites inferred this independently before this module existed, and all
three shared one blind spot: a key matching neither ``sk_test_`` nor ``sk_live_``
was reported as "not configured". A restricted live key is exactly that shape.
Most of this file is about that case, because it is the only one where being
wrong moves real money.
"""
import html
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import stripe_mode  # noqa: E402


@pytest.fixture(autouse=True)
def _unconfigured(monkeypatch):
    for name in stripe_mode.TEST_MODE_REQUIRED_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    # Not in TEST_MODE_REQUIRED_ENV_VARS, but it *overrides* one of them, so a
    # developer who happens to have it exported would otherwise get different
    # answers from the same test.
    monkeypatch.delenv(stripe_mode.PUBLISHABLE_KEY_OVERRIDE_ENV_VAR, raising=False)


def _key(monkeypatch, value):
    monkeypatch.setenv(stripe_mode.SECRET_KEY_ENV_VAR, value)


def _pk(monkeypatch, value):
    monkeypatch.setenv(stripe_mode.PUBLISHABLE_KEY_ENV_VAR, value)


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


# --- two keys that name different Stripes ------------------------------------
#
# A half-finished test-mode rollout produces this, and reading the secret key
# alone cannot see it. The combination below is the dangerous one: it used to
# report mode=test, may_move_real_money=False and test_mode_ready=True, which
# is exactly the answer that says "safe to run the card suite" while the
# browser is tokenising a real card against live Stripe.

def test_a_test_secret_behind_a_live_publishable_key_is_not_test_mode(monkeypatch):
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "pk_live_abc")
    assert stripe_mode.mode() == stripe_mode.MIXED
    assert not stripe_mode.is_test_mode()


def test_a_mismatched_pair_is_assumed_to_move_real_money(monkeypatch):
    """One of the two keys is a live key, whichever way round it is, so one half
    of the checkout is reaching live Stripe."""
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "pk_live_abc")
    assert stripe_mode.may_move_real_money()


def test_the_mismatch_is_caught_in_the_other_direction_too(monkeypatch):
    _key(monkeypatch, "sk_live_abc")
    _pk(monkeypatch, "pk_test_abc")
    assert stripe_mode.mode() == stripe_mode.MIXED
    assert stripe_mode.may_move_real_money()


def test_a_mismatched_pair_names_the_publishable_key_as_missing(monkeypatch):
    """So the handoff is a variable to fix rather than a state to interpret."""
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "pk_live_abc")
    monkeypatch.setenv(stripe_mode.WEBHOOK_SECRET_ENV_VAR, "whsec_abc")
    monkeypatch.setenv(stripe_mode.CONNECT_CLIENT_ID_ENV_VAR, "ca_abc")
    assert stripe_mode.missing_test_mode_variables() == [
        stripe_mode.PUBLISHABLE_KEY_ENV_VAR]
    assert not stripe_mode.test_mode_ready()


def test_an_agreeing_pair_is_not_a_mismatch(monkeypatch):
    for secret, publishable in (("sk_test_a", "pk_test_a"), ("sk_live_a", "pk_live_a")):
        _key(monkeypatch, secret)
        _pk(monkeypatch, publishable)
        assert not stripe_mode.is_mixed()
        assert stripe_mode.mode() != stripe_mode.MIXED


def test_an_unreadable_publishable_key_is_an_unknown_not_a_contradiction(monkeypatch):
    """`mixed` means the two keys provably name different Stripes. A key nobody
    can classify does not prove that, so it blocks readiness without claiming to
    know which environment it belongs to."""
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "not-a-publishable-key")
    assert not stripe_mode.is_mixed()
    assert stripe_mode.mode() == stripe_mode.TEST
    assert not stripe_mode.may_move_real_money()
    # ...but it is still not ready, by the same doctrine as the secret key.
    assert stripe_mode.PUBLISHABLE_KEY_ENV_VAR in stripe_mode.missing_test_mode_variables()


def test_a_missing_publishable_key_is_not_a_mismatch(monkeypatch):
    _key(monkeypatch, "sk_test_abc")
    assert not stripe_mode.is_mixed()
    assert stripe_mode.mode() == stripe_mode.TEST


# --- which publishable key actually reaches the browser ----------------------

def test_the_next_public_variable_wins_because_bot_py_checks_it_first(monkeypatch):
    """`bot.py` resolves NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY before
    STRIPE_PUBLISHABLE_KEY. Classifying the variable that is merely *set* would
    describe a value no browser ever receives."""
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "pk_test_abc")
    monkeypatch.setenv(stripe_mode.PUBLISHABLE_KEY_OVERRIDE_ENV_VAR, "pk_live_abc")
    assert stripe_mode.publishable_key_env_var() == (
        stripe_mode.PUBLISHABLE_KEY_OVERRIDE_ENV_VAR)
    assert stripe_mode.publishable_mode() == stripe_mode.LIVE
    assert stripe_mode.mode() == stripe_mode.MIXED


def test_bot_py_still_reads_the_override_first(monkeypatch):
    """Pins the precedence this module mirrors. If `bot.py` ever stops
    preferring the override, `publishable_key_env_var` becomes a lie and this
    test is the thing that notices."""
    import pathlib
    import re

    src = pathlib.Path(__file__).resolve().parents[2].joinpath("bot.py").read_text()
    assignments = re.findall(
        r'^STRIPE_PUBLISHABLE_KEY\s*=\s*(.+)$', src, flags=re.MULTILINE)
    # Two assignments exist; the last one wins at import time.
    assert assignments, "STRIPE_PUBLISHABLE_KEY is no longer assigned at module scope"
    winner = assignments[-1]
    assert winner.index("NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY") < winner.index(
        '"STRIPE_PUBLISHABLE_KEY"')


def test_the_override_is_named_in_the_handoff_when_it_is_the_one_in_use(monkeypatch):
    """Telling the owner to fix STRIPE_PUBLISHABLE_KEY while the override shadows
    it is a wasted round trip that leaves the live key in place."""
    _key(monkeypatch, "sk_test_abc")
    monkeypatch.setenv(stripe_mode.PUBLISHABLE_KEY_OVERRIDE_ENV_VAR, "pk_live_abc")
    monkeypatch.setenv(stripe_mode.WEBHOOK_SECRET_ENV_VAR, "whsec_abc")
    monkeypatch.setenv(stripe_mode.CONNECT_CLIENT_ID_ENV_VAR, "ca_abc")
    assert stripe_mode.missing_test_mode_variables() == [
        stripe_mode.PUBLISHABLE_KEY_OVERRIDE_ENV_VAR]


def test_the_override_satisfies_the_requirement_it_shadows(monkeypatch):
    """A deployment that sets only the override is configured, not missing a
    variable -- the override is what the browser gets."""
    _key(monkeypatch, "sk_test_abc")
    monkeypatch.setenv(stripe_mode.PUBLISHABLE_KEY_OVERRIDE_ENV_VAR, "pk_test_abc")
    monkeypatch.setenv(stripe_mode.WEBHOOK_SECRET_ENV_VAR, "whsec_abc")
    monkeypatch.setenv(stripe_mode.CONNECT_CLIENT_ID_ENV_VAR, "ca_abc")
    assert stripe_mode.missing_test_mode_variables() == []
    assert stripe_mode.test_mode_ready()


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


def test_the_payout_worker_will_not_pay_into_a_mismatched_pair(monkeypatch):
    """Two keys naming different Stripes is not something anyone configured on
    purpose, so the three switches cannot be read as consent to it."""
    from services import marketplace_payout_worker as worker

    monkeypatch.setenv(worker.ENABLED_ENV_VAR, "true")
    monkeypatch.setenv(worker.DRY_RUN_ENV_VAR, "false")
    monkeypatch.setenv(worker.OWNER_AUTHORIZED_ENV_VAR, "true")
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "pk_live_abc")
    assert worker._mutation_preconditions() != ""


def test_the_status_surface_says_which_half_is_live(monkeypatch):
    """`mixed` alone cannot say which side of the mismatch is the live one, and
    that is the first thing an operator needs."""
    _key(monkeypatch, "sk_test_abc")
    _pk(monkeypatch, "pk_live_abc")
    status = stripe_mode.status()
    assert status["mode"] == stripe_mode.MIXED
    assert status["keys_disagree"] is True
    assert status["secret_mode"] == stripe_mode.TEST
    assert status["publishable_mode"] == stripe_mode.LIVE
    assert status["test_mode_ready"] is False
    assert status["may_move_real_money"] is True


def test_the_provider_status_asks_rather_than_re_deriving(monkeypatch):
    from services import payment_provider

    _key(monkeypatch, "rk_live_abc")
    # The copy that lived in provider_status called this "not_configured".
    assert payment_provider.provider_status()["mode"] == stripe_mode.LIVE


# --- the admin surface -------------------------------------------------------
#
# /admin/payments-health is where an operator goes to ask "is Stripe set up".
# It counted populated variables, so a mismatched pair -- the one state a count
# cannot see -- read as "ready".

def _payments_health(monkeypatch, secret, publishable):
    os.environ.setdefault("PULSE_TEST_SQLITE", "1")
    import bot

    _key(monkeypatch, secret)
    _pk(monkeypatch, publishable)
    # The module captured these at import, before this test set anything.
    monkeypatch.setattr(bot, "STRIPE_SECRET_KEY", secret, raising=False)
    monkeypatch.setattr(bot, "STRIPE_PUBLISHABLE_KEY", publishable, raising=False)
    monkeypatch.setattr(bot, "STRIPE_WEBHOOK_SECRET", "whsec_abc", raising=False)
    monkeypatch.setattr(bot, "require_admin_page", lambda _p: ({"user_id": 1}, None))
    monkeypatch.setattr(bot, "admin_page_html", lambda _t, body, _a: body)
    with bot.webhook_app.test_request_context("/admin/payments-health"):
        # Unescaped, because the page renders the JSON into HTML and `&quot;` in
        # an assertion reads as a bug in the assertion rather than a contract.
        return html.unescape(bot.admin_payments_health_page())


def test_the_admin_page_does_not_call_a_mismatched_pair_ready(monkeypatch):
    body = _payments_health(monkeypatch, "sk_test_abc", "pk_live_abc")
    assert '"status": "keys_disagree"' in body
    assert '"status": "ready"' not in body
    assert '"stripe_mode": "mixed"' in body
    assert '"may_move_real_money": true' in body


def test_the_admin_page_still_says_ready_when_the_keys_agree(monkeypatch):
    body = _payments_health(monkeypatch, "sk_live_abc", "pk_live_abc")
    assert '"status": "ready"' in body
    assert '"keys_disagree": false' in body


def test_the_admin_page_carries_no_key_material(monkeypatch):
    """It promises "No secrets are exposed" in its own subtitle. The mode block
    added here must not be the thing that makes that false."""
    secret = "sk_test_thisisthesecretvalue"
    body = _payments_health(monkeypatch, secret, "pk_live_alsosecret")
    assert secret not in body
    assert "thisisthesecretvalue" not in body
    assert "alsosecret" not in body
