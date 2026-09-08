# CJ Connect — store-session authority failure

**Reported:** an owner of *M&W Store* (approved, open for orders) walked
`Store → M&W Store → Dropshipping → Connect supplier → Connect CJ Dropshipping`,
pasted a CJ API key, tapped **Connect to CJ**, and read:

> You're not signed in to this store anymore.

They were signed in. The store was theirs. CJ was never contacted.

This was not a CJ problem, and not a session problem. It was three separate
defects stacked so that the first one's cause was erased by the second and
guessed at by the third.

---

## 1. The request path, as measured

```
ConnectSupplierScreen ("Connect to CJ")
  → discoverSupplierShops()            mobile-native/src/api/dropshipping.ts
  → pulseApi POST /api/business-os/suppliers/cj/discover-shops
      Cookie: session
      Authorization: Bearer <mobile access token>
      X-PulseSoc-Platform: ios
      (no X-CSRF-Token — the app has none)
  → services/business_os_supplier_routes.py::_request_context(write=True)
      _bot().api_account_user()        → resolves, user is signed in
      _csrf_ok()                       → False          ← DEFECT 1
      raise SupplierError("csrf", 403)
  → _error(exc)                        → {"ok": false, "code": "csrf"}   ← DEFECT 2
  → PulseApiError(status=403, code=undefined)
  → stateForError()                    → "UNAUTHORIZED"                  ← DEFECT 3
  → "You're not signed in to this store any more."
```

`connections.discover_shops` was never reached. Neither was the ownership
lookup, the seller record, or the CJ adapter. The refusal happened three frames
before any of that.

### Why reads worked and writes did not

Every Business OS **read** the merchant made on the way to this screen
succeeded — the store loaded, the dropshipping hub loaded, the supplier list
loaded. `_request_context(write=True)` is the only path that consults
`_csrf_ok()`. So the merchant arrived at a screen that had proved four times
over that they were signed in, and was then told they were not.

`pulseApi` refreshes its token on 401 and retries. This was a 403, so the
refusal was permanent for the life of the install.

---

## 2. Defect 1 — the native write gate never opened

`_csrf_ok()` accepted a native request on one condition:

```python
if getattr(g, "mobile_access_user_id", None):
    return True
```

`g.mobile_access_user_id` is set inside `bot.account_user_id()`, in its bearer
branch. That function is:

```python
session.get("account_user_id")
    or account_user_id_from_mobile_access_token()
    or restore_account_from_persistent_cookie()
```

The native app sends a session cookie *and* a bearer. The cookie satisfies the
first term, so the `or` short-circuits and the bearer branch never runs. The
flag is therefore never set for the app — not for a broken install, not for an
expired token, but for every native request that has ever been made.

