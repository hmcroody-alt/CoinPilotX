# AGENT 6 — THE MATERIAL-CHANGE CONTRACT

Branch: `search-os/agent-06-crawl-distribution`
Base: `origin/main` @ `5bdf4e431`
Status: **contract frozen. Nothing implemented. Agent 0 approval required before any code.**
Consumer: Agent 8 (`docs/seo/02_open_web_distribution_contract.md` §H, on
`origin/search-os/agent-08-open-web`)

This document answers one question — *what is a material search change?* — and
deliberately does not build the thing that would emit one.

---

## 0. The headline, stated once

> **A material change is a transition in the distribution projection of a canonical
> entity. It is observed as a diff between the current eligible universe and the last
> durable observation of that universe. It is not a database write.**

That phrasing is forced by the codebase rather than chosen for elegance, and §4
explains why: in PulseSoc, *being public is a predicate, not a column*. There is no
row whose UPDATE means "this became public", so there is no write to hook. The
producer has to be a differ.

Everything else here follows from that one fact.

---

## 1. Existing event / outbox / job inventory

Method: enumerate `CREATE TABLE` statements in `bot.py`, filter to
event/queue/job/audit shapes, then count call sites per candidate.

### Tables that look like a bus and are not one

| Table | Shape | Verdict |
|---|---|---|
| `event_bus_events` | `channel, event_type, priority, status, trace_id, payload_json, created_at` | **one writer** (`bot.py:113449`), **zero readers**. Named a bus; is a write-only log. No entity key, no lease, no `run_after`, no attempt counter. Cannot be a delivery mechanism. |
| `analytics_events` | 31,830 rows in prod | ingest-only; `services/analytics_service.py` has zero call sites. Not a bus. |
| ~111 other `%event%`/`%audit%` tables | 49 non-empty, 62 empty | no shared envelope, no shared consumer. Agent 11 reached the same conclusion independently and declined to add a 112th. |

**Correction to this mission's premise:** the brief says Agent 11 "already found an
existing canonical commerce-discovery event envelope." Agent 11's report says the
opposite, in as many words — ~111 event/audit tables "with no shared envelope, no
shared schema, no shared consumer." I checked and Agent 11 is right. There is no
envelope to adopt. There *is* a durable job primitive, which is a different thing,
and it is the next table.

### The one primitive that already does the job

`pulse_jobs` (`bot.py:126230`), drained by `media_worker.py` which **is** in the
Procfile:

```
id, job_type, target_type, target_id, status, attempts, max_attempts,
error_message, run_after, created_at, updated_at, completed_at
```

It is the only candidate that is *entity-keyed* — `(target_type, target_id)` — which
is precisely the identity shape this contract needs. More importantly, the live code
around it already demonstrates every property Agent 8 asked for:

| Property Agent 8 requires (§H) | Where `pulse_jobs` already does it |
|---|---|
| durable | a table, not a list |
| atomic claim | `UPDATE … SET status='processing' WHERE id=? AND status='pending'` + `rowcount` check — `media_worker.py:1068`. Compare-and-swap; two workers cannot both win. |
| crash recovery | stuck `processing` rows older than 10 minutes return to `pending` — `media_worker.py:1111` |
| retry with backoff | `attempts`, `max_attempts`, `run_after` — `media_worker.py:700`, `:860` |
| coalescing per entity | enqueue guarded by `NOT EXISTS (… job_type=? AND target_type=? AND target_id=? AND status IN ('pending','processing'))` — `media_worker.py:1134`, `:1147` |
| multiple consumers, one table | `media_worker` filters `job_type IN MEDIA_JOB_TYPES` (`media_worker.py:79`) |

**Conclusion: do not build a queue.** `pulse_jobs` is the queue. Two real gaps, both
small and both recorded in §8 and §9: the dedupe is a racy `NOT EXISTS` rather than a
unique index, and there is no environment column.

### What does not exist anywhere

- No per-URL state. No table keyed on a canonical path; nothing stores what we last
  observed or last submitted about a URL. Agent 8 reached the same finding.
- No sitemap invalidation mechanism. Every sitemap is computed per request from live
  SQL. There is no cache to invalidate and therefore no existing dirty-marking to reuse.
- No lifecycle event for publication. See §4.

**Also note, because it misled me for a minute:** `CLAUDE.md` claims `media_worker`
is not in the Procfile. It is (`Procfile:6`), along with `ads_worker`, `alert_worker`
and `supplier_worker`. The whole design below depends on `pulse_jobs` actually being
drained in production, so this was worth checking rather than inheriting.

---

## 2. Material-change vocabulary

### A materiality vocabulary already exists. Adopt it, do not write a third.

`services/marketplace_listing_lifecycle.py:86`:

```python
MATERIAL_FIELDS = frozenset({
    "title", "description", "short_description", "category", "subcategory",
    "price_label", "currency", "cover_image_url", "gallery_json", "video_url",
    "product_type", "listing_type", "listing_metadata_json",
})

def requires_rereview(changed_fields: set[str]) -> bool:
    return bool(MATERIAL_FIELDS.intersection(changed_fields))
```

This is live, tested (`tests/marketplace/test_listing_rereview_reset.py`) and has
three real call sites (`bot.py:63798`, `:64040`, `:64162`). There is already a
*second*, unrelated `MATERIAL_FIELDS` in
`services/business_os/advertising/creatives.py:87`. A third would be the point at
which "material" stops meaning anything.

**The contract adopts `marketplace_listing_lifecycle.MATERIAL_FIELDS` as the content
dimension of listing materiality, and states the delta rather than redefining the set.**

The delta is exact and has one cause: `MATERIAL_FIELDS` is tuned for *moderation
re-review*, so it covers fields a seller edits and omits everything that changes
without a seller touching it. Search needs those too:

| Search-material, absent from `MATERIAL_FIELDS` | Why it is absent there | Why search needs it |
|---|---|---|
| availability / `quantity` | restocking must not re-trigger moderation | availability is a distribution transition (class 4) and a Merchant property |
| `status`, `approval_status` | they are the *output* of review | they flip indexability |
| seller approval, seller store name | a different table entirely | `public_sql()` joins them; they gate publication |
| canonical path | derived, never stored | a path change is a distinct transition (class 5) |

So: `search_material ⊃ MATERIAL_FIELDS`, and the extension is exactly the
eligibility dimension. Nothing in `MATERIAL_FIELDS` is non-material to search.

### The twelve transition classes

Each is a transition of the pair `(eligible, fingerprint)` for one canonical entity.
`PRIORITY` governs coalescing (§10).

| # | Class | Transition | Priority |
|---|---|---|---|
| 1 | `ENTERED_UNIVERSE` | not eligible → eligible | NORMAL |
| 2 | `CONTENT_CHANGED` | eligible → eligible, content fingerprint differs | NORMAL |
| 3 | `PRICE_CHANGED` | eligible → eligible, price component differs | ELEVATED |
| 4 | `AVAILABILITY_CHANGED` | eligible → eligible, availability component differs | ELEVATED |
| 5 | `CANONICAL_CHANGED` | entity's canonical path differs from last observation | **URGENT** |
| 6 | `INDEXABILITY_CHANGED` | directive flips index ↔ noindex while the URL still resolves | **URGENT** |
| 7 | `LEFT_UNIVERSE` | eligible → not eligible, cause unknown | **URGENT** |
| 8 | `LEFT_UNIVERSE_DELETED` | …because the entity is gone or soft-deleted | **URGENT** |
| 9 | `LEFT_UNIVERSE_PRIVATE` | …because visibility left `public` | **URGENT** |
| 10 | `LEFT_UNIVERSE_HELD` | …because moderation or approval withdrew it | **URGENT** |
| 11 | `MEDIA_ELIGIBILITY_CHANGED` | a public searchable media asset on an eligible page became unavailable | ELEVATED |
| 12 | `SOCIAL_PUBLICATION_CHANGED` | a Signal/Reel/Post entered or left eligible distribution | NORMAL |

Classes 8–10 are **refinements of 7, not alternatives to it.** Today only class 7 is
producible — see §13 — because the reason codes that would distinguish them do not
exist yet. The design must emit 7 and let 8/10 arrive later without a schema change.
A design that can only express the refined classes would be unimplementable today.

---

## 3. Non-material-change vocabulary

A change is **non-material** if it does not alter the distribution projection
(§5 fingerprint) of an eligible URL. Named exclusions, each of which is a real field
or mechanism in this repo:

- `updated_at` / `last_seen_at` / any audit timestamp moving on its own
- worker leases, `attempts`, `run_after`, `locked_at`
- `engagement_score`, view counts, likes, reactions, follower counts
- analytics and funnel rows (`analytics_events`, `conversion_funnel_events`)
- private moderation notes, internal confidence scores, admin-only state
- supplier-side metadata that does not reach the page: `variant_key` churn,
  supplier prose, `cover_attempts`, reconciliation bookkeeping
- cache warming, schema guard runs, health checks
- provider diagnostics and submission bookkeeping (our own records of talking to Bing)

**The rule that makes this enforceable:** the fingerprint is an allowlist, not a
denylist. A field absent from the fingerprint spec is non-material *by construction*,
so a new column added anywhere in the repo cannot silently become a search event.
The failure mode of an allowlist is a missed change; the failure mode of a denylist is
continuous churn. Agent 8's quota makes the second one much worse than the first.

