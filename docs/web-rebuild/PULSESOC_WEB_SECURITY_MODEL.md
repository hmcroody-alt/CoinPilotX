# PulseSoc — Web Security Model

**Scope:** Stage 12. The security posture the rebuilt web client must ship with, the posture it
must **not** inherit, and the specific places where adding a second client changes the threat
model of code that is safe today.

All findings verified against the working tree and production Postgres on 2026-09-12. Urgent
items in the *existing* site are in `PULSESOC_WEB_SECURITY_FINDINGS_URGENT.md`; this document is
about the target.

---

## 0. The one-sentence summary

The backend's security posture is **better than its reputation in three places** (admin RBAC,
the Private Office second lock, bearer-token verification) and **worse than it looks in three
others** (HTML escaping, CSRF coverage outside `/admin`, and session revocability) — and the
rebuild's job is to inherit the first set structurally while refusing to carry the second.

---

## 1. What changes when a second client appears

Four properties are safe *today* only because there is exactly one writer and no cross-origin
traffic. A web client is the event that ends each of them. These are the highest-value items in
this document because none of them will produce an error when they break.

| # | Property | True today because | Ends when |
|---:|---|---|---|
| **T1** | JSON routes need no CSRF | *"the browser preflights it cross-site and this app sets no CORS headers"* (`bot.py:3451–3456`) — verified: zero `Access-Control-Allow-Origin`, zero `flask_cors` in the backend | **Anyone adds a CORS header.** The exemption's premise is 3,000 lines away from the exemption |
| **T2** | Zero foreign keys is contained | One backend, written by one team, is the only writer. 884 tables, 9,458 columns, **0 FK constraints** | A second client writes to the same tables. There is no database-level backstop against orphans |
| **T3** | Rate limiting works | Limits key on `X-PulseSoc-Device-Id`, which the phone sends | A browser arrives sending neither device header — every Chrome-on-Windows visitor collapses into one bucket |
| **T4** | One signing key is acceptable | `COINPILOTX_SECRET_KEY` signs Flask sessions *and* bearer tokens (`bot.py:3619`) | A third credential family joins. Rotating to kill a compromised web session logs out every phone in the field |

**T1 is the sharpest and deserves a standing rule:**

> Any pull request that adds a CORS header to the Flask app must, in the same diff, add JSON
> CSRF enforcement. These two changes are not separable, and nothing in the codebase currently
> links them.

---

## 2. Authentication

### 2.1 What is already strong — keep it

`account_user_id_from_mobile_access_token()` (`bot.py:3605–3655`) is a genuinely good design and
the rebuild must not weaken it. It does not merely decode:

1. HMAC-SHA256 over the token body, compared with `hmac.compare_digest`.
2. Rejects `uid <= 0`, empty device hash, expired `exp`.
3. **Database check** — the token hash must match an `active`, non-revoked, non-expired row in
   `mobile_security_sessions`.
4. The row's `device_hash` must equal the token's `dh`.

A stolen token alone is not enough, and revocation is immediate. That is the bar.

The session store itself is already shared and already browser-aware: **7,446 `web` sessions**
vs 2,686 `ios`, with refresh rotation, `session_family_id`, `reuse_detected_at`,
`revoked_reason` and `last_risk_score` all working for browsers today.

### 2.2 The cookie/bearer short-circuit — fix before the first authenticated page

`bot.py:3659` is a left-to-right `or` chain. A request carrying both a cookie and a bearer
short-circuits on the cookie, so `g.mobile_access_user_id` — set only inside the bearer branch —
is never set. Write gates that read it as their "CSRF-safe native caller" signal refuse. Reads
pass, writes 403.

**Two independent places in the codebase describe this bug in their own comments**, which is the
strongest available confirmation it is live. `services/business_os_commerce_routes.py:46–73`
contains the correct fix — re-verify the bearer independently, require it to name the same user
as the cookie, **deny on mismatch** — but it protects only 37 routes, while `bot.py:18415`
carries a comment asserting the opposite is true for the ads family.

