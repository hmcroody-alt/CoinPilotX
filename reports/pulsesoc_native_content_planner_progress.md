# PulseSoc Native Content Planner + Scheduled Publishing Gateway Foundation

Date: 2026-07-05

## Scope

Built a native Content Planner and Scheduled Publishing gateway for the parallel PulseSoc native app.

This is a native client and gateway over existing PulseSoc Creator/Content Planner backend behavior. It does not create new publishing authority, recurring schedules, bulk publishing, version history, or fake scheduler success.

## Production Codebase Inspection

Production PulseSoc currently exposes:

- `/api/dashboard/content-planner/item`
- `/dashboard/creator/content-planner`
- `/dashboard/creator/post-scheduler`
- `/dashboard/creator/draft-studio`
- `/pulse/dashboard/content-planner`
- `/pulse/dashboard/post-scheduler`
- `/pulse/dashboard/draft-studio`
- `pulsesoc_content_planner_items`
- `pulsesoc_dashboard_centers.build_content_planner`
- `pulsesoc_dashboard_centers.build_post_scheduler`
- `pulsesoc_dashboard_centers.build_draft_studio`

The existing backend enforces draft/schedule validation and explicitly keeps publish-now, recurring schedule, bulk schedule, and version history unavailable unless backend services are connected.

## Reused Backend/API/Business Logic

- Existing `/api/dashboard/content-planner/item` write endpoint.
- Existing `pulsesoc_content_planner_items` database table.
- Existing creator state API through `getCreatorState()`.
- Existing Creator Studio state, recommendations, premium/creator metrics, and cached creator state.
- Existing dashboard Content Planner, Post Scheduler, and Draft Studio web gateways.
- Existing server-side validation that scheduled content requires `scheduled_at`.
- Existing moderation/privacy/checklist/publishing safety rules.

## Native Work Added

- Extended `mobile-native/src/api/creator.ts` to support the broader existing planner payload shape:
  - `scheduled_at`
  - `alt_text`
  - `thumbnail_selected`
  - `links_validated`
  - `media_attached`
  - `final_preview_reviewed`

- Added `mobile-native/src/screens/ContentPlannerScreen.tsx`:
  - Native Content Planner route.
  - Native Scheduled Publishing route.
  - Native Draft Studio route.
  - Draft save form.
  - Scheduled draft form.
  - Planner summary from existing creator state.
  - Checklist-style local controls that submit to backend.
  - Gateway cards to existing full web planner/scheduler/draft flows.
  - Loading, offline, error, and safe unsupported states.

- Navigation/deep links:
  - `/pulse/content-planner`
  - `/dashboard/creator/content-planner`
  - `/pulse/dashboard/content-planner`
  - `/dashboard/creator/post-scheduler`
  - `/pulse/dashboard/post-scheduler`
  - `/dashboard/creator/draft-studio`
  - `/pulse/dashboard/draft-studio`

- Entry points:
  - Creator Studio native actions.
  - Creator Studio backend cards route to native planner/scheduler/draft surfaces.
  - Settings entry point.
  - Notification/deep-link routing.

## Safe Fallbacks

The following remain on web fallback or unavailable by design:

- Publish now.
- Recurring schedules.
- Bulk schedule.
- Smart rescheduling.
- Version history.
- Full planner board/list management.

## QA Notes

Static verification covers type safety and route wiring. Browser QA should verify native route rendering and basic draft/scheduled form validation. Provider-backed planner list management remains blocked by the absence of a dedicated native JSON list/read contract.

Authenticated local QA completed with a disposable local account/session:

- `/pulse/content-planner` rendered the native Content Planner screen.
- `/dashboard/creator/content-planner` rendered the native Content Planner screen.
- `/pulse/dashboard/content-planner` rendered the native Content Planner alias.
- `/dashboard/creator/post-scheduler` rendered the native Scheduled Publishing screen.
- `/dashboard/creator/draft-studio` rendered the native Draft Studio screen.
- No visible runtime error text was detected during route checks.
- Direct authenticated API checks confirmed `/api/dashboard/content-planner/item` accepted:
  - a draft planner item.
  - a scheduled planner item with `scheduled_at`.

## Remaining Gaps

- No dedicated native JSON read/list endpoint for planner items was found.
- Native displays creator-state summary, not full planner rows.
- Native edit/delete planner item flows should wait for backend endpoints.
- Publish-now and recurring/bulk scheduling should remain fallback-only until backend services exist.

## Next Recommendation

Recommended next highest-value action: Native Courses + Learning Gateway Foundation.

Reason: production PulseSoc already has course creation, teacher dashboard, course draft tables, and free/paid-course-ready routes. The native app now has Profile, Premium, Marketplace/media viewer, Creator Studio, Content Planner, Events, Notifications, Search, and Trust/Safety foundations that can support a safe native learning gateway while keeping paid courses, checkout, teacher tooling, and compliance-sensitive operations on existing web/backend flows.
