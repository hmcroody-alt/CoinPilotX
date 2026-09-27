# Pulse Commerce Intelligence — delivery report

**If you want the summary, read `SIGNAL_COMMERCE_DELIVERY_REPORT.md` instead.** That document
answers the brief's 64 required items in order and points back here for proof. This one is
the evidence: every defect with the measurement that found it and the mutation that proved
the test could fail. It is long because that is what it is for.

**Status: not deployed.** Nothing in this report is live. The work is committed on the
local branch `commerce-discovery-audit` in a worktree, and has **not** been pushed, merged,
or rolled out. The last increment described below is §24; the commit before it is
`31e2ef257`, so if `git log --oneline 31e2ef257..commerce-discovery-audit` prints more than
§24's own commit and this document's, the branch has moved past what is written here. Read
the figures below as
measurements of that point, not of whatever is currently checked out. (The head is named
this way rather than pinned because a pinned head SHA is wrong the moment it is written —
it cannot name the commit that contains it. The first three revisions of this line were all
stale by exactly one commit.)
Committed is not deployed, and the distinction matters
here: §16 lists changes that alter what feed, reels, post-detail, Messenger, Marketplace
and product-page users see. As of §23 there is now a per-surface kill switch to stage them
behind, which is the one thing that makes that order less frightening than it was; §16 still
explains why the rollout decision itself is not mine to take.

**Scope correction up front.** The mission brief asked for a commerce discovery engine
to be built. One already existed, live in production across six surfaces. The brief
anticipated this — *"DO NOT blindly implement instructions in this mission if repository
evidence shows that PulseSoc already has a stronger mechanism. Inspect first"* — so what
follows is not a new engine. It is an audit of the existing one, four defects found in it,
and the fixes for those defects — plus two things the audit established were missing rather
than broken, and which are therefore features and labelled as such: the per-surface kill
switch (§23) and the creator-tagging write path (§24). Section 3 is an honest ledger of what
pre-existed versus what I added, because the difference is most of the value of this document.

**On the shape of this report.** The brief specified a final report of 41 numbered
sections. This one has 24. That is a deliberate departure, and the reason is the same
reason the engine was not rebuilt: the section list was written on the assumption that
all of it would be new construction. Roughly two thirds of the headings — embeddings,
the vector index, the new worker framework, the experimentation platform, the load
benchmarks at 1M products — describe work that either already exists in PulseSoc under
another name or was deliberately not undertaken. Writing a section for each would mean
filling 24 headings with "not applicable" or, worse, with prose that implies a system
was delivered because a section about it exists. Padding a report is the cheapest way to
make a small change look like a large one, and this change is small: four defects and
their fixes. §15 lists every part of the brief I did not build, by name, which is the
honest version of the missing sections. If the 41-section structure is wanted for
review, say so and I will map this content onto it.

---

## 1. What was already there

`services/commerce_discovery/` is a fourteen-module package, live in production, serving
six surfaces through `services/commerce_discovery_routes.py`. Before I touched anything
it already had:

| Concern | Module | State on arrival |
| --- | --- | --- |
| Eligibility gating | `eligibility.py` | working |
| Exposure ledger | `exposure.py` | working |
| Cooldowns, caps | `pool.py` | working, two defects (§4, §5) |
| Multi-stage ranking | `ranking.py` | working, one ceiling (§6) |
| Diversity | `ranking.py` | working, see §12 |
| Promotion separation | `promotion.py` | working |
| Privacy opt-out | `preferences.py` | working, one gap (§6) |
| Explainability | `events.py` | working |
| Schema | `schema.py` | working, no ALTER path (§13) |

So the honest headline is: **the engine was sound, and its failures were all of one
kind.** Every defect I found was a *reachability* failure — a control that was correct
in itself but that could not see, or could not be reached by, the thing it was meant to
govern. None of them were logic errors. All four were invisible to a 415-test suite that
passed before and after.

## 2. The non-negotiable principle, and whether it held

The brief's one non-negotiable: separate *"how relevant is this product?"* from *"should
we show this product again?"*

It held, and it was already held before I arrived. `ranking.py` answers the first
question and `exposure.py`/`pool.py` answer the second, and they do not share state. I
preserved that boundary in every change. In particular the new retrieval sources
(§6) produce a *candidate* set and carry no score — a targeted source cannot be used to
push a product past the frequency controls, which is asserted directly by
`test_an_at_cap_listing_is_dropped_however_it_was_found` and three siblings.

## 3. What I actually changed

Four defects, in the order they were found. Each is stated as the measurement that
found it, because each was silent.

| # | Defect | Measured before | After |
| --- | --- | --- | --- |
| 1 | Product cap was never enforced on totals | 5 sightings against a cap of 3 | cap holds |
| 2 | Fatigue cooldowns did not escalate | re-showed at a fixed interval | escalates |
| 3 | Most of the catalogue was unreachable | 17.0% reachable, 70.8% permanently dark | 40.5% / 0.2% dark |
| 4 | Retrieval was viewer-blind, capping personalisation | 11.9% cameras for a camera-only viewer | 48.4% |

Plus one defect I introduced and then caught (§8), which is in this table's spirit the
most interesting entry.

Every number in both columns is regenerated by
`scripts/protection/measure_commerce_discovery_reachability.py`, which is the reason two
of them changed after they were first written — see §6.

**Which of these is actually hurting production today.** This matters more than the table
and cuts against my own work, so it goes here rather than buried:

| # | Biting prod now? |
| --- | --- |
| 1 — cap never enforced | **Yes.** Prod has ~11 eligible listings, which puts every request on the ladder's bottom rung permanently. This is the defect's worst case, and it is the live case. |
| 2 — fatigue did not escalate | **Yes**, for the same reason: a tiny catalogue is where repeat exposure is most likely and escalation matters most. |
| 3 — catalogue unreachable | **No.** Eleven rows fit in the first batch. Latent; triggers at ~540 eligible listings. |
| 4 — retrieval viewer-blind | **No.** With eleven listings every source returns the same eleven rows, so targeted retrieval is a no-op until the catalogue grows. |

So the two *least* impressive fixes in this report are the two that change production
behaviour this week, and the two headline measurements describe a catalogue PulseSoc does
not yet have. Both are still worth landing — a size-triggered silent failure is much
cheaper to fix before the size arrives than after — but the 70.8% and 4.84× figures are
projections about a future catalogue, not descriptions of the current feed, and should
never be quoted as the latter.

Nothing else was added. No new database, queue, cache, vector store, search engine,
worker framework, or analytics system — the brief forbade all of those and none was
needed. The embeddings, the velocity model, the experimentation framework and the load
benchmarks in the brief were **not built**; §15 says so plainly and explains why each
was declined rather than deferred.

## 4. Defect 1 — the product cap counted nothing

`pool.build` walked a relaxation ladder: ask with full spacing, and if too few
candidates come back, ask again with less. The bottom rung divided the product cooldown
by four.

On the shopping surfaces that bottom rung was not an emergency path — it was the routine
case, because a marketplace with a few sellers hits the low watermark immediately. So
**the nominal product cooldown was a quarter of its configured value in practice**, and
because nothing anywhere checked a *total*, one product reached five sightings against a
configured cap of three.

The distinction that fixes it: a cooldown is *spacing* and must yield to scarcity; a cap
is *volume* and must not. `product_cap` is now deliberately absent from the ladder. A
thin catalogue is an argument for showing something sooner than preferred; it is not an
argument for showing it more times than allowed.

## 5. Defect 2 — fatigue did not escalate

A viewer who had seen a product three times waited exactly as long for the fourth
sighting as they had for the second. `_escalated(cooldown, seen) = cooldown * max(1, seen)`
now applies in both `_reject` and `_hard_exclusions`.

Linear, not exponential, and that is a choice rather than an oversight: exponential
backoff on a small catalogue removes inventory permanently after a handful of
impressions, which is the failure the ladder in §4 exists to prevent. Linear escalation
spaces a repeat without retiring the product.

## 6. Defect 3 — most of the catalogue could not be reached at all

This is the one I would flag hardest if only one thing from this report survives.

`exposure.rotation_offset` returned `(digest % 4) * 60`. Four slots, sixty per batch —
so **the deepest row any viewer could ever read was offset 540, a constant, independent
of catalogue size.** On the ~100-item catalogues every test fixture builds, one blind
ordering already reaches everything, so nothing failed. On a real catalogue:

- 300 items: 100% reachable.
- 2,000 items: **17.0% reachable, 70.8% permanently dark.**

Not "ranked low". Never retrieved, by any viewer, ever. A seller whose stock sorted past
offset 540 was invisible to the entire platform, and no metric anywhere would have said
so — the feed looked healthy because it was full.

The fix is a cached eligible-catalogue-size count feeding ceiling division, with the
configured slot count as a floor. Ceiling division matters: the last partial batch is
exactly the tail that would otherwise stay dark. After: 40.5% reached per rotation
period, **0.2% dark**.

**Every number in this section and the next is regenerated by
`scripts/protection/measure_commerce_discovery_reachability.py`.** It drives the real
`engine.serve` over 60 rotation epochs × 4 surfaces on a 2,000-row catalogue, in four tree
states, and fails if a figure quoted here has drifted from what the code now does. The
older states are reconstructed by re-applying, in a sandbox, the three reverts the
`prove_*` harnesses already label "the true pre-fix" path — history is not on this branch.

Writing it changed two figures. The before column originally read *18.7% reachable* here
and *14.1% cameras* in §7; measured, they are **17.0%** and **11.9%**. Both had been
recorded during the work, against a tree that also predated the cap, fatigue and signal
fixes, and the reproducer can only revert the four mechanisms these two measurements
depend on. So the pre-fix state was slightly *worse* than first written, not better — the
report was understating its own improvement — but the direction is not the point. The
point is that eight citations of a number rested on prose nobody could re-derive, which is
the same defect as the mutation harnesses once living in `/tmp`. The figures that did
reproduce exactly, on the first run: **70.8% dark**, nothing past ordering row **585**,
**0** cameras of **960** placements, and every number in the after column (40.5%, 0.2%,
shallowest id 6, 48.4%, 4.84×).

**How much of this is biting production right now: none of it.** The prod marketplace has
roughly eleven eligible listings, all owned by a single seller. Eleven rows sit entirely
inside the first batch, so every one of them is reachable today and the fix changes
nothing for a live user this week. I am stating that plainly because the numbers above are
from a 2,000-item fixture and it would be easy to read them as a description of the
current production feed. They are not.

What they *are* is a description of what happens the moment the catalogue grows. The
defect is latent, it is size-triggered, and the threshold is about 540 eligible listings —
low enough that a single successful seller onboarding push crosses it. It would then fail
silently, because a feed full of the first 540 products looks exactly like a healthy feed.
That is the argument for fixing it now rather than when it starts costing sellers
impressions.

## 7. Defect 4 — retrieval was viewer-blind, so personalisation had a ceiling

`ranking.py` gives an affinity bonus for a category the viewer has engaged with. It
worked. It had nothing to work on: `pool._fetch` ordered by `featured DESC, updated_at
DESC, id DESC` with no reference to the viewer at all, so ranking could only reorder rows
that a viewer-blind query had already chosen.

**Ranking cannot surface what retrieval never fetched.** Measured: a viewer whose only
twenty clicks were all on cameras, against a catalogue 10% cameras, was served **11.9%
cameras** — 1.19× lift where the affinity weight implied far more. The signal was
firing into an empty room. (On the full pre-audit tree, with the reachability fix also
absent, the same viewer got **0 cameras out of 960 placements** — the cameras sat at the
deep end, so they were not merely outranked, they were never fetched.)

The fix asks the *same eligibility query* several times with one extra `AND`:

| source | restriction | share |
| --- | --- | --- |
| `affinity` | viewer's engaged categories | 0.35 |
| `followed` | viewer's follow graph | 0.25 |
| `trending` | windowed engagement pre-query | 0.20 |
| `rotation` | none — the original blind ordering | remainder |

After: **48.4% cameras (4.84×)**, same 960 placements filled, reachability unchanged.

