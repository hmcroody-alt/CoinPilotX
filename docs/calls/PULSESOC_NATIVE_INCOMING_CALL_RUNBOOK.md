# PulseSoc Native Incoming Call — Operations Runbook

Deployment, diagnosis and rollback for the native incoming-call path at commit
`7d2f0fc8`.

No secret values appear in this document. Every credential is referred to by variable
name only.

---

## 1. Pre-deployment checklist

### 1.1 Backend configuration

Confirm each of these is **set** (do not print values):

```bash
# On the Railway service, check presence only:
railway run --service CoinPilotX python3 -c "
import os
for name in ['APNS_TEAM_ID','APNS_KEY_ID','APNS_PRIVATE_KEY',
             'APNS_BUNDLE_ID','APNS_VOIP_BUNDLE_ID','APNS_USE_SANDBOX',
             'AGORA_APP_ID','AGORA_APP_CERTIFICATE']:
    print(f'{name}: {\"SET\" if os.getenv(name) else \"MISSING\"}')
"
```

`services/pulsesoc_voip_push.py:is_configured()` requires `APNS_TEAM_ID`,
`APNS_KEY_ID`, `APNS_PRIVATE_KEY` and a resolvable `voip_topic()`. If any is missing,
`send_voip_push()` returns `{"ok": False, "status": "config_missing"}` and **no VoIP
push is attempted** — calls will fall back to the ordinary alert push, which does not
present CallKit.

### 1.2 APNs environment

`APNS_USE_SANDBOX` is a **deployment-wide** switch shared with the alert sender.

| Deployment | Build entitlement | `APNS_USE_SANDBOX` |
| --- | --- | --- |
| Production (App Store / TestFlight) | `aps-environment: production` | unset / `0` |
| Development-signed device build | `aps-environment: development` | `1` |

A development-signed build talking to a deployment with `APNS_USE_SANDBOX` unset is
the misconfiguration that produces "rings once, then never again" — see §4.2.

The entitlement is no longer a literal: it expands `$(PULSESOC_APS_ENVIRONMENT)`,
declared `development` for Debug and `production` for Release. **Do not read the
checked-in plist to find out what a build actually got.** Under automatic signing
Xcode rewrites `aps-environment` from the provisioning profile, so the file is
advisory there; under manual signing (EAS) the file is authoritative. The only
answer that is always true comes from the artefact:

```bash
codesign -d --entitlements :- <path>/PulseSoc.app
```

A local device build is Release *and* development-signed. That combination is
expressed by overriding the setting on the command line —
`PULSESOC_APS_ENVIRONMENT=development` — which
`scripts/install_pulsesoc_native_dev_iphone.sh` passes.

### 1.2.1 Bundle ids and `apns-topic`

`apns-topic` is derived per device from the `app_bundle` recorded at registration,
falling back to the deployment-wide `voip_topic()`. Before shipping any build on a
second bundle id, add that id to `APNS_ALLOWED_BUNDLE_IDS` — an undeclared bundle
falls back to the wrong topic, and `DeviceTokenNotForTopic` revokes the token
permanently instead of retrying. Ordering matters: configuration first, build second.

### 1.3 Gates

Run all three before deploying. They must be run against **committed** state; the
audio gate diffs commits and reports `0 file(s) inspected` for uncommitted work.

```bash
# Realtime-audio protection gate
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD

# Protection suite (expect 673 checks / 44 suites)
/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python \
  scripts/protection/run_protection_suite.py

# Call and VoIP tests (expect 130 passed)
.venv/bin/python -m pytest \
  tests/test_call_accept_race.py \
  tests/test_call_acceptance_sync.py \
  -p no:cacheprovider -q
```

Backend tests require the checkout's `.venv` — the system `python3` lacks `requests`
and `pytest` and will fake a protection-suite failure.

---

## 2. Deployment

### 2.1 Backend

Backend deploys to Railway. The change in `7d2f0fc8` is confined to
`services/pulsesoc_communications_engine.py` and its test file. It contains **no
schema change** — the compare-and-set adds a predicate to an existing `UPDATE` and
reads `cursor.rowcount`, both of which work on the current `communication_calls`
table on SQLite and PostgreSQL alike.

