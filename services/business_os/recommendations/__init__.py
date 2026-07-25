"""Business OS — Recommendations vertical (Stage 6).

Informational-only, deterministic recommendation intelligence. It records an
append-only log of implicit-feedback *interactions* (view / click / like /
purchase / dismiss) between users and a catalog of recommendable *items*, and
computes a rebuildable, ranked *recommendation* projection per user under four
transparent models (popularity / content-based / collaborative / hybrid).

Discipline mirrors the crypto and attribution modules exactly:

* **Append-only truth + rebuildable projection.** Items and interactions are
  immutable logs; recommendations are a deterministic projection recomputed
  from them (recompute = replace). The logs are the authority.
* **Deterministic.** Every model produces a stable ranked list with an explicit
  tie-break (score desc, then item_id asc) — no randomness, no float drift in
  ordering.
* **Idempotent ingest.** UNIQUE (source, external_ref) on the interaction log
  makes a replayed feed event a no-op (NULL ref — manual entries — is exempt).
* **Informational only.** A recommendation is a suggestion, not an action.
  Nothing here posts to the ledger, sends a notification, or mutates a feed. It
  is a lens over engagement that already happened elsewhere.
* **Flag-gated.** The whole surface is dark (404) unless
  ``BUSINESS_OS_RECOMMENDATIONS`` is enabled. No legacy table is read or written.
"""
