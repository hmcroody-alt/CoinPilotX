"""What is a provider fact, what is a deployment switch, and what is neither.

There used to be a three-way approval gate here, because nobody knew whether a
merchant could hand us their own CJ API key for us to hold and call with. CJ
Developer Support answered that on 2026-09-09: that is the standard model, it
needs no separate approval, and it repeats per merchant. So the gate is gone --
not loosened, gone -- and these tests pin what replaced it.

The risk this file exists to catch is the mirror image of the old one. Before,
we understated our rights and could not do the ordinary thing. Now the tempting
mistake is to overstate them: to let "CJ confirmed how the API works" drift into
"CJ is our partner", or to let the removed gate take the real production
blockers with it. So the assertions come in three kinds:

  * the standard model needs no approval flag (the confirmation, honoured);
  * `CJ_NETWORK_ENABLED` alone still keeps production dark (the switch, kept);
  * no status field can be read as a partner agreement, and multi-merchant
    scale stays blocked on the one thing still genuinely unproven -- which
    outbound IP our calls leave from (the limits, not papered over).
"""

import pytest

from services.business_os.suppliers import policy
from services.business_os.suppliers.errors import SupplierError

# The first three are the retired approval flags. They are listed here so the
# test suite keeps naming them: their whole point now is that setting them must
# do nothing at all, and a name you stop mentioning is a name you stop checking.
RETIRED_FLAGS = (
    "CJ_HOSTED_CREDENTIALS_APPROVED",
    "CJ_MULTI_MERCHANT_SAAS_APPROVED",
    "CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED",
    "CJ_CONTENT_REDISPLAY_APPROVED",
)
LIVE_FLAGS = (
    "CJ_NETWORK_ENABLED",
    "CJ_ENVIRONMENT_MODE",
    "CJ_EGRESS_IP_ATTESTED",
    "PRODUCTION_CJ_FULFILLMENT_ENABLED",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in RETIRED_FLAGS + LIVE_FLAGS:
        monkeypatch.delenv(name, raising=False)


def _code(excinfo):
    return getattr(excinfo.value, "code", None)


def test_an_unconfigured_deployment_still_cannot_reach_the_provider():
    """Removing the approval gate must not have removed the rollout switch."""
    with pytest.raises(SupplierError) as excinfo:
        policy.require_network()
    assert _code(excinfo) == "provider_network_disabled"
    assert excinfo.value.http_status == 503


def test_the_standard_model_needs_only_the_rollout_switch(monkeypatch):
    """CJ confirmed the merchant-own-key model needs no separate approval.

    So the switch that remains is the only thing between a deployment and CJ.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    policy.require_network()


@pytest.mark.parametrize("retired", RETIRED_FLAGS)
def test_a_retired_approval_flag_neither_grants_nor_withholds_anything(monkeypatch, retired):
    """Setting one must not open the network; unsetting it must not close it.

    A stale `CJ_HOSTED_CREDENTIALS_APPROVED=OFF` is still set on at least one
    real environment. If it regained meaning, that environment would go dark for
    a reason no longer connected to anything CJ requires.
    """
    monkeypatch.setenv(retired, "ON")
    with pytest.raises(SupplierError) as excinfo:
        policy.require_network()
    assert _code(excinfo) == "provider_network_disabled"

    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    policy.require_network()
    monkeypatch.setenv(retired, "OFF")
    policy.require_network()


def test_the_confirmation_is_a_constant_not_a_switch(monkeypatch):
    """A provider fact must not be answerable per deployment.

    A flag here would imply some deployment could decide a merchant may not use
    their own credential. That is not a thing a deployment gets to decide, so
    there must be no environment variable that changes these.
    """
    for name in RETIRED_FLAGS + LIVE_FLAGS:
        monkeypatch.setenv(name, "ON")
    assert policy.STANDARD_API_MODEL_CONFIRMED is True
    assert policy.MERCHANT_OWNED_CREDENTIAL_CUSTODY_CONFIRMED is True
    assert policy.MULTI_MERCHANT_STANDARD_MODEL_CONFIRMED is True
    assert policy.PARTNER_AGREEMENT_REQUIRED is False


def test_no_status_field_can_be_read_as_a_partner_claim(monkeypatch):
    """CJ confirmed a technical model. That is not an agreement.

    The failure this guards is a dashboard growing an "Official CJ Partner"
    badge out of a truthy status field, so the two facts are reported as
    separate keys and the partner one is pinned False.
    """
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    status = policy.safe_status()
    assert status["standard_api_model_confirmed"] is True
    assert status["partner_agreement_held"] is False
    assert policy.PARTNER_AGREEMENT_HELD is False
    # No key may go True on the strength of the confirmation alone.
    assert not any("partner" in key and value is True for key, value in status.items())


def test_content_usage_is_confirmed_and_does_not_depend_on_a_flag(monkeypatch):
    """CJ confirmed storing and displaying its media to sell those products.

    No attribution, no per-supplier permission, no fee -- so the old
    `CJ_CONTENT_REDISPLAY_APPROVED` switch has nothing left to gate. Sanitizing
    the bytes is a separate obligation asserted elsewhere; confirmation of
    rights is not a reason to trust provider content.
    """
    assert policy.content_usage_authorized() is True
    assert policy.safe_status()["content_usage_authorized"] is True
    monkeypatch.setenv("CJ_CONTENT_REDISPLAY_APPROVED", "OFF")
    assert policy.content_usage_authorized() is True


def test_cj_hard_limits_are_reported_as_the_provider_stated_them():
    """Three accounts and ten business calls per second, per outbound IP.

    Pinned as numbers because they are CJ's, not ours to tune. `quota.py`
    enforces them; this asserts the policy surface agrees with what CJ said, so
    a limiter change cannot silently redefine the limit it claims to enforce.
    """
    assert policy.MAX_CJ_ACCOUNTS_PER_EGRESS_IP == 3
    assert policy.MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP == 10
    status = policy.safe_status()
    assert status["max_cj_accounts_per_egress_ip"] == 3
    assert status["max_cj_calls_per_second_per_egress_ip"] == 10


def test_multi_merchant_scale_is_blocked_until_the_egress_ip_is_attested(monkeypatch):
    """The honest blocker that survives CJ's confirmation.

    CJ's ceiling is per outbound IP, and `quota.py` counts per egress *label* --
    a string a human types. Those agree only if someone checked. Until they
    have, the blocker is architectural, not a policy question.
    """
    assert policy.egress_ip_attested() is False
    assert policy.multi_merchant_scale_blocker() == "BLOCKED_BY_EGRESS_ARCHITECTURE"
    assert policy.safe_status()["multi_merchant_scale_blocker"] == "BLOCKED_BY_EGRESS_ARCHITECTURE"

    monkeypatch.setenv("CJ_EGRESS_IP_ATTESTED", "ON")
    assert policy.egress_ip_attested() is True
    assert policy.multi_merchant_scale_blocker() is None
    assert policy.safe_status()["egress_ip_attested"] is True


def test_enabling_the_network_does_not_attest_the_egress_ip(monkeypatch):
    """Two unrelated facts. Reaching CJ at all is not knowing where from."""
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    assert policy.multi_merchant_scale_blocker() == "BLOCKED_BY_EGRESS_ARCHITECTURE"


def test_safe_status_never_reports_production_fulfillment_or_funding(monkeypatch):
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    monkeypatch.setenv("PRODUCTION_CJ_FULFILLMENT_ENABLED", "ON")
    status = policy.safe_status()
    assert status["production_fulfillment_enabled"] is False
    assert status["real_funding_enabled"] is False
    assert status["configuration_valid"] is False


def test_network_permitted_tracks_the_switch_and_nothing_else(monkeypatch):
    assert policy.safe_status()["network_permitted"] is False
    monkeypatch.setenv("CJ_HOSTED_CREDENTIALS_APPROVED", "ON")
    assert policy.safe_status()["network_permitted"] is False
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    assert policy.safe_status()["network_permitted"] is True


def test_sandbox_fulfillment_still_refuses_a_non_sandbox_environment(monkeypatch):
    """Removing the approval gate must not have loosened the fulfillment guard."""
    monkeypatch.setenv("CJ_NETWORK_ENABLED", "ON")
    monkeypatch.setenv("CJ_ENVIRONMENT_MODE", "PRODUCTION")
    with pytest.raises(SupplierError) as excinfo:
        policy.require_sandbox({"isSandbox": 1})
    assert _code(excinfo) == "production_fulfillment_disabled"


def test_sandbox_fulfillment_requires_the_per_order_flag(monkeypatch):
    """CJ has no account-level sandbox switch; it is `isSandbox` per order.

    Typed strictly: `True` is an `int` subclass in Python and a string "1" is
    what a JSON body most easily carries, so both must be refused or the flag
    could be satisfied by something that never reaches CJ as the integer 1.
    """
    for payload in ({}, {"isSandbox": 0}, {"isSandbox": "1"}, {"isSandbox": True}):
        with pytest.raises(SupplierError) as excinfo:
            policy.require_sandbox(payload)
        assert _code(excinfo) == "sandbox_flag_required"
    policy.require_sandbox({"isSandbox": 1})


def test_funding_is_refused_regardless_of_any_flag(monkeypatch):
    for name in RETIRED_FLAGS + LIVE_FLAGS:
        monkeypatch.setenv(name, "ON")
    with pytest.raises(SupplierError):
        policy.require_funding_disabled()


def test_the_canonical_policy_doc_never_names_an_identifier_that_does_not_exist():
    """The doc declares itself the authority; a fabricated symbol defeats that.

    `docs/cj/CJ_PROVIDER_POLICY_CONFIRMATION.md` opens by saying that if it and
    the code disagree, the code is wrong and this file is what to check it
    against. That only works while every symbol it cites is real. It once named
    an error code `CJ_EGRESS_CAPACITY_EXHAUSTED` that appears nowhere in the
    repository -- an operator grepping for the account-cap control would have
    found nothing and concluded there wasn't one, which is a worse failure than
    the doc simply being out of date.

    So: every SCREAMING_SNAKE_CASE token in backticks must exist somewhere in
    the supplier package, and every `module.attribute` reference must be defined
    in *that* module -- naming the wrong module is its own way of sending a
    reader to a dead end. Deliberately a weak check on the prose side --
    existence, not correctness of the surrounding sentence -- because a strong
    one would need to parse English and would be turned off the first time it
    was wrong. Names that are environment variables or provider concepts rather
    than code are listed explicitly, so adding to that list is a visible act.

    `CJ_EGRESS_CAPACITY_EXHAUSTED` is on that list even though it is exactly the
    fiction this test exists to catch. The doc now names it in a correction note,
    spelled out rather than escaped, so that an operator who read the old line
    and grepped for it finds the correction instead of nothing -- which was the
    original failure. Suppressing the name to satisfy this check would have
    recreated it.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    doc = (root / "docs/cj/CJ_PROVIDER_POLICY_CONFIRMATION.md").read_text()
    package = root / "services/business_os/suppliers"
    modules = {path.stem: path.read_text() for path in sorted(package.glob("*.py"))}
    source = "\n".join(modules.values())

    # Read by os.getenv or set by an operator, so they are not expected to
    # appear as defined symbols; several are deliberately *absent* from the
    # code because they were retired, and the doc says so.
    not_code = {
        "CJ_NETWORK_ENABLED", "CJ_ENVIRONMENT_MODE", "PRODUCTION_CJ_FULFILLMENT_ENABLED",
        "REAL_CJ_FUNDING_ENABLED", "CJ_EGRESS_IP_ATTESTED", "CJ_EGRESS_GROUP",
        "BUSINESS_OS_SUPPLIERS_CJ", "CJ_HOSTED_CREDENTIALS_APPROVED",
        "CJ_MULTI_MERCHANT_SAAS_APPROVED", "CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED",
        "CJ_CONTENT_REDISPLAY_APPROVED",
        # Retired, and named in the doc's own correction note so a grep for it
        # lands somewhere. See the docstring.
        "CJ_EGRESS_CAPACITY_EXHAUSTED",
        # CJ's own vocabulary, not ours.
        "OFF", "PRODUCT", "VARIANT", "STOCK", "ORDER", "LOGISTIC",
    }
    spans = re.findall(r"`([^`\n]+)`", doc)

    cited = {span for span in spans if re.fullmatch(r"[A-Z][A-Z0-9_]{4,}", span)} - not_code
    missing = sorted(token for token in cited if token not in source)
    assert not missing, (
        f"{missing} cited in CJ_PROVIDER_POLICY_CONFIRMATION.md but absent from "
        f"services/business_os/suppliers/. Either the doc invented it or the code "
        f"renamed it; both make the doc unusable as the authority it claims to be."
    )

    # `policy.PARTNER_AGREEMENT_HELD`, `connections.inactivity_forecast` and the
    # like. A bare-token pattern cannot see these -- the backtick is followed by
    # a lowercase module name -- and they are the doc's most load-bearing
    # citations, so the first version of this check silently ignored them.
    members = set()
    for span in spans:
        match = re.fullmatch(r"([a-z_]+)\.([A-Za-z_][A-Za-z0-9_]*)(?:\(\))?", span)
        if match and match.group(1) in modules and match.group(2) != "py":
            members.add((match.group(1), match.group(2)))
    undefined = sorted(
        f"{module}.{attr}"
        for module, attr in members
        if not re.search(rf"^\s*(?:def\s+{re.escape(attr)}\b|{re.escape(attr)}\s*[:=])",
                         modules[module], re.M)
    )
    assert not undefined, (
        f"{undefined} cited in CJ_PROVIDER_POLICY_CONFIRMATION.md but not defined in the "
        f"module the doc names. A reader sent to the wrong file is no better off than one "
        f"sent to a name that does not exist."
    )

    # The check is worthless if it matched nothing, so prove both halves bite.
    assert len(cited) >= 3, f"only matched constants {cited}; the extraction pattern has drifted"
    assert len(members) >= 3, f"only matched members {members}; the extraction pattern has drifted"
