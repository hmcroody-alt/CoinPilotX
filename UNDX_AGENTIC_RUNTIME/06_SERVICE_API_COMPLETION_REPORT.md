# 06 — Internal service API completion

## Verdict

**PASS on the build. PRODUCTION QA STILL BLOCKED ON THE SAME UNPUSHED COMMIT.**

Thirteen capabilities covering the eleven requested operations are wired end to end
through all five files the runtime requires. The registry stands at **120 capabilities /
48 writes**, `unregistered_tool_names() == []`, and the new suite is **95 passed, 137
subtests passed**. Every mutation reaches the database through a shared service function
that a Flask route calls too, or — in two cases named precisely below — through a service
function that no route calls because no route exists.

The mission's definition of COMPLETE has six clauses. Five are met for all thirteen. The
sixth, *"existing routes call it"*, is met for twelve and is vacuous for one: PulseSoc has
never had a bio-only endpoint, so there was no route to migrate. That is recorded here
rather than smoothed over, because "no route exists" and "the route was migrated" are
different facts and only one of them is evidence of a shared authority.

An earlier draft of this report said the clause was vacuous for **two**, claiming no unblock
route existed either. That was wrong, and the error was in the method rather than the
reading: the ROUTES MIGRATED table was built by grepping `bot.py`, which is where most of
this product's routes live but not all of them. `services/pulse_settings_routes.py` carries
a blueprint of its own, and it has both `POST` and `DELETE /blocked`. Corrected below.

The mission's **audit contract is met by six of thirteen in full**, not by all of them. Six
more write to pre-existing audit tables that predate the contract and lack the correlation
id; `reels.comment.create` writes no mutation-audit row at all. This follows directly from
the instruction not to build a second authority system — reuse inherits the reused thing's
gaps — but the verdict should not read as though the trail were uniform. The coverage table
is under AUDIT TESTS and the exceptions are named there.

Local `HEAD` is still `1ec72577caad41b744a1c1ce1e10d51b4c8b3ea8` on
`release/full-sweep-20260826`, which contains none of this work. Nothing here is
deployable until the working tree is committed and pushed from a machine whose git can
take a lock — the sandbox limitation described in report 05 is unchanged.

## FOUND EXISTING SERVICES

Four of the eleven operations already had a service function that was correct and was
left alone:

- `pulse_feed_engine.add_comment` — already the single writer for comments, already
  called by four routes (bot.py:81353, 82319, 82364, 84027).
- `pulse_profile_service.update_profile` — already owned sanitation, the reserved-handle
  check, the uniqueness probe, the hourly rate limit, the `UPDATE` and the audit write.
- `business_os.marketplace.service` — `create_product`, `update_product` and
  `transition_product` already carried ownership, the state machine and the flag gate.
- `pulse_feed_engine.report_content` — extended rather than replaced (see REPORT API).

Per the binding decision on Fork 2, **nothing new was built in the marketplace service.**
The five listing capabilities are wiring over verbs that already existed.

## EXTRACTED ROUTE LOGIC

Three mutations lived only inside Flask handlers and were lifted out:

| Was | Now |
|---|---|
| block logic inline in the `/api/pulse/users/<id>/block` handler | `pulse_social_graph_service.block_user` |
| block/unblock via the generic `_mutate_relationship` toggle in the Settings blueprint | `…block_user` / `…unblock_user` |
| reel deletion inline in the web reels handler | `pulse_feed_engine.delete_owned_reel` |
| comment edit/delete inline in the web reels handlers | `pulse_feed_engine.update_comment` / `delete_comment` |

The block extraction removed behaviour as well as moving it. The old handler opened a
`pulse_reports` row on **every** block, so "I don't want to see this person" and "I am
accusing this person" filed the same moderation case — inflating the open-report count and
putting unreviewed accusations against people whose only offence was being blocked into a
queue. Per the binding decision on Fork 1, canonical `block_user` writes `blocked_users`
**and** `comm_v2_blocks`, always emits the safety event, and **never** auto-files a report.
Reporting stays available explicitly through `/api/pulse/report`. The route now carries a
comment saying so, at bot.py:91012-91019.

