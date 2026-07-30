# UNDX Phase 3B — Schema Grounding Audit

**Date:** 2026-07-29
**Branch:** `release/undx-nexus-core-v4`
**Scope:** `services/undx_personal_intelligence_service.py`, `services/undx_agent_tools.py`,
`services/undx_tool_gateway.py`, `services/undx_agent_contracts.py`, `services/undx_agent_runtime.py`,
`services/undx_knowledge_map.py`, `tests/undx_agent/test_personal_intelligence_pack.py`,
`tests/undx_agent/test_knowledge_map.py`, `tests/undx_agent/test_knowledge_map_grounding.py`
**Suite:** 255 tests in `tests/undx_agent/`, 0 failures (was 237 before this audit)

## Summary

The personal intelligence layer shipped with **ten fabricated schema references** across
seven of its capabilities. Every one of them raised `sqlite3.OperationalError` on any real
database. None of them failed a test, and none of them surfaced an error to a user.

They were invisible because two mechanisms hid them from opposite directions. `_read()`
caught every exception and returned `[]`, so a broken query was indistinguishable from a
quiet day. And the unit fixture had been written to match the queries rather than to match
production, so the fabricated names agreed with themselves and the tests passed.

The result was the most dangerous failure available to this layer: `activity.daily_summary`
answering *"nothing happened today"* with `confidence: 1.0`, from a query that never ran.
That is not a missing answer. It is a wrong answer delivered with full authority, and the
stated purpose of Phase 3B is that every fact be sourced.

## The ten defects

Each was confirmed against the `CREATE TABLE` statement that actually builds the object.

### Fabricated tables

| Read as | Actually | Declared at | Used by |
|---|---|---|---|
| `pulse_conversation_members` | `pulse_conversation_participants` | bot.py:38775 | `activity.daily_summary`, `search.messages` |
| `marketplace_orders` | `business_os_mkt_orders` | services/business_os/marketplace/schema.py:127 | `marketplace.order.status` |
| `pulse_course_progress` | `education_progress` | bot.py:102970 | `learning.progress` |
| `business_os_ad_metrics` | *does not exist* — telemetry is event-sourced | advertising/schema.py:384, :421, :538 | `ads.performance.summary` |

### Fabricated columns on real tables

| Read as | Actually | Declared at | Used by |
|---|---|---|---|
| `pulse_messages.sender_id` | `sender_user_id` | bot.py:38807 | `activity.daily_summary`, `search.messages` |
| `business_os_ad_campaigns.id` | `campaign_id` | advertising/schema.py:102 | `ads.performance.summary` |
| `business_os_ad_campaigns.owner_user_id` | `advertiser_user_id` | advertising/schema.py:102 | `ads.performance.summary` |
| `security_events.severity` | no such column; table has `status`, `ip_address` | bot.py:105459 | `security.activity.summary` |
| `marketplace_listings.price_amount`, `.currency` | `price_label` (free text) | bot.py:99994 | `marketplace.search`, `marketplace.listing.summary` |
| `pulse_live_sessions.host_user_id`, `.description`, `.peak_viewers`, `.total_views`, `.reaction_count`, `.duration_seconds` | `user_id`, `category`, `audience`, `viewer_count` | bot.py:100891 | `live.search`, `live.summary`, `live.performance` |

`marketplace_orders` and `pulse_course_progress` were declared in exactly one place in the
repository: the test fixture that validated the queries reading them.

## Repairs

Queries were rewritten against the real schema rather than deleted, because in every case
the capability the query serves is genuinely supported — the names were simply wrong. Three
repairs changed what a capability reports, and those changes are the substantive ones:

**`live.performance` now returns fewer metrics.** Of `peak_viewers`, `total_views`,
`reaction_count` and `duration_seconds`, the table holds none. It holds a running
`viewer_count` and two timestamps. A performance summary is precisely where an invented
number would be believed and acted on, so the capability now returns the smaller true set
and names it in `metrics_available`.

**`ads.performance.summary` no longer derives spend from impressions.** Impression and
click counts now come from the event tables, windowed on `event_at`. Spend comes from
`business_os_ad_spend_accumulator`, which holds money the platform actually recognised.
That accumulator is lifetime-to-date rather than windowed, so the payload carries a
`spend_basis` note saying so — a recomputed figure would have disagreed with the
advertiser's invoice.

**`security.activity.summary` no longer labels events by severity.** The table does not
grade events. Ranking a user's own security events by an invented risk level is the kind of
fabrication that changes behaviour.

