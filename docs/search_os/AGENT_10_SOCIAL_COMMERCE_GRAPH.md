# Agent 10 — Social × Commerce Search Graph

Measured against production at `5bdf4e431`, the deployed commit, on 2026-10-03.
Re-derive every number with:

```
railway run --service Postgres .venv/bin/python \
    scripts/search_os/agent_10_graph_inventory.py
```

Do not trust the numbers below without re-running it. Pulse Loop publishes
hourly, so the PulseDrop counts move on their own.

## Executive status

**The social × commerce graph asked for in the brief already exists, is
deployed, is correctly built, and is empty.** The brief reads as a greenfield
design task. It is not one. Both factual edge types it specifies are already in
production, with provenance, with owner-only authority, with the price read live
rather than snapshotted, and with the publisher/merchant separation the brief
calls for. What is missing is not architecture. It is rows.

**No new edge table, no new node type, and no new service was added by Agent 10,
because the correct ones were already there.** This document and the inventory
script are the deliverable.

**"See It in the Pulse" must not ship yet.** Section 5 gives the measurement and
the precondition. The short version: the creator-tagged source would render
empty for 196 of 196 products, and the only non-empty source is the platform's
own promo output, which must not be dressed as social proof.

## 1. The two factual edges that exist

| | `pulse_content_products` | `pulsedrop_publications` |
|---|---|---|
| claim | "the creator says this post is about this product" | "PulseDrop published a promo for this listing" |
| author | the creator, via the composer | the Pulse Loop curator |
| writer | `commerce_discovery.tagging.attach` | `pulsedrop.publisher` |
| reader | `tagging.tagged_listing_ids` | `pulsedrop.hydration` |
| provenance column | `authority` (`'owner'`, the only value) | `ranker_version`, `editorial_label`, `rank_score` |
| key | polymorphic `(content_type, content_id)` | `post_id`, `reel_id` |
| **prod rows** | **0** | **146** |

Both resolve to a canonical `marketplace_listings.id`. Neither snapshots price,
title or availability — those are read live on every serve. That is not an
accident of implementation; `pulsedrop/hydration.py` and
`commerce_discovery/tagging.py` each open with a written argument for why a
frozen price is "not a stale copy of the truth, it is a lie to a buyer."

Phases 56, 53/54 and 118 of the brief are therefore **already satisfied by
construction**, not pending:

- A commerce attachment cannot alter canonical price — nothing stores one.
- A held or sold-out product withdraws its CTA rather than its content.
  `hydration` has no code path that can emit a route which would 404.
- PulseDrop is publisher, the seller is merchant, the listing is the commerce
  object, "never collapsed". `attribution_token` is deliberately unsigned
  because it is a lookup, not a claim.

`relationship.UNIMPLEMENTED_RELATIONSHIPS` is the right file to read before
proposing any new edge; `CREATOR_TAGGED` sat in it until a writer existed.

## 2. What does **not** exist

**There is no reverse reader.** `tagging.tagged_listing_ids` answers
*content → products*. Nothing anywhere answers *product → content*. Grep for a
reader of either edge table keyed on `listing_id` and the only hits are the
curator's own diversity and ops queries.

So the question the brief is built on — "which public Signals and Reels
legitimately reference this product?" — has no implementation, no cache, and no
consumer. That is the real gap, and Section 5 is why filling it now would be a
mistake rather than progress.

**There is no user-authored edge on `pulse_posts` itself.** No `listing_id`, no
`product_id` column, in the DDL or in `add_columns_if_missing`. The attachment
is the separate polymorphic table, shaped after `pulse_content_music`. Anyone
looking for a column will conclude the feature is absent; it is not.

## 3. Measured inventory

```
users                                        48
published listings                          196
distinct sellers                              1
creator-tag edges                             0
pulsedrop edges (published)                 146
listings with a pulsedrop edge               39
listings with no social edge of any kind    157   (80% of the catalogue)
orphan edges (either table)                   0
publications whose post is deleted/private    0   (all 146 public, none deleted)
```

Edge integrity is clean. There is nothing to repair: zero orphans, zero
publications outliving their post, zero edges pointing at a vanished listing.
Phases 51 and 52 find no work.

## 4. The binding constraint is one seller, not engineering

`tagging.attach` refuses any listing the author does not own
(`REFUSED_NOT_OWNER`), deliberately and for a stated reason: tagging someone
else's product is affiliate marketing, which needs a commission model and a
per-jurisdiction disclosure obligation, and those are "much harder to withdraw
than to delay."

