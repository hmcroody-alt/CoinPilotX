# PulseSoc Native Incoming Call — Architecture

Describes the call system as it exists at commit `7d2f0fc8`. Component boundaries,
ownership, the state machine, and the delivery path.

---

## 1. Layer map

```
┌──────────────────────────────────────────────────────────────────────┐
│ iOS (Swift)                                                          │
│   AppDelegate.swift                                                  │
│     voipRegistration()          registers PKPushRegistry (.voIP)     │
│     didReceiveIncomingPushWith  → RNCallKeep.reportNewIncomingCall   │
│                                 → RNVoipPushNotificationManager      │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ native events
┌───────────────────────────▼──────────────────────────────────────────┐
│ React Native (mobile-native/src/calls/)                              │
│   callKitBridge.ts        provider-agnostic CallKit seam             │
│   callKitNativeProvider.ts CallKeep implementation                   │
│   callSessionStore.ts     singleton: ONE Agora engine, polling       │
│   useAgoraCallRoom.ts     RTC room lifecycle                         │
│   IncomingCallLayer.tsx   in-app ring UI (non-CallKit path)          │
└───────────────────────────┬──────────────────────────────────────────┘
                            │ HTTPS (bearer + session cookie)
┌───────────────────────────▼──────────────────────────────────────────┐
│ Flask backend                                                        │
│   pulse_communications_v2/routes.py       /api/calls/*               │
│   services/pulsesoc_communications_engine.py   state machine         │
│   services/pulsesoc_voip_push.py          APNs VoIP transport        │
│   services/db.py                          SQLite / PostgreSQL        │
└───────────────────────────┬──────────────────────────────────────────┘
                            │
              ┌─────────────┴─────────────┐
              ▼                           ▼
        APNs (VoIP topic)            Agora RTC
        ring / cancel                media plane
```

Mux is **not** in this diagram. It retains its livestream / replay / VOD role and has
no part in call transport or call media.

---

## 2. Ownership boundaries

| Concern | Sole owner | Notes |
| --- | --- | --- |
| Call state truth | `services/pulsesoc_communications_engine.py` | Backend is authoritative. Clients render state; they never decide it. |
| `communication_calls.status` writes | `_transition()` (engine:922) | The **only** writer. Every other function goes through it. |
| RTC media | Agora, one engine | `callSessionStore.ts:605` lazily creates exactly one `createAgoraRtcEngine()`. |
| Ring presentation | CallKit via `react-native-callkeep` | `callKitBridge.ts` is the seam; `callKitNativeProvider.ts` the implementation. |
| Push delivery | `services/pulsesoc_voip_push.py` | The only module that talks to APNs for calls. |
| Audio session | `mobile-native/src/core/realtimeAudioEngine.ts` + `audioOwnershipPolicy.ts` | Unchanged by this work. Screen-level `AVAudioSession` setup is forbidden. |
| RTC token minting | `_generate_agora_token()` (engine:522) | App Certificate never leaves the server. |

### Why one writer matters

Because `_transition()` is the only writer of `status`, a single compare-and-set
predicate inside it enforces first-answer-wins for every caller — accept, decline,
end, cancel, admin force-end, and both sweepers — without each of them needing its own
locking discipline.

---

## 3. State machine

```
                  ┌──────────┐
                  │ created  │
                  └────┬─────┘
                       │
         ┌─────────────┼──────────────┬───────────┬──────────┐
         ▼             ▼              ▼           ▼          ▼
    ┌─────────┐  ┌────────────┐  ┌─────────┐ ┌────────┐ ┌────────┐
    │ ringing │  │ connecting │  │declined │ │ missed │ │canceled│
    └────┬────┘  └─────┬──────┘  └─────────┘ └────────┘ └────────┘
         │             │
    ┌────┴────┐        │
    ▼         ▼        │
┌────────┐ ┌─────────┐ │
│accepted├─►connecting│◄┘
└───┬────┘ └────┬────┘
    │           ▼
    │      ┌──────────┐      ┌──────────────┐
    └─────►│connected │◄────►│ reconnecting │
           └────┬─────┘      └──────┬───────┘
                │                   │
                ▼                   ▼
            ┌───────┐           ┌────────┐
            │ ended │           │ failed │
            └───────┘           └────────┘
```

Authoritative table — `services/pulsesoc_communications_engine.py:131-138`:

```python
ALLOWED_TRANSITIONS = {
    "created":      {"ringing", "connecting", "declined", "missed", "canceled", "failed"},
    "ringing":      {"accepted", "connecting", "declined", "missed", "canceled", "failed"},
    "accepted":     {"connecting", "connected", "failed", "ended"},
    "connecting":   {"connected", "failed", "ended"},
    "connected":    {"reconnecting", "ended", "failed"},
    "reconnecting": {"connected", "ended", "failed"},
}
```

Terminal set (`FINAL_STATUSES`, line 130): `ended`, `missed`, `declined`, `failed`,
`canceled`, `cancelled`, `expired`, `rejected`, `disconnected`. A call in any of these
accepts no further transition — a late answer cannot reactivate it.

### 3.1 Compare-and-set

```python
values.append(int(call["id"]))
values.append(current)
cur.execute(
    f"UPDATE communication_calls SET {', '.join(updates)} "
    f"WHERE id=? AND COALESCE(status,'created')=?",
    values,
)
if getattr(cur, "rowcount", -1) == 0:
    return _err("This call has already moved on.", 409, "transition_conflict",
                from_status=current, to_status=new_status)
```

Three properties follow:

1. **First valid answer wins.** The loser's `UPDATE` matches zero rows.
2. **Side effects are gated.** The early return precedes `_event()` and
   `_voip_stop_ringing()`, so a loser writes no timeline entry and sends no VoIP
   cancel.
3. **Correct under PostgreSQL MVCC.** Under READ COMMITTED a blocked `UPDATE`
   re-evaluates its `WHERE` clause against the committed row version (EvalPlanQual).
   A predicate on `id` alone always passes that recheck, so the write is merely queued
   and then applied — last-writer-wins. Adding the status predicate makes the recheck
   fail, which is what produces first-writer-wins.

`COALESCE(status,'created')` mirrors the default the read path applies, so a NULL
status is still matchable.

### 3.2 Behaviour on CAS loss, by caller

| Caller | Behaviour |
| --- | --- |
| `accept_call` | Idempotent. Still returns a join token off the *current* state, but suppresses the second `call_accepted` emission and the second answered-elsewhere fan-out. If the call has reached `FINAL_STATUSES`, returns `409 call_final`. |
| `_mark_missed_stale_calls_cur` | `continue` — the row keeps its new state; no missed notification, no participant teardown. |
| `_expire_stale_active_calls_cur` | `continue` — same. |
| `admin_force_end_call` | Maps `transition_conflict` onto the pre-existing "Call was already final." response. |
| `decline_call`, `end_call` | Ignore the result by design. Their per-participant bookkeeping is correct regardless of who wins the call-level teardown. |

### 3.3 Why suppressing the duplicate fan-out is load-bearing

A second `call_accepted` is not merely noisy. `_voip_stop_ringing()` excludes the
answering device id from the answered-elsewhere cancel. A duplicate emission carries a
*different* device id, so it cancels the CallKit ring on the device that actually won
the call.

---

## 4. Incoming-call flow

```
Caller                Backend                       APNs           Callee device
  │                      │                            │                  │
  ├─ POST /api/calls/start ──────►                    │                  │
  │                      │ create row, status=created │                  │
  │                      │ mint Agora token           │                  │
  │                      │ _transition → ringing      │                  │
  │                      ├─ ring_devices() ──────────►│                  │
  │                      │   apns-push-type: voip     ├─ VoIP push ─────►│
  │                      │   apns-topic: <bundle>.voip│                  │
  │                      │   apns-priority: 10        │      AppDelegate.didReceiveIncomingPush
  │                      │   apns-expiration: 0       │                  │
  │  ◄─ call payload ────┤                            │      RNCallKeep.reportNewIncomingCall
  │                      │                            │      (BEFORE the RN handler — or iOS
  │                      │                            │       kills the process)
  │                      │                            │                  │
  │                      │                            │      native CallKit UI appears
  │                      │◄── POST /accept ───────────┼──────────────────┤
  │                      │  _transition ringing→accepted (CAS)           │
  │                      │  _voip_stop_ringing → cancel other devices    │
  │                      │      reason = answered_elsewhere              │
  │                      │      exclude = answering device id            │
  │  ◄─ call_accepted ───┤                            │                  │
  │                      ├──── join token ────────────┼─────────────────►│
  │                      │                            │                  │
  │  ◄═══════════ Agora RTC media plane ══════════════════════════════►  │
```

