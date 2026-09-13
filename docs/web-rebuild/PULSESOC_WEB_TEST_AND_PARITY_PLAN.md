# PulseSoc — Web Test and Parity Plan

**Scope:** Stage 14. How every parity claim in this project gets converted from a code-tracing
judgement into a verified fact, and the specific ways this repository's test environment will
lie to you while you do it.

---

## 0. The rule this document exists to enforce

> **No endpoint was called during the inventory.** Every 🟢 in
> `PULSESOC_NATIVE_TO_WEB_PARITY_MATRIX.md` is a capability judgement derived from reading
> code. Route existence is not proof of behaviour, auth, or response shape.

A parity mark becomes real only when its row in §3 passes **against a live environment**. Until
then the honest status is "expected to work", and the plan says so.

This is not pedantry. The inventory already caught itself twice on exactly this class of error —
a route-extraction pass that missed 369 blueprint routes, and an f-string constant trap that
made four live marketplace families read as "native calls an endpoint that does not exist."
Static analysis is a hypothesis generator.

---

## 1. What each test layer can and cannot prove

| Layer | Proves | **Cannot** prove |
|---|---|---|
| **TS typecheck** (`tsc --noEmit`) | The client's own shape assumptions are internally consistent | That the server sends that shape |
| **Jest / RTL (client)** | Component logic, state machines, error/empty exclusivity | Anything about the backend |
| **pytest (SQLite)** | Python logic, route wiring, most auth paths | **Postgres type behaviour, constraint behaviour** (§4.1) |
| **pytest (Docker Postgres 18)** | Type and constraint behaviour | Production data shape |
| **Contract tests** (§2) | Request/response shape against a live API | That a human can complete the journey |
| **E2E journey** (§3) | The journey | Load, cross-browser rendering |
| **Manual device QA** | Universal links, push, checkout, uploads | Regression over time |

**The row that matters most is pytest-on-SQLite.** It is the default local and CI path, and it
is permissive in exactly the places this product is dangerous.

---

## 2. The parity ledger

Each row in §3 is a **contract test plus a journey test**, and both must name a real response.

**Contract test template** — this is the part that catches wire-level drift:

1. Call the endpoint against a live environment with a real session.
2. Assert the **response shape field-by-field**, not `status == 200`.
3. Assert the **error contract**: `pulseApi` reads **`error_code`**, not `code`. A backend
   answering only `code` collapses every error state to a generic failure, and a hand-built
   error object in a unit test hides it completely. Assert against the real error response.
4. Assert the auth contract: the same call **without** credentials returns 401, and **with a
   different user's credentials** returns 403 or an empty set — never another user's data.

**Journey test template:**

1. The journey completes at **all four breakpoints** (phone <768 / tablet 768–1119 /
   desktop 1120–1599 / wide ≥1600).
2. The journey completes **by keyboard alone**.
3. **Error and empty never co-render.** An empty-state claim requires every source READY; a
   failed fetch is not empty data. Test the mutual exclusion explicitly — this is a recurring
   defect shape in this codebase and it is invisible to a happy-path test.
4. The App Store CTA renders where required, from `services/app_links.py`, with
   `--pulse-on-accent` contrast.

---

## 3. The parity ledger rows

Grouped by phase. **Status starts at ⬜ for every row.** A phase does not exit until its rows
are ✅.

### Phase 0 — foundations

| # | Assertion | How |
|---:|---|---|
| 0.1 | A route registered without an auth decorator and not on the public allowlist **fails boot** | Add a deliberately ungated test route in CI; assert the process exits non-zero |
| 0.2 | Browser multipart upload to R2 completes | Scratch harness in a real browser; assert a non-null `ETag` per part |
| 0.3 | `/@username` is an index scan | `EXPLAIN` against production, post-index |
| 0.4 | Rate limits are global, not per-worker | Drive `N+1` requests across 4 workers; assert refusal at `N` |
| 0.5 | `bot.py:28760` escapes output | Store `<img src=x onerror=alert(1)` (no closing `>`) in a user field; render the admin table; assert escaped |
| 0.6 | AASA returns 200 with non-empty `details[]` | Health check, continuous |

