"""The environment label must be derived from the gate, not asserted beside it.

The mission that prompted these tests asked for the Sandbox badge to be replaced
with a server-verified ``Live``. The honest outcome is the opposite of the one
requested: this deployment has no code that can place a payable CJ order, so
there is nothing for a ``Live`` badge to be true about, and the useful work is
making the label *incapable* of claiming otherwise.

The label used to be the string ``"SANDBOX"``, written directly into the
connection payload and into ``policy.safe_status``. That was accurate on the day
it was written and structurally unable to stop being accurate, which is the
problem: a constant cannot track the thing it describes. Setting
``CJ_ENVIRONMENT_MODE=PRODUCTION`` stopped sandbox orders working -- ``cj.py``
raises ``PRODUCTION_FULFILLMENT_DISABLED`` -- while every merchant's screen kept
showing the same Sandbox chip. The badge and the runtime disagreed and only the
runtime knew.

So the tests below are about provenance, not about a string. They assert that
the label follows ``require_sandbox``, including into the configuration nobody
intended, and that ``LIVE`` stays unreachable while the live path is missing.
"""

import os
import tempfile

os.environ.setdefault("DATABASE_URL",
                      "sqlite:///" + tempfile.mkstemp(suffix="_env_badge.db")[1])

import pytest

from services.business_os.suppliers import policy
from services.business_os.suppliers.errors import SupplierError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("CJ_ENVIRONMENT_MODE", "PRODUCTION_CJ_FULFILLMENT_ENABLED"):
        monkeypatch.delenv(name, raising=False)
    yield


def test_the_default_deployment_reports_sandbox(monkeypatch):
    """Unset environment means sandbox, and sandbox orders really do work."""
    assert policy.fulfillment_environment() == policy.ENVIRONMENT_SANDBOX
    # Not just the label: the gate the label is derived from agrees.
    policy.require_sandbox({"isSandbox": 1})


def test_production_mode_reports_disabled_not_live(monkeypatch):
    """The configuration that *sounds* live is the one that orders nothing.

    This is the case the old constant got wrong in the safe direction and would
    have got wrong in the unsafe direction the moment someone "fixed" the badge
    by reading the same variable the gate reads. ``PRODUCTION`` here does not
    mean real orders flow; it means ``create_sandbox_fulfillment`` refuses and
    no order of any kind is created.
    """
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    assert policy.fulfillment_environment() == policy.ENVIRONMENT_DISABLED
    assert policy.fulfillment_environment() != policy.ENVIRONMENT_LIVE
    with pytest.raises(SupplierError):
        policy.require_sandbox({"isSandbox": 1})


def test_turning_on_production_fulfillment_also_reports_disabled(monkeypatch):
    """The second half of the sandbox gate moves the label too.

    ``require_sandbox`` refuses when ``PRODUCTION_CJ_FULFILLMENT_ENABLED`` is on
    even while the mode is SANDBOX. A label reading only ``CJ_ENVIRONMENT_MODE``
    would miss this entirely and keep saying Sandbox over a dead order path --
    the exact drift that probing the gate is meant to make impossible.
    """
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "SANDBOX")
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "1")
    assert policy.fulfillment_environment() == policy.ENVIRONMENT_DISABLED


@pytest.mark.parametrize("mode", ["sandbox", " SANDBOX ", "Sandbox"])
def test_the_label_is_case_and_whitespace_insensitive_like_the_gate(monkeypatch, mode):
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", mode)
    assert policy.fulfillment_environment() == policy.ENVIRONMENT_SANDBOX


def test_live_is_unreachable_while_no_live_path_exists(monkeypatch):
    """No environment variable may produce a Live badge today.

    Exhaustive over the mode values a deployment could plausibly set. The point
    is not that these particular strings are handled; it is that `LIVE` is
    gated on code existing rather than on configuration, so no amount of
    variable-setting can produce it.
    """
    assert policy.live_fulfillment_path_exists() is False
    for mode in ("PRODUCTION", "LIVE", "production", "prod", "", "REAL"):
        monkeypatch.setenv("CJ_ENVIRONMENT_MODE", mode)
        assert policy.fulfillment_environment() != policy.ENVIRONMENT_LIVE


