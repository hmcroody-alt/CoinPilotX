# Pages × Marketplace

Page OS does **not** build commerce. It links pages to the existing canonical
Marketplace and gates management behind page roles.

## Model

- A page may hold a `store` link (`page_links`, `LINK_TYPES` includes `store`)
  pointing at the existing seller/store identity.
- The **STORE ≠ PAGE** invariant holds: the store keeps its own lifecycle, listings,
  orders, and payout rails. Deactivating a page never deletes a store; unlinking is
  just removing the pointer.

## Endpoints

- `GET /api/pages/:id/links` — current links (member-only).
- `POST /api/pages/:id/links` — set/replace a link; requires `manage_marketplace`
  for `store` links. Audited.

## Native surface

- Artist **merch** tab and business **shop** tab navigate into the existing
  Marketplace screens — no parallel product UI.
- `PagesHubScreen` shows a Marketplace management button only when the viewer's
  role grants `manage_marketplace` (OWNER, ADMIN, MARKETPLACE_MANAGER).

## Explicitly out of scope (protected)

No changes to fees, settlement, payouts, checkout, order flows, or any financial
path. Page OS reads and links; it never touches money movement.
