# Economy Mobile QA

## Scope

The Economy UI uses responsive grid layouts, wrap-safe action rows, horizontal table overflow on narrow screens, and extra bottom padding to avoid mobile bottom-navigation overlap.

## Checks Covered by Audit

- `/dashboard/economy` loads for an authenticated user.
- Every Economy subsystem route loads.
- `/api/dashboard/economy/state` returns safe data.
- Non-admin users are blocked from `/admin/economy-command-center`.
- Admin users can open every Economy command-center section.
- Contextual Economy buttons render.
- No internal design philosophy name appears in rendered UI.
- Sensitive payment/provider terms are not rendered.

## Manual QA Status

Automated Flask route QA was added in `scripts/economy_loginexus_operating_system_audit.py`. Browser screenshots were not captured in this pass because the local validation can prove route, permission, and leak boundaries without touching production.

## Mobile Layout Notes

- Economy cards collapse to one column under 760px.
- Action rows become grid layout on narrow viewports.
- The shell includes bottom padding for mobile navigation clearance.
- Admin tables are horizontally scrollable on narrow screens.
