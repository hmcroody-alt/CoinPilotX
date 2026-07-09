# PulseSoc Native Home Visible Publish QA

Date: 2026-07-09

## Scope

This pass focused only on proving the native Home text-publishing workflow after the QA runtime stabilization. It did not add new Home features, did not focus on Android, did not start final UI polish, and did not touch production WebView paths.

## Runtime

- Built-in QA browser: used visibly.
- Native web QA URL: `http://127.0.0.1:8094`.
- Local API proxy: `http://127.0.0.1:5108`.
- Disposable QA backend: local SQLite backend on `127.0.0.1:5107`.
- Credentials: runtime-only local QA account. No password was committed, reported, or logged into repo files.

## Result

Result: passed.

Visible publish proof completed.

Roody could watch the authenticated native Home surface render and the Home composer workflow execute in the built-in QA browser. The final publish proof used a real text post:

`Visible Home publish QA signal 1783617700017 draft restored and completed`

## What Was Visibly Proven

- Authenticated Home opened from the local QA session.
- Pulse Network hero rendered.
- Status rail rendered.
- Pulse Composer rendered.
- Empty publish validation fired: `Add text or media before publishing.`
- A real text draft was typed into the composer.
- Character counter updated to `59/3000` for the first draft.
- The page was reloaded.
- Draft restored after reload.
- Recovered draft UI appeared with `Recovered saved draft.` and `Clear Draft`.
- Draft was edited after recovery.
- Character counter updated to `73/3000`.
- Publish succeeded.
- Composer reset after publish.
- Character counter reset to `0/3000`.
- Draft recovery indicators disappeared.
- Feed refreshed after publish.
- Exactly one matching post appeared in the feed.
- A second reload still showed exactly one matching post, proving no duplicate publish.
- No Metro, `expo-modules-core`, or `nullthrows` runtime failure appeared during the visible proof.

## Backend Evidence

Disposable local SQLite verification found one matching post:

- Table: `pulse_posts`
- Post ID: `1`
- Post type: `text`
- Visibility: `public`
- Moderation status: `approved`
- Body: `Visible Home publish QA signal 1783617700017 draft restored and completed`

The post also wrote live events:

- `pulse_post_created`
- `new_post`

## Cursor Sync Evidence

The first visible proof showed the publish path wrote to the feed and live-event table, but `/api/pulse/sync/events` was notification-backed and did not expose the post publish event. A scoped backend fix now emits an owner-scoped `pulse_post_created` sync notification after successful Home publish.

Cursor sync exposed `pulse_post_created` with:

- `event_type`: `pulse_post_created`
- `entity_type`: `post`
- `entity_id`: published post ID
- `source`: `native_home_composer`
- `invalidate`: `activity`, `notifications`

The audit script verifies this with a disposable backend publish.

## Practical Gaps

- Activity/Notifications invalidation was backend/cursor verified, but not visually demonstrated on the Activity screen after publish.
- Retry state was not forced through a real server failure during the visible walkthrough.
- Offline recovery was not simulated in the visible browser.
- Photo/video publish handoff still depends on media/device QA and was not part of this text-only proof.

## Device-Only Items

- Native camera permission.
- Native microphone permission.
- Native gallery picker.
- Physical photo/video capture.
- Large video upload.
- Background interruption recovery.

## Conclusion

Can Home foundation be considered complete: YES.

The Home text publishing foundation is now visibly proven end to end for validation, draft recovery, publish, composer reset, feed refresh, duplicate prevention, and cursor-visible sync event emission. Remaining Home work is release hardening and media/device proof, not a blocker for considering the Home foundation complete for this phase.
