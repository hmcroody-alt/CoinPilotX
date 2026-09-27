# SIGNAL COMMERCE — final delivery report (§100)

This answers the 64 items §100 of the brief asks for, in its order and with its numbering.

**Read this with `PULSE_COMMERCE_INTELLIGENCE_REPORT.md`, not instead of it.** That document
is the evidence: 23 sections, every defect with the measurement that found it and the
mutation that proved the test could fail. This one is the index — each item below answers
the question, names the file, and points at the section that proves the answer. Where an
item is not built, it says so in the first sentence.

**Status: nothing here is deployed.** Branch `commerce-discovery-audit` in a worktree, not
pushed, not merged. It is also **22 commits behind `origin/main`** as of writing, which is a
rollout input and not a footnote — see item 59.

### How to regenerate every figure in this report

```
cd <worktree>
.venv/bin/python -m pytest tests/commerce_discovery -q          # 763 pass
.venv/bin/python -m pytest tests/protection -q                  # 663 pass
python3 scripts/protection/measure_commerce_discovery_reachability.py
python3 scripts/protection/audit_commerce_discovery_failsoft.py
python3 scripts/protection/prove_commerce_discovery_sources.py
python3 scripts/protection/prove_commerce_discovery_fatigue.py
python3 scripts/protection/prove_commerce_discovery_value_tiers.py
python3 scripts/measure_commerce_suitability_cost.py
```

The venv is the main checkout's (`/Users/hmcherie/Desktop/CoinPilotX/.venv`); this worktree
has none. §14 of the evidence report is the reason these scripts exist at all: a figure
nobody can re-derive is a defect, so every number quoted in either document has a script or
a test behind it.

---

## The one-paragraph version

The brief asked for a content→commerce discovery layer. **Most of it already existed** —
live in production, across six surfaces, with ranking, capping, fatigue, an exposure
ledger and per-surface relevance floors. So this work is not a new engine. It is: an audit
that found and fixed **four defects** in the existing one (a cap that counted nothing,
fatigue that did not escalate, 70.8% of the catalogue unreachable, retrieval that was
viewer-blind), the **suitability gate the brief's §13/§14 asked for and that did not exist
in any form**, a **payload leak** closed, and — the largest single body of work — the
**mutation harnesses and fail-soft audit** that established the tests can actually fail.
Two things the brief asked for are **not built**: web, and the creator-tagging write path.

---

## 1. Repository architecture discovered

Flask monolith `bot.py` (~120k lines, ~1,538 routes) over `services/` (239 modules).
Commerce discovery is a package, `services/commerce_discovery/` — 18 modules, 7,964 lines —
plus `services/commerce_discovery_routes.py` (839 lines), which sits *outside* the package
because it imports `bot` and the package deliberately does not. That boundary is real and
load-bearing; it is also what made the fail-soft audit miss the whole request layer for two
increments (§22).

Evidence report §1. The finding that mattered: **a commerce discovery engine already
existed and was live**, which the brief anticipated — *"DO NOT blindly implement
instructions in this mission if repository evidence shows that PulseSoc already has a
stronger mechanism."*

## 2. Existing systems reused

Everything in the retrieval and ranking path. `engine.serve` is still the entry point;
`pool` still generates candidates; `ranking.score_listing` still scores; `exposure` still
holds the ledger; `config` still holds the per-surface caps. No parallel infrastructure was
created, per the brief's §4. The canonical marketplace listing remains the only product
record — there is no shadow product table.

Reused and *not* reimplemented, specifically because reimplementing them is the failure the
brief names: `bot.pulse_marketplace_listing_payload` (the card payload),
`bot.parse_price_label_to_cents` (money), `pulse_feed_engine.list_feed` (the feed itself).

## 3. New architecture introduced

Four modules that did not exist:

| module | lines | what it is |
|---|---|---|
| `suitability.py` | 614 | the content→commerce permission gate (items 11, 12) |
| `relationship.py` | 264 | provenance vocabulary: *why* is this product here (items 14, 7) |
| `content.py` | 219 | server-side context derivation from a post (item 9) |
| `taxonomy.py` | 88 | category-path handling, shared by dedup and diversity |

Plus `suitability.annotate` called from the feed response in `bot.py:90531`, which is the
only `bot.py` change in the entire branch (16 lines, and 10 of them are the comment
explaining why it has its own `except`).

## 4. Architecture diagram

