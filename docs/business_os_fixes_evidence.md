# Business OS — Fixes and Evidence

The companion to `business_os_ground_truth.md`. That document identified what was
broken; this one records what was fixed, how each fix was proved, and what was
deliberately left alone.

Ten defects were closed across three passes. Nine of them lose, mis-account or
mis-report money. The tenth produces a badge the user cannot clear. All of them
were silent — every existing test passed the entire time, which is itself the
finding that shaped how these were verified.

Two of the five closed in the first pass were **reopened by an independent
review** and needed a second fix: the Stripe cumulative-field defect survived in
the reverse event ordering, and the refund idempotency key never reached the
admin form route or the dispute-resolution path. Both are recorded in place below
rather than quietly absorbed, because a document that swallows its own
corrections is the same hazard as a stale docstring — and in both cases the first
pass had shipped a suite that scored full marks while the defect was still live.

The third pass closed the three items this document previously listed as open:
the two remaining review findings (the connection wrapper's blanket rollback,
and the store gate swallowing transient database errors) and the non-atomic
payment capture that had been recorded as deliberately deferred. Those entries
have been moved out of "What was deliberately not done" into sections 6, 7 and 8
below. **An earlier version of this document listed all three as outstanding;
that version was accurate when written and is no longer.**

## How each fix was proved

A test that passes against the fixed code proves nothing on its own; it may be
asserting something that was already true. So every fix here went through the
same loop:

1. Write the fix.
2. Write a suite that passes against it.
3. Back up the fixed source, script a revert to the exact pre-fix behaviour, and
   run the suite again.
4. Confirm the suite **genuinely fails**, and read the failure messages to check
   they describe the original defect rather than incidental breakage.
5. Restore from the backup, confirm restoration by grep, re-run to green.

The revert numbers below are the evidence. A suite that scored 13/13 after the
fix and 13/13 before it would have been worthless.

| # | Defect | New suite | After fix | Against pre-fix code |
| --- | --- | --- | --- | --- |
| 1 | Ledger overdraft guard is a no-op on Postgres | `test_ledger_concurrency_portability.py` | 8/8 | **3/8** |
| 2 | Marketplace refunds are not idempotent | `test_refund_idempotency.py` | 14/14 | **11/14** |
| 3 | Stripe cumulative `amount_refunded` posted as a delta | `test_stripe_refund_delta.py` | 20/20 | **17/20** |
| 4 | Store never checks seller eligibility | `test_store_seller_eligibility.py` | 19/19 | **15/19** |
| 5 | Commerce threads badge the social Messages tab | `test_badge_domain_scoping.py` | 14/14 | **6/14** |
| 5 | — client half | `badgeSources.test.ts` | 19/19 | **16/19** |
| 6 | Blanket rollback discards the caller's savepoint | `test_savepoint_recovery.py` | 19/19 | **15/19** |
| 7 | Store gate reads a database outage as "not approved" | `test_store_seller_eligibility.py` | 19/19 | **15/19** |
| 8 | Payment capture spans three connections | `test_capture_atomicity.py` | 15/15 | **5/15** |

Rows 2 and 3 grew in the second pass; the "against pre-fix code" column for those
two now reports the revert of the *second* fix, with the first fix left in place.
That is the stricter reading: 11/14 and 17/20 are the scores of a suite reverted
against code that already passed 9/9 and 11/11. The three failures in each case
are the reopened defect and nothing else.

Rows 4 and 7 are the same file, listed twice on purpose: defect 4 was the missing
gate and defect 7 was that gate misreading an outage. The suite grew from 13
tests to 19 when the second was fixed, so the 13/13 → 4/13 originally recorded
for row 4 no longer describes a file that exists. Both rows now report the
current file reverted against the *second* defect.

Row 8's 5/15 needs one honest caveat, given below in section 8: two of the ten
failures are the deadlock the old code hit, one is the connection count, six are
the drift detector's absence — and two tests that pin the owned path pass against
the old code, because that path was already correct. The defect lived on the
borrowed-connection path, which the fix removes rather than repairs.

---

## 1. The overdraft guard was upheld by an accident

`services/business_os/ledger/ledger.py`