**`engagement_score` is called out separately** because it is the one non-material
field that the current code lets leak into distribution. See §11.

---

## 4. Entity identity contract

### Why there is nothing to hook

`marketplace_listing_lifecycle` has no transition functions. It has `is_public(listing)`
and `public_sql(alias, seller_alias)` — a **predicate over current row state**,
evaluated per read. The same is true of posts: `pulse_public_entries` is a SQL
prefilter plus `search_visibility.content_eligibility(row)`.

So a listing becomes public when a conjunction flips, and the inputs live in at least
three tables:

```
public_sql()  =  seller approved        (marketplace_sellers)
              AND seller named          (marketplace_sellers)
              AND status released       (marketplace_listings)
              AND in stock              (marketplace_listings)
              AND priced                (marketplace_listings)
         AND discovery_visible_sql()    (users)
```

An `AFTER UPDATE` hook on `marketplace_listings` would therefore **miss** publication
caused by a seller being approved, by a store being named, by a supplier restock, or
by a discovery-visibility change on the `users` row. Those are not edge cases; seller
approval is the normal way a listing goes live.

This is the whole justification for a differ. **A row-level producer is not a
simplification of this design; it is a design that does not work.**

### Identity

| Entity class | Identity | Source of truth |
|---|---|---|
| `listing` | `marketplace_listings.id` | Agent 3 |
| `department` | department slug from `marketplace_seo.category_entries` | Agent 7 |
| `post` | `pulse_posts.id` | Agent 2 |
| `page` | the path itself (static, no row) | Agent 2 |

Forbidden as identity, each for a recorded reason:

- **`variant_key`** — 2,571 distinct values across 3,797 variants, not URL-safe,
  embeds prose. It is not a variant id. (Agent 3.)
- **supplier SKU / supplier product id** — unstable across reconciliation runs.
- **a caller-supplied URL** — see §5.
- **a title or slug** — mutable; a rename would read as delete + create.

---

## 5. URL resolution contract

```
ENTITY CLASS + ENTITY ID
        ↓   owner-supplied resolver (never a caller-supplied string)
CANONICAL PATH                      marketplace_seo / pulse post path / static path
        ↓   search_visibility.canonical_url(path)
ABSOLUTE CANONICAL URL              origin is hardcoded, not derived from the request
        ↓   search_visibility.classify / is_indexable / sitemap_eligible(path, row)
AGENT 2 INDEXABILITY TRUTH
        ↓   Agent 6: in a sitemap source? past the per-row gate?
DISTRIBUTION ELIGIBILITY
        ↓
OPEN-WEB CANDIDATE                  → Agent 8
```

Non-negotiable rules at this boundary:

1. **An event carries an entity reference, never an authoritative URL.** The URL is
   re-resolved at consumption time. An event is a hint to look, not a fact to act on —
   Agent 8 §I-J rule 1, which this contract adopts verbatim.
2. **Reject before resolving, not after.** A path must begin with a single `/`.
   Explicitly rejected: `https://evil.example/x`, `//evil.example/x`, `../`,
   anything with a host component, anything not rooted.
   Agent 8 proved why: `canonical_url()` normalises by *prepending* the origin, so
   `https://evil.example/x` becomes `https://pulsesoc.com/https://evil.example/x`,
   which passes a naive host assertion and is a guaranteed 404. The hardcoded origin
   means this is not a host leak — it is a malformed-input hole, and the input that
   reaches it is seller- or importer-supplied data stored in a field a path was
   expected from, which is exactly the shape of data the marketplace carries.
3. **Every candidate URL must equal `CANONICAL_ORIGIN + path`.** Not "be on the right
   host" — be byte-identical to the resolver's output. IndexNow `422`s a whole batch
   for one foreign URL.
4. **Query strings only from the resolver's own allowlist.** `?category=` is canonical
   for departments (self-canonical, own `h1`/`title`/meta). `?utm_*`, `?page=`,
   `?q=` never are. The allowlist already exists: `_CONTENT_QUERY_PARAMS`
   (`search_visibility.py:555`).
5. **Sitemaps and feeds are never candidates.** `sitemap_eligible('/sitemap.xml')`
   returns `True` with reason "public content" — defensible for that function's
   purpose and wrong as a submission input. Agent 8 withholds them explicitly rather
   than relying on no source class supplying them.

---

## 6. Current universe vs change batch

Frozen, in Agent 8's terms.

