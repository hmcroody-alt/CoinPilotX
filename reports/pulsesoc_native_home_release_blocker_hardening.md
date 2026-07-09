# PulseSoc Native Home Release Blocker Hardening

Date: 2026-07-09

## Scope

Closed the remaining Home release blockers identified after the Home Activity + Notification invalidation QA pass:

- Persistent Hide
- Native User Mute
- Comment Submit Accessibility

This was not a Home redesign, new feature expansion, Android task, or UI/UX polish pass.

## Backend Reuse

Reused the existing PulseSoc backend authority model:

- authenticated account session via `api_account_user()`
- existing `pulse_posts` ownership and visibility records
- existing `users` identity table
- existing `pulse_notifications` backed `/api/pulse/sync/events` cursor path
- existing `pulse_emit_event(...)` realtime event path
- existing feed filtering flow in `services/pulse_feed_engine.py`
- existing Home feed API and native cache/refresh behavior

## Completed

### Persistent Hide

- Added server-authoritative `pulse_post_hides` persistence.
- Added `pulse_feed_engine.hide_post(...)`.
- Added `POST /api/pulse/posts/<post_id>/hide`.
- Home now calls the backend before removing a card.
- Feed refresh now excludes hidden posts through backend filtering.
- Hide creates a safe self-scoped `pulse_post_hidden` notification so `/api/pulse/sync/events` can expose the mutation.
- Hide also emits `pulse_post_hidden` through the existing realtime event hook.

### Native User Mute

- Added server-authoritative `pulse_user_mutes` persistence.
- Added `pulse_feed_engine.mute_user(...)`.
- Added `POST /api/pulse/users/mute`.
- Home now calls the backend before removing a muted user's posts.
- Feed refresh now excludes muted user content through backend filtering.
- Mute creates a safe self-scoped `pulse_user_muted` notification so `/api/pulse/sync/events` can expose the mutation.
- Mute also emits `pulse_user_muted` through the existing realtime event hook.

### Comment Submit Accessibility

- Added a QA-addressable comment input test ID.
- Added a QA-addressable comment submit test ID.
- Added semantic button role and accessibility label.
- Added disabled/busy accessibility state.
- Kept the existing visual structure intact.

## Visible QA Result

Visible QA was run in the built-in QA browser against a local isolated QA stack:

- Home route: `http://127.0.0.1:8094/pulse`
- Local API proxy: `http://127.0.0.1:5108`
- Local backend: `http://127.0.0.1:5107`
- QA database: `/tmp/pulsesoc_home_release_qa.sqlite`

Verified:

- Hide post `91001`, refresh Home, and confirm `Ethical Hackers Hot Take` stayed hidden.
- Mute author `QA Author Beta`, refresh Home, and confirm `Native Mute Proof` stayed removed.
- Open post `91003`, type a comment, submit through `post-detail-submit-comment`, and confirm the comment rendered.
- Confirmed persistence rows in `pulse_post_hides`, `pulse_user_mutes`, and `pulse_comments`.
- Confirmed `/api/pulse/sync/events` exposes `pulse_post_hidden` and `pulse_user_muted` with `sync_cursor_key` metadata.

## Remaining Home Release QA

- Physical-device push delivery and notification tap behavior still require iPhone release QA.
- Background recovery still requires device QA.
- Broader accessibility audit beyond the comment submit path remains a polish/release-readiness task, not a Home foundation blocker.

## Status

Home foundation remains complete. The three Home release blockers have scoped code fixes and passed visible QA verification in the built-in QA browser.