**Security framing, not just correctness:** the naive fix (reorder to bearer-first) silently
changes *which identity wins* when a browser tab and a device token disagree. Verify-both-and-
require-agreement is the only option that fails closed. Lift it into `account_user_id()`.

### 2.3 Session revocability — the accepted-or-fixed decision

**This is the most significant open security decision in the rebuild.**

The Flask cookie session has **no server-side row**. There is no `SESSION_TYPE` and no
Flask-Session backend, so it is the default client-side signed cookie. Combined with:

- `PERMANENT_SESSION_LIFETIME` = `PERSISTENT_SESSION_DAYS` days
- `PERSISTENT_SESSION_DAYS = max(3650, env)` — **a 10-year floor.** The env var can only make it
  *longer*. Setting `PULSESOC_PERSISTENT_SESSION_DAYS=30` has no effect
- `SESSION_REFRESH_EACH_REQUEST = True` — re-emits `Set-Cookie` on every response

…the consequence is:

> **A "sign out everywhere" button cannot kill a web cookie session today.** The native leg is
> revocable (a DB row); the web leg is not. A browser session, once established, is effectively
> permanent, and the only way to invalidate it is to rotate the secret — which also logs out
> every phone.

The rebuild multiplies web sessions. Two options, and the plan must pick one before launch:

| Option | Cost | Effect |
|---|---|---|
| **Add a server-side session record** keyed to the existing `mobile_security_sessions` row, checked per request | One lookup per request; needs the row already being created for web | Makes web sessions genuinely revocable. **Recommended** |
| Accept and document | Zero | "Sign out everywhere" is a partial control and the UI must not claim otherwise |

If the second option is chosen, the UI must not display a promise the backend cannot keep. That
is the part that is non-negotiable either way.

### 2.4 Native-only 2FA

`/api/account` is 16 routes covering 2FA, trusted devices and session revocation, of which
**13 have never had a web caller** and the re-auth flow assumes a native prompt. A browser needs
its own step-up path. This is the one auth area that is genuinely unbuilt for web rather than
merely unexercised.

---

## 3. CSRF

### 3.1 The actual coverage

| Surface | Blanket enforcement |
|---|---|
| `/admin`, `/api/admin` | **Yes** — `enforce_admin_form_csrf` (`bot.py:3443`) + token injection (`bot.py:3272`) |
| `application/json`, everywhere | **Exempt by design** (`bot.py:3451–3456`) — see T1 |
| **All 406 `/api/pulse` routes** | **None** |
| All 203 `/api/business-os` routes | None (families roll their own) |
| `/api/account`, `/api/messages`, `/api/reels` | None |

Even within `/admin`, the in-code comment at `bot.py:3240–3267` records an audit finding **79
state-changing admin form POSTs, 42 of which never called `verify_csrf()`** and 39 of which
rendered no token field at all. The structural hooks were added precisely because per-route
discipline had failed — which is the argument for making the rebuild's protection structural
too.

### 3.2 Six contracts coexisted — RESOLVED, `services/csrf.py`

**Status: done.** The finding below is kept because it is the reason the module exists and the
reason it must not be unpicked.

Six pieces of code answered "is this write CSRF-safe?": `bot.verify_csrf`,
`bot._business_os_ent_csrf_ok`, `bot.pulse_ads_verify_write`,
`bot._subscription_action_write_allowed`, `business_os_commerce_routes._csrf_ok`, and a sixth
spelled inline in `admin_business_os_reconcile`. Driven through the same nine request shapes
inside a request context, they **disagreed on four of them** — measured, not inferred:

| request shape | `verify_csrf` | `_business_os_ent_csrf_ok` | `_csrf_ok` |
|---|---|---|---|
| form field, correct | ACCEPT | ACCEPT | ACCEPT |
| `X-CSRF-Token`, correct | refuse | ACCEPT | ACCEPT |
| `X-CSRFToken`, correct | refuse | refuse | ACCEPT |
| header correct, form wrong | refuse | ACCEPT | ACCEPT |
| bearer, no token anywhere | refuse | refuse | ACCEPT |
| constant-time comparison | no | no | yes |

