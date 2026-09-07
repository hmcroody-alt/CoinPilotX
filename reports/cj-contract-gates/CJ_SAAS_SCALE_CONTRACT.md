# CJ hosted SaaS and scale contract

Checked 2026-09-07. **G1 and G11: OPEN_RELEASE_BLOCKING.** Decision: NO-GO for connected multi-merchant PulseSoc CJ integration under the currently established permissions. This is an engineering contract gate, not a conclusion that CJ can never approve PulseSoc.

## Hosted authorization evidence

| Question | Official evidence | Finding |
| --- | --- | --- |
| Can a merchant obtain an API key? | API app installation and Add API account flow | Yes, technical account capability; not permission for PulseSoc custody |
| May another entity use account information/permissions? | User Agreement III.2 requires CJ consent for third-party availability/joint use; III.6 restricts outside-business account/ID/password use | No applicable PulseSoc consent or exception established |
| May PulseSoc hold openId? | Webhook notice explicitly warns against disclosure to integration providers | Requires explicit resolution for hosted verification, not merchant consent alone |
| Are external platform integrations supported? | Store Authorization Agreement describes CJ accessing authorized store interfaces and returning order/product data | Yes; does not itself document merchant CJ key/openId delegation to an arbitrary SaaS |
| Is there a partner program? | Current first-party Partner Network and application page | Yes, application route exists; listing/marketing partnership is not automatically API approval |
| Public partner OAuth? | Token guide says Auth2.0; public auth gives API-key exchange; optional platformToken appears in order docs | No deployable authorize URL/client registration/scopes/consent/callback contract established |
| Many merchants behind one backend? | Frequency docs explicitly cap each IP at 3 users | Ordinary shared egress is not proven suitable; no published SaaS exemption found |
| Multiple stores per CJ account? | getShops and documented store management | Supported; maximum API shops not published in reviewed material |

