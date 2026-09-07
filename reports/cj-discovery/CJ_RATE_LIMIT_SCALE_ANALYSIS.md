# CJ rate limits and scale analysis

Checked 2026-09-07. Published constraints, calculations and proposed controls are separated below. No authenticated settings or load tests were run.

## Published constraints

| Dimension | Current documented constraint | Consequence |
| --- | --- | --- |
| Egress IP | ≤10 requests/second and **≤3 CJ users/IP** | A standard shared SaaS worker is not documented to support hundreds of merchant accounts |
| Non-login interfaces | Maximum30 requests/second; exact aggregate scope unclear | Ask CJ how it intersects with IP/account restrictions |
| Account tiers | Free/sales0–1:1/s; Plus/2:2/s; Prime/3:4/s; Advanced/4–5:6/s | Per-account limiter, not independent quota per shop/API key |
| Token acquisition | Explicit1/s | Refresh/exchange locks and persisted tokens, no per-request login |
| Ticket family |1/s, calls serially with a gap | Dedicated paced support queue |
| Batch order detail and proof of delivery |2 requests/s/account, maximum100 IDs (20–50 recommended for batch detail) | Intersect with stricter account/IP limits; inspect omitted records |
| Product subscriptions |lv1:1000; lv2:2000; lv3:3000; lv4:5000; lv5:10000 | Product caps independent of points; lv0/membership mapping not documented |
| Subscription batches |Subscribe/unsubscribe max100 product IDs; list max200/page | No subscribeAll for any account after July2026 |
| Callback destinations |One public HTTPS URL/topic | Approved ingress and internal tenant-safe fan-out |
| Account activity |API access suspension after30 consecutive days with zero CJ transaction amount; reminder after more than7days | Trial/idle merchants need explicit reactivation state, not endless token retries |