`_begin()` took a write lock only when `db.ENGINE_NAME == "sqlite"`. The
insufficient-funds check then read the balance with an unlocked `SELECT`. On
SQLite, `BEGIN IMMEDIATE` serialized writers and the guard held. On Postgres
under READ COMMITTED `_begin()` did nothing, two concurrent posters read the same
pre-debit balance, both passed, and both posted.

The guard was correct only on the development engine. Every test passed, and
passed for a reason that disappears in production — which is the worst shape a
test can have.

The fix takes explicit row locks on the affected balance rows before reading
them, acquired in sorted order so two transactions touching the same pair of
accounts cannot deadlock against each other. Locking is done through a no-op
`UPDATE` rather than `SELECT ... FOR UPDATE` so the same statement works on both
engines. Expected-to-fail INSERTs are wrapped in savepoints, because on Postgres
a failed statement poisons the surrounding transaction and every subsequent
statement in it.

## 2. The refund key was generated inside the call it was meant to protect

`services/business_os/marketplace/refunds.py`

The idempotency key was `"mkt_refund:" + uuid4().hex`, minted fresh on each
invocation. The ledger beneath it is correctly keyed and would have deduplicated
— it simply never saw the same key twice. The function signature accepted no
idempotency parameter, so a caller could not supply one even knowing this.

The fix derives the key from a caller-supplied refund identifier, following the
pattern already used by `advertising/funding.py:489`. The database is the
enforcer: the derived value is a PRIMARY KEY, so correctness does not depend on
callers remembering to be careful.

### Second pass: the key was plumbed everywhere except where it was needed

An independent review found that the fix above, while correct, was not reaching
two of its callers.

**The admin refund form stripped the key at the door.** `_bo_mkt_form_payload`
takes an allowlist, and the Flask route
`admin_business_os_marketplace_refund_order` in `bot.py` passed
`{"amount_cents", "reason"}`. The JSON API path allowlisted the key —
`api.ADMIN_REFUND_FIELDS` includes `idempotency_key` — but nothing connected the
two sets, so nothing noticed they had drifted. Any key a client sent to the form
route was discarded and `refund_order` fell back to `"mktr_" + uuid4().hex`. The
double-submitted refund button, which is the case the whole fix was written for,
was the one case still issuing two refunds.

The route now allowlists `idempotency_key`, and additionally accepts it as an
`Idempotency-Key` or `X-Idempotency-Key` header — both spellings, because both
are in the wild — with an explicit form field winning over a header.

**`resolve_dispute` issued its refund with no key at all.** The one refund caller
that provably issues at most one refund per entity was the one caller not saying
so. Its `status != 'open'` check is a read followed by a write, so two concurrent
resolutions can both pass it and both refund; the status guard cannot close that
window because the guard is the thing racing. The refund is now keyed
`f"dispute:{dispute_id}"` — keyed on the entity that resolves exactly once, which
makes the second refund a replay of the first rather than a second refund.

The lesson is the reason two of the new tests read source rather than values:
**every layer had the key and the layer facing the button did not.** Each layer's
tests passed against its own inputs while the chain between them was broken.
`test_admin_layer_threads_the_key_and_does_not_double_audit` even carries the
docstring "The admin form is the caller that most needed this" — and exercises
`admin.admin_refund_order`, not the form. A value-based test cannot see a missing
link; it can only see the two ends. So the new tests slice the Flask route out of
`bot.py`, `ast.parse` it, extract the literal allowlist from the
`_bo_mkt_form_payload` call, and assert it equals `api.ADMIN_REFUND_FIELDS`. Two
allowlists for one operation must not drift, and now they cannot drift silently.
Parsing rather than grepping matters here for the same reason as in §3: the
route's own explanatory comment contains the string a grep would match.

## 3. A cumulative field was being posted as a delta

`services/business_os/payments/stripe_ledger_handler.py`

Stripe's `amount_refunded` on a Charge is a running total for that charge, not
the amount of the refund that triggered the event. `charge.refunded` fires again
on each partial refund, with a new event id and a larger total. Two partials of
$5 and $3 produced one event reporting 500 and a second reporting 800; both had
distinct event ids, so per-event idempotency correctly admitted both, and $13
left the ledger against $8 of actual refunds.

