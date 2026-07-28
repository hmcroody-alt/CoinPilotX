"""Business OS — Section 6: Messages (the ONE canonical message domain).

There is exactly one message store in the platform and it already exists, stable and
in production: the canonical ``pulse_conversations`` / ``pulse_conversation_participants``
/ ``pulse_messages`` engine (direct messages, groups, rooms, receipts, reactions,
reports). This package is the canonical *business messaging domain surface* over that
same engine — NOT a second message system.

Reuse over duplication:

  * it creates NO ``business_os_messages`` table and NO second conversation store;
    a "business inbox" is simply a canonical ``pulse_conversations`` row tagged with an
    additive ``business_id`` column and ``conversation_type='business'``;
  * every message a business thread carries is a row in the canonical ``pulse_messages``
    table with identical send semantics (participant scoping, client-message-id
    idempotency, unread counters, ``last_message_at`` bump);
  * identity (customer vs. business staff) is always the authenticated caller, never the
    request body; who may act *as the business* is resolved against S1 canonical
    membership/RBAC (``business.service._effective_role``), never re-modeled here.

Enabling it is gated behind ``BUSINESS_OS_MESSAGES``. If the message lifecycle ever
changes it changes in one place — the canonical pulse message tables — and both the
native DM surface and this business surface inherit it.
"""
