# MULTI-GUEST LIVE FOUNDATION MAP

Stage 0 forensic map of the existing PulseSoc Live path, produced before any code is
written. Its purpose is to record what the Live foundation actually is today, and to
name every assumption that currently implies ONE HOST / ONE PUBLISHER, so that the
multi-guest work extends this foundation instead of replacing it.

Scope of authorization for this mission: the **livestream** RTC/audio/video path only.
Audio calls, video calls, multi-guest calling and Messenger voice playback are out of
scope and must not be touched.

---

## 1. Headline finding

The brief assumes multi-guest Live has to be built. It largely does not. The
**server-side multi-guest foundation already exists and is sound**: there is a guest
table, a request/approve/remove lifecycle, a role-aware token minter, and a single
Agora channel per Live. What is missing is concentrated in three places:

1. **Client-side identity** — the mobile app invents remote participant identity
   instead of receiving it from the server, so it cannot tell one guest from another.
2. **Client-side rendering** — both the host screen and the audience screen are hard-
   wired to a small fixed number of publishers (three floating tiles on the host, and
   exactly **one** remote video on the audience side).
3. **Configuration** — the guest ceiling is a literal `LIMIT 12` inside a SQL string,
   and there are no feature flags at all.

The single most consequential architectural fact found is that the Agora RTC uid **is
the PulseSoc user_id verbatim**. That makes the identity fix cheap, and simultaneously
raises three problems documented in section 8.

The architecture already matches the mission's senior architectural decision. It is a
broadcast: `ChannelProfileLiveBroadcasting`, one channel, publishers hold
`ClientRoleBroadcaster`, audience holds `ClientRoleAudience`. Nothing needs to be
converted away from a group-call model, because it was never built as one.

---

## 2. Provider and channel topology

Agora is the canonical Live RTC provider. `react-native-agora` is pinned at 4.6.2 and
is listed in the realtime-audio gate's `dependency_watch`. LiveKit is legacy for Live;
only vestigial `livekit_identity` / `livekit_room` columns survive inside the guest
payload, and they carry no meaning on the Agora path.

There is exactly ONE channel per Live session. The channel name is derived server-side
in `bot.py` from the session row:

```python
room_name = clean_html(live.get("webrtc_room_id") or f"pulse-live-{live_id}")[:120]
```

Every participant — host, co-host, guest, audience — is minted a token for that same
`room_name`. There is no code path anywhere that creates a second channel, a per-guest
room, or a sub-room. **The "one channel, never one room per guest" requirement of Stage
2 is already satisfied and must be preserved, not implemented.**

The client joins that channel once, in `useAgoraLiveBroadcastRoom.ts`:

```ts
const result = engine.joinChannel(credentials.token, credentials.channelName, credentials.uid, {
  clientRoleType: publish ? agora.ClientRoleType.ClientRoleBroadcaster
                          : agora.ClientRoleType.ClientRoleAudience,
  channelProfile: agora.ChannelProfileType.ChannelProfileLiveBroadcasting,
  publishMicrophoneTrack: publish,
  publishCameraTrack: Boolean(localTrack),
  autoSubscribeAudio: true,
  autoSubscribeVideo: true,
});
```

`autoSubscribeAudio` and `autoSubscribeVideo` are both true for everyone. This means
that at the transport layer, an audience member is **already receiving every publisher's
audio and video today**. A second guest's media is arriving on the device. The app
simply never renders it (section 6.2) and never names it (section 6.1). This materially
changes the shape of the work: the audio matrix in Stage 15 is closer to a verification
exercise than a build, and the video work is a rendering problem, not a transport one.

---

## 3. Role and token contract

### 3.1 The minter

`services/pulsesoc_communications_engine.py` holds the only Agora Live token minter:

```python
def generate_agora_live_token(room_name, user_id, role, *, live_id,
                              guest_id=0, request_id=0, host_user_id=0) -> dict:
    normalized_role = str(role or "viewer").strip().lower()
    can_publish = normalized_role in {"host", "cohost", "guest"}
    if normalized_role not in {"host", "cohost", "guest", "viewer"}:
        return _err("Unsupported Agora Live role.", 400, "invalid_live_role", provider="agora")
    ttl = 1800 if normalized_role in {"cohost", "guest"} else 7200 if normalized_role == "host" else 3600
    uid = _agora_uid(user_id)
    token = RtcTokenBuilder.buildTokenWithUid(app_id, certificate, room_name, uid,
                                              1 if can_publish else 2, expires_at)
```

