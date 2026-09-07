"""Fail-closed rollout policy. These switches never authorize real funding."""

import os

from services.business_os.suppliers.errors import SupplierError


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "on", "yes"}


def require_enabled() -> None:
    if not enabled("BUSINESS_OS_SUPPLIERS_CJ"):
        raise SupplierError("disabled", http_status=404)


def require_network() -> None:
    # Provider approval includes delegated credentials and a compliant fixed
    # egress/account plan. A deployment flag is not evidence of that approval.
    if not enabled("CJ_HOSTED_CREDENTIALS_APPROVED"):
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
        "configuration_valid": os.getenv("CJ_ENVIRONMENT_MODE", "SANDBOX").strip().upper() == "SANDBOX"
        and not enabled("PRODUCTION_CJ_FULFILLMENT_ENABLED"),
        "network_enabled": enabled("CJ_NETWORK_ENABLED"),
        "provider_approval_recorded": enabled("CJ_HOSTED_CREDENTIALS_APPROVED"),
        "production_fulfillment_enabled": False,
        "real_funding_enabled": False,
    }
