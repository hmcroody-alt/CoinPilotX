# Sentinel — Deployment Readiness

Branch `feat/sentinel-server-defense-mesh`, based on `d8aaf911`. Twelve commits.
**Not pushed.** The brief did not authorize a push and no release policy for this
branch was found in the repo, so landing it is a decision for a human.

Every behaviour change in this branch ships **dark**: the two enforcement gates
default off, and turning them on is a separate, reversible act performed with an
environment variable. Nothing here changes a response any production client sees
until someone sets a variable. That is the whole shape of the deployment.

---

## 1. What actually changes on deploy, before any variable is set

Three things happen the moment this code boots, with no configuration:

| Change | Effect | Why it is safe to ship on |
|---|---|---|
| Sentinel schema DDL at boot | Creates its own tables; touches nothing existing | `schema_bootstrap` is default-on but only issues `CREATE TABLE IF NOT EXISTS` against `sentinel_*` names. Honest on failure — a DDL error marks the schema **not ready** rather than proceeding |
| Rate-limiter bucket sweep | Reclaims memory in three in-process limiters | Decision-neutral by construction and by assertion. A key past its window already prunes to `[]` on read, so removing it produces the identical verdict — `test_a_swept_key_and_an_expired_key_are_indistinguishable` |
| `pulse_ai_service.web_search_knowledge_item` extraction | None | Pure refactor. The envelope flag is off in production, so the item is byte-identical to what ships today |

Everything else is gated.

**The sweep is the only unswitched behaviour change in the branch.** That was a
deliberate call, and the argument is in the map (§4): a kill switch for it would
be a switch that turns a memory leak back on. The equivalence it rests on is
asserted, not argued, and six mutants plus two call-site mutants defend it.

---

## 2. Gate inventory

Thirteen gates, from `killswitches.GATES`. `all_gates()` returns the live values;
`enforcement_gates()` returns the subset that can refuse a request.

| Gate | Env var | Kind | Default | Governs |
|---|---|---|---|---|
| `schema_bootstrap` | `SENTINEL_SCHEMA_BOOTSTRAP_ENABLED` | schema | **on** | Creating Sentinel tables at boot |
| `ingest` | `SENTINEL_INGEST_ENABLED` | observe | **on** | Writing observations to Sentinel's own tables |
| `request_bridge` | `SENTINEL_REQUEST_BRIDGE_ENABLED` | observe | off | The request path emitting security events |
| `external_intel` | `SENTINEL_EXTERNAL_INTEL_ENABLED` | observe | off | Outbound calls to intel providers |
| `financial_detection` | `SENTINEL_FINANCIAL_DETECTION_ENABLED` | observe | off | Read-only financial risk analysis; **parent of the next four** |
| `marketplace_risk` | `SENTINEL_MARKETPLACE_RISK_ENABLED` **+ parent** | observe | off | Marketplace risk scoring |
| `payout_risk` | `SENTINEL_PAYOUT_RISK_ENABLED` **+ parent** | observe | off | Payout risk scoring |
| `refund_risk` | `SENTINEL_REFUND_RISK_ENABLED` **+ parent** | observe | off | Refund risk scoring |
| `ad_wallet_risk` | `SENTINEL_AD_WALLET_RISK_ENABLED` **+ parent** | observe | off | Ad wallet risk scoring |
| **`distributed_limits`** | `SENTINEL_DISTRIBUTED_LIMITS_MODE` | **enforce** | off | Rejecting requests with 429 across workers |
| **`receipt_participation`** | `SENTINEL_RECEIPT_PARTICIPATION_ENFORCED` | **enforce** | off | Refusing read receipts from a departed member |
| `automation` | `SENTINEL_AUTOMATION_ENABLED` | automate | off | All runbook automation |
| `financial_automation` | *(none — see below)* | automate | off | Money movement — hard-false, no such capability exists |

Two rows in that table would have been operationally wrong if written from the
source rather than executed, so both were checked by flipping the variable in a
live process and reading `all_gates()` back:

- **The four financial subdomains are AND, not OR.** Setting
  `SENTINEL_MARKETPLACE_RISK_ENABLED=1` alone does nothing; the parent
  `SENTINEL_FINANCIAL_DETECTION_ENABLED=1` must be set too. Setting only the
  parent also does nothing. An operator who set one variable and saw no effect
  would reasonably conclude the gate was broken.
- **`SENTINEL_FINANCIAL_AUTOMATION_ENABLED` is deliberately ignored.** The gate
  returns `False` with the variable set to `1`. There is no money-movement
  capability in Sentinel for it to enable, so the variable is reserved, not
  wired. Listing it as a working switch would imply a capability that does not
  exist.

