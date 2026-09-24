# Control Plane Reconstruction — design record

**Mission:** make feature/runtime control describe production reality before it
is allowed to control production.

**Status:** inventory complete, activation **blocked** by one row. Nothing in
this change gates anything.

Measured against production on 2026-09-23 (Railway service `CoinPilotX`,
Postgres read-only session). Every number below came from the live database, a
live route probe, or the source tree at `c1cb743b4` — none from a prior document.

---

## 1. There are three control planes, and the one with a UI controls nothing

| Plane | Storage | Surface | Audit trail | Actually gates? | Failure polarity |
|---|---|---|---|---|---|
| **Environment** | Railway service variables — 297 total, 113 gate-shaped | none (redeploy to change) | none | **Yes — this is the real one** | mostly fails **closed** |
| **`feature_flags`** | Postgres, 15 rows | `/admin/capability-matrix`, owner-gated | `feature_flag_updated` | **No** | fails **open** |
| **`pulse_premium_feature_flags`** | Postgres, 7 rows | admin premium page, read-only | none | **No** | n/a |

`evaluate_flag` — the function that would apply the second plane — occurs
**exactly once in the repository: its own definition** at
`services/feature_flag_engine.py:259`. Nothing calls it.

### Proof the database plane has never been used

Two independent proofs, either sufficient:

1. All 15 rows carry the identical `updated_at` of `2026-05-22T11:51:57`.
2. `capability_audit_results` holds **0 rows**, while every `GET` of
   `/admin/capability-matrix` inserts one row per feature
   (`bot.py:103352`) and nothing anywhere deletes from that table. The page has
   never been opened in production.

> The second proof is why this audit was done from database counts and route
> probes rather than by loading the admin page. Opening it to look would have
> written the 15 rows that prove it had never been opened.

---

## 2. The seeded matrix is wrong in both directions

Only **7 of 15** rows describe production. The rest fail in opposite directions,
which is why "just enable everything" is not the fix either.

| `feature_key` | seeded | reality | production evidence |
|---|---|---|---|
| `pulse_posts` | `enabled` | LIVE_GLOBAL ✓ | 2,465 posts |
| `pulse_comments_reactions` | `enabled` | LIVE_GLOBAL ✓ | 40 comments, 113 reactions |
| `pulse_messenger` | `enabled` | LIVE_GLOBAL ✓ | route 401; 7 conversation settings |
| `pulse_spaces` | `enabled` | LIVE_GLOBAL ✓ | route 405; 2 space members |
| `pulse_groups` | `enabled` | LIVE_GLOBAL ✓ | 2 groups; route 401 |
| `marketplace_browse` | `enabled` | LIVE_GLOBAL ✓ | 47 listings |
| `merchant_applications` | `enabled` | LIVE_GLOBAL ✓ | 5 applications |
| `pulse_reels` | `beta` | **LIVE_GLOBAL** ↑ | 71 reels; shipped App Store tab |
| `pulse_livestream` | `beta` | **LIVE_GLOBAL** ↑ | 284 live streams, 167 reactions |
| `ai_assistant` | `beta` | **LIVE_GLOBAL** ↑ | 782 AI messages, 1,977 AI posts |
| `premium_advanced_tools` | `beta` | **LIVE_CONDITIONAL** ↑ | all 7 premium flags `enabled=1` |
| `creator_cockpit` | `beta` | **LIVE_CONDITIONAL** ↑ | 19 growth workspaces (19 of 41 users) |
| `marketplace_checkout` | `internal-only` | **LIVE_GLOBAL** ↑↑ | **32 real orders in `seller_transactions`** |
| `premium_identity` | `enabled` | **LIVE_CONDITIONAL** ↓ | entitlement-gated, not global |
| `admin_command` | `enabled` | **INTERNAL_ONLY** ↓ | 199 admin routes behind `require_admin_page` |

↑ seed understates exposure · ↓ seed overstates exposure

### The row that makes this urgent

`evaluate_flag` maps `internal-only` to `{"visible": False, "usable": False}`
for any user without `is_admin`. `marketplace_checkout` is seeded
`internal-only` and production has taken **32 real orders** through it.

**Wiring the engine to the seeded table would withdraw checkout from every
customer on the platform — and would look like a configuration cleanup in
review.**

---

## 3. The two planes have opposite failure polarity

This is the finding that makes reconciliation a *rewrite* rather than a copy.

- **Environment plane — fails closed.** Every `BUSINESS_OS_*` gate reads
  `getenv(KEY, "") in {"1","true","yes","on"}`. Unset means off.
- **Database plane — fails open.** `normalize_state` returns `"beta"` for any
  unrecognised value, and `evaluate_flag` grants `beta` both `visible` and
  `usable`.

So a state misspelled in the admin form — `"internal only"` with a space rather
than a hyphen is the most plausible slip — silently grants **full public
access** instead of erroring. On a page whose entire stated purpose is
restricting public exposure.

`tests/pulse_control_plane/` pins this, including the space-instead-of-hyphen case.

### Two more defects in the same page