Row two is the one that would have broken the rebuild. `verify_csrf()` read `request.form` and
nothing else, and it is the verifier behind **52 call sites plus `enforce_admin_form_csrf`**. A
`fetch` client sending `X-CSRF-Token` — which three of our own JS bundles already send — was
refused, and refused with "Security check failed", which reads as a stale tab rather than a
contract mismatch.

All six now delegate to `services/csrf.py`. Verified: 0/9 disagreements, 5/5 delegating,
constant-time comparison, and the bearer exemption exactly where declared.

**The accept-set, and why each is in it:**

| channel | status | why |
|---|---|---|
| `X-CSRF-Token` | **the contract** | a custom header cannot be attached cross-origin without a CORS preflight, and `webhook_app` sets no CORS response headers at all — so it is *stronger* evidence than a form field, not weaker |
| `X-CSRFToken` | legacy alias, deprecated | already accepted by the commerce and supplier packs; dropping it is a narrowing against traffic this repo cannot see |
| `csrf_token` form field | kept | 15 templates emit it and `inject_admin_form_csrf` writes it into every admin POST form |
| `<meta name="csrf-token">` | **not a channel** | a *source* the client reads, not something the server accepts. Named here so nobody adds a fourth spelling by assuming symmetry |

**When more than one channel carries a token, any of them matching is enough.** This is not a
detail. The first draft made the header win outright; an exhaustive 6-verifier × 30-shape sweep
against the pre-unification code showed that this **narrowed the gate in 12 places** — a valid
form field alongside a stale header passed before and would have been refused after. A narrowed
CSRF gate does not fail loudly; it refuses writes for a subset of clients while everything else
looks fine. Precedence survives only to decide what a *refusal* reports.

### 3.2.1 What each client must do

- **The SPA sends `X-CSRF-Token`.** One spelling. Do not add a seventh; the protection suite
  scans `static/` and `templates/` and fails on any `X-…CSRF…` header the server does not accept.
- **The native app sends nothing** and relies on the bearer exemption. It has no CSRF token to
  echo. `allow_bearer=True` is set on the three member-facing write gates and nowhere else.
- **`allow_bearer` defaults to `False`** so a future caller writing `csrf.verify()` cannot
  inherit an exemption by accident. The admin form path leaves it off deliberately: a bearer
  resolves a *member* identity, and letting it vouch for a request whose authority comes from
  `session['admin_user_id']` crosses a boundary for no gain — there is no admin client that
  carries a bearer.

A latent production bug surfaced while tracing this. `pulse_ads_verify_write` *intended* the
bearer exemption and said so in a comment, but implemented it as a bare `g.mobile_access_user_id`
test — a flag `account_user_id()` only sets when it reaches its bearer branch, which it skips
whenever a session cookie is present. The native app sends both, so **the exemption never fired
in production**: every native ad write and subscription action was refused while every read
succeeded. Fixed here.

The bearer verifier is resolved through a single seam (`services/csrf._bot()`). That seam is
load-bearing: the suites proving the gate fails closed — forged bearer, verifier absent, verifier
raising, bearer naming a different user than the cookie — inject their verifier by replacing it.
Bypassing it with an inline `import bot` leaves those five tests *passing while testing nothing*,
because an ignored fake bearer denies just as convincingly as a rejected one.

Guarded by `tests/protection/test_csrf_contract.py` (16 checks, mutation-tested at 64/64
including 15 negative controls).

### 3.3 SameSite is doing the real work, and it is not ours

`SESSION_COOKIE_SAMESITE = "Lax"` is hardcoded (`bot.py:1217`) and the code says it is the
primary defence. Lax blocks cross-site form POSTs in current browsers, but it is **a property of
the visitor's browser, not an application control**, and it does not protect against same-site
subdomain origins. It stays — as defence in depth, never as the only line.

---

## 4. Authorization

### 4.1 There is no auth decorator — and that is the structural problem

