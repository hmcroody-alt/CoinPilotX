# Pages × Advertising

Pages plug into the **existing** advertising system (BusinessOS Ads); Page OS adds
role-gated access, not a second ads product.

## Model

- A page may hold an `ad_account` link (`page_links`) to an existing ad account.
- Ad management for a page requires the `manage_ads` capability (OWNER, ADMIN,
  ADVERTISING_MANAGER). ANALYST can see measured performance via `view_analytics`
  but cannot create or edit campaigns.

## Endpoints

- Links via `GET/POST /api/pages/:id/links` with type `ad_account` (requires
  `manage_ads`). Audited.
- Campaign CRUD stays on the existing `/api/business-os` ads routes — Page OS does
  not duplicate them.

## Native surface

`PagesHubScreen` shows an Advertising button (→ existing BusinessOS Advertising
screen) only when the server-returned capability set includes `manage_ads`.

## Guarantees

- Real metrics only: page ad analytics are the measured numbers the ads system
  already reports — never estimated or fabricated at the page layer.
- No ranking changes: promoted content flows through the existing ads injection
  path (`src/feed/injectAds`); Page OS does not alter feed ranking or ad delivery.
- No billing changes: payment methods, budgets, and invoicing remain entirely in
  the existing ads/payments systems.
