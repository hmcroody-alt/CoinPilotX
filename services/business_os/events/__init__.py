"""Business OS — Section 9: Events (the ONE canonical events / ticketing domain).

There is no pre-existing "gatherings with tickets" store on the platform (the many
``*_events`` tables in bot.py are analytics/telemetry logs, and ``support_tickets`` is the
help desk — neither models a business hosting a ticketed event). This package is that
canonical domain: a business creates an event, defines ticket types, publishes it, and
sells tickets; attendees hold tickets and get checked in.

Reuse over duplication:

  * identity + who-may-manage a business's events is resolved against S1 canonical
    membership/RBAC (``business.service._effective_role``) — never re-modeled here;
  * money for PAID tickets never touches a second payment system: every purchase,
    settlement, and refund is a double-entry against the ONE canonical ledger
    (``business_os.ledger.ledger``), and the platform fee split reuses the marketplace
    order engine's own ``_fee_split`` / ``DEFAULT_FEE_BPS`` — the same take-rate math the
    rest of commerce uses. Free tickets skip the ledger entirely;
  * the events/ticket_types/tickets tables are genuinely new (no canonical store to reuse),
    additive, and never mutate a legacy table.

Enabling it is gated behind ``BUSINESS_OS_EVENTS``. When off the surface is dark.
"""
