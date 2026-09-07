# CJ order state mapping

Documentation checked: 2026-09-07. This is a proposed adapter contract, not an implementation. CJ states below are verified from official documentation; names in the PulseSoc supplier columns are proposed normalized states. PulseSoc's existing customer-order and payment state machines remain authoritative for customer money and must not be overwritten by supplier events.

## Three independent state domains

`PulseSoc customer order → PulseSoc fulfillment(s) → CJ order reference(s)`

`customer_refund_status` belongs to PulseSoc's customer-payment workflow. `supplier_recovery_status` belongs to the supplier dispute/reimbursement workflow. One customer order can require multiple fulfillments, and CJ can merge or split external orders; preserve line-level references and aggregate locally. [CJ query and merged-line references](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-7-query-order-get), [COGS original-order references](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_4-1-query-cogs-basic-data-order-info-post).

The repository audit in this discovery identifies canonical customer order values `created`, `paid`, `fulfilled`, `completed`, `cancelled`, and `refunded`. In the current transition policy, a paid order may move to fulfilled or refunded; it does not take a direct paid→cancelled path. These are existing commerce semantics, not CJ enums. A CJ cancellation must therefore create a fulfillment exception or recovery decision; it cannot rewrite a paid customer order as cancelled. See the accompanying [data authority map](CJ_PULSESOC_DATA_AUTHORITY_MAP.md).

## Verified CJ order values → proposed supplier status

| CJ `orderStatus` | CJ `subStatus` / meaning | Proposed `supplier_status` | Customer/payment implication |
| --- | --- | --- | --- |
| `CREATED` | Created, waiting for confirmation | `created` | External order exists; does not establish customer or supplier payment |
| `IN_CART` | In CJ cart, awaiting confirmation | `in_cart` | No customer-money change |
| `UNPAID` | Confirmed but supplier payment outstanding | `awaiting_funding` | Merchant must fund the supplier order; customer may already have paid |
| `UNSHIPPED` | `PENDING`; paid, waiting for processing, provider code300–399 | `paid_awaiting_processing` | Supplier payment evidence only; not buyer payment evidence |
| `UNSHIPPED` | `PROCESSING`; provider code400–499 | `processing` | Await shipment; no customer refund/capture mutation |
| `UNSHIPPED` | Missing/unrecognized substatus | `paid_awaiting_processing` with `phase_unknown=true` | Do not invent processing progress; preserve raw status and reconcile |
| `SHIPPED` | In transit | `shipped` | Update shipment projection only after authenticating/reconciling provider evidence; local order aggregation decides fulfillment |
| `DELIVERED` | Package delivered | `delivered` | Delivery is supplier evidence; local completion/dispute policy still applies |
| `CANCELLED` | Provider cancelled | `cancelled` | Customer order/refund must follow local policy and actual payment evidence |
| Unexpected value, including query-filter-only `OTHER` | No exhaustive meaning established | `unknown` | Preserve evidence, schedule reconciliation, no destructive or monetary transition |