Two smaller correctness fixes came out of the same reading. `activity.daily_summary` now
excludes soft-deleted posts and statuses (both tables carry `deleted_at`), since a row the
user already deleted is not something that happened today. And `business_os_mkt_orders`
stores its party columns as `TEXT`, so the order lookup stringifies its parameters — binding
an integer against a `TEXT` column matches nothing in SQLite, which would have read as "no
such order" and been indistinguishable from a genuine miss.

Authorization was preserved or tightened throughout. Every repaired query keeps its owner
constraint inside the SQL rather than comparing after the fetch, so a stranger's resource id
returns no row instead of revealing that the resource exists. `live.search` and
`live.summary` gained an `audience='public' OR user_id=?` clause that the originals lacked.

## Structural changes

**`_read()` no longer swallows failures silently.** It logs the exception and records the
read's name in a `contextvars`-scoped degradation set. This is what found four of the ten
defects: the first test run after the change printed `no such table: pulse_conversation_members`
from `search.messages`, a second instance of the bug I had just fixed elsewhere.

**`activity.daily_summary` now returns `complete` and `degraded_sources`,** and drops
`confidence` to 0.5 when a source is missing. A narrator must consult `complete` before
saying nothing happened; with a source down, the honest answer is "nothing I could see".

**Three new tests read production DDL rather than a fixture.** `SchemaGroundingTests` parses
every `CREATE TABLE` in `bot.py`, `services/` and `pulse_communications_v2/` — plus columns
added later by `add_columns_if_missing` migrations — then checks every table and column the
intelligence layer reads against it. A fourth test asserts that a failed read produces
`complete: False` rather than an empty day.

This is the part worth keeping. The pre-existing tests could not have caught any of these
defects, because a test that builds its own schema can only prove a query is self-consistent.
Only a check against the schema the application actually creates can prove a query is true.

## The eleventh defect: the audit trail agreed

Closing the loop above exposed one more, and it is the worst of the set because it was the
record rather than the answer. `undx_tool_gateway._status_for` read:

```python
if not spec.is_write:
    return AgentOutcome.VERIFIED_SUCCESS
```

A read reached `verified_success` by not raising. Since `_read()` catches everything and
returns `[]`, a query that failed was filed in `pulse_ai_tool_operations` as **verified**,
against an empty result, with the sentence *"Here is what I found."* The fourteen audit rows
in this database are all `status: verified`, and eleven of them predate the repairs — so the
ledger we would consult to ask *"was UNDX right that day"* records full confidence for calls
whose underlying queries were raising `no such table` at the time.

Three connected repairs:

**Degradation now reaches the gateway from every capability, not one.** Only
`activity.daily_summary` opened a `_collecting()` block, so the other twenty-six personal
capabilities logged their failures and told no one. `_personal_read` in
`services/undx_agent_tools.py` now wraps every call, and `ToolResult` carries a
`degraded_sources` field.

**`collecting()` is reentrant.** The naive version would have broken the capability it was
meant to protect: `activity_daily_summary` opens its own block, and an inner collector that
installed a fresh set would discard its failures on exit and hand the outer caller a clean
run. Nesting now shares the outermost set. This is asserted directly, because the failure
mode it prevents is silent by construction.

**A degraded read is `accepted_unverified`, and says so.** The prose branch for a non-write
`accepted_unverified` did not previously exist — it fell through to write-flavoured wording
about PulseSoc accepting a change. It now reports that part of the data could not be reached
and that the answer is incomplete. The audit row follows: `canonical_verified` for a read was
hardcoded `None`, and is now `not result.degraded_sources`, so a degraded read lands as
`failed_verification` rather than `verified`.

Verified against the live database rather than a fixture. `ads.performance.summary` — the one
capability with a genuine environmental gap — now returns
`degraded=['business_os_ad_campaigns'] -> accepted_unverified`. `activity.daily_summary`
(5 records), `security.activity.summary` (50), `live.search` (20), `marketplace.search`,
`search.messages` and `learning.progress` all return `degraded=[] -> verified_success`,
which confirms the check downgrades only what actually broke. `search.messages` returning
zero rows with no degradation is the sharpest of these: it is the repaired
`pulse_conversation_participants` / `sender_user_id` query genuinely finding nothing, where
before the repair it raised on every call and reported the same empty answer.

## What this audit did not verify

Following the standing rule that nothing is called verified without executable evidence:

- ~~**No repaired query has been executed against a populated production database.**~~
  **Closed 2026-07-29 21:22.** All eight repaired capabilities were executed against the live
  `coinpilotx.db` as user `10910211866`. Seven returned without error:
  `activity.daily_summary` (5 items, `complete: true`, `degraded_sources: []`),
  `security.activity.summary` (50), `marketplace.search` (20), `live.search` (20),
  `notifications.inbox` (1), `search.messages` (0 rows, no error), `learning.progress`
  (0 rows, no error). The `complete: true` is the load-bearing result: it means every read
  feeding that summary executed, including the `pulse_conversation_participants` /
  `sender_user_id` query that raised on every call before the repair.

  The eighth, `ads.performance.summary`, returned zero campaigns and logged
  `undx_personal_intelligence_read_failed source=business_os_ad_campaigns` —
  `no such table: business_os_ad_campaigns`. That is an environmental gap rather than a query
  defect: the advertising schema has never been migrated into this database. It is recorded
  here because it is the new degradation machinery doing precisely its job on real data. Before
  this audit the identical condition produced a confident empty answer and no log line at all.
- ~~**The provenance of the Phase 3B evidence screenshots is unestablished.**~~
  **Closed 2026-07-29.** The screenshots are genuine output of the governed runtime, and the
  audit trail proves it: `pulse_ai_tool_operations` holds fourteen rows for user
  `10910211866` timestamped 02:29–02:53 UTC, matching the screenshot mtimes, covering
  `activity.daily_summary`, `notifications.group_summary`, `search.global`,
  `settings.inspect`, `security.sessions.list`, `premium.status`, `marketplace.search`,
  `learning.search` and `memory.activity.inspect`. `"Here is what I found."` exists at
  exactly one place in the repository and is reachable only through
  `undx_tool_gateway.execute`, which is downstream of the cohort gate — so the gate was open
  when they were taken.

  What it was open *by* is the finding. The variables were never in `.env.local`; they were
  exported in the shell that launched the server. When that process died and was restarted,
  the cohort silently emptied, `available()` began returning `False`, and every message fell
  through to the raw model — which is why UNDX later offered to "perform a web search",
  the exact fabrication this phase exists to prevent. A rollout gate whose only home is a
  shell's exported environment is a gate that switches itself off on restart with no log
  line and no error, so the three variables are now declared in `.env.local`. Writes remain
  deliberately absent.

  The screenshots are nonetheless **superseded**, because 02:29–02:53 predates the repairs.
  They depict the governed runtime running the fabricated queries, with the audit trail
  calling the result verified. The replacement evidence is the live run recorded below.
- ~~**The live simulator run is blocked on model configuration, not on this code.**~~
  **Closed 2026-07-30 05:22.** The earlier 503 was upstream of the intelligence layer:
  `pulse_ai_provider_router` has zero configured providers across openai, claude, gemini,
  deepseek and groq, and fails closed after emitting `UNDX_FINAL_MODEL_REQUEST`. That path is
  only reached when a turn matches **no** registered capability. Phrasing a turn as a
  registered intent routes it through the governed runtime instead, which needs no provider
  key at all — so the simulator run was never blocked on model configuration for capability
  turns, and the two are now distinguishable by which one answers.

  Seven turns were run live in the iPhone 17 Pro Max simulator against the repaired build,
  each verified against `pulse_ai_tool_operations` rather than against the screen:

  | # | Turn | Capability | Status | `degraded_sources` |
  |---|---|---|---|---|
  | 26 | what happened today | `activity.daily_summary` | `verified` | — |
  | 27 | how did my ads perform | `ads.performance.summary` | `failed_verification` | `business_os_ad_campaigns` |
  | 28 | show my account activity | `security.activity.summary` | `verified` | — |
  | 29 | show my saved posts | `saved_items.list` | `verified` | — |
  | 30 | show my conversations | `conversations.list` | `verified` | — |
  | 31 | what is my verification status | `verification.status` | `verified` | — |
  | 32 | is my account healthy | `account.health.summary` | `verified` | — |

  Row 27 is the whole point of the eleventh defect's repair, observed end-to-end in the app.
  `business_os_ad_campaigns` has never been migrated into this database, and the runtime said
  so: on screen, *"Here is what I found, but I could not reach one part of your data, so treat
  this as incomplete rather than as the full picture."*; in the trail, `verified: false` and
  the missing source named. The pre-repair behaviour on the identical condition was a
  confident empty answer stamped `verified`. Six other reads, on the same build and within ten
  minutes of it, classify as `verified` with no degraded sources — so the runtime is
  discriminating between a read that ran and one that could not, rather than hedging uniformly.

  Two of the six verified reads returned **zero** rows, which is the failure mode that most
  resembles the defect, so both were checked against the database directly. The QA account
  owns none of the three rows in `pulse_saved_items` — they belong to `970301`, `-920871340`
  and `10910211826`, the last of which differs from the actor by one digit — and has zero
  rows in `comm_v2_participants`. Both zeros are true actor-scoped zeros, reached by a query
  that ran.

  The `reports/evidence/` screenshots remain superseded and this table replaces them.
  Capability turns are proven; a provider key is still required before a non-capability turn
  can be answered at all, which is a separate open item.
