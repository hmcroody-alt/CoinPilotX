# UNDX Agent Stage 0 Inventory And Architecture

Date: 2026-07-25

## Mission Boundary

This is the first sovereign UNDX governance slice. It does not create a second
assistant, second Marketplace system, second payment system, or second action
executor. It inventories the current production paths and adds the missing
governance control-plane primitives that future UNDX actions must pass through.

## Canonical UNDX Intelligence Path

- `services/pulse_ai_service.py` remains the user-facing UNDX orchestration path.
- `services/undx_policy.py` owns server-side bounded policy/context compilation.
- `services/pulse_ai_provider_router.py` owns provider routing, identity enforcement,
  and unavailable-state behavior.
- `services/pulse_ai_knowledge.py` owns UNDX product identity and bounded knowledge.

Decision: preserve this path. The new action governance layer is not an LLM provider
and does not replace `/api/pulse-ai/message`.

## Existing Business OS Surfaces Found

- Marketplace: `services/business_os/marketplace/`
- Advertising: `services/business_os/advertising/`
- Payments/ledger: `services/business_os/payments/`, `services/business_os/ledger/`
- Entitlements: `services/business_os/entitlements/`
- Recommendations, attribution, localization, merchant automation, creator commerce
- Existing governed UNDX action projection: `services/business_os/undx_actions/`

Decision: UNDX actions must call canonical services through governed boundaries.
Marketplace listing/order behavior remains in `services/business_os/marketplace/`.

## Marketplace Canonical Assistant Boundary

The Marketplace assistant already implements the correct first workflow pattern:

- `services/business_os/marketplace/assistant.py`
- Tool catalog: `list_tools()`
- Plan phase: `plan(user_id, tool, params)`
- Execute phase: `execute(user_id, tool, params, confirmation_token=...)`
- Confirmation tokens are bound to canonical user/tool/params.
- Writes can be disabled with `BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES`.
- Writes re-read canonical state before reporting `verified`.

Existing tools include:

- `create_product` creates a draft product.
- `publish_product` requires confirmation and verifies active product state.
- `pause_product` requires confirmation and verifies paused product state.
- Order and payout read/write tools remain server-authoritative.

Decision: the first UNDX marketplace workflow should wrap this plan/execute boundary,
not call lower-level product mutations directly.

## Existing UNDX Actions Layer Before This Slice

The existing UNDX actions layer had:

- `business_os_undx_policies`
- `business_os_undx_action_requests`
- `business_os_undx_decisions`
- `business_os_undx_audit`

It was intentionally informational-only. It computed `allow`, `deny`, and
`require_approval` labels, but had no tool registry, actor permission facts,
confirmation receipts, action receipts, action center, or emergency stop.

## Stage 1 Governance Foundation Added

Additive tables:

- `business_os_undx_tool_registry`
- `business_os_undx_permissions`
- `business_os_undx_confirmations`
- `business_os_undx_action_receipts`
- `business_os_undx_emergency_stops`

Engine additions:

- Tool registration/update for canonical UNDX tool descriptors.
- Actor-scoped permission facts with exact and wildcard matching.
- Durable confirmation records bound to request and payload hash.
- Action receipts for verified/failed/cancelled/blocked canonical service results.
- Emergency stop facts that override permissions and org policies.
- Action center snapshot for stops, decisions, requests, confirmations, receipts,
  and permissions.

Projection order:

1. Active emergency stop denies the request.
2. Matching actor permission decides the request.
3. Matching org policy decides the request.
4. Missing policy defaults to `require_approval`.

Execution boundary:

- No UNDX action executes in `services/business_os/undx_actions`.
- Canonical services still execute, verify, and audit their own mutations.
- UNDX action receipts only record what canonical services verified.

## Feature Flags And Stops

- Existing dark-launch flag: `BUSINESS_OS_UNDX_ACTIONS`
- Marketplace write kill switch:
  `BUSINESS_OS_MARKETPLACE_ASSISTANT_DISABLE_WRITES`
- New emergency stop table: `business_os_undx_emergency_stops`

## First Marketplace Workflow Readiness

Ready foundation:

- Register `marketplace.create_product` with action type
  `marketplace.product.create`.
- Register `marketplace.product.publish` with action type
  `marketplace.product.publish`.
- Use action request -> governance decision -> confirmation -> Marketplace
  assistant plan/execute -> verified receipt.

Still pending for full autonomous Marketplace listing drafts:

- Native/API-facing draft builder from text/image inputs.
- Image/media metadata validation against Marketplace product media contracts.
- Human approval UI or Action Center surface.
- End-to-end publish receipt wiring to the Marketplace assistant execute path.
- Xcode/physical-device evidence for the native presentation.

## Verification Completed

Used `python3` because this checkout does not currently have `venv/bin/python`.

- `python3 tests/business_os/test_undx_schema.py` PASS
- `python3 tests/business_os/test_undx_engine.py` PASS
- `python3 tests/business_os/test_undx_api.py` PASS

## Release Judgment

Stage 0 inventory: complete enough to proceed.

Stage 1 governance foundation: implemented and test-covered as a backend control
plane. It is not yet a complete production autonomous marketplace listing workflow.

Next highest-value action:

Build the Marketplace listing draft workflow on top of the existing Marketplace
assistant plan/execute boundary and the new UNDX governance request, confirmation,
receipt, and action-center primitives.
