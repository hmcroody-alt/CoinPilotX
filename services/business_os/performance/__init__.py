"""Business OS — Performance (Stage 6, sixth vertical).

An **informational-only**, **deterministic** performance-summary projection. An org
records *metric samples* (a numeric ``value`` for a ``metric_key`` at some ``captured_at``,
optionally bucketed by a ``window`` label) and optionally declares *targets* (a warn /
breach threshold for a metric with a direction — lower-is-better or higher-is-better). The
engine computes a rebuildable per-(org, metric, window) **summary** — count, min, max,
mean, p50, p95 — and labels each summary against the newest active target as
``ok`` / ``warn`` / ``breach`` (or ``none`` when no target applies).

Hard boundary: **nothing renders and nothing acts.** A summary is a reporting label — a
quantity describing what the samples *say* — not an alert, a page, a scaling decision, or
any side effect. Wiring a ``breach`` into a real alert or autoscaler is a separate,
separately-reviewed integration on top of the product's real observability path.

Strangler pattern: a new canonical ``business_os_perf_*`` surface built beside whatever
metrics/monitoring exists; nothing legacy is read or written. Gated behind
``BUSINESS_OS_PERFORMANCE``. Mirrors the attribution / recommendations /
merchant-automation / creator-commerce / governed-UNDX / localization modules' discipline
exactly: append-only truth + rebuildable projection, idempotent ingest, dark-404 gating,
curated error codes.
"""
