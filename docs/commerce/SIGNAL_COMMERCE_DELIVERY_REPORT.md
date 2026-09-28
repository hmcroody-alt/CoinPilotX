# SIGNAL COMMERCE — final delivery report (§100)

This answers the 64 items §100 of the brief asks for, in its order and with its numbering.

**Read this with `PULSE_COMMERCE_INTELLIGENCE_REPORT.md`, not instead of it.** That document
is the evidence: 24 sections, every defect with the measurement that found it and the
mutation that proved the test could fail. This one is the index — each item below answers
the question, names the file, and points at the section that proves the answer. Where an
item is not built, it says so in the first sentence.

**Status: nothing here is deployed.** Branch `commerce-discovery-audit` in a worktree, not
pushed, not merged. It is **23 commits ahead of and 32 behind `origin/main`** as of writing,
which is a rollout input and not a footnote — see item 59. That gap grows on its own:
`origin/main` takes roughly 60 commits a day from parallel sessions.

### How to regenerate every figure in this report

```
cd <worktree>
.venv/bin/python -m pytest tests/commerce_discovery -q          # 858 pass
.venv/bin/python -m pytest tests/protection -q                  # 663 pass
python3 scripts/protection/measure_commerce_discovery_reachability.py
python3 scripts/protection/audit_commerce_discovery_failsoft.py
python3 scripts/protection/prove_commerce_discovery_sources.py
python3 scripts/protection/prove_commerce_discovery_fatigue.py
python3 scripts/protection/prove_commerce_discovery_value_tiers.py
python3 scripts/protection/creator_tagging_mutation_matrix.py    # 20/20 killed
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
in any form**, a **payload leak** closed, the **creator-tagging write path** (the one relation
that genuinely did not exist anywhere), and — the largest single body of work — the **mutation
harnesses and fail-soft audit** that established the tests can actually fail. Two things the
brief asked for remain **not built**: web, and the composer UI that would let a creator
actually use tagging.

---

## 1. Repository architecture discovered

Flask monolith `bot.py` (~120k lines, ~1,538 routes) over `services/` (239 modules).
Commerce discovery is a package, `services/commerce_discovery/` — 19 modules, 8,559 lines —
plus `services/commerce_discovery_routes.py` (1,006 lines, seven endpoints), which sits
*outside* the package
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

Five modules that did not exist:

| module | lines | what it is |
|---|---|---|
| `suitability.py` | 614 | the content→commerce permission gate (items 11, 12) |
| `relationship.py` | 309 | provenance vocabulary: *why* is this product here (items 14, 7) |
| `tagging.py` | 291 | the creator's own statement about what is in their post (item 7) |
| `content.py` | 219 | server-side context derivation from a post (item 9) |
| `taxonomy.py` | 88 | category-path handling, shared by dedup and diversity |

`bot.py` is **+212 / −0** across the branch, in three unrelated places, and it is worth
saying what they are rather than quoting a line count:

* `_cd_suitability.annotate` on the feed response (`bot.py:90626`) — 16 lines, 10 of them
  the comment explaining why it carries its own `except`.
* The composer write path: `PULSE_PRODUCT_TAG_REQUEST_LIMIT` (`bot.py:45046`),
  `pulse_attach_products_to_content` (`:45049`), `pulse_product_tag_ids_from_payload`
  (`:45114`), and the call sites in the post and reel creation routes. This is the half of
  item 7 that has to live in `bot.py`, because the thing that knows a post was just created
  is the route that created it.
* The `pulse_content_products` DDL in `init_db` (`bot.py:120360`). Schema for this table is
  owned by `bot.init_db`, beside `pulse_content_music`, rather than by this package's
  `schema.ensure_schema` — see §6 for why the writer, not the reader, owns it.

An earlier version of this report claimed the `annotate` call was the *only* `bot.py` change
in the branch. That was true when it was written and stopped being true when item 7 landed.

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
                        │ buyer_safe   ← strips PIPELINE_ONLY  │ §18.6: the leak
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

Since that was written, one table has been added: `pulse_content_products`, the post↔listing
relation item 7 reported did not exist anywhere. It is owned by `bot.init_db` beside
`pulse_content_music` rather than by this package's `schema.ensure_schema`, because the
writer is the composer and making a post save depend on the discovery package's schema guard
would point that dependency the wrong way. Nothing in this package's own schema changed.

The ALTER-after-CREATE ordering and its PostgreSQL translation are pinned by tests
(`test_schema_durability.py`) rather than argued for in prose, because the repo has no
migration framework: `bot.init_db()` creates ~550 tables imperatively and every change has
to be idempotent.

## 7. Creator product-tagging architecture

**Built** (`a89f741b1`), after this report first said it was the largest single gap. Not
deployed — see item 59.

`services/commerce_discovery/tagging.py`, the write path
`bot.pulse_attach_products_to_content`, and `pulse_content_products` in `bot.init_db`.
`CREATOR_TAGGED` has left `UNIMPLEMENTED_RELATIONSHIPS`, so `assert_servable` now permits a
value the system can actually produce.

It follows `pulse_content_music` as the earlier version of this section predicted — same
polymorphic `(content_type, content_id)` key, written by the composer, resolved by a reader —
with two departures:

* **`seller_user_id` is stored** and re-checked against the live listing on every read, in
  the JOIN rather than in Python. It is the seller the tag was *authorised against*; a
  listing that changes hands afterwards carries a permission its new owner never granted,
  and the read drops it rather than serving it.
* **Nothing else is snapshotted.** Music snapshots a licence because a stale song is still
  the song. Price, title and availability are read live from `marketplace_listings` on every
  serve, because a stale price is not a stale copy of the truth, it is a lie to a buyer.

**A creator may only tag a listing they own** (`REFUSED_NOT_OWNER`). Tagging someone else's
product is affiliate marketing: it needs a commission model, a disclosure obligation that
differs by jurisdiction, and a decision about whether PulseSoc takes a cut. None of those are
engineering decisions and all are much harder to withdraw than to delay, so the check is
ownership and `AUTHORITY_OWNER` is recorded on every row — the day a second authority exists,
rows written under this one are still distinguishable.

In the engine the tag is a **precedence tier in `_select`, not a ranking weight**.
`ranking.score_listing` normalises `score` by the sum of positive weights, so adding a weight
would have shifted every relevance threshold in the system. It is taken ahead of the
relevance floor (relevance to the content is exactly what the tag establishes by fiat, and a
tagged product scoring below the floor is usually a brand-new listing the scorer has no
signal for) and ahead of the per-seller diversity cap (every tagged row is one seller *by
construction*, so the cap would silently truncate every creator's tags to two and look like a
composer bug). It is **not** exempt from `eligibility`, `promotion.assert_unpaid`, the surface
budget, `MAX_TAGGED_PER_CONTENT`, or the forward-feeding counts — so a two-slot surface filled
by tags serves no inferred rows at all.

Evidence report §24. 64 tests, and all 12 controls are proven observed by
`scripts/protection/creator_tagging_mutation_matrix.py`.

`COMPLEMENTARY` remains unimplemented and genuinely needs a product↔product relation that
does not exist. **`PULSEDROP_CURATED` does not** — see the correction at item 30.

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

Route-level: `_require_user` (`commerce_discovery_routes.py:157`). All seven endpoints are
authenticated; none is public. The seventh, `/taggable-products`, is the only *creator*-side
one, and it is signed-in-only for the same reason as the rest plus one of its own: it reads
out a named seller's own catalogue including listings that are refused, with the refusal
reason attached (§25 of the evidence report).

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
`engine.PIPELINE_ONLY_FIELDS` and stripped by `buyer_safe` before the response.

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

**Server side built, client side not.** The relation table now exists (item 7), so the
sentence this section used to carry — "there is nothing to build a workflow over" — is no
longer true.

What a client can do today: send `product_listing_ids` (or `listing_ids`, or `product_ids` —
three keys because three clients, and every id is ownership-checked regardless of which one
carried it) on post create or reel create. `bot.pulse_attach_products_to_content` accepts up
to `PULSE_PRODUCT_TAG_REQUEST_LIMIT` ids, returns `{ok, attached, refused}` with a per-id
reason, and cannot lose the post: on the post path the row is already committed by the time
tags are written, so the attach runs on its own connection inside `try/except/finally` (§82);
on the reel path it runs inside the reel's own transaction, where rolling back with a failed
reel is the correct outcome. A reel that is shared to the feed gets its products attached to
the mirror post as well, because the mirror row is what makes a reel's products actually
appear — the same thing `pulse_attach_music_to_content` does for a track.

**What is missing is the UI**, and it is now missing more narrowly than when this line was
first written. No composer screen sends any of the three keys yet, and the multipart/form post
path does not carry them at all — only the JSON path does. So the feature is still reachable
by an API client and by nothing a user can tap. That is the honest status and it is
deliberately not hidden behind "built".

What changed is that the *server* side of the picker now exists too.
`GET /api/pulse/commerce/discovery/taggable-products` answers the question a composer has to
ask before it can render anything — which of my listings are there, and which of them will
actually be shown if I tag them — and it answers the second half explicitly rather than by
filtering, so the creator is told *why* a product is refused instead of watching it silently
not appear. §25 of the evidence report is the whole argument, including why a tag can succeed
and still never serve. The remaining client work is three named things:
`product_listing_ids` added to `createPost`'s field whitelist in
`mobile-native/src/api/feed.ts:290` (which drops unlisted keys silently), a
`taggableProducts.ts` client, and the picker itself in `HomePulseComposer.tsx` — where the
three publish paths already thread `music_track_id` as the precedent to copy.

Refusals a client must render: `not_listing_owner` (the common one, and it is the system
working — logged at info, not warning), `listing_not_found`, `too_many_products`,
`invalid_reference`, `unknown_content_type`.

## 31. See All contextual Marketplace experience

Partial. `GET /api/commerce-discovery/marketplace/modules` exists and the context survives
the hop, so the destination can be contextual. The contextual *landing experience* — a
marketplace view that visibly preserves "from this Signal" — is not built on either client.

## 32. PulseDrop integration

`relationship.PULSEDROP_CURATED` is reserved and unimplemented. `PULSEDROP_ENABLED` is off
everywhere in production.

**Correction.** Earlier drafts of this report grouped `PULSEDROP_CURATED` with
`CREATOR_TAGGED` and `COMPLEMENTARY` as "blocked on a table that does not exist." That was
wrong, and it stayed wrong here for one increment after `relationship.py` itself was
corrected — which is the more useful lesson: fixing the claim at its source does not fix the
copy of it in the report, and a report is exactly where a wrong blocker survives longest.

`pulsedrop_publications` (`services/pulsedrop/schema.py:86`) already carries `(surface,
listing_id, post_id, state, seller_user_id, category)` — every column a retrieval source
needs. `PULSEDROP_CURATED` is a **read away, with no new table**, which is what brief §4
("do not create parallel commerce infrastructure where canonical systems already exist")
asks for. It is not built because of the frequency-ledger finding immediately below, not
because of a missing relation.

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
handed to every buyer's device. §18.6.

Precisely where that stands, because "fixed" and "live" are different claims: the strip is
**built and tested on this branch** (`engine.buyer_safe`, applied in `_payload`). It is
**not in production.** `git show origin/main:services/commerce_discovery/engine.py` contains
no reference to `buyer_safe`, and production runs `main`. So every card served right now
still carries `seller_risk_score` and `candidate_source` to the device. The fix is one merge
away and it is not mine to merge.

Personalisation is switchable per viewer (item 8); shop surfaces are exempt from the social
opt-out by design.

## 47. Security

- `_require_user` on every endpoint.
- **Event-route errors do not leak their message.** `_event_route` answers 400 with a code
  or 500 with a fixed string; the test plants
  `no such column: marketplace_sellers.internal_risk_note` in the exception and asserts the
  response does not contain it. Mutating the handler to interpolate `str(exc)` turns that
  test red — which is how the handler was proved reachable at all (§22).
- `PIPELINE_ONLY_FIELDS` + `buyer_safe`, applied in `_payload` before the marketplace
  serializer runs. An earlier draft of this line called it "an allowlist, replacing a
  denylist." That is wrong twice over, and wrong in the flattering direction, so it is worth
  correcting rather than quietly editing: it is a **denylist**, and it does not replace
  anything — it runs *in front of* the serializer's own denylist. The engine's docstring
  argues the choice: the serializer's list is shared by every marketplace endpoint, and
  widening it from inside this package would change payloads this mission never examined.
  The three columns named are ones this pipeline invented, so the strip belongs with the
  pipeline.

  What that means honestly is that the structural weakness survives. A denylist still
  exposes a new pipeline column by default, which is exactly how the original leak happened.
  `test_pipeline_columns_stay_server_side.py` is the compensating control — it re-reads the
  comprehension and fails if it inverts to an allowlist, and it parametrizes over
  `PIPELINE_ONLY_FIELDS` so each entry is separately proved absent from a real payload. A
  test is not a type system; a fourth pipeline column added without a matching entry ships.
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

`audit_commerce_discovery_failsoft.py` inventories all of it: **81 fail-soft handlers across
20 files**, each classified `exercised` / `never` / `silent`. It exits 0 whatever it finds,
deliberately — a `never` handler is a question ("can this happen?"), and §11a argues some
answers are legitimately no. Making it a gate would buy tests for unreachable branches.

## 53. Automated tests

**858 tests in `tests/commerce_discovery/`** (29 test files plus `conftest.py`, 11,554
lines) + 663 in `tests/protection/` — 1,521 in one run. All files registered in
`config/ci_test_manifest.json`, which is default-deny and runs one process per file.

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
   `buyer_safe` exclusively *removes* keys, so no client can begin receiving something it
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

63 files, +19,979 / −224 against the merge base — this document included, which is why the
figure moves when it is written. `git diff --stat $(git merge-base HEAD origin/main)..HEAD`
regenerates it. Breakdown:

- **`services/commerce_discovery/`** — 19 modules, 5 of them new (`content`, `relationship`,
  `suitability`, `taxonomy`, `tagging`); +3,953 / −160.
- **`services/commerce_discovery_routes.py`** — +488 / −5. Seven endpoints; the seventh
  (`/taggable-products`) is the creator's side and the only one that writes nothing while
  answering a question about writes.
- **`bot.py`** — +212 / −0: item 5's annotate call and its guard, item 30's composer write
  path on the post and reel create routes, and the `pulse_content_products` DDL.
- **`tests/commerce_discovery/`** — 29 files, 19 new; +8,890 / −17.
- **`scripts/protection/`** — 8 new harnesses, +2,216; **`scripts/`** — 1
  (`measure_commerce_suitability_cost.py`, +382).
- **`mobile-native/src/`** — +911 / −42 (items 26, 27, 29). **No composer work** — item 30.
- **`config/ci_test_manifest.json`** — +19 (every new test file; the gate is default-deny).
- **`.env.example`** — +76 (item 58; the env-contract gate requires every new `os.getenv`).
- **`docs/commerce/`** — +2,832 across both documents.

## 62. Known limitations

1. **No web commerce discovery at all** (28).
2. **Creator tagging has no UI** (7, 30). The server accepts tags, ownership-checks them,
   serves them ahead of the scorer, and now also tells a composer which of the creator's
   products will actually be shown (§25) — but no composer screen sends or reads any of it,
   and the multipart/form post path does not carry the ids at all. So the feature is
   reachable by an API client and by nothing a user can tap. `COMPLEMENTARY` is still
   genuinely blocked on a product↔product relation; `PULSEDROP_CURATED` never was (32).
   Related, and not fixed by the picker: a creator who tags an ineligible listing through
   the raw API still gets a clean `ok:true` and a product that never appears. The picker
   makes that knowable *before* posting; it does not make the write path warn about it.
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

1. **Ship stage 1.** The payload leak is live *in production* — the strip is written and
   tested here and absent from `origin/main`, so nothing about it has reached a device yet
   (§46). Everything else on this list can wait; that cannot, and it is the cheapest change
   in the report, because the code is already written and the remaining work is a merge.
2. ~~**Build the post↔product relation table.**~~ **Done — `pulse_content_products`, §24.**
   It unblocked creator tagging as predicted. Two corrections to what this item claimed,
   both worth keeping visible rather than editing away:

   * It claimed the table unblocked **four** named experiences. It unblocked one. PulseDrop
     curation never needed it (`pulsedrop_publications` already has every column — item 32),
     "Shop this look" needs multimodal understanding rather than a relation, and
     `COMPLEMENTARY` needs a product↔product relation which this is not. Counting a
     dependency four times is how one table comes to look like the highest-leverage build
     remaining.
   * "A day of work, not a quarter" was right about the table and wrong about the feature.
     The write path, the precedence tier, the stale-authorisation drop and 63 tests are the
     day. **The composer UI is not built and is the remaining majority of the user-visible
     work** (item 30).

   What replaces this item at position 2: **build the composer UI**, and while doing it
   decide whether the multipart/form post path should carry `product_listing_ids` too, or
   whether the client should always use the JSON path when tagging.

   The server half of that is now done and the item is smaller than it was.
   `GET /taggable-products` (§25) gives the picker its data, including a `serves` boolean and
   a `blocked_reason` per listing, so the screen does not have to re-derive eligibility and
   must not try. Three concrete things remain, all client-side: `product_listing_ids` in the
   `createPost` whitelist (`mobile-native/src/api/feed.ts:290` — an unlisted key is dropped
   with no error, which would present as "tagging silently does nothing"), a
   `taggableProducts.ts` client, and the picker in `HomePulseComposer.tsx`. The multipart
   question is still open and is a real decision, not a detail: a creator attaching an image
   *and* tagging a product is the ordinary case, not the exotic one.
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

It kept being true after that was written. The creator-tagging increment (§24) shipped with
11 controls and a mutation matrix over them, and **two of the 11 mutations survived the first
pass — both in tests written that same hour, by someone who had just finished writing this
paragraph.** One of them was a single line handing every surface the client's post id, and it
survived the entire 826-test package as it then stood, because the guard over it was a regex asserting the
*correct* assignment still existed. An addition defeats that; a behavioural assertion on the
value the engine actually received does not.

And once more after *that*. Writing the matrix down as a runnable script surfaced a control
that had no entry because it had no test: a post the server can no longer see must be
**refused**, not read as "no evidence about this post." There is no deletion cascade for
`pulse_content_products` — nor, it turned out on checking, for `pulse_content_music`, which
this report had previously cited as the precedent. The twelfth entry existed because writing
the other eleven down forced the question "what else is a control here?" in a form that prose
never had.

And once more after that, with the pattern now fully explicit. The `/taggable-products`
increment (§25 of the evidence report) landed **31 tests green on the first run**, which by
this point in the mission is a symptom rather than a result. Nothing was red, so there was
nothing to follow; I went looking for the survivor by reasoning instead, and found it —
`assert body["max_per_content"] == tagging.MAX_TAGGED_PER_CONTENT` puts the same number on
both sides of `==`, so hardcoding `5` in the route passed all 31. That is the
fixture-supplies-both-the-value-and-the-threshold shape, and it is the third time this
mission has produced it. **The matrix is twenty entries now**, eight of them from that one
increment, and `picker-hardcodes-the-cap` is the entry that came *before* its test rather
than after.

The same increment proved two existing controls stale, both loudly, which is the direction
you want: a gate assertion of `"tagging" not in source` over a whole module broke the moment
a second, legitimate reader of `MAX_TAGGED_PER_CONTENT` appeared in that file, and the same
test had been locating its `handler` node by taking the first one in the module — correct
only by accident until a third route added one. Neither was a wrong claim. Both were right
claims held in place by a proxy that could not survive the file growing.

This branch adds **eight harnesses** to `scripts/protection/` (which holds twelve in total).
They are slower than reading and they are the only part of this delivery I would defend
without qualification.
