# 06 — SECURITY KNOWLEDGE MAP

Stage 6 recon. Read-only pass over `bot.py` (117k lines / 1,523 routes), `services/` (295 modules),
`services/sentinel/` (55 modules), `docs/sentinel/` (46 docs), `mobile-native/`, and the live
`coinpilotx.db` (776 tables, opened read-only).

Throughout, **"the doc says"** and **"the code does"** are kept strictly separate. Every claim cites
`file:line`. Row counts come from the live SQLite DB and are used as evidence of whether a code path
has ever actually executed in production.

---

## 0. Executive summary

The platform's authentication *primitives* are better than expected — the mobile refresh-token family
with reuse detection (`bot.py:30213`) is genuinely well built, biometrics are real, and secrets
handling is clean. The weakness is not in the primitives, it is in **enforcement being a convention
rather than a mechanism**, and in a layer of **security features that are UI-complete but
logic-absent** (2FA, recovery codes, trusted devices).

Three structural facts dominate everything below:

1. **There is no auth decorator anywhere in `bot.py`.** Authorization is 553 hand-written
   `api_account_user()` calls plus ~15 bespoke per-family guard helpers. Nothing fails closed.
2. **Several security features write state that nothing ever reads.** 2FA is a boolean column that
   the login path never consults. Recovery codes can be generated but never redeemed.
3. **Sentinel — 46 design docs, 55 service modules, 22 live DB tables — is dormant.** 21 of its 22
   tables have zero rows, its HTTP API is deliberately unregistered, and all automation is
   default-OFF.

---

## 1. AUTHENTICATION

### 1.1 The two identity paths

Everything funnels through one resolver:

```python
# bot.py:3173
def account_user_id():
    return session.get("account_user_id") \
        or account_user_id_from_mobile_access_token() \
        or restore_account_from_persistent_cookie()
```

`require_account()` (`bot.py:4914`) loads the user and additionally applies
`account_login_restriction_message(user)` (`bot.py:3839`) — the ban/suspension check — popping the
session if the account is restricted. `api_account_user()` (`bot.py:29847`) is a thin alias over
`require_account()` and is the idiom used by API routes.

**Path A — web session cookie.** Flask signed-cookie session, configured at `bot.py:432-435`
(and again at `bot.py:1184-1187`, the duplicate-app-object hazard noted in `CLAUDE.md`):
`SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SAMESITE="Lax"`,
`SESSION_COOKIE_SECURE` defaulting to on in deployed environments (`bot.py:111`).

**Path B — mobile bearer token.** A custom HMAC-SHA256 token, not a JWT
(`bot.py:3120` verify, `bot.py:30127` mint):

```python
# bot.py:30127  mobile_access_token()
payload = {"uid":…, "dh": device_hash, "iat":…, "exp":…, "jti": secrets.token_urlsafe(12)}
body = base64url(json(payload))
sig  = hmac.new(COINPILOTX_SECRET_KEY, body, sha256).hexdigest()
return f"{body}.{sig}"
```

Verification at `bot.py:3120-3170` does the right things: constant-time `hmac.compare_digest`,
expiry check, **and a DB round-trip** confirming a live row in `mobile_security_sessions` with
matching `access_token_hash`, `status='active'`, empty `revoked_at`, and a matching `device_hash`.
This means mobile access tokens are genuinely revocable — unusual and good.

### 1.2 Endpoint inventory

| Capability | Endpoint(s) | Impl | Table | Status |
|---|---|---|---|---|
| Web signup | `/signup` | `bot.py:5980` | `users` | Complete |
| Web login | `/login` | `bot.py:6056` | `users`, `auth_events` | Complete |
| Web logout | `/logout` | `bot.py:6184` | — | Complete |
| Mobile register | `/api/mobile/auth/register` (+`/api/pulse/…` alias) | `bot.py:6383` | `users` | Complete |
| Mobile login | `/api/mobile/auth/login` | `bot.py:6296` | `mobile_security_sessions` | Complete |
| Mobile session probe | `/api/mobile/auth/session` | `bot.py:6257` | — | Complete |
| Mobile refresh | `/api/mobile/auth/refresh` | `bot.py:6267` → `rotate_mobile_refresh_token` `bot.py:30213` | `mobile_security_sessions` | Complete, strong |
| Mobile logout | `/api/mobile/auth/logout` | `bot.py:6584` → `revoke_mobile_refresh_token` `bot.py:30351` | same | Complete |
| Mobile logout-all | `/api/mobile/auth/logout-all` | `bot.py:6595` | same | Complete |
| Password reset (web) | `/forgot-password`, `/reset-password[/<token>]` | `bot.py:12432`, `bot.py:12493` | `password_reset_tokens` (19 rows) | Complete |
| Password reset (mobile) | `/api/mobile/auth/reset-password`, `/api/mobile/auth/recover` | `bot.py:6523`, `bot.py:6513` | same | Complete |
| Email verification | `/verify-email[/<token>]`, `/api/mobile/auth/confirm-email` | `bot.py:12462`, `bot.py:6481` | `email_verification_tokens` (34 rows) | Complete |
| SMS / OTP | — | — | `sms_verification_codes` (**0 rows**) | **Table only** |
| Recovery codes | `/api/account/recovery-codes/generate` | `bot.py:79253` | `user_recovery_codes` (**0 rows**) | **Generate-only — see F2** |
| 2FA enable/disable | `/api/account/2fa/enable`, `/api/account/2fa/disable` | `bot.py:79227`, `bot.py:79240` | `users.two_factor_enabled` | **Cosmetic — see F1** |
| Admin login/logout | `/admin/login`, `/admin/logout` | `bot.py:15121`, `bot.py:15300` | `admin_users` (34), `admin_session_logs` (2) | Complete |