| | ELIGIBLE UNIVERSE | CHANGE BATCH |
|---|---|---|
| Question | what is eligible *right now* | what *materially changed* since the last durable observation |
| Computable from | current state alone | current state **and** a durable prior observation |
| Needs memory | no | **yes** |
| Size today | 148 URLs | a handful on a normal day; 0 on a quiet one |
| Produced by | the four sitemap sources | the differ in §8 |
| Consumer | sitemaps; `/api/indexnow` seeding; proving the gate | Agent 8 submission |
| Cadence | per request | per tick |

**A batch of size zero is the expected steady state** and must be representable.
A design in which the batch is never empty is a design that submits the universe.

Sitemaps stay a pure projection of the universe. They get no event dependency, no
queue, and no persistence. That is the correct shape for them: a crawler asking
"what exists" should get an answer computed from what exists.

---

## 7. Privacy and removal priority

Frozen: **`PRIVACY REMOVAL > CANONICAL CHANGE > AVAILABILITY/PRICE > CONTENT > COSMETIC`.**

Removal is the case the natural implementation gets wrong by doing nothing at all, so
it gets three specific rules:

1. **Removal never waits in a coalescing window.** URGENT-class transitions (5–10)
   bypass batching and are processed on the next tick. A `public → private`
   transition queued behind eleven cosmetic edits is Mutation 12 and must fail.
2. **PulseSoc's own public access does not depend on distribution state at all.**
   This is already structurally guaranteed and is worth stating so nobody builds a
   weaker version of it: because publicness is a *predicate evaluated per request*
   (§4), a privated entity stops being publicly reachable the instant its row
   changes. There is no distribution cache, no sitemap cache, and no submission state
   that could keep it reachable. Nothing waits on a provider.
3. **We do not submit dead URLs to a fast-crawl channel.** Submitting a 404 asks an
   engine to come look at a 404, and submitting a URL that *still renders* is the
   opposite of a removal request. The correct action is to stop including it and let
   the page's own status and directives do the work. Adopt Agent 8 §K unchanged.

The asymmetry is deliberate: a missed publication costs us some latency on a URL the
next sitemap crawl will find anyway. A missed removal is stale public exposure of
something a member made private — a privacy failure, not a latency one.

---

## 8. Durability semantics

### Required guarantees

Survive: Railway restart, worker crash mid-batch, network failure, provider timeout,
duplicate delivery, deployment, concurrent writers.

Forbidden as the durable layer: in-memory lists, request-local state, process-local
timers, fire-and-forget.

### The minimum new primitive: one table, and nothing else

Everything in this design is satisfiable with `pulse_jobs` *except* the differ's
memory. A diff needs something to diff against, and no per-URL state exists anywhere
(§1). That is the one genuine gap.

```sql
-- PROPOSED. NOT CREATED. Requires Agent 0 approval.
CREATE TABLE IF NOT EXISTS search_distribution_observations (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_class    TEXT NOT NULL,   -- listing | department | post | page
    entity_id       TEXT NOT NULL,   -- §4 identity, as text so classes can share
    canonical_path  TEXT NOT NULL,   -- last observed canonical path
    eligible        INTEGER NOT NULL,-- last observed distribution eligibility
    fingerprint     TEXT NOT NULL,   -- §9, hash of the allowlisted projection
    environment     TEXT NOT NULL,   -- §12
    first_seen_at   TEXT,
    last_seen_at    TEXT,
    last_change_at  TEXT             -- the only defensible lastmod source; §16
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_sdo_entity
    ON search_distribution_observations(environment, entity_class, entity_id);
```

One table. No second queue — candidates go into `pulse_jobs` as new `job_type`s,
keyed `target_type=entity_class`, `target_id=entity_id`.

**The one thing `pulse_jobs` needs and lacks:** its per-entity dedupe is a racy
`NOT EXISTS` (`media_worker.py:1134`). Two concurrent producers can both pass it and
both insert. The fix is a partial unique index on
`(job_type, target_type, target_id)` restricted to open statuses — which is a
migration on a live, drained table and therefore **also** Agent 0's call, not mine.
Until then, coalescing at *consumption* time (§10) is what makes duplicates harmless.

### The failure mode that makes this design dangerous, and the rule that defuses it

Both universe sources swallow query failures and return an empty list:

```python
except Exception:
    logging.exception("MARKETPLACE_PUBLIC_QUERY_FAILED serving an empty list")
    return []
```

`bot.py:32593` for products; the same pattern at `pulse_public_entries`. For a
*sitemap* that is the right call — an empty `<urlset>` beats a 500, and my own
`NOINDEX_ALLOWED` carve-out rests on exactly this reasoning: a row count cannot tell
an empty catalogue from a failed query.

For a **differ** it is catastrophic. One transient database failure reads as *"all 148
URLs were removed"*, and a naive differ emits 148 URGENT removal events.

