"""Fail-closed rollout policy. These switches never authorize real funding."""

import os

from services.business_os.suppliers.errors import SupplierError


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "on", "yes"}


def require_enabled() -> None:
    if not enabled("BUSINESS_OS_SUPPLIERS_CJ"):
        raise SupplierError("disabled", http_status=404)


def _sandbox_mode() -> bool:
    return os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() == "SANDBOX"


def multi_merchant_saas_approved() -> bool:
    """Provider-wide approval to run hosted CJ for many merchants. UNRESOLVED.

    `CJ_HOSTED_CREDENTIALS_APPROVED` is the original name for exactly this claim
    and stays a synonym, so existing deployments keep their meaning.
    """
    return enabled("CJ_MULTI_MERCHANT_SAAS_APPROVED") or enabled("CJ_HOSTED_CREDENTIALS_APPROVED")


def single_merchant_sandbox_authorized() -> bool:
    """One named merchant, using their OWN CJ credential, against sandbox.

    Scoped to sandbox deliberately. A narrow authorization that survived into a
    production environment would stop being narrow: "one merchant" is a claim
    about who is testing, not about what the blast radius is, so the environment
    has to carry its own half of the condition.
    """
    return enabled("CJ_SINGLE_MERCHANT_SANDBOX_ALLOWED") and _sandbox_mode()


def content_redisplay_approved() -> bool:
    """Rights to redisplay CJ media/copy publicly. UNRESOLVED.

    Governs PUBLICATION, not fetching, so it is not part of require_network():
    importing to a private draft redisplays nothing. Recorded here so the answer
    is asked for at the point it actually matters instead of being assumed.
    """
    return enabled("CJ_CONTENT_REDISPLAY_APPROVED")


def provider_authorized() -> bool:
    return multi_merchant_saas_approved() or single_merchant_sandbox_authorized()


def require_network() -> None:
    # Provider approval includes delegated credentials and a compliant fixed
    # egress/account plan. A deployment flag is not evidence of that approval.
    #
    # These used to be one flag, and that conflation had a cost: the narrow,
    # defensible case -- a single authorized merchant putting their own CJ key
    # against sandbox -- was blocked behind the unresolved platform-wide SaaS
    # question, and the only way to unblock it was to assert a provider-wide
    # approval nobody has. Splitting them lets the narrow case proceed on its own
    # evidence while the broad claim stays honestly OFF.
    if not provider_authorized():
        raise SupplierError("provider_approval_required", http_status=503)
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
        # Deliberately still the BROAD claim only. Reporting the narrow
        # single-merchant authorization here would read as provider-wide
        # approval in every dashboard that shows it.
        "provider_approval_recorded": multi_merchant_saas_approved(),
        "single_merchant_sandbox_authorized": single_merchant_sandbox_authorized(),
        "multi_merchant_saas_approved": multi_merchant_saas_approved(),
        "content_redisplay_approved": content_redisplay_approved(),
        "network_permitted": provider_authorized() and enabled("CJ_NETWORK_ENABLED"),
        "production_fulfillment_enabled": False,
        "real_funding_enabled": False,
    }