**The idempotency was never the broken part. The field was.**

Three changes:

- **Expand `refunds.data`** into per-refund deltas, and key each on the Stripe
  *refund* id rather than the event id. `refund.created` and `charge.refunded`
  describe the same refund under different event ids, so keying on the event
  could never make them deduplicate against each other; keying on the entity
  does it for free.
- **Subtract before falling back.** Where only a cumulative total is available,
  the handler subtracts what it has already posted for that charge.
- **Tag every refund posting with `related_object = "stripe_charge:<id>"`.** The
  fallback sums on that tag, not on `provider_reference` — which holds the refund
  id on one path and the charge id on the other, so a sum over it would have come
  back zero and posted the cumulative total in full on top of the individual
  refunds. That is the original defect reintroduced through the back door, and it
  is the mistake this fix came closest to making.

The module docstring was rewritten. Its old claim — "the ledger idempotency key
is derived from the Stripe event id" — was false for refunds after this change,
and a stale docstring is its own defect: the next reader trusts it.

One test pins the fix at the source level by walking the AST rather than
grepping. A substring search for `amount_refunded` matches the explanatory
comment as readily as a relapse. The test asserts exactly one assignment reads
that field and that its target is named for a total.

### Second pass: the netting was only one-directional

An independent review found the double-count still live in the reverse event
ordering, and it reproduced immediately: one $5 refund, `charge.refunded` first
and `refund.created` second, produced **1000 posted against 500 of actual
refunds** — two rows, `stripe:charge_refund_total:ch_1:500` and
`stripe:refund:re_1`.

The subtraction ran one way only. A cumulative event subtracted the individual
refunds it could see; an individual refund arriving *after* a cumulative event
added on top of it, because nothing told the handler to look. And that ordering is
not exotic — it is the default. On current Stripe API versions `refunds.data` is
not expanded, so `charge.refunded` takes the cumulative fallback and
`refund.created` follows for the same refund. The path the fix had left open was
the path production actually takes.

The fourth change:

- **Netting is symmetric.** `_already_refunded_for_charge` became
  `_charge_refund_state`, returning `(posted_cents, established_total_cents)` from
  one scan, and every individual refund posting now carries `cap_to_charge` so the
  handler knows which charge to compare against. When a cumulative total has
  already been asserted for that charge, the individual refund posts at most
  `established - already`.

The justification is what `amount_refunded` means. A cumulative event asserts a
running total for the charge, so it has accounted for **every** refund on it —
including the ones it could not enumerate, which is precisely why the fallback
ran. An individual refund arriving afterwards is not new money; it is that same
money, named.

Three details that were not obvious:

- **Headroom, not an outright skip.** `established - already` rather than
  "cumulative has spoken, drop it." Void the cumulative row and its claim is
  withdrawn with it — `posted` drops, headroom reopens, and individual refunds may
  post normally again. A hard skip would have made a voided row unrecoverable.
- **The asserted total is parsed out of the idempotency key.** A cumulative row's
  `amount_cents` is the *delta it moved*, not the *total it stood for* — after a
  500 cumulative posting followed by a 300 one, the second row's amount is 300 and
  its claim is 800. Reading the delta would have left 500 of phantom headroom. The
  total is recovered from `stripe:charge_refund_total:<charge>:<total>`, so the
  fact is read from the same string that guarantees its uniqueness rather than
  from a second column that could disagree with it. A test pins this directly:
  `_charge_refund_state("ch_s", "usd") == (800, 800)`.
- **Under-posting is the safe direction.** One case is imperfect: a genuinely new
  refund arriving while an older cumulative claim is outstanding is absorbed, and
  the ledger is briefly short. It closes on the next `charge.refunded`, which
  Stripe always sends. Being briefly short is recoverable; having paid twice is
  not.

Nine tests cover the orderings — cumulative-then-refund, refund-then-cumulative,
two partials each way, replays, two charges not sharing a budget, an expanded
refund list not absorbing its own event, and a refund naming no charge never being
absorbed. That last pair guard against the cap firing unconditionally, which would
have traded a double-post for lost money.