Sources: [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [User Agreement](https://cjdropshipping.com/user-agreement) (updated 2025-09-26, effective 2025-10-04), [Store Authorization Agreement](https://cjdropshipping.com/storeAuthorizationAgreement), [webhook notice](https://developers.cjdropshipping.com/en/api/start/webhook.html), [Partner Network](https://cjdropshipping.com/partnership/home), [partner application](https://cjdropshipping.com/partnership-add.html), [limits](https://developers.cjdropshipping.com/en/api/start/limit.html), [Shop](https://developers.cjdropshipping.com/en/api/api2/api/shop.html).

Do not conflate CJ authorization to access a merchant's sales platform with PulseSoc authorization to access the merchant's CJ account. Existing supported-platform OAuth plugins and affiliate programs are not general-purpose account delegation evidence. No application, consent, login, support request or authenticated account operation was performed.

## Legitimate architecture decision

Prefer Model B only after CJ approves it: merchant-owned CJ accounts, encrypted per-merchant credentials, account-aware workers and explicit merchant supplier funding. Model A central account changes liability, balances and isolation; it is not an escape from consent or quotas. Model C adds content-sharing and account-pricing constraints; defer it.

Acceptable routes to evaluate with CJ:

- Written SaaS/partner approval covering credential custody, sandbox use and named backend egress.
- A documented delegated platformToken/OAuth contract with required consent and revocation flows.
- CJ-approved dedicated worker/account groupings with explicit quota scope, stable egress and capacity agreement.
- Authorized caching and batching to reduce requests; private inventory, account pricing, orders and recipient data never pooled.

Dedicated IPs, regional workers, proxy rotation or credential multiplexing are not an approved workaround. Do not procure or rotate egress to bypass the 3-user control. A merchant-owned test account still needs authorization appropriate to this hosted use; changing the account label to test does not waive provider terms.

## Effective quota contract

| Boundary | Documented rule | Enforcement proposal |
| --- | --- | --- |
| IP | 10 requests/s and 3 CJ users/IP | Approved-egress limiter and approved account registry; reject unauthorized account admission |
| Non-login | 30/s; aggregate interaction unclear | Intersect with known stricter constraints; ask CJ |
| Account | Free/levels 0–1: 1/s; Plus/2: 2/s; Prime/3: 4/s; Advanced/4–5: 6/s | One coordinated limiter per account, not per API key or shop |
| Endpoints | Token 1/s, Ticket 1/s, batch detail/proof 2/s/account | Apply most restrictive relevant limit |
| Points | 50,000/day base plus conversion; per-minute total/1440 replenishment | Use current pointsInfo and reserve recovery capacity; no assumed full bucket at startup |
| Activity | 30 zero-transaction days suspend API; reactivation in CJ account UI | auth/access_suspended state, not infinite refresh/retry |
| Product events | 1,000/2,000/3,000/5,000/10,000 caps for levels 1–5; 100 IDs/write | Not currently enabled; add/replace/account/shop semantics unresolved |

Sources: [limits](https://developers.cjdropshipping.com/en/api/start/limit.html), [points](https://developers.cjdropshipping.com/en/api/api2/standard/points.html), [webhooks](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html). Points are documented per user; multiple keys/tokens/shops cannot be assumed to multiply the budget. Settings may further constrain actual accounts.

## Retained scale model, recomputed

Reuse the discovery assumptions, not a new endpoint map: 100 active products/merchant, 10 searches, 20 product reads, 10 variant reads, 200 inventory reads, 10 quotes, 1 subscription batch, 5 orders × 8 calls, and 13 health/reconciliation calls/day. This is **304 calls and 2,910 points per merchant/day**. It includes currently-disabled operations for comparison; it is not the currently authorized workload or a capacity commitment.

| Merchants | Calls/day | Mean requests/s | Aggregate points/day | Points/base central account | Direct shared backend viability |
| ---: | ---: | ---: | ---: | ---: | --- |
| 100 | 30,400 | 0.352 | 291,000 | 5.82× | Not established; 3-users/IP and custody block |
| 1,000 | 304,000 | 3.519 | 2,910,000 | 58.2× | Not established; same blockers |
| 10,000 | 3,040,000 | 35.185 | 29,100,000 | 582× | Not established; also aggregate capacity planning |
| 100,000 | 30,400,000 | 351.852 | 291,000,000 | 5,820× | Not established; requires explicit provider agreement and measured capacity |

Per-merchant points average 5.82% of the base allocation, but averages do not prove burst throughput. If subscriptions are disabled, 1 write and 10 points may be removed; replacement polling must be budgeted separately. Polling 1,000 variants hourly alone consumes 240,000 points/day. A webhook-free fallback therefore means a bounded pilot with strict stale-data refusal, not unchanged large-scale performance.

At base replenishment, 10 points accrue in 17.28s, 50 in 86.4s and 1,000 in 28.8min from an empty bucket. A cold import of 100 details + 100 inventory reads + one subscription batch costs 2,010 points and 201 calls: at least 201s at 1 QPS, or about 57.9min if the points bucket starts empty. Real timing may be worse. No load tests occurred.

## Client isolation and backpressure

Mobile → PulseSoc API → server authorization → canonical commerce service → Supplier Gateway → bounded cache/queue → CJ adapter. No per-visible-card provider fetches. No raw secrets, openId or provider quota internals in mobile/UNDX responses.

Use tenant-scoped cache keys, coalesced reads, account-scoped credential locks and points/QPS admission. Prioritize unknown outcomes and existing obligations over search. Expired inventory/quotes fail closed; lack of quota cannot turn unknown stock into available stock. Recovery reserve, job deadlines and retry ceilings must be explicit pilot configuration. Propagate normalized freshness/action-required states, not fake success.

## Closure evidence required

Obtain written CJ answers to S1–S10 and R1–R2 in [provider questions](CJ_PROVIDER_QUESTIONS.md), including policy version/applicable entity, credential and signing-key custody, consent method, worker/IP-account scope, limits, shop maxima, supported test account and remediation contact. Then validate account settings and approved pilot capacity without real orders or charges. A generic sales assurance that CJ supports APIs is insufficient.
