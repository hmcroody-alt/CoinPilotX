"""Business OS — Attribution vertical (Stage 6, Part 1).

An **informational-only** analytics surface that answers one question: *which
touchpoints deserve credit for a conversion?* It records an append-only log of
touchpoints (impressions/clicks/visits/engagements) and conversions (purchases,
subscriptions, signups), then computes fractional per-touchpoint **credit** under
several standard multi-touch models (last-touch, first-touch, linear,
position-based).

Hard boundaries (mirrors the crypto vertical's discipline):

* **No money moves.** Attribution distributes *credit* — a reporting quantity — not
  funds. It never posts to the ledger, never bills an advertiser, never pays a
  creator. It is a lens over events that already happened.
* **Append-only truth, projected credit.** ``business_os_attr_touchpoints`` and
  ``business_os_attr_conversions`` are immutable logs; ``business_os_attr_credits``
  is a *projection* recomputed deterministically from them and always rebuildable.
* **Integer cents only.** A conversion's value is integer cents; credit is a
  remainder-safe integer split that sums back to the conversion value exactly — no
  floats, no lost pennies.
* **Idempotent ingest + recompute.** A UNIQUE ``(source, external_ref)`` makes a
  replayed touchpoint/conversion a no-op; credit is keyed by
  ``(conversion_id, model, touchpoint_id)`` so a recompute is exactly-once.
* **Legacy untouched, flag-gated.** Builds a new canonical ``business_os_attr_*``
  surface beside existing analytics; reads/writes are gated behind
  ``BUSINESS_OS_ATTRIBUTION`` in the service layer. Creating empty tables changes
  zero runtime behaviour.
"""