Source: [CJ Order Status](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#order-status). The list API accepts PENDING/PROCESSING as filters, but current single/batch detail returns the aggregate UNSHIPPED with subStatus. The batch API changed on2026-08-19; do not carry forward its old direct PENDING/PROCESSING mapping. [Batch details and change notice](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-16-query-orders-in-batch-post).

Proposed compatibility handling: if an older event payload presents direct `PENDING` or `PROCESSING`, store the raw observation, then query current order detail before applying the corresponding supplier phase. This is a robustness policy, not evidence that every webhook uses those values.

## Submission, confirmation, and payment are separate operations

| Operation | Verified CJ behavior | Proposed internal evidence checkpoint |
| --- | --- | --- |
| V2 create with `payType=3`, `orderFlow=1` | Create only | Persist merchant request reference and provider response; `created` only after verified success |
| Add cart | Returns successful order codes and intercepted orders in example | Record per-order results; do not interpret envelope success as all orders accepted |
| Confirm cart | Returns `submitSuccess`, `successCount`, **`shipmentsId`**, interceptions | Correlate exact accepted lines/order codes and parent ID |
| Generate parent order | Receives `shipmentOrderId`; example returns `payId`, payment expiry, totals, partial results | Store the quote/fees and explicit supplier-funding authorization |
| Pay Balance V2 | Receives parent `shipmentOrderId`, optional `payId`; deducts connected account balance | Reconcile supplier payment outcome before retry or advancing state |
| Query details | Provider state, amounts, references, tracking | Authoritative provider observation for reconciliation |

Sources: [V2](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-1-create-order-v2-post), [Add cart](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-3-add-cart), [Confirm cart](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-4-add-cart-confirm-post), [Parent order](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-5-save-generate-parent-order-post), [Payment](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_2-3-pay-balance-v2-post).

`payType=2` on V2 combines creation/cart/confirmation/payment. It must not be used when the caller intends a reviewable unpaid draft. V3 does not document a payType request field. Store Order Flow explicitly requires V3 in its guide, despite the V2 table advertising that mode. These conflicts require CJ confirmation and sandbox contract tests before enabling an alternate flow. [V2](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-1-create-order-v2-post), [V3](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-2-create-order-v3-post), [Store flow](https://developers.cjdropshipping.com/en/api/start/Orders-Synchronization-Processing.html#_3-2-how-to-use-store-order-flow).

## Platform logistics and carrier status

For ordinary CJ logistics, `shopLogisticsType=2`, an available `logisticName`, and an empty `storageId` select CJ-arranged shipment after payment. For platform-label logistics1/3, fulfillment additionally waits for a waybill upload after payment; updates are permitted while UNSHIPPED. Represent this as a separate proposed `operational_hold=waybill_required`, without inventing a CJ `orderStatus` value. The dedicated guide's warehouse immutability requirement conflicts with the changeWarehouse API and mode3 warehouse rules; preserve this as an unresolved contract question. [Full platform flow](https://developers.cjdropshipping.com/en/api/start/Orders-Synchronization-Processing.html#_1-platform-waybill-order-processing), [CJ logistics flow](https://developers.cjdropshipping.com/en/api/start/Orders-Synchronization-Processing.html#_2-the-process-of-synchronizing-orders-and-utilizing-cj-logistics), [Warehouse change](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-10-change-order-warehouse).

`GET /api2.0/v1/logistic/trackInfo` exposes `trackingStatus` as a string, with `In transit` in its sample, plus origin/destination, last-mile carrier/number, delivery day/time. It does not publish a complete carrier-state enumeration. Keep `provider_tracking_status_raw` separately. A tracking number alone does not establish physical handoff or delivery; unknown carrier text must remain unknown. Use order status plus reconciled shipment observations; query proof-of-delivery after DELIVERED where available. [Tracking](https://developers.cjdropshipping.com/en/api/api2/api/logistic.html#_2-1-get-tracking-information-get), [Proof](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-17-query-proof-of-delivery-post).

## Cancellation gates and recovery

- `DELETE /api2.0/v1/shopping/order/deleteOrder` is allowed only for CREATED or IN_CART. It is not a cancellation API for UNPAID, paid, shipped, or delivered ordinary orders. [Delete](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-8-order-delete-del).
- `POST /api2.0/v1/shopping/order/updateLogistics` only modifies editable orders through the cart stage. Error8002 means state changed. Re-query before deciding next action. [Modify logistics](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-14-modify-order-logistics-post).
- Paid/shipped cancellation requests become proposed `cancellation_requested` operational state pending CJ support/eligible dispute handling, not an asserted provider cancellation. A customer refund, if approved by local policy, remains separate from supplier recovery.
- Private-inventory outbound `/outbound/cancel` has a different order domain and OMS eligibility; it must never be used as a substitute for ordinary-order cancellation. [Outbound cancel](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_8-9-cancel-outbound-order-post).

## Supplier dispute domain

CJ dispute `status` is documented as a string without a complete status enumeration on the Dispute page. `finallyDeal` is explicitly1=refund,2=reissue,3=reject. The create request's `expectType` is an expectation, not a final outcome; `refundType=1` balance or2 platform does not authorize or confirm a refund to a PulseSoc buyer. [Create](https://developers.cjdropshipping.com/en/api/api2/api/dispute.html#_3-create-dispute-post), [Detail](https://developers.cjdropshipping.com/en/api/api2/api/dispute.html#_6-get-dispute-detail-get).

| Verified observation | Proposed `supplier_recovery_status` | Separate customer action |
| --- | --- | --- |
| Eligible lines/reasons returned | `eligible` | Customer return/refund eligibility still local |
| Create call succeeded; detail not reconciled | `submitted` | No automatic customer refund |
| Provider final outcome unavailable | `pending` / raw status | Merchant support may continue independently |
| Final decision refund with provider reimbursement confirmed | `reimbursed` | Reconcile supplier ledger only |
| Final decision reissue and replacement order reference | `replacement_issued` | Merchant decides customer replacement experience |
| Final decision reject | `rejected` | Customer refund policy remains applicable |
| Cancel success, then verified provider state | `cancelled` | Does not undo a customer return or refund |

The supplier API supports dispute creation only for API-created orders. Preserve original and replacement external-order references and line quantities. No return-label/RMA API or complete settlement-timing guarantee was established by these docs.

## Reconciliation rules proposed for the adapter

1. Persist raw status, substatus, source, observed time, local received time, and typed external IDs. These are data-design requirements, not new schema changes in this mission.
2. Scope all observations by authenticated merchant, store, supplier connection, provider account, and mapped external order. Never match only on a merchant-supplied `orderNumber` or a webhook callback field.
3. Deduplicate callbacks and requests; reconcile provider reads after ambiguous writes. Global code1603003 rejects duplicate order creation, but does not promise an idempotent replay response. [Errors](https://developers.cjdropshipping.com/en/api/api2/standard/ps-code.html).
4. Batch query at most100 IDs (recommended20–50), no more than2requests/second/account. Missing/foreign IDs are omitted; retryable provider failure1600000 is not evidence of deletion. Archived/platform-identifier lookups require the single-order endpoint. [Batch query](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-16-query-orders-in-batch-post).
5. Retain terminal state until a newer verified provider observation and local policy support a correction; do not assume a complete monotonic live-order transition graph from the sandbox simulator.
6. Sandbox's numeric path300→400→500→600→700 is explicitly ordered without skips/reverts. It is a simulation rule, not proof of an exhaustive production state machine. [Sandbox status](https://developers.cjdropshipping.com/en/api/api2/api/shopping.html#_1-12-sandbox-update-status-post).

See [failure recovery matrix](CJ_FAILURE_RECOVERY_MATRIX.md) and [detailed endpoint evidence](orders_evidence.md).