Production has **exactly one seller** — user 1, `roodycherie`, who owns all 196
published listings. So the set of accounts that can create a creator-tag edge at
all is one account, and it is the repo owner's. The other 47 users open the
composer's product picker, see nothing, and cannot draw a single edge.

**The creator-tagged graph is empty because of the ownership rule meeting a
one-seller marketplace, not because creators declined to use a shipped
feature.** The full stack is live: `TaggableProductPicker.tsx`,
`HomePulseComposer.tsx`, `GET /taggable-products`,
`bot.pulse_attach_products_to_content`, 67 tests across
`tests/commerce_discovery/test_a_creator_can_tag_their_own_products.py` (49) and
`test_the_composer_is_told_what_will_actually_serve.py` (18), plus a regenerable
mutation matrix at `scripts/protection/creator_tagging_mutation_matrix.py`.

This is the single most important finding for Agent 0's sequencing: the social ×
commerce graph is gated on **seller supply**, which no search agent can fix.

## 5. "See It in the Pulse" — do not ship, and why

Shadow-computed what the module would render today, per source:

**Creator-tagged source: empty for 196 of 196 products.** Zero edges exist.

**PulseDrop source: non-empty for 39 products, and unusable.**

- 33 of those 39 listings carry **4 publications each**; 4 carry 3; 2 carry 1.
- **130 of 146 publications have a post title that equals the listing title
  verbatim.**

So for a product with social context, the module would show up to four
machine-authored posts whose titles are copies of the title already at the top
of the page, linking back to the page they are on. Three failures at once:

1. **Manufactured social proof** (Phase 76). Presenting the platform's own
   marketing output as "see it in the Pulse" tells a buyer other people are
   talking about this product. Nobody is. PulseDrop is.
2. **A duplicate-content loop** (Phases 19, 27). PDP → promo post → same PDP,
   where the promo's title competes with the PDP for the same query. These posts
   are already served `index,follow` and already sitemapped.
3. **No diversity possible** (Phase 62). One author, one product, four
   near-identical posts. The diversity rule has nothing to choose between.

A truthful module needs content that is *about* a product and *not authored by
the platform*. The precondition is therefore population, and there are only two
routes to it:

- **more sellers**, which makes the existing owner-only rule sufficient; or
- **a second `authority` value** — affiliate or editorial — which `tagging.py`
  deferred on legal grounds, not technical ones. The column and the refusal
  vocabulary are already shaped to accept one.

Until one of those lands, the honest module is no module. An empty state on 196
of 196 PDPs is not a feature, and Phase 22 already forbids rendering the module
with placeholders.

## 6. Findings handed to other agents

**To Agent 2 and Agent 6 — PulseDrop promos are competing with the PDPs they
advertise.** 130 of 146 publications duplicate a listing title verbatim, served
`index,follow`, and `/sitemap-posts.xml` is 14/15 PulseDrop posts. This is
Agent 2's lane (indexability) and Agent 6's (sitemap eligibility), not mine. A
fix keyed on authorship — `COALESCE(login_enabled,1)=0 AND password_hash IS
NULL`, which matches exactly one of 48 users and zero humans — exists on
`claude/seo-phase1-defects` and is unmerged.

**Why the existing automated-author veto does not stop it, precisely.**
`content_eligibility` does call `is_automated_author`, so the veto exists and is
not missing. It fails for a reason worth stating exactly, because the obvious
fix is a no-op: `is_automated_author` tests four things in order — a nested
`author.automated` / `author.official_system_account` / `author.account_type`,
then top-level `automated` / `official_system_account` / `account_type`, then
falls back to `user_id <= 0`. **Neither `users` nor `pulse_posts` has any of
those marker columns** (checked `information_schema`: zero columns matching
`account_type`, `automat%`, `system%`, `official%` on either table). PulseDrop's
markers are produced by `pulsedrop/account.py::profile_overlay()`, which is a
*profile payload* builder and never reaches this path. So the first three
branches are structurally unreachable from `pulse_public_entries`, whose SELECT
is `pulse_posts` columns only, and the decision collapses to the id fallback.
PulseDrop is `users.user_id = 42`, `username='pulsedrop'`, positive — so
`42 <= 0` is False and all **146** of its posts clear the prefilter.