There is therefore no migration step and no schema-bootstrap requirement.

### 2.2 Client

The native CallKit path is gated by a build-time flag,
`mobile-native/src/api/config.ts:99`:

```ts
export const NATIVE_CALLKIT_ENABLED =
  isFlagValueOnUnlessDisabled(process.env.EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED);
```

Default-on. To ship with it off, set `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED=0` at build
time.

The flag is resolved at **bundle time**, not at runtime. Expo's babel plugin inlines a
direct `process.env.X` member access; a computed lookup would not be inlined and the
flag would be dead in a Release build. The current code uses the direct form.

Verify build and dependencies:

```bash
cd mobile-native
npm run verify        # typecheck + i18n + jest
```

`patch-package` runs postinstall. `npm ci` in `mobile-native` is known to break
patches silently — prefer `npm install`, and if `npm ci` was used, re-apply
`patches/react-native+0.81.5.patch` manually and confirm.

---

## 3. Post-deployment verification

### 3.1 Backend is answering

```bash
curl -s https://pulsesoc.com/api/calls/capabilities -H "Authorization: Bearer <token>"
```

Should return the capability payload from `call_capabilities()` (engine:1807),
including the RTC provider and participant limits.

### 3.2 VoIP push configuration is live

The engine exposes `call_delivery_diagnostics(call_ref)` (engine:2341) and
`test_config(payload)` (engine:2497). Use the admin surface rather than inventing a
probe. `call_delivery_diagnostics` reports, per call, which devices were claimed for a
ring and what APNs answered.

### 3.3 A real call

Requires two accounts and at least one physical device. Simulator results do not
substitute — the simulator cannot receive APNs pushes at all, and ad-hoc signing
strips the entitlements that universal links and push depend on.

Sequence:

1. Caller: `POST /api/calls/start`.
2. Callee device (locked, app terminated): native CallKit UI must appear.
3. Answer. Caller's `call_accepted` arrives; both join the Agora room.
4. Callee's *other* devices must stop ringing, showing no missed call.

---

## 4. Troubleshooting

### 4.1 No CallKit UI at all; an ordinary notification banner appears instead

The device is receiving the **alert** push, not the VoIP push. Check in order:

1. `is_configured()` — is any of `APNS_TEAM_ID` / `APNS_KEY_ID` / `APNS_PRIVATE_KEY`
   / the topic missing? If so no VoIP push is attempted.
2. Is there an **active** row in `voip_push_tokens` for that user and device? Alert-push
   suppression is conditioned on an active token; no token means both the VoIP push is
   skipped and the alert push is sent.
3. Did the device ever call `POST /api/calls/voip-token`? PushKit hands the token to JS
   through the `register` event, which fires once per launch.

An ordinary notification banner is never an acceptable substitute for CallKit and must
not be treated as a partial success.

### 4.2 Device rings once, then never rings again

This is the signature failure of an APNs host mismatch, and it looks exactly like a
CallKit bug.

Mechanism (documented at `services/pulsesoc_voip_push.py:550-575`): APNs answers
`BadDeviceToken` both for a genuinely dead token **and** for a live token offered to
the wrong host. A sandbox token (any build carrying `aps-environment: development`)
sent to `api.push.apple.com` gets `BadDeviceToken`. If that is read as a dead token,
`_deliver` revokes it — and because alert-push suppression is conditioned on an active
token, the phone then quietly reverts to the ordinary alert push forever.

The current code replays a `BadDeviceToken` once against the other host and persists
the host that worked, so a single mismatched token self-corrects. Diagnose by:

1. Checking `APNS_USE_SANDBOX` against the build's signed entitlement.
2. Inspecting the `token_environment` and `revoked_reason` columns on the device's
   `voip_push_tokens` row.
3. Re-registering: relaunch the app so PushKit re-fires `register`.

### 4.3 Stale full-screen CallKit UI for a call that no longer exists

The user's only escape is to answer a dead call, so this is a severity-1 symptom.