```
                        ┌─────────────────────────────────────┐
  POST / REEL ─────────►│ content.derive                      │  subject + tags, capped
                        │   (server-side, §9)                 │  to the same shape the
                        └──────────────┬──────────────────────┘  wire allows
                                       │ context
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ suitability.assess                  │  PERMITTED | 5 refusals
                        │   3 evidence tiers, 5 categories    │  ← refuses BEFORE retrieval
                        └──────────────┬──────────────────────┘
                                       │ permitted only
                                       ▼
  VIEWER ──────────────► ┌─────────────────────────────────────┐
  (interests,            │ pool.candidates                     │ 4 sources, union'd:
   followed sellers)     │   affinity · followed · trending ·   │ each one a separate
                        │   rotation                          │ question (§7)
                        └──────────────┬──────────────────────┘
                                       │ candidates + candidate_source
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ eligibility.check   (merchandising)  │ cover, price, moderation,
                        └──────────────┬──────────────────────┘ listing risk, seller risk
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ ranking.score_listing → 9 reasons    │ relevance floor per surface
                        └──────────────┬──────────────────────┘
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ engine._pick   greedy, live          │ diversity re-scored as the
                        │   diversity re-score + explore slot  │ answer is built (§7)
                        └──────────────┬──────────────────────┘
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ exposure  (ledger: caps, fatigue,    │ the only writer; §4, §5
                        │   cross-surface cooldown)            │
                        └──────────────┬──────────────────────┘
                                       ▼
                        ┌─────────────────────────────────────┐
                        │ _buyer_safe  ← strips PIPELINE_ONLY  │ §18.6: the leak
                        └──────────────┬──────────────────────┘
                                       ▼
        commerce_discovery_routes ──────┴──► client  (200, placements[])
              │
              └── fail-safe #2: returns 200 + ok:true + [] on ANY exception (§21)

  FEED RESPONSE ────────► suitability.annotate ──► commerce_suitable per post
                          (bot.py:90531)           → client declines the *adjacency*
```

The two fail-safes on the right edge of that diagram are the subject of items 52 and 53,
and they are the reason four of this report's own sections exist.

## 5. Content → Commerce pipeline

Two directions, and keeping them separate is the design:

**Pull** — a client asks for products *for* a piece of content.
`POST /api/commerce-discovery/<surface>` with a context → `content`/`suitability` →
retrieval. The gate runs **before** retrieval, so a refused post costs one string pass and
no query.

**Push** — the feed response tells the client which of its *own* posts may have commerce
placed beside them. `suitability.annotate` stamps `commerce_suitable` on each post in
`api_pulse_feed`'s payload. This exists because the feed's commerce row is a sibling row
*between* posts: the request that fetched the products never knew which posts it would land
between. Only the feed response knows. Without this, a correctly-refused bereavement post
still gets a shopping carousel directly beneath it, served against a *different* post's
context — the exact failure the brief's §14 is about, arriving by a route the gate cannot
see.

## 6. Data model changes

One column, nullable, no backfill: `commerce_discovery_placements.relationship TEXT`.
`schema.py`, +80 lines. Existing rows stay `NULL`, which `relationship.normalize` reads as
*unknown* rather than silently as `catalogue` — the distinction matters because
`catalogue` is a servable value and unknown is not.

Nothing else. No new table, and in particular **no post↔listing relation table**, which is
item 7's whole answer.

The ALTER-after-CREATE ordering and its PostgreSQL translation are pinned by tests
(`test_schema_durability.py`) rather than argued for in prose, because the repo has no
migration framework: `bot.init_db()` creates ~550 tables imperatively and every change has
to be idempotent.

## 7. Creator product-tagging architecture

**Not built.** This is the largest single gap and it is honest to lead with that.

`relationship.CREATOR_TAGGED` exists as a constant and is in
`UNIMPLEMENTED_RELATIONSHIPS`, which `assert_servable` refuses. So the vocabulary is
reserved and the engine will not serve a value it cannot produce — but there is no write
path, because **no post↔product relation exists anywhere in the repository.** Greps for
`content_product`, `post_product`, `signal_product`, `product_tag`, `tagged_product`,
`post_listing`, `listing_post`, `attached_product`, `marketplace_attachment` are all empty.
`pulse_posts` (`bot.py:119204`) has no listing reference.

The precedent shape for when it is built is `pulse_content_music` — a join table with the
post id, the entity id, and a position — and the reason to follow it rather than invent is
that it already solved moderation and deletion cascade for a post-attached entity.

`COMPLEMENTARY` and `PULSEDROP_CURATED` are in the same state for the same reason.

## 8. Permission model

Two independent permissions, and they answer different questions:

1. **May commerce appear beside this content?** `suitability` (item 11). A property of the
   *content*. Not viewer-specific, not overridable by the viewer.
2. **May this viewer be personalised to?** `preferences` + `COMMERCE_DISCOVERY_PERSONALIZATION`.
   A property of the *viewer*.

The asymmetry is deliberate: a viewer who opts out of personalisation still gets a
non-personalised shelf, but no setting on either side turns suitability off. And per the
existing memory note, shop surfaces (`marketplace`, `product_detail`) are exempt from the
*social* opt-out — asking not to be profiled in the feed is not asking the shop to stop
being a shop.

Route-level: `_require_user` (`commerce_discovery_routes.py:151`). Every endpoint is
authenticated; none is public.

## 9. Context extraction strategy

`content.derive(post)` → `{subject, tags}` or `None`. Text only, deterministic, no model
call, no network:

- `subject_of` — title, else the first meaningful line of the body.
- `tags_of` — declared tags (`tags_json`, `ai_tags_json`) plus hashtags parsed from the
  text, Unicode-aware, normalised, deduped, capped at 12 × 40 chars.
- Tags shorter than 3 characters are dropped, because `ranking._tokens` discards words of
  ≤2 characters as stopwords: a shorter tag cannot contribute to a match, so keeping one
  consumes a slot a real signal could have used.

