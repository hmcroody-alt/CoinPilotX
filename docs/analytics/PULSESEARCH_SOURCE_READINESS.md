# PulseSearch — source readiness audit

The instruction for this stage was "do not automatically build it; first prove
canonical searchable sources exist." This records the attempt at that proof and
its outcome.

**Verdict: do not build PulseSearch.** Not deferred on effort, and not because
the sources are un-canonical — because there is not yet a corpus in which the
right document is hard to find, and because a unified search already exists and
nobody has reported it failing.

Evidence gathered 2026-09-22: production Postgres by direct introspection, the
repo by full-text audit.

---

## 1. There is already a unified search

`bot.py:43159` serves `GET /api/pulse/search`, and it is not a per-vertical
stub. One request fans out across posts, creators, presences, videos, reels,
statuses, marketplace listings, music, groups and comments, returning all ten
keyed in a single payload. It is authenticated, rate-limited (90/60s per
ip-hash + user), length-capped at 120 characters, and it composes author
visibility from `services/discovery_visibility.discovery_visible_sql` rather
than re-deriving who is allowed to appear.

Six further per-vertical searches exist — users (`bot.py:94398`), messages
(96749), marketplace (57722), music (44451, 44475), crypto (8985) — plus two
admin ones and two in service routes. The mobile client (`SearchScreen.tsx` via
`api/search.ts`) calls the unified endpoint.

All of them match with SQL `LIKE '%q%'`. So the honest description of the gap is
not "PulseSoc has no search" but "PulseSoc has substring matching and no
ranking".

`services/pulse_search_engine.py` is a 1.2 KB module with a `score_result`
keyword/trust/freshness scorer and a `search()` that sorts a list handed to it.
It is imported at `bot.py:392` and **never called** — the only other reference
is a declaration in `services/undx_knowledge_map.py:1950` describing it as
though it were live. It ranks items already fetched, so it was never wired to
anything that fetches. Named-and-unreachable, which is the shape
`project_services_dir_is_full_of_name_shaped_stubs` warns about.

---

## 2. Production has no text-search infrastructure

| Probe | Result |
|---|---|
| GIN / GIST indexes | **0** |
| `tsvector` columns | **0** |
| Extensions installed | `plpgsql` only — no `pg_trgm`, no `unaccent` |

This is worth stating plainly because it bounds what the current endpoint can
be: a leading-wildcard `LIKE` cannot use a btree index, so every one of those
ten sub-queries is a scan. That is survivable at today's row counts and stops
being survivable silently — the same failure mode recorded for
`marketplace_listings` in `PULSEANALYTICS_DESIGN_RECORD.md` §3.

---

## 3. The corpus is ~550 documents, and most of it is one author

| Table | Rows | Actually searchable |
|---|---|---|
| `pulse_posts` | 2,458 | ~450 — 9 distinct authors, and `user_id=0` wrote 1,995 (81%), `user_id=1` a further 411 |
| `pulse_ai_posts` | 1,970 | generated, not user-authored |
| `pulse_reels` | 71 | **39** carry a caption, from 4 authors |
| `marketplace_listings` | 47 | **15** published, from **1 seller** |
| `users` | 41 | 35 usernames, **0 bios** |
| `pulse_comments` | 40 | 40 |
| `business_os_mkt_products` | 0 | 0 |

Roughly **550 real documents**, of which the commerce half is fifteen listings
belonging to a single seller.

A search engine earns its keep by ranking: it solves "the right document exists
but is buried." At fifteen listings there is nothing to bury. A seller typing
any word from their own listing gets it back from `LIKE` today, and typo
tolerance, stemming and BM25 would each return the same fifteen rows in a
different order.

The 81% figure matters more than the totals. A relevance model trained or tuned
against `pulse_posts` would be tuned against the system account. Ranking quality
is not measurable here, so a build would ship with no way to tell whether it had
improved anything — and an unmeasurable improvement is how this codebase
acquired 111 event tables.

---

## 4. What would change the verdict

Stated as thresholds rather than adjectives, so this document can be checked
rather than re-argued:

* **Commerce:** more than one seller, and published listings in the low
  hundreds. This is the first one likely to trip, and the first that would make
  search commercially load-bearing.
* **Posts:** human-authored posts (excluding `user_id` 0 and 1) into the
  thousands, from tens of authors.
* **Evidence of failure:** searches that return nothing while a matching
  document exists. Nothing measures this today, which is the cheapest gap to
  close — see below.

Any one of these makes ranking a real problem. None of them is true now.

---

## 5. The cheap thing that is worth doing instead

`/api/pulse/search` currently emits no signal about its own quality. There is no
record of what was queried, how many results came back, or how often a query
returned zero while the corpus held something plausible.

That is one log line on an endpoint that already exists — the same shape as
`PULSE_ANALYTICS_FUNNEL_SERVED` — and it converts this verdict from a judgement
into something with a trigger. Without it, the decision to build PulseSearch
will be made the same way this one nearly was: by estimating.

Not built under this stage, because this stage's instruction was to prove or
disprove the precondition, and an endpoint change is not that.

---

## What was deliberately not built

* **No search engine, internal or vendored.** §61's hard lock is not the
  constraint here — the corpus is. Building internally would still be building
  a ranker for 550 documents.
* **No index on the searched columns.** `pg_trgm` plus GIN would make the
  existing `LIKE` fast, and nothing is slow.
* **No deletion of `services/pulse_search_engine.py`.** It is unreachable and
  therefore harmless, and removing it means editing `bot.py`'s import block
  under a stage that is supposed to conclude with a finding. Recorded here
  instead, with its knowledge-map entry noted as overstating it.
