# CJ API Maintenance Policy

CJ publishes API V2.0 at <https://developers.cjdropshipping.com/en/api/api2/> and changes it
without notifying integrators. This document exists so that reading their documentation is a
scheduled activity with a defined output, rather than something that happens after a merchant
reports a broken store.

**Scope.** This policy governs `services/business_os/suppliers/cj.py` — the only module in
PulseSoc permitted to make CJ HTTP calls — and the connection lifecycle in
`services/business_os/suppliers/connections.py`.

---

## The rule this document exists to enforce

**CJ documenting something is not PulseSoc doing it.**

A monthly review produces a *diff*, not a deployment. Newly documented endpoints — especially
write endpoints, payment endpoints, and anything that moves money or dispatches goods — are
recorded here and left unimplemented until they are separately specified, reviewed and
approved. There is no such thing as adopting a CJ endpoint because it appeared in their docs.

Corollaries, each of which has its own guard in code:

- No endpoint may be called that is not in the `PATHS` allowlist (`cj.py:22`). Adding a path
  is a reviewed code change, not configuration.
- Funding and payment calls stay refused unconditionally (`cj.py`, `fund_fulfillment` and its
  aliases; `policy.require_funding_disabled`). CJ documenting a friendlier payment API does
  not change this.
- Fulfilment stays sandbox-enforced at both the caller and the adapter
  (`policy.require_sandbox`), independently. Neither layer may be relaxed on the grounds that
  the other one checks.
- `createOrderV2` is not to be opportunistically migrated to V3. A version bump on CJ's side
  is a scheduled, tested migration or it is nothing.
- UNDX is never given merchant CJ credentials. The AI layer has no read path to the vault.

---

## Monthly review checklist

Run on the first working day of each month. Record the outcome in the change log at the
bottom of this file, including "no changes" months — an empty result is only meaningful if
the absence of an entry is unambiguous.