The veto is therefore defeated by a **record-shape mismatch**, not a wrong
comparison. MEMBER_000 is caught only because `user_id <= 0` happens to be true
of it. Measured on the live prefilter: 1,875 public approved posts by the single
`user_id <= 0` author are vetoed, and 170 posts by 7 positive-id authors walk
through — **146 of those 170 are PulseDrop's**, leaving ~24 genuinely human. Do
not "fix" this by comparing `account_type` in SQL; there is no such column.
Either join `users` on the authorship predicate above, or call
`pulsedrop.account.account_user_id()`. The docstring above that SELECT already
states the general rule this violates: "A column left out of the SELECT list is
a permission silently granted."

**To Agent 2 — `hidden_from_discovery` is not a noindex consent signal.** One
account (user 30, a QA account) carries `hidden_from_discovery=1` and three
public, non-deleted posts, reachable anonymously at `/pulse/post/<id>`. The
canonical gate `services/discovery_visibility.py::discovery_visible_sql` encodes
"may appear in in-product discovery". Reusing it as "consented to be indexed"
is how someone gets published without being asked. Decide this deliberately
rather than by reusing the fragment.

**To Agents 2, 5, 6, 11 and 12 — provenance must be semantic, not a sign test
on a user id.** This is the generalisation of the finding above and the one to
carry forward: `user_id <= 0` does not mean "automated". PulseDrop is a real,
followable `users` row with a positive id, deliberately — `profile_overlay()`
explains that being followable "is the entire reason this account is a real
`users` row". Any new rule that infers authorship class from the id's sign will
misclassify it as an ordinary human creator, and will keep doing so as more
automated accounts are provisioned the same way. What the platform needs is an
authority/provenance field that survives into whatever record the consumer
builds. It does not have one; `account_type` exists only in payload builders.

**To Agent 4 — do not let a graph query decide whether a PDP renders.** If a
social-commerce module is eventually built: render only real public content,
link canonically, never render an empty shell, and never pad to a card count
(see §5 — padding here means four near-identical PulseDrop promos). Keep the
query bounded and keep the PDP's own render independent of it, so a slow or
failed graph read degrades the module and not the product page.

**To Agent 5 — none of these edges support a structured-data claim.** This is
the sharpest boundary Agent 10 owns. A creator attachment is a statement of
*aboutness* and nothing more: it is not a review, a rating, an endorsement, a
demonstration, a purchase or an ownership claim. A PulseDrop publication is a
curation act, not social proof. Therefore likes are not a `ratingValue`,
comments are not `review`, reel views are not a popularity claim, and no count
on either table may become `aggregateRating`. There is no review corpus in
production to aggregate.

**To Agent 7 — canonical Marketplace truth outranks every social surface.**
Never let a PulseDrop promo body, creator caption, signal text or reel caption
override merchant price, availability, brand, identifier, variant or seller.
The edges already do this correctly by storing none of it (§1); the risk is a
consumer re-deriving a product fact from post copy, which for 130 of 146
publications is a verbatim copy of the listing title and so looks authoritative
while being a duplicate.

**To Agent 8 — do not proactively submit social URLs before Agents 2 and 6
decide.** IndexNow distribution must consume their eligibility decision rather
than assume every publication is worth announcing. Submitting the PulseDrop
corpus today would be announcing 146 pages that duplicate the titles of the
PDPs they point at.

**To Agent 3 — do not model a second seller identity.** Both edge tables already
store `seller_user_id` and `tagging` re-checks it against the live listing on
every read, so a listing that changes hands stops carrying a permission its new
owner never granted. Consume that; it is stricter than a join.

**To Agent 9 — 70 of 146 publications carry a `reel_id`.** The render is keyed
by `(listing_id, composition_version, source_fingerprint)` in
`pulsedrop_renders`, not by post id, so a seller replacing their product photos
produces a new render. Media truth is addressable independently of the post.

**To Agent 11 — the inventory script is the telemetry.**
`scripts/search_os/agent_10_graph_inventory.py` emits every quantity in
Section 3 as JSON. It is read-only and safe to schedule.

**To Agent 12 — the invariants worth attacking are already pinned.** 67 tests
plus `scripts/protection/creator_tagging_mutation_matrix.py`, which deletes each
of 11 guards in turn and proves a test fails for each. Attack the *reverse*
projection when someone writes one; that is the unguarded surface, because it is
where a private post or a hidden author would first leak into a public page.
Section 8 lists the fifteen specific mutations that must fail.

## 7. What Agent 10 deliberately did not do