### 1.3 Refresh-token rotation — the strongest component

`rotate_mobile_refresh_token()` (`bot.py:30213`) implements a proper rotating-token family:

- Refresh tokens are stored **hashed** (`mobile_token_hash`, SHA-256, `bot.py:30108`).
- On presentation of a token that is no longer `active`, it treats this as **reuse** and revokes the
  entire `session_family_id` (`bot.py:30274-30281`, `revoked_reason='refresh_token_reuse'`).
- It then **notifies the user** ("Suspicious session activity detected", `bot.py:30283`).
- A deliberate, bounded grace window (`PERSISTENT_REFRESH_REUSE_GRACE_SECONDS`, default 180s,
  `bot.py:116`) tolerates genuine network-retry races, but only when device hash *or* IP hash matches
  (`mobile_refresh_reuse_grace_allowed`, `bot.py:30112`).

This is the one area of the codebase that is defensively engineered rather than conventionally
engineered.

### 1.4 Session lifetime — effectively permanent, and not configurable downward

```python
# bot.py:114
PERSISTENT_SESSION_DAYS = max(3650, int(os.getenv("PULSESOC_PERSISTENT_SESSION_DAYS", "3650")))
# bot.py:30042
MOBILE_REFRESH_TOKEN_TTL_SECONDS = int(os.getenv("PULSESOC_MOBILE_REFRESH_TOKEN_TTL_SECONDS", str(60*60*24*3650)))
```

The `max(3650, …)` **clamps the floor at ten years**. An operator who sets
`PULSESOC_PERSISTENT_SESSION_DAYS=30` gets 3650 anyway. `PERMANENT_SESSION_LIFETIME` is derived from
it (`bot.py:435`), so the web session cookie is also a decade long. Access tokens are 15 min
(`bot.py:30041`), which is fine; the refresh tier is where the risk sits.

### 1.5 Secret key handling — correctly hardened

`bot.py:98-110` refuses to boot in a deployed environment when no stable `FLASK_SECRET_KEY` /
`SECRET_KEY` / `SESSION_SECRET` is present, with an accurate explanation that a per-process random
key would sign mobile tokens the *other* gunicorn worker rejects. Escape hatch is explicit
(`PULSESOC_ALLOW_EPHEMERAL_SECRET`). This is a good guard and should not be removed.

One residual: `bot.py:5759` falls back to a literal `"pulse-reset-token"` string when signing
password-reset tokens if no env secret is present. Given the boot guard above this is unreachable in
production, but it is a hardcoded cryptographic constant in a reset path and should be deleted.

### 1.6 Mobile client storage & biometrics — real

- `mobile-native/src/session/sessionStore.ts:39-49` — tokens in `expo-secure-store` with
  `keychainAccessible: AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`. Correct choice: survives reboot for
  background refresh, never syncs to iCloud/another device.
- `mobile-native/src/session/biometricAuth.ts` — genuine `expo-local-authentication` integration
  (`~17.0.8`, `mobile-native/package.json:58`): `hasHardwareAsync`, `isEnrolledAsync`,
  `supportedAuthenticationTypesAsync`, `authenticateAsync`, with a typed result union covering
  `lockout`, `cancelled`, `not_available`, `session_invalid`.
- `mobile-native/src/settings/schema.ts:472-491` — `security.biometricUnlock` is correctly marked
  device-local (`DEVICE_LOCAL_KEYS`) so enrolling Face ID on a phone does not tell a tablet it has
  biometrics. Well reasoned.
- Note: `mobile-native/src/screens/settings/SecuritySettingsScreen.tsx:156` documents that
  **PulseSoc has no authenticated change-password mutation** — the only path is the emailed reset
  link. Deliberate, but it means an attacker holding a live session cannot be locked out by the
  legitimate user changing their password from within the app.

---

## 2. AUTHORIZATION

### 2.1 Ownership: 132 hand-written SQL filters, no shared primitive

There is **no** `assert_owns(user, resource)` helper. Each domain re-implements it. Representative
example — the ads subsystem, which is the money-touching one:

```python
# services/pulse_ad_payments.py:95
def _owner_account(conn, user_id, account_id) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM pulse_ad_accounts WHERE id=? AND owner_user_id=?",
        (safe_int(account_id, minimum=1), safe_int(user_id, minimum=1)),
    )
    account = row_to_dict(cur.fetchone())
    if not account:
        raise pulse_ads_service.PulseAdsError("Ad account not found.", 404)
    return account
```

This is the correct pattern (ownership folded into the `WHERE`, 404 not 403). The problem is its
*reach*. Parallel, separately-written equivalents include `_owned_account`
(`services/pulse_ads_service.py:688`), `_owned_campaign` (`:697`), `_owned_ad_media_asset`
(`:489`), `_owned_audience`, `_owner_where`, `_owner_column`, `_owned_account_ids` — plus **132 raw
`AND user_id=?` / `AND owner_user_id=?` filters spread across 32 files in `services/`**.