**The two fixes are not independent, and the measurement says so in a way worth reading
before §16's rollout order.** With multi-source retrieval present but the span fix absent,
the camera viewer gets **49.9%** — targeted retrieval reaches the deep end for a category
the viewer has engaged with, whatever the rotation offset is doing. General reachability in
that state is still **17.0%**. So shipping §7 without §6 would make the deep end visible
to viewers with a profile and leave it dark for everyone else, including every new user,
while the aggregate personalisation metric improved. That is a defect that looks like a
win on a dashboard, and it is the argument for landing them in the stated order rather
than the other way round.

Restraints, all of them recorded in the code rather than only here:

- Every targeted source **narrows**. None can widen the eligible set, so none can admit
  a product the gates reject. Four tests assert this directly.
- Shares are fractions of the pool target, so **no source can crowd out the others**.
- Targeted sources do **not** rotate; at subset scale, cooldowns pushing seen rows out
  of the SQL is the right mechanism and a second offset space would be redundant.
- Query budget is `max_batches + 3 * (max_batches // 3)`, so three extra questions cost
  at most three extra partial budgets — not four full ones.
- Saved products feed **ranking but not retrieval**. A save is a strong interest signal
  and a weak *novelty* signal; retrieving on it means showing people what they have
  already bookmarked.
- A viewer with no history gets `rotation` alone, byte-identical to the previous
  behaviour. Asserted row-for-row, not merely by count.

## 8. The defect I introduced, and how it surfaced

Worth its own section because it is the most instructive thing in this report.

My first version of `trending` counted **all** engagement rows regardless of
`subject_ref`. On a catalogue this size, one viewer's twenty clicks were enough to make
those exact twenty listings the busiest rows in the window. So "trending" partly meant
*"you clicked it"*, and the source became a **fourth route back to the products the
viewer had just engaged with** — arriving with its own quota and its own provenance,
under a name that reads like a crowd signal. The exposure ledger would still have spaced
repeat impressions, but retrieval would have kept re-proposing the same rows. That is
precisely the anti-repetition principle in §2, defeated by the thing I added to help.

It surfaced because a *provenance* test expected the deep cameras to be credited to
`affinity` and got `{'affinity', 'trending'}`. I had written that test to check a label;
it caught a design error. The fix excludes the asking viewer's own events, and
`subject_ref` is taken off the policy object every caller already passes rather than
being a parameter of its own — so a new call site cannot forget it and quietly get the
circular version back.

Re-measured after the fix: **48.4%**, against 48.5% before. The circularity was
contributing nothing to the headline number. Affinity was doing all of the work, and the
fix was free.

## 9. Tests

18 files, 444 tests, all passing, one pytest process per file.

| file | tests |
| --- | --- |
| `test_retrieval_asks_several_questions.py` | 29 (new) |
| `test_value_is_reconciled.py` | 29 (new) |
| `test_repetition_is_observed.py` | 25 (new) |
| `test_signals_tell_the_truth.py` | 23 (new) |
| `test_exposure_is_capped.py` | 20 (new) |
| `test_diversity_is_operative.py` | 19 (new) |
| `test_the_catalogue_is_reachable.py` | 17 (new) |
| `test_fatigue_escalates.py` | 12 (new) |
| *(ten pre-existing files)* | 246 |

All eight new files are registered in `config/ci_test_manifest.json`; the manifest gate
is default-deny and would fail the whole protection suite otherwise.

## 10. Why the tests needed mutation harnesses

**All 415 pre-existing tests passed unchanged both before and after every fix in this
report.** That is not a reassuring fact, it is the central finding: the suite was blind
to a defect that made 70.8% of the catalogue unreachable, because every fixture builds a
~100-item catalogue where one blind ordering already reaches everything.

A test file written against a defect I had just fixed myself would be worthless if it
merely described the new code. So each chapter got a mutation harness that reverts the fix
and asserts the new tests go red. All five live in `scripts/protection/`, and all five were
re-run from that location to confirm the results below are reproducible rather than
remembered:

| harness (`scripts/protection/prove_commerce_discovery_…`) | mutants | result |
| --- | --- | --- |
| `…_signal_defects.py` — the four ranking signals | 1 combined revert | killed by 14 tests |
| `…_value_tiers.py` — purchase/intent value | 13 | all 13 killed |
| `…_fatigue.py` — cap and escalation | 28 | 26 killed, 2 surviving by documented design |
| `…_reachability.py` — catalogue span | 12 | all 12 killed |
| `…_sources.py` — multi-source retrieval | 25 | 23 killed, 2 surviving by documented design (§11a) |

The retrieval headline mutant — `_sources` returning only the untargeted rotating source,
which is the pre-fix code exactly — is killed by **12** of the 29 tests in its file. That
is the number that says the file describes new behaviour rather than restating old.

Each harness's headline mutant restores the pre-fix code *exactly*. If that mutant does
not kill a large fraction of the new file, the file is describing behaviour that was
already true.

Each also checks its own baseline before mutating anything and aborts if the unmutated
suite is red, because a mutant "killed" by an already-failing test proves nothing. And the
summary line distinguishes *killed* from *survived as designed* rather than printing "all N
killed" over the top of two deliberate survivors — the difference between "nothing escaped"
and "nothing escaped unexpectedly" is the entire point of the `EXPECTED SURVIVOR` labels,
and collapsing it is how a harness ends up quoted as proving more than it does.

These are **manual** harnesses, deliberately not wired into CI. Each one copies the repo
into a temporary directory, mutates the copy, and runs pytest against it once per mutant —
minutes of work for a signal that only changes when the tests or the fixes change. CI runs
the resulting test files; the harnesses exist to answer "are those files worth running?",
which is a question you ask when writing them and when changing them, not on every push.

They were nearly lost. All five were written in `/tmp` and would have gone with the next
cleanup, leaving this section citing evidence nobody could reproduce — the report's central
claim about its own tests would have become unfalsifiable. Moving them in surfaced two real
problems: every one hardcoded an absolute path to one particular checkout and to one
particular virtualenv, so from any other clone they would have silently proven someone
else's source; and the two oldest mutated the working tree directly, restoring it in a
`finally`, which leaves a mutant in real source if the process is killed mid-run. Both are
fixed — paths derive from `__file__` and `sys.executable`, and all five now mutate a
throwaway copy, so there is no restore step that can fail.

## 11. What the harnesses caught in my own tests

The Chapter 7 harness's first run left ten survivors. Two were invalid mutants; **six
were real holes in tests I had just written and believed**:

- `test_a_followed_sellers_listings_are_retrieved_from_the_deep_end` passed
  `followed_sellers` in by hand, proving the pool *can* use follows and saying nothing
  about whether anything ever hands them over. Mutating the engine to pass `()` left the
  file green — the capability was tested, the wiring was not.
- The trending-failure test monkeypatched `_trending_ids` to return `()` and then
  asserted it returned `()`. Mutating the real `except: return ()` to `raise` left it
  green. It now drops the table.
- `observe_sources` was tested by calling it directly, so deleting the engine's call to
  it changed nothing.
- The empty-mix test asserted the return value but not the absence of a log line.
- The query-budget bound was asserted on a catalogue rich enough that the pool filled
  before any budget ran out, so raising every budget did not change the query count.
- `MAX_SOURCE_TERMS` was a comment, not an assertion.

All six now have tests. This is the part of the process I would defend hardest: writing
tests for your own fix and then never checking whether they can fail is how a suite ends
up with 415 green tests and a 70.8%-dark catalogue.

**And then the replacement test was nearly vacuous too.** My first attempt at the
query-budget test used a *scarce* catalogue, on the theory that scarcity forces scanning.
It does the opposite: the `len(fetched) < batch_size` short-circuit ends a source after
one query when there is little to read. The test passed in 0.08s having proved nothing.
The budget is the binding constraint in exactly one shape — the query keeps returning
*full* batches and the rows keep being *rejected* — so the test now puts every seller
inside their cooldown, and asserts `batches > max_batches` **before** asserting the
ceiling. Without that first assertion a scenario that stops after two queries satisfies
any ceiling, which is precisely how the two budget mutants got through the first time.

**And the harnesses themselves were nearly unverifiable.** All five were written in `/tmp`.
Had they been left there, §10 would cite 79 mutants that nobody could re-run, which makes
the report's strongest claim — that these tests are capable of failing — a claim you either
take on trust or discard. Moving them into `scripts/protection/` was not filing; it turned
up two defects in the harnesses (a hardcoded checkout path that would make a committed
harness silently prove someone else's source, and an in-place mutation that leaves a mutant
in real source if the process is killed) and required re-running all five to confirm the
numbers are reproducible rather than remembered. One of them — `…_signal_defects.py` — had
never reported a kill count at all; it printed pytest output for a human to read. It now
counts, and the count is 14.

## 11a. Two mutants that *should* survive, and why that matters

A surviving mutant is not automatically a gap. Two of the twenty-five survive by design,
and each one surviving is itself a property worth asserting:

**`_hard_exclusions` dropped for targeted sources.** It is an optimisation — it removes
in SQL what `_reject` removes in Python — so dropping it must change the query *cost* and
not the accepted rows. A test failing here would mean correctness had migrated into the
optimisation, which is the dangerous direction. Under-exclusion is cost-only;
over-exclusion silently destroys inventory.

**The per-source budget, with the outer loop's absolute ceiling left in place.** These two
guards are deliberately redundant and the ceiling is the binding one, so removing the
budget alone changes no behaviour and no test can or should fail. The companion mutant
that removes the *ceiling* is killed. Between the two, each guard is covered; neither
alone is a valid mutant. It took two rewrites to work out which of the pair was actually
load-bearing.

This same pattern appeared twice more. `rotation`'s unconditional presence is guaranteed
both in `_sources` and again by `_scan`'s `sources or (rotation,)` fallback — so making
`_sources` return nothing changes no behaviour at all. In the reachability chapter,
mutating only the span guard to `span is not None` was absorbed by `max(slots, 0)`.

The general lesson, which I would carry to any future mutation work in this repo: **a
guard held in two places produces a mutant that looks like a test gap and is not.** The
distinguishing question is always "did behaviour actually change?" — and if the answer is
no, the mutant is invalid and needs rewriting to remove every copy of the guard.

## 12. Findings I am reporting but did not change

Each of these is a real property of the live system. I am not fixing them in this pass,
for the reason given.

**`DROP_CODES` systematically under-reports the controls that work best.**
`pool._hard_exclusions` removes cooled-down rows in SQL before `pool._reject` can count
them, so the counter for a control measures how many rows *leaked past the
optimisation*, not how often the rule fired. Worse, the engine only logs the drop
counters when the pool comes back empty. Any dashboard built on these numbers will
conclude the cooldowns barely fire. Not fixed because the fix is an observability
redesign, and changing what the counters mean mid-flight is worse than documenting it.

**`engine.serve`'s broad `except` converts a signature error into a silently empty
surface.** A `TypeError` from a bad call becomes an empty feed, not a 500 — indistinguishable
from a viewer with no eligible inventory. This is the same silent-vanishing pattern
`CLAUDE.md` warns about for route packs. Not fixed because narrowing it is a
availability change that deserves its own decision.

> **Half of this is now closed — see §19, and §20 for the rest of the package.** The
> paragraph above under-rates it: the handler was not only hiding failures from production,
> it was hiding them from **48 of this report's own tests**, which were green against an
> engine that could not run. That half is fixed, without touching the handler. The
> availability half — a caller still cannot distinguish a crash from a decision — stands as
> written, and §82 is the reason it stays that way.

**`diversity_bonus` is 0.07 and I think it is too low.** I pinned it in a test rather
than changing it: it is a product judgement about how much variety to buy with relevance,
and the brief does not authorise me to make that trade unilaterally. Flagged as an open
question, not a defect.

**`services/pulsedrop/` is a second commerce curation system, and the two share no
frequency ledger.** PulseDrop landed on `main` in #69 while this work was in progress,
with its own `diversity.py`, `eligibility.py` and `ranking.py`. This report must not be
read as claiming `commerce_discovery` is the only commerce curation path on the platform.
I reconciled the two far enough to state the gap precisely, and the gap is not the one
I expected.

There is **zero cross-reference in either direction** — `git grep commerce_discovery`
over `services/pulsedrop/` and `git grep pulsedrop` over `services/commerce_discovery/`
both return nothing. Neither system can see the other's history:

- `commerce_discovery`'s fatigue layer reads one table, `commerce_discovery_impression_events`,
  keyed on `subject_ref` (`exposure._load_impressions`). Only its own surfaces write it.
