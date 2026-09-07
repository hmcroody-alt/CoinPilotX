# CJ webhook, settings, sandbox, limits, and ticket evidence

Inspected current official CJ documentation on 2026-09-07. Discovery only: no authenticated CJ requests, registrations, purchases, ticket submissions, production changes, or integration code. HTML heading IDs were read directly to verify anchors. These are documentation findings, not tested account capabilities.

## Official sources fully inspected for this assignment

- [Introduction](https://developers.cjdropshipping.com/en/api/introduction.html)
- [Start: Webhook Mechanism](https://developers.cjdropshipping.com/en/api/start/webhook.html)
- [API: Webhook](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html)
- [Settings](https://developers.cjdropshipping.com/en/api/api2/api/setting.html)
- [Sandbox](https://developers.cjdropshipping.com/en/api/start/sandbox.html)
- [Interface Call Restrictions](https://developers.cjdropshipping.com/en/api/start/limit.html)
- [Points Resource Rules](https://developers.cjdropshipping.com/en/api/api2/standard/points.html)
- [Ticket](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html)

Additionally inspected Shopping sandbox sections 1.11, 1.12, 1.15, relevant sandbox fields in create/pay/query, and batch-order limits. The complete Shopping section belongs to the order research assignment.

## Webhook API inventory

Base: `https://developers.cjdropshipping.com/api2.0/v1`. All listed management calls use `CJ-Access-Token`; POST examples use JSON.

| Method and path | Request contract | Response / behavior | Official section |
|---|---|---|---|
| POST `/webhook/set` | `product`, `stock`, `order`, `logistics`: objects marked required; `makeup`, `privateOrder`: optional objects. Each uses `type` = `ENABLE` or `CANCEL`, and `callbackUrls`: array containing exactly one reachable public HTTPS URL. | `data:true`; invalid callback example code `1607001`. No partial-update or replacement semantics stated. | [Message setting](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_1-1-message-setting-post) |
| POST `/webhook/product/subscribe` | `productIds:string[]`, max 100; `subscribeAll:boolean`. Table marks both optional but current policy requires product IDs. | `data.successProductIds`, `failProductIds`, `subscribeAll`; already subscribed/nonexistent products may appear among failures. Inspect per-ID results despite outer success. | [Subscribe](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_2-1-subscribe-products-post) |
| POST `/webhook/product/unsubscribe` | Required `productIds:string[]`, max 100. | Boolean `data`. | [Unsubscribe](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_2-2-unsubscribe-products-post) |
| GET `/webhook/product/subscribe/list` | Required `shopId`; optional `pageNum` default/min 1; `pageSize` default20/min1/max200; `sku` SPU filter max21; `productId` max50. | `data.pageSize,pageNumber,totalRecords,totalPages,content[]`; item fields `productId,sku,productName,productImage,status,reason,createAt`. `status=false` means inactive; reason can identify delisting. | [Subscribed list](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_2-3-query-subscribed-products-get) |

Management envelopes document `code,result,message,data,requestId,success`. `requestId` is a request diagnostic identifier; it is not documented as webhook event identity.

### Registration and filtering rules

Current date is after July 2026: the documented `subscribeAll=true` facility is unavailable for all users. Earlier eligibility applied only to accounts registered before June 2026, before July. Explicit product subscription is necessary for PRODUCT, VARIANT, and STOCK notifications; enabling callback topics alone is insufficient. The API still describes historical subscribe-all filtering, a documentation inconsistency to retain as a compatibility issue. [Subscription](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_2-1-subscribe-products-post), [filtering](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_3-notification-rules).

| Subscription level | Product cap |
|---|---:|
| lv1 | 1000 |
| lv2 | 2000 |
| lv3 | 3000 |
| lv4 | 5000 |
| lv5 | 10000 |

Subscription errors: `1606010` product webhook disabled; `1606011` limit exceeded; `1606012` unsubscribable product; `1606013` subscribe failure. No lv0 subscription cap or explicit mapping from membership names to these subscription levels was found. List requires `shopId` although subscribe/unsubscribe have no shopId field; scope must be confirmed. Batch atomicity and whether a subsequent productIds batch replaces or adds to existing product subscriptions are not expressly specified; do not infer these from historical subscribe-all replacement language.

### Transport, authentication, acknowledgement, retries

Official signing contract: `sign` HTTP header contains standard padded Base64 of HMAC-SHA256; key is the stored account `openId` converted to a string; message is the exact raw request body. API key is not the signing key. The Python example uses UTF-8 and constant-time comparison. Use the previously obtained account identifier as key, never a caller-selected payload identifier. [Signature authentication](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication).

The docs explicitly treat `openId` as secret because disclosure enables signed-request forgery. Several topic payloads include it. Restrict receiver traffic, raw-body logs, exception payloads, support attachments and monitoring accordingly. An example that logs all callback parameters conflicts with that newer security guidance. [Signature security notice](https://developers.cjdropshipping.com/en/api/start/webhook.html#_2-signature-authentication), [legacy listener example](https://developers.cjdropshipping.com/en/api/start/webhook.html#listening-example).

Delivery: public HTTPS, POST, JSON; TLS1.2/1.3 recommended. Acknowledge HTTP200 within 3 seconds. Samples return a JSON success body, but a mandatory exact ACK body is not specified. The MAKEUP and PRIVATE_ORDER response sections say, as for other topics, failed delivery is retried up to three times. Retry schedule/backoff, whether this counts retries versus total attempts, retention period, and retryable-status classification remain undocumented. [Requirements](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#webhook-configuration-requirements), [retry statement](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_4-3-response-requirements).

CJ tracks hourly results per topic. A topic closes automatically if success is below 80% in each of the two previous complete hours; threshold is described as configurable. Reactivation is manual and a reason is recorded. No configuration endpoint, zero-volume-hour rule, delivery-history/replay API, or callback failover is documented in this section. [Auto-close](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_3-3-auto-close-mechanism).

No signed event timestamp, signature timestamp/header version, nonce, replay tolerance, sequence number, ordering guarantee, IP allowlist, mTLS requirement, key rotation API, universal event-ID uniqueness scope, or exactly-once guarantee was found in the inspected sections. This means **not documented**, not proven absent in CJ's infrastructure. MAKEUP and PRIVATE_ORDER explicitly retain `messageId` across retries; other topics expose messageId but do not state this retry invariant individually. [MAKEUP](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_4-makeup-bill-message), [PRIVATE_ORDER](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_5-private-order-message-privateorder).

Engineering inference: a future receiver should verify raw bytes, persist a durable deduplication receipt before ACK, process asynchronously, and reconcile relevant CJ records through read APIs. Business dates cannot substitute for a signed delivery freshness mechanism. Retry exhaustion and automatic closure make polling/reconciliation necessary. These are design recommendations, not implemented behavior.

### Event and payload map

Common documented envelope: `messageId`, `type`, `messageType`, `params`; some examples also include top-level `openId`. Payload examples contain nullable fields even when tables mark them required. Preserve update-field semantics; do not treat every null as an instruction to erase a stored value.

| `type` | `messageType` / trigger | Principal `params` fields and special notes | Official source |
|---|---|---|---|
| `PRODUCT` | `INSERT,UPDATE,DELETE`; creation/change | `pid,categoryId,categoryName,productDescription,productImage,productName,productNameEn,productProperty1/2/3,productSellPrice,productSku,productStatus,fields[]`; productStatus2=off,3=on. | [Product](https://developers.cjdropshipping.com/en/api/start/webhook.html#product-message-product) |
| `VARIANT` | `INSERT,UPDATE,DELETE` | `vid,variantName,variantWeight,variantLength,variantWidth,variantHeight,variantImage,variantSku,variantKey,variantSellPrice,variantStatus,variantValue1/2/3,fields[]`; dimensions mm, weight g, price USD; status0=off,1=on. | [Variant](https://developers.cjdropshipping.com/en/api/start/webhook.html#inbound-message-for-variant) |
| `STOCK` | sample `UPDATE` | `params` is map keyed by VID to warehouse rows: `vid,areaId,areaEn,countryCode,storageNum`. No explicit per-topic schema table or event-time field. | [Stock](https://developers.cjdropshipping.com/en/api/start/webhook.html#stock-message) |
| `ORDER` | table lists `INSERT,UPDATE,DELETE,ORDER_CONNNECTED` (literal spelling); regular orders do not receive INSERT per newer rule | `cjOrderId,orderNumber,orderNum` deprecated, `orderStatus,logisticName,trackNumber,trackingUrl,trackingProvider,createDate,updateDate,payDate,deliveryDate,completeDate,privateOutboundOrder,orderItems[]`. Items: `vid,quantity,sellPrice,lineItemId,storeLineItemId,productionOrderStatus,abnormalType[]`. Re-association event returns actual CJ order ID. Private outbound orders get INSERT with `privateOutboundOrder=true`, followed by UPDATE. | [Order](https://developers.cjdropshipping.com/en/api/start/webhook.html#order-message), [outbound clarification](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_3-4-private-inventory-outbound-order-notification) |
| `ORDERSPLIT` | `INSERT,UPDATE,DELETE`; sample UPDATE | `originalOrderId,orderSplitTime,splitOrderList[]`; split rows `orderCode,createAt,orderStatus,productList[]`; product `sku,vid,quantity,productCode`. Numeric createAt example versus string schema requires tolerant normalization. | [Split](https://developers.cjdropshipping.com/en/api/start/webhook.html#order-splitting-message) |
| `SOURCINGCREATE` | sample UPDATE | `cjProductId,cjVariantId,cjVariantSku,cjSourcingId,status,failReason,createDate`. Its schema incorrectly labels type ORDERSPLIT; sample says SOURCINGCREATE. Registration-topic routing not explicitly identified. | [Sourcing result](https://developers.cjdropshipping.com/en/api/start/webhook.html#source-product-creation-result) |
| `LOGISTIC` | `INSERT,UPDATE,DELETE`; sample UPDATE | `orderId,storeOrderNumbers[],logisticName,trackingNumber,trackingUrl,trackingProvider,trackingStatus,logisticsTrackEvents`. Events arrive as JSON-encoded string, not an outer JSON array; provider may be null for older records. | [Logistics](https://developers.cjdropshipping.com/en/api/start/webhook.html#logistics-message) |
| `MAKEUP` | INSERT→CREATED, CANCEL→CANCELED, PAID→PAID | `orderId` BT bill number (`orderCode` in makeup list), `relationOrderId` original order, `payOrderId,amount,reason,type,diffUseType,status,createDate,paymentDate`. Amount USD; type1; diffUseType0=order adjustment,1=top-up,2=repayment,3=transfer freight. | [Makeup](https://developers.cjdropshipping.com/en/api/start/webhook.html#makeup-bill-message-makeup) |
| `PRIVATE_ORDER` | UPDATE; SY inventory order status changes | `orderId` SY+19 digits, optional `orderNumber`, `status,orderType` always2, `createDate,paymentDate,deliveryDate,completeDate`. Excludes dropshipping/deposit orders. Separate from ORDER private outbound traffic. | [Private order](https://developers.cjdropshipping.com/en/api/start/webhook.html#private-order-message-private-order) |

LOGISTIC trackingStatus: `0` no information, `1` warehouse outbound, `2` forwarder received, `3` forwarder return, `4` forwarder dispatched, `5` international transit, `6` destination country, `7` customs started, `8` customs cleared, `9` last-mile pickup, `10` out for delivery, `11` pickup ready, `12` delivered, `13` failed/exception, `14` return. [Logistics enum](https://developers.cjdropshipping.com/en/api/start/webhook.html#logistics-message).

PRIVATE_ORDER status enum: `WAIT_PAY,PAYMENT_INCOMING,PAID,WAIT_SHIPMENT,INTERCEPTING,INTERCEPT,SHIPPED,COMPLETED,OVER,CANCELLED,REFUND_COMPLETE,RESEND_OVER`. Date strings use `yyyy-MM-dd HH:mm:ss`; the webhook sections do not establish one universal timezone. [Private-order statuses](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html#_5-2-status-values).

Additional schema cautions: PRODUCT messageId length200 versus VARIANT/ORDER50; generic enum tables can overstate actual insert behavior; ORDER example orderItems is malformed JSON; VARIANT example has trailing comma. MAKEUP/PRIVATE_ORDER Start payloads include openId while API examples omit it. Examples need contract validation before becoming fixtures. No explicit refund/dispute or ticket webhook topic was found; PRIVATE_ORDER refund-complete is narrower than a general refund event.

## Settings and resource limits

`GET /setting/get`, `CJ-Access-Token`, no documented parameters. Result includes `openId,openName,openEmail,setting.quotaLimits[],setting.qpsLimit,callback,root,isSandbox`. Quota row: `quotaUrl,quotaLimit,quotaType`; types0=total,1=year,2=quarter,3=month,4=day,5=hour. `root`: `NO_PERMISSION,GENERAL,VIP,ADMIN`. Callback sample uses `urls`, whereas webhook setting writes `callbackUrls`. `isSandbox` has a boolean sample and byte table; settings is object in sample but list in table. Example qpsLimit100 and quotaLimit74 are examples, not entitlements. No authenticated settings were read. [Settings](https://developers.cjdropshipping.com/en/api/api2/api/setting.html#_1-1-get-settings-get).

| Limit dimension | Documented rule |
|---|---|
| IP rate | 10 requests/second/IP |
| Non-login interfaces | Maximum30 requests/second; scope interaction with IP limit not explained |
| Account sharing an IP | Maximum3 users/IP |
| Free / sales0–1 | 1 request/second |
| Plus / sales2 | 2 requests/second |
| Prime / sales3 | 4 requests/second |
| Advanced / sales4–5 | 6 requests/second |
| Ticket family override | 1 request/second; serial calls with a gap |
| Batch order detail override | 2 requests/second/account |

Sources: [base](https://developers.cjdropshipping.com/en/api/start/limit.html#base-frequency), [tiers](https://developers.cjdropshipping.com/en/api/start/limit.html#special-frequency), [Ticket](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html#_10-ticket), [batch detail](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-16-query-orders-in-batch-post). Conservative design inference: enforce overlapping global/account/endpoint buckets and refresh actual settings; never adopt sample100 QPS as account capacity.

### Points, active on the inspection date

Daily call-count quotas were replaced June1,2026 for newer accounts, July1 for earlier accounts. Base allocation50,000/day resets00:00UTC. Additional daily allocation = maximum of last three months' transaction amounts ×100 points/USD; recalculated daily. Base points are consumed before conversion points. Available points replenish per minute at total/1440; `remaining` is presently spendable points, capped at total and nonnegative. Initial midnight bucket funding/rounding and transaction calculation details are not specified. At base allocation the average replenishment is approximately34.72/minute (calculation). [Points rules](https://developers.cjdropshipping.com/en/api/api2/standard/points.html#daily-available-points), [replenishment](https://developers.cjdropshipping.com/en/api/api2/standard/points.html#per-minute-points-replenishment).

| Points/call | Exact listed suffixes |
|---:|---|
| 50 | `/product/listV2`; `/product/list` |
| 10 | `/product/query`; `/product/variant/query`; `/product/variant/queryByVid`; `/product/stock/queryByVid`; `/product/stock/queryBySku`; `/product/stock/privateInventory/querySpuPage`; `/product/stock/privateInventory/querySkuListByProductId`; `/product/stock/privateInventory/querySkuDetailPage`; `/product/stock/privateInventory/querySkuDetailListBySku`; `/product/stock/privateInventory/querySkuFlowByCondition`; `/storehouseCenterWeb/syncStorehouseVideoRequests`; `/product/stock/getInventoryByPid`; `/logistic/freightCalculate`; `/logistic/freightCalculateTip`; `/logistic/partnerFreightCalculate`; `/logistic/getSupplierLogisticsTemplate`; `/webhook/product/subscribe`; `/webhook/product/unsubscribe` |
| 1000 | `/product/queryProductsByImage` |
| 0 under stated rule | Endpoints omitted from the cost table |

[Official cost table](https://developers.cjdropshipping.com/en/api/api2/standard/points.html#api-endpoint-point-costs). Insufficient points: HTTP429 with `pointsInfo.usedToday,remaining,total`; docs say every API response includes pointsInfo, although many endpoint examples omit it. Rate rejection and insufficient-points429 require different scheduling. Never assume zero-point calls bypass QPS. API access suspends after30 consecutive days with no CJ transaction volume; reminder after more than7 days; manual reactivation through CJ's API account page. [Exhaustion](https://developers.cjdropshipping.com/en/api/api2/standard/points.html#insufficient-points), [suspension](https://developers.cjdropshipping.com/en/api/api2/standard/points.html#api-access-suspension-policy).

## Sandbox contracts and limitations

Sandbox is explicitly selected with JSON `isSandbox:1` during order creation; default0 creates a normal order. Same documented API host and `CJ-Access-Token` are used. No separate sandbox base URL, sandbox-specific authentication header, isolated catalog/account credentials, test balance reset, or automatic cleanup contract appears in the inspected Sandbox page. Settings' account-level `isSandbox` does not eliminate the need to set the documented order flag. Sandbox suppresses real balance deduction, fulfillment, logistics and labels. Query/list returns isSandbox. [Sandbox](https://developers.cjdropshipping.com/en/api/start/sandbox.html#sandbox-order-creation).

| POST endpoint | Required JSON / restrictions |
|---|---|
| `/shopping/sandbox/simulatePay` | At least one `orderId` or `shipmentOrderId` (strings max200). A parent with multiple suborders must be paid via shipmentOrderId. Only flagged sandbox orders. Moves unpaid to paid300. |
| `/shopping/sandbox/updateStatus` | `orderId` string max200; `targetStatus` integer400/500/600/700. Requires sequential300→400→500→600→700; no skips or reversals. |
| `/shopping/sandbox/updateTrackNumber` | `orderId` string max200; `trackNumber` string max64. Paid and not closed (300–499,500,600–601). Repeated writes replace prior value; no authenticity/uniqueness check. Read back from trackNumber; existing forwarder relabel may take priority. |

All use JSON/CJ-Access-Token, return boolean data. [Simulate pay](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-11-sandbox-simulate-pay-post), [status](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-12-sandbox-update-status-post), [tracking](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-15-sandbox-update-track-number-post).

Tracking errors803=missing order,811=non-sandbox,818=wrong account,819=unpaid/closed,820=missing logistics data,821=persistence failure. Tracking docs mention downstream fulfillment-callback testing, but do not specify a webhook simulator endpoint, topic firing guarantee, failure/duplicate/out-of-order simulator, or event-time controls. Do not declare webhooks untestable; distinguish the documented data/status simulation from delivery cases that require verification. No sandbox operation was run.

## Ticket API: optional support operations, not commerce core

All paths share the base and `CJ-Access-Token`; JSON for POST. This is an optional support workflow suitable for a later operator tool. It is unnecessary for product import, price/stock reconciliation, freight quotes or normal order placement. Ticket creation/reply/remind transmit communications and must remain explicitly authorized operations.

| Method/path | Fields |
|---|---|
| POST `/ticket/list` | Optional `pageNum` default1, `pageSize` default20/max100, `searchType` TICKET_NO/CJ_ORDER_NO/YOUR_ORDER_NO paired with `search` max100, `ticketTypeId`, `status`, `shopIds[]`. |
| GET `/ticket/detail` | Required `ticketId`. |
| GET `/ticket/messages` | Required `ticketId`; pageNum default1,pageSize default20/max100. |
| GET `/ticket/types` | None; returns ticket types→question types→required form definitions. |
| POST `/ticket/create` | Required `ticketTypeId,questionTypeId,message,expectResult`; text max1000. Conditional `formList[{fieldName,value}]`; optional `cjOrderNo,yourOrderNo,sku` max100, `attachments[{fileName,url}]` max10, `requestNo` max64. |
| POST `/ticket/reply` | Required `ticketId,message` (5–1000); optional attachments. |
| POST `/ticket/complete` | Required `ticketId`; check canComplete. |
| POST `/ticket/remind` | Required `ticketId`; check canRemind/hasReminded. |
| POST `/ticket/evaluate` | Required `ticketId,level` GOOD/AVERAGE/POOR; optional comment max1000. |
| GET `/ticket/pendingCount` | None; returns pendingCount/updateTime. |
| POST `/ticket/attachment/upload` | Required `fileName,sourceUrl`; public downloadable HTTP(S) URL. |
| GET `/ticket/order/questionTypes` | Required `type` ORDER_ISSUE/PRODUCT_ISSUE; separate question dataset. |
| POST `/ticket/order/create` | Required `businessNo` CJ-order/sourcing identifier max100, `type`, `questionTypeId`, `message,expectResult` max1000; optional `yourOrderNo` max100,attachments. |

[Ticket endpoint inventory](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html). Filterable statuses: `AWAITING_CJ_REPLY,AWAITING_YOUR_REPLY,COMPLETED,EXPIRED,REVIEWED`; response-only `ON_HOLD,COLLABORATING`; numeric/unknown filters are errors. Detail exposes canReply/canComplete; logistics tickets cannot be user-closed. One reminder per ticket after an unspecified waiting interval. Returned timestamps are UTC milliseconds; legacy status can be null.

Critical correction dated2026-09-07: create's `requestNo` blocks duplicates for only5 seconds; it is not durable idempotency. Create returns estimatedHandleTime, not ticketId. Reconcile newest tickets before resubmitting after timeout. Reply and order-specific creation explicitly have no idempotency protection; reconcile messages or order-filtered ticket list before retry. [Create semantics](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html#_5-create-ticket-post), [reply](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html#_6-reply-post), [order support creation](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html#_13-create-ticket-for-an-order-sourcing-post).

Attachments max5MB each; allowed extensions `jpg,jpeg,png,bmp,gif,txt,rar,zip,doc,docx,xls,xlsx,pdf,ini,conf,eml`. Source rejects private/loopback/metadata URLs; target filename requires extension and rejects separators, traversal and markup/quotes. Use returned CJ URL unchanged in attachments. Message API supplies HTML and plain text; future client should prefer plain text or sanitized rendering despite docs suggesting HTML rendering. [Upload](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html#_11-upload-attachment-post).

Ticket errors1609000–1609016 cover status/filter/page/query/search/ownership/duplicate/type/create/state/reminder/form/review/operation/attachment cases;1600200 is QPS. Missing and foreign-owned ticket IDs both return1609005. Discovery classified endpoints without invoking any.

## Questions requiring CJ clarification or an authorized contract test

1. Product subscription scope with required shopId only on list; additive/replacement/atomic behavior of repeated batches; lv0 and membership-to-subscription-level mapping.
2. Callback partial-setting semantics, ORDERSPLIT/SOURCINGCREATE routing, exact ACK body, retry count interpretation/schedule/retention, redelivery API, ordering and messageId scope for all topics.
3. Signing-key rotation and compromise recovery for openId; whether timestamp/replay protections exist beyond the published contract.
4. Sandbox webhook triggering and deterministic replay/failure/ordering simulation; order cleanup and limits.
5. Interaction among settings.qpsLimit, endpoint limits, tier limits, 10/IP and30 non-login; point-bucket initialization/rounding; authoritative transaction conversion rules.

These gaps should be tracked as unresolved contracts, not filled with assumptions or represented as production verification.
