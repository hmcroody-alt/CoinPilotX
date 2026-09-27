# PulseDrop — autonomous commerce curator

Mission report. PulseDrop is the system that lets PulseSoc's own account publish
marketplace products to the feed on a schedule, without a human choosing which
product or writing the copy.

The design constraint that shaped everything else: **an empty feed is a better
outcome than a dishonest post.** Most of the code below exists to make "publish
nothing" a first-class, well-instrumented result rather than a failure.

---

## 1. What it does

Every two hours, PulseDrop wakes, reads the marketplace, and decides whether any
listing deserves to be shown to members right now. If one does, it publishes it
as a Signal (an image post) and/or a Reel (a 9:16 video), captioned and labelled,
from the verified `@pulsedrop` account, linking to the real product.

If none does — and most of the time none does — it records why and goes back to
sleep.

## 2. Where it lives

```
services/pulsedrop/         16 modules, ~6,400 lines
  __init__.py               public surface
  schema.py                 6 tables, 8 indexes, idempotent
  config.py                 26 settings, DB-backed with env fallback
  account.py                the @pulsedrop identity
  eligibility.py            the buyer gate
  ranking.py                the linear scorer
  editorial.py              labels and copy
  diversity.py              cooldowns and caps
  distribution.py           which format, or none
  reel_composer.py          the renderer
  publisher.py              the two publish paths
  hydration.py              the read path
  lease.py                  the distributed lock
  curator.py                the tick
  audio.py                  music beds
  ops.py                    the admin surface
tests/pulsedrop/            8 files, 255 tests
```

Nothing was added to `bot.py` except one admin page. The subsystem is importable,
testable and deletable on its own.

## 3. The tick

`curator.worker_cycle()` is called by `media_worker.py` every few seconds. Almost
every call returns immediately.

1. `config.enabled()` — off? return `DISABLED`.
2. `reel_composer.run_pending(limit=1)` — drain at most one render. This happens
   **before** the lease check, so encodes make progress even on cycles where the
   curator is not due.
3. `lease.acquire()` — not due, or someone else holds it? return `NOT_DUE`.
4. `publisher.reap_stale_claims()` — close rows left `claimed` by a crashed tick.
5. `account.ensure_account()` — resolve `@pulsedrop`.
6. `eligibility.fetch_candidates()` + `partition()` — gate.
7. `diversity.History.load()` — recent publications.
8. `ranking.rank()` — score and sort.
9. `_choose()` — walk the ranking, ask `distribution.decide()` about each, stop at
   the first publishable one.
10. `_publish()` — render/enqueue, post, settle.
11. `_record()` — write the run row.
12. `lease.release(next_run_seconds=...)`.

Outcomes: `DISABLED`, `NOT_DUE`, `PUBLISHED`, `NO_CANDIDATES`, `NONE_ELIGIBLE`,
`DEFERRED`, `SKIPPED`, `RENDER_PENDING`, `FAILED`, `ERROR`.

Seven of those ten mean "correctly published nothing", and they are distinguished
from each other on purpose. "Nothing was eligible" and "the lock was held" and
"we're inside the pacing floor" are different incidents and must not look alike
on the dashboard.

## 4. Distributed safety

Two Railway services deploy from `main` and both import this code. The curator
must run once per interval across the fleet, not once per container.

`pulsedrop_leases` is a single row keyed `curator`, claimed by a conditional
`UPDATE`:

```sql
UPDATE pulsedrop_leases
   SET owner=?, acquired_at=?, expires_at=?, next_run_at=?, updated_at=?
 WHERE lease_key=?
   AND COALESCE(next_run_at,'') <= ?
   AND COALESCE(expires_at,'') <= ?
```

The database decides the winner; `cursor.rowcount` tells the caller whether it
won. There is no read-then-write race because there is no read.

Two properties worth naming:

- **The claim commits on its own connection, immediately.** A tick that takes 40
  seconds does not hold an open transaction over the lock for 40 seconds.
- **`next_run_at` is pushed forward at acquire time, not at release time.** If the
  tick crashes, the interval has already been consumed, so a crash-looping
  container cannot publish on every restart. The lease expiry (`lease_seconds`,
  5 min) is the backstop that returns the lock; the interval is the backstop that
  stops it being useful immediately.

## 5. Idempotency

`pulsedrop_publications.idempotency_key` is `UNIQUE` and constructed as:

```python
f"{surface}:{listing_id}:{now:%Y-%m-%d}"
```

The day bucket is the deliberate part. Within a day, a duplicate attempt — retry,
double-deploy, two workers racing past the lease — loses on the constraint and
publishes nothing. Across days, the same product can legitimately be published
again, and the key naturally permits it. No cleanup job, no TTL.