- PulseDrop's fairness layer reads one table, `pulsedrop_publications`, keyed on
  `listing_id`/`seller_user_id`/`category`/`surface`/`published_at` (`diversity.History.load`).
  **It has no viewer column at all.** Its cooldowns are platform-global editorial spacing,
  not per-viewer suppression. Its `eligibility.py` joins `marketplace_listings`,
  `marketplace_saved_products`, `marketplace_buyer_interest`, `marketplace_orders`,
  `marketplace_reports`, `users` and `marketplace_sellers` — not one `commerce_discovery`
  table.

**They overlap in one literal list, which I checked at the render layer rather than
inferring from the surface names.** `HomeScreen.tsx` calls `useFeedCommerce`, which fetches
`surface="feed"` placements and hands them to `injectCommerceRows` — the client interleaves
a `CommerceFeedCard` into the post list roughly once per eight posts (`config.py`'s own
summary: "a feed unit once per eight posts, one reels chip"). PulseDrop's output is a
`pulse_posts` row from the system account, i.e. an ordinary post in that same list.

So one listing can reach one viewer twice in the same scroll: once as an injected commerce
card, which writes `commerce_discovery_impression_events` and counts against `product_cap`
(3), and once as a PulseDrop post a few rows away, which `commerce_discovery` never learns
about. On `reels` the shape differs — commerce_discovery attaches a *chip to an existing
reel* (`ReelsScreen`'s `chipByReelId`, budgeted at one per session) rather than inserting a
unit — but a PulseDrop reel and a chipped reel are still two sightings of one product with
one of them uncounted.

**The honest statement of the defect is narrower than "the exposures add up", because
they are not in the same units.** `product_cap` bounds impressions per viewer; PulseDrop's
`PULSEDROP_PRODUCT_COOLDOWN_HOURS` (336) bounds publications per platform. A PulseDrop post
is a durable feed object, so how many times a given viewer sees it is decided by the pulse
feed's own dedup, not by either commerce system. The two numbers cannot be summed, which
means **there is currently no quantity anywhere in the platform that answers "how many
times has this viewer been shown this product?"** That question is the entire premise of
the fatigue layer in §6, and it is answerable only within `commerce_discovery`'s own
surfaces.

**This is latent, not live.** `PULSEDROP_ENABLED` defaults to `"false"`
(`pulsedrop/config.py` `SETTINGS`), and prod has no `PULSEDROP_*` variable set at all —
`railway variables --service CoinPilotX | grep -i pulsedrop` is empty. The DB override
path (`pulsedrop_settings`) requires an admin to write rows into a table that shipped two
commits ago. So today PulseDrop publishes nothing and the overlap has never occurred.

**Recommendation, not a fix.** The cheap version is for PulseDrop's publisher to write an
impression row into `commerce_discovery_impression_events` when its post is rendered to a
viewer, which would make its exposures visible to `product_cap` for free. I did not do it:
it means editing PulseDrop, which is outside the package this report audited and landed
after it was scoped, and a shared ledger is a design decision about which system owns
viewer-level frequency — not a defect fix. What must not happen is enabling PulseDrop
while believing §6's caps cover the platform. They cover `commerce_discovery`.

## 13. Why per-row provenance is not persisted

> **Closed by §18** (commit `87a2f1b50`). The blocker below was real and is now removed:
> `schema.py` has an additive-column path, and per-placement provenance is recorded on a
> `relationship` column. The section stays because the *diagnosis* is the part worth
> keeping — a missing schema capability was silently deciding a product question — and
> because the trap it names still applies to the next column anybody adds.

`candidate_source` exists on every pool row in memory and is exposed in aggregate via
`PoolResult.sources` and `metrics.observe_sources`. It is deliberately **not** written to
`commerce_discovery_placements`.

The reason is a property of this package's schema layer, and it generalises: the DDL is a
module-level tuple of `CREATE TABLE / CREATE INDEX IF NOT EXISTS` statements with **no
ALTER path**. Adding a column to a declaration applies on a fresh database and **silently
does not apply** to the table already in production. So a persisted `candidate_source`
would work perfectly in every test and be permanently absent in prod. The aggregate log
carries the operational value without that trap.

This also means `events.explain` cannot report provenance — it reads the persisted
placement row. Stated here so nobody goes looking for it there.

## 14. Verification actually run

- 18 files, 444 tests, one process per file: green. Also green as a single whole-directory
  run, which is not the same check — per-file is how CI runs them, whole-directory catches
  cross-test state leaking through import-time DB setup.
- `tests/protection/test_every_test_file_is_run_by_ci.py`: 9 passed. All eight new test
  files are declared in `config/ci_test_manifest.json`; that gate is default-deny, so an
  undeclared file would fail the whole protection suite rather than simply not run.
- `tests/protection/test_environment_contract.py`: 14 passed.
- `tests/protection/test_route_auth.py`: 12 passed.
- `tests/protection/test_fixture_audits_cannot_reach_production.py`: 3 passed, re-run after
  adding five scripts to `scripts/protection/` since that gate inspects that directory.
- Five mutation harnesses (§10), each re-run from its committed location.
- `scripts/protection/measure_commerce_discovery_reachability.py`: exit 0, all 11 figures
  this report cites reproduced. Reachability and affinity measured end-to-end through
  `engine.serve` on a 2,000-item catalogue over 60 rotation epochs × 4 surfaces, in four
  tree states.

**That measurement script did not exist until after the second commit, and writing it
found two wrong numbers in this report.** The five harnesses prove the tests have teeth;
none of them prints a measurement, so §6 and §7's before/after figures lived only in prose
in three files and nothing could check them. That is the same defect as the harnesses once
living in `/tmp` — evidence in a form nobody else can regenerate — and it took eight
citations of `70.8%` and four of `14.1%` before I noticed it. Corrected: 18.7% → **17.0%**
reachable and 14.1% → **11.9%** cameras, in the report, `exposure.rotation_offset`'s
docstring, and both test-file docstrings. The after column and `70.8%` itself reproduced
exactly on the first run.

**The mobile side, which this list was missing until after the first commit.** Four
`mobile-native/` files changed, and none of the checks above touch them — the Python suite
cannot see a TypeScript error. `mobile-native/` had no `node_modules` in this worktree, so
for a while the honest status of those four files was "unverified", which is not the same as
"fine". Now run:

- `tsc --noEmit`: exit 0.
- `node scripts/validate-i18n.mjs`: OK, 11 locales at 100%. This change adds no user-facing
  string — the only new text in the mobile diff is a comment — so the i18n gate was never at
  risk, but a green gate is worth more than that argument.
- `jest` on the two changed test files: 2 suites, 58 tests, passing.
- `jest src/commerce`: **17 suites, 350 tests, passing.** This is the one that mattered and
  it was nearly missed. `src/api/commerceDiscovery.ts` is imported by six hooks —
  `useFeedCommerce`, `useReelsCommerce`, `useMessengerCommerce`, `usePostDetailCommerce`,
  `useProductDetailCommerce`, `useMarketplaceCommerce` — none of which is a file I touched,
  and all of which have their own suites. Running only the changed files' tests would have
  left a type-compatible wire-shape change unexercised against every consumer of it.
- `jest` (whole suite): **534 suites, 9,330 tests, passing in 33s.** Cheap enough that
  scoping it was false economy.

A note on how that was made possible, because it is a trap: `npm ci` is known to break
`patch-package`'s postinstall in this repo and silently drop the patches. Instead the main
checkout's `node_modules` was APFS-clone-copied in (`cp -Rc`, ~7s, blocks shared), which
preserves the relative `modules/*` symlinks so they resolve against *this* worktree. One
link had to be added by hand — this branch's `package.json` declares
`pulse-apple-translation` and the main checkout's does not, so its tree was missing that
entry. Nothing was written into the main checkout, and `node_modules/` is gitignored.

Not run, and I will not claim otherwise: device QA and load benchmarks at 10K/100K/1M
products. See §15. "No verification against production data" was also true when this
section was written and is no longer — §17 measures the suitability gate against live
Railway Postgres, read-only. Nothing in §§4–7 was ever measured against prod.

## 15. What the brief asked for that I did not build

Listed as declined-with-reason rather than quietly omitted. The brief's own constraint was
*"Do not introduce another database, queue, cache, vector database, search engine, worker
framework, or analytics system merely because it is mentioned here"* — most of this list
falls under that instruction rather than against it.

| Asked for | Status | Why |
| --- | --- | --- |
| Semantic embeddings | not built | needs a vector store; the brief forbids one. Category affinity delivered 4.84× lift without it, so the marginal case is unproven. |
| Velocity/trending model | partly | `trending` is a windowed **count**, deliberately not a rate. `ranking` already divides clicks by impressions; a second rate here would be a staler opinion about the same thing, and the two would disagree. |
| Experimentation framework | not built | `PulseExperiments` already exists on the platform — shipped but inert. Building a second one is the exact duplication the brief warns against. Wiring the existing one is a separate mission. |
| Load benchmarks at 10K/100K/1M | **not run** | I measured reachability and affinity on 2,000 items. I have no data at 1M and will not imply otherwise. The reachability fix adds one cached `COUNT`; the sources add ≤3 partial batch budgets. Both are bounded by design, neither is benchmarked at scale. |
| Always-on workers | not built | no worker was needed; everything added is request-path with a TTL cache. |
| Cross-surface fatigue | pre-existed | `exposure.surface_age` already tracked it. |
| Cursor pagination, session memory | pre-existed | already in `events.py` / `exposure.py`. |
| Frontend primitives, app+web parity | out of scope this pass | the four defects were all server-side retrieval. |
| Chaos tests, security audit | partial | the harnesses cover failure injection for the paths I touched (dropped table, failed query, empty pool). A security audit was not performed. |
| Data retention | not touched | no new table was created, so no new retention question arises. |
| Repetition metrics + alerting | **see §12** | the counters exist and are misleading. I documented that rather than building a dashboard on top of numbers I know to be wrong. |
| Creator product tagging | **built after this table was written — §24** | it was the one item here that turned out to be neither forbidden by the brief's no-new-infrastructure rule nor already present under another name. `pulse_content_products` is a join table modelled on `pulse_content_music`, which is the opposite of a parallel system. The **composer UI** is still not built and is now the larger half of the remaining work. |

One row of that table needs correcting rather than quietly editing: "Data retention — not
touched — no new table was created, so no new retention question arises." §24 created one, so
here is the retention answer, checked rather than assumed.

`pulse_content_products` holds two user ids, both already foreign keys throughout this schema,
and no other personal data. **Its rows outlive a deleted post**, and that is not an oversight
inherited from `pulse_content_music` — I went looking for the cascade that precedent was
supposed to provide and **there isn't one, for music either.** Post deletion in PulseSoc is
*soft* (`UPDATE pulse_posts SET deleted_at=…`, six call sites); the row stays and so do its
attachments. The two `DELETE FROM pulse_content_music` statements in `bot.py` are in the
*audio-replacement* route, not a deletion path.

So what stops a deleted post serving its tags is **not** referential cleanup. It is the
suitability gate: `_content_post` filters `deleted_at IS NULL` in the query, an absent row is
`_ROW_ABSENT`, and `_content_refusal` refuses on absent — so retrieval never runs and the tags
are never resolved. That is a real protection and it is already tested
(`test_a_deleted_post_is_filtered_in_the_query_not_afterwards`), but it means the property
"a deleted post shows no products" rests on the gate rather than on the data, which is worth
knowing before anybody moves the gate. `test_a_deleted_post_serves_no_tags_and_the_rows_remain`
now pins both halves in one place, including the retention fact, so the next reader does not
have to re-derive that there is no cascade.

The residual retention question is genuine and belongs at delivery-report item 62: tag rows
for hard-deleted listings are dropped from every *read* by the JOIN, but are never *removed*.
That is a storage question, not a correctness or privacy one.

## 16. Rollout

**My recommendation: do not ship this as one change.**

These fixes alter what every feed user sees, how often they see it, *and* which products
can be retrieved at all. The reachability fix in particular takes 70.8% of a large
catalogue from never-shown to shown — for a real seller that is a step change in
impressions, and it is the kind of change that looks like a bug to whoever is watching
the graphs.

Suggested order, each independently reversible:

1. **Cap enforcement** (§4) and **fatigue escalation** (§5). Lowest risk; both only ever
   show *less*.
2. **Reachability** (§6). Highest impact, and the one to watch seller-level impression
   distribution on.
3. **Multi-source retrieval** (§7). Ship behind a flag; `interests=()` and
   `followed_sellers=()` reproduce the previous behaviour exactly, which makes the
   off-switch a two-line change rather than a revert.

Note that there is **no per-surface kill switch** in this package today. Turning
discovery off on one surface is not currently possible without a code change.

**One ordering constraint from outside this package:** do not enable PulseDrop
(`PULSEDROP_ENABLED`, off everywhere today) in the same window as stage 2. Both change
how much product a feed carries, they share the `feed` and `reels` surfaces, and per §12
neither counts the other's exposures — so if impression distribution moves you would not
be able to attribute it. Either is safe alone.

The decision is the user's. **Nothing is pushed or deployed**; §§17–18 add further
increments to the same branch and the same statement covers them.

### What §18 adds to this order

§18 contains two changes with very different risk profiles, and they should not travel
together.

**Ship the payload strip (§18.6) first, and on its own.** It is the only change in this
report that is one-way safe: `_buyer_safe` exclusively *removes* keys from a product
payload, so no client can begin receiving something it did not receive before. Nothing in
`mobile-native/` or `templates/` reads `seller_risk_score` or `candidate_source` — they
were never part of a documented response shape, they arrived by accident of a denylist
serializer — so there is no consumer to break, and the revert is a one-line restoration of
`serialize(row)`. Against that, the cost of *waiting* is not neutral: until it ships, every
commerce-discovery card on every surface continues to hand the buyer's device an internal
risk assessment of a named store and a readout of which retrieval question produced the
card. It has the best ratio of harm-removed to blast-radius of anything here, and it does
not depend on the schema change below.

**The `relationship` column (§§18.1–18.5) is additive and invisible.** One nullable `TEXT`
column on `commerce_discovery_placements`, no default, nothing backfilled — existing rows
keep `NULL`, which `normalize` already treats as "unknown" rather than silently as
`catalogue`. It is **not on the wire in either direction**: no client sends it,
`PIPELINE_ONLY_FIELDS` exists specifically to stop it being returned, and no route reads it
yet. So it carries no client risk, and its only real failure mode is the schema path
itself — which is why §18.2's ALTER-after-CREATE ordering and its PostgreSQL translation
are pinned by tests rather than argued for in prose. It can ship in any of the stages
above, or between them.

What the column does *not* do is change ranking, eligibility, capping, or what any buyer
sees. A placement that would have been served before is served now, in the same slot, with
the same `reason_code`. The column records how it got there; it does not decide.

### What §§19–22 add to this order: nothing, and one thing

§§19–22 need no rollout stage. They are tests, a conftest guard and an audit script; the
three commits touched no file under `services/`, and that is checked rather than asserted —
`git hash-object` on `services/commerce_discovery_routes.py` matches
`git rev-parse HEAD:services/commerce_discovery_routes.py` at
`d5c01ccfda4c33d9de0527206c15c4f357c8272d` on both sides of both mutations in §22. Ship
them with any stage or none.

They do surface exactly one production change, and it is the `_listing_stats` log level
quoted in §20. Putting it here rather than doing it is the whole point of deferring it, so
here is the decision in the form it needs to be taken:

**Proposed: `engine.py:533` and its sibling move from `LOGGER.debug` to `LOGGER.warning`.**

- *Why it matters more than a log level usually does.* `_listing_stats` is not a
  per-card decoration; with no stats every listing looks unproven at once, so a silent
  failure here re-ranks every card on every one of the six surfaces simultaneously. At
  `debug` there is no signal that it happened — not in production, and (until §20) not in
  a test either.
- *Why it is not free.* The read is per-listing, so the worst case is a warning per
  listing per request on a path that runs on feed and reels. If the failure is systemic —
  a dropped column, a dead table — that is not one warning, it is a log flood on the
  hottest read in the feature, and log volume on Railway is a cost line.
- *The middle option, which I am not choosing unilaterally because it is a judgement about
  what an on-call engineer wants to see:* warn once per process (a module-level
  `_warned` flag) and keep `debug` for the rest. That gets the signal without the flood,
  at the cost of hiding a failure that starts mid-shift after an earlier one was already
  reported.

This is a §1 escalation only in the sense that it is the first item in this report whose
right answer depends on how the operator watches production rather than on what the code
does. Any of the three is defensible; picking one without knowing which dashboards exist
would be guessing, and a guess about observability is how `debug` got there.

## 17. The suitability gate was wired to the wrong text

Written after §§1–16, and it reports a defect in work this report already described as
done. §16's four fixes are about *which* product is shown. This section is about *whether*
one is shown at all — the brief's own rule, "every post may be commerce-capable; not every
post should display commerce" — and the gate enforcing it was reading the wrong thing.

### What was wrong

`services/commerce_discovery/suitability.py` was correct and tested. The route called it
on the wrong input.

The serve endpoint received a `context` — a topic, a category, up to twelve tags — built
by `mobile-native/src/commerce/postContext.ts`, which caps every field at
`MAX_FIELD_CHARS = 80`. That cap is right: an unbounded free-text field from a client has
no business being unbounded. But it means the server was judging the client's
eighty-character *summary* of a post rather than the post, and a bereavement post very
commonly opens with a preamble. Measured:

```
len(body) = 158
topic on the wire = 'Thank you all so much for the kind words these past few days, it has meant'
server sees the FULL body : SENSITIVE_CONTEXT   permitted = False
server sees the WIRE topic: PERMITTED           permitted = True
```

And every structural gate was invisible to the wire, because `post_type`,
`moderation_status` and `risk_score` are not fields a client sends. A `scam_report`, an
unmoderated post and a high-risk post all passed.

### The fix

`post_id` on the serve wire; the server reads the `pulse_posts` row and runs
`assess_adjacency` on it, before retrieval. The precedent is `_anchor_context`, which
already replaces a client-described product taxonomy with the listing's own. Then the same
thing for reels, where the words live on `pulse_reels.caption` rather than on the post
body — `pulse_reel_payload` sets `caption = reel.caption or post.body` — so both rows are
read and either may refuse.

Three distinctions are encoded rather than defended, and each is a place where the obvious
code is wrong:

| Distinction | Why |
| --- | --- |
| A post that is **gone** refuses; a post we could not **read** does not | Conflating them turns one bad minute on the database into commerce disappearing from every post page, while the posts that genuinely need suppressing carry on being served for as long as the query works. |
| The wire check still runs **first** | It is the only check that sees what a client actually sent, so it is the one a forged id cannot get past. Defence in depth, not alternatives. |
| An absent **reel** row does *not* refuse, unlike an absent post | A video post with no `pulse_reels` row is legitimate; `pulse_reel_payload` synthesises one and serves it. The post row has already been judged by then, so an absent reel adds no text rather than an unknown one. |

`assess_adjacency` and not `assess`: `assess` adds the `NO_SUBJECT` rule and would refuse
every caption-less post. That is the same choice `suitability.annotate` makes for the feed,
and it is not hypothetical — see the 17 below.

### Measured against production

Live Railway Postgres, read-only, 2026-09-27. 2,069 non-deleted `pulse_posts` rows,
`assess_adjacency` run over each.

Reproduce with `scripts/measure_commerce_suitability_cost.py`:

```
railway run --service Postgres python3 scripts/measure_commerce_suitability_cost.py
```

It is committed rather than quoted for §14's reason — a number nobody can regenerate is
prose. Two things about it are load-bearing rather than tidy. It imports `_POST_COLUMNS`
and `_REEL_COLUMNS` from the route module instead of listing the columns again, so it reads
what the route reads. And it *parses* the four wire caps out of `postContext.ts` and
`reelContext.ts` at runtime and aborts if it cannot find them, rather than keeping a Python
copy of a TypeScript constant — which is exactly the client/server divergence this whole
section is about. The session is opened `readonly=True`, so it cannot write even if a later
edit tries to.

**The population moves.** Re-running it hours after the table below was taken gave 2,070
posts, 72 reels and a reel that had not existed at breakfast. The counts here are a
snapshot; the script is the measurement.

| | Posts |
| --- | --- |
| Refused by the **wire** check as shipped | **0** |
| Refused once the **row** is read | **207** |
| — `MODERATION_NOT_CLEARED` (`needs_review`) | 195 |
| — `CONTENT_RISK` (`risk_score >= 30`) | 12 |
| Overlap between the two | **0** |
| Caption-less posts, which `assess` would have blanket-refused | 17 |

Every one of the 207 is a post the shipped check let through, and the shipped check
refuses nothing at all. That is the honest description of the gate before this change: on
the content surfaces it was not doing anything.

So **10.0% of live posts (207 of 2,069) stop showing a commerce card on `post_detail`** —
9.4% of them for moderation, 0.6% for risk score. That is not a new policy.
`CLEARED_MODERATION_STATES` is already default-deny and the feed already enforces it
through `suitability.annotate` stamping `commerce_suitable` on every post in the payload.
`post_detail` was the inconsistent surface; this makes it consistent. Whoever watches the
graphs should still be told, because a 10% drop in one surface's commerce impressions looks
like a bug.

All 17 caption-less posts are row-permitted, so `assess` would have cost every one of them
its card for having no derivable subject — a refusal on top of the 207, for a population
with nothing wrong with it.

Supporting distributions, same probe: `moderation_status` is approved 2,294 /
needs_review 196 / blocked 5, no NULLs. `post_type` is text 2,060, live 279, video 109,
image 37, repost 5, `scam_report` 4 — and all four `scam_report` posts are already deleted,
so the post type the rule most obviously exists for has no live instance.

**The reels half changes nothing measurable today, and that is worth stating rather than
implying otherwise.** 72 `pulse_reels` rows, 62 not deleted, all `moderation_status =
approved` with no NULLs, `safety_score` between 90 and 100. Of the 62, **14** join to a
live post; the other 48 hang off a tombstoned one, and `pulse_feed_engine.get_post`
filters `deleted_at IS NULL` and returns `None`, so `pulse_reel_payload` already returns
nothing for them and they do not render. Across those 14: 0 refused by the wire check, 0 by
the post row, 0 by the reel row.

The caption census needed correcting, and the script is what corrected it. An earlier
hand-written probe counted captions across the whole table and reported "39 captioned, 0
differing from the post body". Both halves were measuring the wrong population: 40 of 72
rows carry a caption, but only **1 of the 14 that can render** does, and that one's caption
*does* differ from its post body. The difference is a single newline where the body has a
space — so it changes no verdict, because `_flat` collapses every non-word character to one
space before the phrase search runs — but "0 differ" was wrong, and it was wrong in the
direction of understating the fix.

Restricting to the renderable population is the correction that matters generally, not just
here: a count over rows that `pulse_reel_payload` never returns describes a surface no
viewer sees. The script does the join.

So the caption read remains a structural fix against a shape the code explicitly supports
rather than one the data has produced a consequence for yet. It becomes load-bearing the
moment any edit path writes a caption without rewriting the post body, which is the natural
way to implement caption editing, and at that point the reel a viewer is reading and the row
the gate judges are two different texts. Reel 77 is that shape already, one newline short of
mattering.

### Verification

`tests/commerce_discovery/`: 608 passed (85 in
`test_suitability_gate_is_wired.py`, up from 62 before these two increments).
`mobile-native` `jest src/commerce` plus the api test: 455 passed, 21 suites. `tsc
--noEmit`: exit 0. `i18n:validate`: OK, 11 locales. Environment, route-auth and
route-contract gates: 43 passed. Audio gate: no protected path touched.

Mutation-checked, each harness run from `scripts/protection/` and the source restored and
verified byte-identical afterwards. 8/8 killed on the post half after one survivor was
fixed — `assess` substituted for `assess_adjacency` survived, which was a real coverage
gap: no test covered "a post id, a cleared benign row, and no context", exactly the 17
caption-less posts above. 16/16 on the reel half, including the reel keyed on its own id
instead of `post_id`, `safety_score` projected as `risk_score`, and the screen passing no
resolver at all.

No new table, column, index, flag or env var. Two wire fields — `post_id` on the serve
request, and nothing on the response.

## 18. §6's third axis, and two columns that were leaving the building

Commit `87a2f1b50`. Two independent things, one of which I went looking for and one of
which found me.

### 18.1 The conflation was real, and it was caused by §13

§6 lists seven relationship types and says they "MUST NOT be silently conflated". Before
this increment they were. What made it worth writing down is that it was not carelessness —
**the axis did not exist**, and two others were being mistaken for it:

| axis | question it answers | where it lives |
| --- | --- | --- |
| `promotion_class` | who *funded* the card | `promotion.py` |
| `reason_code` | what the buyer is *told* | `ranking.choose_reason` |
| `relationship` | **how the product got here** | did not exist |

`reason_code` is derived from *signal thresholds* with no knowledge of which retrieval
question produced the row. So a listing pulled from the untargeted `rotation` source earned
`related_to_this_post` whenever its relevance happened to clear 0.6, and was afterwards
indistinguishable from one retrieved *because* it matched the post. That is the conflation,
in the column an operator would actually read.

`candidate_source` does know, and `pool.build` stamps it on every row — but
`metrics.observe_sources` aggregates it to a per-surface count. §13 above records why it
was never persisted, and the reason is a schema capability rather than a product decision:
the DDL was `CREATE TABLE IF NOT EXISTS` with no ALTER path, so a new column would apply on
a fresh database and silently not apply to production. **A missing migration path was
deciding a product question.** That is the shape worth reporting, more than the fix.

*(I had summarised this package as having aggregated provenance away irrecoverably, inferred
from `PoolResult.sources: Mapping[str, int]`. Grepping found `pool.py:664` setting
`row["candidate_source"] = source.name`. The inference was wrong and checking changed the
design: the gap was persistence, not capture. It also turned out to matter for §18.2.)*

### 18.2 Three pieces

**`schema.py` — an additive-column path.** `_ADDITIVE_COLUMNS` plus `_add_columns(cur)`,
called from `ensure_schema` **after** the CREATEs. Two decisions in it:

- *No `IS_POSTGRES` branch, and no `information_schema`/`PRAGMA` probe.* I was part-way
  through writing one — it would have been this package's first engine conditional — before
  reading `services/db.py:811`. `_translate_alter_table` already rewrites every
  `ALTER TABLE … ADD COLUMN` into `ADD COLUMN IF NOT EXISTS` on PostgreSQL. SQLite has no
  such syntax and raises `duplicate column name`, which `_add_columns` treats as success.
  The dialect layer is real; reimplementing it would have been the defect.
- *After the CREATEs, never before.* An ALTER against a table the same pass is about to
  create fails on a fresh database, and on PostgreSQL that failure aborts the transaction
  and takes the CREATEs with it — a brand-new worker boots with no commerce tables, and
  `serve` reports that as "no placements". Now pinned by
  `test_the_alters_run_after_the_creates`.
- *No `NOT NULL`, no `DEFAULT`.* Rows written before the column existed have no
  relationship and `NULL` is the honest spelling. A `DEFAULT` would backfill several
  thousand historical placements with a provenance nobody measured — and `catalogue` is
  precisely the value that raises no suspicion in a report.

**`relationship.py` — the vocabulary.** All seven of §6's values, `assert_servable` as the
write gate, and `classify` deriving the value from what retrieval and ranking actually did.

**`engine.py` — the wiring.** Derived in the scoring loop, where `candidate_source` and
`signals` are both in hand, and carried to `_persist` in a side table keyed by listing id.

### 18.3 An eighth value, flagged rather than decided quietly

§6 enumerates seven types. I added **`catalogue`**. Two of the four live retrieval sources —
`trending` and `rotation` — are neither about the content nor about the viewer, and §6's
vocabulary has no name for that. The options were:

1. Map them onto `contextual` or `personalized`. This is the silent conflation §6 forbids,
   and it would do the most damage to the one audit the axis exists to enable: every
   untargeted card would count as a contextual match, and "is personalization overriding
   context?" would answer *no* by construction.
2. Leave them `""`. Then most placements on a cold viewer carry no relationship, and an
   empty string reads as a bug rather than as a fact.
3. Name the case.

Third. Extending the vocabulary to cover a real case is the rule-respecting move; squeezing
a real case into a name that does not fit is the violation. **Flagged here because §6
enumerated seven and this is an eighth** — an owner who wants the brief's list held exactly
should say so, and the change is one constant plus one test.

`creator_tagged`, `complementary` and `pulsedrop_curated` are declared with **no write
path**, deliberately. Each needs a store this product does not have: a post↔listing relation
(no such table exists anywhere in the repo — `pulse_content_music` is the nearest shape), a
complement graph, and a bridge from the other curator, which is off in production and shares
no ledger with this one. Declaring them now, while nothing depends on them, fixes the wire
values so the eventual write paths land against a vocabulary instead of inventing one each —
the alternative is how `creator_tagged`, `creator-tagged` and `tagged_by_creator` end up in
one column. `classify` never returns one and `assert_servable` refuses them, both pinned, so
an unbuilt feature cannot report itself as working.

`sponsored` is refused outright, for `promotion.assert_unpaid`'s reason: a sponsored
placement recorded here would go unbilled *and* inflate organic reach.

### 18.4 The ordering is §44's, and it is the whole audit

A row can be true on two axes at once — retrieved by `affinity` *and* a good match for the
post. One value gets recorded, and **context wins**.

Not arbitrary. §44 requires that personalization must not override context, and the only way
to audit that is to ask "how many cards on a content surface matched nothing about the
content?". For that to have a true answer, `personalized` must mean *the content did not
match*, not merely "affinity retrieved it". Ordered the other way, an affinity-retrieved
card that also matched the post counts as personalization winning, and the metric reports a
violation that did not happen while hiding ones that did.

Both readers of the 0.6 threshold now share `ranking.CONTEXT_CLAIM_MIN_RELEVANCE`, extracted
from a literal inside `choose_reason`. Two copies would be one edit away from a card
labelled "Related to this post" whose own audit row says the context never matched — the
conflation this axis removes, reintroduced one layer down. The test moves the constant and
asserts *both* readers follow, which `assert x == 0.6` cannot do.

### 18.5 A divergence reachable from the wire

`classify` is gated on whether a contextual claim is **sayable**, not merely on whether a
context arrived. Three terms, each decided elsewhere: `policy.personalized` (a request can
carry a context policy forbids using — `serve` passes `context=None` in that case), a
non-empty context, and `context_reason in REASON_PRIORITY`.

That third term is the interesting one. `messenger` and `marketplace` have **no**
`CONTEXT_CLAIM` entry, so `choose_reason` will not claim relatedness there — yet
`commerce_discovery_routes.py:604` hands both a client-supplied context. Without the term, a
Messenger card would record `contextual` while its own label truthfully claimed nothing of
the kind. Reachable from the wire today, not hypothetical, and pinned per-surface with the
guard asserting the surface still has no claim, so the test cannot quietly become vacuous.

### 18.6 Two columns that were already leaving the building

This is the part I did not go looking for, and it is worse than §18.1.

I first stamped the relationship onto the row dict — following `pool.build`'s own precedent
for `candidate_source`. Before committing I checked whether the row reaches the client.
`_payload`'s docstring said the product half goes through
`bot.pulse_marketplace_listing_payload` *because* that serializer strips the reviewer-only
columns, and that hand-building the payload locally "is the one change that would leak them
to a buyer's phone".

Half true, in the dangerous direction. The serializer is a **denylist** (`bot.py:58378`):

```python
item = {key: value for key, value in dict(listing or {}).items()
        if key not in MARKETPLACE_REVIEWER_ONLY_FIELDS}
```

It removes the columns *it* knows about. Every column this package invents is unknown to it
and ships by default. Driven against the real serializer, two were already reaching buyers:

| column | what it is |
| --- | --- |
| `seller_risk_score` | `COALESCE(ms.risk_score,0)` (`eligibility.py:128`), for the ranker's seller-reliability signal. An internal risk assessment of a named store. `ranking.EXPLAINABLE_FACTORS` already refuses to publish the *reason* derived from it, on the stated grounds that naming it "publishes an internal assessment of a named store" — the raw number is strictly worse than the reason. |
| `candidate_source` | which retrieval question produced the card. A free per-card readout of the retrieval strategy. |

**Why no test could see it, which is the part worth keeping.** `conftest`'s
`SimulatedMarketplace.serve` passes no `serialize`, so every test in the package takes
`_payload`'s `serialize is None` fallback — and that fallback is a hand-written **allowlist**
of nine buyer-visible fields. The fixture models the one path that is safe by construction
and never the one production runs. Reverting the fix leaves **666 tests green** and fails
only the two new ones. A green suite asserting the inverse of production, again, and this
time in the direction of a privacy leak rather than a tuning miss.

Fixed with `engine.PIPELINE_ONLY_FIELDS` and `_buyer_safe(row)` applied *before* the
serializer. Deliberately **not** by widening `MARKETPLACE_REVIEWER_ONLY_FIELDS`: that list
is shared by every marketplace endpoint, and widening it from inside this package would
change payloads this mission has not looked at. The columns are ours; the strip belongs here.
`relationship` is in the list as belt-and-braces and should be unreachable — it is kept off
the row precisely because `candidate_source` demonstrates what happens to anything put there.

`tests/commerce_discovery/test_pipeline_columns_stay_server_side.py` drives a *pass-through*
serializer rather than the fixture's, and separately pins by AST that the real serializer
still opens with a `not in` dict comprehension — so the stand-in cannot drift into flattering
the engine. It also asserts `seller_risk_score` is still in `CANDIDATE_COLUMNS`, so the guard
cannot pass because the column stopped being selected.

**Still open, and reported rather than changed.** The same denylist passes several other
columns to the device that the marketplace's own endpoints also return —
`seller_verification_status`, `seller_status`, `publication_blocker`, `publication_state`,
`approval_status`, `inventory_state`. Those are not this package's inventions and not this
mission's regression, so widening the shared list is an owner decision about the marketplace
payload contract, not a commerce-discovery fix. Worth an audit; `seller_verification_status`
and `publication_blocker` are the two I would look at first.

### 18.7 Verification

`tests/commerce_discovery/` + `tests/protection/`: **1,331 passed**, 37 subtests, in 126s.
The two top-level discovery files: 21 passed, 30 subtests. New: 43 tests in
`test_relationship_is_recorded.py`, 10 in `test_pipeline_columns_stay_server_side.py`, and
`test_schema_durability.py` from 7 to 16.

`test_every_ddl_statement_runs` was asserting `len(executed) == len(schema._DDL)`, which the
additive ALTER made a false negative. Rewritten to containment in *both* directions —
everything declared runs, and nothing undeclared runs — which is what the count was standing
in for, plus the ordering and PostgreSQL-translation tests above. Strengthened, not relaxed.

Five mutations, each reverted and the source confirmed restored:

| mutation | result |
| --- | --- |
| drop the `context_reason in REASON_PRIORITY` term | 2 red — messenger *and* marketplace record `contextual` against a `new_to_marketplace` label |
| put the `PERSONALIZED` check before the context check (inverts §44) | 1 red |
| give `classify` a private `0.6` | 2 red (both threshold tests) |
| declare `relationship` in the CREATE body only — **the production shape** | 1 red, and **only** in `test_schema_durability.py`; all 43 relationship tests still pass, because SQLite gets the column from the CREATE. The clearest evidence that the durability file is the sole defence against this class. |
| hand the raw row to the serializer (restore the leak) | 2 red; 666 others green |

Non-vacuity checked directly before trusting any of it. On `feed` with a matching context the
engine records a *mixed* set — one `contextual`/`related_to_this_post` row beside one
`catalogue`/`new_to_marketplace` row — so the label-vs-provenance agreement test exercises
both directions on real rows rather than one. `product_detail` records 6/6 `similar`;
`messenger` and `marketplace` with a context record `catalogue`.

One new column, `commerce_discovery_placements.relationship`, nullable, no default, not on
the wire in either direction. No new table, index, flag or env var. No protected audio path
touched.

## 19. 48 of this report's own tests were green against an engine that could not run

The last section, and the one I least wanted to write, because it is about §§9–11 — the
sections where this report says the tests are good.

`engine.serve` ends in `except Exception: return []`. That is deliberate and it stays; the
brief's §82 requires that a post render when commerce fails, and a 500 on the feed because
a product query broke is exactly the failure mode this package was built to avoid. But the
fail-safe means a **crash and a decision are the same value to the caller** — an empty
list — and the majority of this package's assertions are assertions about that value.

So I measured it, by putting `raise TypeError` on `_serve`'s first line so the engine could
not answer anything at all, and running the package:

| | failed | passed |
| --- | --- | --- |
| engine fully broken, before the guard | 110 | **558** |
| engine fully broken, after the guard | 158 | 522 |

**48 tests were green against an engine that could not run.** Some of the 558 legitimately
never call `serve` — `ranking`, `relationship`, `schema` and `metrics` have real unit tests
and those are honestly green. The 48 are the *negative* assertions: the opted-out viewer,
the reached session cap, the suppressed surface, the empty pool, the wrong surface name.
Each one asserts that nothing came back, and each one passes exactly as well when nothing
*could* have come back, because `[] == []`.

This is the same confusion as §18.6 one layer up, and the same one `schema.py`'s own
docstring describes for a rolled-back `CREATE`: **a shop that is permanently, quietly shut
is indistinguishable from a shop with nothing to sell.** Every place that confusion appears
in this system, it appears because something fails soft and nothing downstream can tell
the difference.

### How it is closed

By reading the signal the fail-safe already emits, rather than by changing the engine. An
autouse fixture in `tests/commerce_discovery/conftest.py` watches `engine`'s logger for
`COMMERCE_DISCOVERY_SERVE_FAILED` for the duration of each test, and a
`pytest_runtest_makereport` wrapper flips a report that *otherwise passed* into a failure
carrying the exception and its traceback.

What I deliberately did **not** do is add a strict mode that re-raises under test. It is
the obvious fix and it is the wrong one: it would make the code path the tests exercise
different from the one production runs, which is the entire subject of §18.6 — a test
double kinder than production is how two columns reached buyers' phones for as long as they
did. The guard observes; it does not alter.

Three conditions keep it from becoming noise, each of which is its own test:

- only the `call` phase, so setup and teardown keep their own stories;
- only a report that otherwise **passed** — a genuinely broken run should not report every
  failure twice, with the second copy saying less than the first;
- an opt-out marker, `commerce_serve_may_fail`, for the handful of tests whose subject *is*
  the fail-safe.

The log prefix is pinned to the engine's real message by driving an actual crash through
`serve`, so renaming the log line cannot silently disarm 48 tests' worth of rigour. The
decision is a plain function so it can be tested as one, and the three lines of hook glue
around it are pinned from source.

The guard fired on its own author the first time the file ran: the test that breaks `serve`
in order to have a real record to report on came back red, correctly, and now carries the
marker. That is the least ceremonious available demonstration that it works.

### What this does not fix

It is scoped to `tests/commerce_discovery/`. The route-level tests elsewhere in the tree
call `serve` through Flask and are not covered; neither is any other fail-soft path in this
package, and there are several — `_reconciled_value`, `_interest_profile`, the follow-graph
read, `metrics.observe`. §11a already argues that some fail-soft paths *should* survive
mutation. The distinction is whether a test can tell that it took one, and for `serve` the
answer was no for the entire life of this report.

Verified: 680 package tests and 663 protection tests green; the 158/522 mutation above,
reverted.

---

## 20. The previous section found one unwatched fail-soft path. There were 25.

§19 ends by naming four more fail-soft paths no test walks, and saying the distinction that
matters is "whether a test can tell that it took one". That was a list assembled by reading.
Assembling a list by reading is how you find the handlers you already suspected.

So the handlers were counted instead.
`scripts/protection/audit_commerce_discovery_failsoft.py` inventories every `except` clause
in `services/commerce_discovery/` by AST, then runs the package's own suite under
`sys.settrace` and records which handler *bodies* execute. Three states, one of which is
fine:

| state | meaning |
| --- | --- |
| `exercised` | a test reaches it, and it logs or re-raises. Its failure behaviour is tested behaviour. |
| `never` | no test in the package reaches it. It is a claim about what happens when something breaks, and nothing has checked the claim. |
| `silent` | reached, but neither logs nor re-raises. Production cannot tell it fired; nor can a test, except by noticing a missing value. |

Measured before any of this section's tests existed:

```
56 fail-soft handlers in services/commerce_discovery/
25 NEVER REACHED by any test in the package
 9 reached, but neither logged nor re-raised
22 reached, and says so
```

`exposure.py` was 5 of 6 never reached. `engine.py` 6 of 11. `ranking.py` 4 of 5.

It needs no new dependency. That is deliberate and it is the §14 constraint, not laziness:
`coverage` is not a dependency of this repo, and a measurement that requires one more
install than the suite already needs is a measurement nobody re-runs. The global trace
function returns `None` for every frame outside the package, so line tracing is only paid
for where it is read.

**It exits 0 whatever it finds.** A `never` handler is a question — "can this actually
happen?" — and §11a is the reason some of the answers are legitimately "no". A gate here
would force tests for unreachable branches, which buys a green tick and no safety.

### The one that was my own sentence

`engine.py:924`, inside `_persist`, is the handler that bounds
`relationship.assert_servable`. The previous increment put a comment beside it saying the
blast radius is one dropped placement. The audit says that handler had never executed.

An untested claim about a blast radius is a confident sentence. Nothing produces an
unservable relationship today — `classify` only ever returns one of four — which is exactly
why the guard sits on the write path and why its radius has to be *demonstrated* rather than
reasoned about. The next writer of that column is a feature that does not exist yet.

### What was closed, and what was left

`tests/commerce_discovery/test_the_fail_soft_paths_are_walked.py` does not chase all 25.
It takes the handlers where the **direction** of the failure is a promise somebody relies on:

* **`_persist`** — one unwritable row now provably costs one card and not the response, and
  says so in the log. A separate test asserts every card that *did* come back carries an
  impression token, which is the reason dropping is correct: a tokenless card can never
  record an impression, so the frequency cap never learns it was shown and it is eligible
  again on the next request, forever. The `assert_servable` radius is demonstrated
  end-to-end by making it refuse exactly one row.
* **`_session_cap_reached`** — annotated `return True  # fails closed`. The comment is now
  the test. A frequency limit that fails *open* shows more commerce to precisely the viewer
  who has had their allowance, and nothing anywhere would say so. End-to-end, the surface
  goes quiet rather than uncapped — and note what §19's guard deliberately does *not* do
  here: no `COMMERCE_DISCOVERY_SERVE_FAILED` fires, because this `[]` is a decision the
  engine made on purpose after a failed read. The guard is narrow enough to tell those
  apart, which is the property that makes it usable.
* **`_payload`** — if the real serializer throws, the fallback must still be the allowlist.
  §18.6 is about what reaches a buyer's device, and that guarantee has to survive the
  serializer failing, not only the serializer working. Parametrized over every member of
  `PIPELINE_ONLY_FIELDS`.
* **`exposure`'s purchase / cart / save reads** — each one removes a reason a product would
  be filtered or boosted. Each is now exercised by dropping the table out from under it and
  asserting the shop stays open.

After:

```
56 fail-soft handlers
18 NEVER REACHED   (was 25)
 9 reached, but neither logged nor re-raised   (unchanged)
29 reached, and says so   (was 22)
```

The nine `silent` handlers are unchanged **on purpose**. Every one is an
`_int`/`_json_list`-shaped coercion whose failure is meant to be indistinguishable from an
absent value; making them log would put a line in production for every malformed row, which
is how you train people to ignore logs.

The audit script is itself under test — §14 says evidence nobody can regenerate is a defect,
and a silently-zero inventory would report a clean bill of health. Three tests assert the
inventory still finds the package, still finds the two handlers this section is about by
name, and can still distinguish a logging handler from a silent one, because if that
distinction collapsed the `silent` category would become unreachable and the audit could not
report it. Only the inventory half is called from a test; the other half runs the whole
suite, which a test must not do.

### Still open, and the one worth acting on

`ranking.py` remains 4 of 5 unreached and those four are coercion guards of the same shape
as the `silent` nine. `engine.py:533` — the first of `_listing_stats`' two handlers — is
still unreached, and that one is not a coercion:

> **Finding (not changed).** Both of `_listing_stats`' handlers log at `LOGGER.debug`. A
> stats outage is therefore invisible in production, and its effect is not small: with no
> stats, *every* listing looks unproven, which changes the ranking of every card on every
> surface simultaneously. This is a one-word change to `LOGGER.warning`, but it is a change
> to production logging volume on a path that reads per-listing, so it belongs in the same
> conversation as §16's rollout rather than smuggled in beside a test file.

§12's entry on `engine.serve`'s broad `except` should now be read against §19 and this
section: the *test-visibility* half is closed, for `serve` specifically and for four more
handlers here. The availability half — that a caller still cannot distinguish a crash from a
decision — is unchanged, and §82 is the reason it stays that way.

Verified: 697 package tests and 663 protection tests green, plus the audit re-run above.

---

## 21. There were two fail-safes. The tests about bereavement were green against the broken one.

§19 closed the engine's fail-safe and ended with a caveat: the guard is scoped to
`tests/commerce_discovery/`, and "the route-level tests elsewhere in the tree call `serve`
through Flask and are not covered".

That caveat was wrong in both directions, and the second one is the section.

**Wrong the harmless way:** there are no serve-route tests elsewhere in the tree. Only one
file posts to `/api/pulse/commerce/discovery/<surface>`, and it is inside
`tests/commerce_discovery/`, so it was already in scope.

**Wrong the way that matters:** being in scope was not the same as being covered.
`commerce_discovery_routes.commerce_discovery_serve` has **its own** `except Exception`
wrapping the engine's, and the guard could not see it for two independent reasons, each of
which looks like it ought to work:

1. It logs `COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED`. That does **not** start with
   `COMMERCE_DISCOVERY_SERVE_FAILED` — the words diverge immediately after `SERVE_`.
2. `logging` propagates records to *ancestors*. `services.commerce_discovery_routes` is a
   sibling of `services.commerce_discovery.engine`, not a descendant, so a handler on the
   engine's logger never sees it however the message is spelled.

And the outer fail-safe is the more dangerous of the two, because of what it returns:

```python
def _empty():
    """The one shape every serve failure returns."""
    return _json({"ok": True, "placements": []})
```

**HTTP 200, `ok: True`.** A crashed route is indistinguishable from "no products for you" —
not only to a test, but to the mobile client and to anything counting empty responses. Three
unrelated conditions collapse into that one value: an unknown surface name, a rate-limited
client, and a total failure of the handler.

### The measurement

`raise TypeError` on the route handler's first line, so the route could not answer anything:

| | failed | passed |
| --- | --- | --- |
| guard watching the engine's logger only | 48 | 649 |
| guard watching both | **77** | 620 |

**29 tests changed sides, and they are the suitability tests.** The ones that certify
PulseSoc does not put a shopping card beside a bereavement post:
`test_a_sensitive_context_is_refused_before_retrieval`,
`test_grief_expressed_through_an_object_is_refused_too`,
`test_a_bereavement_in_the_caption_refuses_before_retrieval`,
`test_an_uncleared_reel_refuses_over_a_cleared_post`, and
`test_post_detail_is_gated_on_its_own_body`.

These are the tests behind the mission's central ethical promise, and every one of them was
green against a route that could not run.

### Why this is not a story about careless tests

It would be comfortable to call those tests lazy. They are not. Look at what they assert:

```python
response = ask(client, surface, GRIEF)

assert response.status_code == 200
assert response.get_json() == {"ok": True, "placements": []}
assert not serve.called, (
    "the candidate pool was built for a post commerce must stay away "
    "from; a refusal that runs after retrieval has already written the "
    "exposure ledger and minted an impression token"
)
```

`assert not serve.called` is a *second* assertion, written deliberately to catch a refusal
that happens too late — a real and specific failure mode, correctly anticipated, with the
reasoning spelled out. And a crash before retrieval satisfies it **more** thoroughly than a
genuine refusal does.

That is the transferable lesson, and it is the same one as §19 and §18.6 in a third costume:
**a stronger assertion in the same direction is still the same direction.** No amount of care
inside a test substitutes for something watching from outside it. The eight tests that
survived the mutation legitimately never touch the route — they call
`suitability.assess_adjacency` directly — which is §11a's point restated: the goal was never
zero survivors.

### The fix

One list, in `conftest`:

```python
WATCHED_FAILSAFES = (
    ("services.commerce_discovery.engine", SERVE_FAILED_PREFIX),
    ("services.commerce_discovery_routes", ROUTE_FAILED_PREFIX),
)
```

The recorder attaches to every logger named there and matches *all* the prefixes on each, so
moving a fail-safe between modules cannot disarm it. Loggers are attached by **name**, never
by importing the module: `commerce_discovery_routes` imports `bot` — 111k lines — and a
conftest that imported it at collection time would make every test in the package pay for
that to answer a question about a log record. Records are identity-deduped, because one
handler instance on several loggers would otherwise list a single failure twice and read like
two bugs. The failure text now names the module that swallowed the exception, because
"`engine.serve` failed" sent me to read the wrong file the first time the route fail-safe
fired.

Still **zero production change**, for §19's reason: a strict mode that re-raises under test
makes the tested path differ from the shipped one, which is what §18.6 was about.

Eight new tests pin it, including the two traps above stated as assertions
(`test_the_route_prefix_is_not_caught_by_the_engine_prefix`) so that anyone "simplifying" the
two prefixes into one `startswith` gets a red suite instead of a silently narrower guard, and
one asserting that `_empty()` still answers `ok: True` — not as a complaint, but so that if
anyone ever does make the route answer 5xx on a crash, the test fails and points them at the
guard they can then delete.

### What this does not fix

The availability half is still untouched, and deliberately: §82 requires the post to render
when commerce does not. What has changed twice now is that a *test* can no longer be fooled
by it. Both times, the gap was found by mutation rather than by reading — §19's four
hand-listed paths did not include either of the two that mattered here. That is worth saying
plainly in a report that recommends mutation harnesses: I wrote the list by reading, and the
list was wrong.

Verified: 705 package tests and 663 protection tests green; the 77/620 mutation above,
reverted and confirmed byte-identical to `HEAD`.

---

## 22. The audit had the same blind spot the suite did

§20 built an audit of fail-soft handlers and scoped it to `services/commerce_discovery/`.
§21 then found the most expensive unwatched fail-soft path in the feature sitting in
`services/commerce_discovery_routes.py` — one directory up, because it imports `bot` and the
package deliberately does not.

So the audit inherited the boundary that caused the problem it was built to find. Widened:

```
74 fail-soft handlers across 19 files
31 NEVER REACHED by any test in the package
12 reached, but neither logged nor re-raised
31 reached, and says so

per module (never / total)
      commerce_discovery_routes.py  13 /  18      <-- worst in the feature
      ranking.py                     4 /   5
      events.py                      4 /   9
      ...
```

The request layer was **13 of 18 unreached** — proportionally worse than any module inside
the package, and it is the layer that actually forms the response a buyer's device receives.
An audit of "what happens when commerce breaks" that stops before that layer is answering a
narrower question than its name suggests. The scope is now a named list with a comment
explaining why the extra file is not discovered by pattern: a
`startswith("services/commerce_discovery")` test would match the routes module *and* any
future `commerce_discovery_*.py` nobody had told the audit about, silently — which is how the
two got conflated in the first place.

### Three of the thirteen were load-bearing

`tests/commerce_discovery/test_the_request_layer_fails_soft_correctly.py` (19 tests) takes
those three and leaves the other ten, for §20's reasons about coercion guards.

**1. `_with_db` — a written prediction of an outage in unrelated features.** Its docstring:

> `close()` is in a `finally`, not on the success path. A close reachable only when nothing
> raised leaks one pooled connection per failure, and this pool is 8+8 with a 3s timeout — a
> few dozen failures is an outage on every other feature sharing it.

All four of its handlers were unreached. This is §20's `_persist` finding again — a confident
sentence about a blast radius that nothing had checked — and the lesson had not generalised
far enough: I audited the package's confident sentences and not the route's.

Now tested: commits and closes on success; **rolls back and closes on failure, in that
order** (the reverse rolls back a closed connection, which raises on some drivers and
silently does nothing on others); re-raises rather than returning a sentinel, which is the
line that makes the route's own fail-safe reachable at all; a `rollback()` that itself fails
does not skip the close or mask the original exception; and a `close()` that fails on the
*success* path does not throw away placements that were already computed and committed —
turning a pool problem into a blank shelf.

Proved by mutation: moving `close()` from `finally` to `else` turns **4 tests red**. Worth
recording *why* `else` is not a near-miss but a total failure — the success path `return`s
from inside the `try`, so an `else` clause never runs at all. The leak would have been
complete, not occasional.

**2. `commerce_discovery_serve`'s own fail-safe had never run — including when §21 shipped a
guard keyed to its log line.** §21 extended `conftest` to watch for
`COMMERCE_DISCOVERY_SERVE_ROUTE_FAILED`, and the audit says the handler emitting that string
had never executed in a test. A guard matching an unexercised log line fails open on a typo:
misspell the message and 29 suitability tests go quietly back to being vacuous, with the
guard still in place and still green.

That loop is now closed by making a real request crash and asserting the real record — the
prefix, that it carries a traceback (`LOGGER.exception`, not `LOGGER.error`, or an operator
gets a prefix and no stack), and that it lands in the recorder the report hook reads. Every
other test of the guard builds a `LogRecord` by hand; this one does not. The tests carry
`@pytest.mark.commerce_serve_may_fail`, because they deliberately cause the line the guard
exists to catch — the same self-reference §19's file hit.

**3. `_event_route`'s error vocabulary, including a security property written as a comment.**
Its docstring says three copies of this logic "would be three places to forget that a
`DiscoveryEventError` is a 400 and everything else is a 500 that must not leak its message."
Both handlers were unreached, so neither half was checked.

Now tested: a known event error returns 400 carrying its own code in **both** `error_code`
and `error` (the older web handlers read the second; setting only the first collapses every
failure to a generic message — a trap this repo has hit before), and an unexpected failure
returns a 500 that does not contain the exception text. Proved by mutation: making the 500
interpolate `str(exc)` turns the security test red, with a planted message naming a column
and a table.

It is also asserted that the unexpected failure is still **logged**. Not returning detail to
the client is only correct if somebody can see it; a 500 that is silent on both sides is
unfixable. And one test asserts `COMMERCE_DISCOVERY_EVENT_FAILED` does **not** trip §21's
guard — it is on a watched logger but is not a watched prefix, and it should not be: a failed
event write is a 500 the client can see and retry, not a silently empty shelf. A guard that
fires on already-visible errors is a guard that gets deleted.

### Also pinned while here

The route normalises `surface` once and reuses it. Its own comment records the bug: the check
used to lowercase a copy and leave the original in play, so `/REELS` cleared the gate and was
then served feed cadence and reported its events under a surface name nothing else writes.
That is now a test — and a second one asserts the converse, that no surface is *declared* in
a form the normaliser would reject, since such a surface could never be requested at all.

Two tests assert that the unknown-surface and rate-limited paths return the empty shape
**without** logging a fail-safe line. Those are decisions, not failures, and if they logged
one the guard would flip every test that asks about a suppressed surface — the noise problem
§19 spent three conditions avoiding.

### Still open

`ranking.py` remains 4 of 5 unreached, all coercion guards. The twelve `silent` handlers now
include three in the route module (`_anchor_listing_id`, `_anchor_context`,
`_content_post_id`), all `int()`-shaped, all left alone on the same reasoning. And §20's
`_listing_stats` debug-logging finding is unchanged and still the one item here worth acting
on in production — it is now written up as a decision with three options in §16, under
*What §§19–22 add to this order*, because leaving it as a finding in a section about test
coverage was a way of never having to make the call.

Verified: 724 package tests and 663 protection tests green; both mutations above reverted,
`services/commerce_discovery_routes.py` confirmed byte-identical to `HEAD`.

## 23. The rollout was cautious because there was no way to stop it

Every section above this one is an audit finding. This one is a feature, and it is here
because the audit is what identified it: item 58 of the delivery report had to say that
turning commerce discovery off on a single surface was a code change, a review and a Railway
deploy — and Railway variables only reach a container at boot, so even the deploy is slower
than it sounds.

That was not a footnote in the rollout plan, it *was* the rollout plan. §16's staged order is
as conservative as it is because each stage changes what feed, reels, post-detail, Messenger,
Marketplace and product-page users see at once, and the only available undo was shipping
again. Every stage had to be sized against "what are we willing to un-ship by deploying"
rather than "what do we want to learn". A switch that is coarser than the thing likely to go
wrong gets used late, or not at all.

`COMMERCE_DISCOVERY_DISABLED_SURFACES` now takes a comma- or space-separated list of surface
names, validated against `schema.SURFACES`, read per request. A named surface returns an empty
placement list — the same outcome as the master switch, scoped — which every client already
renders as no commerce unit, so nothing on the device needs to change for this to work.

### The switch is free, and the master switch became free with it

The check sits at the top of `_serve`, above `promotion.assert_unpaid`, above
`schema.ensure_schema(conn)` and above `preferences.viewer_policy(cur, user_id)`. A disabled
surface costs one environment read and **zero queries**.

That placement exposed something about the switch that already existed. `config.enabled()`
had exactly one call site — `preferences.py:154`, inside `viewer_policy` — which runs *after*
the schema guard and takes a cursor. So with discovery globally off, every single request
still paid for `ensure_schema` and a cursor in order to be told no. Checking both switches in
one place fixes that as a side effect, and `test_the_master_switch_is_free_too_now` exists so
the side effect cannot be quietly lost; mutating `surface_enabled` to consult only the
per-surface list turns it and two others red.

The check is deliberately **silent**. An operator-requested empty list is not an incident, and
a log line on `feed` would put one on the hottest read path in the product to report that
something is working as configured.

### A typo leaves the surface serving. That is chosen, and it is the uncomfortable choice

`disabled_surfaces` drops names it does not recognise. So `COMMERCE_DISCOVERY_DISABLED_SURFACES=post-detail`
disables nothing and post-detail keeps serving — a kill switch failing open, which is the bad
direction for a kill switch.

It is still the right one, because the alternative is worse in a way that is not symmetric:
treating an unknown token as "disable everything" turns one typo in a Railway variable into a
platform-wide commerce outage. There is no third option, because nothing in the process can
guess which of six surfaces `reel` or `post-detail` was meant to be. So the mitigation is
loudness rather than cleverness — one `COMMERCE_DISCOVERY_UNKNOWN_DISABLED_SURFACE` warning
naming the ignored token, the valid set, and `still_serving=1`, emitted once per distinct raw
value rather than once per request, since this is read on the feed path. Keying the memo by
the raw string rather than by the bad token means *fixing* the variable is observable too.

`TestATypoFailsOpenLoudly` is where that choice is written down, specifically so it cannot be
reversed by someone who reads the drop as a bug. Mutating the parser to fail closed turns two
of its tests red.

### The test that proved the placement did not prove the placement

Thirty-nine tests, and the mutation that matters most is moving the check below
`ensure_schema`. Run against the first version of the file, that mutation turned **one** test
red — and not either of the two written to pin the placement.

The reason is the subject of §§19–22 arriving one layer lower than expected. The forbidden
cursor and connection raised `AssertionError` on contact, as designed, but `schema.ensure_schema`
has its own `except Exception` at `schema.py:351`: it caught the assertion, logged
`COMMERCE_DISCOVERY_SCHEMA_FAILED`, and returned `False`. `_serve` then returned `[]` — which
is precisely what the test was asserting a correctly-disabled surface returns. The conftest
guard did not save it either, because that guard watches `COMMERCE_DISCOVERY_SERVE_FAILED` and
the exception never travelled as far as `serve`'s fail-safe.

This is the general hazard of auditing a feature built to three levels of fail-soft (§82): in
this package, *an exception and a success produce the same observable value*, so any test that
asserts only the return value is at risk of passing for the wrong reason. §19 found 48 tests
in that state, §21 found 29 more, and then this section's own tests joined them — which is
worth recording plainly, because the lesson evidently does not transfer by having been
written down once.

Fixed by recording contact in a module-level `TOUCHES` list that no intervening `except` can
reach, and asserting on the record instead of relying on the raise. The assertion lives in the
`serve_with_nothing` helper, so all five callers pin the placement rather than the two that
mention it. The mutation now turns 5 tests red.

### Still open

The switch does not suppress the feed's `commerce_suitable` annotation, and should not: the
annotation is a property of the post, costs no query, and a client with no placements has
nothing to do with it either way. That is stated in the test file as a deliberate non-property
so the next reader does not file it.

There is still no per-*viewer* or percentage rollout — this is on/off per surface, which is
what a rollout needs to be reversible, not what an experiment needs to be measurable. Item 45
of the delivery report (no experimentation framework wired to commerce) is unchanged, and
`PulseExperiments` remains shipped-but-inert.

Verified: 763 package tests and 663 protection tests green. Three mutations, each caught by
the tests that name it: guard below `ensure_schema` (5 red), `surface_enabled` ignoring the
master switch (3 red), unknown token failing closed (2 red); all three reverted and both
source files confirmed restored. The fail-soft handler audit reports the same 74 handlers and
25 unreached as before — the switch adds no `except` (§82's handlers are untouched).

---

## 24. The one relationship in the system that is a statement rather than a guess

Like §23, this is a feature rather than an audit finding, and like §23 the audit is what
identified it. §15 ("what the brief asked for that I did not build") and item 7 of the
delivery report both said the same thing: `relationship.CREATOR_TAGGED` had been a documented
constant sitting in `UNIMPLEMENTED_RELATIONSHIPS` the whole time, because **no post↔listing
relation existed anywhere in the repository, under any name.** Greps for `content_product`,
`post_product`, `product_tag`, `tagged_product`, `post_listing`, `attached_product` and five
other spellings were all empty; `pulse_posts` has no listing reference.

### Why this, ahead of the other unbuilt items

Not because it was the biggest. Because of what it *is*.

Every other retrieval source in `pool` is an **inference** — this viewer likes cameras, this
post mentions a tripod, the crowd is looking at lenses. Ranking exists to order guesses. A
creator tag is the only edge in the system that is a **statement**: the person who made the
post says this is the product in it. That makes it simultaneously the most useful signal
available and the one with the least excuse for being wrong, and it is why `tagging.py` is
mostly refusals — 5 of them, enumerated in a `REFUSALS` frozenset so a test can assert
`attach` never returns a reason outside it rather than restating the list.

### What was built

`services/commerce_discovery/tagging.py` (write + read), `pulse_content_products` in
`bot.init_db`, `bot.pulse_attach_products_to_content` (the composer path, wired into post
create and reel create), the `preferred` precedence tier in `engine._select`, and
`CREATOR_TAGGED` leaving `UNIMPLEMENTED_RELATIONSHIPS`.

Shaped after `pulse_content_music` — same polymorphic `(content_type, content_id)` key,
written by the composer, resolved by a reader — because it already solved deletion and
moderation for a post-attached entity. Two departures:

* **`seller_user_id` is stored** and re-checked against the live listing on every read. It is
  the seller the tag was *authorised against*. A listing that changes hands afterwards
  carries a permission its new owner never granted, and the read drops it. The check is in
  the JOIN (`l.seller_user_id = p.seller_user_id`) rather than in Python, so a transferred
  listing costs nothing to exclude.
* **Nothing else is snapshotted.** Music snapshots a licence because a stale song is still
  the song. Price, title and availability are read live on every serve, because a stale price
  is not a stale copy of the truth — it is a lie to a buyer.

### The decision worth arguing with: a precedence tier, not a ranking weight

The obvious implementation is a `tagged` weight in `ranking.score_listing`. That was wrong,
and the reason is arithmetic rather than taste: `score_listing` normalises `score` by the sum
of positive weights, so adding one **shifts every relevance threshold in the system** — six
surfaces' floors, the fatigue escalation bands, the `diversity_bonus` pinned at 0.07. A
feature that is supposed to add one retrieval source would have silently re-tuned the entire
engine, and the tests that pin those thresholds would have been updated to match, which is
how that kind of change becomes invisible.

So tags are taken **first in `_select`**, ahead of the floor and ahead of the caps. Each
exemption is argued separately in the docstring, because they are separate claims:

* *Ahead of the score* — a tag is a statement about what the post is of; everything else in
  the list is a guess about what the viewer wants.
* *Ahead of the relevance floor* — the floor asks "is this a good answer for this person",
  and relevance to the content is precisely what the tag establishes by fiat. A tagged
  product below the floor is usually a **brand-new listing the scorer has no signal for**,
  which is exactly the product a creator is most likely to be posting about.
* *Ahead of the per-seller diversity cap* — the cap exists so one store cannot dominate a
  session. On a post whose creator tagged three of their own products that reasoning does not
  apply: every tagged row is one seller **by construction**. Applying the cap would silently
  truncate every creator's tags to two and look like a bug in the composer.

What is **not** exempt: `eligibility`, `promotion.assert_unpaid`, the surface budget,
`MAX_TAGGED_PER_CONTENT`, and the forward-feeding counts. So a two-slot surface filled by tags
serves no inferred rows at all — the creator's statement displaces the guesses rather than
being appended to them.

### The refusal that is a product decision, not an engineering one

**A creator may only tag a listing they own.** Tagging someone else's product is affiliate
marketing. It needs a commission model, a disclosure obligation that differs by jurisdiction,
and a decision about whether PulseSoc takes a cut. None of those are mine, and all of them are
much harder to withdraw than to delay. So the check is ownership, the refusal is explicit
(`REFUSED_NOT_OWNER`), and `AUTHORITY_OWNER` is recorded on **every** row so that the day a
second authority exists, rows written under this one are still distinguishable. A test asserts
nothing writes any other value.

### Two defects found by writing the tests

1. **`engine.py` did not run.** It arrived referencing `tagging` without importing it and
   `content_post_id` without accepting it as a parameter — a `NameError` on *any* serve, on
   every surface. Not a subtle break; the package's 763 tests were green because the fixture
   called `engine.serve` with the arguments it had before.
2. **`_int(2.5)` returned 2.** A lossy coercion, caught by a parametrized test rather than by
   review. It matters more than it looks: `content_id` spaces overlap across
   `pulse_posts`/`pulse_reels`/`pulse_status`, so a tag filed against a truncated id points at
   a stranger's content, is not even obviously orphaned, and shows no symptom to anybody. Now
   refused. `2.0` is still accepted, because JSON has no integer type and a client sending
   `4242.0` meant 4242 — the test is integrality, not type.

There was also a third thing that looked like a defect and was not: `placement["relationship"]`
read `None`. That is deliberate. `PIPELINE_ONLY_FIELDS` strips it because `_payload`'s
serializer is a **denylist**, and §18 is the section about what a denylist ships by accident. A
test asserting `placement["relationship"] == CREATOR_TAGGED` would have been **asserting the
leak exists**, so the assertion reads the persisted column instead and a new test
(`test_the_provenance_does_not_reach_the_buyers_device`) pins the absence.

### The mutation matrix, and the two mutations that survived it

12 controls, each deleted from real source with the named suites run against it. The harness
is committed — `scripts/protection/creator_tagging_mutation_matrix.py` — per §14: evidence
nobody can regenerate is a defect, and the first version of this run was ad-hoc bash that
existed only in a transcript.

| # | control deleted | killed by |
|---|---|---|
| 1 | stale-seller JOIN condition | `test_the_tag_is_dropped_once_the_listing_changes_hands` |
| 2 | the reader's own `LIMIT` | `test_the_reader_stops_at_the_cap_even_when_the_writer_did_not` |
| 3 | ownership check on write | `test_a_stranger_may_not`, `test_the_viewer_may_not_tag_a_sellers_product` |
| 4 | write-side cap | `test_the_writer_stops_at_the_cap` |
| 5 | lossy-id refusal | 3 red |
| 6 | `AUTHORITY_OWNER` on the row | 3 red, incl. `test_nothing_writes_an_authority_other_than_owner` |
| 7 | tagged outranking contextual in `classify` | 3 red |
| 8 | `_select`'s preference for tags | 3 red |
| 9 | tagged lookup moved inside the personalisation gate | `test_an_opted_out_viewer_still_sees_the_creators_product` |
| 10 | route resolving the post id on every surface | **survived — see below** |
| 11 | suitability refusal never called | **survived — see below** |
| 12 | an absent post permitted instead of refused | `test_a_deleted_post_serves_no_tags_and_the_rows_remain` |

Ten of twelve is not the finding. (Entry 12 was written after the first eleven, when the
retention question below turned out to have a real answer; it was killed first time.) The finding is that **the two survivors guarded the two
most safety-relevant properties in the increment, and both were defects in tests written the
same hour by someone who had just finished writing §19, §21 and §22 about exactly this.**

**#11 — the suitability gate stops refusing anything.** Mutation:
`verdict = None if engine.serve else _content_refusal(...)`. Commerce is then served onto
bereavement, medical and distress posts. Both guards in the new file survived it:

* `test_the_lookup_is_below_the_gate_in_the_route` compared **line numbers**, and the mutation
  keeps `_content_refusal` textually above the `engine.serve` call.
* `test_the_refusal_returns_before_retrieval` **never invoked the route at all.** It called
  `suitability.assess(...)` directly and then asserted `not served.called` — vacuously true,
  because nothing had called the handler. It was a test that could not fail.

The mitigating fact, established by re-running the mutation against the whole package rather
than one file: **41 tests in `test_suitability_gate_is_wired.py` do kill it.** That file was
written for precisely this reason and it works. So #11 was a gap in one file, not in the
suite — which is itself worth recording, because running a mutation against only the suite
you just wrote is how a covered property gets reported as uncovered, and it is the mirror
image of the §22 mistake.

**#10 — every surface gets the client's post id.** Mutation: one line, moving
`content_post_id = _content_post_id(payload)` above the `if surface == "product_detail"`
branch. Effect: a creator's product can be served into a **Messenger conversation** or a
Marketplace shelf, labelled as tagged on a post that is not on screen and that the viewer may
not be able to see. This one **survived the entire 826-test package.** It was a real hole.

It survived because `test_the_route_only_supplies_a_post_id_where_a_post_is_on_screen` was a
regex asserting the assignment *inside* the `CONTENT_SURFACES` branch still existed. It does
still exist. An *additional* unconditional assignment above it leaves the regex satisfied.
**A structural test that asserts the presence of the right code cannot detect the addition of
wrong code** — and "assert the guard is still there" is the natural way to write that test,
which is why this is worth a paragraph rather than a line.

Fixed three ways, because the single behavioural test is not sufficient on its own:

* `test_a_surface_with_no_post_on_screen_ignores_a_post_id`, parametrized over
  `frozenset(schema.SURFACES) - CONTENT_SURFACES` (derived, never listed — a hand-written
  list goes stale in the safe-looking direction, by silently not testing a new surface), which
  drives the **real Flask route** and asserts on the `content_post_id` the engine was actually
  handed. A mutant cannot satisfy that by addition.
* `test_every_surface_that_does_have_a_post_uses_it`, the other half, so the first cannot be
  satisfied by hard-wiring 0. Each direction is trivially passable alone; the pair is the rule.
* `test_the_post_id_is_resolved_in_exactly_one_place`, an AST walk **counting** call sites
  inside `handler` and checking the nearest enclosing `If` tests `CONTENT_SURFACES`. This
  covers the seventh surface that does not exist yet, which the parametrized test cannot.

The AST walker had its own bug on the first attempt, worth naming because it is a general
trap: **`elif` is an `If` node inside the outer `If`'s `orelse`**, so a walker that only
inspects its children's tests and never a node's own reports every call site as unguarded. It
failed loudly rather than passing vacuously, which is the only reason it was caught.

`test_the_refusal_returns_before_retrieval` was also rewritten to drive the route, and a
second test — `test_a_tag_cannot_be_resolved_on_a_refused_post` — now watches
`tagging.tagged_listing_ids` directly, so a future route that resolves tags itself "to avoid a
wasted engine call on untagged posts" fails even though `serve` was still not called.

### What is not built

**The composer UI.** No client screen sends `product_listing_ids`, and the multipart/form post
path does not carry them at all — only the JSON path does. The feature is reachable by an API
client and by nothing a user can tap. Delivery report item 30 states this; it is repeated here
because a section this long about a feature reads as "shipped" unless told otherwise.

### Verified

827 package tests and 663 protection tests green (1,490 in one run). All 12 mutations killed,
zero survivors, every source file confirmed restored byte-identical afterwards. The fail-soft
handler audit is unchanged: `tagging.py` adds one `except` — the reader's — and it is the
logged kind, not the silent kind, because this is the one source whose absence downgrades an
explicit creator statement to a guess and the symptom (a tagged post showing unrelated
products) looks exactly like a ranking complaint.