> **RULE D1 — A change batch may never be computed from a universe read that could
> have failed.** The universe computation used by the differ must return
> `(status, rows)` where status distinguishes `OK` from `FAILED`, and a `FAILED` read
> aborts the tick with no events emitted. Not "emit nothing on empty" — *abort on
> failed*, because a genuinely empty catalogue is a real state that must still diff
> correctly.

This means the differ cannot call `marketplace_public_entries()` as it stands. It
needs a status-returning sibling. That is a read-only addition and is the one piece of
code I would recommend building first, because it is useful on its own and it is the
precondition for everything else.

---

## 9. Idempotency semantics

**At-least-once internal delivery, idempotent and coalesced external submission.**
No exactly-once networking is assumed anywhere.

```
EVENT A ─┐
EVENT A ─┼─► COALESCE by canonical URL ─► ONE candidate ─► submission attempt ─► durable result
EVENT A ─┘
```

The fingerprint is what makes this safe:

```
fingerprint = H( entity_class, entity_id, canonical_path, eligible,
                 <allowlisted projection fields for this class> )
```

Allowlist per class, from §2/§3:

- `listing` — `MATERIAL_FIELDS` ∪ `{availability, status, approval_status, seller_approved, price_component}`
- `department` — `{slug, indexable_listing_count_bucket}`; a *bucket*, not a count, so
  one listing moving in and out of a 40-item department is not a department change
- `post` — `{title, body_hash, visibility, moderation_status, deleted}`.
  `body` is the only stored copy of a caption, so it is the content source.
- `page` — `{template_fingerprint}` only; these have no row

Excluded from every fingerprint, explicitly: `updated_at`, `engagement_score`, view
and like counts, `cover_attempts`, `variant_key`, supplier metadata, moderation notes.

Properties this buys:

- the same transition observed twice produces the same fingerprint → no second event
- a redelivered event re-resolves current state and finds nothing to do
- an event for a stale fingerprint is recognisable as stale without a version column,
  because the observation row *is* the version
- a non-material write moves `last_seen_at` and nothing else

---

## 10. Coalescing semantics

Window: one differ tick. Proposed cadence 300s, matching `supplier_worker --interval 300`.

```
10:00  price changes      ┐
10:01  image changes      ├─ one tick, one fingerprint delta, ONE candidate
10:02  description changes┘
10:03  availability changes   → folded into the same candidate if same tick
```

Rules:

1. **Coalesce by canonical URL**, not by entity — two entities that resolve to the
   same URL are one candidate.
2. **Priority is the max of the folded transitions.** A cosmetic edit folded with an
   availability change is ELEVATED.
3. **URGENT does not coalesce downward and does not wait.** Classes 5–10 are emitted
   on the tick that observes them even if a cosmetic change for the same URL is
   already pending; the pending candidate is upgraded, never the reverse.
4. **Re-evaluate eligibility at consumption time, always.** A candidate created when
   the entity was public and consumed after it was held must be dropped, not sent.
   This is Agent 8 §I-J rule 3 and it is the reason rule 1 of that section exists.
   There is no path by which a queued candidate outlives the eligibility of its subject.
5. **Supersede, don't accumulate.** A newer fingerprint for the same URL replaces the
   pending candidate.

---

## 11. Two traps in the current universe sources that would corrupt a differ

Neither is a defect today. Both become one the moment anything diffs these sources.

### Trap 1 — the posts universe is engagement-ordered and capped

```sql
-- pulse_public_entries, bot.py:32502
ORDER BY engagement_score DESC, created_at DESC
LIMIT 200
```

At >200 public posts, membership in the universe starts depending on
`engagement_score`. A post that nothing changed about leaves the universe because 200
other posts out-engaged it, and re-enters when they cool off. A differ over this
output manufactures phantom `LEFT_UNIVERSE` / `ENTERED_UNIVERSE` pairs, and
submission volume becomes a function of traffic — which Agent 8 ruled out explicitly:
*"an event stream that includes those is unusable for this purpose."*

> **RULE D2 — The differ diffs the eligibility predicate, never the sitemap's
> truncated, engagement-ordered output.** The universe-for-diffing is every entity
> passing the gate, unordered and uncapped. The sitemap's cap is a rendering concern
> and must not leak into change detection.

Current scale: 15 public posts against a cap of 200. Latent, not live. It becomes live
at 200 and there is no alarm on it.

### Trap 2 — the products cap is a cliff, not churn

```sql
-- marketplace_public_listings, bot.py:32581
ORDER BY l.id DESC LIMIT 500
```

`id` is immutable, so truncation here is deterministic: past 500 listings the *oldest*
silently fall out and stay out. No churn, but a permanent silent loss of distribution
for the back catalogue, and a differ would report each as a one-time removal — which
in distribution terms is *correct*, which is what makes it insidious. Current scale:
42 listings against a cap of 500.