Agora's own token privilege — `1` publisher, `2` subscriber — is what actually enforces
publishing. This is the correct place for it. A client cannot self-promote by lying,
because the media server rejects a publish attempt made with a subscriber token. Stage
3 ("client may never self-promote") and Stage 4 (token binds user + channel + uid +
role + expiry) are structurally satisfied; what is missing is the **test evidence**
required by Stage 36, not the mechanism.

Guest and co-host tokens are deliberately short-lived at 30 minutes, against 2 hours for
the host and 1 hour for a viewer. The existing code comments the reason: a removed guest
cannot outlive their removal by more than the TTL, and the refresh path re-checks the
active guest slot. That is a genuine security property and must be preserved.

### 3.2 The authority decision

`bot.py:49636`, `api_pulse_live_agora_token`, decides the role server-side and never
trusts the requested one:

```python
is_host = user_id == host_user_id or bool(admin_current_user())
viewer_authorized, viewer_reason = pulse_live_viewer_authorized(cur, live, user_id)
is_guest_request = requested_role in {"guest", "cohost", "co-host"}
guest = pulse_live_active_guest(cur, live_id, user_id) if is_guest_request else {}
if requested_role in {"publisher", "host", "creator"} and not is_host:      # 403
if is_guest_request and not guest:    # TOKEN_MISSING_PUBLISH_PERMISSION 403
if is_guest_request and not pulse_live_session_accepts_guest_requests(live): # LIVE_ENDED 409
token_role = "cohost" if is_guest_request else "host" if is_host and requested_role in {"publisher","host","creator"} else "viewer"
```

A caller asking for `guest` who holds no active guest row is refused. The guest row is
the single source of publishing authority.

### 3.3 The co-host gap (Stage 22)

`token_role` collapses `guest` and `cohost` into the literal string `"cohost"`. The
minter then treats `host`, `cohost` and `guest` identically — all get publisher
privilege `1`, all get a 30-minute TTL. **CO-HOST IS CURRENTLY AN ALIAS FOR GUEST, not
for HOST.** Stage 22 requires co-host to be a distinct set of server-defined
permissions. The `pulse_live_guests.role` / `role_label` columns exist to carry that
distinction and are currently decorative.

---

## 4. Server data model

### 4.1 Tables in play

- `pulse_live_sessions` — the Live itself. Carries `webrtc_room_id`, `provider`,
  `publish_state`, `status`, `viewer_count`, `feed_post_id`, `replay_reel_id`,
  `agora_recording_resource_id`.
- `pulse_live_guests` — the stage roster. Statuses observed in queries:
  `pending`, `accepted`, `active`, `joining`, `joined`, `publishing`, `live`, `left`,
  `removed`.
- `pulse_live_guest_requests` — audience request-to-join.
- `pulse_live_viewers` — presence, statuses `hosting` / `watching` / `left`.
- `pulse_live_chat`, `pulse_live_reactions`, plus an audit trail via
  `pulse_live_audit` and `pulse_live_record_timeline_event`.

This is already a `live_session` + `live_participant` model in all but name. Stage 1
should map onto these tables rather than introduce parallel ones — a new participant
table alongside `pulse_live_guests` would create exactly the second source of truth the
mission forbids.

### 4.2 The stage roster query

```python
# pulse_live_active_guests(cur, live_id)
status IN ('active','accepted','joining','joined','publishing','live')
ORDER BY COALESCE(g.layout_position, 0), g.id
LIMIT 12
```

`layout_position` already exists and already orders the stage. Pending requests are
capped at `LIMIT 30`.

### 4.3 The guest payload — the identity gap

`pulse_live_guest_payload(row)` returns:

```
id, live_id, user_id, request_id, role, role_label, display_name, avatar_url,
status, livekit_identity, livekit_room, audio_muted, video_enabled,
audio_published, video_published, participant_sid, layout_position,
joined_at, live_at, updated_at
```