> **0.5 must use the unterminated-tag payload.** `clean_html()` neutralises
> `<script>alert(1)</script>` but passes `<img src=x onerror=alert(1)` through intact, because
> the regex requires a closing `>`. A test using the well-formed payload **passes against
> broken code.**

### Phases 1–2 — shell and auth

| # | Assertion |
|---:|---|
| 1.1 | Tokens match native `colors.ts` exactly across all 5 themes — assert values, not "looks right" |
| 1.2 | The 14-node mesh SVG is byte-comparable to `pulseBackground.ts:117-155` |
| 1.3 | Feed column ≤ 680px at **every** breakpoint including ≥1600 |
| 1.4 | Reduced-motion and high-contrast blocks mirror native `theme.duration()` / `HIGH_CONTRAST_*` |
| 1.5 | Exactly **one** service worker is registered; `safeNotificationUrl()` is present |
| 1.6 | CSP on the SPA route contains **no `'unsafe-inline'`** in `script-src`; `connect-src` is not `https:` |
| 2.1 | Sign-in, refresh, sign-out round-trip |
| 2.2 | **Refresh under concurrency** — two tabs, a prefetch, and a parallel XHR burst do not trigger family revocation |
| 2.3 | Device revocation removes the session and the revoked session's next call 401s |
| 2.4 | 2FA step-up completes in a browser |
| 2.5 | A cookie-plus-bearer request resolves identity and **succeeds on a write** (the §3.3 fix) |
| 2.6 | A cookie and bearer naming **different** users is **denied** |

**2.2 and 2.6 are the two that will actually find bugs.** 2.2 exercises rotating refresh tokens
under browser concurrency — a pattern the phone never produces. 2.6 asserts the fail-closed
behaviour of the identity fix; a naive bearer-first reorder passes 2.5 and fails 2.6.

### Phases 3–6 — product core

| # | Assertion |
|---:|---|
| 3.1 | `/api/pages` full CRUD, all 21 routes, contract + journey |
| 3.2 | Error and empty mutual exclusion, proven |
| 4.1 | Profile renders; the wire payload does **not** carry all 106 `users` columns |
| 4.2 | Settings read/write round-trips through `user_settings` with no DDL |
| **5.1** | **Web feed row count equals the native app's for the same account** |
| 5.2 | Keyset pagination is stable under insertion during paging |
| 5.3 | Media renders from the API's resolved payload; `media_ids_json` never reaches the client |
| 5.4 | The 11 social-graph FKs validate with zero orphans |
| 6.1 | **Every** notification type resolves to a correct destination — enumerated per type |
| 6.2 | Web Push delivers with the tab closed |

> **5.1 is the single most important row in this document.** 81% of `pulse_posts` rows carry
> `user_id = 0`. A feed built with `INNER JOIN users` renders successfully, looks plausible, and
> is missing 1,915 posts. **A test that asserts "the feed rendered" passes.** Only a count
> compared against native catches it.

### Phases 7–10 — media, messaging, search

| # | Assertion |
|---:|---|
| 7.1 | Large multipart upload completes; **resumes after interruption**; produces a media record shape-identical to native's |
| 7.2 | No upload path proxies through Flask |
| 8.1 | Reels playback, creation and navigation work **by keyboard alone** |
| 8.2 | Status progresses with an explicit control, not a tap-hold analogue |
| 9.1 | Message send/receive round-trips against `comm_v2_*` |
| 9.2 | Polling backs off when the tab is hidden and resumes on focus |
| 9.3 | Optimistic echo reconciles correctly when the send **fails** |
| 10.1 | Saved round-trips for every `content_type` |
| 10.2 | Search returns PulseSoc content, not the 139 static marketing pages |

**9.3, not 9.1, is where a polling messenger breaks.** The success path is easy; the failure
path is where an optimistic echo silently becomes a message the user believes was sent.