## 4. Two selling surfaces, one approval record, one gate

`services/business_os/store/service.py`

Store had exactly one access check: `_require_biz_permission`, which resolves the
caller's role on the business. That answers *"may this person act for this
business"* — a completely different question from *"may this business take money
from the public"*. Publishing a storefront and activating a product are the two
acts that put goods in front of shoppers, and both were reachable by anyone
holding an admin role on a business nobody had ever reviewed.

The marketplace — the *other* surface over the same catalogue idea — gets this
right. `marketplace/service.py::require_active_seller` refuses every seller write
unless `business_os_mkt_sellers.status == 'approved'`. The approval record
existed, was already described in its own schema comment as the authority on who
may sell, and Store simply never read it.

The fix reads that same record before making anything live, with four deliberate
properties:

- **Keyed on the business owner, not the caller.** Otherwise an approved
  individual could front for an unvetted business — the exact substitution the
  approval exists to prevent.
- **Drafting stays open.** The review is about selling, not about typing. A
  business locked out of its own catalogue editor until approval day would have
  nothing to launch.
- **Taking a store down is never gated.** Suspend and archive keep working for a
  rejected or suspended seller. A gate that traps a live storefront online is
  worse than no gate.
- **Re-checked on the public read path**, so revocation is immediate rather than
  waiting for a sweep job — while the *stored* status is left untouched, so
  re-approval restores the store without anyone remembering to re-publish it.

"We cannot check" and "we checked and no" are reported as different facts:
`503 seller_review_unavailable` when the seller table is absent,
`403 seller_not_approved` when it says no. Both refuse — the gate fails closed
either way — but an operator staring at a 403 would go looking for an application
that was never the problem.

The gate reads the table directly rather than calling `require_active_seller`,
because that function asserts the marketplace feature flag. Store's availability
must not be coupled to the marketplace being switched on. The new suite sets
`BUSINESS_OS_MARKETPLACE = ""` at module level and asserts the flag is off, so
this cannot be "simplified" away later without the suite noticing.

`test_store_core.py` and `test_store_api.py` both had to be updated: they
previously encoded the bug, publishing live storefronts for businesses no one had
approved. Saying so plainly is part of the fix.

## 5. A badge pointing at a screen that would not show it

`services/notification_service.py`, `bot.py`, `mobile-native/src/`

The mission document asked to "remove commerce threads from PulseSoc social
Messages." That work already shipped — but only in the lists, not in the badge.

`pulse_conversation_summaries()` takes an `include_types` filter and both social
list endpoints pass `{"direct"}`, so the Messages screen has never rendered a
business thread. `pulse_badge_counts` summed `unread_count` across every row of
`pulse_conversation_participants` with no domain predicate at all, and the
Business OS messages facade bumps exactly that counter when a business replies.

The result: a number on the Messages tab pointing at a conversation the Messages
tab will not open. An unread the user cannot clear — which trains people to
ignore badges, the one thing a badge cannot survive.

The participants sum now splits on `conversation_type`. Social keeps
`chat_unread_count`; business threads move to a new `commerce_unread_count`, so
the number is separated rather than discarded and the Commerce Inbox has
something to badge.

Four decisions worth stating:

- **The filter excludes only `'business'`.** Written as "direct only" it would
  have silently emptied the badge for group and room chats, which the social
  lists *do* fetch. A test pins this.
- **`LEFT JOIN`, with the type COALESCEd to `'direct'`.** A participant row whose
  conversation is missing stays social, which is what it counted as before. An
  INNER JOIN would have quietly deleted unreads as a side effect of a fix that
  was supposed to be about domains.
- **`comm_v2_participants` and the legacy `private_messages` join are
  untouched.** Neither carries a `business_id` or a `'business'` type; the
  Business OS messages facade writes only to `pulse_conversations`. Verified
  before deciding, not assumed.
- **`total_unread_count` stays `alert + chat`, excluding commerce.**
  `totalUnreadCount()` in `mobile-native/src/api/notifications.ts` falls back to
  `alert_unread_count + chat_unread_count` whenever the explicit total is absent
  or zero. Folding commerce into the server's total would make the client
  disagree with itself depending on which branch ran.