| # | Check | Source | What counts as a finding |
|---|---|---|---|
| 1 | Endpoint set | [api2 index](https://developers.cjdropshipping.com/en/api/api2/) | A path we call was moved, renamed, versioned or removed |
| 2 | Request/response fields | Each endpoint page | A field we read disappeared, changed type, or a new required request field appeared |
| 3 | Error codes | Global error codes appendix | A code we branch on changed meaning, or a new terminal code exists |
| 4 | Points / quota rules | [points rules](https://developers.cjdropshipping.com/en/api/api2/standard/points.html) | Point cost per call changed, or the daily quota model changed |
| 5 | Rate limits | [limits](https://developers.cjdropshipping.com/en/api/start/limit.html) | Per-IP, per-user-level QPS, or the 3-users-per-IP cap changed |
| 6 | Token lifetimes | [get token](https://developers.cjdropshipping.com/en/api/start/token.html) | Access (15d) or refresh (180d) lifetime changed |
| 7 | Webhooks | Webhook section | A new event type, or a payload change to one we subscribe to |
| 8 | Sandbox semantics | Shopping / order pages | The meaning or requirement of `isSandbox` changed |
| 9 | MCP tool list | MCP integration section | New tools that imply new API surface |
| 10 | Merchant-facing key path | [Add API dialog](https://developers.cjdropshipping.com/en/api/api2/api/auth.html) | The control names in our help copy no longer match CJ's |

Check 10 is the one that silently rots. The merchant help copy in
`mobile-native/src/api/dropshipping.ts` quotes CJ's own control names (`Apps`, `API`,
`Add API`, `Type`, `API Key`). When CJ renames a button, our help sends merchants looking for
something that is not there, and nothing in CI can tell — the tests assert our copy is
internally consistent, not that CJ still agrees with it.

After any finding, run the adapter contract tests (`tests/dropshipping/`) and update this
file in the same commit as the code change.

---

## Contract inventory

Everything PulseSoc is allowed to call, and why. This mirrors the `PATHS` allowlist in
`cj.py:22-30`; if the two disagree, the allowlist is authoritative and this table is stale.

Base URL: `https://developers.cjdropshipping.com/api2.0/v1`

### AUTH — 2 paths

| Path | Method | We send | We read | Notes |
|---|---|---|---|---|
| `authentication/getAccessToken` | POST | `apiKey` | `accessToken`, `refreshToken`, `openId`, `accessTokenExpiryDate`, `refreshTokenExpiryDate` | QPS 1. Same account within 24h returns the *same* tokens — a second call is not a rotation |
| `authentication/refreshAccessToken` | POST | `refreshToken` | same bundle, no `openId` guarantee | Access 15d, refresh 180d |

`openId` is required on first authentication (`cj.py:269`) — a bundle without it raises
`ACCOUNT_IDENTITY_UNRESOLVED`, because a connection we cannot attribute to a CJ account is a
connection we cannot later prove belongs to this merchant.

`authentication/logout` and `getAffiliateAccessToken` are documented by CJ and deliberately
**not** in the allowlist.

### SETTINGS — 1 path

| Path | Method | Purpose |
|---|---|---|
| `setting/get` | GET | Independent identity verification and quota/QPS discovery |

This is the endpoint that makes "connected" mean something. `connections._verify` requires
the `openId` returned here to equal the one from the token bundle, and raises
`identity_mismatch` otherwise. A stored API key is never, anywhere, treated as evidence of a
healthy connection.

### SHOP — 1 path

| Path | Method | Purpose |
|---|---|---|
| `shop/getShops` | GET | The shops this credential may act for |

> **SUPERSEDED 2026-09-09.** The paragraph below was accurate when written and is
> no longer true of the first sentence. Left in place rather than rewritten, for
> the same reason the rest of this directory is: it records what was believed,
> and the reason it changed is the useful part.
>
> **What changed.** A CJ "shop" is an external storefront — Shopify, Woo —
> authorized inside the merchant's CJ account. PulseSoc *is* the storefront, so a
> merchant who sells only here legitimately owns none, and `shop/getShops`
> returns nothing or refuses outright. Requiring a shop to connect made that
> merchant's correct account unusable, and the mobile screen told them their key
> was wrong — which it was not.
>
> **Current behaviour.** `connect_cj` accepts a connection with no shop and
> records `""`. `connection_health` skips `getShops` when nothing is bound. The
> second sentence still holds and is now the whole of the control: when a shop
> *is* selected, an unreadable shop list is fatal, and the binding is re-checked
> against the live active list on every hydrate. Fulfillment fails closed with
> `SHOP_BINDING_REQUIRED` — binding is required to *ship*, not to connect.
>
> **Merchant A / Merchant B is still prevented**, and by the same check, because
> the risk was never that a shop was absent. It was that an absent one could be
> confused for someone else's, and the empty-string binding is not a shop.
> See commit `ff4325eb`.

Shop selection is explicit and mandatory (`connect_cj` refuses with `shop_required`), and the
chosen shop is re-checked against the live list on every hydrate. This is what stops Merchant
A binding Merchant B's CJ shop.

### PRODUCT — 6 paths

`product/getCategory`, `product/listV2`, `product/query`, `product/variant/query`,
`product/variant/queryByVid`, `product/stock/getInventoryByPid`.

All read-only. Provider content is an untrusted snapshot: it is normalised into PulseSoc DTOs
and never rendered raw. Stock that cannot be read is `UNKNOWN`, never `UNAVAILABLE`.

### WAREHOUSE — 1 path

`product/globalWarehouseList`. Read-only.

### LOGISTICS — 2 paths

`logistic/freightCalculateTip`, `logistic/trackInfo`. Freight results are **estimates** and
must be presented as such; they are not quotes and not commitments.

### SHOPPING — 3 paths

| Path | Status |
|---|---|
| `shopping/order/createOrderV2` | Sandbox only, `isSandbox: 1` enforced twice |
| `shopping/order/getOrderDetail` | Read-only |
| `shopping/pay/getBalance` | Read-only. Reading a balance is not spending one |

`payBalance` / `payBalanceV2` are **not** in the allowlist and must not be added.

### WEBHOOK — 1 path

`webhook/product/subscribe/list`. Read-only; listing subscriptions, not creating them.

### Documented by CJ, deliberately unimplemented

Dispute, Ticket, MCP tooling, order payment, and every write endpoint outside the sandbox
order path. Their absence is a decision, not an oversight — see the rule at the top.

---

## Change log

| Date | Reviewer | Findings |
|---|---|---|
| 2026-09-07 | Initial baseline | Documentation read at V2.0. Adapter's auth contract confirmed to match: `getAccessToken` takes `apiKey` only; `refreshAccessToken` takes `refreshToken`. Token lifetimes 15d/180d recorded. Rate limits recorded (10 req/s per IP, 3 users per IP, QPS by account level 1/2/4/6). Merchant help copy corrected to CJ's documented control names. No code change required to the adapter. |