The caps mirror the route's wire allowlist exactly (`MAX_FIELD_CHARS = 80`, `MAX_TAGS = 12`).
A server-derived context that was wider than the route accepts would make the same post
behave differently depending on which path it arrived by, which is a bug that only shows up
in production.

## 10. Multimodal strategy if used

**Not used.** No image or video understanding, no embeddings, no vision model. Cover images
are checked for *existence* by `eligibility` and never looked at.

Stated plainly because "Shop this look" in the brief implies vision, and shipping a
keyword matcher while letting the phrase imply a classifier would be the kind of quiet
overclaim §14 is against. What is built matches text to text.

## 11. Suitability gate

`suitability.py`, 614 lines, and it did not exist before this mission — `eligibility.py`'s
gates were merchandising and risk only (no cover image, no resolvable price, moderation
flagged, listing risk ≥30, seller risk ≥60). Nothing anywhere asked whether commerce was
*appropriate* beside a piece of content.

Six outcomes: `PERMITTED`, and refusals `SENSITIVE_CONTEXT`, `POST_TYPE_EXCLUDED`,
`MODERATION_NOT_CLEARED`, `CONTENT_RISK`, `NO_SUBJECT`.

Three evidence tiers, and the tiering *is* the design:

- **`CERTAIN_PHRASES`** — multi-word, unambiguous. Refuse alone. Membership test: *can I
  write a product post containing this phrase?* If yes, it does not belong.
- **`STRONG_TERMS`** — single terms rare in commercial language. Refuse alone. `wake` is
  the instructive exclusion: a funeral wake is exactly what this tier is for, and "wake up
  early" is the most ordinary sentence in the feed. The test is not "does this word appear
  in bereavements" but "is there no plausible product post containing it".
- **`WEAK_TERMS`** — heavy benign register. One is not evidence; **two from the same
  category are.** `treatment` and `ill` are deliberately absent: a hair treatment and a
  skin treatment are products, and pairing one with "sick" — which in a beauty caption
  means the opposite — buys a suppressed shelf on exactly the posts this layer is for, for
  no safety at all.

Five categories: `grief`, `self_harm`, `health_condition`, `disaster`, `violence`.

Applies on `CONTENT_SURFACES` = `{feed, reels, post_detail}` only. `marketplace` and
`product_detail` are shops; a grief term in a product title is a merchandising question,
not an adjacency one.

Evidence report §17 — which reports that the gate as first built was **wired to the wrong
text**, and how that was found.

## 12. Sensitive-context protections

Item 11 is the gate; this is the belt-and-braces around it.

- **Refusal precedes retrieval.** A refused post issues no query, so there is no window
  where products for a bereavement exist in memory.
- **Adjacency is a separate check.** `assess_adjacency` + `annotate` (item 5) — the client
  declines the *position*, not just the request.
- **`EXCLUDED_POST_TYPES`** = `{scam_report, memorial, obituary, tribute}` — structural, no
  text analysis needed, cannot be argued with.
- **Moderation must be positively cleared**, not merely un-flagged:
  `CLEARED_MODERATION_STATES` = `{approved, auto_approved, clean, ok}`. An unrecognised
  status refuses. Fail-closed, and the inverse of the `UNKNOWN_CLASS_RANK` trap recorded
  elsewhere in this codebase.
- **`MAX_CONTENT_RISK = 30`**, aligned with the listing risk threshold.

The refusal tests are the ones §21 found had been **green against a dead route for 29
tests**. That is item 53's subject and the single most important finding in this delivery.

## 13. Candidate-generation strategy

`pool.candidates` asks four separate questions and unions the answers:
`CANDIDATE_SOURCES = ("affinity", "followed", "trending", rotation)`.

Before this mission it asked one, and that one was viewer-blind — hence defect 4 (§7) and
defect 3 (§6). `prove_commerce_discovery_sources.py` is the harness that proves each source
contributes rows the others do not.

## 14. Candidate provenance

Two fields, both server-side only: `candidate_source` (*which question found this*) and
`relationship` (*what kind of connection this is*). Both are in
`engine.PIPELINE_ONLY_FIELDS` and stripped by `_buyer_safe` before the response.

They were **not** stripped before this mission. `serialize(row)` was a denylist, so both
reached every buyer's device on every card — an internal risk assessment of a named store
(`seller_risk_score`) and a readout of which retrieval question produced the card. §18.6.
Per-row provenance is deliberately not *persisted* beyond `relationship`; §13 of the
evidence report is the argument.

## 15. Ranking strategy

`ranking.score_listing`, nine reason codes in a fixed priority
(`REASON_PRIORITY`): creator context, interests, viewed, similar product, followed seller,
trending, new arrival, popular, explore. Per-surface relevance floors in `router.py`.

The change this mission made is in `engine._pick`: diversity is now **re-scored live as the
answer is built**, because the diversity of a partial answer is the only thing a greedy
pick can know. The previous implementation scored every candidate with `diversity=1.0` and
then wrote a computed `diversity_bonus` into the picks *after* deciding — so the number
appeared in the payload, was asserted in a test, and had never influenced an ordering.