`SENTINEL_EMERGENCY_KILL_SWITCH=1` forces **all thirteen** gates off — verified by
execution, not by reading — including `schema_bootstrap` and `request_bridge`.
Stage 21 existed because it did *not* previously reach those two; mutants G8 and
G9 defend the fix.

Two registry rules are enforced by tests, not by convention: an unknown gate name
resolves to **denied**, and no gate of an enforcement kind may declare
`default_on=True`.

---

## 3. Rollout order

Each step is independently reversible by unsetting one variable. Do not compress
them — the point of the ordering is that every enforcement step is preceded by a
step that measures what that enforcement would have done.

**Step 1 — observe.** `SENTINEL_REQUEST_BRIDGE_ENABLED=1`

Security events start reaching Sentinel's tables. No response changes.

Verify with `request_bridge.stats()` (field names checked against a live call):

- `schema_ready: true` and `worker_alive: true` — otherwise nothing is being
  written regardless of what `emitted` says.
- `emitted` and `written` both climbing.
- `evidence_complete: true`. If `dropped` is non-zero the buffer is shedding
  under load and the evidence has gaps — reported, not hidden, because a
  security log that silently loses events is worse than one known to be partial.
- `flush_failures` flat.

**Step 2 — shadow the limiter.** `SENTINEL_DISTRIBUTED_LIMITS_MODE=shadow`

The shared counter runs and records what it *would* have refused. Still no
response changes.

Verify with `rate_limit.stats()`:

- `mode: "shadow"` and `schema_ready: true`.
- `checks` climbing, `distributed_ok: true`, `degraded: false`. A degraded
  counter has fallen back and its numbers are not fleet-wide.
- **`shadow_limited`** — the count of requests enforcement *would* have blocked.
  This is the number step 3 depends on.
- `enforced_blocks` must stay **0** in shadow. If it moves, the mode is not what
  you think it is.

**Do not skip to `enforce`.** Three of the four limiters on these paths are
per-worker, so the effective production limit today is roughly the configured
number times `WEB_CONCURRENCY` (4). Enforcing makes the count fleet-wide, which is
an immediate ~4x tightening against real users on a client that cannot be updated.
`shadow_limited` is the number that tells you how many real people that is, before
it happens to them.

**Step 3 — enforce the limiter.** `SENTINEL_DISTRIBUTED_LIMITS_MODE=enforce`

Only after `shadow_limited` has been observed across a representative window,
including a peak. Refusals are 429 with the body the route already returned.
**Read blocker B2 below before this step** — it changes which rate-limit policy is
authoritative on `/api/mobile/auth/recover`.

**Step 4 — the receipt fix.** `SENTINEL_RECEIPT_PARTICIPATION_ENFORCED=1`

Independent of the limiter; order between them does not matter.
While off, the route emits a `receipt_from_departed_member` shadow event each time
it *would* have refused. Turn this on once that event count is understood — a
non-trivial rate means real clients are sending receipts for conversations users
have left, and it is worth knowing why before returning 404 to them.

Enforcement answers **404**, not 403, matching the four sibling routes: a
conversation the caller may not touch is indistinguishable from one that does not
exist. Mutants O11 and O12 defend that.

---

## 4. Rollback

Unset the variable. Every gate is read per-call, not cached at import, so there is
no restart required for a gate to take effect — `test_gate_registry.py` covers
this by flipping variables inside a running process.

For a total stop: `SENTINEL_EMERGENCY_KILL_SWITCH=1`.

The schema and the bucket sweep are not covered by rollback because neither is
gated in a meaningful sense. Reverting them means deploying the previous commit.

---

## 5. Verification performed

- **85/85 mutants killed** — `scripts/sentinel_mutation_check.py`, fourteen suites.
- **`tests/sentinel` + `tests/undx_brain`**: 1545 passed, 14 failed. The same 14
  fail at `d8aaf911` in a clean baseline worktree, with a byte-identical failure
  list. **Zero new failures.**
- **Integration suites**, each in its own process because each sets a
  process-wide `DATABASE_URL`: 22 / 17 / 11 passed.
- **Hard Rule #1**: `git diff --name-only d8aaf911..HEAD` over `mobile-native/**`,
  `ios/**`, `android/**`, `app.json`, `eas.json`, `*.plist` → **0 files**.
- **Hard Rule #2**: `scripts/realtime_audio_change_gate.py` → no protected
  real-time audio path changed, 29 files inspected.