**There is no `rtc_uid` field.** The server knows every guest's `user_id`, and
`_agora_uid(user_id) == user_id`, so the server knows every guest's Agora uid — it just
never tells the client. This omission is the direct cause of the client-side identity
failure in section 6.1, and adding one derived field to this payload is the smallest
correct fix.

Note also that `livekit_identity` and `livekit_room` are shipped to clients on every
guest payload despite LiveKit being legacy for Live. They are dead weight and mildly
misleading, but removing them is a compatibility decision, not a bug fix, and is
recorded here rather than acted on.

---

## 5. Live route surface

All under `/api/pulse/live/`:

`start`, `mux/*`, `<id>/agora/token`, `<id>/rtc/token`,
`<id>/guests/<gid>/publish-complete`, `<id>/debug-event`, `<id>/native-publish`,
`<id>/browser-publish`, `<id>/webrtc/signal(s)`, `<id>/chat`, `<id>/state`,
`<id>/react`, `<id>/join`, `<id>/join-request`, `<id>/cohost/request`,
`<id>/cohost/debug`, `<id>/join-status`, `<id>/cohost-trace`, `<id>/join-requests`,
`<id>/join-requests/<rid>/<action>`, `<id>/join-requests/<rid>/cancel`,
`<id>/guests/<gid>/<action>`, `<id>/end`.

Stages 6, 7, 19 and 20 are already served by this surface. `<id>/join-request` plus
`<id>/join-requests/<rid>/<action>` is audience request-to-join with host approval.
`<id>/guests/<gid>/<action>` accepts `mute | unmute | remove | leave` with the correct
authority split — host-only for `mute`/`unmute`/`remove`, guest-self-only for `leave`:

```python
if action in {"mute", "unmute", "remove"} and not is_host:
    return api_error("Only the host can manage guests.", 403)
if action == "leave" and not is_self_guest:
    return api_error("Only the guest can leave their guest slot.", 403)
```

**Stage 20 is already correct at the API layer**: `leave` and `remove` write guest status
only; ending the Live is a separate endpoint, `<id>/end`. A guest leaving cannot end the
Live because the two are different routes with different authority checks. What remains
for Stage 20 is client-side — proving the host UI's "end" affordance and the guest UI's
"leave stage" affordance are visually and behaviourally distinct.

Missing from this surface: **host-initiated invite** (Stage 6). Everything today is
audience-pull (`join-request` / `cohost/request`) approved by the host. There is no
host-push invite, and therefore no `invite_id` for the deduplication Stage 30 requires.
This is the one genuinely absent server capability.

---

## 6. Client-side one-publisher assumptions

This section is the core deliverable of Stage 0.

### 6.1 Remote identity is invented, and the host is guessed positionally

`mobile-native/src/live/useAgoraLiveBroadcastRoom.ts`, line 134:

```ts
onUserJoined: (_c, uid) => setState((s) => {
  const participant: LiveParticipant = {
    identity: `agora-${uid}`,
    name: "Live participant",
    isLocal: false,
    isHost: !publish && s.participants.length === 0,
    videoTrack: { provider: "agora", uid },
    audioTrack: { provider: "agora", uid },
    hasVideo: false, hasAudio: false, audioMuted: false, speaking: false
  };
  ...
```

Three separate defects in five lines:

- `name` is the hardcoded string `"Live participant"`. Every remote publisher has the
  same name. With one publisher this is invisible; with three it is unusable.
- `identity` is synthesised from the uid rather than supplied by the server, so there is
  no link back to a PulseSoc user, avatar, or guest row.
- `isHost` is `!publish && s.participants.length === 0` — **the host is whoever happened
  to arrive first, as observed by a non-publishing client.** If the host's connection
  blips and rejoins after a guest, the guest becomes "the host" as far as the audience
  app is concerned, and the audience's sort in section 6.2 then promotes the wrong
  person to the only visible video tile.

This is the exact failure mode Stage 11 was written to prevent: *never guess remote
identity in components*. There is no canonical runtime participant registry. Stage 11 is
therefore a real build, and it is the keystone — Stages 12, 13, 14 and 19's client half
all depend on knowing who a uid is.

