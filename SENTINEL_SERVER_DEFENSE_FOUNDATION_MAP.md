# Sentinel Server Defense — Foundation Map (Stage 0)

Baseline: `d8aaf911` on `main`. Worktree branch `feat/sentinel-server-defense-mesh`.
Every claim below was read out of the repository or out of the live Railway
project; nothing here is inferred from naming.

> **Corrections applied during Stage 2.** Two claims in the first draft were
> wrong, both from reading the package's documentation rather than its code:
> the storage entry point is `store.ensure_schema()`, not `store.init_db()`
> (no function of that name exists), and there are **22** tables, not 17. Both
> surfaced the moment Stage 2 tried to *call* the thing this map described,
> which is the argument for wiring early rather than mapping exhaustively first.

## 1. The headline finding

**Sentinel is built and almost entirely unwired.**

`grep -c sentinel bot.py` returns **0**. The 55-module, ~11k-LOC `services/sentinel/`
package has:

* no blueprint registered on the Flask app (`api.sentinel_bp` exists, nothing mounts it),
* no `store.ensure_schema()` call in the boot path, so **none of its 22 tables exist in production**,
* exactly one live caller anywhere in the product: `alert_worker.py:79`
  `sentinel_runtime.run_scheduled_ingestion()`.

So the security *foundation* is real, but the live request path emits nothing into
it. This mission is therefore mostly an **activation and bridging** job, not a
green-field build. The temptation — and the way this mission goes wrong — is to
write a second security stack next to the dormant one.

## 2. Authorities that already exist (REUSE, DO NOT DUPLICATE)

| Concern | Authority | Location |
|---|---|---|
| Security event schema | `Event`, `EVENT_VERSION = "1"`, 15 categories | `services/sentinel/events.py` |
| Event ingestion | `events.ingest(event, conn)` — idempotent on `dedupe_key`, redacts to CONFIDENTIAL, synchronous | `services/sentinel/events.py` |
| Policy / authority | `authority.check()`, 5 dimensions × 5 levels, fail-closed on unknown | `services/sentinel/authority.py` |
| Hard constraints | 15 append-only rules SC1–SC15 | `services/sentinel/constitution.py` |
| Action broker | `runbooks.register()/execute()`, 3 cascading env gates, `EXECUTED_UNVERIFIED` → independent verifier → `COMPLETED` | `services/sentinel/runbooks.py` |
| Incidents | `open_incident()`, 11-state machine, dedupe by `incident_key` | `services/sentinel/incidents.py` |
| Correlation | CR1–CR5, each requires ≥2 events AND ≥2 distinct types (SC8) | `services/sentinel/correlation.py` |
| Storage | 22 `sentinel_*` tables + 29 indexes (51 statements), `store.ensure_schema()` | `services/sentinel/store.py` |
| Evidence | append-only hash chain | `services/sentinel/evidence.py` |
| Health | freshness-decaying `HealthSnapshot`; UNKNOWN/STALE/CONFIGURED are **not** healthy | `services/sentinel/health.py` |
| Kill switches | emergency > master > domain > per-runbook; **plus `GATES`, the registry every gate must appear in** (Stage 21) | `services/sentinel/killswitches.py` |
| Prompt-injection primitives | `scan_for_injection()`, `wrap_untrusted()` | `services/sentinel/ai_security.py` |
| UNDX tool authorization | `undx_tool_gateway.execute()` 9-step chokepoint; `undx_agent_policy.Decision` | `services/undx_tool_gateway.py`, `services/undx_agent_policy.py` |
| Member login throttle | `login_security_preflight()` / `register_failed_login()` — **DB-backed**, per ip/email/domain | `bot.py:5360`, `bot.py:5491` |
| Admin login throttle | `admin_gateway.login_rate_limited(conn, …)` — **DB-backed**, counts `admin_audit_logs` | `services/admin_gateway.py:138` |
| Session authority | `mobile_security_sessions`, family rotation, reuse-detection grace | `bot.py:30424–30750` |
| Session revocation | `revoke_mobile_refresh_token()`, `revoke_all_mobile_security_sessions()` | `bot.py:30710`, `bot.py:30733` |
| Audit | `log_security_event()` → `security_events`; `log_admin_audit()` → `admin_audit_logs`; `user_security_events` | `bot.py:28797`, `bot.py:14414` |
| Private Office second lock | `_office_lock_gate()` → `validate_grant()`; locked ⇒ **423** | `services/private_office_routes.py:304`, `services/private_office/security.py:485` |

