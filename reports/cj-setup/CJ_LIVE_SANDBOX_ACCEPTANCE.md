# PULSESOC CJ LIVE SANDBOX + IMPORT ACCEPTANCE

## FINAL VERDICT: PARTIAL

Observed 2026-09-07, approximately 18:53–18:57 UTC. The complete authorized
support request is verified in Sent mail. Affirmative CJ approval has not been
received. No merchant credential ingestion, live CJ API acceptance, provider
order, or catalog-to-Marketplace implementation was attempted in this
continuation. The user explicitly requires approval before those actions.

## CJ APPROVAL

- Support request sent: YES — sender `support@pulsesoc.com`, recipient
  `developer@cjdropshipping.com`, mailbox date September 7, 2026, 11:46 AM.
- Subject: `Re: PulseSoc: permission for one merchant's hosted CJ sandbox integration`.
- Official channel verified against [CJ Developer Contact](https://developers.cjdropshipping.com/en/about.html).
- Evidence: opened the corrected message in the authorized Chrome business-mail
  Sent folder and verified all eight questions, one-merchant scope, and safety
  limitations. The original was signature-only; the corrected reply includes
  the full body. No additional duplicate was sent in this verification turn.
- Sent-mail locator: `https://privateemail.com/spm/mail/?f=Sent&search=cjdropshipping&m=6`.
  This is account-local evidence, not a CJ ticket or provider acknowledgment.
- Response: no matching CJ response visible in Inbox or Spam searches. No claim
  of remote mailbox receipt, human review, or contractual permission.
- Hosted API-key custody: PENDING.
- openId custody: PENDING.
- Shared backend use: PENDING.
- Three-users/IP: PENDING provider clarification.
- Production-scale status: `OPEN_RELEASE_BLOCKING_FOR_MULTI_MERCHANT_SCALE`.
- `CJ_HOSTED_CREDENTIALS_APPROVED`: OFF, verified from staging-only configuration
  and public health. A successful infrastructure test is not provider approval.

## APPROVAL SCOPE TO CHECK BEFORE ENABLING

The existing approval flag is environment-wide. Permission for one merchant
must be restricted to the explicitly authorized staging identity/account; it
does not authorize a general multi-merchant rollout. No flag was changed.

The sent request follows the user's exact eight questions, including retaining
openId solely for webhook HMAC. The existing implementation additionally uses
openId for account identity and refresh-stability checks, and derives a keyed
opaque account index from it (`connections.py:151–155, 160–174, 202–210`).
Do not represent that implementation as HMAC-only. Before ingestion, the actual
use must fit the received permission, or the implementation must be narrowed
without weakening account identity/refresh fencing. No broader request was
sent and no permission was inferred.

## EXISTING STAGING RECHECK — NO REBUILD

Backend health returned HTTP 200:

- Deployed backend SHA: `e4f2b52e480babf676fcccf0e9e3456d16c13ed0`.
- Infrastructure ready, PostgreSQL, supplier schema, vault: true.
- Failed route packs: empty.
- Worker heartbeat: observed, sequence 23, age 43 seconds, state `deferred`.
  Deferral is expected with provider approval OFF; it is not live CJ acceptance.
- `live_cj_accepted`: false.
- Provider approval, production fulfillment, real funding: false.
- Health: <https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app/health/cj-staging>.

`scripts/cj_staging_railway.py verify` also returned configured matching
service keyrings, staging-only database reference, no global CJ key, and
approval/funding/production fulfillment false. Secret values were not printed.
No Railway variables, deployments, domains, databases, or services were changed.
Prior PostgreSQL/concurrency/rotation/restart evidence is recorded in
`CJ_RAILWAY_STAGING_ACCEPTANCE.md`; those full suites were not rerun here.

## MERCHANT

- User/store/CJ account: not selected for live acceptance; no production customer
  data was copied or queried for this continuation.
- API entry: not created; no key acquired.
- Connection status: not connected; secure ingestion remains gated.

## AUTH

Access token, refresh, settings, shops, connection health: NOT RUN against CJ.
No real token expiry or stable-account proof is claimed.

## CATALOG

Categories, search, details, variants, inventory, warehouses: NOT RUN against CJ.
No real payload compatibility or inventory freshness is claimed.

## SHIPPING

Quote, route, currency, fees, restrictions, transit estimate: NOT RUN against CJ.
No guaranteed delivery or landed-cost claim.

## SANDBOX ORDER

- Created: NO — zero provider create requests in this continuation.
- isSandbox: caller/adapter guards retained; no real provider response proof.
- Readback: NOT RUN against CJ.
- Real balance deducted: $0 caused by this mission; no provider balance read was
  performed, so unchanged-balance acceptance remains unproven.
- Real shipment/production order: none created by this mission.
- Unknown-write recovery: prior local/staging synthetic evidence retained;
  real provider timeout-after-success acceptance NOT RUN.

## WEBHOOK

Delivery, live signature, dedup, ACK timing, readback: NOT RUN with a real CJ
connection. No callback was configured and no sandbox-delivery limitation was
asserted without provider evidence. No direct canonical payment mutation.

## QUOTA / EGRESS

No real pointsInfo/QPS/Retry-After observation in this continuation. No quota
exhaustion probe or proxy/IP evasion. Existing staging shared-egress evidence
is retained; multi-merchant policy remains a production-scale blocker.

## IMPORT

- Required canonical ledger: `marketplace_listings`.
- Required supplier mapping: `marketplace_product_sources`.
- Live CJ search/import bridge: NOT IMPLEMENTED; approval and live catalog
  normalization prerequisites have not passed.
- Draft, variants, media, private cost, inventory, shipping, fulfillment mapping,
  duplicate prevention, merchant override preservation: NOT ACCEPTED for real CJ.
- No second `business_os_mkt_products` product ledger was created or seeded.
- No auto-publication or live CJ calls from Marketplace cards were added.
- Concurrent local main `b32cc2bed3d08611fe2efb8fc9c7f9addb1e0be8` and dirty supplier,
  schema, variants, and test work were observed and left untouched. No merge or
  cherry-pick occurred merely to proceed with this approval request.

## SECURITY / EXECUTED CHECKS

- Read-only guard audit: real transport calls require provider approval before
  HTTP; connect/discover cannot vault-save a credential before successful
  auth/settings/owned-shop verification; worker ticks gate before claiming work.
- In-memory policy/transport checks: disabled approval/network combinations
  blocked before HTTP; forbidden funding endpoint remained disallowed even with
  approval/network true; funding/subscription/webhook-config stubs remained
  locked. Zero live provider HTTP calls from these checks.
- API `payBalanceV2` is excluded from the transport allowlist.
- `REAL_CJ_FUNDING_ENABLED` and `CJ_SUBSCRIPTION_MUTATIONS_ENABLED` remain OFF;
  corresponding production mutations remain hard-disabled in code.
- No raw credentials, openId values, customer data, or secret screenshots were
  included in the support request or this report. No secrets were acquired.
- No new Sentry/UNDX/live cross-merchant acceptance is claimed. Prior security
  and mutation results are historical; no full repository regression was run
  for this documentation-only continuation.

## PRODUCTION / APP STORE / RTC / BILLING

Production changed: NO. Production deployment: NO. CJ fulfillment: OFF.
Real funding caused by this mission: $0. Production orders created: 0.
New App Store build/upload: NO. Agora/audio/calls/live/camera/mic changes: 0.
New infrastructure/services: 0. Nothing deleted. Existing staging remains intact.

## FILES / GIT

Acceptance branch: `codex/cj-staging-acceptance`.
Starting HEAD: `331b7e5229c3ac02f9553f06de5c7763276ab911`.
Only documentation is changed:

- `reports/cj-setup/CJ_SUPPORT_REQUEST_DRAFT.md`
- `reports/cj-setup/CJ_RAILWAY_STAGING_ACCEPTANCE.md`
- `reports/cj-setup/CJ_LIVE_SANDBOX_ACCEPTANCE.md`

The resulting local evidence commit is reported in the task handoff. No push,
merge to main, deployment, or unrelated file staging was performed.

## BLOCKERS / NEXT ACTION

1. Await affirmative CJ permission covering the one-merchant hosted model and
   actual openId use. Sending the request does not close this gate.
2. Once permitted, bind exactly one explicitly authorized non-production
   merchant/store/account and securely ingest its credential. Scope enforcement
   must match the provider response before the environment-wide flag is enabled.
3. Then run bounded live auth/catalog/shipping, exactly one sandbox order with
   same-order unknown-response recovery, and actual webhook acceptance.
4. Only after live catalog normalization passes, deliberately reconcile the
   concurrent canonical Marketplace source/listing work and connect draft import.

These downstream acceptance steps remain unexecuted dependencies, not a reason
to rebuild the proven staging foundation or waive provider authorization.