The floor is tested against the **pre**-diversity score and the ordering against the
**post**-diversity one. That split is the design, not an oversight: the floor is a question
about relevance, and letting a diversity penalty push a relevant product under it would
make composition silently override relevance.

## 16. Personalization

Viewer interests and followed sellers, read per request, gated by
`COMMERCE_DISCOVERY_PERSONALIZATION`. `interests=()` and `followed_sellers=()` reproduce
the pre-mission behaviour exactly — which is what makes the off-switch a two-line change
rather than a revert, and is why §16 recommends shipping multi-source retrieval behind it.

## 17. Exposure Ledger integration

`exposure.py` is the only writer. It holds the session caps, the fatigue ladder, and the
cross-surface cooldown (`COMMERCE_DISCOVERY_CROSS_SURFACE_COOLDOWN`, 1800s).

Two defects were here. The product cap **counted nothing** (§4) and fatigue **did not
escalate** (§5) — a repeat cost the same as the fifth repeat. `prove_commerce_discovery_fatigue.py`
is the harness.

## 18. Deduplication strategy

Per-response by listing id, and per-viewer over the ledger window. `test_repetition_is_observed.py`
holds the assertions.

## 19. Product-family deduplication

By category path via `taxonomy.category_key` / `segment_root`, not by title similarity.
There is no variant-family table on marketplace listings to dedupe against, so category
path is the strongest available proxy — and saying that is better than implying a family
model exists.

## 20. Seller/category diversity

Per-surface budgets in `router.py`, enforced in `engine._pick` (item 15).

One figure worth flagging: the diversity caps are **loosened to the diversity the
production catalogue actually holds** (`engine.py:712`). That is correct behaviour for a
catalogue of one seller and it will need re-examining when there are more — prod marketplace
is currently **one seller, 11 eligible listings**, all `user_id=1`.

## 21. Exploration

One reserved slot per response, taken from the **bottom** of the qualifying set, not from
above the floor. `engine._pick`. Taking it from the top would be indistinguishable from
ranking; taking it from below the floor would serve something judged irrelevant.

## 22. New-product strategy

`REASON_NEW_ARRIVAL` plus the explore slot. A product with no stats is not penalised for
having none — which is what makes item 51's `_listing_stats` finding matter: if the stats
read fails silently, *every* listing looks new at once.

## 23. New-seller strategy

The seller-diversity budget (item 20) is what gives a new seller reachable slots. Defect 3
(§6) was the real barrier: 70.8% of a large catalogue could not be retrieved at all, and
that is not distributed evenly across sellers.

## 24. New-user strategy

`interests=()` degrades to trending + rotation + explore. A viewer with no history gets a
non-personalised shelf rather than an empty one.

## 25. Shelf types

Six surfaces: `feed`, `reels`, `messenger`, `marketplace`, `post_detail`, `product_detail`
(`schema.py:52`). Each has its own cap, spacing, relevance floor and diversity budget in
`config.py` / `router.py`.

`post_detail`'s share is 0.10 — the change there removes the commerce card from 10.0% of
live posts (207 of 2,070), which is item 59's most concrete rollout number.

## 26. Home integration

The feed surface, via `injectCommerceRows` in `mobile-native/src/commerce/commerceRows.ts`:
lead-in 4, interval 8, max 2 rows, 4 products per row. Plus `neighbourAllowsCommerce` /
`neighbourSellsItsOwnProduct`, which read the `commerce_suitable` annotation from item 5 and
decline the adjacency.

## 27. Mobile implementation

`mobile-native/`: `commerce/commerceRows.ts` (+88), `useReelsCommerce.ts`,
`usePostDetailCommerce.ts`, `api/commerceDiscovery.ts`, `ReelsScreen.tsx`,
`MarketplaceProductScreen.tsx`. Tests: `commerceRowsNeighbour.test.ts` (+401),
`useReelsCommerce.test.ts`, `usePostDetailCommerce.test.ts`, `MarketplaceProductFunnel.test.tsx`.

**There is no shared canonical product card.** Each surface renders its own, which means a
change to how a product looks is four changes. Item 63.

## 28. Web implementation

**Not built. Zero.** `grep -rl commerce_discovery templates/ static/` returns nothing.

The brief is explicit about what the answer is *not* — *"Do NOT simply stretch the mobile
carousel across 1400 pixels"* — and it is right: the mobile shelf is a horizontally-scrolled
row of four sized for a thumb, and the web equivalent is a grid with different density,
different hover affordances and a real "See All" page. That is a design decision, not a
port, and there is a full web rebuild already planned in `docs/web-rebuild/` that this
should land inside rather than beside.

## 29. Reels implementation

`useReelsCommerce.ts` + `ReelsScreen.tsx`, surface `reels`, its own cap and floor.
`useReelsCommerce.test.ts` (+147).

## 30. Creator workflow

**Not built** — it is the client half of item 7, and there is nothing to build a workflow
over until the relation table exists.

## 31. See All contextual Marketplace experience

Partial. `GET /api/commerce-discovery/marketplace/modules` exists and the context survives
the hop, so the destination can be contextual. The contextual *landing experience* — a
marketplace view that visibly preserves "from this Signal" — is not built on either client.

