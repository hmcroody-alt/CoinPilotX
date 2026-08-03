# Business OS v2 — Tier 2 plan and supersession table

This is the sequencing half of the deep-review adoption. The verdict record
(`V2_VERDICT_RECORD.md`) says what each finding is; the ADRs
(`adr/`) say what the architecture becomes. This document says in what order the
work happens, and — the part that matters most to anyone holding an older
mission document — which parts of the earlier missions still apply.

## The rule that governs conflicts

Where a review section and a section mission overlap, the stricter rule wins.
This is not a tie-break convenience; it is the mechanism that keeps the standing
decision about money and verification true. The review strengthens those rules
in several places and relaxes them in none, and the "stricter wins" rule means
that even if a future reader finds the review's wording looser than a mission's,
the mission's wording is what ships.

Everywhere else the review wins, because the missions were written one surface
at a time and the review is the first document to look at all of them together.

## Supersession table

Status values are **Stands** (the mission section is unchanged and remains the
specification), **Amended** (the mission section remains in force with the
review's additions layered on top), and **Superseded** (the review's definition
replaces the mission's).

| Mission | Section | Status | Notes |
| --- | --- | --- | --- |
| Business Hub rebuild | Light hub screen, live card state | **Superseded** | Reverted before this review. `HUB_LIVE_CARDS` stays false. The review's expanded navigation maps behind the dark cards. |
| Business Hub rebuild | Per-card data bindings (`api/businessHub.ts`, `core/hubBindings.ts`) | **Stands** | Wiring was never the failure. Retained and still tested. |
| Business Hub revert | Everything | **Stands** | Not reopened. The dark eleven-card screen is the front door. |
| Store rebuild | `deriveStatus` store-open derivation | **Superseded** | Replaced by the readiness ladder (ADR-0003) once ADR-0001 makes empty distinguishable from paused. |
| Store rebuild | Per-section loading with independent retry | **Stands** | The refusal to let `Promise.allSettled` collapse a failed orders call into an empty array is the reference behaviour for ADR-0003. |
| Store rebuild | `linkRow` two-tile composition | **Stands** | This is the correct pattern; ADR-0002 generalises it rather than replacing it. |
| Marketplace | Buying-mode location strip | **Stands** | Already honest — "Location not set — showing all listings". |
| Marketplace | "Just listed near you" section header | **Superseded** | Contradicts the strip directly above it. Becomes "Just listed" until a location exists. |
| Marketplace | `moreGrid` four-across quick links | **Superseded** | Tier 0.1. Replaced by ADR-0002's two-tile row, which cannot express four-across. |
| Marketplace | Listing entity model | **Amended** | ADR-0001 splits product from store listing from marketplace listing. |
| Advertising (slices 1–7, stage 2) | All money rules, spend caps, wallet handling, confirmation before committing money | **Stands** | Explicitly untouched by ADR-0005. |
| Advertising | Flat campaign object | **Amended** | ADR-0005 adds ad group and ad beneath it, plus the edit classification. Migration is lossless. |
| Advertising | `toolGrid` four-across quick links | **Superseded** | Tier 0.1, same mechanism as Marketplace. |
| Advertising | `EXPO_PUBLIC_ADS_POST_MODE` gating | **Amended** | Remains a build-time flag for unfinished surfaces. Stops being read as an entitlement (ADR-0004). |
| Advertising | Ad account strip rendering the raw account ID | **Superseded** | "Ad account 8" is de-emphasised; a support reference replaces it where a reference is needed. |
| Orders | Two-sided orders surface, own header with `headerShown: false` | **Stands** | Correct today; joins ADR-0002's shell when the shell exists. |
| Orders | `ordersAwaitingSeller` derivation ownership | **Stands** | Live reader in `OrdersManagerScreen`. |
| Orders | Order line item references a listing | **Amended** | ADR-0001: an order references the product and snapshots the listing. Financial records never change when a listing is edited. |
| Messages | Inbox design — context chips, expiry banner, saved replies, away mode | **Amended** | Applies to the Commerce Inbox, not the social messenger. The social tab is already clean. |
| Messages | Client-side chip resolution (`rowMatchesFilter` on `row.chip?.kind`) | **Superseded** | Replaced by the server-side `conversation_domain` and the commerce-object join. |
| Messages | Unpartitioned conversation query | **Superseded** | Tier 0.4. The Commerce Inbox queries the commerce partition. |
| Payments | Money source rules, no client-side money derivation, source always shown | **Stands** | Strictest rules in the system. Inherited by ADR-0004 and ADR-0007 rather than amended by them. |
| Payments | Screen shell — own gradient header above the stack header | **Superseded** | Tier 0.2. One shell, one header, one back action. |
| Payments | Error presentation — hero dash plus a second error card | **Superseded** | Tier 0.2. One consolidated error per failure, with a support reference. |
| Payments | "Display problem, not a change to your money" copy | **Stands** | Kept verbatim. Said once. |
| Insights | Export blocked while showing cached figures | **Stands** | Better than the review asks for. Becomes the general rule in ADR-0003 for exports, shares and prints. |
| Insights | Export disabled state and its accessibility hint | **Stands** | The review's finding here is dismissed with evidence in the verdict record. |
| Insights | `insightsErrorMessage` three-way cause split | **Amended** | Extended to ADR-0003's five causes: offline, authentication, entitlement, service unavailable, unexpected. |
| Insights | Hard-coded `unreadCount={0}` in the header | **Superseded** | Reads the scoped count once ADR-0002 owns the header. |
| Events | Hosted-events manager and its own header | **Stands** | Joins ADR-0002's shell on the general schedule. |
| Verification | Trust content — documents are not inspected, stored or reviewed by native | **Stands** | Good product. Kept and said more clearly. |
| Verification | Unconditional "Start verification request" primary action | **Superseded** | Tier 0.3. Status-aware: Approved offers View, Update, Add another. |
| Verification | Developer-language copy (API paths, "server-authoritative") | **Superseded** | Tier 0.3, widened to every surface using this vocabulary. |
| Verification | `Request #{requestId}` raw ID | **Superseded** | Same class as "Ad account 8". The review missed this one. |
| Verification | Dark `Panel` / `colors` shell | **Stands** | The review's "third shell" claim is false. No migration off a third theme, because there isn't one. |
| Shared foundation | Nine light theme modules and their component families | **Superseded** | ADR-0002. One shell, two appearances. |
| Shared foundation | `core/unreadCounts.ts` single snapshot, bell and message counts separated | **Stands** | The "single source" half of the badge finding is already satisfied. |
| Shared foundation | Commerce vs social message count separation | **Amended** | Falls out of ADR-0004's `conversation_domain` for free. |
| Wiring mission | S1–S9 scenarios | **Amended** | Retained as intermediate gates. The review's two end-to-end chains become the acceptance test above them. |

## Phase sequence

**Phase 0 — audit, do not rebuild.** Several of the review's Phase 0 findings
are already implemented, and in a few cases implemented better than specified.
The Insights export policy is the clearest example, and `deriveStatus`'s own
comment is the clearest example of code that was honest about a gap rather than
hiding it. Rebuilding those destroys working code and the comments that explain
why it is the way it is. Phase 0 produces a written audit result per item —
present and correct, present and insufficient, or absent — and nothing else.

**Phase 1 — Tier 0.4, commerce and social separation.** Runs first because
everything else in the messaging area depends on it: the scoped badge counts,
the Commerce Inbox design work, and the search partitioning all need
`conversation_domain` to exist. The backfill is the first job built to ADR-0007
and is a deliberate forcing function for that standard.

**Phases 2 through 8 — the section surfaces.** Each layers the review's
corresponding section on top of the existing section mission, under the
stricter-rule-wins rule. Sequenced so that ADR-0002's shell has landed for
Payments (Tier 0.2) before the remaining surfaces migrate onto it, and so that
ADR-0003's state enum exists before the em-dash retirement begins.

**Phases 9 through 12 — the remainder of the review's build-out**, unchanged in
content from the review.

**Phase 13 and the risk platform — roadmap.** Specs preserved intact. Only the
risk hooks are wired now, so the platform has attachment points when it arrives.
UNDX integration is out of scope for this mission entirely.

**Phase 14 is not a phase.** Its items become permanent gates at every phase
boundary, starting with Tier 0. The font-scale gate is the first of them, and it
is automated rather than written down, because the identical lesson was already
recorded in prose in the hub revert post-mortem and did not prevent the same
defect appearing on two more screens. A prose reminder is what failed. An
assertion is what replaces it.

## Tier 0 ordering inside this plan

Tier 0 does not wait for the phases; it is the entry condition for them. The
order within Tier 0 is set by dependency rather than by severity.

0.1's shared tile and row primitives come first, because they are part of
ADR-0002's shell and because 0.2 needs that shell to repair Payments properly
rather than by hiding one header.

0.2 follows immediately, as the shell's first consumer and smallest complete
case.

0.4 runs as Phase 1 in parallel with the shell work, since it is a backend and
data change with no dependency on the shell.

0.3 follows the shell, because the Verification repair is a rewrite of the same
render logic that lands status-awareness, role-awareness (ADR-0006) and the copy
fix, and doing those in three passes means touching it three times.

0.5 is split by dependency: the "Just listed" header, the raw ID removal and the
badge scoping land immediately; the readiness ladder waits for ADR-0001; the
em-dash retirement waits for ADR-0003.

## What is deliberately not in this plan

Named owners. The ADRs carry surface owners because the mission ran without a
roster, and substituting real names is the product owner's first follow-up.

A date estimate for ADR-0002. Consolidating nine design systems is the largest
item here and estimating it before the reference theme is chosen would produce a
number with nothing behind it.

Device evidence. Every gate in this plan is automated for a reason, but
automation does not replace looking at the thing. The device checklist in
`BUSINESS_HUB_REVERT.md` remains unexecuted and is joined by the before-and-after
screenshot requirement for each Tier 0 item, including max-font-scale evidence.
Both are the product owner's to run.