### Phases 11–12 — commerce and the money path

| # | Assertion |
|---:|---|
| 11.1 | Browse, filter and cart round-trip |
| **11.2** | **A test asserts on the query binding type, not on the behaviour** (§4.1) |
| 12.1 | Full purchase completes against Stripe test mode |
| 12.2 | Offers (counter/accept) and returns complete |
| 12.3 | The checkout response carries a PaymentIntent client secret for `payment_mode: "payment_sheet"` and Stripe.js consumes it unchanged |
| 12.4 | `form-action` CSP remains pinned to `checkout.stripe.com` |
| 12.5 | **Stripe-web and Apple-IAP verification write the same entitlement record** — assert convergence, both directions |

### Phases 13–15

| # | Assertion |
|---:|---|
| 13.1 | Seller application submits **with documents** (depends on 7.1) |
| 13.2 | Store dashboard and both order-manager sides render and mutate |
| 13.3 | The commerce pack's local `_verified_bearer_write_authority()` is removed and its routes still pass 2.5/2.6 |
| 14.1 | A campaign is created, funded, reviewed and launched from desktop **in fewer steps than the native wizard** |
| 15.1 | Private Office returns **423** without a grant, and the browser unlock handshake succeeds with `X-Office-Grant` + `X-Office-Device` |
| 15.2 | A grant minted for one session **fails** when presented by another |
| 15.3 | Pulse AI `actions/confirm` / `actions/cancel` is a real control — **assert that cancel prevents execution**, not that the UI dismissed |
| 15.4 | No UNDX write-capable action is reachable from the web bundle |
| 15.5 | No AI provider key appears in any built artifact — grep every bundle in CI |

---

## 4. Environment constraints that will fake a result

These are observed properties of this repository's test environment. Each has produced a false
pass or a false failure before. **A test plan that does not account for them will report green
and ship a defect.**

### 4.1 SQLite hides Postgres type errors — the highest-stakes trap

Local development and the test suite run on **SQLite**, which is permissive about type affinity
and will happily compare an integer id to a text id. **Production Postgres raises.**

`marketplace_*` tables use **integer** ids; `business_os_mkt_*` tables use **text** ids.

> A web checkout path that crosses between them can pass **every** local test and fail in
> production **on the money path**.

**Mitigation, and it is specific:** assert on the **query binding** — the parameter type handed
to the driver — not on the outcome. A test asserting "the checkout succeeded" passes under
SQLite regardless of the bug. A test asserting the bound parameter is `int` catches it. Anything
Postgres-only additionally gets a run against a throwaway Docker Postgres 18.

### 4.2 The backend suite cannot run as one process

A clean `main` produces roughly **325 failures and 111 errors** when the whole backend suite is
run in a single pytest process. This is a pre-existing property of the suite, not a signal.

**Never interpret an absolute failure count.** Diff against a baseline worktree at the same
commit. Several directories are known to be process-hostile and must be run **one file per
process**:

| Directory | Symptom when batched |
|---|---|
| `tests/marketplace/` | ~8 failures reading *"no such table: marketplace_listings"* — **raised in a file you did not touch** |
| `tests/dropshipping/` | ~84 setup errors on a clean tree |
| Crypto alert tests | Each sets its own temp `DATABASE_URL` at import; batching two produces ~17 failures |

**The diagnostic that settles it:** pair the suspect file with a file you did **not** touch. If
the untouched file fails too, the failure is the process, not your change.

### 4.3 Auth-route tests leak rate-limit state across tests

`/api/mobile/auth/login` is limited to 10 per 300s per IP+device in a **process-level bucket
that a database reset does not clear.** Two independent limiters are involved — the
`auth_events` velocity check and `pulse_security_core`'s 10/300s.

**Symptom:** login tests pass individually and fail as a file.

**Mitigation:** reset **both** limiters per test, and **vary the source IP per test**. This
matters more for the rebuild than it did before, because Phase 2 adds many more auth tests.