`grep -oE '^@[A-Za-z_.]*' bot.py` returns **seven** decorator kinds. **None is an auth
decorator.** All 1,775 in-file routes gate themselves by calling one of at least **17 different
helpers** inside the view body: `api_account_user()` 574 times, `require_account()` 152,
`verify_csrf()` 52, `require_admin_api()` 48, and so on.

**Consequences, stated as security properties:**

- There is no static way to prove a route is protected.
- No linter can require it.
- **Adding a route with no auth at all is a silent, test-passing change.**

> **The highest-leverage security change in this project** is a decorator plus a *boot-time*
> assertion: every registered rule is either decorated or on an explicit public allowlist, or
> the process refuses to start. This converts an unprovable property into a startup check, and
> it is the only mechanism that scales to a second client and a growing route count.

### 4.2 What is genuinely strong — inherit it

**Admin RBAC.** Of **330** routes under `/admin` or `/api/admin`, a gate-name scan leaves 8
apparent gaps and **all 8 are false positives on manual inspection**. Effective coverage is
100%. Plus: per-permission checks, `admin_has_permission` **returns `False` on exception**
(fails closed), every denial writes to `admin_audit_logs`, failed logins lock for 15 minutes
after 5 attempts, and admin sessions have real lifetimes the user sessions lack
(`ADMIN_SESSION_ABSOLUTE_HOURS` 12, `ADMIN_SESSION_IDLE_MINUTES` 60) with legacy sessions
treated as expired.

**Two separate identity systems — but not two disjoint ones.** `session['account_user_id']`
over `users` and `session['admin_user_id']` over `admin_users`. Keep them separate: no client
may be able to turn one cookie into the other.

> **Correction.** An earlier version of this section said "being a logged-in user grants no
> admin capability whatsoever, and there is no path from one to the other." That is false, and
> it is false for a fifth of the admin surface. `require_super_user_page()` and
> `require_super_user_api()` (`bot.py:5314`, `bot.py:5325`) resolve the **member** session and
> then call `user_is_super_user()`, which is true when the account row carries `is_super_user`
> **or** when the account's email equals the configured owner email. **105 of the 496
> admin-classified rules** reach admin standing by that path — `require_owner_api` 85,
> `require_owner_admin_page` 9, `require_super_user_api` 8, `require_super_user_page` 2,
> `require_owner_account_page` 1.
>
> So a role column on `users` would not be *introducing* the pattern. It already exists, named
> `is_super_user`, alongside an env-configured owner email that confers the same standing with
> no row change at all. The design advice survives — do not widen it — but it has to be given
> for the real reason: there are two elevation paths to audit, not one property to preserve. A
> rebuild that inherits the false version will build a client authorization model that cannot
> express what 105 live routes already do, and will read every one of them as a bug.

Counts from `config/route_auth_baseline.json`; reproduce with
`python3 scripts/protection/generate_route_auth_baseline.py`.

**Private Office second lock.** Header-bound `X-Office-Grant` + `X-Office-Device`, bound to the
credential family that authenticated the request — so a stolen grant presented by another
session fails equality. Fails closed with a `423 Locked` carrying no Office data, under the
in-code comment *"a broken lock check is a locked door."* The web client must **implement the
handshake, not route around it.**

**Ownership delegation.** No route in `bot.py` was found that mutates a user-owned object
without carrying the actor id into the service. The honest caveat: this proves the actor
*reaches* the service, not that the service *uses* it — the ownership predicate lives in
`services/`, and the rebuild must not treat this as an isolation guarantee.

### 4.3 The one weak gate — keep it off the web entirely

`require_admin_password()` (`bot.py:14939`):

- Accepts a shared password from the **query string** (`?password=...`) — lands in access logs,
  `Referer` headers and browser history.
- Compares with `==`, not `secrets.compare_digest` — timing-attackable.
- One shared secret, no per-actor identity, so its **9 call sites** produce no attributable
  audit trail.

**These routes must not be exposed to the web rebuild.** They are a role that is not an admin
user.

---

## 5. XSS — and why the rebuild is the fix

