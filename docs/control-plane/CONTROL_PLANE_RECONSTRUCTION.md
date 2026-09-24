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
