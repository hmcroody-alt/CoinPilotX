"""PulseAnalytics — a read layer over events this platform already collects.

There is no ingest here, and there is no new event table. Production already
holds 111 of those; the census in
``docs/analytics/PULSEANALYTICS_EVENT_SOURCE_INVENTORY.md`` found 62 of them
empty, 14 duplicating each other, and one holding 3,833 rows that nothing reads.
A 112th would not have been an analytics pipeline, it would have been a 112th.

What was actually missing is a way to ask the events a question. This package is
that, and only that.

The envelope is borrowed, not invented
--------------------------------------

``services/commerce_discovery`` already defines a canonical commerce event —
``event_id``, ``subject_ref``, ``surface``, ``listing_id``, ``seller_user_id``,
``event_at``, ``dedup_key`` — and a funnel vocabulary ordered
``click → product_view → save → add_to_cart → checkout_started → purchase``.
Both are imported from there. Redefining either would create a second answer to
"what is a commerce event", and two answers is the condition this package exists
to end.

Where the outcome number comes from
-----------------------------------

Not from the event log. ``commerce_purchase`` is a *client-reported* step, and
this codebase has already been burned by trusting the client's idea of a sale:
Business OS once reported 32 orders to a seller when not one of the 32 was paid,
and 43 live listings when 28 were drafts no buyer could see. The fix was
``business_os/marketplace/seller_metrics.py``, which owns ``is_live_listing``
and ``confirmed_order_state``. This package calls it rather than re-deriving
them, so there is exactly one definition of a sale on the platform.

That leaves the two numbers free to disagree — client purchases against
confirmed orders — and :mod:`.funnel` reports the gap instead of picking a
winner. The gap is a measurement, not an error: it is the size of the lie the
old dashboard was telling.
"""

from __future__ import annotations

from . import funnel, read

__all__ = ["funnel", "read"]
