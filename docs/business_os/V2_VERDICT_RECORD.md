# Business OS v2 — Verdict record

The deep review of eighteen production screenshots ("PulseSoc Business OS — Deep
Review and Corrected Expansion") has been accepted by the product owner. This
document is the triage: every finding in that review is classified against the
missions that produced the build, the review's own diagnosis is checked against
the code, and the corrections are recorded where the review was wrong or too
narrowly scoped.

This is not a restatement of the review. The review is incorporated by
reference and remains the specification for what the Business OS should become.
What this record adds is the verdict — whether each finding means the build
disobeyed a spec it had, or the spec never said, or the spec was followed and
the review simply proposes something better. Those three answers imply three
different kinds of work, and conflating them is how a review turns into an
unbounded rewrite.

## The standing decisions this record is written under

Four decisions override the review wherever they conflict, and every verdict
below was reached inside them.

The Business Hub stays the original dark eleven-card screen. The revert
(`BUSINESS_HUB_REVERT.md`) is not reopened by this review. Where the review
describes an expanded Business navigation, that expansion maps onto and behind
the existing cards; it does not replace the hub, and no card gains a live state
line as part of this work.

Settings stays static. No state line, no redesign, no exception.

Every money and verification rule from the Payments, Advertising, Orders and
Verification missions remains in force. The review strengthens those rules in
several places and relaxes them in none; where the review appears more
permissive than an existing mission, the existing mission wins.

The review's Phase 13 (UNDX agency) and any UNDX integration is out of scope
for this mission and is recorded as roadmap only.

## How to read a verdict

DEVIATION means the build violated a rule an existing mission had already
written down. These are defects. They are fixed against the original spec, and
the fact that they shipped is itself worth a note, because it means a gate
failed somewhere.

SPEC GAP means the missions under-specified the area and the build's behaviour
was a reasonable reading of an incomplete instruction. The review's definition
is adopted as the new spec. Nobody violated anything; the specification grows.

COMPLIANT-REFINE means the build did what its mission said, correctly, and the
review proposes a better product. These are the ones that get scheduled rather
than escalated, because shipping them is an improvement and not shipping them
is not a bug.

Each verdict below carries the code evidence it rests on. Findings that drive a
Tier 0 classification were read directly in the source rather than accepted from
a summary — a habit adopted after a research pass reported two of the Payments
findings backwards in both directions.

## Corrections to the review

Four of the review's findings are wrong, inverted, or scoped to one screen when
the defect is systemic. Adopting the review verbatim on these four would have
produced work that fixed nothing, or fixed one instance of something that
appears on a dozen screens. The corrections are load-bearing, so they come
first.

### Correction 1 — the messaging finding is inverted

The review's top priority is stated as removing Offers and Orders filters from
the social messenger, on the reading that commerce has leaked into the social
inbox. The code says the opposite.

`MessengerScreen.tsx` is the social tab. Its filter union is
`type ConversationFilter = "all" | "direct" | "groups" | "rooms" | "ai" |
"unread"` and `conversationMatchesFilter` switches on `conversation_type`
alone. There is no Offers filter and no Orders filter in the social messenger.

The screen the reviewer photographed is `CommerceInboxScreen.tsx`, a separate
423-line screen reached through `MessagesRoute` at the registered route
`BusinessOsMessages`, which is exactly where the hub's Messages card already
points. The Commerce Inbox exists, is already built, and is already the hub's
target. It is where Offers and Orders filters live, and that is correct — those
filters belong there.

The actual defect is the reverse of the review's: the Commerce Inbox has no way
to ask for commerce conversations, so it loads the same undifferentiated list
the social tab loads, and social threads leak *into* commerce. Its own header
comment admits the wiring: conversations, unread counts, timestamps and
snippets are live from the Messenger v2 surface via `loadInboxModel` →
`listConversations`. The filters then run client-side on a field that is never
populated — `rowMatchesFilter` switches on `row.chip?.kind`, and `realLinkFor()`
reads a `commerce_link` property nothing ever sets.

The backend confirms there is nothing to ask for. `comm_v2_conversations`
carries `id`, `conversation_type`, `created_by_user_id`, `owner_user_id`,
`business_id`, `title`, `status`, `is_public`, `member_count`,
`last_message_at`, `last_activity_at`, `created_at`, `updated_at` and no domain
discriminator at all. The endpoint at
`/api/pulse/communications/v2/conversations` has no domain to filter on because
the column does not exist.

This reframes Tier 0.4 from a UI deletion into a schema change, which is how it
has been authorised: add `conversation_domain` to `comm_v2_conversations` with
a backfill, thread it through the API and the mobile type, and partition the
Commerce Inbox query on it. The verdict on the underlying finding stands —
commerce and social must be separated — but the direction of the leak, and
therefore the entire shape of the fix, is corrected here.

### Correction 2 — both Payments findings are true

A research pass reported the Payments double-shell and duplicated-error findings
as false. Both are true, verified by reading `BusinessOsPaymentsScreen.tsx`
directly.

The double shell is real and Payments is the only Business OS screen that has
it. Every other business screen that draws its own header is registered with
`headerShown: false` — `BusinessOsOrders`, `BusinessOsMessages`,
`BusinessOsEvents`, `BusinessOsActivity`, `BusinessOsInsights`,
`MarketplaceManager`, `BusinessProfile`, `SellerStore` in dashboard mode, and
`BusinessOsAdvertising` in manager mode. `BusinessOsPayments` is registered with
a title and nothing else, so the stack header renders, and then the screen
renders its own `LinearGradient` header containing a `Pressable` with a
`chevron-back` icon and a `Text` reading "Payments". Two headers, two titles,
two back affordances. The earlier report that there was no back button misread
`<View style={styles.back} />` — that is the right-hand spacer, not the control.

The duplicated error is real. `BalanceHero` sits inside the header gradient and
renders unconditionally. On `balanceError` it receives `formattedAmount` of
`"—"` (from `const heroAmount = balanceError ? "—" : formatMoney(...)`) and an
`onRetry` handler. Below it, `{!loading && balanceError ? <PaymentsError
onRetry={...} /> : null}` renders a second failure card with a second retry. The
user sees an em dash where their balance should be, one retry attached to it,
and a separate error card with another retry underneath. The earlier report
placed `BalanceHero` inside a `!balanceError` branch; it is not in one.

### Correction 3 — developer copy is systemic, not a Verification defect

The review scopes the developer-language problem to the Verification Center. It
is not confined there. "server-authoritative" and its neighbours appear in
user-facing copy across more than a dozen screens. Fixing only Verification
would leave the same defect on the rest of them and would give the impression
the class had been handled.

Within Verification the specifics are as the review describes and worse.
`VerificationCenterScreen.tsx:215` tells the user "Documents are uploaded only
to the existing private verification route. Native does not inspect, store, or
review identity documents" — the second sentence is good and reassuring, the
first names an internal route. Line 148 says the records "stay
server-authoritative through existing PulseSoc verification systems." Line 193
renders `item.detail`, and `api/verification.ts:209` sets one of those details
to the literal string ``Requests use
`/api/dashboard/account/verification/request`.`` — a raw API path, rendered to a
seller.

The correction is that Tier 0.3 is widened from a Verification copy pass to a
copy audit across every user-facing surface that uses this vocabulary, with
Verification as the first and most urgent instance because it is the screen
where a seller's trust is most in play.

### Correction 4 — the "third visual shell" claim is false, and the real number is nine

The review says Verification introduces a third visual shell. It does not.
`VerificationCenterScreen.tsx` imports `Panel` from `../components/Panel` and
`colors` from `../theme/colors` — the same dark primitives `BusinessOsScreen`
uses. Verification is on the standard shell. Its problems are state logic and
copy, not theme.

The real finding is larger than the review's. There are nine separate light
theme modules in `theme/`: `paymentsLight`, `storeLight`, `insightsLight`,
`adsLight`, `messagesLight`, `ordersLight`, `eventsLight`, `marketplaceLight`
and `hubLight`, each with its own component family beneath it — nine under
`components/payments/`, twelve under `components/ads/`, seven under
`components/insights/`, and so on. This is not two shells or three. It is one
dark shell plus nine independent light ones, each of which independently decided
its own spacing, type ramp, header shape and state vocabulary.

That materially changes Tier 1.2. "One adaptive BusinessShell" is not a
refactor of a fork; it is a consolidation of nine parallel design systems, and
it is the largest single item in this mission. It also explains Tier 0.1: nine
independent implementations of a quick-link tile is precisely how the same
truncation defect appears in three places with no shared code to fix.

### Correction 5 — the commerce/social premise names a surface that does not exist natively

Added after Tier 0.4 was implemented, from evidence found during that work.

The review's top-priority finding describes Offers and Orders filters sitting
over social threads. They do not exist in the native social messenger.
`MessengerScreen.tsx` filters are `all | unread | direct | groups | rooms | ai`.
The Offers and Orders chips live only in `components/messages/FilterChips.tsx`
and `api/commerceInbox.ts`, consumed by `CommerceInboxScreen.tsx` — which is the
commerce surface, where they belong. The screenshot is either a web surface or a
build that predates the commerce inbox.

Tier 0.4 stands regardless, for a different reason than the review gave. The
defect is not that the chips are in the wrong place; it is that nothing in the
data layer prevented them from being. Conversations had no domain, so the two
inboxes were separated only by which query a screen happened to call. The fix is
therefore the required `conversation_domain` and the partitioned caches, not a
removal.

A second finding fell out of the same work: **the app has no conversation search
at all.** The review's "marketplace threads must never appear in social search"
could not be implemented as a fix to an existing leak. `searchSocialConversations`
was built as the single social search entry point, scoped by construction before
the text match, so that when a search field is eventually added it cannot reach a
marketplace thread. This is a spec gap the review found by accident.

## Tier 0 verdicts — launch blockers

### 0.1 Layout resilience — DEVIATION

The clipped quick-link cards in Marketplace ("S…", "M…") and Advertising ("A…",
"Cr…") are the same defect class that forced the hub revert, which makes this a
deviation rather than a gap: the rule existed, it had already been paid for
once, and it was not applied.

The mechanism is exact and shared. `components/store/StoreQuickLinkTile.tsx:75`
declares its wrapper as `{ flex: 1 }`. Store's dashboard uses it correctly, in
explicit `linkRow` containers holding exactly two tiles each
(`StoreDashboardScreen.tsx:289–356`, styles at `:635–636`), so each tile
resolves to fifty percent. Marketplace puts four of the same tiles into
`moreGrid: { flexDirection: "row", flexWrap: "wrap", gap: 10 }`
(`MarketplaceManagerScreen.tsx:926–959`, style at `:1718`) and Advertising does
the same into `toolGrid` (`AdsManagerScreen.tsx:629–666`, style at `:954`). Four
`flex: 1` children in one wrapping row each get roughly a quarter of the width,
and the label has nowhere to go.

The aggravating finding is that no Dynamic Type guard exists anywhere in the
codebase. There is no `useWindowDimensions`, no `PixelRatio.getFontScale`, no
`allowFontScaling` policy, no `maxFontSizeMultiplier` and no
`adjustsFontSizeToFit` in any business surface. Every font size is hard-coded.
The build was validated at default font scale on a wide device, which is the
identical root cause the revert post-mortem recorded — and recording it did not
prevent it recurring, because the finding was written as prose in a document
rather than as an executable gate.

The fix is therefore the component and the gate, not the instances. The shared
tile gets hard rules: no mid-word breaks, the title never truncates below full
word visibility, explicit minimum and maximum line counts, equal-height rows by
construction, and a disabled state that is visually distinct without relying on
grey truncated text. All four-across rows are replaced with Store's two-column
pattern. And the gate becomes automated font-scale render tests asserting layout
invariants at large `fontScale`, added permanently to the definition of done of
every future UI change, backed by a device checklist for the product owner. A
prose reminder is what failed; an assertion is what replaces it.

### 0.2 Payments shell repair — DEVIATION

See Correction 2 for the evidence. Both halves are deviations: the Payments
mission specified one shell and one error presentation, and the screen ships
two of each.

The target is one shell, one header, one back action, and one consolidated error
per failure, ordered as business header, then title, then balance summary, then
a single error card carrying a support reference. The "display problem, not a
change to your money" copy is kept, because it is correct and it is the single
most important sentence on the screen — but it is said once. The support
reference code is added, because a seller looking at a failed balance needs
something to quote.

Registering `BusinessOsPayments` with `headerShown: false` is the smaller half.
The larger half is that Payments is the only screen still forking its own header
inside a stack header, so repairing it is also the first consumer of the Tier
1.2 shell.

### 0.3 Verification compliance pass — DEVIATION

Three defects, all deviations from the Verification mission.

The status logic is wrong.
`VerificationCenterScreen.tsx:210` renders `<ActionButton label={busy ===
"request" ? "Submitting..." : "Start verification request"} ...>` with no status
check whatsoever. An already-approved seller is shown a primary call to action
inviting them to start a verification request. The screen must be status-aware:
Approved offers View, Update and Add another, and never a primary "Start
verification request".

The developer copy is as described in Correction 3, and the fix is widened
accordingly.

The shell migration item is amended by Correction 4 — Verification is already on
the standard dark shell, so there is no migration off a third theme. It joins
the adaptive shell when the shell exists, on the same schedule as everything
else, and is not special-cased.

The review is right that the trust content itself is good product. The sentence
about not inspecting or storing identity documents is worth keeping and worth
saying more clearly. What has to change is the presentation and the state logic
around it.

### 0.4 Commerce and social messaging separation — SPEC GAP

Adopted in full, with the direction of the leak corrected per Correction 1.

No mission ever said conversations carry a domain, so nothing was violated —
the specification simply never existed. It is adopted now.
`conversation_domain` becomes a required field on `comm_v2_conversations` with
values SOCIAL, MARKETPLACE, STORE_SUPPORT, DISPUTE and EVENT, backfilled,
threaded through the v2 conversations endpoint and the mobile type, and used to
partition storage and queries. The Commerce Inbox queries the commerce
partition. Marketplace threads never appear in social search.

The Messages mission's inbox design — context chips, expiry banner, saved
replies, away mode — now applies to the Commerce Inbox rather than to the social
app, which is where it was always headed and where the screen already lives.
The existing MOCK-DATA note in `api/commerceInbox.ts` already named this gap
precisely: join conversations to their commerce object server-side, by
`offer_id`, `order_id` or `listing_id` on the conversation, or by a resolver
endpoint. That note becomes the implementation.

### 0.5 Honest-status corrections — mixed

These are individually small and collectively the difference between a seller
trusting the numbers and not.

**Store readiness ladder — SPEC GAP.** `api/storeDashboard.ts:444–446` reads
`if (rows.length === 0) return { open: true }`, so a store with nothing in it
reports itself open. The function's own comment is candid that there is no
seller-level storefront switch in the API and that the derivation is a best
honest reading, which is exactly what a spec gap looks like — the build was
transparent about inventing a definition because none was supplied. The review
supplies one: Draft, Needs setup, Ready, Open, Paused, Restricted, Suspended,
with a setup checklist card. Adopted.

**"Just listed near you" — DEVIATION.** This one is a genuine internal
contradiction. `LocationStrip` at `MarketplaceManagerScreen.tsx:1522` already
does the right thing and renders "Location not set — showing all listings", with
a comment explaining that printing a fabricated radius would be a claim about
where the user is. Directly beneath it, line 1277 renders `<SectionHeader
title="Just listed near you" />` unconditionally — the exact fabricated
proximity claim the strip was written to avoid. The header becomes "Just listed"
until a location is set, and the section gains empty-state actions.

**Ambiguous "—" — SPEC GAP.** The em dash is used as a placeholder across
roughly seventy-eight screen files and forty API files, meaning variously no
data, not loaded, failed to load, and not applicable. The state language
standard from Tier 1.3 replaces it. This is a large mechanical change and is
sequenced behind the standard, not ahead of it.

**Insights errors — partially COMPLIANT-REFINE.** The review's claim that Export
is enabled without data, and that the reason is not exposed to assistive
technology, is **false**. `BusinessOsInsightsScreen.tsx` computes
`exportDisabled = !summary || exporting || Boolean(exportBlockedReason)` and
renders the pill with `accessibilityHint={exportBlockedReason || "Shares a CSV
of the figures on screen"}` and `accessibilityState={{ disabled: exportDisabled
}}`. Export is already disabled when there is no data and the reason is already
announced. The screen also already blocks export of cached figures, with a
comment explaining that a stale CSV outlives the banner that qualified it —
which is better than the review asks for. What is genuinely thin is the cause
vocabulary: `insightsErrorMessage` at `api/insightsDashboard.ts:538` distinguishes
only 401, 503 and everything else. Offline, entitlement and not-configured are
missing. That narrower gap is adopted; the Export finding is dismissed with
evidence.

**Raw internal IDs — DEVIATION.** `AdsManagerScreen.tsx:322` renders
`{account.business_name || "Ad account"} · Ad account {account.id}`, producing
"Ad account 8". The review missed a second instance of the same class:
`VerificationCenterScreen.tsx:163` renders `Request #{state?.requestId || "not
started"}`. Both are de-emphasised or removed; where a reference is genuinely
needed for support, it becomes a support reference code rather than a database
key.

**Notification badges — partially COMPLIANT-REFINE.** `core/unreadCounts.ts`
already separates `bellCount` from `messageCount` via `alertUnreadCount` and
`chatUnreadCount` in `api/notifications.ts`, and already exposes a single
snapshot with listeners, so the "single source" half of the finding is already
satisfied. The real gap is that commerce and social message counts are not
distinguishable, which falls out of Tier 0.4 for free once
`conversation_domain` exists. A smaller instance worth noting: Insights renders
its header with a hard-coded `unreadCount={0}`.

## Tier 1 verdicts — architecture commitments

All seven are adopted. Each becomes an accepted ADR with a named owner; none is
silently deferred. The verdicts here record why each is architecture rather than
a fix.

**1.1 Canonical commerce entity graph — SPEC GAP.** The distinction between a
Product, a Store listing and a Marketplace listing was never written down, so
each screen made its own assumption. Adopted and supersedes the looser entity
language in the prior section missions.

**1.2 One adaptive BusinessShell — SPEC GAP, scope amended by Correction 4.**
Appearance becomes a shell mode rather than a per-screen fork. The hub keeps its
dark appearance. The scope is nine light theme modules and their component
families, not one fork, which makes this the largest Tier 1 item and the one
most likely to need staging across phases.

**1.3 Unified state and error standard — SPEC GAP.** A screen-state enum plus
the zero, loading, unavailable, not-configured, restricted and no-activity
vocabulary. This is the prerequisite for the "—" cleanup in 0.5 and for the
Insights cause vocabulary, so it is sequenced early.

**1.4 Seller eligibility and entitlements — SPEC GAP.** One entitlement source
covering Store, Marketplace selling, Advertising and Payouts.

**1.5 Campaign hierarchy — SPEC GAP, amends the Advertising mission.** Campaign
→ Ad group → Ad, with edit classification as safe, restart-learning,
review-required or locked. The Advertising mission's flat campaign object is
amended rather than superseded; its money rules are untouched.

**1.6 Roles and permissions — SPEC GAP.** Minimum first cut is Owner and Admin
enforced on money and verification, with verification documents excluded from
normal staff visibility. That exclusion is a compliance property, not a
convenience, and is not negotiable down in a later phase.

**1.7 Reconciliation and background-job standard — SPEC GAP.** Job ID, retry,
dead-letter, idempotency, audit event and admin visibility.

## Tier 2 — staged build-out

The review's Phase 0 through Phase 12 is adopted with adjustments.

Phase 0 items that already exist are audited, not rebuilt. Several of the
review's Phase 0 findings turned out on inspection to be already implemented and
in some cases implemented better than specified — the Insights export policy is
the clearest example. Rebuilding those would destroy working code and working
comments that explain why the code is the way it is.

Phase 1 is Tier 0.4 and runs first, because the domain field is a dependency of
the badge work, the Commerce Inbox design work and the search-partitioning work.

Phases 2 through 8 layer the review's sections on top of the existing section
missions. Where a review section and a mission section overlap, the stricter
rule wins. This matters most for money and verification, where the existing
missions are stricter than the review in several places.

Phases 9 through 12 follow.

Phase 13 and the full risk platform are recorded as roadmap with their specs
preserved intact. Only the risk hooks are wired now, so the platform has
somewhere to attach when it arrives.

Phase 14 is not a phase. Its items become permanent gates at every phase
boundary, starting with Tier 0. The font-scale gate from 0.1 is the first of
them, and it is automated precisely because the same lesson was already learned
once in prose and did not hold.

## Completion standard

Both of the review's end-to-end chains become the system's acceptance test,
superseding the wiring mission's S1 through S9 where the two overlap. The
S-scenarios remain in place as intermediate gates rather than being deleted,
because they are cheaper to run and they fail earlier.

## Findings the review missed

Three, recorded so they are not lost between documents.

`VerificationCenterScreen.tsx:163` exposes a raw request ID to the seller, the
same class of defect as "Ad account 8".

There is no Dynamic Type guard anywhere in the codebase — not a missing guard on
the affected screens, but no instance of the pattern at all. This is what makes
0.1 systemic rather than local.

The nine light theme modules described in Correction 4. The review saw two or
three shells because it was looking at screenshots; the code has nine.

## Open questions for the product owner

What is the correct `conversation_domain` for a conversation that predates the
column and cannot be classified from its participants or its title? The backfill
needs a default, and defaulting ambiguous rows to SOCIAL risks hiding commerce
history from the Commerce Inbox, while defaulting them to MARKETPLACE risks
leaking social threads into a seller surface. A third value for
unclassifiable-legacy rows is available but adds a state every query has to
decide about.

Should the Store readiness ladder's Restricted and Suspended states be derivable
from data the mobile client can see, or do they require a backend field that
does not exist yet? The other five states are derivable; these two describe
administrative actions, and inventing them client-side would repeat the mistake
`deriveStatus` is already apologising for.

Which of the nine light themes is the reference for the adaptive shell's light
appearance? Consolidating nine into one requires picking a winner, and the
choice determines how much visual change each of the other eight screens absorbs.

Is there an appetite for the "—" replacement to land as one large mechanical
change or to be absorbed screen-by-screen as each screen is touched for other
reasons? One large change is auditable and reviewable in a single pass;
screen-by-screen leaves the codebase inconsistent for longer but never presents
a hundred-file diff.