While ringing, the callee polls `GET /api/calls/<id>/status` every **700 ms**
(`callSessionStore.ts:58`), dropping to **4200 ms** once settled (line 57).

### 4.1 Consequence: the sweepers run on the poll

`call_status()` (engine:1897) and `active_calls()` (engine:1914) each invoke
`_mark_missed_stale_calls_cur(cur)` and `_expire_stale_active_calls_cur(cur)` inline.
There is no sweeper worker — `Procfile` runs `web`, `undx_worker`, `email_worker`,
`ads_worker`, `alert_worker`, `media_worker`, `supplier_worker` and nothing else.

Therefore the sweepers execute several times per second per ringing call, spread
across 4 gunicorn workers × 8 threads. This is why the CAS guard on the two sweeper
loops is not defensive padding: it is the guard on the most frequently executed writer
in the system.

---

## 5. Ring teardown

`_voip_stop_ringing()` (engine:1002-1068) is hooked to the single
`ringing -> anything` edge inside `_transition()`, not to decline/end/cancel/timeout
individually. No exit from `ringing` can forget to cancel the CallKit UI.

It is scoped to `current == "ringing"` — the only state in which a ring exists — which
also keeps the blocking APNs round trip off the hot path for mute, connected, quality
and screen-share transitions.

Recipient resolution:

1. Select participants whose row is still `status='ringing'`.
2. If the transition is an answer (`accepted` / `connected`) and there is an actor,
   add the actor back. Their own participant row is already `joined` and thus invisible
   to step 1, but their **other devices** are still ringing — participant rows are per
   user, not per device.
3. Exclude the answering device id (`_answering_device_ids()`, engine:984-999) for the
   actor only. Everyone else's devices all stop.

Cancel reason is `answered_elsewhere` for an answer and the transition reason
otherwise, so a user's other devices do not log a missed call for a call they took.

Failures are logged and swallowed: the state change is already committed, and a cancel
push that did not land must not roll it back.

---

## 6. VoIP push transport

`services/pulsesoc_voip_push.py`

### Storage

Table `voip_push_tokens` (lines 69-87) — deliberately separate from
`notification_device_tokens`. Writing a VoIP token into the alert registry would send
alert pushes to a topic that rejects them and silently kill normal notifications for
that device.

`register_token()` (242) is upsert-by-`(user_id, device_id, platform)` and first
releases the token from any **other** user:

```sql
UPDATE voip_push_tokens
SET active=0, revoked_at=?, revoked_reason='claimed_by_another_account', updated_at=?
WHERE token_hash=? AND user_id<>?
```

The token is the device's identity, so a handset that moves to another account must
not keep ringing for the old one.

### Authentication and headers

Token-based (`.p8`) JWT, signed per request:

```python
{"iss": APNS_TEAM_ID, "iat": <now>}   headers={"kid": APNS_KEY_ID}
```
signed with `APNS_PRIVATE_KEY` (with `\\n` unescaped).

Headers (lines 460-468):

| Header | Value |
| --- | --- |
| `authorization` | `bearer <jwt>` |
| `apns-topic` | `topic_for_bundle(device["app_bundle"])` |
| `apns-push-type` | `voip` |
| `apns-priority` | `10` |
| `apns-expiration` | `0` |

`voip_topic()`: `APNS_VOIP_BUNDLE_ID or APNS_BUNDLE_ID`, with `.voip` appended if
absent. This is the **deployment-wide** topic, and is now only the fallback.

`topic_for_bundle()` resolves the topic from the `app_bundle` the device reported at
registration, so one deployment can address two build flavours. A bundle that is not
`APNS_BUNDLE_ID`, `APNS_VOIP_BUNDLE_ID` or a member of `APNS_ALLOWED_BUNDLE_IDS` falls
back to `voip_topic()` rather than being trusted: `app_bundle` is client-supplied, and
an arbitrary topic earns `DeviceTokenNotForTopic`, which is classified `invalid_device`
and — unlike `BadDeviceToken` — is **not** replayed, so the token is revoked
permanently. A wrong host costs one request; a wrong topic costs the handset its
ability to ring at all.

The topic is resolved once per send and held fixed across the host replay. Varying
both at once would make an accepted replay unattributable, and `environment_corrected`
would then persist a host that was never at fault.

