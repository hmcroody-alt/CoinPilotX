"""PulseSoc Business OS — canonical Business domain (Section 1: Business HQ).

This is the *source of truth* for business identity on PulseSoc: legal/display
identity, brand, contact, organization/locations, team + RBAC, policies, and an
append-only business timeline. Every other Business OS module (Store, Marketplace,
Advertising, Orders, Messages, Insights, Payments, Events, Verification) is meant
to reference the canonical business row owned here rather than restate business
information locally.

Strangler-pattern, exactly like the marketplace/advertising slices:

  * additive ``business_os_business_*`` tables — never mutates a legacy table;
  * the whole surface is DARK (503 in the service, 404 at the route) unless the
    ``BUSINESS_OS_BUSINESS`` rollout flag is explicitly enabled, so nothing changes
    in any environment that hasn't opted in;
  * all decision logic lives in importable pure modules (``service`` / ``api``) so
    every branch is unit-testable without importing Flask or ``bot.py``.
"""