### 6.2 The audience renders exactly ONE remote video

`mobile-native/src/screens/LiveScreen.tsx`, lines 328–336:

```ts
const rtcParticipants = useMemo(() =>
  room.participants
    .filter((p) => !p.isLocal && (p.hasVideo || p.hasAudio))
    .sort((a, b) => Number(b.isHost) - Number(a.isHost)),
  [room.participants]);
const rtcVideoParticipant = rtcParticipants.find((p) => p.videoTrack);
const localVideoParticipant = room.participants.find((p) => p.isLocal && p.videoTrack);
```

and at line 637:

```tsx
{room.connected && rtcVideoParticipant?.videoTrack && VideoViewComponent ? (
  <VideoViewComponent videoTrack={rtcVideoParticipant.videoTrack} style={StyleSheet.absoluteFill} ... />
```

`.find()` — singular. **The audience experience is structurally single-publisher.** A
second guest's video track arrives over the wire, is auto-subscribed, and is then
discarded by a `.find()`. The `.sort()` by `isHost` is what decides which single stream
survives, and `isHost` is the positionally-guessed flag from 6.1. So the audience shows
one video, chosen by a guess.

The audience does render its own local video, but only when it is itself a guest, as a
picture-in-picture at line 661. Audio is unaffected — all remote audio is subscribed and
mixed by Agora regardless of what is rendered, which is why "everyone is audible" is
likely to pass before "everyone is visible" does.

### 6.3 The host stage caps at three tiles and splits binary

`mobile-native/src/screens/LiveHostSessionScreen.tsx` (2035 lines):

```ts
const guests = useMemo(() => room.participants.filter((p) => !p.isLocal), [room.participants]);
const localParticipant = useMemo(() => room.participants.find((p) => p.isLocal) || null, [room.participants]);
const stageParticipants = useMemo(() => [localParticipant, ...guests].filter(Boolean) as LiveParticipant[], [localParticipant, guests]);
...
{guests.slice(0, 3).map((participant) => (<FloatingGuestTile ... />))}
split={stageParticipants.length > 1}
```

`guests.slice(0, 3)` is a hardcoded three-tile ceiling. `split` is a boolean — one
publisher or "more than one", with no notion of four, six or thirteen. Against a limit
of 12 guests plus the host, the host stage can represent at most 4 of 13 people. Stage
12's adaptive layout is a genuine build.

The host screen does already have the guest *management* surface — `activeGuests`,
`requests`, `listGuestManagement(liveId)`, `moderateGuest(guest, "mute"|"unmute"|"remove")`,
`confirmRemoveGuest`. The moderation of Stage 19 exists; it is the stage *rendering*
that does not.

### 6.4 No active speaker detection at all

`enableAudioVolumeIndication` does not appear anywhere in `mobile-native/src`, and
neither does an `onAudioVolumeIndication` handler. The `LiveParticipant` type carries a
`speaking: boolean` field which is therefore permanently `false`. Stage 14 is unbuilt.
Agora provides this natively; no custom DSP is needed or permitted.

### 6.5 Dual-stream is publisher-only

`engine.enableDualStreamMode(true)` is called only inside the
`publish && options.video !== false` branch. Audience clients never enable it, and
`setRemoteSubscribeFallbackOption` is not called at all. With one publisher this barely
matters. With six publishers an audience device subscribes to six high-quality streams
with no fallback ladder — the Stage 24 and Stage 26 risk.

### 6.6 Role promotion in place — already correct

Lines 65–97 of the hook handle a viewer becoming a publisher **without leaving the
channel**:

```ts
const sameAgoraSeat = Boolean(activeEngine && activeCredentials
  && activeCredentials.provider === "agora"
  && activeCredentials.channelName === credentials.channelName
  && activeCredentials.uid === credentials.uid);
if (sameAgoraSeat && ... Boolean(options.publish) !== Boolean(activeCredentials.canPublish)) {
  if (activeEngine.renewToken(credentials.token) < 0) throw ...
  if (promote) {
    activeEngine.enableVideo();
    activeEngine.setVideoEncoderConfiguration({...});
    activeEngine.setClientRole(agora.ClientRoleType.ClientRoleBroadcaster);
    activeEngine.updateChannelMediaOptions({ clientRoleType: Broadcaster,
      publishMicrophoneTrack: true, publishCameraTrack: options.video !== false, ... });
    activeEngine.startPreview();
```