`_voip_stop_ringing()` (engine:1002) is hooked to the single `ringing -> anything` edge
inside `_transition()`, so no exit from `ringing` can skip it. If a stale ring persists:

1. Confirm the call actually left `ringing` — check `call_timeline(call_ref)`
   (engine:2276) for the transition event.
2. Confirm a `voip_cancel_sent` event exists for that recipient. `_voip_stop_ringing`
   records one per recipient whose devices were claimed.
3. Cancel-push failures are **logged and swallowed** by design (`PULSESOC_VOIP_CANCEL_FAILED`).
   Search the Railway logs for that marker:
   ```bash
   railway logs --service CoinPilotX -n 500 | grep PULSESOC_VOIP_CANCEL_FAILED
   ```

Historical log windows age out of retention within the hour; capture promptly.

### 4.4 A stale "Active call" banner with no call

Check whether the call reached a terminal status server-side. The stale-active sweeper
(`_expire_stale_active_calls_cur`, engine:1205) runs inline on every
`GET /api/calls/<id>/status` and `GET /api/calls/active`, with these windows:

| Status | Env var | Default (s) |
| --- | --- | --- |
| `created` | `PULSESOC_CALL_CREATED_STALE_SECONDS` | 60 |
| `accepted`, `connecting` | `PULSESOC_CALL_CONNECTING_STALE_SECONDS` | 120 |
| `reconnecting` | `PULSESOC_CALL_RECONNECTING_STALE_SECONDS` | 180 |
| `connected`, `active` | `PULSESOC_CALL_CONNECTED_STALE_SECONDS` | 21600 |

A hard floor of 30 s applies regardless.

### 4.5 `409 transition_conflict` in logs

This is the compare-and-set working, not a fault. It means a writer lost a race: a
second accept, a sweeper that tried to expire a call that had just been answered, or a
teardown arriving after the call already ended.

Investigate only if the **rate** is anomalous, which would point at a client retry loop
rather than at the state machine. `accept_call` maps the conflict onto its idempotent
path; only a call that has reached `FINAL_STATUSES` returns `409 call_final` to the
user.

### 4.6 Two devices both appear to be in the call

Before `7d2f0fc8` this was reachable. After it, a second accept cannot re-drive the
call to `accepted` and cannot emit a second answered-elsewhere fan-out.

If it recurs, capture:

- `call_timeline(call_ref)` — look for two `accepted` events on the same call.
- `call_events(user_id, call_ref)` (engine:2010).
- Whether the deployed revision actually contains `7d2f0fc8`.

Note the open verification gap: the two-genuine-threads-under-PostgreSQL proof is still
skipped (no PostgreSQL available on the verification machine). The SQLite coverage
proves the snapshot-staleness half only.

### 4.7 Calls work but audio is silent, or a live stream loses audio when a call starts

Audio-session ownership, not the call system. The arbitration lives in
`mobile-native/src/core/realtimeAudioEngine.ts` and `audioOwnershipPolicy.ts`
(priorities: `audio_call`/`video_call` 100, `voice_message` 90, `live_host`/`live_guest`
80, `live_viewer` 40, `music_playback` 10).

The characteristic cause is an unrelated screen calling `Audio.setAudioModeAsync` or
`AVAudioSession.setCategory` and stealing the session. Read
`docs/realtime_audio_change_policy.md` before changing anything under
`config/realtime-audio-protected-paths.json`.

Observe real device audio state via syslog, grepping `PulseSocRealtimeAudio` in the
capture first, and `audiomxd` for `AVAudioSession` truth.

---

## 5. Rollback

Three independent levers, cheapest first.

### 5.1 Disable native CallKit (client, no deploy)

Rebuild with:

```bash
EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED=0
```

This disables the native CallKit presentation path while leaving the backend, Agora and
the audio coordinator untouched. In-app call UI (`IncomingCallLayer.tsx`) continues to
work for a foregrounded app.

This is a flag flip, not a revert — the code stays in place.

### 5.2 Disable VoIP push (backend, config only)