- **`rollout_percentage` is decorative.** Written by the form, persisted,
  rendered back, indexed by `idx_feature_flags_state` — and read by no logic.
  A 0% and a 100% rollout produce identical verdicts.
- **`public_label` desynchronises permanently.** The admin `POST` never writes
  it (`bot.py:103331`), while `init_db()` unconditionally resets it from the
  hardcoded default on every call (`bot.py:121991`) — and `init_db()` is called
  per request. So the one field whose name says it is user-visible is the one
  field an owner cannot change.

---

## 4. Environment gates: 14 are set in production and read by nothing

Derived by `scripts/control_plane_audit.py`, cross-checked against an
independent manual grep — both methods returned the same 14.

```
AGORA_OWNER_LIVE_TEST_ENABLED      TRANSLATION_AUTO_DETECT_ENABLED
ENABLE_SMS                         TRANSLATION_GLOSSARY_ENABLED
ENABLE_TELEGRAM                    TRANSLATION_USER_OVERRIDE_ENABLED
ENABLE_TOOL_SEARCH                 UNDX_AGENT_COUNCIL_ENABLED
META_MODEL_API_ENABLED             UNDX_HTTP_RUNTIME_ENABLED
META_MUSE_FALLBACK_ENABLED         UNDX_METRICS_ENABLED
                                   UNDX_NATIVE_CONTEXT_ALLOW_TEXT_ID_OVERRIDE
                                   UNDX_NATIVE_CONTEXT_ENABLED
```

`ENABLE_SMS=true` and `ENABLE_TELEGRAM=true` are the instructive pair: both look
like master switches for live integrations, and neither is read anywhere. The
real gates are `BREVO_SMS_ENABLED` and the Telegram token's presence.

### A near-miss worth recording

The first version of the audit detected readers by matching
`os.getenv("NAME")` and reported **51** dead variables. It was wrong about 38,
including `MARKETPLACE_CARD_PAYMENTS_ENABLED` — the gate holding checkout open —
because this codebase reads environment variables through at least four
indirections:

```python
_flag("UNDX_ROUTER_ENABLED", False)                    # undx_router.py:231
_env_bool("COMMAND_CENTER_ENABLED", False)             # command_center_client.py:63
_env_enabled("PUSH_NOTIFICATIONS_ENABLED", True)       # push_service.py:182
CARD_PAYMENTS_ENABLED_ENV_VAR = "MARKETPLACE_..."      # marketplace_payment_pause.py:55
```

plus one name assembled at runtime (`bot.py:52554` chooses between two names
depending on which is set).

Detection is therefore a plain **substring search for the variable name**, which
over-counts readers — a name in a comment registers. That bias is deliberate: a
false DEAD deletes a live kill switch, whereas a false alive merely leaves a
stale variable for someone to check by hand. The audit script's docstring
carries this reasoning so the "improvement" is not re-attempted.

---

## 5. What this change does and does not do

**Does:** adds `services/pulse_control_plane/` (a description), a regenerating
audit script, 24 tests, and this record.

**Does not:**

- wire `evaluate_flag` to anything — it still has one occurrence, its own definition
- change any stored flag value, in the database or in Railway
- add a route, a table, an index, or an `os.getenv`
- touch `bot.py`, mobile, or any shipped commerce surface
- give PulseExperiments authority over any existing feature

`services.pulse_control_plane.reconcile.activation_gate()` encodes the mission's
ordering constraint as a function that raises. It has no `force` argument and no
environment override, because an override is the first thing reached for during
an incident.

It currently raises on `marketplace_checkout`.

---

## 6. Ordered path to activation

1. **Repair the stored states** to match column "reality" above — a data change,
   reviewed on its own, with `marketplace_checkout` first.
2. **Fix the open-failure polarity.** `normalize_state` should refuse an
   unrecognised state rather than defaulting to the most permissive one.
3. **Either implement `rollout_percentage` or remove it** — including the index
   that serves no query.
4. **Fix `public_label`**, or drop it and derive the label from state.
5. **Retire the 14 dead variables** from Railway, then enable the audit script
   in CI (it exits non-zero on them today, which is why this change does not
   wire it in — an audit that is red on arrival gets disabled).
6. Only then may `evaluate_flag` or PulseExperiments govern a shipped feature.

Steps 1–4 are each independently reviewable and independently reversible. None
is bundled here, because a data repair and a polarity change have different blast
radii and should not share a rollback.

---

## 7. Mission 2 — what changed, and where the plan above was wrong

Mission 2 worked §6 in order. It departed from it twice, and in both cases the
plan was wrong: the obvious remedy turned out to be the harmful one. Those two
are worth more than the list of what landed.

### Step 1 did not repair the stored states

The plan said repair `feature_flags.state`. The migration instead writes the
reconciled truth into **new columns** and never touches `state` at all.

Correcting `state` in place sounds strictly better and is not available.
`normalize_state` maps every word it does not recognise to `beta`, which is the
*most permissive* state the legacy engine has — so the natural way to retire the
column, writing something like `deprecated` into it, would have widened all
fifteen rows at once. There is no value meaning "this no longer decides
anything."

