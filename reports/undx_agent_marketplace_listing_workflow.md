# UNDX Marketplace Listing Workflow

Date: 2026-07-25

## Objective

Build the first governed UNDX production workflow over the existing Marketplace
system: listing draft creation and publish confirmation.

## Reused Canonical Systems

- Marketplace seller and product service:
  `services/business_os/marketplace/service.py`
- Marketplace assistant confirmation boundary:
  `services/business_os/marketplace/assistant.py`
- Marketplace controller conventions:
  `services/business_os/marketplace/api.py`
- UNDX governance projection, permissions, confirmations, receipts, and emergency
  stops:
  `services/business_os/undx_actions/`

No second Marketplace service was created.

## Implemented Workflow

### Draft Create

1. UNDX receives structured listing input.
2. `canonical_listing_params()` normalizes title, description, price, currency,
   fulfillment type, and inventory.
3. UNDX records `marketplace.product.create` as an action request.
4. Governance projection decides allow, deny, or require approval.
5. If not allowed, no Marketplace write runs.
6. If allowed, the existing Marketplace assistant executes `create_product`.
7. Marketplace verifies the product is in `draft`.
8. UNDX records a verified or failed receipt.

### Publish

1. UNDX records `marketplace.product.publish` as a high-risk action request.
2. Governance projection applies permissions and emergency stops.
3. If not denied, Marketplace assistant `plan()` returns a confirmation token.
4. UNDX records a pending confirmation.
5. On human confirmation, Marketplace assistant `execute()` publishes with the
   token.
6. Marketplace verifies the product is `active`.
7. UNDX records a verified or failed receipt.

## Safety Behavior

- Default missing policy: `require_approval`.
- Emergency stop overrides permissions and policies.
- Publish requires the Marketplace assistant confirmation token.
- Marketplace assistant confirmation tokens are now stored as server-side grants:
  single-use, time-limited, actor/tool/payload-bound, revocable, and persisted only
  as sha256 hashes.
- UNDX publish execution must redeem a matching pending UNDX confirmation for the
  same organization, actor, request, and canonical payload before Marketplace
  execution can run.
- A failed Marketplace execution burns the Marketplace confirmation grant so the
  same approval cannot be replayed.
- Emergency stops added after a publish plan block execution before confirmation
  redemption and before Marketplace mutation.
- Draft creation does not publish the product.
- Physical products require positive inventory.
- Price remains explicit; UNDX does not infer money terms silently.

## Known Boundaries

- Image-based listing creation is not fully complete. The canonical Marketplace
  service currently stores product text, price, fulfillment type, inventory, and
  status. Media indexing must be added through a Marketplace media contract before
  UNDX can publish image-derived listings with complete media support.
- HTTP route adapters are now exposed in `bot.py` for governance tools,
  permissions, confirmations, receipts, emergency stop, Action Center, and governed
  Marketplace draft/publish workflow.
- Native has a typed UNDX governed action API client and a native
  `UndxActionCenter` route wired to `/pulse/undx/actions`.
- Native publish confirmation execution is now available in the Action Center as an
  explicit human-controlled panel for governed draft creation, publish planning, and
  publish execution with a Marketplace confirmation token.
- Xcode iPhone Simulator and physical iPhone visual QA were not completed in this
  continuation; these remain required before treating the native Action Center as
  release-ready.

## Verification

- `python3 tests/business_os/test_undx_marketplace_workflow.py` PASS
- `python3 tests/business_os/test_marketplace_assistant.py` PASS
- `python3 tests/business_os/test_undx_engine.py` PASS
- `python3 scripts/undx_agent_governance_audit.py` PASS
- `python3 -m py_compile services/business_os/marketplace/assistant.py services/business_os/undx_actions/marketplace_workflow.py services/business_os/undx_actions/api.py tests/business_os/test_undx_marketplace_workflow.py` PASS
- `python3 -m py_compile bot.py services/business_os/undx_actions/api.py services/business_os/undx_actions/marketplace_workflow.py` PASS
- `npm run --prefix mobile-native typecheck` PASS
- `npm test --prefix mobile-native -- --runTestsByPath src/api/__tests__/undxActions.test.ts src/navigation/__tests__/routeResolution.test.ts` PASS
- `git diff --check` PASS

## Native Routes Added

- `/api/business-os/undx/tools`
- `/api/business-os/undx/permissions`
- `/api/business-os/undx/confirmations`
- `/api/business-os/undx/receipts`
- `/api/business-os/undx/emergency-stop`
- `/api/business-os/undx/action-center`
- `/api/business-os/undx/marketplace/listings/draft`
- `/api/business-os/undx/marketplace/listings/publish/plan`
- `/api/business-os/undx/marketplace/listings/publish/execute`
- Native deep link: `/pulse/undx/actions`

## Native Action Center Controls

- Read Action Center state from `/api/business-os/undx/action-center`.
- Read tools and permissions through the new UNDX route adapters.
- Create governed Marketplace drafts through
  `/api/business-os/undx/marketplace/listings/draft`.
- Plan Marketplace publish through
  `/api/business-os/undx/marketplace/listings/publish/plan`.
- Execute Marketplace publish through
  `/api/business-os/undx/marketplace/listings/publish/execute`.
- The native panel requires explicit actor/product/request/token input and does not
  silently execute Marketplace writes.