- **No new edge table, node type or service.** The correct ones exist. A second
  product↔content relation under a new name is precisely the duplication the
  brief's no-interference rule forbids.
- **No `See It in the Pulse` module.** Section 5.
- **No reverse index or cache.** Nothing consumes it yet, and building a privacy
  projection with no reader is how the projection and the privacy rule drift
  apart.
- **No change to the PulseDrop promo indexability.** It is a real defect and it
  is Agent 2's and Agent 6's to make, with a fix already written elsewhere.
  De-indexing live, indexed, sitemapped pages is not a call this agent gets to
  make alone.
- **Nothing touching payments, Stripe, the catalog safety gate, or auth.**

## 8. The fifteen mutations that must fail

Each line is a claim someone could make the code assert. A test must reject it.
Numbers 1 and 15 are the two that production would *currently* let through, so
they are the ones with real bite; the rest guard semantics that hold today and
would be cheap to lose.

| # | mutation | must fail because |
|---|---|---|
| 1 | PulseDrop's positive `user_id` ⇒ human creator | §6 — this is live today, 146 posts |
| 2 | PulseDrop publication ⇒ creator endorsement | it is a curation act, not a statement by a person |
| 3 | creator attachment ⇒ review | attachment is aboutness only |
| 4 | creator attachment ⇒ `aggregateRating` | there is no review corpus to aggregate |
| 5 | signal likes ⇒ product rating | engagement is not an assessment of a product |
| 6 | reel views ⇒ "best seller" / "popular" | needs a separately authoritative methodology |
| 7 | private signal attached to public product ⇒ shown on PDP | the attachment does not publish the content |
| 8 | private reel attached to public product ⇒ public video projection | same, for media |
| 9 | held product ⇒ live CTA via an old attachment | `hydration` withdraws the CTA, not the content |
| 10 | deleted content ⇒ stale PDP module entry | 0 such rows today; keep it 0 |
| 11 | edge snapshots an old price ⇒ old price rendered | nothing stores a price (§1) |
| 12 | client attachment payload carries a price ⇒ canonical price changes | the writer accepts no price field |
| 13 | creator attaches another seller's product ⇒ factual commercial relationship | `REFUSED_NOT_OWNER` (§4) |
| 14 | four repetitive PulseDrop promos ⇒ diverse social proof | §5 |
| 15 | `hidden_from_discovery` ⇒ noindex | **unfrozen contract — Agents 0/2 decide, not a test** |

Mutation 15 is deliberately phrased as "must fail *until* the contract is
frozen". The current code neither infers it nor denies it; the risk is a future
author reusing `discovery_visible_sql` as if it meant indexing consent. The test
to write is the one that fails if someone makes that inference silently.

## 9. Status, and the gate on reactivation

**Status: complete, pushed, standby.** Branch
`search-os/agent-10-social-commerce-graph`, commit `ac1a87a7a` plus this
revision. No new edge table, node type, or service was added, and none should
be — `pulse_content_products` and `pulsedrop_publications` are the two
relations, and a third under a new name (`social_product_edges_v2`,
`product_content_graph`, …) is a duplication defect, not a feature, however it
arrives.

**Numbers in §3 and §5 are a snapshot, not a contract.** Re-derive them with the
inventory script; Pulse Loop publishes hourly, so they move unattended. Nothing
downstream should hard-code them, and no "social SEO score" should be
synthesised from them — UNKNOWN is a valid answer for a product with no social
context, and 157 of 196 products have none.

"See It in the Pulse" stays designed and unbuilt. Agent 0 should freeze
measurable activation criteria rather than inherit a threshold invented here;
the inputs worth evaluating are factual creator coverage, independent
creator/seller count, content diversity, non-duplicate publications, privacy and
moderation eligibility, freshness, and bounded per-viewer frequency — which
nothing currently measures, since the two curators share no viewer ledger.

Reactivate Agent 10 only on: Agent 0 authorising a creator→third-party-product
model; seller population changing enough that real creator edges accumulate;
Agents 2/6 needing implementation changes after the indexability decision;
Agent 11 finding graph drift or corruption; Agent 12 breaking an Agent 10
invariant; a real privacy leak in a relationship resolver; an attachment
beginning to snapshot canonical commerce truth; or the activation criteria above
being met on evidence.

**The standing distinction, in one line:** an empty graph is not a missing
graph, and the remedy for sparseness is real participation — never a weakened
ownership gate, and never automation dressed as human social proof.