### 5.1 The current state

`clean_html()` (`bot.py:119744`) is the de-facto sanitiser at **2,217 call sites**, against
**4** uses of `html.escape`. It is a tag stripper, not an escaper. Verified by execution:

| Input | Output |
|---|---|
| `<script>alert(1)</script>` | `alert(1)` — neutralised |
| `<img src=x onerror=alert(1)` | **passes through intact** — the regex needs a closing `>` |
| `' onmouseover=alert(1) x='` | **passes through intact** |
| `Tom & Jerry` | `&` never encoded |

**264 call sites interpolate into an HTML attribute delimiter.** A find-and-replace across 2,217
sites is explicitly not the plan — that is how escaping bugs get introduced.

### 5.2 CSP exists, and `unsafe-inline` nullifies it

`bot.py:2668–2671` sets a real CSP on non-`/api/`, non-`/static/` paths:

```
default-src 'self'; object-src 'none'; img-src 'self' data: blob: https:;
media-src 'self' blob: https:; style-src 'self' 'unsafe-inline';
script-src 'self' 'unsafe-inline' https://static.cloudflareinsights.com;
connect-src 'self' https: wss:; frame-ancestors 'self'; base-uri 'self';
form-action 'self' https://checkout.stripe.com; upgrade-insecure-requests;
```

Much of this is good — `object-src 'none'`, `base-uri 'self'`, `form-action` pinned to Stripe,
`frame-ancestors 'self'` reinforced by `X-Frame-Options: SAMEORIGIN`, HSTS at one year with
`includeSubDomains`, `nosniff`, and a `Referrer-Policy`.

**But `script-src` contains `'unsafe-inline'`, which removes CSP's value as an XSS mitigation
entirely.** An injected `<img onerror=...>` executes. CSP cannot backstop the broken escaper.

**And `'unsafe-inline'` is not removable today** — it is *forced* by the architecture. 46.7% of
`bot.py` is HTML/CSS/JS string literal, three `after_request` hooks string-splice design tokens,
favicon tags and i18n into the response body, and there is no bundler.

> **This is the strongest security argument for the rebuild, and it is the one that generalises:
> a bundled SPA with hashed assets and no inline script can drop `'unsafe-inline'` and gain a
> real CSP backstop.** Two independent layers — contextual auto-escaping *and* a strict CSP —
> where today there is neither.

### 5.3 Two more CSP items for the target

- **`connect-src 'self' https: wss:` permits exfiltration to any HTTPS origin.** Tighten to the
  actual list (self, R2, Mux, Stripe).
- **CSP is skipped for `/static/`.** If the SPA's `index.html` is served from `/static/` it gets
  **no CSP at all**. It must be served by a `/pulse/*` route. This is a concrete architectural
  constraint, not a preference.

### 5.4 Fix now, independent of the rebuild

`bot.py:28760` — `f"<td>{str(cell)[:180]}</td>"`, **raw database cells with no sanitisation at
all**, rendered into an admin session. User-controlled data executing in an administrator
context is the classic privilege-escalation shape, and it sits on the surface with the strongest
authorization model in the codebase. **It should not wait for a phase.**

---

## 6. Rate limiting and abuse

Three independent, uncoordinated limiters run on every request. For a browser client the net
effect is close to none:

| Issue | Detail |
|---|---|
| **Device keying is native-only** | `X-PulseSoc-Device-Id` / `X-Device-Id`. A browser sends neither, so the fingerprint reduces to the User-Agent — every Chrome-on-Windows visitor shares a bucket, including the 180/60s API mutation catch-all |
| **Per-worker state** | `RATE_LIMIT_BUCKETS` and `_RATE_BUCKETS` are process dicts. The real limit is `N × WEB_CONCURRENCY`; the in-code comment notes a documented 6/300s actually permits 24 with 4 workers. The shared Postgres counter exists but is **default OFF** |
| **IP keying behind shared egress** | `client_ip_hash()` takes the **leftmost** `X-Forwarded-For` element with no trusted-proxy count. Railway's edge normalises it, but a NAT still puts hundreds of browsers in one bucket — including `/login` at 12/300s |
| **GET is never limited** | A browser-driven scraper of `/api/pulse/*` reads is unthrottled |
| **Uncovered mutating surface** | Anything not under `/api/*` and not one of 11 exact paths has no limit at all |