- **Hard Rule #3**: scanning every added line of `bot.py` and `services/` for
  4xx/5xx literals yields 429, 403, 401, 423 and 404 — all in the shipped
  client's existing vocabulary. The only `500` and `538` matches are prose
  (comments arguing that an observer must never 500 the product, and the route
  count "~1,538"). No new status code reaches the shipped binary.

### What this verification does not cover

The baseline comparison is scoped to the suites above. The whole backend suite
cannot run as one process in this repo — a clean `d8aaf911` produces hundreds of
collection errors — so "no regressions" here means *within the suites that can be
run and compared*, not across the entire test tree. Stating the scope is the
honest version of the claim.

No load testing was performed. The sweep's cost is bounded by a 60-second
interval per dict (mutant W5 defends the interval), but that is an argument, not
a measurement.

---

## 6. Blockers and open decisions

These are real. None is an oversight.

### B1 — The four overlapping rate limiters were not collapsed

The brief asked for this. It cannot be done safely as part of this mission.

Four independent limiters decide `/api/mobile/auth/recover`, three of them
per-worker. Collapsing them means choosing one number, and every candidate number
changes what a real user of a frozen client experiences. Worse, B2 shows that
**neither of the two configured numbers is wrong in every configuration** — which
one is authoritative depends on a switch. So there is no reading of the source
that settles it, and "collapse these two" was not a safe instruction to leave for
a later stage without this correction attached.

What is done instead: the overlap is documented in the map (§4) with a table of
which limit binds under which configuration, and the behaviour is pinned by tests
in both configurations so that a future collapse is a deliberate renumbering
rather than a discovery.

### B2 — Enabling the shared counter silently changes which policy is authoritative

On `/api/mobile/auth/recover`:

| Configuration | Binds first | Decorative |
|---|---|---|
| Switch off (production today) | `pulse_security_core` 5/600, **per worker** | `basic_abuse_guard` 6/300 |
| Switch enforcing, 4 workers | `basic_abuse_guard` 6/300, **fleet-wide** | `pulse_security_core` 5/600 |

The two numbers swap roles. Nothing in either file says so, and no alert fires.
This is why step 3 of the rollout is not a routine flag flip: it is a policy
change wearing the clothes of an infrastructure change. Whoever flips it should
know which of the two numbers they are making real.

### B3 — `ai_security` is a module-shaped hole

All three public functions of `services/sentinel/ai_security.py` have **zero
production callers**. It scans for prompt injection, records an event rather than
punishing, labels its method `heuristic_regex_v1` rather than claiming AI, and its
tests pass. It is listed twice in the foundation map. Every available signal short
of grepping for call sites says "covered".

Deliberately not wired in, for two different reasons:

- `wrap_untrusted` **must not** be. `undx_brain.envelope` is the prompt boundary
  and is strictly stronger; adding a second fence vocabulary is worse than one,
  because a payload that can forge either fence escapes whichever envelope it is
  nested in.
- `scan_for_injection` *could* be, but where a detector runs is a policy decision
  — which surfaces are scanned, and what an `injection_detected` event may
  trigger. Wiring a detector into a request path as a side effect of a coverage
  audit is how a detection quietly becomes an enforcement.

`TestTheModuleIsNotWiredToAnything` pins the current state and fails the day it
changes, with a message saying to update the map at the same time.

### B4 — No Sentry

The brief said to reuse the existing Sentry integration. There is none: no
`SENTRY_DSN` among the 228 production variables, no `sentry_sdk` import. Sentinel
events go to Sentinel's own tables. Anything in the brief that assumed an existing
error-reporting pipeline has no referent.

### B5 — No Redis

Also assumed by the brief, also absent — verified against the live project (228
variable names, no `REDIS_URL`; `railway list` shows only `Postgres`). The shared
counter is built on PostgreSQL, following the in-repo precedent of
`admin_gateway.login_rate_limited`. Requiring a new Redis service would have been
a paid-provider blocker; faking distribution with process memory would have been a
lie under Hard Rule #4.

---

## 7. The line this system does not cross

AI may detect, correlate, summarize and recommend. It decides nothing.

Every refusal in this branch comes from deterministic server-side policy: a
timestamp against a window, a row present or absent in
`pulse_conversation_participants`, an integer against a configured limit. The gate
registry classifies each gate as `observe` / `enforce` / `automate` and a test
enforces that no enforcement gate may default on. `financial_automation` is
hard-false because no money-movement capability exists to gate.

There is no code path in which a model's output causes a user to be refused,
throttled, or banned.