So the column keeps its May 2026 words, `marketplace_checkout = 'internal-only'`
among them, and what makes that safe is only that `evaluate_flag` has no call
sites. `activation.readiness()` re-counts them on every run. The first version
of that count matched the function's own `def` line and declared the legacy
engine live on a tree where nothing calls it — the third time this package has
had to separate *mention* from *use*, after an auditor naming a variable and a
catalog describing a gate.

### Step 2's polarity fix moved rather than landed

`normalize_state` still defaults to `beta`. Changing it would alter the
behaviour of the engine being replaced, which is currently load-bearing for
nothing — risk with no matching benefit. Strictness lives in `parsing.py`
instead, where an ambiguous word raises rather than widening.

### Steps 3 and 4 landed as written

`rollout_percentage` is implemented (`rollout.py`), consulted by `evaluate`,
refused outright for protected capabilities, and salted by a constant with no
environment override — rotating a rollout salt silently redistributes who may
use a feature. The `idx_feature_flags_state` index is left alone: dead, but it
indexes `state`, no query filters on it, and an unused index on a fifteen-row
table deceives nobody.

`public_label` turned out to have a writer nobody had noticed. `init_db()` runs
**per request** and unconditionally re-asserted the seeded value, so an admin's
edit was overwritten within milliseconds. The column was writable in schema and
unwritable in practice — which is why production has carried
`marketplace_checkout = "Internal"` since May on a capability taking real
orders, and no operator could have corrected it. The seeder now re-asserts
`label` only, and the admin update coalesces so an omitted field does not blank
the stored value.

### What Mission 2 added

| Module | What it is |
|---|---|
| `model`, `capabilities`, `parsing` | Two axes: `DeploymentState` (a fact about the system) × `EligibilityPolicy` (a policy that *names* an authority and never becomes one) |
| `observations`, `reconciler`, `drift` | Production truth, the table's claim, and the gap — split into static contract drift (blocks a build) and production-observed drift (reported only) |
| `rollout` | The percentage column, implemented |
| `migration` | Additive, compare-and-set, whole-transaction rollback |
| `shadow` | Both engines' answers across all 75 subject × capability cells |
| `activation` | Wave order, and the gate that must pass before wave 1 |
| `write_security` | What guards the admin write once it starts meaning something |
| `env_gates` | The 14 dead Railway variables, re-audited with dispositions |

### The cutover diff, in full

All 75 cells: **61 agree, 11 narrow, 3 widen.** Every widening is
`marketplace_checkout`.

The narrowings fall on rows the legacy engine overstates — `premium_identity`
and `premium_advanced_tools`, where entitlement decides rather than the flag,
and `admin_command`, where role does. The widenings are the row that has taken
32 real orders while stored `internal-only`.

Which is why "refuse every widening" is the wrong cutover rule despite being
the one that sounds safe: it would preserve the single row that is lying about
money. The direction of a change is not its justification. So each widening
cell is written down individually with its evidence, and an unreviewed widening
blocks activation even when it is obviously correct.

Shadow evaluation is exhaustive rather than live because there is no request
path to shadow — the flags gate nothing — and manufacturing one would be the
wiring this mission forbids.

### Two capabilities are refused outright, not deferred

- **`admin_command`** governs `/admin/*`, which contains the capability matrix
  itself. A row that wrongly denied admins would delete the surface needed to
  fix the row, and now that `init_db()` no longer re-asserts stored values,
  nothing would restore it — recovery would mean a direct production Postgres
  session. `require_admin_page` already enforces this correctly across all 199
  routes.
- **`pulse_livestream`** sits on the Agora → Mux path, under the change
  hard-lock in `docs/realtime_audio_change_policy.md`. Adding a runtime
  consultation to it is a change to it whatever the consultation returns.

### Security of the write path — two findings, recorded not fixed

Four guards stand in front of `/admin/capability-matrix` POST: session auth,
`system.view`, owner level, CSRF. Each is pinned by a test that re-greps it
against `bot.py`. Both findings below are documented in `write_security.py`.

1. **CSRF coverage and admin auth are keyed on one value.**
   `enforce_admin_form_csrf` returns early when `session['admin_user_id']` is
   absent, which is safe only because `admin_current_user` reads that same key.
   A second admin auth leg — a bearer, an API key, an SSO header — would make
   the auth check pass on requests where the CSRF hook does not run. Silently,
   and across all 79 admin form POSTs rather than just this one. `verify_csrf`
   already carries `allow_bearer=False`, so somebody has stood here before.
2. **The owner check sits in the handler body**, after the connection is
   opened, rather than being a guard — though `require_owner_admin_page()`
   exists and does exactly this. Left alone because changing it would alter
   admin behaviour mid-mission.

The audit log is **append-only by convention only**: no trigger, no revoked
grant, no constraint. The property holds because no code violates it, which is
a statement about this tree and not about the table.

### Status

`activation.readiness()` is **red**, on the four known production findings, and
goes green once the Stage 11 migration is applied. Both halves are pinned by
tests — a gate that cannot pass is the same failure as one that cannot fail.

`evaluate_flag` still has zero call sites. Nothing in this package sits on a
request path.