`renewToken` + `setClientRole` + `updateChannelMediaOptions` on the existing engine, with
`startPreview()` before publication. **This is exactly the mechanism Stages 9 and 10
call for and it already works.** The demotion path is symmetrical.

Consequence for Stage 10 ("host never restarts camera/mic/engine/audio-session"): a
guest joining runs entirely inside the *guest's* own client. The host client is only
notified through `onUserJoined`. There is no code path by which a guest's arrival
touches the host's engine. Stage 10 is satisfied by construction, and the risk is one of
regression — the new layout work must not introduce a remount that tears the engine
down.

---

## 7. Audio ownership

`mobile-native/src/live-audio/liveAudioEngine.ts` (1048 lines) implements a
single-owner arbitration over `../core/audioOwnershipPolicy`:

```ts
let activeLiveAudioOwner: LiveAudioOwner | null = null;
const displacementHandlers = new Map<string, () => void>();
let lastOwnershipDecision: OwnershipDecision | null = null;

export function claimLiveAudioSession(mode, ownerId, options): LiveAudioOwner {
  const decision = resolveOwnershipDecision(activeLiveAudioOwner, { ownerId, mode });
  if (!decision.allowed) throw new RealtimeAudioOwnershipError(decision.blockedBy, decision.blockedByMode);
```

There is **one** module-level owner, **one** lease, and arbitration that throws
`RealtimeAudioOwnershipError` rather than silently stealing the session. A higher-
priority owner displaces the incumbent through a registered teardown callback, and a
failing teardown handler is explicitly prevented from blocking the incoming owner.
`getActiveLiveMicrophoneOwner()` returns the owner only if `publishesMicrophone`, giving
a single mic owner.

The Agora engine itself is likewise a single instance held in `engineRef.current` inside
the hook, created once by `agora.createAgoraRtcEngine()` and released on `disconnect()`
via `engine.leaveChannel(); engine.release();`.

**Stage 16's hard rule — ONE engine owner, ONE audio-session policy, ONE local mic owner
— is already the architecture.** The mission's prohibitions (`guestAudioEngine`,
`secondaryLiveAudioSession`, an alternate RTC stack) are prohibitions against
*regressing* this, and the correct posture for the whole mission is that no new audio
session, engine, or mic owner is created for guests. Guests reuse the same singleton via
the in-place promotion of 6.6.

Supporting modules, all to be preserved: `liveAudioTrace.ts` (220),
`liveAudioTelemetry.ts` (166), `liveAudioRecovery.ts` (162, with
`LIVE_MAX_RECONNECT_ATTEMPTS = 6`), `liveMicrophonePublisher.ts` (156),
`liveAudioNative.ts` (223), `livePublisherMedia.ts` (34), `liveAudioFlags.ts` (56),
`livePlaybackOwnership.ts` (21), `liveMusicMixing.ts` (49).

---

## 8. The uid identity problem

```python
def _agora_uid(user_id: int) -> int:
    uid = int(user_id)
    if uid <= 0 or uid > 0xFFFFFFFF:
        raise ValueError("PulseSoc user id cannot be represented as an Agora numeric UID")
    return uid
```

The Agora uid **is** the PulseSoc user_id. Three consequences, all newly material once
more than one person is on stage:

1. **Publisher user_id enumeration.** Any client in the channel — including every
   audience member — receives the numeric uid of every publisher in `onUserJoined`, and
   that number is a PulseSoc user_id. This is a small but real information leak that
   scales with the number of publishers.
2. **The uid is not scoped per Live.** The same user carries the same uid across every
   Live they ever join, so uid history is correlatable across sessions.
3. **A second device for the same user collides.** Agora treats a duplicate uid joining
   the same channel as a takeover and hard-kicks the first connection. PulseSoc
   therefore already *has* a one-device policy for Live, but it is enforced by an abrupt
   kick rather than a graceful handoff, and nothing in the product states it. This is
   directly Stage 38 (duplicate participant protection) and Stage 39 (multi-device
   policy — define it).

