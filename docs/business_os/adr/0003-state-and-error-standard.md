# ADR-0003 — Unified state and error standard

Status: Accepted
Owner: Business OS mobile
Date: 2026-08-01
Amends: every section mission's per-screen empty and error copy

## Context

The em dash is currently doing at least four jobs. It appears as a placeholder
across roughly seventy-eight screen files and forty API files, and depending on
the caller it means the value is zero, the value has not loaded yet, the value
failed to load, or the value does not apply. On the Payments screen it means
"your balance failed to load" and is rendered in the position where the balance
would be, which is the single worst place in the product for an ambiguous
character.

Where screens do distinguish causes, they distinguish different ones.
`insightsErrorMessage` in `api/insightsDashboard.ts:538` recognises 401 and 503
and collapses everything else into "{subject} didn't load." Store's loader
distinguishes per-section outcomes and an offline-from-cache state. Payments has
its own error card. Nothing shares a vocabulary, so a seller who learns what one
screen's failure looks like learns nothing transferable.

Some of this is already good and must not be flattened. Insights blocks CSV
export while showing cached figures, with a comment explaining that an exported
file outlives the banner that qualified it and gets read months later as a
record. Store's loader deliberately avoids `Promise.allSettled` collapsing a
failed orders call into an empty array, because that would make "orders failed"
indistinguishable from "no orders yet". Those are the standard, discovered
independently on two screens; this ADR generalises them rather than replacing
them.

## Decision

A screen or a section is in exactly one state, drawn from a closed enum, and
each state has one presentation and one vocabulary.

**Loading** — the request is in flight and there is nothing cached to show.

**Ready** — real data, rendered.

**Ready from cache** — real data, but saved earlier. Carries the age of the
data, and blocks any action that would produce an artefact outliving the caveat.
Insights' export block is the reference implementation of that rule and it
generalises: exports, shares and prints are unavailable from cache.

**Zero** — the request succeeded and the true answer is nothing. This is a
success state and never renders as a dash, an error, or a spinner. It carries
the action that would make it non-zero.

**Not configured** — the feature exists but this seller has not set it up. This
is what a store with no listings is, and it is why `deriveStatus` returning
`{open: true}` for zero rows is wrong: an empty store is not an open store, it
is an unconfigured one.

**Restricted** — the seller is not entitled to see this, per ADR-0004. Names
what would grant access, without implying the data is missing.

**Unavailable** — the request failed. Carries a cause and a retry.

**No activity** — distinct from Zero, for time-windowed views. There are no
orders *in this period*; there may be plenty outside it. Collapsing this into
Zero is how a seller concludes their business has stopped.

Failure causes are a closed set too: offline, authentication, entitlement,
service unavailable, and unexpected. Each has one sentence, written once, and
each carries a retry except entitlement, which carries whatever would resolve
it. `insightsErrorMessage`'s three-way split becomes this five-way one.

Every state's reason is exposed to assistive technology. Insights already does
this correctly — its export pill carries `accessibilityHint={exportBlockedReason
|| "Shares a CSV of the figures on screen"}` alongside `accessibilityState={{
disabled: exportDisabled }}` — and that pattern is the requirement, not an
example of good practice on one screen.

The em dash is retired as a state signal. It survives only as a genuine
typographic dash inside prose.

## Consequences

The retirement is a large mechanical change touching over a hundred files, and
it must land *after* the enum exists, not alongside it, so that each replacement
is a decision about which state the site is in rather than a search and replace.

Several screens will gain states they do not currently have, which is the
point — a store that cannot express "not configured" will keep claiming to be
open.

The store readiness ladder from the review (Draft, Needs setup, Ready, Open,
Paused, Restricted, Suspended) is a domain-specific refinement of this enum, not
a competing one: Draft and Needs setup are Not configured, Restricted and
Suspended are Restricted, and Ready, Open and Paused are Ready with a
store-scoped status.

## Open question

Whether the "—" replacement lands as one large auditable change or is absorbed
screen-by-screen as each screen is touched for other reasons. One pass is
reviewable in a single sitting and leaves nothing half-done; incremental leaves
the product inconsistent for longer but never presents a hundred-file diff to a
reviewer who cannot hold it in their head.