The client half carries the key through to a `commerceCount` on the shared
snapshot, a `"commerce"` badge scope with its own spoken label, and a `"commerce"`
optimistic-read scope so clearing the Commerce Inbox does not blank the social
badge. Two subtleties the tests pin: the snapshot's change-detection had to learn
about the new field, or a commerce-only change would move none of the three
compared numbers and the update would be silently swallowed; and `combined` — the
figure the phone's app icon uses — *does* include commerce, because the app icon
is the one badge with nothing beside it to double against, and an unread the icon
omits is a customer who never learns their order was answered.

`business_id` was **not** used as the discriminator. It is added by an additive
`ALTER TABLE` in `services/business_os/messages/schema.py:128` and is absent from
bot.py's own `CREATE TABLE`, so it may not exist in a given database.
`conversation_type` is in the canonical CREATE and always does.

---

## 6. The recovery mechanism and the thing that destroyed it were the same call

`services/db.py` — `CompatCursor.execute` caught any SQL error and called
`self._owner.rollback()` before re-raising. That existed for a real reason: on
PostgreSQL a failed statement poisons the transaction, and every later statement
errors with 25P02 until someone unwinds it. The blanket rollback un-poisons it.

But a full rollback also discards every open savepoint. So a caller that had
wrapped a statement in `SAVEPOINT sp` — precisely because it *expected* that
statement to fail — found its savepoint already gone by the time its
`ROLLBACK TO SAVEPOINT sp` ran, which then died with SQLSTATE 3B001, "no such
savepoint." The recovery path was destroyed by the same call that was supposed
to make recovery possible. `ledger.py::_ensure_balance_row` is the caller that
does this, and its expected-to-fail bootstrap INSERT is the statement in
question.

The fix narrows the rollback rather than removing it: it still runs when no
savepoint is open, and defers to the caller when one is. Deciding that requires
knowing the savepoint stack, and neither DBAPI nor psycopg exposes it — so
`CompatConnection` now classifies the SQL going past it. `_savepoint_op` reads
`SAVEPOINT`, `RELEASE`, `ROLLBACK TO` and plain transaction ends. The ordering
of those checks is load-bearing: **`ROLLBACK TO SAVEPOINT x` begins with the word
ROLLBACK and means the opposite of one**, so `ROLLBACK TO` is matched before the
bare-transaction pattern. Matching it the other way round would clear the stack
on the exact statement that proves a savepoint is in use.

Four of the nineteen tests pin the direction that must *not* change:
`test_without_a_savepoint_the_transaction_is_still_rolled_back`,
`test_a_released_savepoint_no_longer_defers_the_rollback`, and the two tests
that assert `commit()` and `rollback()` clear the stack. Without those, a stack
that only ever grew would suppress the rollback forever after the first
savepoint in a connection's life — trading the original defect for a worse one.

The suite runs on SQLite and needs no PostgreSQL, because SQLite discards a
savepoint on full rollback for the same reason PostgreSQL does. Against the
pre-fix wrapper it scores 15/19, and the headline failure reads
`OperationalError: no such savepoint: sp` — the original defect, named.

---

## 7. "We cannot check" and "we failed to check" are not the same answer

`services/business_os/store/service.py` — `_seller_status` caught bare
`Exception` and returned `"__unavailable__"`, the sentinel meaning "the seller
table was never provisioned." A transient database error therefore became
indistinguishable from an unprovisioned deployment. Section 4 above makes a
point of separating "we cannot check" from "we checked and the answer is no";
this path collapsed a third case, "we failed to check," into the first.

The consequence lands on `public_storefront`, where the collapse produced a 404
during a database blip. A 404 is not a neutral fallback — it tells the shopper
the shop is gone, tells the crawler to de-index the page, and tells the merchant
nothing at all. The outage hides inside a permission decision and the merchant
is billed for it.

So the catch now asks a specific question. `_is_missing_table_error` checks
SQLSTATE first because it is unambiguous — PostgreSQL's 42P01 is
*undefined_table* and nothing else — and falls back to message patterns only
when no code is present, since PostgreSQL localises its messages and a
substring match on English text is not a guarantee. Anything else re-raises as
`StoreError(503, "seller_review_failed")`, which is retryable and shows up in
the error rate where somebody will look at it.