Both caps are reported to Agent 2 / Agent 7 as universe-definition items. I have not
changed them: raising a cap changes what is distributed, and that is not my call.

---

## 12. Environment isolation

Fail-closed, four independent affirmative conditions, adopting Agent 8 §F unchanged
and adding the producer-side half Agent 8 could not enforce:

```
outbound submission permitted ==
        OPEN_WEB_SUBMIT_ENABLED explicitly truthy        (default: false)
    AND COINPILOTX_ENV_MODE == "production"
    AND search_visibility.CANONICAL_HOST == the host we hold a key for
    AND every URL in the batch starts with CANONICAL_ORIGIN
```

Producer side:

> **RULE D3 — every observation row and every candidate carries `environment`, and a
> consumer processes only candidates matching its own.** Absent or unparseable →
> treated as foreign, not as local.

This matters more here than it looks, for the reason Agent 8 found: `canonical_url()`
hardcodes `https://pulsesoc.com`, so a staging container emits production URLs with a
valid production key. **Staging is indistinguishable from production by payload
inspection alone** — the usual staging-leak tell (staging URLs) is inverted. The gate
cannot be built out of host correctness; it has to come from the environment and
default to off.

Any new `os.getenv` must be declared in `.env.example` or
`tests/protection/test_environment_contract.py` fails. `OPEN_WEB_SUBMIT_ENABLED` is
already declared on Agent 8's branch.

---

## 13. Consumer ownership and upstream dependencies

| Concern | Owner | Agent 6's relationship |
|---|---|---|
| canonical URL, directive, indexability, `sitemap_eligible` | **Agent 2** | consume only |
| product & variant identity | **Agent 3** | consume only |
| structured-data projection | **Agent 5** | care *that* it changed; never regenerate JSON-LD |
| eligible universe, sitemaps, this contract | **Agent 6** | own |
| Merchant feed projection | **Agent 7** | shares this signal; see below |
| IndexNow payload, Bing, submission evidence, the worker | **Agent 8** | produce for |
| media lifecycle | **Agent 9** | consume; never emit distribution events for deleted or private assets |
| social × commerce edges | **Agent 10** | an edge is a change only if it moves a public entity across the gate |
| telemetry | **Agent 11** | expose stages, §15 |
| attacks | **Agent 12** | §16 |

**One shared signal, two consumers.** A listing price change is *one* canonical
commerce transition. It must not become an "IndexNow product event" and a separate
"Merchant product event". The event says `PRICE_CHANGED` on `listing:35`; Agent 7
decides a feed regeneration follows and Agent 8 decides a submission follows. Adding
a per-consumer event type is how one transition becomes two sources of truth.

### Dependency I must declare rather than assume

This mission's brief states Agent 2 is authoritative for `REDIRECT`, `404`, `410`,
`PRIVATE`, `HELD`, `TEMP_UNAVAILABLE`. **Four of those do not exist today.**
`services/search_visibility.py` contains zero occurrences of `410`, `GONE`,
`TEMP_UNAVAILABLE`, `HELD` or `REDIRECT`. Its surface is `classify`, `robots_meta`,
`is_indexable`, `canonical_url`, `content_eligibility`, `sitemap_eligible` — a
directive classifier plus a boolean eligibility projection.

So today the only available signal is **boolean**: in the universe, or out of it.
That is why §2 makes classes 8–10 refinements of class 7. The contract is
implementable now against the boolean, and gains the reason codes without a schema
change when Agent 2 ships them. **Blocking on them would be blocking on something
nobody has committed to build.**

---

## 14. The Agent 8 interface

Exactly what Agent 8 §H asked for, answered field by field.

```
CHANGE CANDIDATE                     (a pulse_jobs row + its observation row)
  job_type        search_distribution_candidate
  target_type     entity_class        listing | department | post | page
  target_id       entity_id           §4
  --- read from search_distribution_observations at consumption time ---
  canonical_path  current             re-resolved, never trusted from the event
  prior_path      last observed       present only for CANONICAL_CHANGED
  transition      §2 class
  priority        URGENT | ELEVATED | NORMAL
  fingerprint     current
  environment     §12
```

Against Agent 8's required properties:

| Required | How this satisfies it |
|---|---|
| ordered per entity / monotonic version | the observation row is the version; a candidate whose fingerprint ≠ the current observation is stale and dropped |
| idempotent | fingerprint equality (§9); no second event for a re-observed state |
| durable, transactional with the write | the differ *reads committed state only* — there is no "event emitted before a commit that then rolls back", because the event is derived after the fact. This is strictly stronger than a transactional outbox, and it is a free consequence of differing rather than hooking. |
| coalescing-friendly | §10; twelve edits in a tick are one candidate |
| no private payload | ids and paths only. No bodies, no PII, no IP, no UA. |
| environment-tagged | RULE D3 |
| no engagement coupling | RULE D2 and the §3 exclusions |