## 32. PulseDrop integration

`relationship.PULSEDROP_CURATED` is reserved and unimplemented (item 7).
`PULSEDROP_ENABLED` is off everywhere in production.

The finding worth carrying forward: **the two curators share no viewer-level frequency
ledger.** PulseDrop counts publications per *platform*; commerce_discovery counts
impressions per *viewer*; nothing sums them. So if both were on, neither would see the
other's exposures, and a viewer could be shown the same product by both without either
ledger noticing. Hence the §16 ordering constraint: do not enable PulseDrop in the same
window as the reachability stage.

## 33. Sponsored-content handling

`relationship.SPONSORED` is reserved and **not servable**. Nothing in this package can
serve a paid placement, and the brief's central constraint — *without turning PulseSoc into
an advertising feed* — is currently enforced by the absence of a mechanism rather than by a
policy. That is the strongest possible enforcement and it should be a conscious decision to
give it up.

## 34. Search integration

**Not built.** No indexing hook, no query-time commerce discovery.

## 35. Semantic/vector infrastructure

**None.** Token overlap in `ranking._tokens`, and category paths in `taxonomy`. No
embeddings, no vector store, no ANN index.

## 36. Open-source tools evaluated

For retrieval: none adopted. The honest reason is that the defect list this audit produced
was not "our similarity function is weak" — it was "70.8% of the catalogue cannot be
retrieved at all", "the cap counts nothing", and "fatigue does not escalate". A vector
index fixes none of those and would have hidden all three behind better-looking results.

For measurement: `coverage` was **evaluated and rejected**, and the reasoning is in
`scripts/protection/audit_commerce_discovery_failsoft.py`'s own docstring — a measurement
that requires one more install than the suite is a measurement nobody re-runs. The audit
uses `sys.settrace` with a filter that gives line tracing only to the audited files.

## 37. Dependencies adopted and why

**Zero new dependencies.** No line added to any `requirements*.txt` or `package.json`.

## 38. Dependencies rejected and why

`coverage` (item 36). Any vector/embedding library (item 36). Any vision model (item 10) —
the gap is not perception, it is that no post↔product relation exists to perceive into.

## 39. Cache architecture

**None added.** Every read is per-request. Item 50 is why that is currently defensible and
item 63 is why it will not stay that way.

## 40. Worker/event architecture

No new worker. Events are synchronous on the request path:
`POST /api/commerce-discovery/events/{impression,engagement,feedback}` and
`POST /api/commerce-discovery/explain/<placement_id>`. `events.py`, 603 lines.

## 41. API contracts

Six endpoints, all authenticated, all POST except `marketplace/modules`:

| endpoint | method |
|---|---|
| `/api/commerce-discovery/<surface>` | POST |
| `/api/commerce-discovery/marketplace/modules` | GET |
| `/api/commerce-discovery/events/impression` | POST |
| `/api/commerce-discovery/events/engagement` | POST |
| `/api/commerce-discovery/events/feedback` | POST |
| `/api/commerce-discovery/explain/<placement_id>` | POST |

Error shape: every failure carries the code in **both** `error_code` and `error`. That is
not redundancy — `pulseApi` reads `error_code` and ignores `code`, so a backend answering
only one collapses every error state to *generic* on the client.
`test_the_request_layer_fails_soft_correctly.py` pins both keys.

## 42. Inventory invalidation

**Not built.** `eligibility` checks price resolvability and cover existence at serve time;
it does not check stock. A sold-out product can be served, and the buyer finds out on the
product page. Item 62.

## 43. Analytics

`metrics.py` (616 lines): `summarize`, `from_events`, `from_state`, `observe`,
`observe_sources`, `alerts`. Repetition concentration, repeat rate, cross-surface rate,
adjacent repeats — with thresholds behind the eight new `COMMERCE_DISCOVERY_REPETITION_*`
and `COMMERCE_DISCOVERY_TREND_*` env keys (all declared in `.env.example`, which the
environment-contract gate requires).

## 44. Attribution

**Not built, and do not call what exists attribution.** Events are recorded; nothing joins
an impression to an order. The wider platform position is the same: `analytics_events` holds
31,830 rows nothing reads, prod has 111 event/audit tables with no shared envelope, and the
gap is a read layer rather than ingest.

## 45. Experimentation

**Not wired.** `PulseExperiments` is shipped but inert — imported by nothing, registry
empty. No commerce discovery experiment is defined, and no arm assignment reaches this
package.

## 46. Privacy

Viewer signals (interests, followed sellers) are read per request and never persisted into
the placement row. The one real privacy defect found was the **outbound** direction:
`seller_risk_score` — an internal risk assessment of a *named third party* — was being
handed to every buyer's device. §18.6. It is still live until the strip ships.

Personalisation is switchable per viewer (item 8); shop surfaces are exempt from the social
opt-out by design.

## 47. Security

- `_require_user` on every endpoint.
- **Event-route errors do not leak their message.** `_event_route` answers 400 with a code
  or 500 with a fixed string; the test plants
  `no such column: marketplace_sellers.internal_risk_note` in the exception and asserts the
  response does not contain it. Mutating the handler to interpolate `str(exc)` turns that
  test red — which is how the handler was proved reachable at all (§22).