### 4.4 Stage tests pin source literals

Marketplace tests assert on exact source literals, so a later stage invalidates an earlier
stage's assertions. **Always re-run the older `tests/test_marketplace_*.py` files**, not only
the ones for the stage you are on. The same discipline applies to any new web-phase tests that
assert on rendered strings — prefer asserting on data, not on copy.

### 4.5 A simulator cannot test the things this project depends on

Ad-hoc signing — required for the Agora frameworks — **strips associated-domains and the team
id**. Universal links and push therefore "fail" on the simulator no matter what the server
does.

> **Universal-link and AASA verification (row 0.6) requires a real device.** A simulator result
> is not evidence in either direction. This is the mechanism the entire app-promotion
> requirement rests on, so it gets device QA, every release.

### 4.6 Client-side gotchas worth pre-empting

- **Backend Python tests need the checkout's `.venv`** — system `python3` lacks `requests` and
  `pytest` and will fake a protection-suite failure.
- **Assert on data, not on Hermes bundles.** `grep` finds nothing in `main.jsbundle` because it
  is bytecode; use `strings -a`, and always grep a known pre-existing control string before
  believing a bundle is stale. The same "grep a known-present control first" discipline applies
  to the web bundle checks in row 15.5.

---

## 5. Regression protection for what already works

### 5.1 Protected systems — the gate is content-based, not path-based

`docs/realtime_audio_change_policy.md` and `config/realtime-audio-protected-paths.json` govern
livestream audio, calls, and the Pulse Radio foundation. **No web-rebuild change may edit a
protected path.**

Two properties that trip people:

- **`bot.py` is protected by diff *content*, not by path.** Only lines matching
  `backend_diff_patterns` trigger the gate — so a web-rebuild edit elsewhere in `bot.py` is
  fine, and one that happens to match is not.
- **`package.json` edits trip the gate.** `dependency_watch` covers `package.json` and the lock
  file, so even a devDependency change demands the full audio battery.
- **Named in the manifest ≠ protected path.** `allowed_paths` is a forbidden-API allowlist;
  only `categories[].paths` gates edits.

Run locally before pushing:
`python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`

### 5.2 The existing suites stay green

| Suite | Command | Applies to |
|---|---|---|
| Protection suite (21 subsystems) | `scripts/protection/run_protection_suite.py` | Every phase |
| Native verify | `cd mobile-native && npm run verify` (typecheck + i18n + jest) | Any phase touching shared contracts |
| Realtime-audio critical | `npm run test:realtime-audio-critical` | Any diff the content gate flags |

**Static checks do not replace device QA** for livestream, push, checkout or uploads. That is a
standing rule in this repo and the rebuild does not change it.

### 5.3 The native app must not regress

The native app is the product source of truth. Any backend change in Phase 0 — the identity
fix, the auth decorator, the CSRF unification, the key split — touches the phone's path too.

**Every Phase 0 backend item ships with a native regression pass on a real device**, not only
the simulator, and not only CI. The identity fix (2.5/2.6) is the one most likely to change
native behaviour, because it changes which credential wins.

---

## 6. CI gates

| Gate | Blocks | Runs on |
|---|---|---|
| `tsc --noEmit` + client jest | merge | every PR |
| Boot-time default-deny auth assertion | **deploy** | every deploy |
| `scripts/ops/route_contract_gate.py` — every client call path has a server route | merge | every PR |
| `scripts/ops/deploy_route_liveness.py` — `/health/routes` healthy on the deployed box | **deploy** | every deploy |
| AASA 200 with non-empty `details[]` | **deploy** | every deploy + continuous |
| No `'unsafe-inline'` in the SPA route's `script-src` | merge | every PR |
| No provider key / `DATABASE_URL` in any built artifact | merge | every PR |
| Realtime-audio change gate | merge | every PR |
| Protection suite | merge | every PR |
| Postgres-only assertions on Docker Postgres 18 | merge | PRs touching SQL |

