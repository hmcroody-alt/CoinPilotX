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
`claude/seo-phase1-defects` and is unmerged. Note the authorship veto in
`search_visibility.content_eligibility()` currently keys on `user_id <= 0`, and
PulseDrop is a real `users` row at id 42, so it walks straight through.

**To Agent 2 — `hidden_from_discovery` is not a noindex consent signal.** One
account (user 30, a QA account) carries `hidden_from_discovery=1` and three
public, non-deleted posts, reachable anonymously at `/pulse/post/<id>`. The
canonical gate `services/discovery_visibility.py::discovery_visible_sql` encodes
"may appear in in-product discovery". Reusing it as "consented to be indexed"
is how someone gets published without being asked. Decide this deliberately
rather than by reusing the fragment.

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