The app also has no CSRF token to echo (it is a token minted for the web
console's session). So the gate had no branch left that could pass, and every
Business OS write from the app was refused.

**Fix.** `_verified_bearer_write_authority()` re-runs the real verifier —
`account_user_id_from_mobile_access_token()`, which checks signature, expiry,
device hash and an active non-revoked `mobile_security_sessions` row — and
accepts the write when it names the same user the cookie does.

Authority comes from the database, never from the client asserting it. The
properties that had to survive, and are now asserted:

| Property | Test |
| --- | --- |
| A forged bearer is still refused | `test_a_forged_bearer_does_not_pass_the_write_gate` |
| A valid bearer for a *different* user than the cookie is refused | `test_a_valid_bearer_for_a_different_user_than_the_cookie_is_refused` |
| A cookie-only web write still needs its CSRF token | `test_a_cookie_only_web_write_still_needs_a_csrf_token` |
| A missing verifier denies rather than admits | `test_the_gate_fails_closed_when_the_bearer_verifier_is_unavailable` |
| A raising verifier denies rather than admits | `test_a_raising_bearer_verifier_denies_rather_than_admits` |
| The verifier the gate depends on still exists in `bot` | `test_bot_still_exposes_the_verifier_the_gate_asks_for` |

The last one is there because this gate is now coupled to a function it reaches
by name. A rename in `bot.py` would otherwise fail the gate closed silently and
reproduce this bug exactly.

---

## 3. Defect 2 — the cause was dropped in transit

The supplier pack answered rejections as:

```python
return _respond({"ok": False, "code": code}, status, ...)
```

`pulseApi` reads `data.error_code`, falling back to `data.error`. It does not
read `data.code`. So every rejection this pack produced reached the client with
**no code at all** — `csrf`, `login_required`, `store_not_approved`,
`forbidden`, all of them indistinguishable from one another and from a bare 403.

**Fix.** `_error` now emits both fields. `_error` is the single funnel every
rejection in the pack leaves through, so
`test_every_rejection_carries_the_field_the_client_reads` pins it there; the
store-authority tests below each assert `error_code` on their own refusal, so
the funnel is covered by its callers as well as directly.

---

## 4. Defect 3 — with no cause, the client guessed

`stateForError` fell through to:

```ts
if (error.status === 401 || error.status === 403) return "UNAUTHORIZED";
```

and `UNAUTHORIZED` renders "You're not signed in to this store any more."

That guess is defensible for a server that says nothing. It is wrong for four
different things that the server *can* say, and it is the worst possible guess
for this one: it sends the merchant to sign in, which they do, and they land
back on the identical screen.

---

## 5. The distinctions that did not exist

§7 asks for four causes to be told apart. Three of them had no server-side
representation at all — every one of them raised the pack's generic
`not_found`/`forbidden`. They now exist:

| Cause | Code | Status | Raised when |
| --- | --- | --- | --- |
| Authentication | `login_required` | 401 | No authenticated caller |
| Write not proven | `csrf` | 403 | Cookie write with no valid token, no verified bearer |
| Store not the caller's / no seller row | `store_not_found` | 404 | `verify_seller_scope` ownership mismatch, or no `marketplace_sellers` row |
| Selling access withdrawn | `store_access_revoked` | 403 | Seller status in `suspended`, `rejected`, `banned`, `closed` |
| Not yet approved | `store_not_approved` | 403 | Seller status is anything else that is not `approved` |
| Client's store context moved on | `stale_store_context` | 409 | Scope names the caller, but their canonical store has changed |
| No merchant identity | `merchant_identity_unresolved` | 409 | Signed in, no seller record and no Business OS workspace |
| Role cannot connect suppliers | `forbidden` | 403 | Membership present, capability absent |

### `store_not_found` is deliberately not `not_found`

`not_found` is the pack's generic code — a missing connection, a missing draft,
a missing cart item, roughly a hundred existing assertions. Mapping it to a
store failure client-side would have told merchants "we couldn't match this
store to your account" whenever a row they had deleted was gone. Only the three
store-authority raises were renamed.

### The rename leaks nothing

`verify_seller_scope` checks ownership *before* the seller row is read. So the
ownership branch and the missing-row branch in `seller_merchant` are never both
reachable from one request, and giving them the identical `store_not_found`
keeps store existence unobservable to a caller probing someone else's scope.
Pinned by `test_a_store_the_caller_does_not_own_is_refused_and_reveals_nothing`.

### Staleness names nobody else's store

`_stale_scope_denial` is only ever consulted for a scope that names the caller
themselves. A request carrying another merchant's scope keeps its 404 and
learns nothing about whether that store exists or has moved. The scope that was
sent is still refused either way — the helper only names the refusal. Pinned by
`test_staleness_is_never_reported_for_another_merchants_scope`.

---

## 6. What the merchant now reads

| State | Sentence | Retry offered |
| --- | --- | --- |
| `SESSION_EXPIRED` | Your session has expired. Sign in again to connect a supplier. | Sign in |
| `CSRF_INVALID` | This device couldn't prove the request came from you. Nothing was sent to your supplier — try again. | Try again |
| `STALE_STORE_CONTEXT` | Your store details moved on while this screen was open. We've refreshed them — try again. | Try again |
| `STORE_NOT_FOUND` | We couldn't match this store to your account. | — |
| `STORE_ACCESS_REVOKED` | This store can no longer sell, so it can't connect a supplier. | — |
| `STORE_NOT_APPROVED` | Your store isn't approved to sell yet, so it can't connect a supplier. | — |
| `STORE_MAPPING_MISSING` | Your store isn't linked to a seller account yet, so there's nothing to connect a supplier to. | — |
| `SUPPLIER_CONNECTION_FORBIDDEN` | Your role in this store can't connect suppliers. | — |

No retry is offered where a second identical request would fail identically.

**§10 — repair without a restart.** `STALE_STORE_CONTEXT` calls
`scopeStatus.reload()`, which drops the module-scope scope cache and
re-resolves. The merchant presses the button they were already looking at. They
are not sent to sign in for a session that was never the problem, and they do
not have to restart the app.

---

## 7. What did not change

- **No second store.** No Business OS business, no storefront row, no duplicate
  M&W Store, no seeded `business_os_mkt_products`, no new merchant identity.
  The canonical mapping is the merchant's existing `marketplace_sellers` record,
  read — not created. Pinned by
  `test_the_canonical_store_mapping_is_the_seller_record_not_a_new_business`.
- **No authorization weakened.** Every refusal that was correct before is still
  a refusal, with the same status. What changed is that correct refusals now say
  which refusal they are. Cross-tenant access, revoked sellers and unapproved
  sellers are all still denied.
- **No provider call before local authorization** (§12, §14). A local
  authorization failure persists no credential and contacts no supplier. The
  test installs an adapter that raises `AssertionError` on `authenticate`;
  `test_no_provider_call_and_no_credential_write_before_store_authorization`
  fails loudly if that ordering ever inverts.
- **No secret logged or echoed** (§13). `test_a_refused_request_never_echoes_the_key_it_was_given`.
- **No real-time audio path touched.** Nothing in this diff matches
  `config/realtime-audio-protected-paths.json`; `bot.py` is untouched, and so
  are `package.json` and the lockfile.

---

## 8. Verification

| Suite | Result |
| --- | --- |
| `tests/business_os/test_cj_store_session_authority.py` | 23 passed |
| `test_cj_adapter` + `test_cj_connections` + `test_cj_routes` | 125 passed |
| `tests/dropshipping/` (one file per process) | 265 passed — matches the recorded green baseline |
| `test_commerce_console_page`, `test_commerce_gateway`, remaining `test_cj_*` | 157 passed |
| `mobile-native` `npm run verify` | 371 suites, 6325 passed (baseline 6302) |

---

## 9. Expected next failure

For a legitimate M&W Store owner the store-authority message should now be
gone. What remains is CJ's own answer: `INVALID_CJ_API_KEY`, `CJ_UNAVAILABLE`,
`CJ_ACCOUNT_VERIFIED`, `SHOP_SELECTION_REQUIRED`, or `CONNECTED`. A store
message reappearing after this means a real store condition, and the code now
says which one.