Agent 8 owns, and Agent 6 does not touch: batching, payload construction, the outbound
call, provider credentials, response classification, retry policy, submission
telemetry. **Agent 6 never makes the outbound call.**

---

## 15. The Agent 11 telemetry interface

Stages, in order, each derivable from the two tables:

| Stage | Derived from |
|---|---|
| `change_detected` | differ tick found a fingerprint delta |
| `candidate_created` | `pulse_jobs` row inserted |
| `coalesced` | insert folded into an existing open candidate |
| `withheld` | failed the gate at consumption — with a reason |
| `queued` | `status='pending'` |
| `submission_attempted` | Agent 8 |
| `provider_accepted` | Agent 8 — IndexNow `200`/`202` |
| `provider_rejected` | Agent 8 — `400`/`403`/`422` |
| `retry_scheduled` | `attempts` incremented, `run_after` set |
| `terminal_failure` | `attempts >= max_attempts` |

Three rules, adopting Agent 11's model:

1. **`indexed` is never derivable from any of these.** The chain
   `ELIGIBLE ≠ SUBMITTED ≠ ACCEPTED ≠ CRAWLED ≠ INDEXED` may not be collapsed, and
   reporting a later stage using an earlier stage's number is the single most likely
   way this subsystem lies to its owner. `provider_accepted` means the provider liked
   the request. Nothing more.
2. **`UNKNOWN` and `NO_EVIDENCE` are publishable values**, distinct from `0`. Bing
   Webmaster Tools is not configured (`/BingSiteAuth.xml` 404s; `BING_SITE_VERIFICATION`
   is read into a dict key nothing consumes), so every crawl and coverage stage is
   `NO_EVIDENCE` today and must render as such.
3. **No new telemetry table.** All ten stages are derivable from
   `pulse_jobs` + `search_distribution_observations`. Agent 11 declined to add a 112th
   event table; this contract does not hand it one.

---

## 16. Mutations for Agent 12

The brief's fifteen, mapped to where each must die. Agent 12 should publish them all;
ownership tells you which lane to fix when one survives.

| # | Mutation | Must be killed by | Owner |
|---|---|---|---|
| 1 | internal timestamp change → submission | `updated_at` excluded from every fingerprint (§9) | 6 |
| 2 | public → private emits no event | class 9/7 is URGENT and unconditional (§2, §7) | 6 |
| 3 | public → held stays eligible | `public_sql()` + re-evaluation at consumption (§10 rule 4) | 6 |
| 4 | deleted post stays in the universe | `deleted_at IS NULL` + `content_eligibility` | 2 |
| 5 | absolute evil URL accepted | rooted-path rejection *before* `canonical_url` (§5 rule 2) | 8 (landed) |
| 6 | `//evil.example/x` accepted | same | 8 (landed) |
| 7 | same event twice → two changes | fingerprint equality (§9) | 6 |
| 8 | worker dies after enqueue → event lost | `pulse_jobs` lease recovery, `media_worker.py:1111` pattern | 6 |
| 9 | provider timeout → marked submitted | response classification (§L) | 8 |
| 10 | provider accepted → `indexed=true` | §15 rule 1 | 11 |
| 11 | one product changes → whole universe submitted | universe ≠ batch (§6) | 6 |
| 12 | removal coalesced behind cosmetics, never processed | URGENT bypasses the window (§10 rule 3) | 6 |
| 13 | staging event → production submission | RULE D3 + §12's four conditions | 6 + 8 |
| 14 | canonical change → only the new URL considered | `prior_path` carried on `CANONICAL_CHANGED` (§14) | 6, pending Agent 2 policy |
| 15 | advertised sitemap child contributes zero possible URLs | `test_no_advertised_child_sitemap_is_structurally_empty` | 6 — **already landed** |

Three more that this design implies and the brief did not ask for. They are the ones I
would actually expect to survive:

| # | Mutation | Must be killed by |
|---|---|---|
| 16 | the universe read raises; differ treats `[]` as "everything was removed" and emits 148 URGENT removals | RULE D1 — `FAILED` aborts the tick |
| 17 | the differ reads the capped, engagement-ordered posts sitemap output, so engagement churn manufactures publish/remove pairs | RULE D2 |
| 18 | a new column is added to `marketplace_listings` and silently becomes a search event | the fingerprint is an allowlist (§3) |

Mutation 16 is the most important item in this document. It is the one that turns a
transient database blip into a mass removal request, and the natural implementation
has it.

---

## 17. Proposed minimal durable primitive