- **The column check is deliberately permissive.** A column need only exist on one of the
  tables a query names; resolving which table owns which name would mean writing a SQL
  parser. This catches fabrications, not mis-attributions between joined tables.
- **Capability counts are unchanged at 70 registered.** This audit repaired existing
  capabilities and registered none.

## The twelfth defect: the map described a file that no longer existed

Schema grounding says a claim about the application must be checked against the application.
The knowledge map makes claims about the application on every line — its `evidence` strings —
and none of them were checked against anything. Applying the same rule to the map itself
found three defects, all now closed.

### Citation drift

Nine of the twelve `bot.py` line citations in `services/undx_knowledge_map.py` had rotted.
`bot.py` is roughly a hundred thousand lines, so any edit above a cited line moves it
silently: nothing fails, and the map simply begins describing a file that no longer exists in
that shape. One citation named a `return jsonify(...)` in an unrelated reel handler while
claiming to be the friend-accept route. A citation that points at unrelated code is worse than
no citation, because it reads as verification and supplies none.

| Cited | Correct | Subject |
|---|---|---|
| 32232 | **33324** | `/pulse/status/<status_id>` page |
| 35189 | **36283** | `/pulse/status` page |
| 35714 | **36808** | `/api/pulse/status/rail` |
| 41458 | **42698** | `/pulse/live` page |
| 77186 | **78411** | reel save handler |
| 78548 | **79780** | friend accept handler (two occurrences) |
| 78587 | **79817** | friend decline handler |
| 81885 | **83106** | marketplace save handler |
| 78612 | **79852** | `DELETE FROM pulse_follows` in the follow toggle |

`tests/undx_agent/test_knowledge_map_grounding.py` now resolves every citation against the
real `bot.py`. The matcher is stricter than it first looks, and had to be: a version that
split a route label into words passed two citations that were off by a thousand lines each,
because `bot.py` embeds the entire web client as minified JavaScript and words like `status`
and `live` occur on thousands of unrelated lines. Requiring the whole route literal within a
seven-line window immediately exposed two of the nine above. Flask converter prefixes are
normalised on both sides, so `<path:status_id>` and `<status_id>` compare equal without
loosening the match.

### Inverted readiness precedence

`classify_readiness()` tested `UNSUPPORTED` first, inverting the two ends of the mandated
order `AUTHORIZATION DEFECT → DOMAIN SERVICE REQUIRED → TOGGLE HAZARD → VERIFIER REQUIRED →
NATIVE CONTEXT REQUIRED → UNSUPPORTED → READY TO WIRE`. A capability that was both unsupported
*and* unscoped classified as the milder of the two, so the defect left the matrix entirely.

That is not a labelling preference. "Not building this yet" gets skimmed; "a caller can reach
rows they do not own" does not — and it stays true on the day the capability ships. Correcting
the order moved **`voice_messages.send`, `moderation.queue.list` and `moderation.action.apply`**
out of `UNSUPPORTED`, taking `AUTHORIZATION DEFECT` from 4 to **7 of 152** records. All three
were hiding a real scope defect behind a label that says "ignore me".

`ReadinessPrecedenceTests` pins this, including a guard test asserting that a record which is
both unsupported and defective actually exists — otherwise the precedence test would pass
vacuously and prove nothing.

### Route validation compared patterns to paths

Import-time validation rejected two correct records. `verification.status` declares
`/pulse/verification`, which `nativeRouteActions.ts:149` navigates to `VerificationCenter`,
but `linking.ts:352` declares that screen as `pulse/verification/:track?` — and an optional
parameter is optional. Separately, `localization.preferences` and `presence.privacy.status`
name concrete paths under `AccountCenter`'s `/pulse/settings/:section` catch-all, which no
string comparison can satisfy.

`_route_matches()` now matches segment-wise, expanding optional trailing parameters, so a
concrete path satisfies the pattern that serves it. Strictness is preserved in two ways: a
required parameter must still be present on both sides, and `_LITERAL_OWNERS` asserts that a
route declared literally by one screen may not be claimed by another — so a record naming
`/pulse/settings/devices` cannot file itself under the catch-all while the user lands on
`AccountDevices`. The two records that named `Settings` for a `/pulse/settings/<section>` path
were corrected to `AccountCenter`; they would have sent the user one level above the screen
that actually shows the setting.

Suite: **255 tests, all passing.**
