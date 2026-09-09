"""Fail-closed rollout policy. These switches never authorize real funding.

CJ Developer Support confirmed the standard API model in writing on 2026-09-09
(recorded in `docs/cj/CJ_PROVIDER_POLICY_CONFIRMATION.md`). A merchant generates
an API key in their own CJ account, hands it to PulseSoc, and PulseSoc may hold
it server-side and call CJ for that merchant. No separate provider approval
gates that, and the same model repeats independently per merchant.

Those are provider facts, so they are constants here rather than environment
switches. A flag implies a deployment may choose otherwise; a merchant's right
to use their own credential is not a thing a deployment gets to toggle, and a
switch would only invite someone to answer the question wrongly later. What
stays an environment switch is what genuinely varies per deployment: whether
this environment may reach CJ at all, and whether it may spend money.

What is NOT confirmed, and what the constants below deliberately do not claim:
any partner, platform, white-label or OEM agreement; a master credential
reaching other merchants' accounts; an IP whitelist exemption. The real limit on
multi-merchant scale is no longer a policy question at all -- it is CJ's
infrastructure ceiling of three accounts and ten business calls per second per
outbound IP, enforced in `quota.py` and attested by `egress_ip_attested()`.
"""

import os

from services.business_os.suppliers.errors import SupplierError

# Confirmed by the provider; not deployment-tunable.
STANDARD_API_MODEL_CONFIRMED = True
MERCHANT_OWNED_CREDENTIAL_CUSTODY_CONFIRMED = True
MULTI_MERCHANT_STANDARD_MODEL_CONFIRMED = True
PARTNER_AGREEMENT_REQUIRED = False
PARTNER_AGREEMENT_HELD = False  # No such agreement exists. Never claim one in UI.

# CJ's hard infrastructure ceilings, per outbound IP. No whitelist exemption.
MAX_CJ_ACCOUNTS_PER_EGRESS_IP = 3
MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP = 10


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "on", "yes"}


def require_enabled() -> None:
    if not enabled("BUSINESS_OS_SUPPLIERS_CJ"):
        raise SupplierError("disabled", http_status=404)


def _sandbox_mode() -> bool:
    return os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() == "SANDBOX"


def egress_ip_attested() -> bool:
    """Has an operator attested that `CJ_EGRESS_GROUP` really is one stable IP?

    `quota.py` enforces CJ's three-accounts-per-IP ceiling by counting rows per
    egress group -- but an egress group is a label a human types, and CJ counts
    actual outbound IPs. Those agree only if someone checked. Railway's outbound
    address is not documented as static or dedicated anywhere in this repository,
    so on an unattested deployment the count is bookkeeping about a fiction: a
    second region, a rotated address or a reused label all keep the counter
    happy while CJ sees a fourth account on an IP.

    This flag is the difference between "we enforce the limit" and "we enforce
    the limit against something we have verified". It gates multi-merchant
    scale, not the single-merchant case, because one account cannot exceed a
    three-account ceiling however the IPs fall.
    """
    return enabled("CJ_EGRESS_IP_ATTESTED")


def multi_merchant_scale_blocker() -> str | None:
    """Why hosted multi-merchant CJ is not yet safe to scale, or None.

    Deliberately no longer a policy answer. CJ confirmed the multi-merchant
    standard model needs no approval, so the honest blocker is the one that
    survives that confirmation: we cannot yet prove which outbound IP our calls
    leave from, and CJ's ceiling is expressed per IP.
    """
    if not egress_ip_attested():
        return "BLOCKED_BY_EGRESS_ARCHITECTURE"
    return None


def content_usage_authorized() -> bool:
    """CJ confirmed: store and display CJ media/copy to sell those products.

    Confirmed for merchant-facing tooling and for selling the CJ products the
    content belongs to -- no attribution, no per-supplier permission, no fee.
    Scoped on purpose: this is not a licence to redistribute CJ media for
    anything unrelated to selling the product it came from, so the name says
    "usage" rather than "redisplay approved" and callers stay honest about it.

    Confirmation of rights is not a reason to trust the bytes. Provider content
    remains untrusted input and must still be sanitized.
    """
    return True


def require_network() -> None:
    # There used to be a provider-approval gate here, and CJ has now answered
    # the question it was standing in for: a merchant's own API key, used from
    # our hosted backend with their consent, is the standard model and needs no
    # separate approval. Keeping the gate would have meant asserting a
    # provider-wide approval nobody has in order to do the ordinary thing.
    #
    # What remains is the rollout switch, which is a genuine per-deployment
    # fact: production leaves CJ_NETWORK_ENABLED unset and therefore stays dark
    # regardless of anything above.
    if not enabled("CJ_NETWORK_ENABLED"):
        raise SupplierError("provider_network_disabled", http_status=503)


def require_sandbox(payload: dict) -> None:
    if os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() != "SANDBOX":
        raise SupplierError("production_fulfillment_disabled", http_status=409)
    if enabled("PRODUCTION_CJ_FULFILLMENT_ENABLED"):
        raise SupplierError("production_fulfillment_disabled", http_status=409)
    if not isinstance(payload, dict) or type(payload.get("isSandbox")) is not int or payload["isSandbox"] != 1:
        raise SupplierError("sandbox_flag_required", http_status=400)


def require_funding_disabled() -> None:
    raise SupplierError("funding_not_ready", http_status=409)


def safe_status() -> dict:
    return {
        "enabled": enabled("BUSINESS_OS_SUPPLIERS_CJ"),
        "environment": "SANDBOX",
        "configuration_valid": _sandbox_mode() and not enabled("PRODUCTION_CJ_FULFILLMENT_ENABLED"),
        "network_enabled": enabled("CJ_NETWORK_ENABLED"),
        "standard_api_model_confirmed": STANDARD_API_MODEL_CONFIRMED,
        # Reported so no dashboard can quietly grow a partner badge out of a
        # technical confirmation. CJ confirmed how the API may be used; that is
        # not an agreement, and this field exists to keep the two apart.
        "partner_agreement_held": PARTNER_AGREEMENT_HELD,
        "content_usage_authorized": content_usage_authorized(),
        "max_cj_accounts_per_egress_ip": MAX_CJ_ACCOUNTS_PER_EGRESS_IP,
        "max_cj_calls_per_second_per_egress_ip": MAX_CJ_BUSINESS_CALLS_PER_SECOND_PER_EGRESS_IP,
        "egress_ip_attested": egress_ip_attested(),
        "multi_merchant_scale_blocker": multi_merchant_scale_blocker(),
        "network_permitted": enabled("CJ_NETWORK_ENABLED"),
        "production_fulfillment_enabled": False,
        "real_funding_enabled": False,
    }
