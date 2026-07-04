# PulseSoc Lightspeed Bottlenecks

Date: 2026-07-03

## Critical findings

1. Mission Control eagerly built all detailed command-center states on every `/dashboard` request.
   - Baseline: 277 ms, 819 database queries.
   - Cause: account, network, intelligence, economy, crypto, ads, AI, and system builders ran before rendering a summary-only page.

2. Growth Center reprovisioned the entire Growth Engine during every page view.
   - Baseline after using a valid account: 86 queries.
   - Cause: `build_growth_state()` called schema creation and idempotent writes on the foreground request.

3. Calls Command Center ran notification schema initialization while rendering.
   - Baseline warm request: 107 queries.
   - Cause: `ensure_schema()` executed metadata and migration checks before a read-only delivery summary.

4. Messenger and call clients continued short fallback polling even when realtime transport was healthy.
   - Messenger: 3-second polling while visible.
   - Calls: 6.5-second active-call polling.

5. Two measured indexes were missing.
   - Notification delivery jobs lacked `(user_id, created_at)`.
   - Admin audit logs lacked time-oriented indexes.

## Operational risks

- Local failed-email queue: 350 `pending` and 9 `retry_ready` records. This is a delivery operations backlog, not a page-render blocker.
- Local notification delivery queue: 500 `ready` records. Production worker depth and oldest-job age should be monitored.
- Local push queue contains 477 `not_configured` outcomes, indicating devices/providers were unavailable for those jobs rather than a server crash.
- Premium is the slowest measured user route at roughly 160-170 ms locally, although it remains well inside the current 1-second budget.
- `/pulse/premium/undx` returns roughly 1.94 MB of HTML, including a 1.39 MB inline script. Extracting that runtime into a versioned deferred static bundle is the highest-value remaining frontend split, but it was not mixed into this release because the current UNDX implementation contains extensive unrelated active work.
- LiveKit's 423 KB browser bundle is necessary for calls and is correctly scoped to Messenger, but remains a mobile transfer cost.
- Several legacy or uploaded PNG/video files exceed 1 MB. They were not removed because ownership and active references require separate review.

## Non-findings

- No general static-cache failure: immutable one-year cache headers are present.
- No broad database-index collapse: core notification, call, reel, growth, and push tables have targeted indexes.
- No external provider call was added to normal page rendering.
- No synchronous notification, intelligence collector, or AI provider work was introduced into navigation routes.