The row is written `claimed` *before* the post exists and settled to `published`,
`abandoned` or `failed` after. A crash between the two leaves a `claimed` row,
which the next tick reaps. There is no window in which a post exists without a
row that knows about it.

## 6. The buyer gate

`eligibility.py` asks one question: *would a member who tapped this be able to buy
it?* Eight reasons to say no, recorded as stable codes so they can be
histogrammed over time:

| Code | Condition |
|---|---|
| `NOT_PUBLIC` | fails `marketplace_listing_lifecycle.public_sql` |
| `SELLER_IS_SYSTEM` | seller is 0 or a system account |
| `OPEN_REPORT` | an open report exists against the listing |
| `SAFETY_FLAGGED` | `safety_score > 60` or any safety flag |
| `NO_TITLE` | blank title |
| `UNPRICED` | blank price label |
| `NO_IMAGE` | no usable image from any of four sources |
| `TEXT_MODERATION` | `ai_moderation_core.moderate_text(..., "pulsedrop")` blocks |

`REASON_ORDER` fixes the evaluation order, so a listing with three problems always
reports the same one and the histogram is stable across runs.

`SELLER_IS_SYSTEM` closes a loop that would otherwise be invisible: PulseDrop
posting PulseSoc's own seed listings would look like activity and be nothing.

## 7. Ranking

`RANKER_VERSION = "pulsedrop-linear-v1"`, stamped onto every publication so a
scoring change is attributable after the fact.

| Component | Weight | Shape |
|---|---|---|
| recent engagement (7d) | 0.32 | saturating, scale 4 |
| lifetime engagement | 0.22 | saturating, scale 12; orders ×3 |
| freshness | 0.20 | linear decay to 0 at 30 days |
| media | 0.18 | images/4, +0.35 for seller video |
| featured | 0.08 | binary |

Saturation is `1 - exp(-v/scale)` rather than a raw count, so one runaway listing
cannot monopolise the surface by being an order of magnitude ahead. The linear
combination is deliberately simple: it is auditable, it has no training data
behind it to go stale, and every input is a number already in the database.

## 8. Editorial honesty

This is the part most likely to embarrass the platform, so the rule is: **a label
is a claim, and PulseDrop only makes claims it can evidence from recorded data.**

| Label | Requires |
|---|---|
| `TRENDING` | ≥3 saves+enquiries in 7 days |
| `NEW_DROP` | published within 72 hours |
| `POPULAR` | ≥10 lifetime engagement |
| `TOP_PICK` | flagged featured by a reviewer |
| `DISCOVERY` | nothing stronger is supported |

Evaluated in that order, strongest first. `DISCOVERY` is the honest default, and
it carries the evidence string *"no stronger claim is supported by recorded
data"* — the fallback says out loud that it is a fallback.

Both the label and its evidence string are **written onto the publication row at
publish time and never recomputed.** Re-deriving at read time would make a post
flicker between "Trending" and "Discovery" as counters moved under it, which is
the kind of detail that makes an automated account look untrustworthy.

Captions cap at 220 characters, titles at 90, hashtags at 4 — of which two are
always `#pulsesocmarketplace` and `#pulsedrop`, so at most two are derived. A
stopword list keeps `#general` and `#uncategorized` out.

## 9. Diversity

Five blockers, each with a stable reason string:

`product_cooldown`, `seller_cooldown`, `category_cooldown`, `cross_format_cooldown`,
`seller_share`.

`REASON_SPECIFICITY` orders them narrowest-first, so when several apply the one
reported is the most informative — "this product, 18 hours ago" beats "some
listing in this category".

The seller-share cap only engages when at least two distinct sellers are eligible.
A share rule enforced against a catalogue with one seller is not fairness, it is
a permanent outage. Production currently *has* one eligible seller, which is
exactly the case this guard exists for.

Category cooldown is deliberately **cross-surface** — a member scrolling sees
Signals and Reels interleaved and does not care which pipeline produced them.

## 10. Distribution

`distribution.decide()` returns one of five outcomes with a reason:

- `SIGNAL_ONLY` — `signal_is_the_right_format`
- `REEL_ONLY` — `seller_video_leads` or `signal_unavailable`
- `SIGNAL_AND_REEL` — `earned_both_formats`
- `DEFER` — `min_publish_interval`, `daily_cap`, or a diversity reason
- `SKIP` — `both_surfaces_disabled`, `no_publishable_format`, `no_enabled_format`

`DEFER` and `SKIP` are not the same and the split is load-bearing. `DEFER` means
*good product, wrong moment* — try again next tick. `SKIP` means *structurally
cannot* — no media, surface off, no ffmpeg. Collapsing them would hide a broken
renderer behind a number that looks like healthy pacing.

## 11. Publishing

