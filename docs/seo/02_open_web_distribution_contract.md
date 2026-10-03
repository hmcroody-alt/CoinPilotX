# The open-web distribution contract

Agent 8 (Bing / IndexNow / open-web distribution). Frozen 2026-10-03.

This is the vocabulary half of the open-web distribution work. It exists so that
the rest of the fleet can depend on precise terms before any of us builds a
submission path, and so that nobody builds a second one.

Everything measured below was measured against production
(`https://pulsesoc.com`) on 2026-10-03, with the deployed commit read from
`GET /api/service/health` and compared against git before any conclusion was
drawn:

| | |
|---|---|
| production `commit` | `5bdf4e431d1fd9164706962d7610d287a1f3092b` |
| `origin/main` | `5bdf4e431d1fd9164706962d7610d287a1f3092b` |
| this worktree `HEAD` | `5bdf4e431d1fd9164706962d7610d287a1f3092b` |

All three agree, so every statement here about source is also a statement about
what is serving. **Read that table before trusting any claim in this document
later** — the one false finding this work produced came from reading a dirty
local checkout whose `bot.py` was 2,189 insertions ahead of what was deployed,
and diagnosing a bug that production had already fixed. See
[What is not wrong](#what-is-not-wrong).

---

## Why the vocabulary comes first

Search distribution fails in a specific way: it succeeds loudly and means
nothing. An IndexNow endpoint returns `200 OK` for a URL that is noindex, for a
URL that was deleted a minute ago, for a URL nobody will ever crawl. The `200`
is a receipt for the *submission*, not for anything a user could find. A system
built without separate words for those states will report a number that looks
like growth and is actually just outbound traffic we generated ourselves.

So the terms below are not pedantry. Each one names a place where this has gone
wrong somewhere before.

---

## Terms

**ENTITY** — one thing PulseSoc publishes that can have its own URL: a member
post, a marketplace listing, a marketplace department, a marketing page, a live
room, a replay. Not a route, not a template, not a database row. A row is not an
entity until something gives it a public URL.

**CANONICAL URL** — the one absolute URL that represents an ENTITY to a search
engine. There is exactly one authority for this: `search_visibility.canonical_url(path)`.
It is not derived here, it is not derived per-surface, and it is not taken from
seller input. Note the shape of that function: it always returns
`search_visibility.CANONICAL_ORIGIN` (`https://pulsesoc.com`) regardless of the
host the request arrived on. That is deliberate — it is the structural defence
against a seller-supplied or attacker-supplied host ending up in a submission
batch — and it is also precisely why §F exists: a staging boot would emit
*production* URLs, so host correctness cannot be the environment gate.

**PUBLIC STATE** — whether the ENTITY is visible to an anonymous visitor. This
is a property of the entity's own lifecycle and it is **not one predicate**:

- Member posts: `visibility='public' AND moderation_status='approved' AND deleted_at IS NULL`, then `search_visibility.content_eligibility(row)`.
- Marketplace listings: `marketplace_listing_lifecycle.public_sql()` *and* `discovery_visibility.discovery_visible_sql()` *and* `marketplace_seo.eligibility(listing).indexable`.

Those two gates do not accept the same set and must never be swapped. See
[Two gates, not one](#two-gates-not-one).

**INDEXABILITY STATE** — whether we are asking search engines to index the URL,
i.e. `search_visibility.classify(path).indexable`. Independent of PUBLIC STATE:
`/signup` is public and deliberately not indexable; a held listing is
non-public on a path shape that *would* be indexable.

**OPEN-WEB ELIGIBILITY** — whether a URL may be handed to a search engine at
all. Defined as, and only as:

```
OPEN_WEB_ELIGIBLE(url) ==
    url == search_visibility.canonical_url(path)        # CANONICAL
    AND entity is public by its own lifecycle gate      # PUBLIC
    AND search_visibility.sitemap_eligible(path, row)   # INDEXABLE
    AND entity class is declared distributable here     # see the class table
```

There is no second definition of this. If a surface needs a different answer,
the surface is wrong or `search_visibility` needs changing — in which case the
change goes to Agent 2, not into a local copy.

**MATERIAL CHANGE** — a change to an ENTITY that alters what a search engine
would see on its canonical URL: it appeared, it became public, its title or
body or price or availability changed meaningfully, it became private, it was
held, it was deleted, its canonical URL moved. Explicitly **not** material: a
view counter, a like, an engagement score, a `last_seen_at`, a reindex of
unchanged content, an internal moderation note. This distinction is the entire
difference between a submission channel and a spam cannon, and PulseSoc does
not currently have a producer that can tell the two apart. See §H.

**CHANGE VERSION / ORDERING** — a monotonic marker per ENTITY that says which
of two observations of it is newer. Required because a submission worker runs
after the fact and must be able to discard an event that a later event has
already superseded. PulseSoc has no such marker today; `updated_at` is the
closest thing and it is not reliably written by every writer.

**SUBMISSION CANDIDATE** — a canonical URL that is OPEN-WEB ELIGIBLE *right
now*, at the moment of evaluation. A candidate is a statement about the present,
not a queued intent.

**SUBMISSION ATTEMPT** — one outbound request we made, recorded with: the URLs
in it, the provider, the time, the environment, the resulting status, and the
reason we believed each URL was eligible. An attempt is evidence; it is not a
claim about the outcome.

**PROVIDER ACCEPTANCE** — the provider's HTTP acknowledgement that the
*submission* was well-formed and the key checked out. For IndexNow: `200` or
`202`. It means the request was accepted. It does not mean crawled, does not
mean indexed, does not mean kept.

**PROVIDER FAILURE** — a provider response that says the submission was not
accepted, with its own distinct meaning per code; see §L. A failure is
actionable; an acceptance is not.

**PROVIDER OBSERVATION** — something a provider tells us about the *world*
rather than about our request: a crawl in a log, a coverage state in Bing
Webmaster, an impression count. This is the only category that can support a
claim about indexing, and PulseSoc currently receives none of it for Bing.

**UNKNOWN** — the correct, reportable value when we have no PROVIDER
OBSERVATION. Not zero. Not "pending". Not "indexed". A dashboard that cannot
distinguish "indexed: 0" from "indexed: unknown" will be read as the first and
is worse than no dashboard. Bing coverage for pulsesoc.com is UNKNOWN as of
2026-10-03 and will remain UNKNOWN until the owner action in §M is taken.

---

## The chain nobody may collapse

```
ELIGIBLE  ≠  SUBMITTED  ≠  ACCEPTED  ≠  CRAWLED  ≠  INDEXED  ≠  RANKING  ≠  IMPRESSION  ≠  CLICK  ≠  ORDER
   │           │            │            │           │           │           │            │        │
   we          we made      provider     provider    provider    provider    provider     user     user
   decided     a request    liked the    fetched     kept it     shows it    showed it    came     bought
               about it     request      it                      for a                            something
                                                                 query
   ───────────────────────┤  ├──────────────────────────────────────────────────────────────────────────────
   things we can prove       things only a provider can tell us
      from our own data      (we have none of this for Bing today)
```

Each arrow is a drop-off we do not control and mostly cannot see. Three rules
follow and they are not negotiable:

1. **Never report a later state using an earlier state's number.** "148 URLs
   indexed" when what we know is "148 URLs eligible" is a false statement, and
   it is the single most likely way this subsystem lies to its owner.
2. **The left half and the right half live in different stores.** Our
   submission evidence is ours. Coverage, crawl and impression data is a
   provider observation and must be labelled with its source and its
   as-of time.
3. **UNKNOWN is a publishable value.** Any surface that cannot render UNKNOWN
   distinctly from zero is not ready to render this data at all.

---

## What already exists, and must not be rebuilt

The incumbents, all live in production at `5bdf4e431`:

| System | Owner | What it decides |
|---|---|---|
| `services/search_visibility.py` | Agent 2 | canonical URL, robots directive, indexability, sitemap eligibility. 731 lines. One authority. |
| `services/seo_engine.py` `sitemap_xml()` | Agent 2 | renders a urlset, re-applying `sitemap_eligible` so a new caller cannot forget it |
| `services/marketplace_listing_lifecycle.py` `public_sql()` | commerce | which listings are public at the SQL level |
| `services/marketplace_seo.py` | Agent 7 | product/category paths, per-listing indexability, department eligibility |
| `services/merchant_center_feed.py` | Agent 7 | the Shopping feed at `/feeds/merchant-center.xml` |
| `bot.pulse_public_entries()` | Agent 2 | public post entries, `(path, lastmod)` |
| `bot.marketplace_public_entries()` | Agent 7 | public product entries, `(path, lastmod)` |
| `bot.marketplace_category_entries()` | Agent 7 | public department entries, `(path, lastmod)` |
| `/api/track`, `window.coinPilotXTrack` | Agent 11 | the analytics path |

Agent 8 owns exactly three things and nothing else: the IndexNow protocol
surface, the Bing relationship, and the submission evidence. **Agent 8 does not
own eligibility.** Every URL Agent 8 ever submits must have been cleared by the
table above, and Agent 8's contribution is to *not* add a fourth opinion about
what is public.

---

## Measured coverage of the fast-change channel

Method: one `GET` per sitemap against production, `<loc>` counted; one `GET`
against `/api/indexnow`, `urlList` counted; the two sets diffed with `comm`.

| Surface | URLs | In the IndexNow payload? |
|---|---|---|
| `/sitemap-pages.xml` | 87 | 76 of them |
| `/sitemap-products.xml` | 42 | **none** |
| `/sitemap-posts.xml` | 15 | **none** |
| `/sitemap-categories.xml` | 4 | **none** |
| `/sitemap-live.xml` | 0 | n/a |
| `/sitemap-replays.xml` | 0 | n/a |
| **union, deduped** | **148** | **76 (51%)** |

The gap is not a rounding error, it is an inversion. `/api/indexnow` derives its
list from `seo.content.all_public_paths()`, which is a hand-maintained list of
marketing pages. So the one channel whose entire purpose is *speed* covers only
the 76 URLs that essentially never change, and covers **zero** of the 61 URLs
that change daily — every product, every department, every member post. The 11
pages it misses are `seo_engine.PUBLIC_LEARN_PATHS` (7) and
`seo_engine.ADS_LANDING_PATHS` (4), which `/sitemap-pages.xml` unions in and
`/api/indexnow` does not.

### One live defect, proven

`https://pulsesoc.com/sports-edge` appears **twice** in the live
`/api/indexnow` `urlList` — 77 entries, 76 distinct. Root cause is upstream of
Agent 8: `seo.content.all_public_paths()` returns 164 paths of which 163 are
distinct, with `/sports-edge` listed twice. `/sitemap-pages.xml` never showed
this because it wraps the call in `set(...)`; `/api/indexnow` builds a list
comprehension with no dedupe.

Severity: low. A duplicate in a batch wastes quota and muddies per-URL
diagnostics; it leaks nothing and asks for nothing improper. It is listed here
because it is the smallest possible demonstration of the thing this contract is
about — two surfaces reading the same source and disagreeing, with neither one
wrong on purpose.

Agent 8 fixes this at its own boundary by deduping the candidate set, and by
unioning the pages source through a `set()` the way `/sitemap-pages.xml` does —
so the duplicate is now absorbed twice over. The duplicate in
`all_public_paths()` is reported to Agent 2 as an upstream item and is **not**
edited here.

### Two more findings, both from writing the tests rather than reading the code

**`sitemap_eligible` returns true for `/sitemap.xml`.** Reason: "public
content". That is defensible for the policy's own purpose — those URLs are
public, crawlable, not noindex — but it means the sitemaps were kept out of the
submission payload only by the accident that no source class supplied them.
Hand one in and it sails through. Withholding them is now an explicit check in
`open_web_distribution`, not an implicit property of the input.

**An absolute URL supplied where a path was expected lands back on-host.**
`canonical_url` normalises by prepending the origin, so
`https://evil.example.com/x` becomes
`https://pulsesoc.com/https://evil.example.com/x`. That URL passes the host
assertion and is a guaranteed 404. The hardcoded origin holds, so this is not a
host leak or an open redirect — it is a malformed-input hole, and the input that
would reach it is a seller- or importer-supplied URL stored in a field a path
was expected from, which is the shape of data the marketplace carries. Rejected
now by a rooted-path check, alongside the protocol-relative `//host/x` form.

Neither of these was visible from reading. Both came out of writing an assertion
that turned out to fail.

---

## Entity classes: what is distributable, and why

| Class | Distributable | Gate it must pass | Why |
|---|---|---|---|
| Marketing pages | **yes** | `sitemap_eligible(path)` | already public, already indexable, already in a sitemap |
| Learn pages | **yes** | `sitemap_eligible(path)` | same; currently missing from the payload only because the input set differs |
| Ads landing pages | **yes** | `sitemap_eligible(path)` | same |
| Marketplace products | **yes** | `public_sql()` + `discovery_visible_sql()` + `marketplace_seo.eligibility().indexable` + `sitemap_eligible()` | the highest-value changing inventory; this is the reason the channel exists |
| Marketplace departments | **yes** | `marketplace_seo.category_entries()` (which enforces the ≥3-indexable-listing floor) + `sitemap_eligible()` | public grid, self-canonical, index,follow |
| Member posts | **yes** | the post lifecycle predicate + `content_eligibility(row)` + `sitemap_eligible(path, row)` | public and approved; the per-row form of the gate is mandatory, a bare path check is not enough |
| Sitemap index + children | **no** | — | we tell engines about sitemaps via `robots.txt` and Webmaster tools; IndexNow is for content URLs |
| `/feeds/merchant-center.xml` | **no** | — | `noindex,follow` by design; it is a Shopping feed, not a page |
| Live rooms | **no, pending** | — | 0 locs in production and the surface is ephemeral by nature. A room that ends 90 seconds after we ask Bing to hurry is a URL that 404s or thins out on arrival. Needs Agent 9's lifecycle contract before it can be a candidate. |
| Replays | **no, pending** | — | 0 locs in production. Plausibly a good candidate later; Agent 10's retention and takedown contract is not frozen, and a submitted replay that is later removed is exactly the deletion case §K exists for. |
| Held / draft / pending / archived | **never** | — | not public |
| Private or unapproved posts | **never** | — | not public |
| Admin, API, checkout, merchant console | **never** | — | `_RULES` in `search_visibility` rejects these; they are not content |
| Internal search results | **never** | — | `/search?q=` is rejected; submitting it asks an engine to index a query |
| Tracking-parameter URLs | **never** | — | `?utm_source=`, `?page=2` are non-canonical by definition |
| Any non-`pulsesoc.com` host | **never** | — | IndexNow `422`s a batch whose URLs don't belong to the declared host, and a seller-supplied host in a batch would be an open redirect with our key on it |

"Pending" means: not excluded on principle, excluded because the owning agent
has not published a contract Agent 8 can depend on. Those two rows are the only
honest open questions in this table.

---

## ELIGIBLE UNIVERSE vs CHANGE BATCH

These are two different objects and conflating them is how an IndexNow
integration turns into spam.

**The ELIGIBLE UNIVERSE** is every URL that is OPEN-WEB ELIGIBLE at this
instant. It is a snapshot, it is recomputable from current state alone, it needs
no event log, and it is ~148 URLs today. It is what `/api/indexnow` serves, and
it is useful for exactly two things: proving the eligibility gate is correct,
and seeding the channel once.

**A CHANGE BATCH** is the subset of the universe that has *materially changed*
since we last told anyone. It requires a producer that emits material-change
events and a durable record of what we have already submitted. PulseSoc has
neither. It would be a handful of URLs on a normal day.

Submitting the universe on a schedule *as if* it were a change batch is the
failure mode. It would send 148 unchanged URLs repeatedly, burn the quota,
train the provider to discount our signal, and produce a dashboard where the
submission count is a function of the cron interval rather than of anything
happening on the site.

`/api/indexnow` therefore labels its own output. The payload is the
`ELIGIBLE UNIVERSE`, it says so in a field, and it is not a batch.

---

## §F Environment isolation (designed, not activated)

The gate must be fail-closed, and it must not be built out of host correctness.
`canonical_url()` hardcodes `https://pulsesoc.com`, so a staging container
boots already emitting production URLs with a valid production key — the exact
inversion of the usual staging-leak shape, where staging gives itself away by
emitting staging URLs. Staging here is indistinguishable from production by
payload inspection alone. The gate has to come from the environment, and it has
to default to off.

Shape, following the existing convention (`bot._deployment_environment_enabled()`
at `bot.py:95`, `bot.COINPILOTX_ENV_MODE` at `bot.py:136`):

```
outbound submission is permitted  ==
        OPEN_WEB_SUBMIT_ENABLED is explicitly truthy      (default: false)
    AND COINPILOTX_ENV_MODE == "production"
    AND search_visibility.CANONICAL_HOST == the host we hold a key for
    AND every URL in the batch starts with CANONICAL_ORIGIN
```

Four independent conditions, every one of which must be *affirmatively* true.
Absent or unparseable config is "off", not "on". A missing
`COINPILOTX_ENV_MODE` resolves to `"local"` under the existing convention,
which means a bare container with no environment at all cannot submit — that is
the desired default and it comes for free.

`OPEN_WEB_SUBMIT_ENABLED` is declared in `.env.example` with its default of
false, because the environment-contract gate requires every `os.getenv` to be
declared, and because an undocumented kill switch is not a kill switch.

**Nothing in this branch activates this.** There is no outbound call to
implement a gate for yet; the gate is specified and tested now so that whoever
implements submission cannot ship it ungated, and so that the owner can see the
shape of the switch before it exists.

---

## §H What Agent 8 needs from Agents 0 and 6

Agent 8 cannot build a change batch and will not invent a second event bus to
fake one. These are the requirements for whoever owns the change bus; they are
requirements, not a design, because the design is not Agent 8's to make.

Events needed, one per material transition:

| Transition | Must carry |
|---|---|
| public entity created | entity class, id, canonical path, version |
| public entity materially updated | same, plus which field class changed |
| public entity removed | same; must fire on hard delete *and* soft delete |
| entity became private | same |
| entity became held / unapproved | same |
| canonical URL changed | old path and new path |

Properties the bus must have for a submission worker to be safe on top of it:

- **Ordered per entity** — or carrying a monotonic version, so a stale event
  can be recognised as stale.
- **Idempotent** — the same transition delivered twice must not produce two
  submissions.
- **Durable and transactional with the write** — an event emitted before the
  commit that then rolls back is a submission for a change that never happened.
- **Coalescing-friendly** — twelve edits to one listing in a minute is one
  candidate, not twelve.
- **Carries no private payload** — the event carries ids and paths, not bodies.
- **Environment-tagged** — a staging event must not be processable in
  production and vice versa.

Explicitly **not** wanted: engagement, views, likes, scores, `last_seen_at`. An
event stream that includes those is unusable for this purpose, because the
coupling would make submission volume a function of traffic.

## §I–J What the worker must do, when it exists

Required behaviour, recorded now so the design is constrained before anyone
writes it:

1. **Resolve CURRENT state, never the event payload.** The event says "listing
   35 changed"; the worker re-reads listing 35 and re-runs the eligibility gate
   at send time. An event is a hint to look, not a fact to act on.
2. **Discard superseded events.** If a newer version exists, the older event is
   dropped, not sent.
3. **Re-evaluate eligibility at send time.** A listing that was public when the
   event fired and is held now must not be submitted. This is the single most
   important rule in this section and it is the reason rule 1 exists.
4. **Coalesce per canonical URL** within the batching window.
5. **Batch** up to the protocol limit of 10,000 URLs, well above anything
   PulseSoc will produce.
6. **Respect rate limits** and back off on `429`; see §Q.
7. **Record every attempt** — URLs, provider, time, environment, response.
8. **Retry transient failures** with backoff; **dead-letter permanent ones**. A
   `403` retried on a loop is a key problem being hidden.
9. **Emit evidence, not conclusions.** The worker records SUBMITTED and
   ACCEPTED. It is not permitted to write INDEXED.

## §K Deletions, privacy transitions, and holds

An entity leaving the public web is the case that matters most and is easiest to
get wrong, because the natural implementation does nothing.

- A removed, privated or held entity must **not** be submitted. Submitting a
  dead URL to a fast-crawl channel is asking an engine to come look at a 404 or,
  worse, at a stale cached copy it will then keep.
- The correct action is to stop including it and let the page's own status and
  directives do the work. IndexNow's own semantics permit submitting a deleted
  URL so the engine re-checks it; PulseSoc will not do that until the delete
  path can prove the URL serves a 404 or 410 first, because submitting a URL
  that still renders is the opposite of a removal request.
- Any queued event for an entity that is no longer eligible is dropped at send
  time by §I-J rule 3. There is no path by which a queued event outlives the
  eligibility of its subject.

## §L Provider response semantics

From the IndexNow specification, and the only correct reading of each:

| Code | Means | Our action |
|---|---|---|
| `200 OK` | submission received | record ACCEPTED. Not indexed. Not crawled. |
| `202 Accepted` | received, key validation pending | record ACCEPTED with a pending-validation note; do not resubmit |
| `400 Bad Request` | malformed payload | permanent; dead-letter and alert. Our bug. |
| `403 Forbidden` | key not valid for the host | permanent; dead-letter and alert. Never retry. |
| `422 Unprocessable` | URLs don't belong to the host, or key mismatch | permanent; this is the code a host/URL mismatch produces, and it rejects the *whole batch* |
| `429 Too Many Requests` | rate limited | transient; back off, do not drop |

The `422` row is why `host` and every `urlList` entry must be asserted equal at
build time and not merely hoped to agree. One wrong URL in a batch of 10,000
loses all 10,000.

## §M Bing Webmaster Tools — owner action required

Status: **not configured**. `GET /BingSiteAuth.xml` 404s in production.
`BING_SITE_VERIFICATION` is declared in `.env.example:91` and read at
`bot.py:1479` into a dict key that nothing consumes — so even setting it would
have no effect today.

This cannot be delegated to an agent. It requires signing in to Bing Webmaster
Tools as the domain owner and either importing the existing Search Console
property or verifying the domain directly. Until that happens:

- Bing coverage, crawl state and impressions for pulsesoc.com are **UNKNOWN**,
  and every surface must say UNKNOWN rather than 0.
- There is no feedback loop, so there is no way to tell a successful submission
  from a successful no-op.

The recommendation is to do this **before** activating submission, not after.
Activating an outbound channel with no observation path means the first thing we
learn about a mistake will be from a ranking change weeks later.

## §N The IndexNow key is public verification material, not a secret

`static/indexnow-key.txt` contains `4d4dc0c2c0f94b7bb8184fd91b7f0b1e` — 32 hex
characters, protocol-valid (the spec allows 8–128 of `a-z A-Z 0-9 -`). It is
git-tracked, it is hardcoded in `bot.py`, it appears in a route path, and it is
returned in an unauthenticated JSON response at `/api/indexnow`.

**None of that is a leak.** The protocol *requires* the key be fetchable at
`https://pulsesoc.com/<key>.txt` by anyone, because that file is how the
provider proves we control the host. A key that were secret would be a key that
could not work. Possession of it confers nothing: it only lets someone submit
URLs that already belong to pulsesoc.com, and those URLs are public pages.

This is written down because a scanner, a reviewer, or a future agent will find
it and file it as a P0, and that report will be wrong. The distinction that
matters is *public protocol verification material* vs *secret credential*, and
the test is simple: if the protocol requires third parties to read it, it is not
a secret.

The real handling rules are different ones: the key must match the file, the
file must stay served as `text/plain`, and the key must never be reused for a
host we do not control.

## §O Host ownership

One host, `pulsesoc.com`, from `search_visibility.CANONICAL_HOST`. Both key
routes serve `200 text/plain` in production (`/indexnow-key.txt` and
`/4d4dc0c2c0f94b7bb8184fd91b7f0b1e.txt`). Every candidate URL must start with
`CANONICAL_ORIGIN` and this is asserted, not assumed — see §L on `422`.

`coinpilotx.app` is not a submission host. It appears in this codebase as a CDN
origin and as historical branding; it has no key file and must never appear in a
batch declared under the pulsesoc.com key.

## §P–Q Batching, diagnostics, rate limiting

- Protocol ceiling is 10,000 URLs per POST. PulseSoc's entire eligible universe
  is 148, so batching is a correctness concern (dedupe, host agreement), not a
  scale one.
- **Dedupe before counting.** The `/sports-edge` duplicate above is what
  happens otherwise, and a duplicate also corrupts any per-URL diagnostic.
- Output must be deterministic: same state in, same bytes out. A payload whose
  order changes between calls cannot be diffed, and diffing is how the next
  agent will check our work.
- One endpoint serves all participating engines; there is no per-engine fan-out
  to build.
- Rate limiting is per the provider's `429`, with backoff. There is no
  documented fixed quota to hard-code, and hard-coding a guessed one would be a
  second source of truth about someone else's policy.

## §R–T Evidence, telemetry, and what Agent 11 must not be handed

For the Search Exposure Ledger and for Agent 11:

- Agent 8 can supply: candidate counts by entity class, exclusion counts by
  reason, submission attempts, provider responses, environment, and the
  eligibility reason per URL.
- Agent 8 **cannot** supply: crawled, indexed, ranking, impressions, clicks.
  Those are PROVIDER OBSERVATIONS and Bing supplies none of them today.
- Therefore every exposure metric sourced from Agent 8 must be labelled with
  its position in the chain, and the indexing columns must render UNKNOWN. A
  ledger that shows `submitted: 148 / indexed: 0` will be read as "we submitted
  148 and Bing rejected all of them", which is not what we know.
- Diagnostics must not leak private data. Counts and reasons, never the ids or
  titles of excluded private entities — "4 listings excluded: held" is useful
  and safe, naming them is neither.

## §V Ten mutations for Agent 12

Each should break at least one test. If one does not, the gate it targets is
decorative:

1. Replace the per-entity eligibility call with a bare path check (drops the
   row-level gate on posts).
2. Drop `approval_status='approved'` from the listing predicate.
3. Allow `held` into the published status set.
4. Remove the dedupe step.
5. Replace `canonical_url()` with string concatenation against `request.host`.
6. Add `?utm_source=indexnow` to every submitted URL.
7. Include the sitemap child paths as candidates.
8. Default the environment gate to enabled when the variable is absent.
9. Include a post whose `visibility` is `followers`.
10. Return the candidate set in nondeterministic (set-iteration) order.

Mutation 11, which is really a warning about the harness rather than about the
code: replace all three database-backed class sources with `[]`. One of the
tests written for this branch survived that, because the test compared the
payload's source list against the builder's output and the local database is
empty, so both sides were `[]` and the assertion held. A comparison between two
expressions that can both degenerate to the same empty value is not a test. The
form that works is to substitute a sentinel into the builder and assert the
sentinel arrives. Agent 12 should expect this shape of survivor anywhere a test
asserts equality between two reads of the same empty store.

---

## Two gates, not one

The most dangerous single mistake available in this subsystem, written out
because it looks like a simplification:

`search_visibility.content_eligibility(record)` treats
`{draft, pending, scheduled, archived}` as not-published. That set does **not**
contain `held`. So a record with `status='held'` passes `content_eligibility`.

Marketplace listings are safe from this today only because they never reach
that function — they are gated by `marketplace_listing_lifecycle.public_sql()`,
which restricts status to `('published', 'live', 'active')` and therefore
excludes held. The protection is real but it is *incidental to which function
you call*.

Pointing products at `content_eligibility` — which a reasonable person
refactoring toward "one eligibility function" would do — would silently publish
held inventory to search engines. Verified by probe: `content_eligibility` on a
`status='held'` record returns sitemap-eligible with reason "public content".

No live leak exists. This is recorded as a latent trap, and as mutation 3.

## What is not wrong

Three findings that look like defects, are not, and must not be re-filed:

**The `coinpilotx.app` host mismatch is fixed.** A local read showed
`"host": "coinpilotx.app"` alongside a `pulsesoc.com` key and URL list — a
payload that would `422` every batch. Production says `pulsesoc.com`;
`origin/main` says `pulsesoc.com`; the route's own docstring documents the bug
as historical. The local checkout was dirty and stale. Do not reopen without
new production evidence of regression.

**The IndexNow key is not leaked.** See §N.

**`content_eligibility` is not leaking held marketplace listings.** See the
section above. The trap is latent, the leak is not live.

---

## Status

**SHADOW COMPLETE. BLOCKED ON: shared material-change contract (Agents 0, 6);
provider activation (owner, §M). STANDBY.**

What is done: the vocabulary is frozen; the eligible universe is derived from
the authoritative sources rather than from a hand-maintained page list; the
environment gate is specified and tested; the evidence requirements are
published.

What is deliberately not done: no outbound request to any provider exists in
this branch, no event bus has been invented, no Bing mutation has been
attempted, and no scheduled submission has been configured.
