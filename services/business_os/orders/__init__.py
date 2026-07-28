"""Business OS — Section 5: Orders (the ONE canonical order domain).

There is exactly one order state machine in the platform and it already exists,
stable and tested, inside the marketplace vertical:
``services.business_os.marketplace.orders`` (lifecycle) +
``services.business_os.marketplace.refunds`` (refunds / disputes / payout accrual),
every cent riding the shared canonical double-entry ledger
(``services.business_os.ledger.ledger``).

This package is the canonical *domain surface* for that engine — NOT a second order
system. It creates no new table, defines no second ledger, and re-exports the existing
state machine so orders are reachable as a first-class ``Orders`` domain rather than
only through marketplace buyer/seller endpoints. Enabling it is gated behind
``BUSINESS_OS_ORDERS``; the underlying engine remains gated behind
``BUSINESS_OS_MARKETPLACE`` (both must be on for writes to flow), which keeps the
single-source-of-truth guarantee explicit: this module can never run the engine that
marketplace has turned off.

Reuse over duplication: if the order lifecycle ever needs to change, it changes in one
place — the marketplace order module — and every caller (marketplace endpoints and this
canonical Orders surface alike) inherits it.
"""