Every `app_bundle` stored today is empty, because `mobile-native/src/api/calls.ts`
does not yet report one. Every device therefore still resolves to `voip_topic()`, and
this is a prerequisite rather than a live split.

### Call identity

`call_uuid_for()` (122-124) derives a deterministic UUIDv5 from a fixed namespace, so
the same call maps to the same CallKit UUID on every device and across a
ring-then-cancel pair. CallKit requires a stable UUID to match an `endCall` to the
`reportNewIncomingCall` that created it.

### Environment selection

`default_environment()` (136-144) reads `APNS_USE_SANDBOX`. This is a **deployment-wide**
switch, the same one the alert sender follows, so VoIP and alert pushes cannot disagree
about which host they talk to.

`send_voip_push()` (550+) adds a per-token correction on top: a `BadDeviceToken` is
replayed once against the other host, and if that succeeds the caller is told to
persist the host that worked, so the extra request is paid once per token rather than
once per call. Only a token rejected by *both* hosts is treated as dead.

The client deliberately does **not** report its environment
(`mobile-native/src/api/calls.ts`, doc comment at ~line 295): `__DEV__` is false in a
Release build that still carries `aps-environment: development`, so a client-side guess
would be wrong exactly when it matters. Omitting it defers to `default_environment()`.

See the audit's Unverified section — the signed entitlement is still a hardcoded
literal, and this correction path is a compensation for that, not a replacement.

---

## 7. iOS delivery contract

`mobile-native/ios/PulseSoc/AppDelegate.swift`

1. `voipRegistration()` at line 47, **before** RN startup (lines 28-31). PushKit needs
   a delegate before the first push; deferring to JS loses a launch push.
2. `PKPushRegistryDelegate` at 89-196.
3. In `didReceiveIncomingPushWith`: `RNCallKeep.reportNewIncomingCall()` at 163-176,
   **then** `RNVoipPushNotificationManager.didReceiveIncomingPush()` at 179. Ordering
   is mandatory — the comment at line 76 records that returning without
   `reportNewIncomingCall` gets the process killed by iOS.

`Info.plist:68-74` — `UIBackgroundModes`: `audio`, `voip`, `fetch`,
`remote-notification`.

`PulseSoc-Bridging-Header.h:8-9` — `RNCallKeep.h`, `RNVoipPushNotificationManager.h`.

`PulseSoc.entitlements` — `aps-environment: $(PULSESOC_APS_ENVIRONMENT)`,
`com.apple.developer.associated-domains: applinks:pulsesoc.com`.

`PULSESOC_APS_ENVIRONMENT` is declared per build configuration in
`project.pbxproj`: `development` for Debug, `production` for Release. A
development-signed Release build — every local device build — overrides it back to
`development` on the xcodebuild command line, which is what
`scripts/install_pulsesoc_native_dev_iphone.sh` passes.

**Which signing style is in use decides whether any of this is observable.** Under
`CODE_SIGN_STYLE = Automatic`, which is what `project.pbxproj` sets and what local
device builds use, Xcode rewrites `aps-environment` from the provisioning profile it
selected and the entitlements file is advisory. Measured on an iPhone 16 Pro build:
Release with no override, `ProcessProductPackaging` confirmed to have re-run,
produced an `.xcent` reading `development` — the profile's value, not the
configuration's. Under **manual** signing, which is how EAS builds preview and store
binaries, the entitlements file is authoritative and a value the profile does not
grant is a hard codesign failure. That is the path this wiring exists for, and it is
the path that cannot be exercised without a distribution profile.

Both configurations still build `PRODUCT_BUNDLE_IDENTIFIER = com.pulsesoc.app`. The
`com.pulsesoc.nativeapp.dev` id in `app.config.js` is applied by prebuild only, and a
committed `ios/` directory bypasses prebuild, so it does not reach the native project.

---

## 8. Client call layer

`mobile-native/src/calls/` — 16 modules.