These are reported rather than unilaterally changed. Changing the uid derivation is a
protocol-level change affecting recording (`_rtc_token(channel_name, uid)` in
`agora_cloud_recording_service.py`), the token minter, and any stored `participant_sid`,
and it should be an explicit decision rather than a side effect of the layout work.

---

## 9. Comments, reactions, viewer count

**Comments.** `/api/pulse/live/<id>/chat` reads the last 80 messages from
`pulse_live_chat` joined to `users`, newest-first then reversed. It is keyed on
`live_id` alone and is entirely independent of the guest roster. A guest joining cannot
reset, duplicate or clear it server-side. Stage 27 is a client-side non-regression
check: the risk is a React remount in the new layout unmounting `LiveChatOverlay.tsx`.

**Reactions.** `/react` writes to `pulse_live_reactions` keyed on `live_id`; `/state`
returns the last 24. Audience-wide by construction. Stage 28 is a non-regression check.

**Viewer count.** One rule, in one place, used by both `/state` and `/join`:

```sql
SELECT COUNT(*) AS total FROM pulse_live_viewers
WHERE live_id=? AND status IN ('watching','hosting')
```

The host is inserted as `'hosting'` at Live start (bot.py:48341) and every audience
member as `'watching'`. A guest was an audience member first and their
`pulse_live_viewers` row is **never transitioned when they are promoted** — the only
other writes are the `'watching'` upserts at 50365 and 50636 and the bulk `'left'` at
Live end (51424). **So the existing rule is: guests DO count as viewers, and the host
does too.** Stage 29 requires this to be one explicit server rule; it already is one, it
is simply undocumented. It is recorded here as the decided rule. The Stage 29 risk is
that a naive "show guests separately" change double-counts them.

---

## 10. Recording and archive

`services/agora_cloud_recording_service.py` runs `MODE = "mix"`:

```python
"recordingConfig": {"channelType": 1, "streamTypes": 2, "streamMode": "default",
  "videoStreamType": 0, "maxIdleTime": 120,
  "transcodingConfig": {"width": 720, "height": 1280, "fps": 30, "bitrate": 2500,
                        "mixedVideoLayout": 1, "backgroundColor": "#000000"}},
"recordingFileConfig": {"avFileType": ["hls"]},
```

This is **already a composite recording**. `mixedVideoLayout: 1` is Agora's floating
layout and `streamTypes: 2` captures audio and video. Cloud recording is wired into the
Live lifecycle in `bot.py` around 49849–49950 (acquire / start / query / stop), and the
replay is published exactly once through the existing reels pipeline by
`pulse_live_publish_replay_reel`, idempotent on `pulse_live_sessions.replay_reel_id`.

The Stage 32 concern — "must not silently archive host-only" — therefore appears
**already satisfied**, not by intent but because mix-mode recording composites whatever
publishes. This must be verified against a real multi-guest recording rather than
asserted from configuration, and it is the honest answer required by Stage 32 either
way. Stage 33's investigation is consequently narrower than the brief anticipated: the
question is not whether to build composite recording but what the mixed layout looks
like with 2–13 publishers at a 720×1280 portrait canvas, and what the per-minute cost is.
Per the mission's instruction, this is to be audited and costed, **not enabled or
changed**.

---

## 11. Configuration, flags, and preserved features

**No feature flags exist.** `MULTI_GUEST_LIVE_ENABLED`, `LIVE_GUEST_REQUESTS_ENABLED`
and `LIVE_MAX_GUESTS` do not appear anywhere in `services/`, `bot.py` or
`mobile-native/src`. Stage 40 is entirely unbuilt.

**No guest-limit constant exists.** The ceiling is the literal `LIMIT 12` inside the
`pulse_live_active_guests` SQL string, with a matching `LIMIT 30` on pending requests
and the unrelated hardcoded `3` in the host UI. Stage 5's "no scattered native
constants" is currently violated in the mildest possible way — the constant is not
scattered across native code, it is simply embedded in SQL where it cannot be
configured. The mission's chosen limit of 12 matches this, so the work is to name the
constant, not to change the number.