The deploy gates exist because **~382 routes live inside `except Exception` registrations**, and
**pack #1 alone is 162 routes owning all messaging and calling.** A silent registration failure
is a 404 on the entire messaging product. `/health/routes` has always reported it; as of
2026-09-13 something reads the answer.

### 6.1 The two route gates are not the same gate

They look redundant and are not. Running only one leaves open the hole that produced the
TestFlight build 5 incident, where a client shipped against endpoints the deployed server did
not have, every Settings write returned the generic 404 body, and `/health` answered 200
throughout.

**`route_contract_gate.py` — build time, offline, complete.** Proves *the code in this repo has
a route for every path its clients call*. It derives the contract from client source on every
run rather than from a list: 449 `pulseApi()` call sites in `mobile-native/src/api/`, matched
against the 2,067 rules in `webhook_app.url_map`. Nothing has to be maintained by hand, so a
call site added today is covered today. The web client joins by adding one line to `CLIENTS`.

**`deploy_route_liveness.py` — deploy time, against a URL, shallow.** Proves the thing the
first one structurally cannot: *the process now serving traffic is that code*. A green build
gate says nothing about a deployment built from a different commit, or one where a pack raised
during registration. Production on 2026-09-13: healthy, 10 required endpoints, 24 packs.

Three design points carried over from the `edge_status()` work in
`PULSESOC_WEB_SECURITY_MODEL.md` §6.1, because the same failure modes apply to any gate:

- **Could-not-check is not a pass.** Both scripts use three exit codes — `0` ok, `1` contract
  broken, `3` no data — so a caller can tell "this is broken" from "I could not tell". A gate
  that finds zero call sites, or cannot reach the deployment, exits `3`. The single most likely
  moment for a connection to fail is the moment a deploy is broken.
- **The allowlist fails in both directions.** Known-pending endpoints live in
  `config/route-contract-allowlist.json` with a reason and a reference, because a permanently
  red gate is a gate nobody reads. But an entry whose route *now exists* also fails, as stale. A
  list that can only ever suppress failures rots until it is suppressing a real one.
- **The gate is mutation-tested.** 16 mutations / 32 checks, including two negative controls,
  in `tests/protection/test_route_contract_gate.py`. A gate is code that fails silently by
  construction: every way it can break — a regex that stops matching, an extractor that finds
  nothing — makes it quieter, not louder. Running it on a healthy tree demonstrates nothing.

Its first run found two client call sites with no server route: `POST /api/calls/voip-token`
and `/api/calls/voip-token/revoke`. Both are **known and deliberate**, not defects — CallKit
Stage 2 scaffolding written ahead of a backend blocked on the COINPLOTXAI INC. app transfer,
and unreachable today because `setNativeCallKitProvider()` is never called outside tests. They
are allowlisted against `reports/native_callkit_voip_integration.md`. Worth recording that the
gate found them from source in seconds with no knowledge of that report.

---

## 7. Release gate

The rebuild reaches general availability when **all three** hold:

1. Every in-scope row in §3 is ✅ **against a live environment**.
2. All 15 rows of `PULSESOC_WEB_SECURITY_MODEL.md` §11 are green.
3. Device QA has passed, on a real device, for: universal links, Web Push, checkout, and
   uploads.

Anything still ⬜ is reported as ⬜. **A capability judgement is not a pass**, and the project's
credibility depends on that distinction holding all the way to launch.

---

## Cross-references

- `PULSESOC_NATIVE_TO_WEB_PARITY_MATRIX.md` — the claims this plan verifies
- `PULSESOC_WEB_REBUILD_PHASE_PLAN.md` — the Exit criteria each ledger row serves
- `PULSESOC_WEB_SECURITY_MODEL.md` §11 — the launch gate in §7.2
- `PULSESOC_DATABASE_GAP_ANALYSIS.md` §5 — the type-binding trap behind 11.2
- `docs/realtime_audio_change_policy.md` — the protected-system gate
