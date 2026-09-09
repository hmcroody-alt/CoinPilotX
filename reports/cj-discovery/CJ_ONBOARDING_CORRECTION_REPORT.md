# PulseSoc CJ API Onboarding + Live Connection Flow Correction

**Date:** 2026-09-07 · **Commit:** `f8ac5a78` · **Branch:** `main` (committed, **not pushed**)

---

## 1. CJ documentation baseline

Read at <https://developers.cjdropshipping.com/en/api/api2/> and its sub-pages.

| Item | Value |
|---|---|
| Version | API **V2.0** (CJ's recommended version) |
| Base URL | `https://developers.cjdropshipping.com/api2.0/v1` |
| Categories | Authentication, Settings, Product (+ variants, stock), Warehouse, Shopping, Logistics, Dispute, Webhook, Shop, Ticket, MCP |
| Get token | `POST authentication/getAccessToken` — request is **`apiKey` only** |
| Token response | `accessToken`, `refreshToken`, `openId`, `accessTokenExpiryDate`, `refreshTokenExpiryDate`, `createDate` |
| Refresh | `POST authentication/refreshAccessToken` — request is `refreshToken` |
| Lifetimes | Access **15 days**, refresh **180 days** |
| Token QPS | 1/sec. Repeat calls within 24h return the **same** tokens |
| Rate limits | 10 req/s per IP; max 3 users per IP; per-account QPS 1 / 2 / 4 / 6 (Free / Plus / Prime / Advanced) |
| Quota model | Legacy daily call limit replaced by a **points-based** daily quota |

**Merchant key path, as CJ documents it** (verbatim control names): install the **API** app
under **Apps** → open the **API** tab → **Add API** → enter an **API Key Name**, set **Type**
to **API Key** → **Confirm** → copy from the **API Key & MCP Token** column.

---

## 2. What was wrong

The connect screen asked for a **"Supplier access key"** and, behind a "Where do I find this?"
toggle, told merchants:

> Account → API
> "Open your account settings and find the access section below."
> "Create a new access key there, then copy the whole thing."

CJ has no "Account → API" menu, no "access section", and no control called "create a new access
key". Every one of those was composed rather than read off CJ's product. A merchant following
it is searching CJ's site for words that do not appear on it.

---

## 3. What changed

All of it in the mobile client. **No backend change was required** — see §4.

| § | Requirement | Change |
|---|---|---|
| 1 | CJ-specific terminology | Field label and placeholder are now **"CJ API key"** / "Paste your CJ API key". Rejection copy names the credential too. Generic layer keeps its internal vocabulary |
| 2 | Documentation-safe help | The five steps quote CJ's own controls: `Apps`, the `API` app, `Add API`, `Type` → `API Key`, copy |
| 2 | Security line | "PulseSoc encrypts your CJ API key and never keeps it on this device. It is used only to connect your CJ account." |
| 3 | Sandbox stated up front | Already present and now covered by a test — the sandbox card renders before the credential step |
| 4 | CTA | **"Find my shops" → "Connect to CJ"**. The old label described the backend's next call, using CJ's word for a thing the merchant had not been shown |
| 22 | Success state | Unchanged: the flow still ends on the shop choice and navigates to Suppliers |

The provider record now carries `credentialName`, `helpSteps`, `securityNote` and `connectCta`,
so a second supplier is a data change rather than a screen rewrite.

### Deviation from the brief, and why

§2 specified the security line as "…uses it only from the **backend** to connect your CJ
account." The app's own copy guard (`src/__tests__/userFacingCopy.test.ts`) bans the word
"backend" from user-facing strings. The guarantee is preserved in merchant terms — encrypted,
never on the device — without the jargon the guard exists to keep out.

The same guard bans "API". Here the word is CJ's product name, not our vocabulary, and
paraphrasing it would recreate the exact failure this mission is about. An **exact-string**
exemption list (`EXTERNAL_VOCABULARY`) carries the five sentences, and a new test asserts each
entry is both genuinely banned and genuinely rendered, so the list cannot quietly accumulate
copy or keep dead entries.

---

## 4. Backend conformance — verified, not assumed

| § | Requirement | Finding |
|---|---|---|
| 5–7 | V2.0 auth contract | `cj.py:280` sends exactly `{"apiKey": …}`; `cj.py:286` sends `{"refreshToken": …}`. Matches CJ's documented request bodies |
| 6 | Identity validated | `connections._verify:227-229` requires `setting/get`'s `openId` to equal the token bundle's, else `identity_mismatch`. First auth without an `openId` is refused (`ACCOUNT_IDENTITY_UNRESOLVED`) |
| 7 | "Key exists" ≠ "healthy" | `health_connection` → `adapter_for` → `_hydrate`, which refreshes inside the expiry window and re-runs `_verify` against CJ live on every hydrate. A stored credential is never read as health |
| 8 | Shop binding | ~~Selection is explicit and mandatory (`shop_required`)~~ **superseded 2026-09-09, see note below**; the chosen shop is re-checked against the live active list, so Merchant A cannot bind Merchant B's shop |
| 12 | No V2→V3 rewrite | `createOrderV2` untouched |
| 13 | Payment locked | `fund_fulfillment` and its aliases refuse unconditionally; `payBalance`/`payBalanceV2` are not in the allowlist |
| 14 | Sandbox at both layers | `policy.require_sandbox` checks env mode, the production flag, and a literal `isSandbox == 1`, independently of the adapter |
| 18–19 | Credential security | AES-256-GCM vault with AAD binding merchant/business/store/connection, no plaintext fallback; responses recursively stripped of `accesstoken`/`refreshtoken`/`openid` |
| 20 | Connection states | 8 distinct states, not a boolean |

**No backend file was modified by this mission.** `cj.py` and `connections.py` are byte-identical
to their pre-mission state.

> **Superseding note, 2026-09-09 — row 8 only.** Requiring a shop to connect was
> wrong, and the finding above records it as a control. A CJ "shop" is an
> external storefront (Shopify, Woo) authorized inside the merchant's CJ
> account; PulseSoc *is* the storefront, so a merchant selling only here owns
> none and `shop/getShops` returns nothing or refuses. The requirement made
> those merchants' correct accounts unusable and reported it as a bad API key.
>
> `connect_cj` now accepts a shopless connection and stores `""`. What row 8
> describes as the anti-cross-binding control — re-checking a selected shop
> against the live active list — is unchanged and still enforced, and an
> unreadable shop list remains fatal *when a shop was selected*. Fulfillment
> refuses with `SHOP_BINDING_REQUIRED`: a shop is required to ship, not to
> connect. Commit `ff4325eb`; the mutation guarding it is
> `test_a_shop_list_we_cannot_read_is_fatal_when_a_shop_was_selected`.
>
> The rest of this report stands. It is left unedited on purpose.

---

## 5. Verification

| Check | Result |
|---|---|
| Mobile `npm run verify` (typecheck + i18n + jest) | **6302 passed**, 371 suites, 0 failed |
| Dropshipping screen suite | 60 passed (5 new tests for §1–§4) |
| `tests/business_os/test_cj_adapter.py` + `test_cj_connections.py` | 103 passed |
| `tests/dropshipping/` (one file per process) | 265 passed |
| Real-time audio gate | "No protected real-time audio path changed" — 8 files inspected |
| Mutants | **8 / 8 killed** |

### Mutants (`scripts/cj_onboarding_mutants.sh`)

| # | Reversed guarantee | Killed by |
|---|---|---|
| 1 | Credential renamed back to "Supplier access key" | terminology test |
| 2 | "Account → API" reintroduced into the help | help-copy test |
| 3 | Security note removed | security test |
| 4 | CTA reverted to "Find my shops" | CTA test |
| 5 | Credential field unmasked | key-never-rendered test |
| 6 | `getAccessToken` sends the wrong parameter name | adapter contract test |
| 7 | `setting/get` identity fetched but not compared | connections test |
| 8 | Hydrate stops verifying against CJ | connections test |

The harness restores every file from a byte copy on exit, and treats a `-t` filter that selects
zero tests as a hard error — mutant 3 initially "passed" that way after a test rename, which is
precisely the silent disarming the check exists to catch.

---

## 6. Maintenance (§15, §16, §17)

`reports/cj-discovery/CJ_API_MAINTENANCE_POLICY.md` is new. It carries:

- The governing rule: **CJ documenting something is not PulseSoc doing it.** A monthly review
  produces a diff, not a deployment; newly documented write endpoints are recorded and left
  unimplemented until separately approved.
- A 10-point monthly checklist (endpoints, fields, error codes, points rules, rate limits,
  token lifetimes, webhooks, sandbox semantics, MCP tools, and — the one that rots silently —
  whether CJ still calls its buttons what our help copy says it does).
- The full contract inventory: all 17 allowlisted paths by category, what we send and read, and
  an explicit list of what CJ documents that we deliberately do not implement.
- A change log, with the 2026-09-07 baseline recorded.

UNDX is not connected to merchant CJ credentials, and the policy states it as a standing rule.

---

## 7. Deployment

| Constraint | State |
|---|---|
| §29 RTC changes | **0** — audio gate confirms no protected path touched |
| §30 App Store | No build, archive, upload or submission |
| §31 Push | **Not pushed.** Commit `f8ac5a78` is local on `main` |
| Both devices | P3r7or (device) and the iPhone simulator both rebuilt and installed |

Device install verified by **bundle content**, not by install success: `main.jsbundle` on
P3r7or contains "Connect to CJ", "Under Apps, install the API app" and "never keeps it on this
device", and contains **zero** occurrences of "Account → API", "Supplier access key" or
"Find my shops".

---

## 8. What is still not verified

**The live CJ dashboard has never been seen.** Every statement above about CJ's UI comes from
CJ's published API documentation. Claude in Chrome is not installed in this Chrome
(`list_connected_browsers` returns `[]`), and computer-use grants browsers at tier "read" only,
so the browser cannot be driven. The earlier forensic mission's demand for a verified click
path through the merchant's own logged-in CJ account therefore remains **open**.

Also outstanding, carried from prior missions:

- No real CJ credential has ever been exercised. **No LIVE CJ AUTH PASS is claimed** (§26).
  `CJ_HOSTED_CREDENTIALS_APPROVED` and `CJ_NETWORK_ENABLED` remain unset.
- The shop-selection round trip is jest-only; it has never run against CJ's sandbox.
- Dropshipping screens use hardcoded English rather than i18n keys.
- `connections._row` hardcodes `provider='CJ'`, which the second adapter will have to unpick.