**Consequence:** Stages 2, 17, 18, 19 and 20 of the brief are largely *already
implemented*. The correct move is to extend them, not to re-found them.

## 3. Genuine gaps (what is actually missing)

1. **No request-path bridge into Sentinel.** Nothing in `bot.py` emits a
   `sentinel.Event`. Detection today is limited to whatever `alert_worker`
   scrapes on a schedule.
2. **No Sentinel schema in production.** `store.ensure_schema()` is never called,
   so ingestion would fail against a live database. *(Closed in Stage 2.)*

   Note the function's shape: handed a connection it does **not** commit, and on
   PostgreSQL the uncommitted DDL still holds catalog locks, so the next
   connection attempting the same creation blocks on it. Call it with its own
   connection, or commit for it.
3. **Distributed rate limiting does not exist.** See §4 — this is the most
   consequential gap.
4. **`wrap_untrusted()` is never applied at context assembly.** The primitive
   exists in `ai_security.py`; `undx_policy.compile_context()` does not call it.
   Untrusted document/message/listing text reaches external LLM providers
   without boundary markers. *(Both sentences are wrong in ways that matter —
   see the correction below. The underlying worry was real and the defect found
   was worse than the one described.)*
5. **No Sentry.** No `SENTRY_DSN` among the 228 production variables and no
   `sentry_sdk` import. The brief's "reuse existing Sentry integration" has no
   referent — this must not be papered over.
6. **No automated cross-tenant authorization invariants.** Owner scoping is
   ad-hoc per route (`WHERE user_id=?`), with no test proving user A cannot
   read user B. *(Closed in Stage 5/29 — but see the correction below, which
   changes what this gap actually was.)*

### Found during Stage 21: the emergency switch did not reach everything

`killswitches.py` opens by promising that `SENTINEL_EMERGENCY_KILL_SWITCH` turns
"everything off, no exceptions". It did not, and the gap was in code this mission
wrote.

Established by running the process rather than by reading it — every enabling
variable set, then the emergency switch on, then ask each gate what it returns:

| Gate | Honoured emergency? | Added by |
|---|---|---|
| `killswitches.ingest_enabled` / `automation_enabled` | yes | pre-existing |
| `external_providers.master_enabled` | yes | pre-existing (Stage 45) |
| `rate_limit.mode` / `enabled` | yes | Stage 6 (this mission) |
| `request_bridge.bridge_enabled` | **no** | Stage 3 (this mission) |
| `bootstrap.bootstrap_enabled` | **no** | Stage 2 (this mission) |

Every gate that predates this mission honoured the switch. Both that ignored it
were added by it, which is the useful part of the finding: the convention was
sound and unenforced, so it decayed exactly where new code touched it.

**Severity, stated honestly, because the two are not equal.**

`bridge_enabled` is the *less* serious one despite being on the request path.
`emit()` checks `killswitches.ingest_enabled()` one line after it checks
`bridge_enabled()`, and `events.ingest()` checks it a third time, so under an
emergency stop nothing was ever buffered or written. The consequence was not a
control gap but a reporting lie: `stats()["enabled"]` is `bridge_enabled()`, so
the health surface reported the bridge as **enabled**, with
`evidence_complete: true` beside it, while it was recording nothing. An operator
reading that during an incident would conclude Sentinel was watching. That is
Hard Rule #4 broken in the direction that actually costs something — a stopped
control reported as running is worse than one reported as unknown, because it is
believed.

`bootstrap_enabled` is the substantive one. It gates DDL at boot and genuinely
still ran. The docstring's defence — creating empty tables is inert — holds in
the ordinary case but not in the case you would use the switch for: this
repository has a documented failure mode where schema creation on a connection
that never commits leaves a catalog lock and the next connection blocks on it.
"Sentinel's DDL is hanging boot" is a plausible reason to hit the emergency
switch, and it was one of the few things the switch could not stop. Nothing is
stranded by refusing, since bootstrap runs on every boot.

**Why nothing noticed, which is the finding that mattered.** The test named
`test_emergency_kills_everything` called the three functions defined in
`killswitches.py` and stopped. It was structurally incapable of failing for a
gate defined in another module. A test whose name claims a global property and
whose body checks a local one is the same vacuity this mission has been
correcting elsewhere, and here it had already cost something.