Two paths, same shape: claim → revalidate → create post → settle.

The revalidation step re-reads the listing after the claim. Between ranking and
publishing, a seller can withdraw the item; without the re-read, PulseDrop would
publish a post pointing at a 404. A failed revalidation settles the claim
`abandoned`, not `failed` — nothing broke, the world moved.

Signals reference up to 4 marketplace images with `MEDIA_CONTEXT =
"pulsedrop_product"`. They *reference* seller media rather than copying it, so a
seller deleting a photo does not leave PulseDrop serving an orphan.

## 12. The read path

`hydration.py` is the half of PulseDrop that runs on every feed request, and its
rule is the inverse of the editorial rule: **the caption is frozen, the commerce
is live.**

Caption, label and media are whatever was published. Price, stock, seller name
and availability are read fresh on every request, because a post from three days
ago must not advertise a price that changed yesterday.

Five availability states — `""` (available), `OUT_OF_STOCK`, `UNAVAILABLE`,
`NOT_PRICED`, `REMOVED` — each with its own i18n key, reusing existing marketplace
keys where they exist. The CTA is enabled only when the product is genuinely
purchasable; otherwise members get a state chip and no route. A dead-end tap is
worse than a visibly unavailable product.

If `pulsedrop_publications` is missing, hydration returns an empty overlay rather
than raising. A missing PulseDrop must not take down the feed.

## 13. Configuration

26 settings. Every one is a **function**, not a constant, resolving through
`pulsedrop_settings` (20-second memo) with an env-var fallback and a typed
default. Changing one takes effect on the next tick with no redeploy.

`Setting(kind, default, low, high, label, group, help)` carries its own bounds and
its own explanation, so the admin page is generated from the registry rather than
maintained alongside it. Out-of-range values clamp rather than throw.

The kill switch is `PULSEDROP_ENABLED`, checked first thing in `worker_cycle()`.
`PULSEDROP_SIGNALS_ENABLED` and `PULSEDROP_REELS_ENABLED` are independent, so
either format can be stopped without the other.

## 14. Operations

One page: `/admin/pulsedrop`, admin session required. It shows the account, the
lease, health, recent runs with their rejection histograms, publications, renders
and audio clearances, and it accepts setting changes through `ops.apply_action()`,
which routes to `log_admin_audit` like every other admin mutation.

The dashboard's job is to answer "why is it quiet?" without a database client.
`rejected_json` carries `gate:<code>` and `decide:<code>` counts per run, which is
the difference between "nothing is eligible" and "everything is on cooldown".

## 15. The account

`@pulsedrop` is a real `users` row with `login_enabled=0` and a `NULL` password
hash — it cannot be signed into. It carries a verified badge, a brand avatar and
cover, and two disclosures that are not optional:

> This account is operated automatically by PulseSoc. It is not a human user.

> PulseDrop highlights listings from independent PulseSoc sellers. PulseSoc is not
> the seller of these products.

The second one matters for App Review and for the law. PulseSoc is a marketplace,
not the merchant, and an automated account promoting products must say so.

## 16. Tests

255 tests across 8 files, all registered in `config/ci_test_manifest.json`.

They run against the real `bot.init_db()` schema via the `monolith` fixture, not a
hand-rolled one, because a fixture that drifts from production schema validates
nothing. Unique constraints, defaults and column types are the real ones.

## 17. Production status

Live. First publication `2026-09-27T13:58:56` — listing 26, `SIGNAL_ONLY`,
reason `signal_is_the_right_format`, 15 candidates evaluated, 15 eligible.

Both surfaces have since published. The first composed Reel went out at
`16:02:00` the same day, with a cleared music bed under it; the evidence table is
in `docs/pulsedrop_reels.md` §20.

Known and expected limits of the current environment:

- **One eligible seller.** All 11 eligible listings belong to `user_id=1`. The
  seller-share cap is correctly inert.
- **Two hours between ticks** means at most 12 evaluations a day, and the pacing
  floor and daily cap bind well below that.
- Quiet cycles log nothing on purpose (`_PULSEDROP_QUIET`), because a line that
  never changes, every five seconds, is not observability.

## 18. What is deliberately not here

- **No ML ranker.** A linear scorer over five recorded signals is auditable and
  cannot silently rot. When there is enough traffic for the current one to be
  visibly wrong, that is the time to replace it.
- **No paid placement, no seller opt-in fee, no sponsored slot.** Introducing
  money into the selection would change what every label means.
- **No writes to seller data.** PulseDrop reads the marketplace and writes only its
  own tables and its own posts.
- **No natural-language caption generation.** Captions are templated from real
  fields. A model writing marketing copy about a product it cannot verify is
  precisely the failure mode the editorial rules exist to prevent.