| Module | Role |
| --- | --- |
| `callSessionStore.ts` | Module-scope singleton. Owns the single Agora engine (lazy `createAgoraRtcEngine()` at line 605) and the status poll. |
| `callKitBridge.ts` | Provider-agnostic seam. `setNativeCallKitProvider`, `isNativeCallKitEnabled`, `initNativeCallKit`, `reportIncomingCallKit`, `rememberCallKitCall`, `markCallKitConnected`, `endCallKitCall`, `revokeVoipPushRegistration`, `teardownNativeCallKit`. |
| `callKitNativeProvider.ts` | CallKeep implementation. Line 62 pins `audioSession: { mode: AudioSessionMode.voiceChat }`. |
| `useAgoraCallRoom.ts`, `useNativeCallRoom.ts` | RTC room lifecycle hooks. |
| `IncomingCallLayer.tsx` | In-app ring surface for the non-CallKit path. |
| `MinimizedCallBanner.tsx` | Active-call banner. |
| `callToneLifecycle.ts` | Ringback / tone lifecycle. Single path — no duplicate ringtone source. |
| `callSignalMedia.ts`, `callMediaState.ts`, `callParticipants.ts`, `callCapabilities.ts` | Media and roster state. |
| `callSyncTrace.ts`, `incomingCallQa.ts` | Diagnostics. |
| `AddParticipantsSheet.tsx`, `CallActionsSheet.tsx` | UI sheets. |

The bridge seam is what allows the feature flag to disable native CallKit without
touching the Agora or audio layers.

---

## 9. Audio-session ownership

Unchanged by this work, and stated here so the boundary is explicit.

`mobile-native/src/core/realtimeAudioEngine.ts` and `audioOwnershipPolicy.ts`
arbitrate the shared session with a monotonic lease counter and a priority ranking:

| Consumer | Priority |
| --- | --- |
| `audio_call`, `video_call` | 100 |
| `voice_message` | 90 |
| `live_host`, `live_guest` | 80 |
| `live_viewer` | 40 |
| `music_playback` | 10 |

`callKitNativeProvider.ts:62` declares the CallKeep-side audio session mode only. It
does not call `Audio.setAudioModeAsync` or `AVAudioSession.setCategory`. Screen-level
session setup, a second microphone track, a second publication path and a second
global audio singleton all remain forbidden.

`config/realtime-audio-protected-paths.json:185` protects
`services/pulsesoc_communications_engine.py#generate_agora_live_token`. The `7d2f0fc8`
edits are outside that symbol; the gate confirmed clean over 2 inspected files.

---

## 10. RTC token minting

`_generate_agora_token()` (engine:522-555): RTC privilege `1`, TTL from
`AGORA_TOKEN_TTL_SECONDS` (default 3600). The Agora App Certificate is read
server-side and never sent to a client. `_agora_uid()` (515) maps a PulseSoc user id
onto an Agora uid.

`generate_agora_live_token()` (558) is the **livestream** path and is a protected
symbol. It is separate from the call path and is not touched here.

---

## 11. Configuration surface

All names only — no values.

### Backend

| Variable | Purpose | Declared in `.env.example` |
| --- | --- | --- |
| `APNS_TEAM_ID` | APNs JWT issuer | yes |
| `APNS_KEY_ID` | APNs JWT `kid` | yes |
| `APNS_PRIVATE_KEY` | `.p8` signing key | yes |
| `APNS_BUNDLE_ID` | fallback topic base | yes |
| `APNS_VOIP_BUNDLE_ID` | VoIP topic base | yes |
| `APNS_USE_SANDBOX` | deployment-wide host switch | yes |
| `AGORA_APP_ID` | RTC app id | yes |
| `AGORA_APP_CERTIFICATE` | RTC token signing | yes |
| `AGORA_TOKEN_TTL_SECONDS` | RTC token TTL (default 3600) | yes |
| `PULSESOC_CALL_CREATED_STALE_SECONDS` | expiry window (default 60) | — |
| `PULSESOC_CALL_CONNECTING_STALE_SECONDS` | expiry window (default 120) | — |
| `PULSESOC_CALL_RECONNECTING_STALE_SECONDS` | expiry window (default 180) | — |
| `PULSESOC_CALL_CONNECTED_STALE_SECONDS` | expiry window (default 21600) | — |

### Client (build-time)

| Variable | Purpose |
| --- | --- |
| `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED` | Native CallKit kill switch. Default-on, explicit-off. Not declared in `.env.example` — it is an Expo build-time flag, a different contract from the server's runtime `os.getenv` surface. |

Bundle ids: `com.pulsesoc.app` (production), `com.pulsesoc.nativeapp.dev`
(development). VoIP topic is the bundle id with `.voip` appended.