So the fix is not the two one-line guards. It is `killswitches.GATES`: a registry
of every gate with the module, function, kind and documented default for
each, plus `all_gates()`, `gate_value()` and `enforcement_gates()`. Three
invariants are now enforced by `tests/sentinel/test_gate_registry.py`:

1. every registered gate returns False under the emergency switch — with a
   partner that turns them all on first, since "everything is off" would
   otherwise pass for the boring reason that most of them default off;
2. no gate classified as enforcement may default on — deploying the code must not
   be the decision that starts changing production behaviour for a client that
   cannot be updated (Hard Rule #3). The enforcement gates are
   `distributed_limits` and `receipt_participation`, and both default off;
3. an AST scan finds every function in `services/sentinel` that reads a
   `SENTINEL_*_ENABLED`/`_MODE` switch and fails if one is neither registered nor
   in a documented exemption list — so the next gate cannot repeat this.

The exemptions are six, each with a reason that was checked rather than assumed:
`domain_automation_enabled`, `runbook_enabled` and `provider_enabled` are
parameterised sub-gates that return False unless a registered parent does;
`enrichment_policy.evaluate` and `external_providers.ensure_registered` call the
registered gates rather than deciding anything; `rate_limit.mode` returns a mode
string and is registered through its boolean wrapper `rate_limit.enabled`.

**Detection vs enforcement.** The brief asks for this separation. It already
exists and did not need inventing: `rate_limit` has `off` / `shadow` / `enforce`
with separate `shadow_limited` and `enforced_blocks` counters. Stage 21 adopts
that vocabulary as the `kind` field on `Gate` rather than introducing a second
one, per Hard Rule #6.

**Caught by mutation, not by review:** `gate_value()` on an unknown name returned
False, but nothing tested it, and flipping the fallback to `return True` passed
the entire suite. An unknown gate name is an unanswerable authorization question
and the answer is no (Hard Rule #5); a renamed or deleted gate would otherwise
have read as permanently open. Now tested, with a partner proving a real name
still says yes.

### Correction applied during Stage 10: gap 4 named the wrong function twice

Acting on gap 4 as written would have made the codebase worse. Two errors:

**`compile_context()` carries no untrusted text.** It takes the user's message,
but only to *route* on it — domain detection, risk mode, tool selection, freshness
terms. The `system_context` it returns is compiled from the policy pack. There is
nothing in its output to wrap, and wrapping it would fence the platform's own
policy inside an untrusted marker.

**`wrap_untrusted()` is the duplicate, not the missing piece.** The platform's
untrusted-content boundary is `services/undx_brain/envelope.py`, and it is already
applied at three live call sites (`pulse_ai_web_search.context_block`,
`pulse_ai_knowledge.build_system_prompt` for memory, `undx_brain.corpus.prompt_block`).
It does strictly more than `wrap_untrusted`: five reserved tags rather than two
markers, whitespace-tolerant matching so `< / system >` cannot slip through, a
`Provenance` vocabulary recording which source may instruct, a declaration before
the payload and a reassertion after it, and reported rather than silent truncation.
Wiring `wrap_untrusted` into prompt assembly would have created a second fence
vocabulary — and a payload that can forge one fence escapes whichever envelope it
is nested in, which is exactly why `envelope.RESERVED_TAGS` neutralises the *other*
fence this repo renders. A pointer has been added to `ai_security.wrap_untrusted`
saying so, because the next reader will be told to do what this brief told me to do.

**What was actually broken: the seam between two correct clamps.**
`context_block` clamps its payload to 4000 characters *before* sealing, precisely
so truncation can never remove the closing fence, and its docstring says so.
`build_system_prompt` clamped every knowledge body to 700 characters, because the
prompt has a budget. Composed — and they are composed, on the live path — the second
clamp truncated the first's rendered envelope:

| sealed web payload | closing fence in system prompt |
| --- | --- |
| ≤ ~180 chars | intact |
| > ~180 chars | **gone**, along with the reassertion |

The declaration and opening fence fit inside 700 characters; the close and the
reassertion did not. Real search blocks run to 4000, so in practice this was
always broken — an opening fence with no close, for the single most
attacker-controllable input in the system, in the message carrying the most
authority in the request. Verified by execution, not by reading.

Three things made it invisible. Each function is correct alone, so neither
module's tests could see it. `test_the_clamp_is_applied_before_sealing_and_not_after`
checks a huge payload but never puts it through `build_system_prompt`. And
`test_the_three_hop_path_the_foundation_entry_describes_is_real` does cover the
whole path and asserts exactly the right thing — `count(CLOSE_FENCE) == 1` — on a
fixture whose payload is 144 characters. Adding one more search result to it takes
that assertion from 1 to 0.

**Fixed** by exempting an already-sealed body from the second clamp and rendering
it as its own section rather than as a bullet under "Approved PulseSoc knowledge"
— a heading that contradicted the envelope's own declaration and is the wrong word
for a stranger's web page. The exemption is claimed by the producer
(`envelope_sealed` on the knowledge item, set from the flag by `pulse_ai_service`),
not inferred from the text: a first draft keyed on `envelope.is_sealed(body)` alone,
which is a hole, because `is_sealed` asks about shape and any retrieved document can
contain both fence tokens in the right order. Content cannot authenticate itself.

**Blast radius: none today.** `UNDX_BRAIN_ENVELOPE_ENABLED` defaults off, so nothing
is sealed in production and the fix cannot reach a live prompt. A differential test
against the pre-fix function over 144 input combinations found 0 differences for
unsealed bodies and a difference only for the sealed case. What this stage bought is
that the flag is now safe to turn on — before it, turning it on produced an
unterminated fence and also dropped the legacy "use carefully" preamble, i.e. was
strictly worse than leaving it off.

**Still unsealed, deliberately not changed here:** the rest of the `knowledge` list
reaches the system prompt with no envelope — semantic retrieval, platform knowledge,
market grounding, and the `pulsesoc_search` block at `pulse_ai_service.py:1065`,
whose `preview_text` is other users' content. Sealing those changes prompt content
for every request rather than fixing a broken boundary, so it belongs with the flag
rollout. Also left alone: with the flag off, a forged fence inside an unsealed
knowledge body still arrives live. `corpus.prompt_block` neutralises unconditionally
and this path could too, but `test_off_it_returns_the_legacy_string_unchanged`
asserts the live tag as a deliberate, documented decision, and overturning someone
else's recorded decision is not a testing stage's job.

### Correction applied during Stage 5/29: the scoping is not ad-hoc

Gap 6 above was written from a distance and it overstated the problem. A traced
audit of the `/api/pulse/**`, `/api/mobile/**` and `/api/messages/**` families
found a **consistent, dual-layer idiom**, not an ad-hoc one:

| Layer | What it does | Example |
|---|---|---|
| Route | `api_account_user()` / `pulse_ads_api_user_required()` — proves *somebody* is logged in, nothing more. 255 call sites. | `bot.py:17986` |
| Object | Resolve the object from the client's id → resolve its conversation → require a `pulse_conversation_participants` row | `pulse_send_conversation_message`, `api_pulse_message_react`, `api_pulse_messages_seen`, `api_pulse_conversation_detail`, `api_pulse_conversation_messages` |
| Service | User-owned rows are scoped in the SQL itself, `WHERE id=? AND user_id=?` | `alert_engine.delete_alert`, `pulse_advertiser_portal._role_for_account` |

**No traced IDOR gap was found in those families.** That is a real result and it
changed what this mission owed the codebase. What was actually missing was not
the checks — it was any way to *notice their removal*. So Stage 5/29 built a
regression harness and a detector, and added no permission engine (§7 names one
as a duplicate risk; `sentinel/authority.py` already exists for Sentinel's own
agent authority and is not on the product request path).

Two things now exist:

* `tests/sentinel_integration/test_object_authorization.py` — 16 tests that
  drive HTTP as a member and as an outsider. Every refusal test is paired with
  a success test for the same request, because a route that 404s for
  *everybody* would otherwise satisfy the whole file.
* `INV_MESSAGE_SENDER_PARTICIPANT`, `INV_RECEIPT_READER_PARTICIPANT`,
  `INV_REACTION_AUTHOR_PARTICIPANT` in `services/sentinel/invariants.py` —
  storage-level detectors. They do not restate the route checks; they look for
  the *consequence* of a bypass (a row whose actor was never on the roster),
  which holds no matter which of the ~1,538 routes wrote it, including one that
  does not exist yet.

Seven mutants against `bot.py`'s own access checks (O1–O7) confirm the harness
can see them disappear.

### Correction: `/seen` does not honour `left_at` like its four siblings

Found by mutation O7 and pinned by
`test_the_seen_route_does_not_check_left_at_like_the_others_do`.

`api_pulse_messages_seen` (`bot.py:88404`) gates on
`WHERE conversation_id=? AND user_id=?`. The other four chat routes add
`AND COALESCE(left_at,'')=''`. So a member who has **left** a group can still
write read receipts into it, and the remaining members keep seeing "read by"
from someone who walked out.

Scope, stated without dramatising it: `/seen` returns counters, not message
bodies. The route needed to actually read the thread refuses the departed
member. This is a consistency and privacy-signal defect, not content
disclosure.

**Deliberately not fixed in Stage 5/29**, for the same reason the two
disagreeing rate limits found in Stage 6 were left alone: a stage that builds a
regression harness should not quietly change what a frozen App Store client
experiences. It is a one-line edit and belongs in **Stage 21**, behind the
detection/enforcement flags, where it can run in shadow first.

#### Resolved in Stage 21 — shipped dark, behind `SENTINEL_RECEIPT_PARTICIPATION_ENFORCED`

The gate is registered in `GATES` as an `_ENFORCE` gate defaulting **off**, so it
is revoked by the emergency switch along with everything else rather than by a
special case of its own.

Three details are load-bearing and are each pinned by a mutant:

- **`left_at` is read, not filtered.** The query selects
  `COALESCE(left_at,'') AS left_at` instead of adding the siblings'
  `AND COALESCE(left_at,'')=''` to the `WHERE`. Filtering would have been the
  smaller diff, and it would have collapsed "never joined" into "joined and
  left" — the two cases need different answers here, because a stranger is
  refused *today* while a departed member is refused only once an operator
  turns the gate on. Mutant O12 restores the collapse; it reads as a
  simplification and is a privilege escalation, since with the gate off a
  stranger would fall through to the shadow branch and write a receipt.
- **Enforcement answers 404, not 403**, matching the four siblings, so a
  conversation the caller may not touch stays indistinguishable from one that
  does not exist. This is the property mutant O6 protects, extended to the new
  branch by O11.
- **Shadow mode emits.** When the gate is off and a departed member arrives,
  the route calls `sentinel_note_shadow_refusal` and continues. Without that
  emission "shadow" and "off" are the same state, and the argument for shipping
  dark — that the blast radius gets measured from real traffic before anyone
  decides — is simply false. Mutant O13 deletes the call; nothing about the
  route's observable behaviour changes, which is exactly why it needs a mutant.

The default is asserted separately from the behaviour, because a test that only
covered the enforced path would let the default flip to on without anything
failing.

One incidental repair came with it. `test_a_refused_write_leaves_no_trace_in_the_conversation`
counts rows absolutely — the outsider must own zero messages, reactions and
receipts — which silently depended on no earlier test having written any. That
held only while the outsider was never allowed to succeed at anything, and
proving this gate is off by default requires proving a departed member still
gets a 200, which writes a receipt. The fixture is now restored per-test rather
than teaching one helper to clean up after one route.

## 4. The rate-limiting truth (production-critical)

`services/security_guard.py:11` — `BUCKETS = defaultdict(list)` — is **module-level
process memory**, and `services/pulse_security_core.py:22` `_RATE_BUCKETS` is the same.

The Procfile runs:

```
web: gunicorn bot:app --workers ${WEB_CONCURRENCY:-4} --threads ${WEB_THREADS:-8}
```

Four independent OS processes. Therefore every configured limit is effectively
**multiplied by the worker count**, and which limit an attacker hits depends on
which worker the load balancer happened to pick. `BUCKETS` is also never evicted,
so its key space grows without bound — a slow memory-exhaustion vector.

Two limiters are honest exceptions, and they are the ones that matter most:
member login and admin login are both **DB-backed** and therefore genuinely
cross-process.

### Correction applied during Stage 6: there are four, not two

The paragraph above was written from a `grep` for bucket-shaped module globals in
`services/`. It missed the limiter that actually guards the authentication routes,
because that one lives in `bot.py`:

| # | Where | State | Scope |
|---|---|---|---|
| 1 | `services/security_guard.py:11` `BUCKETS` | process memory | paths from `request_limit_for()` |
| 2 | `services/pulse_security_core.py:22` `_RATE_BUCKETS` | process memory (+ `cache_engine`, which has no Redis to reach) | `HIGH_RISK_RATE_RULES` |
| 3 | **`bot.py:449` `RATE_LIMIT_BUCKETS`, used by `basic_abuse_guard`** | **process memory, never evicted** | **11 paths incl. `/login`, `/signup`, both `recover` routes, `/admin/login`, both checkout routes** |
| 4 | `admin_gateway.login_rate_limited`, `bot.login_security_preflight` | **PostgreSQL** | admin + member login |

How it was found is the useful part: Stage 6 wired a distributed limiter into
three auth routes, and the integration tests failed with 429s the new code could
not have produced. The log line was `Rate limit triggered
path=/api/mobile/auth/recover`, emitted by `basic_abuse_guard` — a
`before_request` already limiting all three, with its own deployed policy table.
The Stage 6 code had a comment asserting one of those routes "had no rate limit
of any kind." It was false.

**Consequence for Stage 6.** Adding a `RULES` registry inside
`services/sentinel/rate_limit.py` naming the same routes with limits of its own
would have been a duplicate rate-limit policy — Hard Rule #6, and the specific
failure where two numbers disagree and nobody can say which one production
applied. The registry was deleted. `rate_limit.py` owns the counting; the limits
stay in `bot.ABUSE_GUARD_PROTECTED`, where they were already deployed, and
`basic_abuse_guard` was extended to consult the shared counter using them. That
covers all eleven paths rather than the three the first attempt picked.

### Correction: one configured limit on `/api/mobile/auth/recover` is dead

Found while writing the Stage 6 route tests. `basic_abuse_guard` states 6-per-300s
for that path; `pulse_security_core` states 5-per-600s for the same path.
`basic_abuse_guard` is registered first and so checks first, but on request 6 its
bucket holds only 5 — under its own limit — so it allows and passes the request
to the stricter guard, which refuses. Request 7, where `basic_abuse_guard` would
finally have refused, is never reached.

So one of the two numbers configured for that route has no effect, and neither
file says which. Not changed here: altering either limit changes what real users
of a frozen client experience, which is outside this stage's remit. Recorded for
Stage 21, and pinned by
`test_the_older_guards_limit_is_unreachable_on_this_route` so it cannot drift
unnoticed.

### There is no Redis

Verified against the live project (`railway variables`, 228 distinct names):
**no `REDIS_URL`**. `railway list` shows no Redis service; only `Postgres`.
`services/cache_engine.py` degrades to `None` without it.

The brief says to use Redis "if the existing production architecture establishes
it as the canonical distributed limiter." It does not. It establishes
**PostgreSQL** as the canonical shared store, and `admin_gateway.login_rate_limited`
is the in-repo precedent for a DB-backed limiter that explicitly avoids "a schema
change or parallel security store."

**Decision:** build the distributed counter on PostgreSQL, following that
precedent. Requiring a new Redis service would be a paid-provider blocker; faking
distribution with process memory is forbidden by Hard Rule #4 and would be a lie.

## 5. Client compatibility constraints (see Stage 1 doc)

The shipped App Store binary is frozen. Status codes already in use:
**401** invalid/expired token · **403** account restricted / email unconfirmed /
admin permission · **423** Private Office locked · **429** rate limited.

Login additionally returns **403** with `error: "login_challenge_required"` —
which the shipped client does not read, rendering it as "identifier mismatch".
See Stage 1 §4.1.

Any new enforcement must express itself only in these codes.

## 5a. `bot.py` boot-path hazards found while wiring Stage 2

* **`init_db` is defined twice** — `bot.py:807` and `bot.py:106154`. Python keeps
  the second, so the first is dead code, exactly like the known double
  `webhook_app = Flask(...)` at 384/1130. Anything added to the first definition
  is silently discarded. The live one delegates to `_init_db_impl()`.
* **`_init_db_impl` has no exception handler**, and `init_db()` wraps it only in
  `try/finally`. It is reached from ordinary route handlers, so anything that
  raises inside it becomes a request-time 500. Boot-time additions must catch
  their own failures.
* **`db()` opens a fresh connection on every call** (`bot.py:534`); there is no
  per-request cached connection. Any per-request Sentinel write with
  `conn=None` would therefore open a new DB connection per event — meaning
  under attack, the moment the bridge matters most, it would amplify attacker
  traffic into connection-pool exhaustion. This constrains Stage 3 to buffering.
* The production boot path is `if __name__ != "__main__":
  initialize_database_for_web_startup()` (`bot.py:119686`), which runs at import
  under gunicorn.

### Found while wiring Stage 3

* **`account_user_id()` is not a read — it can rotate a session.** It falls
  through `session.get("account_user_id")` → `account_user_id_from_mobile_access_token()`
  (opens a DB connection, `bot.py:3243`) → `restore_account_from_persistent_cookie()`,
  which calls `rotate_mobile_refresh_token()` (`bot.py:3233`). Calling it from
  an `after_request` hook would therefore make *logging a 401* both open a
  database connection and rotate the user's refresh token — under a 401 flood,
  connection amplification plus token-family churn, on the same code path
  already known for reuse-detection revocations. Any observer must read
  `session.get("account_user_id")` directly and accept device-level attribution
  for bearer-only clients.
* **A `before_request` already resolves the account for every request**
  (`pulse_security_core_guard`, noted in the docstring at `bot.py:3222`), so by
  `after_request` the session value is populated for free — the cheap read is
  also the well-populated one.
* **`webhook_app` is assigned twice, so hook registration needs proving, not
  reading.** A decorator can attach a hook to the discarded first app object
  and every unit test still passes. `tests/sentinel_integration/` asserts
  membership in `webhook_app.after_request_funcs` and that `bot.app is
  bot.webhook_app`.
* **`log_visitor` excludes `/api/` deliberately** (a SELECT+INSERT+COMMIT per
  request would tax every native call). That exclusion must *not* be copied by
  a buffered observer — the native app drives almost all traffic through
  `/api/`, so inheriting it would blind Sentinel to nearly everything. Pinned
  by `test_api_paths_are_not_excluded_the_way_the_visitor_log_excludes_them`.

## 5b. A dedupe default that silently eats the brute-force signal

`events.Event` derives `dedupe_key` from
`source|category|event_type|subject_type|subject_id|occurred_at`, and
`occurred_at` has **one-second** resolution. For the scheduled scrapers the
package was built around, that is correct idempotency. For anything observing
the request path it is not: 50 failed logins in one second share every one of
those fields, so 49 are discarded as "duplicates" — and volume is precisely
what makes a brute force detectable. The loss is silent and is named after a
virtue.

Any request-path emitter must therefore set `dedupe_key` explicitly. The
bridge uses `reqbridge:{uuid4}`; idempotency is not weakened by this, because
`flush()` pops events off the buffer before writing, so no event is ever
offered to `ingest()` twice.

## 6. Do not touch

* `mobile-native/**`, `ios/**`, Expo/EAS config — Hard Rule #1, expected diff 0.
* Agora / audio / call / livestream — Hard Rule #2, expected diff 0.
* `services/private_office/security.py` lock semantics — may be *observed*, not
  altered. The second lock is a shipped invariant.
* The 6 Procfile workers' contracts.
* Other agents' worktrees (`elegant-meitner`, `nostalgic-neumann`,
  `feat/document-intelligence`) and the 3 untracked files on `main`.

## 7. Duplicate-risk register

| Tempting to build | Already exists — use this instead |
|---|---|
| A security event table | `sentinel_events` + `events.ingest()` |
| An incident ledger | `sentinel_incidents` + `open_incident()` |
| A policy engine | `authority.check()` + `constitution` SC1–SC15 |
| An action broker | `runbooks.register()/execute()` |
| A login throttle | `login_security_preflight()` (member), `admin_gateway` (admin) |
| A rate-limit policy table | `bot.ABUSE_GUARD_PROTECTED` (auth/checkout paths), `pulse_security_core.HIGH_RISK_RATE_RULES` — `sentinel.rate_limit` counts, callers set limits |
| A session store | `mobile_security_sessions` |
| An injection scanner | `ai_security.scan_for_injection()` |
| A UNDX tool gate | `undx_tool_gateway.execute()` |
| An object-ownership check | the roster idiom above — `pulse_conversation_participants` for chat, `WHERE id=? AND user_id=?` in services, `pulse_advertiser_portal._require_account_role` for ad accounts. Stage 5/29 tests and observes these; it does not replace them |
| An invariant engine | `sentinel.invariants.INVARIANTS` + `run_all()` — add a check to the dict, never a second engine |
