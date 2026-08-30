# System-Wide Principle: Never Block the User

**NEVER BLOCK THE USER ON WORK THAT CAN SAFELY RUN IN THE BACKGROUND.**

Established 2026-08-29 by the ZERO-DELAY LIVE END mission, which removed a
multi-second blocking wait after a host ended a Live. The pattern that caused it
recurs everywhere, so the rule is system-wide.

## The rule

When a user takes a terminal action (end, send, post, delete, publish), the UI
must acknowledge and release control immediately. Any work that does not have to
complete before the user's next action — media processing, publication, fan-out,
indexing, notifications — runs in the background and surfaces its result when
ready.

END NOW. RETURN CONTROL NOW. PROCESS IN BACKGROUND. SURFACE THE RESULT WHEN
READY.

## Anti-patterns (forbidden)

- `await finalizeSomething(); navigateAway();` — client blocks navigation on a
  network call whose result the user does not need to proceed.
- A request handler doing media work, publication, indexing, or follower
  fan-out inline before returning its response.
- A blocking full-screen spinner for work that has a valid intermediate state
  ("Preparing replay…", "Uploading…") the user could navigate away from.
- Polling loops that hammer the backend instead of bounded, scheduled retries.

## Required patterns

- Split "mark the action done" (fast, synchronous, idempotent) from "process
  the consequences" (durable background job). The `/api/pulse/live/<id>/end`
  endpoint returns `{"status":"ended","replay_status":"processing"}` in one DB
  transaction; replay creation happens in `media_worker`.
- Use the existing durable job pipeline (`pulse_jobs` + workers + reconcilers)
  rather than fire-and-forget threads. Jobs must be idempotent and safe to
  retry; recovery must never resurrect a completed user action (a replay
  failure never reopens a Live).
- Model background work as explicit states (e.g. processing → ready | failed)
  and render non-blocking UI for each state.
- Notify when the result is ready (push/event), from an exactly-once point
  (e.g. the `replay_reel_id` claim in `pulse_live_publish_replay_reel`).
- The flow must survive the app being killed the instant after the action:
  the server-side job, reconciler, or webhook finishes the work regardless.

## Reference implementation

The live-end flow: `finishBroadcast` in
`mobile-native/src/screens/LiveHostSessionScreen.tsx` (client release-first
ordering), `api_pulse_live_end` in `bot.py` (fast ack + job enqueue),
`_process_live_replay_job` + `reconcile_live_replay_backlog` in
`media_worker.py` (durable finalization), `pulse_live_publish_replay_reel`
(idempotent publish + single-fire notification). Guarded by
`tests/protection/test_live_end_nonblocking.py` and
`mobile-native/src/__tests__/LiveEndNonBlockingArchitecture.test.ts`.