Measured coverage gap in the ads subsystem alone:

| File | Functions taking an `account_id` | Ownership-helper calls |
|---|---|---|
| `services/pulse_ad_payments.py` | 20 | 8 |
| `services/pulse_ads_service.py` | 13 | 0 (uses its own `_owned_*` variants) |

The route layer does **not** check ownership. `pulse_ads_api_user_required()` (`bot.py:17559`)
returns only "is someone logged in"; the `account_id` from the URL is passed straight through to the
service, which is solely responsible for binding it to the caller:

```python
# bot.py:18639
@webhook_app.route("/api/pulse/ads/accounts/<int:account_id>/wallet/funding-session", methods=["POST"])
def api_pulse_ads_wallet_funding_session(account_id):
    user, denied = pulse_ads_api_user_required()   # authN only — no ownership
    if denied: return denied
    ...
    funding = pulse_ad_payments.create_funding_session(conn, user.get("user_id"), account_id, payload)
```

So an IDOR anywhere in this platform is one missing `_owner_*(…)` line inside a service function,
with nothing above it to catch the omission.

### 2.2 Route-level auth coverage (measured)

Parsed all 1,523 route/handler pairs in `bot.py` and matched handler bodies against every known auth
idiom (`api_account_user`, `require_account`, `account_user_id`, `require_admin_api`,
`require_admin_page`, `require_owner_api`, `require_owner_admin_page`, `require_super_user`,
`admin_current_user`, direct `session.get("account_user_id"/"admin_user_id")`):

- 1,523 routes total
- 553 use `api_account_user()` directly
- 556 contain **no** recognised auth idiom in the handler body
- 358 of those are not covered by a `before_request` prefix gate
- **134 of those are state-changing (POST/PUT/PATCH/DELETE)**

Sampling the 134 shows most delegate to a *family-specific* wrapper my matcher didn't know about —
`_messenger_media_user()` (`bot.py:86229`), `_crypto_api_result()` (`bot.py:7543`),
`pulse_ads_api_user_required()`, `require_owner_admin_page()`. So the raw number overstates the
count of genuinely open routes. **But that is precisely the finding**: there is no single predicate
that answers "is this route authenticated?" — not for a reviewer, not for CI, and not for an agent.
Determining the auth status of any given route requires reading the handler and then reading
whatever helper it calls.

### 2.3 The three blanket gates (confirmed)

| Gate | Location | Scope | Behaviour |
|---|---|---|---|
| Arena Pro gate | `bot.py:2611` `enforce_arena_pro_access` | `/arena*`, `/api/arena/*` | Public prefixes `/arena-preview`, `/arena/player/`, `/api/arena/share/` bypass |
| Business OS 401 gate | `bot.py:6239` `enforce_private_business_os_authentication_boundary` | `/api/business-os/` **only** | Blanket `api_account_user()` → 401 |
| Admin form CSRF | `bot.py:3039` `enforce_admin_form_csrf` | `/admin*`, `/api/admin*` | Default-deny; `application/json` deliberately exempt |

Note the Business OS gate covers `/api/business-os/` but **not** `/admin/business-os/` (49 routes).
Those turn out to be individually protected by `require_owner_api()` (`bot.py:17503`) — verified by
sampling `bot.py:21590`, `:21630`, `:21676`, `:21718` — but again, by convention, one route at a time.

### 2.4 Admin / role model

`require_admin_api(permission="users.view")` (`bot.py:17525`) and `require_admin_page(permission)`
(`bot.py:17492`) both: `admin_login_required()` → `admin_has_permission(admin, permission)` →
`log_admin_audit(...)` on denial. Owner tier is a separate, coarser check — `require_owner_api()`
(`bot.py:17503`) compares `admin["role"].lower() != "owner"` as a **string literal**, not via the
permission tables.

Live data: `admin_users` 34 rows, `admin_roles` 25, `admin_role_permissions` 131, `roles` 25,
`permissions` 40. The RBAC tables are populated and in genuine use — this is the most real part of
the authorization story.

Note the **default argument** `permission="users.view"` on `require_admin_api`. A caller that forgets
to pass a permission silently gets the weakest one rather than an error.

### 2.5 Privacy, block/mute

`blocked_users` (2 rows) is referenced 15 times in `bot.py` — i.e. block is enforced at a small,
enumerable set of read paths, not centrally in the feed/query layer. `privacy_preferences` exists
with a single INSERT site (`bot.py:91419`) and **0 rows** in the live DB, so the privacy-preference
surface has never been exercised in this environment.

---

## 3. SECURITY SYSTEMS

### 3.1 Rate limiting — exists, but is per-process and in-memory

Three independent implementations, all module-level Python dicts:

| Layer | Location | Storage |
|---|---|---|
| `basic_abuse_guard` | `bot.py:2647` | in-proc; covers `/login` (12/300s), `/signup` (8/300s), `/forgot-password`, `/admin/login`, `/create-checkout-session`, `/api/ai-assistant` |
| `interactive_security_guard` → `security_guard.rate_limited` | `bot.py:2802`, `services/security_guard.py:30` | `BUCKETS = defaultdict(list)` (`security_guard.py:11`) |
| `pulse_security_core_guard` → `rate_limited` | `bot.py:2716`, `services/pulse_security_core.py:142` | `_RATE_BUCKETS: dict = defaultdict(list)` (`pulse_security_core.py:22`) |