One subtlety the tests pin: *having* a SQLSTATE is not having *that* SQLSTATE.
`test_a_sqlstate_that_is_not_undefined_table_is_treated_as_transient` uses
40P01, `deadlock_detected`, which must read as transient. The test harness
raises only on statements naming `business_os_mkt_sellers`, deliberately narrow
so `public_storefront`'s earlier storefront read still succeeds and the test is
exercising the gate rather than a dead connection. Against the pre-fix code the
suite scores 15/19, failing with `expected seller_review_failed, got
seller_review_unavailable`.

---

## 8. A capture spanning three connections is not one operation

`marketplace/orders.py::pay_order` wrote through three different connections in
a single call: the inventory decrement and the `created ─▶ paid` flip on `conn`,
committed **only when it owned that connection**; the compensation for a failed
capture on a freshly opened `c2`; the `capture_txn_ref` write on a third, `c3`.

A caller passing its own connection therefore ended up with an *uncommitted*
inventory decrement sitting beside a *committed* ledger post. Worse, the
compensation on `c2` could not see that uncommitted decrement, so its
`inventory_qty + quantity` reversal was applied to the committed value — which
had never been decremented. **A failed capture created stock.** Nothing
reconciled it afterwards, and that absence is why this was recorded as
deliberately-open rather than fixed in the earlier passes.

The fix is a refusal rather than a repair, and the reason is worth stating.
`post_entry` opens, commits and closes its own connection by design, and on
SQLite takes a database-wide write lock via `BEGIN IMMEDIATE` while doing it.
So the capture *cannot* join the caller's transaction, and holding a write
transaction open across it deadlocks. **That was discovered by a test failure,
not by reading**: the first design removed the pre-capture commit so the whole
operation would be one transaction, and `test_orders_core` immediately failed
with `sqlite3.OperationalError: database is locked` raised from inside
`post_entry`. The commit-before-capture that looked like sloppiness was
load-bearing.

Those two facts make the borrowed-connection path unsound by construction rather
than merely buggy. A grep of every call site found **no caller passes `conn=`**,
so `pay_order` now refuses one outright — removing an invitation to a bug rather
than a feature anyone used — and does all its own writing, compensation and
`capture_txn_ref` included, on the single connection it opened itself.
`test_no_caller_in_the_codebase_passes_a_connection` AST-walks `services/` and
`bot.py` to keep that premise honest, so if someone later adds such a call the
test explains why it will not work instead of leaving them to find out in
production.

One window remains and is stated rather than papered over: the capture can
succeed and the `capture_txn_ref` write can then fail. That is the safe
direction — money in escrow, order marked paid — and it is now *detectable*.
`reconcile_captures` reports three kinds of drift (`captured_not_paid`,
`paid_not_captured`, `missing_capture_ref`) and **repairs none of them**, because
a crash mid-commit and a support agent who moved money by hand present
identically here, and a job that guesses will eventually guess wrong with
somebody's money. `test_the_reconciler_reports_and_does_not_repair` asserts that
neither order state nor escrow balance moves across a `reconcile_captures()`
call.

Against the pre-fix code the suite scores 5/15. The failures name the defect:
`OperationalError: database is locked` on the two borrowed-connection tests,
`pay_order opens 3 connections; the fix leaves exactly one`, and six
`no attribute 'reconcile_captures'`. The honest caveat is that
`test_a_failed_capture_does_not_create_inventory` and
`test_a_failed_capture_leaves_no_money_in_escrow` **pass against the old code**,
and that is correct: the old owned path did commit before capturing, so `c2`
could see the decrement. The stock-inflation bug required a borrowed connection,
and the fix closes that path rather than making it work. Those two tests are
there to pin that the owned path stays correct, not to demonstrate the defect.

---

## Regression evidence

Re-run in full after the third pass, not just over the suites that changed.
Nothing was left red.