def test_live_would_be_reported_once_the_path_exists(monkeypatch):
    """A positive control, so the LIVE branch is not dead code nobody checked.

    Without this, every assertion above passes against a function hard-wired to
    return ``DISABLED`` and the ``LIVE`` constant would be decoration. Faking the
    capability -- and nothing else that matters -- shows the branch is wired to
    the fact it claims to depend on.

    This now also sets ``PRODUCTION_CJ_FULFILLMENT_ENABLED``, and the change is
    not a weakening. While the live path was a hardcoded ``False`` the badge
    could gate ``LIVE`` on that alone; now that ``require_live`` exists the badge
    probes it, and a label ignoring one of that gate's conditions would be the
    original defect rebuilt on the live side. The test grew a setenv because the
    code stopped guessing.
    """
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "1")
    monkeypatch.setattr(policy, "live_fulfillment_path_exists", lambda: True)
    assert policy.fulfillment_environment() == policy.ENVIRONMENT_LIVE


def test_the_live_path_existing_is_not_on_its_own_enough_for_a_live_badge(monkeypatch):
    """The case the setenv above exposed, asserted rather than assumed.

    Code existing and a deployment being configured to use it are two facts, and
    the badge reports the conjunction. ``PRODUCTION`` mode with the live path
    present but ``PRODUCTION_CJ_FULFILLMENT_ENABLED`` unset is a runtime where
    ``require_live`` refuses every order -- so the honest label is ``DISABLED``,
    for exactly the reason ``PRODUCTION`` alone was never ``LIVE``.
    """
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    monkeypatch.setattr(policy, "live_fulfillment_path_exists", lambda: True)
    assert policy.fulfillment_environment() == policy.ENVIRONMENT_DISABLED


@pytest.mark.parametrize("mode", ["SANDBOX", "sandbox", "", "prod", "REAL", "Live "])
def test_a_live_badge_needs_a_mode_that_names_live(monkeypatch, mode):
    """Everything is granted except the mode, so the mode is what is under test.

    ``require_live`` accepts ``LIVE`` and ``PRODUCTION``; ``require_sandbox``
    accepts ``SANDBOX``. ``"SANDBOX"`` appears here because with the production
    flag on, the sandbox gate refuses too -- neither gate accepts, and the label
    is ``DISABLED`` rather than the nearest match.
    """
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", mode)
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "1")
    monkeypatch.setattr(policy, "live_fulfillment_path_exists", lambda: True)
    expected = (policy.ENVIRONMENT_LIVE if mode.strip().upper() in {"LIVE", "PRODUCTION"}
                else policy.ENVIRONMENT_DISABLED)
    assert policy.fulfillment_environment() == expected


def test_safe_status_carries_the_derived_label(monkeypatch):
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    status = policy.safe_status()
    assert status["environment"] == policy.ENVIRONMENT_DISABLED
    assert status["production_fulfillment_enabled"] is False
    # Independently true regardless of the badge: funding never opens.
    assert status["real_funding_enabled"] is False


def test_funding_is_refused_in_every_environment(monkeypatch):
    """The badge is cosmetic next to this, and this is the real guarantee.

    Whatever any label says, there is no configuration in which a CJ order can
    be paid for. Asserting it here keeps the badge work from reading as though
    it moved the money question.
    """
    for mode in ("SANDBOX", "PRODUCTION", "LIVE"):
        monkeypatch.setenv("CJ_ENVIRONMENT_MODE", mode)
        with pytest.raises(SupplierError):
            policy.require_funding_disabled()


def test_the_connection_payload_does_not_hardcode_an_environment():
    """The payload builder must not reintroduce a constant.

    Read as source rather than behaviour on purpose. A behavioural test here
    would need a seeded connection row, and the thing worth protecting is not
    what the current configuration produces -- it is that nobody writes the
    literal back in when the badge is inconvenient.
    """
    import inspect

    from services.business_os.suppliers import connections

    source = inspect.getsource(connections._public)
    assert 'out["environment"] = policy.fulfillment_environment()' in source
    assert 'out["environment"] = "SANDBOX"' not in source
    assert 'out["environment"] = "LIVE"' not in source