Sources: [Frequency restrictions](https://developers.cjdropshipping.com/en/api/start/limit.html), [auth](https://developers.cjdropshipping.com/en/api/api2/api/auth.html), [Ticket](https://developers.cjdropshipping.com/en/api/api2/api/ticket.html), [Shopping batch detail](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-16-query-orders-in-batch-post), [subscriptions](https://developers.cjdropshipping.com/en/api/api2/api/webhook.html), [points/activity policy](https://developers.cjdropshipping.com/en/api/api2/standard/points.html).

No complete shop-count limit, per-API-key isolation, SaaS multi-tenant exemption, burst allowance or provider-wide throughput commitment is published in the reviewed contract. settings.qpsLimit and quotaLimits must be read for each authorized account; the sample100 QPS is not an entitlement. API quotas remain relevant even for zero-point endpoints.

## Daily points versus currently spendable points

Current policy superseded daily call-count quotas on2026-06-01 for newer accounts and2026-07-01 for earlier accounts. Daily total =50,000 base points +100 × maximum of the last three months' transaction amounts in USD. CJ recalculates conversion daily; clarify exact month/settlement/refund accounting before relying on it. Base points are consumed first.

UTC00:00 is the documented daily reset. Replenishment is **total/1440 per minute**, capped at daily total. Response pointsInfo exposes usedToday, remaining and total; remaining is currently spendable balance. Initial bucket funding, rounding and exact reset/replenishment interaction are not stated, so50,000/day is not an immediate burst budget. At base allocation, replenishment averages34.7222 points/minute or0.578704/second. HTTP429 can signal point exhaustion; global errors also list16900500,1600200 and1600201 for points/rate/quota contexts. [Points](https://developers.cjdropshipping.com/en/api/api2/standard/points.html), [global errors](https://developers.cjdropshipping.com/en/api/api2/standard/ps-code.html).

## Per-endpoint matrix

R means all applicable account/IP/non-login constraints above plus actual settings. Page/batch controls are in the matching [requirements row](CJ_API_REQUIREMENTS.md); an undocumented limit is not unlimited. This lists every operation counted in the report. Costs are per call, not per returned item. Unlisted endpoints are zero-point under the generic table, **not** free of authorization or QPS limits.

| HTTP | Exact endpoint | QPS / points |
| --- | --- | --- |
| POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/getAccessToken | 1 QPS explicit; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/refreshAccessToken | R; 0 pt; 24h account cache |
| POST | https://developers.cjdropshipping.com/api2.0/v1/authentication/logout | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/setting/get | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shop/getShops | R; 0 pt; no endpoint-specific annotation |
| POST | https://developers.cjdropshipping.com/api2.0/v1/store/product/saveProduct | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/store/product/saveVariantBatch | R; 0 pt; batch maximum not specified |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shop/product/queryPage | R; 0 pt; max10/page |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shop/product/queryDetail | R; 0 pt; max10 IDs |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/conn/connection | R; 0 pt; max100/page |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/conn/connection | R; 0 pt |
| DELETE | https://developers.cjdropshipping.com/api2.0/v1/product/conn/connection | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/packaging/connection/create | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/packaging/connection/query | R; 0 pt; max list not specified |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/packaging/connection/delete | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/packaging/connection/variantConditionPackList | R; 0 pt; max batch unspecified |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/getCategory | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/listV2 | R; 50 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/globalWarehouseList | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/list | R; 50 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/query | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/addToMyProduct | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/myProduct/query | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/variant/query | R; 10 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/variant/queryByVid | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/productDetail/query | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/packagingProduct/query | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/cjPackaging/query | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/individualization/add | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/queryProductsByImage | R; 1000 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/queryVideosByProductId | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/stock/queryByVid | R; 10 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/stock/queryBySku | R; 10 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/stock/getInventoryByPid | R; 10 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/warehouse/detail | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/stock/privateInventory/querySpuPage | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/stock/privateInventory/querySkuListByProductId | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/stock/privateInventory/querySkuDetailPage | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/stock/privateInventory/querySkuDetailListBySku | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/stock/privateInventory/querySkuFlowByCondition | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/storehouseCenterWeb/syncStorehouseVideoRequests | R; 10 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/productComments | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/sourcing/create | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/product/sourcing/query | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/product/sourcing/queryList | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/createOrderV2 | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/createOrderV3 | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/addCart | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/addCartConfirm | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/saveGenerateParentOrder | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/list | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getOrderDetail | R; 0 pt |
| DELETE | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/deleteOrder | R; 0 pt |
| PATCH | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/confirmOrder | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/changeWarehouse | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/sandbox/simulatePay | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/sandbox/updateStatus | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getOrderLogisticsInfo | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/updateLogistics | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/sandbox/updateTrackNumber | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getOrderDetailBatch | R + endpoint ≤2/account/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/getProofOfDelivery | R + endpoint ≤2/account/s; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/shopping/pay/getBalance | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/pay/payBalance | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/pay/payBalanceV2 | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/uploadWaybillInfo | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/updateWaybillInfo | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/podProductCustomPicturesEdit | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/order/queryCogsBasicDataOrderInfoList | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculate | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/freightCalculateTip | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/partnerFreightCalculate | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/getSupplierLogisticsTemplate | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/logistic/unavailableShippingMethods | R; 10 pt endpoint / 0 pt generic-table conflict |
| GET | https://developers.cjdropshipping.com/api2.0/v1/logistic/trackInfo | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/disputes/disputeProducts | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/disputes/disputeConfirmInfo | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/disputes/create | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/disputes/cancel | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/disputes/getDisputeList | R; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/disputes/getDisputeDetail | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/mergeOrder/autoMatchMergeOrderListV3 | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/mergeOrder/autoMergeQueryProgress | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/mergeOrder/autoMergeQueryResult | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/mergeOrder/submitMergeOrderBatchV3 | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/mergeOrder/submitProgress | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/mergeOrder/submitResult | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/address/list | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/address/create | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/address/update | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/addToCart | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/getCart | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/removeFromCart | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/getConfirmation | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/logistics/list | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/getCostInfo | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/getConfirmationWithCost | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/createOrder | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/order/list | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/address/list | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/product/query | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/product/batch-refresh | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/create | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/progress | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/submit | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/detail | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/privateInventory/outbound/cancel | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/makeup/list | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/shopping/makeup/createPayOrder | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/set | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe | R; 10 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/unsubscribe | R; 10 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/webhook/product/subscribe/list | R; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/list | R + Ticket ≤1/s; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/ticket/detail | R + Ticket ≤1/s; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/ticket/messages | R + Ticket ≤1/s; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/ticket/types | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/create | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/reply | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/complete | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/remind | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/evaluate | R + Ticket ≤1/s; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/ticket/pendingCount | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/attachment/upload | R + Ticket ≤1/s; 0 pt |
| GET | https://developers.cjdropshipping.com/api2.0/v1/ticket/order/questionTypes | R + Ticket ≤1/s; 0 pt |
| POST | https://developers.cjdropshipping.com/api2.0/v1/ticket/order/create | R + Ticket ≤1/s; 0 pt |

The Unavailable Shipping Methods page states10 points but is absent from the generic cost table that says unlisted endpoints cost0. Budget10 conservatively and ask CJ; do not exploit alternate endpoints to bypass the points policy. Old List's1000/day prose and fixed20/page prose are superseded/conflicted by current points/page-size tables. [Logistics diagnostic](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html#_1-5-unavailable-shipping-methods-post), [Product](https://developers.cjdropshipping.com/en/api/api2/api/product.html).

## Pagination and burst constraints

| Operation | Bound / recovery requirement |
| --- | --- |
| ListV2 |page1–1000; size1–100 default10; totalRecords capped6000. Does not promise exhaustive catalog export |
| Legacy list |pageNum default1; pageSize table max200/default20 conflicts with fixed20 prose; not selected |
| Shop product page/detail |page max10 products; detail max10 product IDs |
| Product connection list |pageSize default10/max100 |
| Product subscription list |pageSize default20/max200; shopId required |
| Batch order detail / proof |max100 IDs; missing/foreign items omitted, so reconcile by ID not list index |
| Order list |pageSize default20; maximum not established; payment filters paired, ≤90days, UTC, exclude unpaid and incompatible with CANCELLED |
| Sourcing queryList |max100 repeated sourceIds; missing omitted; all-missing fails |
| Ticket list/messages |pageSize default20/max100 |
| Private order / makeup list |max200/page |
| Private inventory queries, reviews, My Product |Several maxima/pagination controls undocumented; keep bounded and validate before relying on full scans |

With an empty base-points bucket, a10-point call needs about17.28 seconds of replenishment, a50-point search86.4 seconds, and a1000-point image search28.8 minutes. These are calculations from continuous averages; actual minute granularity/rounding may increase waits. Accumulated balance can permit bursts, subject to QPS. Ten parallel searches must not blindly retry through an empty bucket.

Example cold import:100 product-detail reads +100 product-scoped inventory reads +1 subscription batch =201 calls/2010 points, if product details already supply the necessary variants. At1/s the request-rate floor is201 seconds; starting from zero available points, the refill floor is57.9 minutes. Search and optional dedicated variant calls add cost. This is an illustration, not a CJ latency promise.

## Explicit steady-state model

Assume per merchant:100 active products,10 searches/day,20 product detail reads,10 variant reads, two product-inventory refreshes/day for each product (200 calls),10 shipping quotes,1 subscription batch,5 supplier orders/day, and modest recovery. Treat product sets as disjoint only for the central-account subscription stress case.

| Per-merchant activity | Calls/day | Points/day |
| --- | ---: | ---: |
| Search10×50 |10 |500 |
| Product detail20×10 |20 |200 |
| Variant reads10×10 |10 |100 |
| Inventory200×10 |200 |2000 |
| Shipping quotes10×10 |10 |100 |
| Subscription batch1×10 |1 |10 |
| Five orders ×8 non-point calls: create/cart/confirm/parent/balance/pay/detail/tracking |40 |0 |
| Account/shop/subscription-list checks3 + batch order reconciliation10 |13 |0 |
| **Total** |**304** |**2910** |

This deliberately bounded baseline omits traffic peaks, customer browsing beyond search, repeated failures, images, optional private inventory, dispute/support traffic and extra quote/detail refreshes. It is not a forecast or a recommended universal stock-freshness SLA. Additional reserve is required.

| Merchants | Calls/day | Mean requests/s | Points/day across accounts | Central account base-points multiple | Distinct subscribed products if central/disjoint |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | 30,400 | 0.352 | 291,000 | 5.82× | 10,000 |
| 1,000 | 304,000 | 3.519 | 2,910,000 | 58.20× | 100,000 |
| 10,000 | 3,040,000 | 35.185 | 29,100,000 | 582.00× | 1,000,000 |
| 100,000 | 30,400,000 | 351.852 | 291,000,000 | 5820.00× | 10,000,000 |

**Central account A:** even100 merchants consume5.82× base points and10,000 product subscriptions in this model. Only the highest documented subscription cap reaches that assumed footprint; higher scales exceed it. Sales conversion may increase points but not automatically QPS, subscription ceilings, legal custody permission or financial isolation. Shared account also shares supplier funds and breach impact.

**Merchant accounts B:** each modeled merchant uses2910/50,000=5.82% of base daily points and100 product subscriptions, before bursts/reserve. This is a feasible per-account budget illustration, not a platform deployment approval. The **three-users/IP** limit independently blocks claiming100,1000,10,000 or100,000 accounts work directly behind normal shared egress. Obtain CJ-approved hosted integration/egress capacity and account arrangements. Do not recommend proxy/IP rotation or regional workers as a way around it.

**Hybrid C:** a central catalog account plus merchant execution may reduce duplicated public reads only if CJ licenses that use and proves pricing/visibility can be safely shared. Private catalog, account-specific costs/inventory and merchant secrets cannot be pooled. It adds contracts and failure modes; defer it.

Polling1000 variants once/hour costs240,000 points/day per merchant—4.8× base before other work. Once/minute costs14.4million points/day and1.44million calls/day, exceeding1/s. Prefer subscribed invalidation, product-scoped reads and bounded risk-weighted reconciliation.

## Required architecture and scheduling

- Per-actor admission plus per-account and **approved** egress limiters; account grouping by internal keyed identifier, never raw openId in logs.
- Durable priority queues: ambiguous writes/paid obligations first, fresh checkout quotes next, imported-product sync next, browsing/support last. Separate retry budgets, deadlines, circuit breakers and dead-letter review.
- Cache canonical public supplier data only within proven visibility/license boundaries; tenant-scope private stock, prices, orders and shipping/address fingerprints. Coalesce concurrent reads and token refreshes.
- Track actual pointsInfo and learn quota changes conservatively. Reserve capacity for recovery; if current remaining cannot cover an operation, defer instead of repeatedly requesting.
- Webhook fan-out only after signature/connection/object verification; durable dedup and reconciliation. Health checks must identify auto-closed topics, inactive subscriptions and30-day suspended accounts.
- Regional workers can improve resilience only after CJ approves account/egress policy; they are not an access-limit bypass.
- Safe reads use bounded exponential backoff with jitter. Honor Retry-After if actually returned, but none is promised. Reconcile ambiguous writes; never blindly retry orders/payments or ticket communication.

**Scale verdict:** PARTIAL feasibility, no documented direct multi-merchant SaaS-scale guarantee. Written CJ approval plus observed account limits and authorized sandbox/load tests are required before a production capacity commitment.
