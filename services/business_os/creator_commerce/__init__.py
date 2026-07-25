"""Creator commerce vertical (Business OS, Stage 6).

Informational-only, deterministic **earnings & tier projection** over creator support.
A creator declares *offerings* (a named support option — e.g. a membership tier, a tip
jar, a one-off product) and an append-only log records *contribution* facts (a supporter
contributed an amount toward an offering). The engine computes a rebuildable per-creator
projection: summed support per offering and per supporter, a deterministic supporter
*tier* assignment by cumulative-support threshold, and a ranked top-supporter list.

Hard boundary — NO money moves, NO payout is made, NO one is charged. Earnings here are
a *reporting quantity* summarizing contributions that already happened elsewhere; a tier
is a *label*, not an entitlement grant. Nothing posts to the ledger, pays out, bills, or
unlocks anything.

Gated behind ``BUSINESS_OS_CREATOR_COMMERCE``. Follows the strangler pattern of the
attribution / recommendations / merchant-automation verticals: canonical
``business_os_creator_*`` tables, append-only truth + rebuildable projection, idempotent
ingest, dark-404 gating, curated error codes. Nothing legacy is read or written.
"""
