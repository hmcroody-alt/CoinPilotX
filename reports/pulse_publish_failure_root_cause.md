# Pulse Publishing Failure Root Cause

Generated: 2026-06-02

## Summary

The reported text:

`Session expired. Please refresh and try again. Tap publish to retry.`

is not returned by a Pulse publishing backend route. It is generated in the `/pulse` frontend helper when a request receives HTTP `403` and the response body does not provide a usable `message`.

In the current authenticated local browser session, I could not reproduce a failing publish request:

- Pulse feed text publish succeeded.
- Pulse Status text publish succeeded.
- The active browser session had valid auth for Pulse publishing.
- No CSRF rejection occurred on the tested publish requests.

## Browser Trace Evidence

Current browser/session:

- URL tested: `http://127.0.0.1:5050/pulse`
- Auth state: valid signed session; backend saw `user_id=987654508`
- CSRF: not required by the JSON Pulse publish endpoints tested

### Feed Publish

- Frontend action: `/pulse` composer `Publish`
- Endpoint receiving request: `POST /api/pulse/posts`
- HTTP status returned: `200`
- Response body class: JSON success payload from `pulse_feed_engine.create_post(...)`
- UI result: `Posted successfully`
- DB trace: `pulse_post_attempts.id=101`, `status=success`, `status_code=200`, `user_id=987654508`, `post_type=text`

Code path:

- Frontend submit handler: `bot.py:17145`
- Backend route: `bot.py:48154`, `api_pulse_posts()`
- Auth check: `api_account_user()` -> `require_account()` -> `session["account_user_id"]`
- Backend result handling: returns `jsonify(result), status`

### Status Publish

- Frontend action: `/pulse/status` `Post Status`
- Endpoint receiving request: `POST /api/pulse/status`
- HTTP status returned: `200`
- Response body class: JSON success payload with `ok: true`, `success: true`, `status`, `status_id`
- UI result: `Status posted.`
- Recent Status updated immediately with the submitted text

Code path:

- Frontend submit handler: `bot.py:17082`
- Backend route: `bot.py:17810`, `api_pulse_status_create()`
- Auth check: `api_account_user()` -> `require_account()` -> `session["account_user_id"]`
- Backend result handling: returns JSON success after inserting `pulse_status`

## Exact Message Source

The exact reported message is assembled from two frontend pieces:

1. `Session expired. Please refresh and try again.`

   Location: `bot.py:17084`

   The local `/pulse` frontend helper does this:

   ```js
   r.status===403 ? 'Session expired. Please refresh and try again.'
   ```

   This means any HTTP `403` with no clearer backend `message` is mislabeled as a session expiry.

2. `Tap publish to retry.`

   Location: `bot.py:17145`

   The feed composer catch block appends:

   ```js
   (e.message || 'Upload failed.') + ' Tap publish to retry.'
   ```

## Backend Auth and CSRF Findings

### Auth Validation

Code locations:

- `bot.py:2339`, `account_user_id()`
- `bot.py:2446`, `load_account_by_id(user_id)`
- `bot.py:2760`, `require_account()`
- `bot.py:10012`, `api_account_user()`

Auth behavior:

- API auth depends on `session["account_user_id"]`.
- If missing or invalid, publish APIs return `401` with `{"ok": false, "message": "Login required."}`.
- In the current browser session, auth passed and publishing succeeded.

### CSRF Validation

Code locations:

- `bot.py:2327`, `get_csrf_token()`
- `bot.py:2335`, `verify_csrf()`

CSRF behavior:

- CSRF validation is used by traditional form/admin routes.
- The tested JSON publish endpoints do not call `verify_csrf()`.
- No global before-request CSRF middleware rejected the tested publish requests.

### Middleware Review

Relevant middleware:

- `bot.py:2211`, `basic_abuse_guard()`
- `bot.py:2239`, `interactive_security_guard()`

Findings:

- `basic_abuse_guard()` does not include `/api/pulse/posts`, `/api/pulse/status`, or `/api/pulse/reels/create`.
- `interactive_security_guard()` can reject oversized or unsafe requests, but returns `400`, `413`, or `429`, not the observed session-expired wording.

## Exact Backend Rejection Found

No backend rejection was reproduced in the active authenticated browser session.

Observed publish statuses:

| Surface | Endpoint | Status | Result |
| --- | --- | ---: | --- |
| Feed composer | `POST /api/pulse/posts` | 200 | PASS |
| Status composer | `POST /api/pulse/status` | 200 | PASS |

No exception was raised by either backend route during the trace.

## Root Cause

The current evidence points to a frontend error-classification bug, not a confirmed backend save failure:

- The backend publish endpoints use session auth and return explicit JSON messages for normal auth failures.
- The exact displayed text is created by frontend code on HTTP `403`.
- The frontend treats any `403` as `Session expired`, even when the backend cause could be a permission, moderation, owner-only, premium, group membership, live-host, or other access rejection.
- Because the current authenticated browser successfully published feed and status content, the reported failure was not reproducible as a live backend exception in this environment.

## Most Likely Failing Endpoint Class

The string `Tap publish to retry` appears on these publishing flows:

- Feed composer: `POST /api/pulse/posts`
- Status composer: `POST /api/pulse/status`
- Reels upload modal: upload step `POST /api/pulse/media/upload`, then create step `POST /api/pulse/reels/create`

The exact `Session expired` portion is produced by the `/pulse` local `api()` helper, not by `pulseApi()` and not by the Reels upload fallback parser. Therefore the highest-confidence source for the exact reported combined text is the feed/status helper at `bot.py:17084` plus catch block at `bot.py:17145` or `bot.py:17082`.

## Next Step Recommendation

Do not implement a UI/layout change yet.

For a fix pass, add diagnostic-safe logging around 403 publish responses and change the frontend helper to preserve backend-provided `message`/`error` fields for 403 responses instead of always labeling them as session expiry. Also add explicit route-level logging for:

- endpoint
- status code
- user id from session
- whether `account_user_id` was present
- whether `api_account_user()` returned a user
- safe backend reason

