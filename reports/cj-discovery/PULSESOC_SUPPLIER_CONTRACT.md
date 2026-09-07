# PulseSoc normalized supplier contract

Discovery design v1 — 2026-09-07. Proposed, not implemented.

## Authority boundary

```text
Business OS / canonical commerce domains
                 |
       PulseSoc Supplier Gateway
    tenant authorization + policy + audit
    idempotency + queues + usage budgets
                 |
      normalized supplier contract
       /       |        |        \
      CJ    Printful  Printify  manual/future
```

Only CJ was researched. Other adapters are architectural slots, not asserted implementations.

[Store service](/Users/hmcherie/Desktop/CoinPilotX/services/business_os/store/service.py:1) owns the business storefront/catalog and reuses canonical membership/RBAC. [Orders facade](/Users/hmcherie/Desktop/CoinPilotX/services/business_os/orders/service.py:1) delegates to marketplace Orders/Refunds without another ledger. CJ supplies external facts; it does not own retail pricing, checkout, customer orders, customer payments, refunds, policies or merchant analytics.

## Common contract

Every call requires trusted actor/service identity, business/merchant ID, store ID, supplier connection ID, environment, operation ID and current authorization. Check actor membership/permission, store ownership, connection ownership, provider authorization and capability both when enqueuing and executing. Never resolve a connection by client-provided ID alone. Supplier URLs and credentials come from trusted configuration.

Return provider, connection reference, opaque external IDs, observed-at, source-updated-at when supplied, freshness, capability version and warnings. No token or openId enters a normalized response. IDs remain lossless strings. Monetary values are exact decimals with explicit currency and component; dimensions carry units. Null, absent, zero and unknown differ. Quotes bind destination, origin/warehouse, selected variants/quantities and an observation time; they are not inventory reservations or guaranteed landed cost.