Unsetting `APNS_VOIP_BUNDLE_ID` and `APNS_BUNDLE_ID` makes `voip_topic()` return empty,
`is_configured()` return `False`, and every `send_voip_push()` short-circuit to
`config_missing`. Devices then receive the ordinary alert push.

Use only as an emergency stop: it removes native incoming-call behaviour entirely.

### 5.3 Revert the backend commit

```bash
git revert 7d2f0fc8
```

`7d2f0fc8` touches two files:

- `services/pulsesoc_communications_engine.py`
- `tests/test_call_accept_race.py`

It adds no schema, so a revert requires no data repair. Reverting restores the
read-modify-write race described in the audit — do it only if the compare-and-set is
itself shown to be causing harm, and record what was observed.

### 5.4 What rollback does *not* touch

By construction, none of the three levers affects:

- Agora RTC (provider, engine, token minting)
- Mux livestream / replay / VOD
- The realtime-audio ownership coordinator
- Reels, radio, voice messages

---

## 6. Diagnostic reference

| Need | Entry point |
| --- | --- |
| Per-call delivery trace | `call_delivery_diagnostics(call_ref)` — engine:2341 |
| Call state timeline | `call_timeline(call_ref)` — engine:2276 |
| Raw event log | `call_events(user_id, call_ref)` — engine:2010 |
| Admin detail | `admin_call_detail(call_ref)` — engine:2476 |
| Fleet summary | `calls_dashboard_summary()` — engine:2211 |
| Force-end a call | `admin_force_end_call(call_ref, admin_user_id, reason)` — engine:2304 |
| Agora config status | `agora_config_status()` — engine:488 |
| Recent calls | `recent_calls(limit)` — engine:2096 |

Log markers worth grepping in Railway:

```text
PULSESOC_VOIP_CANCEL_FAILED
PULSESOC_VOIP_RINGING_LOOKUP_FAILED
```

Use `railway logs --service CoinPilotX`. A bare `railway logs` returns only ~500 recent
lines, and `--since` / `--until` / `-n` require the service name.

---

## 7. Known operational gaps

Carried from the audit. These are open.

1. **No PostgreSQL concurrency proof.** The two-thread accept race test is skipped.
2. **No call-creation rate limit.** `/api/calls/start` is authenticated but
   unthrottled.
3. **The `production` entitlement has never been observed in a signed artefact.**
   `aps-environment` is now configuration-derived rather than a literal, but local
   automatic signing takes its value from the provisioning profile, so only the
   `development` half has been proven on hardware. Confirming the `production` half
   needs a distribution profile.
4. **One bundle id, by decision.** Both configurations build `com.pulsesoc.app`;
   development and production differ only in `aps-environment`. There is no second
   push-capable App ID, and none is planned.
   `tests/protection/test_ios_push_bundle_identity.py` fails if that drifts — notably
   if `expo prebuild` ever regenerates the project, since `app.config.js` still
   selects `com.pulsesoc.nativeapp.dev` for the development EAS profiles.

   The per-device topic derivation stays in place as a safety net, though with one
   bundle it resolves to what the deployment-wide topic always was. It is also inert
   for a second reason worth knowing before relying on it:
   `mobile-native/src/api/calls.ts` does not report `app_bundle`, so every stored row
   is empty.

   **Do not ring-test on a build from
   `scripts/install_pulsesoc_native_dev_iphone.sh`.** That script builds
   `com.pulsesoc.nativeapp.dev` so the development app can sit beside the App Store
   one, which is useful and intended — but such a build cannot receive a VoIP push at
   all. It draws `DeviceTokenNotForTopic`, which is revoked rather than retried, so
   the phone goes quiet permanently rather than transiently. The script prints this
   on every install. Use a `com.pulsesoc.app` build for any call-delivery test.
5. **No physical-device verification** of lock screen, terminated app, Silent Mode,
   Focus, or Bluetooth routing.
6. **No sweeper worker.** Stale-call cleanup depends on someone polling
   `/api/calls/<id>/status` or `/api/calls/active`. A call whose participants all
   disappear is not swept until an unrelated request happens to sweep it.
