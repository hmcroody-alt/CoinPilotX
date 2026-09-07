# PULSESOC CJ DROPSHIPPING API DISCOVERY

**FINAL VERDICT: PARTIAL**

The relevant official documentation has been reviewed and the supplier boundary/endpoint inventory is complete for this discovery. CJ provides the catalog, inventory, quotation, order, funding, tracking and recovery capabilities needed for a scoped dropshipping integration. **Production implementation is not yet fully contract-ready:** hosted-account access, webhook credential custody, automatic-funding amount locks and several documented contradictions need CJ answers. No production readiness or authenticated runtime behavior is claimed.

## CJ API VERSION

- **CJ API VERSION:** API2.0; wire prefix `/api2.0/v1/`. Product List V2 and Create Order V2/V3 are endpoint versions, not replacement base prefixes.
- **Documentation checked:** current official English CJ developer documentation,2026-09-07.
- **Documentation date/current update:** the introduction lists2025-11-18 storage detail,2025-11-19 subwarehouse inventory and2025-12-02 platform-logistics processing updates. That list is not the whole current change history: batch-order semantics include2026-08-19 updates and Ticket includes a correction dated2026-09-07. Points/subscription policy changes effective June/July2026 are already applicable.
- **Base API URL:** `https://developers.cjdropshipping.com/api2.0/v1/`.
- **Sandbox:** same host/auth; order-level `isSandbox=1`, default0 is real. Simulates payment and statuses/tracking without real balance deduction, fulfillment or logistics. No separate fully isolated sandbox API host or universal simulation coverage is established.
- **Authentication model:** merchant API key exchanged for access/refresh tokens; business header `CJ-Access-Token`; server-side only. Access TTL conflicts:180days in opening auth prose versus15days in field/refresh/token-guide sections. Refresh nominal180days. Use returned expiry timestamps. Same-account get/refresh has a24-hour cached token-pair behavior; logout invalidates both.