**Python — the entire `tests/business_os/` tree, 92 files, 965 tests, zero
`FAIL` lines and zero non-zero exit codes.** Every one of the 92 files now
reports a count; the three that previously emitted no standalone tally were
picked up once the tally also recognised the bare `N passed` form, so the earlier
"87 of 90 reporting" figure was a limitation of the counter rather than of the
suites. The headline suites: `test_ledger_concurrency_portability` 8/8,
`test_stripe_refund_delta` 20/20, `test_refund_idempotency` 14/14,
`test_store_seller_eligibility` 19/19, `test_badge_domain_scoping` 14/14,
`test_savepoint_recovery` 19/19, `test_capture_atomicity` 15/15, with
`test_store_core`, `test_store_api`, `test_marketplace_*`, `test_orders_*`,
`test_messages_core`, `test_advertising_*` and `test_seller_*` all green
alongside.

The sweep was tallied three ways on purpose, because the first way was wrong.
Counting the leading integer of each `N/M tests passed` line would have read
`11/14` as a pass and hidden three failures; the tally now compares `N` against
`M` and prints a MISMATCH line for any disagreement (it printed none). A separate
`grep -E "^FAIL"` across all 92 logs confirmed **0 FAIL lines**, and the exit
code of every file was recorded independently — **0 non-zero exits**. Three
methods, no disagreement.

One log, `test_savepoint_recovery.log`, contains `ERROR:root:` lines and a
traceback. Those are the connection wrapper's own deliberate diagnostics, emitted
by tests that provoke SQL failures on purpose; that file passes 19/19. A grep for
"Traceback" alone would have flagged it as a failure, which is why the exit code
and the tally were the deciding checks rather than the presence of scary text.

`bot.py` was re-parsed to confirm it still compiles after the route edit.

**TypeScript — `tsc --noEmit` exit 0, zero `error TS` lines**, run from
`mobile-native/` with the vendored compiler (the repo root has no `node_modules`,
and `npx tsc` cannot reach the registry from this environment).

**Jest — 155 suites, 2805 tests, 0 failed**, run as four shards
(`--shard=N/4 --json`) and totalled from the JSON reports. This is the whole
mobile-native suite; the "32 suites, 578 tests" recorded after the second pass
was a narrower invocation (`jest src/navigation src/core`) and is superseded
rather than contradicted. `badgeSources.test.ts` remains 19/19 within it.

**No leftover scaffolding.** `grep -rn "PRE-FIX"` returns nothing across
`services/`, `mobile-native/src`, `bot.py` and `tests/` — none of the nine revert
scripts left a trace.

**Changed surface:** 1253 insertions, 134 deletions across 16 tracked files, plus
seven new test files.

## What was deliberately not done

**The three items this section previously listed are now closed** — money bug #4
in §8, the `db.py` rollback finding in §6, and the `store/service.py` catch in
§7. They are named here rather than silently deleted, because the earlier
reasoning for deferring them is part of the record and the last of it turned out
to be wrong: the capture fix was described as needing "a transaction-boundary
redesign across three connections," and the actual answer was to stop using
three connections and refuse the path that required them.

**The residual capture window is not closed, by design.** `reconcile_captures`
detects it and does not repair it, for the reason given in §8. Turning the
detector into a repairer is a decision about money that wants a human policy
first, not a better algorithm.

**`reconcile_captures` is not scheduled.** It is a callable with tests, not a
cron entry. Wiring it to a scheduler, deciding a cadence, and choosing where its
output goes are deployment decisions this pass does not make.

**PostgreSQL was not exercised.** Both §1 and §6 concern PostgreSQL-specific
behaviour, and both are tested on SQLite, which reproduces the relevant semantics
(row locking in §1, savepoint discard on full rollback in §6). That is a
reasonable proxy and it is not the real thing. No PostgreSQL instance is
reachable from this environment.

**The seven off-by-default flags were not flipped.** That is a rollout decision,
not an engineering one, and it belongs to whoever owns the release. The inventory
is in `business_os_ground_truth.md`; the consequence of not deciding is that any
future screenshot-based audit will keep reporting finished features as missing.

**The unified commerce entity graph was not built.** Three product tables with no
foreign keys between them remains the largest genuinely-absent item and the real
prerequisite for the end-to-end chain the mission document wants. It is a schema
program, not a bug fix, and it did not belong in a pass whose purpose was to stop
the bleeding.
