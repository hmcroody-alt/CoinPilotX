# PulseSoc Native Incoming Call — System Audit

Audited against the working tree at commit `7d2f0fc8`
(`calls: settle call state by compare-and-set so one answer wins`).

Everything below is a statement about code that exists in this repository. Where a
claim could not be verified by reading or running something, it is listed in
[Unverified](#unverified) rather than asserted.

---

## 1. Where the call system actually lives

The call system is **not** in `bot.py`. It is a blueprint plus a service module.

| Layer | Path |
| --- | --- |
| HTTP routes | `pulse_communications_v2/routes.py` |
| Business logic / state machine | `services/pulsesoc_communications_engine.py` |
| PushKit / APNs transport | `services/pulsesoc_voip_push.py` |
| iOS native entry point | `mobile-native/ios/PulseSoc/AppDelegate.swift` |
| React Native call layer | `mobile-native/src/calls/` (16 modules) |
| RN API client | `mobile-native/src/api/calls.ts` |

### Route inventory (`pulse_communications_v2/routes.py`)

| Method | Path | Engine entry point |
| --- | --- | --- |
| POST | `/api/calls/start` | `start_call` |
| POST | `/api/calls/<call_id>/accept` | `accept_call` |
| POST | `/api/calls/<call_id>/ring-seen` | `mark_ring_seen` |
| POST | `/api/calls/<call_id>/invite` | `invite_participants` |
| POST | `/api/calls/<call_id>/decline` | `decline_call` |
| POST | `/api/calls/<call_id>/end` | `end_call` |
| POST | `/api/calls/<call_id>/join-token` | `join_token` |
| GET | `/api/calls/<call_id>/status` | `call_status` |
| GET | `/api/calls/active` | `active_calls` |
| GET | `/api/calls/capabilities` | `call_capabilities` |
| POST | `/api/calls/voip-token` | `register_voip_token` |
| POST | `/api/calls/voip-token/revoke` | `revoke_voip_token` |

The two VoIP-token routes carry an extra `@auth_required` decorator on top of the
shared `_require_user()` gate, and both file the token against the **session's**
user id rather than anything in the request body. The in-file rationale is that a
VoIP token is a ring credential: a request that could name its own owner would be
a way to make an arbitrary handset ring full screen.

---

## 2. Backend state machine

`services/pulsesoc_communications_engine.py:130-138`

```python
FINAL_STATUSES = {"ended", "missed", "declined", "failed", "canceled",
                  "cancelled", "expired", "rejected", "disconnected"}
ALLOWED_TRANSITIONS = {
    "created":      {"ringing", "connecting", "declined", "missed", "canceled", "failed"},
    "ringing":      {"accepted", "connecting", "declined", "missed", "canceled", "failed"},
    "accepted":     {"connecting", "connected", "failed", "ended"},
    "connecting":   {"connected", "failed", "ended"},
    "connected":    {"reconnecting", "ended", "failed"},
    "reconnecting": {"connected", "ended", "failed"},
}
```

All state changes funnel through one function, `_transition()` (line 922). It is the
only writer of `communication_calls.status`, which is what makes a single fix able
to close the race described below.

Call identity is a server-minted opaque public id — `f"call_{secrets.token_urlsafe(10)}"`
(line 1495) — not a database row id.

---

## 3. The defect fixed in `7d2f0fc8`

### 3.1 Shape of the bug

`_transition()` received an **in-memory snapshot** of the call row, taken by a SELECT
earlier in the request, validated the requested edge against `snapshot["status"]` in
Python, and then wrote:

```sql
UPDATE communication_calls SET ... WHERE id = ?
```

There was no status predicate in the `WHERE` clause and no `rowcount` check. That is a
textbook read-modify-write race: between the read and the write, another device's
accept, the caller's cancel, or the stale-call sweeper could have moved the row on,
and the losing writer overwrote them unconditionally.

### 3.2 Consequences proven by test

1. **First-answer-wins was not enforced.** A second accept holding a stale `ringing`
   snapshot could drive an already-`connecting` call *backwards* to `accepted` — an
   edge that does not exist in `ALLOWED_TRANSITIONS["connecting"]`.
2. **Duplicate `call_accepted` fan-out.** The loser also emitted a second
   `call_accepted` sync event and a second answered-elsewhere VoIP cancel, and that
   second cancel excludes a *different* device id — so it cancels the CallKit ring on
   the device that actually won the call.
3. **The sweeper could stomp a live call.** `_mark_missed_stale_calls_cur()` and
   `_expire_stale_active_calls_cur()` both `SELECT` a batch and then loop doing real
   work per row (notification fan-out, participant teardown). A call answered while
   an earlier row in the same batch was being processed would still be written to
   `missed` / `expired` from the stale snapshot.

### 3.3 Why this was a hot path, not a rare one

This is the most consequential finding of the audit.

The sweepers are **not** scheduled background workers. `Procfile` runs seven
processes (`web`, `undx_worker`, `email_worker`, `ads_worker`, `alert_worker`,
`media_worker`, `supplier_worker`) and **none of them is a call sweeper**.

The sweepers run inline inside request handlers:

- `call_status()` — `services/pulsesoc_communications_engine.py:1897` — calls
  `_mark_missed_stale_calls_cur(cur)` then `_expire_stale_active_calls_cur(cur)`.
- `active_calls()` — line 1914 — calls both as well.

`call_status()` is the handler for `GET /api/calls/<id>/status`, and the client polls
it every **700 ms while a call is ringing**:

`mobile-native/src/calls/callSessionStore.ts:57-58`

```ts
const CALL_STATUS_REFRESH_MS = 4200;
const CALL_RINGING_STATUS_REFRESH_MS = 700;
```

So the sweeper executes several times per second, from any of the gunicorn workers
(4 workers × 8 threads), during exactly the ringing window in which an accept can
arrive. The batch-snapshot hazard is therefore reachable on a routine two-device call,
and needs only **one** device to be present — the polling device itself supplies the
competing writer.

### 3.4 The fix

Compare-and-set. `_transition()` now appends the status it validated against to the
`WHERE` clause and rejects a write that touched no rows:

```python
cur.execute(
    f"UPDATE communication_calls SET {', '.join(updates)} "
    f"WHERE id=? AND COALESCE(status,'created')=?",
    values,
)
if getattr(cur, "rowcount", -1) == 0:
    return _err("This call has already moved on.", 409, "transition_conflict",
                from_status=current, to_status=new_status)
```

The early return happens **before** `_event()` and before `_voip_stop_ringing()`, so a
losing writer emits no timeline event and sends no VoIP cancel.

On PostgreSQL under READ COMMITTED this is load-bearing in a way a bare `WHERE id=?`
is not. A blocked `UPDATE` re-evaluates its `WHERE` clause against the *committed* row
version once the blocker releases (EvalPlanQual). With only `id` in the predicate the
recheck always passes and the losing write is simply queued behind the winner and
applied. With the status predicate the recheck fails and the statement touches zero
rows — which is what converts "last writer wins" into "first writer wins".

`COALESCE(status,'created')` mirrors the default the read path already applies, so a
row with a NULL status is still matchable.

Call sites updated in the same commit:

| Site | Behaviour on CAS loss |
| --- | --- |
| `_mark_missed_stale_calls_cur` | `continue` — skip all side effects for that row |
| `_expire_stale_active_calls_cur` | `continue` — same |
| `accept_call` | Stay idempotent: still return a token off current state, but **do not** re-emit `call_accepted` or a second answered-elsewhere fan-out. A call now in `FINAL_STATUSES` returns `409 call_final`. |
| `admin_force_end_call` | Map `transition_conflict` onto the existing "Call was already final." contract |

Deliberately **not** changed: `decline_call` (line ~1831) and `end_call` (line ~1873)
ignore the transition result. Their per-participant bookkeeping is correct regardless
of who wins the call-level teardown, so forcing them to abort on a CAS loss would be a
regression, not a fix.

`services/db.py:717-718` exposes `rowcount` on the cursor wrapper, delegating to the
driver with a `-1` default, and `_translate_sql` rewrites `?` to `%s` for psycopg2 —
so the same statement is correct on both engines.

---

## 4. VoIP / PushKit transport

`services/pulsesoc_voip_push.py`

- Tokens live in their own table, **`voip_push_tokens`** (schema at lines 69-87),
  deliberately separate from `notification_device_tokens`. The in-file reason: they are
  credentials for different APNs topics, and writing a VoIP token into the alert
  registry would silently kill normal notifications for that device.
- APNs authentication is **token-based** (`.p8`), signed with `APNS_KEY_ID` /
  `APNS_TEAM_ID` / `APNS_PRIVATE_KEY` (lines 19-20, 428-431).
- Request headers (lines 460-468): `authorization`, `apns-topic: voip_topic()`,
  `apns-push-type: voip`, `apns-priority: 10`, `apns-expiration: 0`.
- `voip_topic()` (147-152) reads `APNS_VOIP_BUNDLE_ID` or falls back to
  `APNS_BUNDLE_ID`, and appends `.voip` if not already present.
- `call_uuid_for()` (122-124) derives a deterministic UUIDv5 from a fixed namespace, so
  the same call always maps to the same CallKit UUID across pushes and devices.
- `default_environment()` (136-144) reads `APNS_USE_SANDBOX` — a **deployment-wide**
  switch, not a per-device one.
- `send_voip_push()` (550+) already implements a one-shot host correction: a
  `BadDeviceToken` is replayed once against the other APNs host, and the caller is told
  to persist the host that worked. The documented reason is that `BadDeviceToken` is
  APNs's answer both for a genuinely dead token *and* for a live token offered to the
  wrong host, and treating the latter as dead causes `_deliver` to revoke it — after
  which the phone reverts to the alert push and never rings through CallKit again.

Ring / cancel entry points: `ring_devices()` (720), `cancel_devices()` (736),
`register_token()` (242), `revoke_token()` (343).

`_incoming_payload()` (651-677) and `_cancel_payload()` (680-688) carry call metadata
only — no credentials.

---

## 5. Ring teardown

`_voip_stop_ringing()` (engine, 1002-1068) is hooked to the single
`ringing -> anything` edge inside `_transition()`, rather than to decline/end/cancel/
timeout individually. The stated reason is that a missed CallKit cancel leaves a
full-screen system call UI for a call that no longer exists, and the user's only escape
is to answer a dead call.

It is scoped to `current == "ringing"` so the blocking APNs round trip stays out of the
hot path for mute / connected / quality / screen-share transitions.

Answered-elsewhere handling: a participant row is per **user**, not per device, so when
the actor answers their own row is already `joined` and invisible to the ringing query.
The actor is therefore re-added explicitly, with the winning device excluded via
`_answering_device_ids()` (984-999). The cancel reason is `answered_elsewhere` rather
than a hangup reason, so the user's other devices do not show a missed call.

Failures are swallowed by design — the state change is already committed and a cancel
push that did not land must not roll it back.

---

## 6. Stale-call windows

`_mark_missed_stale_calls_cur(cur, timeout_seconds=45)` — ring timeout.

`_expire_stale_active_calls_cur(cur)` (1205+), env-tunable, with a hard 30 s floor:

| Status | Env var | Default (s) |
| --- | --- | --- |
| `created` | `PULSESOC_CALL_CREATED_STALE_SECONDS` | 60 |
| `accepted`, `connecting` | `PULSESOC_CALL_CONNECTING_STALE_SECONDS` | 120 |
| `reconnecting` | `PULSESOC_CALL_RECONNECTING_STALE_SECONDS` | 180 |
| `connected`, `active` | `PULSESOC_CALL_CONNECTED_STALE_SECONDS` | 21600 |

---

## 7. iOS native layer

`mobile-native/ios/PulseSoc/AppDelegate.swift`

- `voipRegistration()` is invoked at line 47, **before** React Native startup
  (lines 28-31). PushKit must have a delegate registered before the first VoIP push can
  arrive; deferring it to JS would lose the launch push.
- `PKPushRegistryDelegate` conformance: lines 89-196.
- In `didReceiveIncomingPushWith`, `RNCallKeep.reportNewIncomingCall()` runs at lines
  163-176 — **before** `RNVoipPushNotificationManager.didReceiveIncomingPush()` at line
  179. The comment at line 76 records why: if the method returns without calling
  `reportNewIncomingCall`, iOS kills the process.

`mobile-native/ios/PulseSoc/Info.plist:68-74` — `UIBackgroundModes`: `audio`, `voip`,
`fetch`, `remote-notification`.

`mobile-native/ios/PulseSoc/PulseSoc-Bridging-Header.h:8-9` imports `RNCallKeep.h` and
`RNVoipPushNotificationManager.h`.

`mobile-native/ios/PulseSoc/PulseSoc.entitlements` contains exactly two keys:

```text
aps-environment                        $(PULSESOC_APS_ENVIRONMENT)
com.apple.developer.associated-domains applinks:pulsesoc.com
```

`aps-environment` was a **hardcoded literal** (`development`, shared by Debug and
Release) until `b3d3c4e2`. It now expands a build setting declared per configuration:
`development` for Debug, `production` for Release.

Reading this file does not tell you what a build actually received. Under
`CODE_SIGN_STYLE = Automatic` — what `project.pbxproj` sets — Xcode rewrites
`aps-environment` from the provisioning profile it selected, so the file is advisory
there; under manual signing, which is how EAS builds, it is authoritative. The signed
artefact is the only source that is always right:

```bash
codesign -d --entitlements :- <path>/PulseSoc.app
```

See [Unverified](#unverified): the `production` half has never been observed in a
signed product.

---

## 8. React Native layer

`mobile-native/package.json` pins:

| Package | Version |
| --- | --- |
| `react-native-agora` | 4.6.2 |
| `react-native-callkeep` | 4.3.16 |
| `react-native-voip-push-notification` | 3.3.3 |

The only patch in `patches/` is `react-native+0.81.5.patch` (a Hermes build fix). It is
unrelated to calls.

`mobile-native/src/calls/callSessionStore.ts` is a module-scope singleton holding
**one** Agora engine, created lazily at line 605 via
`require("react-native-agora").createAgoraRtcEngine()`.

`callKitBridge.ts` is the provider-agnostic seam. Exported surface:
`setNativeCallKitProvider`, `isNativeCallKitEnabled`, `initNativeCallKit`,
`reportIncomingCallKit`, `rememberCallKitCall`, `markCallKitConnected`,
`endCallKitCall`, `revokeVoipPushRegistration`, `teardownNativeCallKit`.

`callKitNativeProvider.ts:62` pins `audioSession: { mode: AudioSessionMode.voiceChat }`
— the CallKeep-side declaration only. Screen-level `AVAudioSession` setup remains
forbidden.

Feature flag, `mobile-native/src/api/config.ts:99`:

```ts
export const NATIVE_CALLKIT_ENABLED =
  isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED);
```

Default-on, explicit-off. Rollback is a flag flip (`EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED=0`)
rather than a revert.

Test coverage: 11 Jest files under `mobile-native/src/calls/__tests__/`, 1852 lines.

---

## 9. Preserved foundations

Confirmed unchanged by `7d2f0fc8`:

- **Agora** remains the sole RTC provider. `_generate_agora_token()`
  (engine, 522-555) mints an RTC token with privilege `1` and TTL
  `AGORA_TOKEN_TTL_SECONDS` (default 3600). The App Certificate never leaves the
  server.
- **Mux** is untouched and retains its existing livestream / replay / VOD role. It
  appears nowhere in the call path.
- **Realtime-audio coordinator** (`mobile-native/src/core/realtimeAudioEngine.ts` +
  `audioOwnershipPolicy.ts`) is unmodified. No second audio manager, no second
  microphone track, no second publication path.
- `config/realtime-audio-protected-paths.json:185` protects exactly one symbol in the
  engine — `services/pulsesoc_communications_engine.py#generate_agora_live_token`. The
  `7d2f0fc8` edits are outside it. The realtime-audio gate was run on the commit and
  reported clean over 2 inspected files.

---

## 10. Verified results

| Check | Result |
| --- | --- |
| `tests/test_call_accept_race.py` | 4 passed, 1 skipped |
| `tests/test_call_acceptance_sync.py` | 10 passed |
| Multi-guest call suite | 22 passed |
| Two-sided hangup suite | 6 passed |
| VoIP PushKit delivery suite | 40 passed |
| UNDX call domain | 25 passed (102 subtests) |
| UNDX call guard | 23 passed |
| **Total call/VoIP** | **130 passed** |
| `scripts/protection/run_protection_suite.py` | 673 checks / 44 suites passed |
| `scripts/realtime_audio_change_gate.py` | clean, 2 files inspected |

Anti-vacuity discipline: every new guard was re-run against the pre-fix code. Both
negative tests fail without the fix; both positive controls pass with and without it.

---

## 11. Unverified

These are open, and nothing in this document should be read as closing them.

1. **Real PostgreSQL two-thread concurrency proof.** `AcceptRaceTest` — two genuinely
   concurrent threads racing an accept under PostgreSQL MVCC — is still
   `skipUnless(DATABASE_URL.startswith("postgres"))` and therefore **skipped**. Docker's
   VM would not boot on the verification machine, there is no Homebrew PostgreSQL, and
   `pgserver` has no wheel for the only Python present (3.14). The SQLite coverage that
   *does* run proves the snapshot-staleness half of the defect by injecting the
   competing write on the sweeper's own cursor mid-batch; it cannot prove the
   two-writers-reach-the-UPDATE-simultaneously half.
2. **Call-creation rate limiting.** `/api/calls/start` has authentication but no
   observed per-user rate limit. Not implemented, not tested.
3. **The `production` entitlement in a signed artefact.** `b3d3c4e2` made
   `aps-environment` configuration-derived (§7), and a development-signed Release
   build was confirmed by `codesign` to carry `development`. The `production` half is
   unproven and cannot be proven here: local automatic signing takes the value from
   the provisioning profile, so it is only authoritative under the manual signing EAS
   uses, which needs a distribution profile this machine does not have.
4. **The dev/prod bundle split.** Both configurations still build `com.pulsesoc.app`.
   `apns-topic` now derives per device from the recorded `app_bundle`, but
   `mobile-native/src/api/calls.ts` never reports one, so every row is empty and every
   device resolves to the deployment-wide topic. Registering
   `com.pulsesoc.nativeapp.dev` as an explicit App ID with Push enabled needs Apple
   Developer account access.
5. **Physical iPhone 16 Pro verification.** No lock-screen, terminated-app, Silent
   Mode, Focus, or Bluetooth-routing verification has been performed on hardware. No
   simulator result substitutes for it.
6. ~~**Merge and production deployment.**~~ Closed: `7d2f0fc8` is on `main` and
   Railway reported `commitHash: b15f4a7a` after the docs commit, so production runs
   the compare-and-set fix.