- `PIPELINE_ONLY_FIELDS` + `_buyer_safe` as an **allowlist**, replacing a denylist. The
  denylist is *how* the leak happened: a field added to the pipeline was exposed by default.
- No raw SQL built from request input.

## 48. Abuse prevention

Rate limiting on the serve route (empty + silent, not 429 — item 52). Per-viewer caps and
fatigue bound how much commerce any session can be shown. There is **no** seller-side abuse
control: nothing stops a seller gaming category paths to widen reachability, and item 20's
loosened diversity caps make that cheaper than it will be later.

## 49. Accessibility

**Not verified.** No screen-reader pass, no contrast audit on the commerce rows, no
reduced-motion handling on the carousel. It is untested, not known-good.

## 50. Performance

`scripts/measure_commerce_suitability_cost.py` (382 lines) exists because a gate on the
feed path needs a number, not an assurance. The gate is string work over a dict already in
memory — no query, no network — and it runs once per post in a page.

Retrieval is per-request with no cache (item 39). The known cost centre is `_listing_stats`,
which reads per listing; item 51.

## 51. Observability

`observe` / `observe_sources` / `alerts` in `metrics.py`, plus the two fail-safe log lines
that items 52–53 are about.

**One open decision, written up in §16 of the evidence report rather than taken here.**
Both `_listing_stats` handlers log at `LOGGER.debug`, so a stats outage is invisible in
production — and its effect is not small: with no stats every listing looks unproven at
once, which re-ranks every card on all six surfaces simultaneously. `LOGGER.warning` is a
one-word change but risks a log flood on the hottest read in the feature; warn-once-per-
process is the middle option. Which is right depends on which dashboards exist, and a guess
about observability is how `debug` got there.

## 52. Failure behavior

**Fail-soft throughout, by §82: the post must still render if commerce fails.** Three
nested layers:

1. `engine.serve` (`engine.py:167`) — logs `COMMERCE_DISCOVERY_SERVE_FAILED`, returns `[]`.
2. `commerce_discovery_serve` (`routes:654`) — logs `COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED`,
   returns `{"ok": true, "placements": []}` with **HTTP 200**.
3. `api_pulse_feed`'s annotation guard (`bot.py:90531`) — logs
   `PULSE_FEED_COMMERCE_SUITABILITY_FAILED`, leaves the feed exactly as it was. Separate
   from the handler's own `except`, which answers 503: a commerce annotation failing must
   not take the feed down.

That is correct availability behaviour and it stays. It is also **exactly** why item 53
exists: at layer 2, three unrelated conditions — unknown surface, rate-limited client, total
crash — are one response, and that response says `ok: true`.

`audit_commerce_discovery_failsoft.py` inventories all of it: **74 fail-soft handlers across
19 files**, each classified `exercised` / `never` / `silent`. It exits 0 whatever it finds,
deliberately — a `never` handler is a question ("can this happen?"), and §11a argues some
answers are legitimately no. Making it a gate would buy tests for unreachable branches.

## 53. Automated tests

**763 tests in `tests/commerce_discovery/`** (27 test files plus `conftest.py`, 10,074
lines) + 663 in
`tests/protection/`. All files registered in `config/ci_test_manifest.json`, which is
default-deny and runs one process per file.

The count is not the point. This is:

> **48 of this report's own tests were green against an engine that could not run.** (§19)
>
> **29 more were green against a dead route — and they were the suitability tests.** (§21)

Mutating the route handler's first line to raise `TypeError` left **37 tests green**, and
among them the bereavement-refusal tests that carry this mission's central ethical promise.
Those tests already had a second, stronger assertion written specifically against late
refusal — `assert not serve.called` — and **a crash before retrieval satisfies it more
thoroughly than a real refusal does.** A stronger assertion in the same direction is still
the same direction.

The fix is a conftest guard that fails any passing test during which a watched fail-safe
swallowed an exception. It watches both loggers by *name*, and it has to, for two
independent reasons: `COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED` does not `startswith`
`COMMERCE_DISCOVERY_SERVE_FAILED` (the words diverge right after `SERVE_`), and
`services.commerce_discovery_routes` is a *sibling* of `services.commerce_discovery.engine`,
so log propagation never carries one to the other's handler.

Mutation result: **48 green / 649 → 77 green / 620**, with zero production change (blob
`d5c01ccf` byte-identical either side).

Harnesses, all in `scripts/protection/` and all committed:
`audit_commerce_discovery_failsoft.py`, `measure_commerce_discovery_reachability.py`,
`prove_commerce_discovery_fatigue.py`, `prove_commerce_discovery_reachability.py`,
`prove_commerce_discovery_signal_defects.py`, `prove_commerce_discovery_sources.py`,
`prove_commerce_discovery_value_tiers.py`.

**The transferable finding, stated for whoever reads this next: both gaps were found by
mutation and neither was found by reading.** §19's own list of four uncovered paths,
assembled by reading the code carefully, did not include either of the two that mattered.

## 54. Anti-repetition results

