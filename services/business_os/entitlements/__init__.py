"""Entitlements bounded context (Stage 2).

One server-authoritative entitlement service across Premium, Business,
Marketplace, Advertising, Creator, and crypto capabilities. Additive
``business_os_ent_*`` tables only; legacy entitlement tables are never mutated
and remain the fallback via the compatibility facade until canonical mode is
proven. Everything is gated behind the ``BUSINESS_OS_ENTITLEMENTS`` flag
(off | shadow | canonical); flag-off is zero behaviour change.
"""

from .schema import (  # noqa: F401
    ensure_schema,
    seed_catalog,
    ensure_ready,
)
from .service import (  # noqa: F401
    EntitlementError,
    has_entitlement,
    get_entitlements,
    get_entitlement_limits,
    explain_entitlement,
    grant_entitlement,
    revoke_entitlement,
    suspend_entitlement,
    sync_subscription_entitlements,
    reconcile_entitlements,
    get_grant,
)
from .facade import (  # noqa: F401
    check,
    shadow_compare,
    get_mode,
    MODE_OFF,
    MODE_SHADOW,
    MODE_CANONICAL,
)
from .usage import (  # noqa: F401
    QuotaExceeded,
    check_and_consume,
    get_usage,
)
from .providers import (  # noqa: F401
    ProviderError,
    ProviderNotImplemented,
    map_stripe_subscription,
    apply_stripe_subscription,
    upsert_provider_subscription,
)
from .premium import (  # noqa: F401
    PREMIUM_ACCESS,
    PREMIUM_CAPABILITIES,
    PREMIUM_PLAN_KEYS,
    FOUNDER_PLAN_KEY,
    FOUNDER_PRICE_CENTS,
    resolve as resolve_premium,
    is_premium,
    parity as premium_parity,
    parity_report as premium_parity_report,
    founder_allocation_status,
    founder_allocation_available,
)