**Required before launch, in order:** (1) the web client mints and sends a stable
`X-PulseSoc-Device-Id`; (2) a shared store (Redis) so limits are global, not per-worker; (3) a
GET limit on the read-heavy `/api/pulse` families. Item 2 in particular must land *before* a
second client multiplies traffic, not after.

---

## 7. Input validation the web client will trip

Two `before_request` guards will reject well-formed browser requests unless the client is built
for them:

- **`pulse_security_core_guard`** (`bot.py:3037–3057`) runs `validate_json_shape()` and **400s
  any JSON body containing an unknown field** for paths in `STRICT_JSON_FIELDS`, returning
  `security_state: "schema_rejected"`. A client that sends one extra field fails.
- **`interactive_security_guard`** (`bot.py:3090–3095`) reads the first 6,000 bytes of any JSON
  body and 400s on `suspicious_text(raw)`. **A user posting HTML-looking text from a rich-text
  web editor can trip this.**

The second is the one that will produce confusing bug reports. It is a real defence and should
stay, but the web client needs a defined failure UX rather than a generic error, and the
rich-text path needs a decision about what it sends on the wire.

Also note `pulseApi` reads `error_code`, not `code` — a backend answering only `code` collapses
every error state to generic. The web client must speak the same contract or its error handling
is cosmetic.

---

## 8. Data integrity as a security property

884 tables, 9,458 columns, **0 foreign key constraints.** Every relationship exists only as
convention inside query code.

Adding 884 tables' worth of constraints is neither necessary nor safe. The plan:

| Add FK on | Status |
|---|---|
| 11 core social-graph relationships | **Clean — 0 orphans verified.** Add `NOT VALID`, then `VALIDATE` |
| `pulse_posts.user_id → users.id` | **Blocked** on the `user_id = 0` decision |
| Everything else | Leave. Revisit per-area as each phase lands |

**`user_id = 0` is a security-adjacent trap, not just a data one.** 81% of `pulse_posts` rows
(1,915 posts) carry a sentinel with no matching user. The current code works because it resolves
authorship in Python rather than joining. **A web feed query written the obvious way — `INNER
JOIN users` — silently drops 81% of the feed**, and an authorization filter written against that
join would be silently evaluating the wrong row set. Resolve it before the feed phase.

Related, and *not* as cheap as it first looked. **9,730 of 10,135 `mobile_security_sessions` rows
are revoked or rotated and are never touched — and every one of them still carries a `user_agent`,
9,729 an `ip_hash`.** The instinct is to delete them. Deleting them disarms refresh-token reuse
detection: `rotate_mobile_refresh_token()` (`bot.py:31288`) identifies a replayed token by finding
its row *with no status or expiry filter*, so a deleted row turns a detected credential theft into
a plain 401 with no security event, no family revocation and no user alert. `rotated` rows are also
still live credentials (`bot.py:91458`).

The job is therefore to tombstone the device and network identifiers and keep the hashes forever —
`scripts/web_rebuild/phase0_session_sweep.py`, with `tests/web_parity/test_session_sweep.py` holding
the invariants. Full reasoning in `PULSESOC_DATABASE_GAP_ANALYSIS.md` §5a.

One thing to settle *before* the web client ships, not after: 47% of that table is
`refresh_token_reuse` revocations belonging to **nine users**, dominated by `platform='web'`, with
single families re-revoked up to 19 times. That is reuse detection firing on benign refresh desync,
and the web rebuild adds exactly the second client leg that causes it (§5b).

---

## 9. Client-side security requirements

