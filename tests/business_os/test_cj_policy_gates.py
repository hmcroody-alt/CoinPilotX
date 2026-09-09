"""The provider-authorization gate is three questions, not one.

Collapsing them into a single CJ_HOSTED_CREDENTIALS_APPROVED flag meant the only
way to let one authorized merchant test sandbox with their own CJ key was to
assert a provider-wide SaaS approval nobody has. These tests pin the split so it
cannot quietly collapse back, and so the narrow flag cannot grow into a general
production bypass.
"""

import pytest

from services.business_os.suppliers import policy
from services.business_os.suppliers.errors import SupplierError

ALL_FLAGS = (
    "CJ_HOSTED_CREDENTIALS_APPROVED",
    "CJ_MULTI_MERCHANT_SAAS_APPROVED",
    "CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED",
    "CJ_CONTENT_REDISPLAY_APPROVED",
    "CJ_NETWORK_ENABLED",
    "CJ_ENVIRONMENT_MODE",
    "PRODUCTION_CJ_FULFILLMENT_ENABLED",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ALL_FLAGS:
        monkeypatch.delenv(name, raising=False)


def _code(excinfo):
    return getattr(excinfo.value, "code", None)


def test_the_gate_fails_closed_when_nothing_is_configured():
    with pytest.raises(SupplierError) as excinfo:
        policy.require_network()
    assert _code(excinfo) == "provider_approval_required"


def test_a_single_authorized_merchant_in_sandbox_may_reach_the_provider(monkeypatch):
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    policy.require_network()


def test_the_narrow_authorization_does_not_survive_a_non_sandbox_environment(monkeypatch):
    """Otherwise "one merchant" becomes a production bypass."""
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    with pytest.raises(SupplierError) as excinfo:
        policy.require_network()
    assert _code(excinfo) == "provider_approval_required"


def test_the_narrow_authorization_never_claims_provider_wide_approval(monkeypatch):
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    status = policy.safe_status()
    assert status["single_merchant_sandbox_authorized"] is True
    assert status["multi_merchant_saas_approved"] is False
    assert status["provider_approval_recorded"] is False
    assert policy.multi_merchant_saas_approved() is False


def test_the_legacy_flag_still_means_the_broad_claim(monkeypatch):
    monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "true")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    policy.require_network()
    assert policy.multi_merchant_saas_approved() is True
    assert policy.safe_status()["provider_approval_recorded"] is True


def test_the_broad_claim_is_not_confined_to_sandbox_but_the_narrow_one_is(monkeypatch):
    monkeypatch.setenv("CJ_MULTI_MERCHANT_SAAS_APPROVED", "ON")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    policy.require_network()


def test_network_enablement_is_still_independently_required(monkeypatch):
    """Authorization is permission, not a switch. Both are needed."""
    for authorizing in ("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "CJ_MULTI_MERCHANT_SAAS_APPROVED"):
        monkeypatch.setenv(authorizing, "ON")
        with pytest.raises(SupplierError) as excinfo:
            policy.require_network()
        assert _code(excinfo) == "provider_network_disabled"
        monkeypatch.delenv(authorizing, raising=False)


def test_content_redisplay_is_tracked_but_does_not_gate_fetching(monkeypatch):
    """Importing to a private draft redisplays nothing publicly."""
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    assert policy.content_redisplay_approved() is False
    assert policy.safe_status()["content_redisplay_approved"] is False
    policy.require_network()

    monkeypatch.setenv("CJ_CONTENT_REDISPLAY_APPROVED", "ON")
    assert policy.content_redisplay_approved() is True


def test_safe_status_never_reports_production_fulfillment_or_funding(monkeypatch):
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "ON")
    status = policy.safe_status()
    assert status["production_fulfillment_enabled"] is False
    assert status["real_funding_enabled"] is False
    assert status["configuration_valid"] is False


def test_network_permitted_requires_both_halves(monkeypatch):
    assert policy.safe_status()["network_permitted"] is False
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    assert policy.safe_status()["network_permitted"] is False
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    assert policy.safe_status()["network_permitted"] is True


def test_sandbox_fulfillment_still_refuses_a_non_sandbox_environment(monkeypatch):
    """The narrow split must not have loosened the fulfillment guard."""
    monkeypatch.setenv("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "ON")
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    with pytest.raises(SupplierError) as excinfo:
        policy.require_sandbox({"isSandbox": 1})
    assert _code(excinfo) == "production_fulfillment_disabled"


def test_funding_is_refused_regardless_of_any_authorization(monkeypatch):
    for name in ("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED", "CJ_MULTI_MERCHANT_SAAS_APPROVED",
                 "CJ_HOSTED_CREDENTIALS_APPROVED", "CJ_NETWORK_ENABLED"):
        monkeypatch.setenv(name, "ON")
    with pytest.raises(SupplierError):
        policy.require_funding_disabled()