Sources: [Introduction and updates](https://developers.cjdropshipping.com/en/api/introduction.html#update-announcements), [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [Token guide](https://developers.cjdropshipping.com/en/api/start/token.html), [Sandbox](https://developers.cjdropshipping.com/en/api/start/sandbox.html), [Ticket correction](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html#_5-create-ticket-post).

## MERCHANT CONNECTION MODEL

| Model | Technical evidence and decision |
| --- | --- |
| A — Platform account | One account can have multiple API shops, but token/account scope, supplier funds, callbacks and quotas are shared boundaries. No documented per-merchant wallet, delegated subaccount treasury or platform-fronting arrangement. **Do not choose as default.** |
| B — Merchant account | **Recommended initial architecture, conditional on CJ-approved SaaS custody/access.** Each merchant authorizes their own CJ account/API key and maintains their own supplier balance. Isolate every connection and object through PulseSoc ownership checks. |
| C — Hybrid | Potential later public-catalog/cache account plus merchant fulfillment accounts, only with CJ approval for data sharing and account-specific pricing/visibility rules. Additional complexity; not needed for initial integration. |

**Platform account:** no central procurement key or platform-funded float is required by the recommended model. **Merchant account:** bring an API key from CJ's API app/account authorization UI; no login password collection required.

**Shop binding:** getShops returns current-account shops, including API shops. Create order optionally accepts storeName; omitted means default API store. StoreName is not authorization: returned shopId identifies association. Validate selected shop against own getShops and local merchant/store mapping. Shop saveProduct explicitly warns that an existing foreign shopId can assign a product to that other owner; CJ cannot be the sole tenant guard.

**Recommended architecture:** Business OS → PulseSoc Supplier Gateway → normalized supplier contract → CJ adapter. Storefronts, merchant product IDs, retail prices, customer orders, payments, policies and analytics stay canonical in PulseSoc. Multiple shops per CJ account are documented; maximum shops and multiple API-key quota/token isolation are not.

The token guide's “Auth2.0” label and redirect/code-related global errors do not supply a usable public OAuth consent/scopes/client-registration flow. Do not claim OAuth is impossible; request its exact partner contract if available. Hosted SaaS permission is not proven merely by API availability. CJ's webhook notice cautions against sharing openId/raw pushes with third-party integration providers, requiring explicit custody clarification. [Shop](https://developers.cjdropshipping.com/en/api/api2/api/shop.html), [global errors](https://developers.cjdropshipping.com/en/api/api2/standard/ps-code.html), [webhook security](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication).

## REQUIRED MVP APIs

Selected scope: ordinary CJ-catalog products, raw CJ flow1, create-only V2, staged explicit merchant funding, CJ-carrier fulfillment and detailed freight quotation. These25 operations define the supported footprint, not25 calls for every order. `CJ` means `CJ-Access-Token`; `CJ+JSON` also means Content-Type application/json.

| CJ official name | HTTP method | Exact endpoint | Authentication | Why PulseSoc needs it |
| --- | --- | --- | --- | --- |
| [Authentication §1.1 Get access token](https://developers.cjdropshipping.com/en/api/api2/api/auth.html) | POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken | JSON apiKey; no access-token header | Merchant credential exchange | Merchant credential exchange |
| [Authentication §1.2 Refresh access token](https://developers.cjdropshipping.com/en/api/api2/api/auth.html) | POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/refreshAccessToken | JSON refreshToken | Refresh without collecting passwords | Refresh without collecting passwords |
| [Setting §1.1 Get Settings](https://developers.cjdropshipping.com/en/api/api2/api/setting.html) | GET | https://developers.cjdropshipping.com/api2.0/v1/setting/get | CJ | Validate account permission and effective configuration | Validate account permission and effective configuration |
| [Shop §1.1 Get Shop List](https://developers.cjdropshipping.com/en/api/api2/api/shop.html) | GET | https://developers.cjdropshipping.com/api2.0/v1/shop/getShops | CJ | Allowlist owned API shops before any order routing | Allowlist owned API shops before any order routing |
| [Category List](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_1-1-category-list-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/getCategory | CJ | Category List | Build category navigation and supported search filters |
| [Product List V2](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_1-2-product-list-v2-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/listV2 | CJ | Product List V2 | Power bounded merchant catalog search from the current primary search API |
| [Global Warehouse List](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_1-3-global-warehouse-list-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/globalWarehouseList | CJ | Global Warehouse List | Expose documented fulfillment-origin countries and area identifiers |
| [Product Details](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_1-5-product-details-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/query | CJ | Product Details | Create and refresh source product, media, specification and cost snapshots |
| [Inquiry of All Variants](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_2-1-inquiry-of-all-variants-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/variant/query | CJ | Inquiry of All Variants | Map the source product's purchasable variants to local merchant variants |
| [Variant ID Inquiry](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_2-2-variant-id-inquiry-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/variant/queryByVid | CJ | Variant ID Inquiry | Resolve and validate the exact selected supplier variant and cost |
| [Query Inventory by Product ID](https://developers.cjdropshipping.com/en/api/api2/api/product.html#_3-3-query-inventory-by-product-id-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/product/stock/getInventoryByPid | CJ | Query Inventory by Product ID | Refresh product and variant stock with warehouse/subwarehouse distribution |
| [Create Order V2](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-1-create-order-v2-post) | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/createOrderV2 | CJ+JSON; platformToken optional | MVP recommended | Create an external fulfillment order without immediately funding it |
| [Add Cart](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-3-add-cart) | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/addCart | CJ+JSON | MVP staged flow | Move eligible created supplier orders into the staged payment workflow |
| [Add Cart Confirm](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-4-add-cart-confirm-post) | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/addCartConfirm | CJ+JSON | MVP staged flow | Validate staged orders and obtain shipment-group references |
| [Save Generate Parent Order](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-5-save-generate-parent-order-post) | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/saveGenerateParentOrder | CJ+JSON | MVP staged flow | Obtain the parent payable, payment reference and expiry for approval |
| [Query Order](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-7-query-order-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getOrderDetail | CJ | MVP | Read back authoritative supplier state and reconcile uncertain outcomes |
| [Order Delete](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-8-order-delete-del) | DELETE | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/deleteOrder | CJ | MVP bounded cancellation | Cancel only eligible CREATED or IN_CART supplier orders |
| [Get Balance](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_2-1-get-balance-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/pay/getBalance | CJ | MVP funding preflight | Check merchant-owned supplier funds before approved payment |
| [Pay Balance V2](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_2-3-pay-balance-v2-post) / [additional schema](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_7-9-balance-payment-v2-post) | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/pay/payBalanceV2 | CJ+JSON | MVP funding | Fund the prepared supplier payable from the connected merchant's balance |
| [Freight Calculation Tip](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html#_1-2-freight-calculation-tip-post) | POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculateTip | CJ+JSON | Preferred estimate contract | Quote fulfillment freight, route eligibility and documented cost components |
| [Get Tracking Information](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html#_2-1-get-tracking-information-get) | GET | https://developers.cjdropshipping.com/api2.0/v1/logistic/trackInfo | CJ per general rule; endpoint example omits header | MVP tracking | Refresh carrier tracking and delivery observations for buyer tracking |
| [Message Setting](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html) | POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/set | CJ+JSON | Configure asynchronous updates | Configure asynchronous updates |
| [Subscribe Products](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html) | POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe | CJ+JSON | Register active imported products | Register active imported products |
| [Unsubscribe Products](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html) | POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/unsubscribe | CJ+JSON | Release unused subscriptions without harming other stores | Release unused subscriptions without harming other stores |
| [Query Subscribed Products](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html) | GET | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe/list | CJ | Verify registration and detect subscription drift | Verify registration and detect subscription drift |

Full input/output/rate/webhook/classification matrix for all126 operations: [CJ_API_REQUIREMENTS.md](CJ_API_REQUIREMENTS.md). No inbound receiver is counted as a CJ-hosted API.

## PRODUCTS

**Search:** ListV2 has keyword/SKU, category hierarchies, stock country, USD price bounds, inventory ranges, verifiedWarehouse, supplier, creation time, sorting, product flags, platform and feature filters. page1–1000, size1–100/default10; totalRecords capped6000. Results are nested content[].productList[], not a flat universal catalog export.

**Details:** product/query supplies source text/images, attributes, category, customs/logistics metadata and variants. Treat descriptions/URLs as untrusted content and licensing separately. **Variants:** preserve pid/vid/SKU, barcodes, option dimension/value relationships, g/mm/mm³ and USD variant cost; hyphenated display values are not reliable IDs.

**Inventory:** getInventoryByPid distinguishes product totals from variant quantities, country/area from physical subwarehouse stock, CJ from factory stock, verified from unverified stock. Never sum private stock states into available public inventory. No reservation/freshness SLA is established. Use webhook invalidation plus bounded reconciliation and recheck selected stock before quotes/fulfillment.

**Warehouses:** globalWarehouseList gives geographic area/country descriptors; warehouse/detail gives physical storage metadata. Preserve areaId versus stockId/storageId. **Sourcing:** POST product/sourcing/create and GET product/sourcing/queryList (max100 IDs); older POST query is deprecated. Sourcing callbacks contain a type/routing conflict; use queries until confirmed.

**My Product:** optional CJ collection membership, not a documented universal prerequisite for raw CJ vid/SKU orders. Store Order Flow connections are a different capability. **Reviews:** technically queryable, but licensing/provenance/local verified-purchase status are unproven; do not merge into PulseSoc verified-purchase ratings. Videos expose copyright/purchase state and are optional.

Sources and complete filters/units: [catalog evidence](catalog_evidence.md), [Product](https://developers.cjdropshipping.com/en/api/api2/api/product.html), [Storage](https://developers.cjdropshipping.com/en/api/api2/api/storage.html), [Products synchronization](https://developers.cjdropshipping.com/en/api/start/Products-Synchronization-Processing.html).

## SHIPPING

**Freight:** selected endpoint is freightCalculateTip with reqDTOS: origin/destination, SKU list, detailed quantities, grams weights, cm³ volume, productProp and route/address details. Product properties come from productProEnSet and eligible origins from inventory. Simpler freightCalculate is a lower-detail alternative, not an additional obligatory checkout call.

**Methods:** provider option/channel IDs and names, applicable origin/warehouse and destination, price components and restrictions. **ETA:** transit estimates, not guaranteed delivery dates; catalog dispatch/deliveryCycle fields are not a universal processing SLA. Preserve unknown processing time.

**Restrictions:** address/postcode, product properties, weight, dimensions, SKU/type, HS code, value, route and platform conditions; unavailableShippingMethods explains some exclusions but filters internal reasons and returns no prices. Missing from both lists is not proof of availability. Use platform=api/default API semantics, not a fabricated Shopify identity.

**Cost boundary:** supported realistic estimate, not guaranteed landed cost. Tip totalPostageFee uses wrapPostage (or discountFee fallback) plus taxesFee, clearanceOperationFee and tariff; avoid double-counting alternative price fields. Some CNY/weight unit labels conflict. Order-parent payable and later makeup bills can differ. PulseSoc customer shipping/tax/retail price remains separately authoritative. [Logistic](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html), [order cost evidence](orders_evidence.md).

## ORDERS

**Create:** POST createOrderV2, explicit payType=3, orderFlow=1, shopLogisticsType=2. Required orderNumber, destination country/name/state/city/name/address, origin and logisticName, products with CJ vid or SKU and positive quantity. Carrier rules may require fields marked optional in the generic schema. Keep storeLineItemId and all returned provider IDs distinct.

**Confirm:** addCart → addCartConfirm (returns shipmentsId) → saveGenerateParentOrder (shipmentOrderId input, payId/payable/expiry response). Inspect partial success and interceptions. Legacy PATCH confirmOrder is an alternative, not required in this chosen sequence.

**Payment:** getBalance then payBalanceV2 under the connected merchant's account with explicit funding approval. V2 payType2 automatically progresses and deducts balance; do not use it for an unreviewed create. payType1/default yields CJ page payment, which is supplier funding—not customer checkout. Card/PayPal/other page methods and automated top-ups are not guaranteed by the reviewed API contract; balance restriction1604001 may require merchant action in My CJ.

**Critical funding gap:** payBalanceV2 has no expected/max amount input. Local quote approval cannot atomically enforce provider spend. Confirm payId amount-lock/expiry/change semantics before fully automatic funding; never imply PulseSoc funds the difference.

**Query:** getOrderDetail; bounded list and max100 batch detail for reconciliation. Batch omissions are not proof of deletion, and list default status is documented CANCELLED—use explicit filters. **Cancel:** DELETE deleteOrder only CREATED/IN_CART. No general paid/shipped ordinary-order cancellation is documented; use authorized support/eligible supplier dispute and independent buyer policy.

V2's table advertises Store Order Flow, but its dedicated guide requires V3; V3 has no payType request row despite sandbox prose. Account-level flow override is also possible. Raw CJ flow1 is the scoped recommendation, subject to account confirmation. [Shopping](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html), [Orders synchronization](https://developers.cjdropshipping.com/en/api/start/Orders-Synchronization-Processing.html).

## FULFILLMENT

**Status:** CREATED → IN_CART → UNPAID → UNSHIPPED (PENDING/PROCESSING substatus) → SHIPPED → DELIVERED, with cancellations/exceptions retained separately; not every transition is locally reversible.

**Shipment:** one customer order can map to several supplier orders/shipments; preserve split/bundle line quantities and external lineage. **Tracking:** trackInfo plus LOGISTIC notifications; tracking number creation is not delivery. REST status is free text while webhook numeric trackingStatus has its own enum.

Ordinary CJ logistics proceeds after supplier payment. Platform-waybill logistics additionally requires uploading the real label before warehouse processing; upload/update/warehouse-selection APIs are optional for that different workflow. Its warehouse immutability/selection instructions conflict and need resolution before adopting it. [State mapping](CJ_ORDER_STATE_MAPPING.md), [full logistics process](https://developers.cjdropshipping.com/en/api/start/Orders-Synchronization-Processing.html).

## WEBHOOKS

**Subscription:** webhook/set plus explicit product IDs, max100/write; no subscribeAll after July2026. **Product/inventory:** PRODUCT, VARIANT and STOCK all depend on product subscription. **Order:** ORDER, ORDERSPLIT; reassociation spelling ORDER_CONNNECTED. **Logistics:** LOGISTIC. Also MAKEUP, PRIVATE_ORDER and a SOURCINGCREATE sample with contradictory table/routing.

**Authentication:** sign header = padded Base64 HMAC-SHA256 over raw body, key saved openId string. openId is secret and must never reach mobile, ordinary logs or UNDX. Use durable dedup and provider read-back before consequential mutations.

**Retry:** ACK HTTP200 within3s; documented up to3 retries, schedule/retention/attempt interpretation unresolved. Topics auto-close after each of two preceding complete hours is below80% success; manual reactivation required. Ordering, general replay history and independent signing-key rotation are undocumented. [Complete event/security map](CJ_WEBHOOK_EVENT_MAP.md), [official webhook](https://developers.cjdropshipping.com/en/api/start/webhook.html).

## DISPUTES

**Available:** six APIs cover eligible lines, confirmation/options, create, cancel, list and detail. Only API-created orders can open disputes through this interface. Text/images/video evidence and refund/reissue choices exist; reasons are dynamically returned.

**Required:** a supplier recovery process is required operationally; its automated API suite is T2. Customer refund decisions and payment-processor settlement remain independent. refundType=platform does not prove a PulseSoc-customer refund; finallyDeal3 means supplier rejection, not permission to reverse a buyer refund. Return-label/RMA destination/settlement SLA are not established. [Dispute](https://developers.cjdropshipping.com/en/api/api2/api/dispute.html), [failure matrix](CJ_FAILURE_RECOVERY_MATRIX.md).

## SHOP APIS

**Required:** getShops for trusted account/shop binding and subscription-list ownership. **Optional:** saveProduct/saveVariantBatch, paged/detail mirrored products, GET/POST/DELETE product connections and four packaging-connection APIs. **Not needed for raw CJ flow1:** mirroring the storefront into CJ or imitating Shopify's platform integration. Multiple API shops are real documented support; their maximum and independent callback/quota/funding scopes are not.

**Ticket:**13 optional support APIs,1QPS. They are not commerce/refund APIs. Create requestNo only prevents duplicate submits for5seconds, not durable idempotency; create lacks ticketId, and reply/order-specific creation have no retry protection. No tickets were sent. [Shop](https://developers.cjdropshipping.com/en/api/api2/api/shop.html), [Ticket](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html).

## RATE LIMITS

**Exact constraints:**10requests/s/IP; maximum3users/IP; non-login ceiling30/s; tier1/2/4/6 account QPS; explicit token/Ticket1QPS and batch-detail/proof2QPS;50,000 base points/day + transaction conversion; per-minute total/1440 replenishment. Endpoint costs range0/10/50/1000 points; diagnostic shipping cost conflicts with the generic table. Daily call-count rules are deprecated.

**Scale implications:** under the explicit model in the scale artifact,304calls and2910points/merchant/day become30,400calls/291,000points at100 merchants and30.4millioncalls/291millionpoints at100,000. Merchant accounts distribute budgets, but do not remove the three-users/IP restriction. A central account additionally encounters shared balance and1000–10,000 product-subscription caps. Queues, caching, fan-out, sync budgets and backpressure are mandatory; approved egress/account terms are a prerequisite, never proxy rotation to evade limits. No infinite-scale claim. [Per-endpoint rate matrix and four-scale model](CJ_RATE_LIMIT_SCALE_ANALYSIS.md).

## SECRETS

**Exact required credentials:** apiKey, accessToken, refreshToken, and openId (also webhook signing key). **Merchant-scoped:** protected per-connection account credential bundle; account-sharing relationships require coordinated token/cache/limit handling. **Platform-scoped:** existing secret-storage/encryption infrastructure only, not an invented central CJ credential. Optional platformToken is documented on create but not established as required for raw CJ flow. No standalone CJ_WEBHOOK_SECRET/client_secret is proven. No Railway variables or secrets are needed for discovery; future backend retrieves encrypted merchant credentials, mobile and UNDX never do. [Secret requirements](CJ_SECRET_REQUIREMENTS.md).

## PULSESOC SUPPLIER CONTRACT

All24 requested normalized methods map to exact CJ calls or explicitly qualified composite/unsupported behavior in [PULSESOC_SUPPLIER_CONTRACT.md](PULSESOC_SUPPLIER_CONTRACT.md). It includes the tenant authorization chain, server-authoritative prices/addresses, immutable fulfillment intents, unknown-write reconciliation, funding limits, durable webhook inbox and threat model. [Data authority map](CJ_PULSESOC_DATA_AUTHORITY_MAP.md) preserves merchant products, canonical customer order/payment/refund domains and separate supplier recovery.

## API COUNTS

- **MVP API COUNT:25**
- **PRODUCTION-HARDENING API COUNT:23**
- **OPTIONAL API COUNT:74**
- **NOT-NEEDED API COUNT:4**
- **TOTAL:126 distinct method+URL operations**

Counts include management/testing capabilities where assigned, not local functions or repeated documentation. See the auditable [requirements registry](CJ_API_REQUIREMENTS.md).

## BLOCKERS

Only genuine provider/account/documentation gaps are listed here; missing local implementation is expected and is not mislabeled a CJ defect.

1. **Hosted SaaS access and custody:** written CJ authorization for merchant API-key/openId custody and shared backend egress; documented three-users/IP rule, maximum shops and callback/account boundaries.
2. **Credential lifecycle:** access-token TTL conflict; same-account key/cache/logout scope; API-key revoke behavior and independent openId compromise/rotation response.
3. **Automatic funding safety:** payId amount immutability/expiry, payment replay/reconciliation guarantees and actual balance-payment eligibility; no API expected-amount guard.
4. **Order/account contract:** flow1 permission/account override, V2/V3 contradiction, exact provider ID correlations and partial/cart/payment response schemas. Platform-waybill warehouse contradictions block that optional flow specifically.
5. **Webhook reliability/security:** subscription batch add/replace semantics and shop/account scope, affected topic routing, event-ID retry scope, retry schedule/history, replay protection and sandbox callback behavior.
6. **Commercial data rights:** media/review licensing and provenance; account-specific public/private data-sharing permission. Reviews/video can remain disabled rather than block a basic catalog integration.
7. **Targeted schema/operational gaps:** quote unit inconsistencies, inventory freshness/accounting and physical warehouse mapping, current point/quota interaction and sandbox coverage. Unknown optional private-inventory units do not justify inventing values or blocking unrelated ordinary-product research.

## NEXT ENGINEERING MISSION

**First close the listed CJ contract gates without creating production orders or requesting secrets prematurely. Then, under a separate approved implementation mission, build a backend-only, feature-gated PulseSoc Supplier Gateway with a CJ sandbox adapter for the25 selected operations.**

Acceptance scope: tenant-owned credential references; dynamic expiry and quota handling; approved merchant/API-shop binding; normalized catalog/import/stock/quote snapshots; raw CJ flow1 create-only intents; staged cart/parent preparation and merchant-approved funding only after amount-lock confirmation; signed durable webhook inbox; batch reconciliation; independent supplier/customer state and recovery; hard sandbox assertions and no production dispatch.

Verification: unit/contract fixtures for all known schema conflicts, two-tenant access-denial tests, long-ID/currency/unit tests, signature/redaction/replay tests, partial acceptance, timeout-after-provider-success, duplicate funding, rate/points exhaustion, auto-closed callbacks, stock/price/route changes and sandbox state/track controls. Any authorized provider test must prove isSandbox1 and no real balance/fulfillment side effects. No App Store build, RTC/audio/live changes, payment rewiring or production enablement belongs in that mission.

## Evidence coverage and scope verification

Reviewed official modules: [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [Setting](https://developers.cjdropshipping.com/en/api/api2/api/setting.html), [Product](https://developers.cjdropshipping.com/en/api/api2/api/product.html), [Storage](https://developers.cjdropshipping.com/en/api/api2/api/storage.html), [Shopping](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html), [Logistic](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html), [Dispute](https://developers.cjdropshipping.com/en/api/api2/api/dispute.html), [Webhook API](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html), [Shop](https://developers.cjdropshipping.com/en/api/api2/api/shop.html), [Ticket](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html).

Reviewed guides: Introduction/update announcements, Development, Get Access-token, Orders Synchronization Processing, Products Synchronization Processing, Start Webhook, Sandbox, Interface Call Restrictions and Points Resource Rules. Supporting interface/field/global-error/platform definitions were also inspected. Detailed source anchors are retained in [catalog evidence](catalog_evidence.md), [order evidence](orders_evidence.md) and [webhook/limits evidence](webhooks_limits_evidence.md).

Local inspection was read-only against canonical Store/Orders/Marketplace/Refunds. Deliverables are nine requested Markdown documents plus three research evidence notes under reports/cj-discovery. No application code, schema, secrets, Railway configuration, checkout, provider account, order, payment, subscription, mobile/RTC/audio/live code or deployment changed. No commit or push was performed for this discovery. Documentation inventory/count/link checks are the appropriate verification; application/runtime/device tests were not run or claimed.