`test_repetition_is_observed.py` (+372) and `prove_commerce_discovery_fatigue.py` (+358).
Fatigue now escalates (§5); the product cap now counts (§4).

## 55. Load-test results

**Not run.** No load test exists for this package. `measure_commerce_suitability_cost.py`
measures the gate's per-post cost, which is the one hot path this mission added — but that
is a microbenchmark, not a load test, and calling it one would be the overclaim §14 exists
to prevent.

## 56. Visual verification

**Not done.** No device QA, no screenshots. The protection policy in this repo is explicit
that static checks do not replace device QA for livestream, push, checkout or uploads, and
a commerce row in the feed belongs on that list.

## 57. Mobile/web parity verification

**Cannot be verified: there is no web implementation** (item 28). Parity is not failing —
it is undefined.

## 58. Feature flags

`COMMERCE_DISCOVERY_ENABLED` (default true), `COMMERCE_DISCOVERY_PERSONALIZATION`,
`PULSEDROP_ENABLED` (off), plus eight new `COMMERCE_DISCOVERY_REPETITION_*` /
`COMMERCE_DISCOVERY_TREND_*` tuning keys, all declared in `.env.example` as the
environment-contract gate requires.

**`COMMERCE_DISCOVERY_DISABLED_SURFACES` is the per-surface kill switch** (§23). A comma- or
space-separated list of surface names, validated against `schema.SURFACES`, read per request;
a named surface returns an empty placement list, which every client already renders as no
commerce unit. Checked at the top of `engine._serve`, so a disabled surface costs one
environment read and zero queries — and the master switch became free the same way, having
previously paid for `schema.ensure_schema` and a cursor in order to say no.

Until §23 there was none, and that absence is what shaped item 59: turning discovery off on
one surface meant a code change, a review and a Railway deploy, and Railway variables only
reach a container at boot.

**One sharp edge, deliberate.** An unrecognised name is *dropped*, not treated as "disable
everything", so a typo leaves that surface **serving**. The alternative turns one typo into a
platform-wide commerce outage, and nothing can guess whether `post-detail` meant `post_detail`.
The mitigation is a `COMMERCE_DISCOVERY_UNKNOWN_DISABLED_SURFACE` warning naming the ignored
token and the valid set, once per distinct value. **Check the logs after setting this variable.**

It is a reversibility switch, not an experiment framework: on/off per surface, no per-viewer
or percentage rollout (item 45).

## 59. Rollout status

**Not rolled out. Not pushed. Not merged.** Committed on a local worktree branch, and
**22 commits behind `origin/main`** — main takes roughly 60 commits/day from parallel
sessions, so that gap grows while this sits.

The recommendation, from §16 and unchanged: **do not ship this as one change.** Four stages,
each independently reversible:

1. **The payload strip (§18.6) first, alone.** It is the only one-way-safe change here:
   `_buyer_safe` exclusively *removes* keys, so no client can begin receiving something it
   did not receive before, and nothing in `mobile-native/` or `templates/` reads either
   field. Waiting is not neutral — until it ships, every card hands the buyer an internal
   risk assessment of a named store.
2. **Cap enforcement (§4) + fatigue escalation (§5).** Both only ever show *less*.
3. **Reachability (§6).** Highest impact: 70.8% of a large catalogue goes from never-shown
   to shown. Watch seller-level impression distribution; this is the stage that looks like
   a bug to whoever is watching the graphs.
4. **Multi-source retrieval (§7), behind the personalisation flag.**

The `relationship` column is additive, invisible, and not on the wire in either direction;
it can ship in or between any stage.

Two hard constraints: **do not enable PulseDrop in the same window as stage 3** (item 32 —
neither curator counts the other's exposures, so you could not attribute a move in
impression distribution), and note that the `post_detail` change removes the commerce card
from **10.0% of live posts (207 of 2,070)**.

**Each stage can now be staged behind `COMMERCE_DISCOVERY_DISABLED_SURFACES`** (item 58,
§23), which changes the character of this plan: a stage that looks wrong on one surface can
be stopped on that surface in the time it takes a variable to take effect, instead of
requiring a revert that un-ships it everywhere. Stage 3 in particular — the one that "looks
like a bug to whoever is watching the graphs" — is much cheaper to attempt when reels can be
switched off without touching feed. The staged order above is unchanged, but it no longer has
to be sized against what we are willing to un-ship by deploying.

**The rollout decision is still the product owner's, not mine.** It changes what feed, reels,
post-detail, Messenger, marketplace and product-page users see. The switch makes it
reversible; it does not make it mine.

## 60. Rollback procedure

Per stage: stage 1 is a one-line restoration of `serialize(row)`; stages 2–3 are flag-free
code reverts of single modules; stage 4 is `interests=()` + `followed_sellers=()`, a
two-line change that reproduces the previous behaviour exactly rather than requiring a
revert.

The schema column needs no rollback — it is nullable with no default, and `NULL` is already
a meaningful value (item 6).

`git revert` of the whole branch is safe: no data migration, no backfill, nothing written
that a previous version cannot read.

## 61. Files/components/services changed

59 files, +16,859 / −218 against the merge base — this document included, which is why the
figure moves when it is written. `git diff --stat $(git merge-base HEAD origin/main)..HEAD`
regenerates it. Breakdown:

- **`services/commerce_discovery/`** — 16 modules touched, 4 of them new (item 3).
- **`services/commerce_discovery_routes.py`** — +322.
- **`bot.py`** — +16 (item 3; the whole change is item 5's annotate call and its guard).
- **`tests/commerce_discovery/`** — 20 files, 17 new; +9,640 total lines in the directory.
- **`scripts/protection/`** — 6 new harnesses; **`scripts/`** — 1 (`measure_commerce_suitability_cost.py`).
- **`mobile-native/src/`** — 10 files (items 26, 27, 29).
- **`config/ci_test_manifest.json`** — +16 (every new test file; the gate is default-deny).
- **`.env.example`** — +62 (item 58; the env-contract gate requires every new `os.getenv`).
- **`docs/commerce/`** — +1,519 evidence report, +837 this document.

## 62. Known limitations

1. **No web commerce discovery at all** (28).
2. **No creator tagging**, because no post↔product relation exists anywhere (7, 30) — and
   with it `COMPLEMENTARY` and `PULSEDROP_CURATED`.
3. **No multimodal understanding.** "Shop this look" is text matching (10).
4. **No inventory check at serve time.** A sold-out product can be served (42).
5. **No attribution**; events are recorded and never joined to an order (44).
6. **No experimentation wiring** (45).
7. **No per-viewer or percentage rollout.** The per-surface kill switch (58, §23) makes the
   rollout reversible, not measurable — there is no way to serve commerce to 5% of viewers.
8. **No load test, no visual/device QA, no accessibility pass** (49, 55, 56).
9. **The two curators share no viewer-level frequency ledger** (32).
10. **Diversity caps are loosened to a one-seller catalogue** (20) — correct now, wrong
    later, and nothing will tell you when it flips.
11. **`_listing_stats` fails invisibly** (51).
12. **The payload leak is still live in production** until stage 1 ships (46, 59). Of
    everything on this list, this is the one with a cost that accrues while you read it.

## 63. Remaining technical debt

- **No shared canonical product card on mobile** (27) — four surfaces, four renderers.
- **`ranking.py` has 4 of 5 fail-soft handlers unreached**, and **12 handlers repo-wide are
  `silent`** (neither log nor re-raise). All coercion guards, all deliberately left:
  §11a's argument is that a handler guarding an impossible input *should* survive mutation,
  and zero survivors is not the goal.
- **`DROP_CODES` under-reports** (§12, unchanged).
- **`diversity_bonus` 0.07 is pinned in a test** — a magic number with no derivation.
- **No cache anywhere** (39).
- **No migration framework**, so item 6's column is hand-rolled idempotent DDL like the
  other ~550 tables.
- **The suitability word lists will need review by someone who is not an engineer.** Every
  entry has a stated membership test and the tests pin the edge cases (`wake`, `treatment`,
  `ill`), but a list of grief terms curated by the person who wrote the matcher is a single
  point of judgement on the most sensitive surface in the product.

## 64. Recommended next evolution

In this order, and the order is the recommendation:

1. **Ship stage 1.** The payload leak is live. Everything else on this list can wait; that
   cannot, and it is the cheapest change in the report.
2. **Build the post↔product relation table.** It unblocks creator tagging, complementary
   products, PulseDrop curation, and any meaningful "Shop this look" — four of the brief's
   named experiences behind one table, modelled on `pulse_content_music`. This is the
   highest-leverage build remaining and it is a day of work, not a quarter.
3. ~~**Add a per-surface kill switch** before stage 3, not after.~~ **Done — §23**, and it
   moved to the top of this list from below it once it was clear that the item making the
   rollout expensive was cheaper than the rollout. It is left struck through rather than
   deleted because the ordering is part of the recommendation: the correct first move was not
   the biggest item, it was the one that made a blocked decision cheap.
4. **Take the `_listing_stats` observability decision** (51). One word, and it is the
   difference between noticing a global re-ranking and not.
5. **Then web** — inside the planned `docs/web-rebuild/` work, not beside it, and as a grid
   rather than a stretched carousel.
6. **Then a shared viewer-frequency ledger** before PulseDrop is ever enabled alongside this.

Attribution, experimentation and vector retrieval are all further out than they look,
because each of them measures or refines a system whose reachability, capping and fatigue
defects were only just fixed — and every one of those four defects had passing tests over it.

---

## The thing I would most want the next engineer to know

Not a defect. A method.

Four defects in this feature had green tests over them. The cap that counted nothing, the
fatigue that did not escalate, the 70.8% of the catalogue that could not be retrieved, and
the `diversity_bonus` that appeared in the payload and influenced no ordering — all covered,
all passing, all wrong. Then 48 tests turned out to be green against an engine that could
not run, and 29 more against a dead route, and those 29 were the ones certifying that
PulseSoc does not put a shopping carousel next to a bereavement.

Every one of those was found by **breaking the code and checking that something went red.**
None was found by reading, including the ones I went looking for by reading, in a section
specifically about what I had missed.

`scripts/protection/` has seven harnesses in it now. They are slower than reading and they
are the only part of this delivery I would defend without qualification.