| Need | Answer |
|---|---|
| durable queue | **`pulse_jobs`**, existing, live, drained. New `job_type`s. No new queue. |
| per-entity coalescing | existing `NOT EXISTS` guard, plus consumption-time supersession |
| retry / backoff / lease recovery | existing `attempts` / `max_attempts` / `run_after` / stuck-row requeue |
| **differ memory** | **ONE new table**, `search_distribution_observations` (§8) |
| status-returning universe read | a read-only sibling of `marketplace_public_entries` / `pulse_public_entries` returning `(status, rows)` — RULE D1 |
| a tick | **unresolved. Agent 0's decision.** |
| telemetry store | none; derived (§15) |

**The tick is the open question.** The differ is a pure function and can be invoked
from anything, but something must invoke it:

- **(a) extend an existing Procfile worker.** Cheapest, no new process. But every
  candidate is a media, ads, alert, email or supplier worker, and search distribution
  belongs to none of them. Lane-crossing.
- **(b) one new worker process, owned by Agent 8.** Cleanest ownership: Agent 8
  already specified a submission worker it does not have (§I-J), and the differ tick
  can live in the same loop. Costs one Railway process.
- **(c) an authenticated admin-triggered run, no scheduler.** Zero new processes,
  proves the whole chain end to end, no autonomous behaviour. **Recommended first
  step** — it makes (a) vs (b) a decision informed by real data instead of a guess.

My recommendation is (c) then (b).

### Sitemap index `lastmod` — the deferred item this unblocks

My prior report deferred per-child `<lastmod>` on the sitemap index on the grounds
that no defensible source existed and *omitting it beats lying with it*. This contract
would create that source: `max(last_change_at)` over a child's entities is a real
material-change timestamp, not a deploy time and not a row's `updated_at`.

It stays deferred. The source must exist and be trusted before the field is emitted,
and at 148 URLs across 4 partitions index-level `lastmod` buys essentially nothing.
Recorded so the dependency is visible, not as a request.

---

## 18. Is implementation safe now?

**No. Not without Agent 0 approval.** Explicitly.

Built in this commit: **nothing.** This document only.

Safe to build without further approval (read-only, no runtime effect):

- the status-returning universe read (RULE D1) — useful standalone, precondition for
  everything else
- a pure fingerprint function with no caller
- tests pinning current behaviour of the four universe sources
- contract types with zero runtime effect

Requires Agent 0 approval, and is therefore **not** in this commit:

- `search_distribution_observations` — a schema change
- new `pulse_jobs` `job_type`s and any consumer of them
- the partial unique index that would fix `pulse_jobs` dedupe — a migration on a live
  drained table
- any tick: new worker, extension of an existing worker, or an admin trigger
- anything outbound. No provider call, no credential, no activation.

Unchanged and verified still green with this commit: the sitemap cleanup. Four
children, 148 URLs, no dead child returned.

### Escalations carried forward, not resolved here

1. **59 of 87 page-sitemap URLs are the retired crypto / sports-betting product** —
   40% of all crawl distribution. Agent 2 + Agent 0. **Not unilaterally removed.**
2. **The posts cap (200, engagement-ordered) and products cap (500, id-ordered)** are
   universe-definition decisions with latent correctness consequences (§11). Agent 2 +
   Agent 7.
3. **`/sitemap-marketplace.xml`** is declared in `config/route_auth_baseline.json` and
   does not exist. Agent 0, baseline hygiene.
4. **Four lifecycle states this contract is told to consume do not exist** (§13).
   Agent 2.
5. **Bing Webmaster Tools is not configured.** Owner action, cannot be delegated to an
   agent. Recommended *before* activation, not after — activating an outbound channel
   with no observation path means the first thing we learn about a mistake comes from a
   ranking change weeks later.

---

## Status

```
AGENT 6   SITEMAP                  COMPLETE, PUSHED, GREEN
          MATERIAL-CHANGE CONTRACT FROZEN
          IMPLEMENTATION           BLOCKED ON AGENT 0
          POSTURE                  STANDBY
```

The architecture, end to end:

```
CANONICAL ENTITY STATE  (predicate, not a column -- §4)
        ↓
ELIGIBILITY PREDICATE DIFF  vs  last durable observation   -- §8, RULE D1/D2
        ↓
MATERIAL-CHANGE CANDIDATE   (pulse_jobs, entity-keyed)     -- §14
        ↓
RE-RESOLVE + RE-EVALUATE AT CONSUMPTION                    -- §10 rule 4
        ↓
AGENT 8 PROVIDER DISTRIBUTION
        ↓
AGENT 11 OBSERVATION  (never "indexed")                    -- §15
        ↓
AGENT 12 ATTACKS                                           -- §16
```