## NEW SERVICES CREATED

- `services/pulse_social_graph_service.py` — `block_user`, `unblock_user`, `block_state`.
- `services/pulse_profile_service.py` — `update_profile`, `update_profile_bio`.
- `services/pulse_mutation_audit.py` — `record(cur, ...)`, taking a cursor so the audit
  row commits inside the mutation's own transaction rather than after it.

`update_profile_bio` is a named wrapper, **not** a second implementation: it delegates to
`update_profile(requester_user_id, bio=bio, surface=surface)` (pulse_profile_service.py:259)
and adds nothing but a convenience key on the result.

It is **not** true that this is the only writer of profile columns, and an earlier draft of
this report said so. Three other writers exist and were out of this mission's scope:
`bot.py:92004` (an admin handler writing `display_name`, `username`, avatar, cover, banner
and `bio` directly, capped at 1000 chars against the service's `BIO_MAX=500`),
`bot.py:100595` (`save_user_name`, `UPDATE users SET display_name=?` with no sanitation, no
audit and no rate limit), and `services/pulse_settings_routes.py:662` (writing
`profile_visibility`, a field `update_profile` also owns at pulse_profile_service.py:170,
inside a `try/except` that swallows failure silently). What this mission establishes is
that **UNDX and the profile route share one authority**; it does not establish that the
whole product does. That remains open.

## ROUTES MIGRATED

Verified by reading the call sites, not by assertion:

| Service function | Route call sites |
|---|---|
| `pulse_social_graph_service.block_user` | bot.py:91021, bot.py:99184, pulse_settings_routes.py:888 |
| `pulse_social_graph_service.unblock_user` | pulse_settings_routes.py:891 |
| `pulse_feed_engine.delete_owned_reel` | bot.py:82413 |
| `pulse_feed_engine.add_comment` | bot.py:81353, 82319, 82364, 84027 |
| `pulse_feed_engine.update_comment` | bot.py:82555 |
| `pulse_feed_engine.delete_comment` | bot.py:82560 |
| `pulse_feed_engine.report_content` | bot.py:90931 |
| `pulse_profile_service.update_profile` | bot.py:98400 (`/api/pulse/profile/update`) |
| `pulse_profile_service.update_profile_bio` | **none — no bio-only route exists** |

The Settings blueprint is the fourth block call site and the **only** unblock one, and it is
the migration that most vindicates the Fork 1 decision. `POST`/`DELETE /blocked` used to run
through `_mutate_relationship`, a generic two-column relationship toggle shared with muting.
That meant a block placed from Settings wrote `blocked_users` and **told nobody** — no
`comm_v2_blocks` row, no safety event — while the feed and Messenger paths wrote more. Two
surfaces, two different meanings of the word "block", and no test comparing them. Both verbs
now delegate to the shared service and the route retains only transport: request to two
integers, service exception to a status code (pulse_settings_routes.py:872-916). Muting
stays on `_mutate_relationship`, correctly — it really is one row read by the ranking path,
with none of the cross-subsystem consequences that made blocking unfit for a generic toggle.

The one remaining blank is an honest gap in the *product*, not in this work. The bio is
reachable today only through the all-fields `/api/pulse/profile/update` form; the wrapper
already shares its authority with that route, so it is the shared authority the moment a
bio-only endpoint is written.

## The thirteen APIs

| Capability | Risk | Confirm | Service | Verifier | Undo |
|---|---|---|---|---|---|
| `profile.block` | reversible | contextual | `pulse_social_graph_service.block_user` | `profile_block_value` | `profile.unblock` |
| `profile.unblock` | reversible | contextual | `…unblock_user` | `profile_block_value` | `profile.block` |
| `profile.bio.update` | consequential | **always** | `pulse_profile_service.update_profile_bio` | `profile_bio_value` | — |
| `reels.delete` | consequential | **always** | `pulse_feed_engine.delete_owned_reel` | `reel_deleted` | — |
| `reels.comment.create` | consequential | **always** | `…add_comment` | `reel_comment_body` | `reels.comment.delete` |
| `reels.comment.update` | consequential | **always** | `…update_comment` | `reel_comment_body` | — |
| `reels.comment.delete` | consequential | **always** | `…delete_comment` | `reel_comment_deleted` | — |
| `feed.report` | consequential | **always** | `…report_content` | `content_reported` | — |
| `marketplace.listing.create` | consequential | **always** | `marketplace.service.create_product` | `marketplace_listing_created` | — |
| `marketplace.listing.update` | reversible | contextual | `…update_product` | `marketplace_listing_field_value` | — |
| `marketplace.listing.pause` | reversible | contextual | `…transition_product` | `marketplace_listing_status` | `…resume` |
| `marketplace.listing.resume` | reversible | contextual | `…transition_product` | `marketplace_listing_status` | `…pause` |
| `marketplace.listing.delete` | consequential | **always** | `…transition_product` | `marketplace_listing_status` | — |

### BLOCK API / UNBLOCK API

Canonical union write across `blocked_users` and `comm_v2_blocks`, always emitting
`pulse_emit_comms_safety_event`, never auto-reporting — verified as the only writer of
either table repo-wide. One qualification the phrase "union write" hides:
`_write_comm_v2_block` is a **silent no-op when the table is absent**
(pulse_social_graph_service.py:201) and swallows exceptions (:217). The `blocked_users`
write is authoritative; the `comm_v2_blocks` write is best-effort. That is the right
trade — a messaging-schema problem must not fail a safety action — but it means the second
table can lag the first without anything raising. Idempotent by reading before
writing. `unblock_user` is **terminal rather than strict**: unblocking someone who was
never blocked succeeds rather than refusing, because the caller's intent — "this person is
not blocked" — is satisfied either way. The two are each other's declared undo. The
safety notification is best-effort behind a lazy import inside `try/except`, so a
notification outage cannot fail a safety action.

### BIO API

Self-only by construction: `update_profile_bio` **takes no target parameter at all**, so
there is no argument through which another account could be named. A bio cannot be
cleared — whitespace is refused at the boundary — and no undo is declared, because the
prior text is not recoverable from the receipt.

### REEL DELETE API

Owner-only soft delete. No restore verb exists, so no undo is declared rather than a
broken reversal button being offered. A reel belonging to someone else and a reel that
does not exist refuse **identically** (`not_found`), so a refusal cannot be used to
enumerate ids.

### REEL COMMENT APIs

The permission asymmetry is deliberate and tested: **edit is author-only**
(pulse_feed_engine.py:1996); **delete is author or post owner** (:2072). The two delete
paths are distinguished in the receipt by `moderated_by_owner`, so an author's withdrawal
and an owner's moderation are not the same event in the audit trail. Comment creation is
not idempotent — two identical comments are two comments — and the private-Reel refusal
happens before `add_comment` is reached.

Two things adversarial review surfaced here that an earlier draft of this report did not
say, both recorded as findings below rather than fixed under a completion mission:
`update_comment` refuses a **foreign** comment with `forbidden` (:1995) and a **missing**
one with `not_found` (:1997), which is exactly the enumeration oracle the reel path
deliberately closed; and `delete_comment`'s `UPDATE` (:2103) carries **no actor or owner
predicate** — `WHERE id=? AND deleted_at IS NULL` — unlike `update_comment` (:2005) and
`delete_owned_reel` (:1910), whose docstring specifically claims the WHERE clause is what
makes the pre-read race harmless. Authorization for comment deletion currently rests on
the pre-read alone.

### REPORT API

The one capability that deliberately reaches content the caller does **not** own; that is
what reporting is. Duplicate reports update the existing row rather than opening a second
case. No withdrawal verb exists, so no undo is declared. `reporting.submit` remains in the
knowledge map as the older of two paths, now marked superseded for agent use.

### MARKETPLACE CREATE / UPDATE / PAUSE / RESUME / DELETE

Wiring only, per Fork 2. `create_product` enforces `TITLE_MAX=160`. The service's
`update_product` **cannot set status** (allowlist at marketplace/service.py:348, with an
`unknown_field` raise at :352) — status changes go through the state machine or not at all.

An earlier draft of this report said `update_product` omits currency. It does not:
`"currency"` is in that same allowlist at :348 and is lowercased at :366. The omission is
one layer up, in `_MKT_UPDATABLE_FIELDS` at undx_capability_registry.py:1968, which is the
enum the capability's `field` argument is constrained to. So **UNDX cannot change a
listing's currency, but the service and its HTTP callers can.** That is a deliberate
narrowing of the agent's surface, not a property of the domain.

Pause and resume are **transitions, not a toggle**: resume re-runs
the activation gate, so a listing that has become ineligible while paused does not
silently return to sale. `marketplace.listing.delete` maps to the existing `archive`
transition and declares **no undo**, because `restore` returns a listing to `draft` rather
than to its prior state — offering that as a reversal would be a lie.

All five are gated by `BUSINESS_OS_MARKETPLACE`, not by an UNDX flag; unset, every verb
returns `disabled`.

## UNDX CAPABILITIES ADDED

Thirteen, each verified present in all five files that must agree — registry spec,
`undx_agent_tools.EXECUTORS`, `undx_verification.VERIFIERS`,
`undx_policy.PRODUCTION_TOOL_REGISTRY`, and `undx_knowledge_map`. None of those files
imports the others, so this was checked by loading all five and intersecting; the result
was 13/13 on every axis with zero failures.

## AUTHORIZATION TESTS / OWNERSHIP TESTS / IDEMPOTENCY TESTS / AUDIT TESTS

`tests/undx_agent/test_service_api_completion.py` — **95 passed, 137 subtests passed.**
Ten classes: `CompletionWiringContract` and `CompletionVocabulariesAgree` (the five-file
contract and naming agreement), then `BlockIsOneCanonicalOperation`, `BioIsSelfOnly`,
`ReelDeletionIsOwnerOnly`, `ReelCommentAuthority`, `ReportingIsScopedToTheReporter`,
`MarketplaceListingAuthority`, and `VerifiersGoAndLook`.

The fixtures use a real SQLite database through `DATABASE_URL` and real owner-scoped SQL —
no mocks. Every assertion reads state back from the service or from the table, **never
from the gateway receipt**, because a receipt is the thing under test and cannot be its
own witness. `VerifiersGoAndLook` exists to prove exactly that: each verifier is driven
against a database mutated behind its back, so a verifier that returned the receipt's
claim instead of the row's value fails.

Idempotency is asserted per the mission's three cases: block already-blocked converges,
pause already-paused produces no duplicate state transition, delete already-deleted
returns the same terminal result.

**The audit claim needs qualifying, and an earlier draft of this report overstated it.**
`pulse_mutation_audit` does carry all eight required fields (pulse_mutation_audit.py:76-88),
and where it is called the tests assert all eight, with snapshots compared as
`json.dumps(..., sort_keys=True)`. But it is **not** the audit trail for all thirteen
capabilities. Actual coverage:

| Capabilities | Audit destination | Missing vs. the contract |
|---|---|---|
| block, unblock, reel delete, comment update, comment delete, report | `pulse_mutation_audit` | — |
| `profile.bio.update` | `profile_audit_logs` (dashboard_account_command_center.py:714) | no `correlation_id`, no `target_type` |
| 5 × `marketplace.listing.*` | `business_os_mkt_audit` (marketplace/service.py:122) | no `correlation_id` |
| `reels.comment.create` | **none** — `add_comment` writes no mutation-audit row | all eight |

So six of thirteen satisfy the mission's audit contract in full; six more are audited by a
pre-existing table that predates the contract and lacks the correlation id; **one is not
audited at all**. Reusing the existing audit tables was the correct call under *"do not
build a second authority system"* — but reuse means inheriting their gaps, and the report
should say which gaps rather than claim a uniform trail.

Separately, `pulse_mutation_audit.record` **swallows its own failures** and logs a warning
(:159). A mutation therefore cannot be rolled back by an audit failure — deliberate, since
the alternative is failing a safety action over a logging problem, but it does mean the
audit trail is not guaranteed complete and no test can make it so.

## FULL RELEVANT TEST RESULT

```
tests/undx_agent/  →  16 failed, 1026 passed, 3785 subtests passed
tests/undx_agent/test_service_api_completion.py  →  95 passed, 137 subtests
tests/undx_agent/test_knowledge_map.py           →  37 passed
scripts/realtime_audio_change_gate.py --base origin/main --head HEAD  →  exit 0
  "No protected real-time audio path changed (9 files inspected)."
```

All 16 failures are pre-existing and none is in a file this mission wrote. They are:

- **12 subfailures** in `test_knowledge_map_grounding.py` — `bot.py:N` citations whose
  line numbers have drifted. Stale references, not wrong claims.
- **4 tests** in `test_saved_post_write_pack.py` (3) and
  `test_content_graph_intelligence_pack.py` (1) — both fixtures declare
  `pulse_saved_collections` **without** the `description` column that
  `saved_content_service._ensure_default_collection` inserts, so the insert raises
  `OperationalError`. Neither file is modified by this mission, and the real
  `coinpilotx.db` *does* have the column — this is fixture drift, not a service defect.
  Note this corrects an earlier triage that called these "stale local db" failures; the
  local db is fine, the fixtures are not.

## REGRESSIONS

**One was introduced by this mission, found, and fixed.**

`test_question_framed_writes.py::test_the_two_refusals_partition_rather_than_compete`
began failing after the knowledge-map records were added. It was not a logic error — it
was a threshold crossing, and the mechanism is worth recording because nothing about it is
visible from either file involved.

`undx_brain.attention._build_index` builds its routing index from capability descriptions
and intents, then drops any term appearing in more than `len(RECORDS) * _COMMON_TERM_SHARE`
records, on the principle that a word in a quarter of the map names no subject. Before this
pack, `"user"` sat at **32 postings against a ceiling of 38**. Nine of the thirteen new
descriptions contained the phrase "the authenticated user" — house style, followed by 22
existing records — which put it at **42 against 41**. The term was dropped outright.

The measured cost was not the test. Driving a corpus of realistic sentences through
`attend` under both versions showed `"who is user 99"` routing to **nothing at all**, and
`"mute user 7"` losing every executable capability, because both routed on that word and
had nothing else to match.

The fix was to stop naming the actor in six descriptions where the actor is not the
subject — every write in this map is performed by the authenticated user, so saying so
discriminates nothing. `"user"` is kept in the two capabilities where a user genuinely *is*
the subject (`profile.block`, `profile.unblock`, whose intents are "block user" /
"unblock user"). That returns the term to **37 against 41**, with four postings of
headroom. Re-measured against the same corpus: every regression gone and every routing gain
retained — `block user 42` → `profile.block`, `report this user` → `feed.report`,
`delete my reel` → `reels.delete`, `update my bio` → `profile.bio.update`, none of which
routed anywhere before.

A comment at `undx_capability_registry.py:1769` records this, because the six strings look
like a style inconsistency somebody will helpfully correct, and doing so will silently
re-break routing with no test in that file noticing.

**No other regression.** The audio gate is clean and no protected path was touched.

## SECURITY FINDINGS

1. **`blocked_users` schema shadowing (production risk, not patched).**
   `_ensure_blocked_users` is a `CREATE TABLE IF NOT EXISTS`, which is silent when the
   table already exists in a **narrower** shape. `_read_state` selects `reason` and
   `created_at`. A deployment whose `blocked_users` predates those columns will raise
   `OperationalError` on every block — the exact error the test fixture first produced.
   Recorded rather than patched from a test, because the fix belongs in a migration.

2. **`reels.comment.*` reach further than they are named (naming gap, not an authority
   gap).** `update_comment` and `delete_comment` are keyed on `comment_id` alone and will
   reach **any** `pulse_comments` row, including comments on ordinary feed posts. The
   executors add no reel scoping. Authority still holds — author-only for edit,
   author-or-post-owner for delete — so this cannot be used to touch someone else's
   comment. It is a naming and UX gap: the capability id promises a narrower blast radius
   than the function has.

3. **Two catalogues share one word.** `marketplace.listing.*` acts on
   `business_os_mkt_products` (string `mktp_` ids), which is a *different table* from the
   consumer marketplace read by `marketplace.search` (`marketplace_listings`, integer
   ids). The key spaces cannot collide so no write can land on the wrong row, but the
   shared word "marketplace" hides two products. Carried as a shared constant on all five
   records so it is stated once and read five times.

4. **No executor accepts an actor identity from model-supplied arguments.** Checked
   mechanically across all thirteen: no `arguments.get("user_id")`, `actor`, `requester`,
   `owner`, `as_user` or `on_behalf`. Independently re-checked for the subtler failures a
   regex would miss — a target id landing in a requester parameter position, a positional
   argument swap, requester and target aliased to one variable — and all thirteen pass the
   executor's own `user_id` first and argument-derived ids second, each matching its
   service signature. Reinforced structurally by `undx_agent_contracts.py:430-432`, which
   **drops** undeclared arguments rather than forwarding them.

5. **`delete_comment`'s UPDATE has no actor predicate** (pulse_feed_engine.py:2103), unlike
   every sibling mutation. Authorization holds today because the pre-read and the write are
   in one transaction, but the defence is weaker than the one `delete_owned_reel`'s
   docstring describes for itself, and the asymmetry is invisible unless the three are read
   side by side. Worth a one-line fix in a mission that is allowed to change it.

6. **`update_comment` leaks existence.** `forbidden` for a foreign comment
   (pulse_feed_engine.py:1995) versus `not_found` for a missing one (:1997) lets a caller
   distinguish "this comment exists and is not yours" from "no such comment" — the exact
   oracle `delete_owned_reel` deliberately closed by refusing both identically. Pre-existing
   behaviour, inherited by the capability, not introduced by it.

7. **Six stale knowledge-map records corrected.** `social.block.set`, `social.block.read`,
   `profile.self.update`, `comments.delete`, `reporting.submit` and `reporting.status.read`
   all asserted the absence of services that now exist. None had a test catching it,
   because map ids differ from registry ids and nothing cross-checks the two vocabularies.
   `CompletionVocabulariesAgree` now does, for the thirteen.

## MISSING or BLOCKED

- **Production QA remains blocked**, unchanged from report 05 and for the same reason:
  the sandbox cannot complete a git write. Running
  `scripts/commit_action_surface_expansion.sh` locally and pushing is the whole gap.
  Railway deploys from the pushed branch, so nothing here is live.
- **No bio-only route exists** to migrate (see ROUTES MIGRATED). An earlier draft also
  claimed no unblock route existed; it does, in the Settings blueprint, and it is migrated.
- The 12 drifted `bot.py` citations and the 4 fixture-drift failures are pre-existing and
  out of this mission's scope; both are cheap fixes for whoever takes them.

## Commit

```
feat(pulsesoc): complete shared mutation service APIs for UNDX
```
