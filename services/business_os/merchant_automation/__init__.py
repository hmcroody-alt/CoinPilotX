"""Merchant automation vertical (Business OS, Stage 6).

Informational-only, deterministic **rule engine** over merchant signals. A merchant
declares rules (e.g. ``stock_level <= 5`` -> a "reorder" suggestion); the engine
evaluates each active rule against the latest value in an append-only signal log and
produces a rebuildable projection of **proposed actions**.

Hard boundary — NO money moves, NO action is ever taken. A proposal is a
*suggestion*: a reporting quantity, not an instruction. Nothing here reorders stock,
places an order, sends a notification, adjusts a price, or bills anyone. It is a lens
over merchant state that surfaces what a human (or a separately-reviewed integration)
*could* choose to do.

Gated behind ``BUSINESS_OS_MERCHANT_AUTOMATION``. Follows the strangler pattern of the
attribution / recommendations / crypto verticals: canonical ``business_os_merchant_*``
tables, append-only truth + rebuildable projection, idempotent ingest, dark-404
gating, curated error codes. Nothing legacy is read or written.
"""