**`multi_guest` is sent but never consumed.** `mobile-native/src/live/liveSession.ts`
already computes and posts it:

```ts
export const MULTI_GUEST_TYPES = new Set<LiveTypeKey>(["guest", "interview", "panel", "community"]);
// buildLiveStartPayload
multi_guest: MULTI_GUEST_TYPES.has(draft.liveType),
```

Nothing server-side reads it. It is a ready-made hook for Stage 40's per-session gating.

**Screen share (Stage 34) is not implemented on native.** `LiveHostSessionScreen.tsx`
line 1118 renders a tool tile that calls `flagComingSoon("screen_share")` with the copy
*"Screen Share is landing in an upcoming native build."* The scaffolding exists in
`services/live_scene_engine.py` (a `screen_share` scene with `["screen", "camera_pip"]`
layers) and in the communications engine's event map
(`screen-share-start` / `screen-share-stop`). Stage 34 says preserve, do not combine
with a rewrite — so the correct action is to leave all of it untouched.

**Live music mixing (Stage 35)** lives in `liveMusicMixing.ts` (49 lines) over Agora's
`startAudioMixing`. Preserve unchanged.

---

## 12. Stage 0 verdict — what is built vs what is not

**Already correct; must be preserved, and protected by regression tests rather than
rebuilt:**

- One Agora channel per Live, `ChannelProfileLiveBroadcasting` (Stage 2)
- Server-authoritative role decision; token privilege enforces publishing (Stages 3, 4)
- Single audio-session owner, single mic owner, single engine instance (Stage 16)
- In-place role promotion with no channel restart and preview before publish
  (Stages 9, 10)
- Audience request-to-join with host approval (Stage 7)
- Host moderation: mute, unmute, remove (Stage 19)
- Leave stage ≠ end Live at the API layer (Stage 20)
- Comments, reactions and viewer count keyed on `live_id`, roster-independent
  (Stages 27, 28, 29)
- Composite mix-mode recording, idempotent single replay reel (Stages 32, 33)

**Genuinely absent; this is the build:**

| Gap | Stage | Location |
|---|---|---|
| Canonical participant registry; server-supplied `rtc_uid` | 11 | `pulse_live_guest_payload`, `useAgoraLiveBroadcastRoom.ts` |
| Remote name/identity invented; host guessed positionally | 11, 13 | hook line 134 |
| Audience renders one video via `.find()` | 12 | `LiveScreen.tsx` 335, 637 |
| Host stage caps at 3 tiles; binary split | 12, 13 | `LiveHostSessionScreen.tsx` |
| No `enableAudioVolumeIndication`; `speaking` always false | 14 | hook |
| Dual-stream publisher-only; no fallback option | 24, 26 | hook |
| Host-initiated invite; no `invite_id` | 6, 30 | new route |
| Co-host is an alias for guest | 22 | `generate_agora_live_token`, `token_role` |
| No feature flags | 40 | server + client |
| Guest limit embedded in SQL, not configurable | 5 | `pulse_live_active_guests` |
| Multi-device / duplicate uid policy undefined | 38, 39 | `_agora_uid` |
| Guest waiting-state machine | 8 | client |

**Reported, not acted on without a decision:** uid = user_id (enumeration, no per-Live
scoping, duplicate-device kick), and the vestigial `livekit_*` fields in the guest
payload.

---

## 13. Git and protection posture for this mission

Per Stage 57, no `git add -A`, no `reset --hard`, no `clean -fd`, no force push, and
hunk-by-hunk inspection of shared Agora and audio files before staging. The working tree
contains concurrent work from other missions, so only files this mission touches will be
staged, by explicit path.

The realtime-audio gate must be run locally before any staging:

```
python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD
```

`config/realtime-audio-protected-paths.json` holds 58 protected paths plus a
`dependency_watch` list. `bot.py` is protected by diff *content* through
`backend_diff_patterns`, not by path. Live changes are authorized for this mission;
audio-call, video-call and Messenger voice paths are not, and the gate is the mechanism
that proves the distinction was respected.

Per Stage 54, the hard-lock baseline stays **NOT UPDATED** until physical-device
acceptance passes. Promoting the multi-guest path to the protected known-good foundation
is the last action of the mission, not an early one.