Distinguish accepted, partial, rejected, pending and outcome_unknown. HTTP200 alone is insufficient: inspect code, result/success flags, required IDs and per-item outcomes. Contradictory envelopes enter reconciliation. Messages are human diagnostics, not parser contracts. [Development](https://developers.cjdropshipping.com/en/api/start/development.html).

## Exact CJ mapping

Secondary paths below use the full base `https://developers.cjdropshipping.com/api2.0/v1/`. Business calls use CJ-Access-Token; exchange/refresh instead take the indicated JSON secret. JSON writes use Content-Type application/json.

| Normalized method | HTTP | Exact primary endpoint | Inputs / normalized result | Support / composition |
| --- | --- | --- | --- | --- |
| authenticate() | POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken | apiKey → credential reference; secrets never returned to callers | Supported; account model approval required |
| refreshAuthentication() | POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/refreshAccessToken | refreshToken → atomically replaced credential bundle | Supported; 24h cache and TTL conflict handled by returned expiry |
| connectionHealth() | GET | https://developers.cjdropshipping.com/api2.0/v1/setting/get | Read permission, quotas, account/sandbox metadata; redact openId | Composite with GET shop/getShops for owned shop binding |
| searchProducts() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/listV2 | Bounded filters/pagination → supplier product summaries | Supported; not a full-catalog export guarantee |
| getProduct() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/query | pid or documented SKU → normalized supplier snapshot | Supported; preserve source currency/units and sanitise descriptions |
| getVariants() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/variant/query | pid/productSku → variants; queryByVid for an individual selected vid | Supported; never guess option identity from display strings |
| getInventory() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/stock/getInventoryByPid | pid → variant/country/warehouse/subwarehouse snapshot | Also GET product/stock/queryByVid or queryBySku; availability is not a reservation |
| getWarehouses() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/globalWarehouseList | No input → area/country descriptors; GET warehouse/detail?id= for physical storage detail | Supported; distinguish geographic areaId from physical stockId/storageId |
| getSupplierCost() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/variant/queryByVid | vid → current variantSellPrice and source metadata | Composite with product/query and order quote; no dedicated price-lock endpoint |
| estimateShipping() | POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculateTip | reqDTOS with origin/destination, grams weight/wrapWeight, cm³ volume, productProp, skuList and freightTrialSkuList → eligible methods, prices and restrictions | Simple POST logistic/freightCalculate is a lower-detail alternative; POST logistic/unavailableShippingMethods diagnoses exclusions; no binding price guarantee |
| importProductReference() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/query | Validate snapshot, selected variants/inventory, then persist owned local reference | No CJ import mutation required for raw CJ order flow; subscription is a separate provider write |
| refreshProduct() | GET | https://developers.cjdropshipping.com/api2.0/v1/product/query | Refresh supplier-owned fields only | Composite with variant/query and stock/getInventoryByPid; do not overwrite merchant-authored content/retail price |
| createFulfillment() | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/createOrderV2 | Immutable customer-line references + server-approved address/route; explicit payType=3, orderFlow=1 | Supported for scoped MVP; V3 required by Store Order Flow guide, excluded until account contract resolved |
| confirmFulfillment() | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/addCart | cjOrderIdList → accepted/intercepted order set | Composite: POST shopping/order/addCartConfirm → shipmentsId; POST shopping/order/saveGenerateParentOrder → payId/quote/expiry. Inspect every result before advancing |
| fundFulfillment() | POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/pay/payBalanceV2 | shipmentOrderId + persisted payId + merchant-approved quote → supplier funding outcome | Conditional on provider/account balance permissions; GET shopping/pay/getBalance preflight. Not customer checkout |
| cancelFulfillment() | DELETE | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/deleteOrder | Owned CJ orderId only while CREATED/IN_CART | PARTIAL: no general instant paid/shipped cancellation. UNPAID and later require CJ support/eligible dispute, not this endpoint |
| getFulfillment() | GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getOrderDetail | Known customer/CJ orderId → owned supplier status/snapshot | POST shopping/order/getOrderDetailBatch for up to100; GET shopping/order/list for bounded recovery |
| getShipments() | GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getOrderDetail | Project verified supplier order/tracking fields into one-to-many shipment records | Composite with batch detail and ORDER/ORDERSPLIT/LOGISTIC events; no standalone universal shipment-list API established |
| getTracking() | GET | https://developers.cjdropshipping.com/api2.0/v1/logistic/trackInfo | Repeated trackNumber query → status/last-mile carrier/number | PARTIAL schema: tracking example omits auth header; global headers prescribe CJ token. Send token server-side; confirm actual requirement without exposing buyer data |
| createDispute() | POST | https://developers.cjdropshipping.com/api2.0/v1/disputes/create | Persist businessDisputeId and approved evidence/amounts → provider claim | Composite preflight GET disputes/disputeProducts then POST disputes/disputeConfirmInfo; API-created orders only |
| getDispute() | GET | https://developers.cjdropshipping.com/api2.0/v1/disputes/getDisputeDetail | Owned disputeId → supplier recovery outcome | GET disputes/getDisputeList reconciles; not a buyer refund confirmation |
| subscribeProductEvents() | POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe | productIds max100 → per-ID subscription result | Configure POST webhook/set first; read GET webhook/product/subscribe/list with owned shopId |
| unsubscribeProductEvents() | POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/unsubscribe | productIds max100 → unsubscribe result | Connection/product reference-counted; do not unsubscribe other active stores' products |
| receiveWebhook() | POST inbound | No CJ endpoint | CJ POSTs to the HTTPS callback URL configured by PulseSoc | Local receiver capability, NOT a CJ-hosted endpoint; no receiver route is implemented in this mission |

Sources: [Authentication](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [Setting](https://developers.cjdropshipping.com/en/api/api2/api/setting.html), [Product](https://developers.cjdropshipping.com/en/api/api2/api/product.html), [Storage](https://developers.cjdropshipping.com/en/api/api2/api/storage.html), [Shopping](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html), [Logistic](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html), [Dispute](https://developers.cjdropshipping.com/en/api/api2/api/dispute.html), [Webhook](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html). Exact secondary URLs and schema limits are in [API requirements](CJ_API_REQUIREMENTS.md).

## Safe fulfillment sequence

1. Customer payment remains canonical and independently verified. Supplier procurement timing requires explicit merchant policy; no browser success callback proves customer payment.
2. Revalidate owned import mapping, variant membership, availability, cost, product eligibility, address and shipping quote. Reject unapproved substitutions and keep immutable customer line snapshots.
3. Persist an intent with stable merchant/store-qualified orderNumber (CJ max50), request hash and storeLineItemId (max125). Proposed local uniqueness: tenant+connection+customer-order+fulfillment-group+operation-version. CJ does not document an idempotency-header guarantee.
4. POST createOrderV2 with payType=3/orderFlow=1; reconcile returned order references. POST addCart then addCartConfirm; inspect accepted and intercepted sets. Persist returned shipmentsId. POST saveGenerateParentOrder with shipmentOrderId to obtain payId, payable breakdown and expiry. Do not treat similarly named IDs as interchangeable without verified mapping.
5. Enforce merchant approval for exact currency/amount, quote freshness and spend tolerance as a **local preflight**. GET balance is advisory, not a reservation. POST payBalanceV2 uses that merchant's account funds, never implicit platform advances. It has no expectedAmount/currency/maxAmount input: this is not an atomic provider-enforced spend cap. Whether payId locks the approved payable amount is undocumented and must be settled before fully automatic funding.
6. Timeout at any write means outcome_unknown until provider read-back settles it. Do not generate new orderNumber/payId or blindly retry payment. Duplicate-order code1603003 is a reconciliation signal, not exactly-once proof.
7. Reconcile splits and per-line quantities before requesting canonical order transitions. Supplier funding, customer payment, shipping label and actual delivery remain separate facts.

The selected cart/parent-payment sequence does not additionally require the alternative PATCH confirmOrder. Store Order Flow has a V2/V3 documentation conflict and an account-level override; validate that an account supports raw CJ flow1 before enabling dispatch. [Shopping](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html).

## Events, snapshots and scale

Supplier changes update supplier snapshots only; merchant title/content/retail-price overrides survive refresh. Event processing never directly charges/refunds buyers. Verify raw-body HMAC using the stored secret openId, bind connection/object ownership, durably record an inbox item and ACK within3s; process asynchronously. Deduplicate by connection/account+topic+messageId, detect same-ID/different-body conflicts, and use CJ read-back for money-impacting decisions.

Use webhook invalidation plus risk-weighted bounded reconciliation. Batch order reads, coalesce product inventory reads, reserve points, prioritise open obligations, and apply backpressure. Absence of an event is not proof of freshness. One callback per account/topic requires approved shared ingress and tenant-safe fan-out; do not replace another integration's callback without account authorization.

Disallow unrelated merchants sharing a CJ account by default. Shared-account quota coordination must never share private inventory, prices, order/address data or credentials. Three users/IP is a real provider restriction: obtain approved SaaS egress terms, never rotate proxies to bypass it. [Limits](https://developers.cjdropshipping.com/en/api/start/limit.html).

## Threat model

| Threat | Required control / release test |
| --- | --- |
| Credential leakage | Encrypted secret references, least privilege, log/trace/export redaction; seeded-secret tests through failures and telemetry |
| Cross-tenant supplier access | Actor → business → store → connection → provider checks; attempt every operation with another tenant's identifiers |
| Forged pid/vid/SKU | Verify provider product-variant relation and local owned import; reject mismatched or removed variants |
| Foreign shopId | Allowlist own getShops result plus store mapping before dispatch; never trust foreign owner metadata |
| Webhook spoofing | Stored-key raw-body HMAC and constant-time comparison; altered-body/wrong-key/attacker-openId fixtures; authenticated state read-back |
| Replay, duplicates, ordering | Durable inbox uniqueness, digest conflicts and reconciliation; crash-after-ACK/late-event tests |
| Duplicate fulfillment/payment | Unique immutable intents, locks, stable provider refs; timeout-after-success and crash-at-each-write tests |
| Order tampering | Rebuild requests from canonical server-side lines/address and approved operation; reject client totals or substituted identities |
| Price manipulation | Exact currency, independent retail authority, fresh quote hash and approved tolerance; fail on unknown/missing totals |
| Shipping manipulation | Requote selected address/origin/warehouse and method; reject stale quote and unauthorized waybill changes |
| Address leakage | Minimize, encrypt and expire PII; no addresses in ordinary logs, prompts or public catalog objects |
| Rate abuse | Per-actor/account/approved-egress budgets, queues and deadlines; 429, exhausted-points and flood tests |
| UNDX secret/action escalation | Redacted normalized read models; separately authorized execution; supplier text cannot become instructions |
| Hostile media/HTML | Sanitize supplier content and URLs; SSRF/script/private-network/oversize payload tests |

Shop saveProduct explicitly permits assigning a product to someone else's existing shopId; this makes gateway-side ownership enforcement non-negotiable. [Shop](https://developers.cjdropshipping.com/en/api/api2/api/shop.html).

## Unsupported / unresolved promises

Do not promise inventory reservation, immutable landed price, platform submerchant balances, automatic wallet top-up, usable delegated OAuth, arbitrary paid/shipped cancellation, buyer refunds from CJ disputes, webhook event-history replay or unlimited scale. Complete warehouse enumeration beyond the documented geographic list and stock-specific physical IDs is not established. Hosted credential custody and openId security require CJ approval. See [secrets](CJ_SECRET_REQUIREMENTS.md), [state mapping](CJ_ORDER_STATE_MAPPING.md), [failure recovery](CJ_FAILURE_RECOVERY_MATRIX.md) and [scale analysis](CJ_RATE_LIMIT_SCALE_ANALYSIS.md).
