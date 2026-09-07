# CJ webhook event map

Current official docs checked2026-09-07. Proposed receiver contract only; no endpoint, configuration, subscription or callback was created.

## Management endpoints

All use CJ-Access-Token, with JSON for POST.

| Official operation | Exact method / URL | Inputs and result |
| --- | --- | --- |
| Message Setting |POST https://developers.cjdropshipping.com/api2.0/v1/webhook/set |product,stock,order,logistics objects required; optional makeup/privateOrder. Each type=ENABLE/CANCEL and exactly one reachable public HTTPS callbackUrls entry. Boolean result |
| Subscribe Products |POST https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe |productIds max100; inspect successProductIds/failProductIds. subscribeAll unavailable after July2026 |
| Unsubscribe Products |POST https://developers.cjdropshipping.com/api2.0/v1/webhook/product/unsubscribe |required productIds max100; boolean result |
| Query Subscribed Products |GET https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe/list |required shopId; pageNum default1; pageSize default20 max200; optional sku/productId; paged active/inactive records and reason |
| Inspect callback configuration |GET https://developers.cjdropshipping.com/api2.0/v1/setting/get |account callback metadata; sample read field urls differs from write callbackUrls |

Sources: [Webhook API](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html), [Settings](https://developers.cjdropshipping.com/en/api/api2/api/setting.html).

Enablement alone does not subscribe the entire catalog. Product subscription filters PRODUCT, VARIANT **and STOCK**. Caps: lv1=1000, lv2=2000, lv3=3000, lv4=5000, lv5=10000. Lv0 and membership mapping are not supplied. Subscription list requires shopId although subscription writes do not; additive/replacement/batch atomicity and account/shop scope require CJ confirmation. Track desired versus observed subscription membership and reference-count shared products within an authorized account. Preserve other authorized callback consumers; one store may not replace account callbacks unilaterally.

## Event mapping

Envelope: messageId, type, messageType, params; some topics additionally include openId. Examples contain nullable fields despite required tables. fields[] is a change hint, not permission to erase every null-valued field.

| Exact type | Configuration / scope | Documented trigger and fields | Proposed normalized handling / reconciliation |
| --- | --- | --- | --- |
| PRODUCT | product + explicit product subscription | INSERT/UPDATE/DELETE; pid, fields, names/content/category, productSellPrice, productSku, productStatus (2 off/3 on) | Refresh product/query; preserve merchant overrides; no automatic publication |
| VARIANT | product + parent product subscription | INSERT/UPDATE/DELETE; vid, fields, variantSku/key/value1–3, dimensions mm, weight g, price USD, status0 off/1 on | Refresh variant/queryByVid and parent mapping; block removed option, never substitute |
| STOCK | stock + parent product subscription | Example UPDATE; params map keyed by vid to areaId/areaEn/countryCode/storageNum rows | Invalidate then GET product/stock/getInventoryByPid or queryByVid; not an ordered delta or private-stock equivalent |
| ORDER | order | Table INSERT/UPDATE/DELETE/ORDER_CONNNECTED (literal spelling); regular orders do not get INSERT under newer rule; cjOrderId/orderNumber/orderStatus, dates, tracking, orderItems, privateOutboundOrder | GET shopping/order/getOrderDetail; bind order and line ownership; supplier state only |
| ORDERSPLIT | Related order event; exact routing not expressly confirmed | originalOrderId, orderSplitTime, splitOrderList with orderCode/createAt/numeric orderStatus/productList | Read each owned split child and verify quantities; preserve lineage; no duplicate customer order |
| SOURCINGCREATE | No explicit dedicated setting/routing contract | Sample UPDATE with cjProductId/cjVariantId/cjVariantSku/cjSourcingId/status/failReason/date; table incorrectly says ORDERSPLIT | GET product/sourcing/queryList fallback; require CJ clarification before event-dependent sourcing |
| LOGISTIC | logistics | INSERT/UPDATE/DELETE; orderId,storeOrderNumbers,trackingNumber/provider/status, logisticsTrackEvents JSON-encoded string | Read order and GET logistic/trackInfo; parse nested events with bounds; shipment projection only |
| MAKEUP | makeup (optional) | INSERT→CREATED; CANCEL→CANCELED; PAID→PAID; BT orderId,relationOrderId,payOrderId,amount USD,type,diffUseType,status,dates | POST shopping/makeup/list read-back; separate supplier-cost adjustment and approval, no buyer surcharge |
| PRIVATE_ORDER | privateOrder (optional) | UPDATE for SY private-stock orders only; orderId,orderNumber,status,orderType2,dates | POST shopping/privateInventory/order/list; not regular dropship and not private outbound ORDER |

Sources: [Start webhook topics](https://developers.cjdropshipping.com/en/api/start/webhook.html#list-of-topics), [API notification rules and makeup/private topics](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_3-notification-rules).

ORDER special cases: deprecated orderNum is replaced by orderNumber; ORDER_CONNNECTED returns the actual CJ order ID after reassociation. Private outbound receives INSERT with privateOutboundOrder=true and later UPDATE, unlike ordinary orders. Line items carry vid, quantity, sellPrice, lineItemId, storeLineItemId and optional production status/abnormality. POD production states1 pending order/2 pending production/3 in production/4 complete/5 abnormal are separate from shipment status.

LOGISTIC numeric trackingStatus is documented:0 no information;1 warehouse outbound;2 forwarder received;3 forwarder return;4 forwarder dispatched;5 international transit;6 destination country;7 customs started;8 customs cleared;9 last-mile pickup;10 out for delivery;11 pickup ready;12 delivered;13 failed/exception;14 return. This is **not** the REST trackInfo free-string enum. TrackInfo's “In transit” example cannot define every carrier status. Keep raw status and source endpoint. [Logistics payload](https://developers.cjdropshipping.com/en/api/start/webhook.html#logistics-message).

PRIVATE_ORDER states: WAIT_PAY, PAYMENT_INCOMING, PAID, WAIT_SHIPMENT, INTERCEPTING, INTERCEPT, SHIPPED, COMPLETED, OVER, CANCELLED, REFUND_COMPLETE, RESEND_OVER. MAKEUP diffUseType0=order adjustment,1=balance top-up,2=repayment,3=transfer shipping fee. These private/adjustment values must not become ordinary customer-order enums. No general dispute/refund, ticket, category-tree or warehouse-directory webhook is documented.

## Exact authentication

`sign = Base64(HMAC-SHA256(key=UTF8(saved openId string), message=unaltered raw HTTP body bytes))`

Use standard padded Base64 and constant-time comparison. The key is **openId**, not apiKey/accessToken and not an independently issued webhook secret. Select saved credentials from trusted connection routing; never use payload-provided openId to choose or override a verification key. Preserve exact IDs losslessly. Verify before schema processing, then bind any payload identity/order/product to that connection. [Signing contract](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication).

Important residual risk: openId is both account identifier and signing-secret material, and some payloads expose it. Possession enables forgery. Do not leak raw pushes, access-token responses, logs, exception dumps or support screenshots. CJ explicitly warns about sharing these with third-party integration providers; hosted PulseSoc custody requires CJ clarification. No independent key rotation, cryptographic freshness timestamp, nonce, key version, mandatory mTLS or authoritative source-IP allowlist is documented. Do not invent one.

Compensating controls: trusted opaque connection route, private encrypted credential lookup, gateway/WAF rate limits, bounded raw body/schema, durable inbox, topic/object allowlists, replay detection, and **authenticated provider read-back before financial or irreversible state effects**. Signatures do not grant customer-payment authority. If key compromise is suspected, stop processing consequential events, reconcile via outbound authenticated reads and seek CJ key/account remediation; refreshing an access token is not proven to rotate openId.

## Delivery and failure semantics

| Requirement | Verified / unresolved |
| --- | --- |
| Transport |Public HTTPS POST; application/json; TLS1.2/1.3 recommended |
| ACK |HTTP200 within3seconds; sample JSON success bodies exist, exact mandatory body not stated |
| Retry |MAKEUP/PRIVATE_ORDER sections say same as other topics, up to3 retries; timing, total-attempt interpretation, status eligibility and retention unspecified |
| Auto-close |Per-topic hourly metrics; if each preceding2 complete hours is below80% success (configurable), topic closes; manual reactivation required |
| Event ID |messageId present; unchanged on retry explicitly stated for MAKEUP/PRIVATE_ORDER, not individually for all topics; global uniqueness scope undocumented |
| Ordering |No sequence, timestamp freshness window, delivery ordering or exactly-once guarantee established |
| Replay/history |No management endpoint for delivery history or replay established |
| Failover |One callback/topic; failover and zero-event-hour auto-close behavior not specified |

Sources: [Webhook transport](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#webhook-configuration-requirements), [auto-close](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_3-3-auto-close-mechanism), [retry statement](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_4-3-response-requirements).

Proposed receiver sequence: verify raw signature → validate trusted connection/topic/object → transactionally save encrypted/minimized inbox with dedup key and body digest → ACK200 under3s → async normalize/read-back/apply. Do not ACK work that has not been durably accepted. Duplicate valid receipts may ACK after confirming durable original receipt. Invalid signatures reject; owned but unknown event types quarantine without making up a state. Same messageId with a different body is a security/data conflict, not a duplicate to ignore. Enforce no automatic money mutation even for signed payloads.

Bounded reconciliation covers inactive subscriptions, auto-closed topics, stock freshness, pending shipments, splits and supplier adjustments. Regular order INSERT is not promised, so initial order persistence must come from the create response/read-back. STOCK storageNum is a legacy-shaped example with no verified/factory/subwarehouse split, event timestamp or snapshot/delta guarantee: do not apply it as a private inventory ledger change.

## Sandbox and verification plan, not executed

Order creation with isSandbox=1 uses the same host and simulates funding without real logistics. POST shopping/sandbox/simulatePay, updateStatus and updateTrackNumber provide state/data controls; their exact full URLs are in [requirements](CJ_API_REQUIREMENTS.md). They do not document a universal webhook delivery simulator or duplicate/out-of-order/retry fixture service.

After separate approval and CJ account/egress/custody clarification: verify real callback signing against an approved sandbox account, all topic schemas/routing and long-ID behavior, per-ID subscription outcomes, registration reachability probe, manual reactivation, state/track update callbacks, repeated events, crash boundaries and signed-body redaction. Use local synthetic fixtures for invalid signatures/replay/order permutations; label them synthetic, not observed CJ behavior. No production testing should precede a mapped, isolated sandbox plan.