`HIGH_RISK_RATE_RULES` (`services/pulse_security_core.py:38-58`) covers mobile login (10/300s),
register (6/300s), recover (5/600s), reset-password (5/600s), media upload, reels, posts, live, ads,
checkout. The rules themselves are sensible.

Consequences of in-memory storage, all real:
- The Procfile runs `--workers 2` (`Procfile:1`), so **every limit is effectively 2×** — mobile login
  is 20/300s, not 10.
- Every deploy resets all counters.
- `BUCKETS` / `_RATE_BUCKETS` are keyed by `ip_hash:path` and **never evicted** — unbounded memory
  growth under scan traffic.
- Redis is an optional integration and is not used by any of the three limiters.

`pulse_security_core` also implements `STRICT_JSON_FIELDS` allow-lists for the auth endpoints
(`pulse_security_core.py:60-70`) and env-driven kill switches (`KILL_SWITCH_ENV`, `:89`) — both real
and wired via `before_request`.

### 3.2 CSRF — admin-only

`enforce_admin_form_csrf` (`bot.py:3039`) is default-deny but scoped to `path.startswith("/admin")
or path.startswith("/api/admin")`. `CSRF_EXEMPT_ADMIN_PATHS = {"/admin/login", "/admin/logout"}`
(`bot.py:3003`). `inject_admin_form_csrf` (`bot.py:3008`) rewrites admin POST forms in the response
body so 79 call sites didn't need editing — pragmatic and effective.

Outside `/admin`, CSRF is 52 scattered `verify_csrf()` calls plus per-family helpers like
`pulse_ads_verify_write()` (`bot.py:17545`). The ~1,000 non-admin state-changing routes rest on
`SESSION_COOKIE_SAMESITE="Lax"` (`bot.py:433`) as their primary cross-site defence.
`pulse_ads_verify_write` contains an explicitly reasoned exemption for bearer-token callers
(`bot.py:17546-17552`) — that reasoning is sound (a custom header cannot be forged cross-site).

### 3.3 Audit logging

| Table | Live rows | Write site |
|---|---|---|
| `admin_audit_logs` | 64 | `insert_admin_audit_with_cursor` `bot.py:595`, `log_admin_audit`, `services/audit_service.py:7` |
| `auth_events` | 460 | login/logout paths |
| `security_events` | 99 | `services/security_monitor.py:11` `record()` |
| `user_security_events` | 4 | `bot.py:79234`, `:79247`, `:79264` |
| `admin_session_logs` | 2 | admin login |
| **`audit_logs`** | **0** | **no INSERT site anywhere in `bot.py` or `services/`** |

`audit_logs` is a dead table. `admin_audit_logs` is the live one. Admin permission *denials* are
logged (`bot.py:17498`, `:17509`, `:17520`, `:17531`); ordinary user actions largely are not.

### 3.4 Device recognition & trusted devices

Device fingerprinting is real: `pulse_security_core.device_fingerprint()`
(`services/pulse_security_core.py:107`), with `cache_device_trust` / `get_device_trust` (`:113`,
`:118`) backed by an in-memory `_TRUST_CACHE` (`:24`). `mobile_security_sessions` carries
`device_hash`, `device_label`, `platform`, `country`, `last_risk_score` and holds **9,152 live rows**
— this is genuinely populated.

However **`user_trusted_devices` has 0 rows and zero `INSERT INTO` sites** across `bot.py` and
`services/`. The "trusted devices" concept exists as a table and as UI copy
(`bot.py:53004`, `:53052`) but has no implementation.

### 3.5 Failed-login lockout

`failed_login_controls` schema at `bot.py:5055`, index at `:5084`, read at `:5213`, written at
`:5334`, admin safe-listing at `:27425`, admin review at `:27476`. The code path exists end-to-end —
but the table has **0 rows** in a database that has recorded 460 `auth_events` and 9,152 mobile
sessions. Either the insert path is unreachable in practice or the thresholds are never met. Worth a
runtime check; treat lockout as unproven rather than absent.

### 3.6 Moderation pipeline

`services/pulse_moderation_engine.py` is small (`moderate_text` `:39`, `moderate_comment` `:98`,
`extract_tags` `:30`) — heuristic text classification, not a case-management system. The
report→case→action chain is thin: `pulse_reports` 6 rows, `moderation_cases` 1 row, with a single
`INSERT INTO moderation_cases` at `bot.py:96871`. Companion modules `services/ai_moderation_core.py`,
`services/roast_safety_filter.py`, `services/pulse_ai_safety.py` exist. Scam shield is three modules
(`services/scam_shield.py`, `scam_shield_engine.py`, `scam_shield_service.py`) plus a public
`/scam-shield` page.

### 3.7 Fraud prevention

`wallet_risk_checks` — 0 rows. `risk_scores` — 0 rows, **no INSERT site**. Marketplace-fraud, payout,
refund-abuse and ad-wallet-integrity logic lives almost entirely in `services/sentinel/` — see §4,
where it is shown to be dormant. The one exception is `services/sentinel/ad_wallet_integrity.py`,
whose invariants are also unexercised (`sentinel_financial_*` tables all 0 rows).

---

## 4. THE SENTINEL SUBSYSTEM

**Bottom line: the code is real and well written; the deployment is inert.**

Three decisive pieces of evidence:

1. **`bot.py` contains zero occurrences of the string "sentinel"** (case-insensitive; verified count
   = 0). The Blueprint is never registered. `services/sentinel/api.py:1-18` says so in its own
   docstring:

   > "DELIBERATELY NOT REGISTERED with bot.py in V1… Reasons for shipping unwired: bot.py is under
   > concurrent change and is protected by the audio diff gate."

   So all ~15 `/api/admin/sentinel/*` endpoints defined in `services/sentinel/api.py` are
   unreachable. (Its `_admin_guard` at `:30` fails closed, and it is read-only by design — good, but
   moot while unmounted.)

2. **21 of 22 Sentinel tables in the live DB have 0 rows.** Only `sentinel_edges` has data (10 rows),
   which exactly matches the sole production write path: `_sentinel_edge()` in
   `services/pulsesoc_pages.py:339`. Notably `sentinel_events` is **0** despite `_sentinel_event()`
   existing at `services/pulsesoc_pages.py:314` — its `ingest` call is inside a `try/except`
   (`:320`), so failures are silent.

3. **All automation is default-OFF** (`services/sentinel/killswitches.py:6-9`):
   `SENTINEL_AUTOMATION_ENABLED` default OFF, per-domain default OFF, per-runbook default OFF. Only
   `SENTINEL_INGEST_ENABLED` defaults on. `.env.example` declares just 7 `SENTINEL_*` keys against 46
   design documents.

The only live driver is `alert_worker.py:18,69` calling `sentinel_runtime.run_scheduled_ingestion()`.
`alert_worker` **is** in the Procfile (`Procfile:5`) — note this corrects the stale claim in
`CLAUDE.md`. But `services/sentinel/runtime.py:1-7` states it "remains inert until the master and
provider kill switches are explicitly enabled in Railway."

There are 16 files in `tests/sentinel/` (incl. `test_adversarial.py`, `test_ai_boundaries.py`,
`test_authority_and_risk.py`), so the modules are unit-tested in isolation.

### Capability matrix

| # | Documented capability | Doc | Code | DB rows | Verdict |
|---|---|---|---|---|---|
| 1 | Event ingestion model | `event_model.md` | `events.py` (11k) | `sentinel_events` **0** | **PARTIAL** — code real, 1 emitter, silent-fail |
| 2 | Entity/edge graph | `architecture.md` | `graph.py`, `entities.py` | `sentinel_edges` **10** | **IMPLEMENTED (minimally)** — the only live path |
| 3 | Detection model | `detection_model.md` | `detections.py` (13k) | — | **DOC-ONLY in effect** — no event stream to fire on |
| 4 | Incident lifecycle | `incident_model.md` | `incidents.py` (17k) | `sentinel_incidents` **0** | **PARTIAL** |
| 5 | Invariant enforcement | `invariant_model.md` | `invariants.py` (18k) | — | **PARTIAL** |
| 6 | Evidence chain | `evidence.md` | `evidence.py` | `sentinel_evidence` **0** | **PARTIAL** |
| 7 | Identity model / trust | `identity_model.md` | `identity.py`, `identity_trust.py` (18k), `identity_detections.py` (33k) | `sentinel_identity_risk` **0** | **PARTIAL** — largest module, zero data |
| 8 | Authority model | `authority_model.md` | `authority.py` | — | **PARTIAL** |
| 9 | Constitution | `constitution.md` | `constitution.py` (1.9k) | — | **PARTIAL** |
| 10 | Data classification | `data_classification.md` | `classification.py` | — | **IMPLEMENTED** (pure logic, no state) |
| 11 | Threat model | `threat_model.md` | — | — | **DOC-ONLY** (by nature) |
| 12 | Financial risk | `financial_risk.md` | `financial_risk.py` (13k) | `sentinel_financial_risk` **0** | **PARTIAL** |
| 13 | Financial invariants | `financial_invariants.md` | `financial_invariants.py` (13k) | — | **PARTIAL** |
| 14 | Financial reconciliation | `financial_*.md` | `financial_reconciliation.py` | `sentinel_financial_reconciliations` **0** | **PARTIAL** |
| 15 | Financial exposure | `financial_risk.md` | `financial_exposure.py` | `sentinel_financial_exposure` **0** | **PARTIAL** |
| 16 | Financial ATO | `financial_ato.md` | (within `identity_detections.py`) | — | **DOC-ONLY** |
| 17 | Financial event model | `financial_event_model.md` | `financial_events.py` | — | **PARTIAL** |
| 18 | Financial mutation lock | — | `financial_mutation_lock.py` | — | **PARTIAL** |
| 19 | Marketplace fraud detection | `marketplace_fraud_detection.md` | (within `financial_detections.py`) | — | **DOC-ONLY** — gated by `marketplace_risk_enabled()` default OFF |
| 20 | Payout security | `payout_security.md` | (within `financial_detections.py`) | — | **DOC-ONLY** — `payout_risk_enabled()` default OFF |
| 21 | Refund abuse | `refund_abuse.md` | (within `financial_detections.py`) | — | **DOC-ONLY** |
| 22 | Ad wallet integrity | `ad_wallet_integrity.md` | `ad_wallet_integrity.py` (5.4k) | — | **PARTIAL** |
| 23 | Provider trust | `provider_trust.md` | `providers.py`, `source_trust.py` | `sentinel_external_providers` **0** | **PARTIAL** |
| 24 | Provider circuit breakers | `provider_circuit_breakers.md` | (in `external_providers.py`) | `sentinel_provider_circuits` **0** | **PARTIAL** |
| 25 | External intelligence fusion | `external_intelligence.md` | `external_fusion.py`, `external_observations.py` | `sentinel_external_observations` **0** | **PARTIAL** |
| 26 | Enrichment policy | `data_sharing_policy.md` | `enrichment_policy.py` (12k) | `sentinel_enrichment_requests` **0** | **PARTIAL** |
| 27 | Supply chain security | `supply_chain_security.md` | `supply_chain.py` (19k), `github_security.py` (12k) | `sentinel_dependency_inventory` **0** | **PARTIAL** |
| 28 | Vulnerability intelligence | `vulnerability_intelligence.md` | `vuln_adapters.py` (17k) | `sentinel_vulnerability_findings` **0** | **PARTIAL** |
| 29 | Runbooks | `runbook_contract.md`, `provider-runbook.md` | `runbooks.py` | `sentinel_runbook_executions` **0** | **PARTIAL** — `runbook_enabled()` default OFF |
| 30 | Kill switches | `operations.md` | `killswitches.py` | — | **IMPLEMENTED** (and all default OFF) |
| 31 | Observability / owner summary | `operations.md` | `observability.py` (26k) | `sentinel_metrics` **0** | **PARTIAL** |
| 32 | Health snapshots | `operations.md` | `health.py` | `sentinel_health_snapshots` **0** | **PARTIAL** |
| 33 | Correlation / sequences | `detection_model.md` | `correlation.py`, `sequences.py` | `sentinel_sequence_firings` **0** | **PARTIAL** |
| 34 | Journeys | `architecture.md` | `journeys.py` | — | **PARTIAL** |
| 35 | UNDX boundary / interface | `undx_boundary.md`, `undx_interface.md` | `undx_interface.py` (16k), `ai_security.py` | — | **PARTIAL** — no UNDX module imports it |
| 36 | Verification | `verification.md` | `verification.py` (2.8k) | — | **PARTIAL** |
| 37 | Security Center integration | `security_center_integration.md` | `security_center_bridge.py` (10k) | — | **DOC-ONLY in effect** — nothing calls the bridge |
| 38 | Backend API surface | `architecture.md` | `api.py` (13k) | — | **DOC-ONLY in effect** — Blueprint never registered |
| 39 | Adaptive throttling | `adaptive_throttling_design.md` | — | — | **DOC-ONLY** |
| 40 | Cloudflare consolidation | `cloudflare_consolidation.md` | — | — | **DOC-ONLY** |
| 41 | Device intelligence provider | `device_intelligence_provider_evaluation.md` | — | — | **DOC-ONLY** (evaluation) |
| 42 | External retention/deletion | `external_retention_and_deletion.md` | (in `enrichment_policy.py`) | — | **PARTIAL** |
| 43 | Roadmap | `roadmap.md` | — | — | **DOC-ONLY** (by nature) |