| Requirement | Why |
|---|---|
| **No AI provider key in the bundle** | Provider routing is server-side (`undx_router.py`, 7 providers) specifically so keys never reach a client. A web bundle is far easier to inspect than a Hermes-compiled binary — the temptation is higher and the consequence is worse |
| **No `DATABASE_URL`, ever** | G1 of the architecture. The client consumes JSON |
| **One service worker, seeded from `sw.js`** | `sw.js` and `service-worker.js` are both registered at two scopes and have **diverged**: `sw.js` has `safeNotificationUrl()` hardening at `:186-198` that `service-worker.js` lacks. Which one handles a notification click depends on scope. **This is a live security bug, not just duplication.** Rebuild one, unregister the other |
| **UNDX write actions stay off web in phase 1** | `APPROVE UNDX WRITE` and `APPROVE UNDX GUARD CHANGE` typed into a browser are materially weaker controls than in an authenticated native app. Read-only surfaces are safe. Emergency-stop deserves a design conversation, not a port |
| **Pulse AI confirm/cancel ported as a control** | `actions/confirm` + `actions/cancel` is a safety control. Flattening it into an optimistic toast — the natural web implementation — removes it |
| **Capital-graph data renders, never acts** | Financial read-only data must never be paired with an action that reads as advice or execution |
| **Never proxy uploads through Flask** | Direct-to-R2 presigned, same as the phone. Requires `ExposeHeaders: ["ETag"]` on the bucket |

---

## 10. Protected systems — security posture is inventory-only

No web work is proposed, and no modification is permitted, for: livestream audio, livestream
infrastructure, audio calls, video calls, call audio routing, the Agora session foundation, or
the Pulse Radio audio foundation. `/api/pulse/live` (38 routes) and `/api/calls` (24 routes) are
**⛔ excluded**.

The characteristic failure in this area is silent: an unrelated screen steals the audio session,
the build stays green, tests pass, and production goes quiet. A web client has no business near
it.

---

## 11. Launch gate

The rebuild does not ship to general availability until every row is green.

| # | Gate | Owner |
|---:|---|---|
| 1 | `account_user_id()` verifies both credentials and denies on mismatch | Backend |
| 2 | Auth decorator live, with boot-time default-deny assertion | Backend |
| 3 | ~~One header CSRF contract enforced on cookie-authenticated mutations~~ **DONE** — `services/csrf.py`, §3.2 | Backend |
| 4 | Session and bearer signing keys split | Backend |
| 5 | Web session revocability: implemented, **or** formally accepted and the UI corrected | Security + Product |
| 6 | CSP `script-src` has **no** `'unsafe-inline'` on the SPA surface; `connect-src` tightened | Client |
| 7 | SPA `index.html` served from a `/pulse/*` route, not `/static/` (so CSP applies) | Client |
| 8 | Shared rate-limit store on; browser sends `X-PulseSoc-Device-Id`; GET limits on `/api/pulse` | Both |
| 9 | Single service worker, hardened `safeNotificationUrl()`, the other unregistered | Client |
| 10 | R2 CORS verified against the live bucket, `ExposeHeaders: ["ETag"]` | Infra |
| 11 | `bot.py:28760` fixed (**should already be done — it does not wait for this gate**) | Backend |
| 12 | 11 core social-graph FKs added and validated; `user_id = 0` decided | Data |
| 13 | Session TTL sweep running | Infra |
| 14 | AASA health check returns 200 with non-empty `details[]` | Infra |
| 15 | No admin route reachable via `require_admin_password()` exposed to the web surface | Backend |

---

## Cross-references

- `PULSESOC_WEB_SECURITY_FINDINGS_URGENT.md` — findings in the *existing* site, pre-rebuild
- `PULSESOC_WEB_TARGET_ARCHITECTURE.md` §3 (auth), §6 (uploads), §11 (forbidden list)
- `PULSESOC_BACKEND_ROUTES_AND_AUTH.md` §4–§6 — the auth, authz and rate-limit detail
- `PULSESOC_DATABASE_GAP_ANALYSIS.md` — `user_id = 0`, the 0-FK posture, session TTL
- `PULSESOC_WEB_REBUILD_PHASE_PLAN.md` — when each gate item is due