**Tally: 2 IMPLEMENTED-and-live, ~1 minimally live, ~28 PARTIAL (code exists, never runs), ~12
DOC-ONLY.** By *executed behaviour in production*, Sentinel is roughly **5% real**. By *code written
and unit-tested*, roughly 65%. The gap between those two numbers is the entire finding.

---

## 5. SECURITY POSTURE FINDINGS — ranked by blast radius

Framed for the stated purpose: an AI agent will later be given tool access to this platform. The
recurring hazard is **every place the platform relies on the caller to remember a check.**

---

### F1 — 2FA is a boolean with no enforcement anywhere. **Blast radius: total account compromise.**

`bot.py:79227`:

```python
@webhook_app.route("/api/account/2fa/enable", methods=["POST"])
def api_account_2fa_enable():
    user = api_account_user()
    if not user: return api_error("Login required.", 401)
    cur.execute("UPDATE users SET two_factor_enabled=1, updated_at=? WHERE user_id=?", (now, user["user_id"]))
```

There is no TOTP secret, no enrollment, no QR provisioning, no verification step. And critically:
`two_factor_enabled` is referenced in only five places — `bot.py:79021` (adds **+25 to a "security
score"**), `:79082` (JSON echo), `:79118` (HTML label), `:79234` and `:79247` (the setters). **The
login path never reads it.** A user who enables 2FA gets a higher score, a green label, and exactly
zero additional security.

`/api/account/2fa/disable` (`bot.py:79240`) returns the message *"Two-factor protection disabled
after reauthentication"* while performing **no reauthentication whatsoever**.

This is worse than absent 2FA: it induces users to reuse passwords under a false belief.

---

### F2 — Recovery codes can be generated but never redeemed. **Blast radius: permanent account lockout + false assurance.**

`bot.py:79253` generates 8 codes, hashes them with `generate_password_hash`, stores them in
`user_recovery_codes`, and tells the user *"Save them now; they will not be shown again."*

Exhaustive search of `bot.py` and `services/` for `user_recovery_codes` yields exactly four sites:
- `bot.py:79263` — INSERT (generate)
- `bot.py:79261` — DELETE (regenerate)
- `bot.py:79056` — `SELECT COUNT(*) … WHERE used_at IS NULL` (display)
- `services/dashboard_account_command_center.py:1407` — same count, for a dashboard tile

**There is no SELECT that matches a submitted code against `code_hash`, and no UPDATE that ever sets
`used_at`.** No route accepts a recovery code. The feature is write-only. Live table: 0 rows.

---

### F3 — `undx_desktop_connector.py` grants repo write + `git push` with no authentication. **Blast radius: full source-code compromise of the deployed product.**

- Binds `127.0.0.1` (`undx_desktop_connector.py:1167`) — the only real control.
- The single `before_request` (`:182`) handles **CORS preflight only**; there is no auth hook.
- Exposed routes include `/file/read` (`:1090`), `/patch/apply` (`:1107`), `/git/commit` (`:1127`),
  `/git/push` (`:1143`).
- The sole gate is a **hardcoded, source-visible approval phrase**:

```python
# undx_desktop_connector.py:40-42
APPROVAL_WRITE = "APPROVE UNDX WRITE"
APPROVAL_GIT   = "APPROVE UNDX GIT"
APPROVAL_PUSH  = "APPROVE UNDX PUSH"
# :1146
if str(payload.get("approvalPhrase") or "") != APPROVAL_PUSH:
    raise ConnectorError("Push approval phrase required.")
```

These are constants in a committed file, not secrets. Any local process — or any browser page
reaching localhost — that knows the string (i.e. anyone who has read the repo) can push to
`origin/main` (`:1149`).

Aggravating: `undx_desktop_connector.py:177` sets `Access-Control-Allow-Private-Network: true`, and
`:171` reflects **any** `http://127.0.0.1:*` or `http://localhost:*` origin with
`Allow-Credentials: true`.

Mitigating: `PROTECTED_PATTERNS` (`:53-62`) blocks paths containing `.env`, `.git/`, `credentials`,
`secret`, `token`, `private_key`.

**An agent must never be pointed at this connector.** If it must run, it needs a per-launch random
bearer token, not a literal phrase.

---

### F4 — Authorization is 553 hand-copied checks + ~15 bespoke guards, with no fail-closed default. **Blast radius: one forgotten line = one open route.**

Quantified in §2.2: 1,523 routes, zero decorators, 553 inline `api_account_user()` calls, 132 raw
ownership filters across 32 service files, no shared `assert_owns()`.

The specific hazard for an agent: **you cannot determine a route's auth status from its signature.**
`bot.py:18639` looks unauthenticated until you read `pulse_ads_api_user_required()` at `bot.py:17559`;
`bot.py:7543` looks unauthenticated until you read `_crypto_api_result()`. Conversely, a genuinely
open route is visually identical to a protected one. There is no CI check that a new route is
authenticated — the protection suite (`scripts/protection/run_protection_suite.py`) covers 21
subsystems but not "did you add an unauthenticated route."

Compounding: optional route packs register inside `except Exception` blocks (per `CLAUDE.md`), so a
subsystem can vanish silently; and `require_admin_api`'s `permission="users.view"` default
(`bot.py:17525`) means a forgotten argument degrades to the weakest permission instead of erroring.

---

### F5 — Rate limiting is per-process, in-memory, and unbounded. **Blast radius: credential stuffing at 2× stated limits; memory exhaustion.**

`services/security_guard.py:11` (`BUCKETS = defaultdict(list)`) and
`services/pulse_security_core.py:22` (`_RATE_BUCKETS`). With `--workers 2` (`Procfile:1`) every
documented limit is doubled — mobile login is 20 attempts/300s, not 10. All counters reset on deploy.
Neither dict evicts keys, so `ip_hash:path` entries accumulate without bound under scanning.

Directly relevant to F1/F2: with no working 2FA and no confirmed lockout (F6), the login rate limit
is the *only* barrier against password guessing — and it is soft.

---

### F6 — Security tables that exist but are never written. **Blast radius: controls believed active that are not.**

Verified against the live DB (which has 9,152 `mobile_security_sessions` rows and 14,644
`verification_requests` rows, so it is not a fresh database):

| Table | Rows | INSERT site |
|---|---|---|
| `user_trusted_devices` | 0 | **none** |
| `risk_scores` | 0 | **none** |
| `audit_logs` | 0 | **none** |
| `wallet_risk_checks` | 0 | none found |
| `user_recovery_codes` | 0 | generate-only (F2) |
| `failed_login_controls` | 0 | `bot.py:5334` exists but never fired |
| `sms_verification_codes` | 0 | none found |
| `privacy_preferences` | 0 | `bot.py:91419` |
| `user_verifications` | 0 | none found |

An agent reading the schema would reasonably conclude that trusted-device pinning, risk scoring, and
failed-login lockout are operative. None of them demonstrably are.

---

### F7 — Ten-year sessions with a hard floor. **Blast radius: indefinite persistence of a stolen token.**

`bot.py:114` `PERSISTENT_SESSION_DAYS = max(3650, …)` and `bot.py:30042` refresh TTL default 3650
days. The `max()` makes shortening **impossible via configuration**. Because there is no
authenticated change-password mutation (`SecuritySettingsScreen.tsx:156`), a user who suspects
compromise cannot invalidate a stolen refresh token by changing their password — they must find
`/api/account/sessions/revoke-all` or `logout-all`.

Partly offset by the strong reuse-detection in `rotate_mobile_refresh_token` (`bot.py:30213`), which
does revoke a whole family on reuse.

---

### F8 — CSRF covers `/admin` only. **Blast radius: cross-site state change on ~1,000 user routes.**

`enforce_admin_form_csrf` (`bot.py:3039`) is scoped to `/admin*` and `/api/admin*`. Everything else
depends on `SESSION_COOKIE_SAMESITE="Lax"` (`bot.py:433`) plus 52 scattered `verify_csrf()` calls.
Lax blocks cross-site POST from forms, so this is defensible — but it is a single global setting with
no per-route backstop, and any future need to relax SameSite (third-party embed, OAuth return,
payment redirect) would silently expose the entire non-admin write surface at once.

---

### F9 — Zero foreign keys; integrity is application-level only. **Blast radius: orphaned auth/permission rows.**

Confirmed by the sibling agent and consistent with `bot.py:init_db()`'s imperative schema. For
security specifically this means: deleting a user does not cascade to
`mobile_security_sessions` (9,152 rows), `admin_role_permissions` (131 rows), `blocked_users`, or
`password_reset_tokens`. An orphaned session row with a live `refresh_token_hash` remains valid
because `rotate_mobile_refresh_token` (`bot.py:30219`) `JOIN`s to `users` — that join saves it. But
any future query that omits the join would not be protected.

---

### F10 — Silent-failure idiom hides security telemetry loss. **Blast radius: undetected blindness.**

`services/pulsesoc_pages.py:314-337` wraps Sentinel event emission in bare `try/except`. Result:
`sentinel_events` has **0 rows** while `sentinel_edges` has 10 — the events are failing silently and
nobody noticed. The same idiom appears in the optional route-pack registration described in
`CLAUDE.md`. Security telemetry that fails open and silently is worse than none, because dashboards
show green.

---

### Positives worth preserving

- Refresh-token family + reuse detection + revoke-all + user notification (`bot.py:30213-30290`).
- Boot-time refusal to run with an ephemeral secret key (`bot.py:98-110`).
- Mobile access tokens validated against a **revocable DB row**, not just a signature (`bot.py:3120`).
- `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` keychain accessibility (`sessionStore.ts:40`).
- Device-local scoping of `biometricUnlock` (`schema.ts:472-491`).
- Admin CSRF default-deny with response-rewriting token injection (`bot.py:3008`, `:3039`).
- Ownership folded into `WHERE` clauses returning 404 not 403 (`pulse_ad_payments.py:95`).
- `STRICT_JSON_FIELDS` allow-lists on auth endpoints (`pulse_security_core.py:60`).

---

## 6. SECRETS & CONFIG

- **`.env.example` declares 532 keys** (`grep -cE '^[A-Z0-9_]+=' .env.example`) — notably more than
  the ~180 cited in `CLAUDE.md`. Only **7** are `SENTINEL_*`, against 46 Sentinel design docs.
- **Loading:** plain `os.getenv` throughout; no secrets manager, no vault, no schema validation.
  Multi-name fallback chains are common (`bot.py:5759`: `SECRET_KEY` → `FLASK_SECRET_KEY` →
  `app.secret_key` → literal).
- **`.gitignore`** correctly ignores `.env` and `.env.*` while allowing `.env.example`
  (`.gitignore:1-3`). `git ls-files` confirms **no `.env` file is tracked**.
- **Hardcoded-secret scan** (`sk_live_`, `sk_test_`, `pk_live_`, `AKIA…`, `ghp_…`, `xoxb-`,
  `-----BEGIN … PRIVATE KEY`) across `bot.py`, `services/`, `mobile-native/src/`, `config/`:
  **no real credential found.** All hits are prefix comparisons (`bot.py:1161`, `:11198`, `:11200`,
  `services/payment_provider.py:42`), a redaction regex (`services/undx_brain/corpus.py:104`), or
  test fixtures (`mobile-native/src/api/__tests__/stripePaymentSheet.test.ts:29` etc.).
- **Tracked key material:** only `certificates/apple/AppleRootCA-G3.pem` — a public root CA, correct
  to commit.
- **Two exceptions to report (fact only, values not printed):**
  1. `bot.py:5759` — a literal string constant is the last-resort fallback for signing password-reset
     tokens. Unreachable in production given the `bot.py:98` boot guard, but should be removed.
  2. `undx_desktop_connector.py:40-42` — three approval phrases used as authorization gates are
     hardcoded constants in a committed file (see F3). These are functioning as shared secrets while
     being public.
- **Secret hygiene in logs** is deliberate and good: `services/sentinel/runtime.py:23` defines
  `ProviderTransportError` whose "message must never include credentials", and error paths collapse
  to `"provider request failed"` (`runtime.py:55`).

---

## Appendix — how the numbers were produced

- Route/auth coverage: AST-free line parser over `bot.py` pairing every
  `@webhook_app.route|get|post|put|delete|patch` with its handler body, matched against a
  15-alternative auth-idiom regex. 1,523 routes parsed.
- Row counts: `sqlite3` opened `file:coinpilotx.db?mode=ro` (read-only URI); 776 tables enumerated.
- Sentinel wiring: `grep -ic sentinel bot.py` → `0`; importers found by
  `grep -rl sentinel --include=*.py services/ *.py` → 7 files, of which 2 are real integrations
  (`services/pulsesoc_pages.py`, `alert_worker.py`) and 5 are unrelated uses of the English word
  "sentinel".
- No file in the repository was modified. The only write was this document.
