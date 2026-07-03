# PulseSoc Calls Phase 3 Live QA Activation

## Scope

Phase 3 is the production-media activation gate for the PulseSoc Communications Engine. It does not add a second calling system. It verifies whether the Phase 1 foundation and Phase 2 real call UI can be proven with real LiveKit two-user audio/video calls.

## Provider Variable Presence

Railway production variable presence could not be confirmed from this shell because the Railway CLI returned:

```text
Token refresh failed: invalid_grant
Unauthorized. Please run `railway login` again.
```

Local process environment presence at the time of this report:

```text
LIVEKIT_URL: missing
LIVEKIT_API_KEY: missing
LIVEKIT_API_SECRET: missing
LIVEKIT_WEBHOOK_SECRET: missing
TURN_SERVER_URL: missing
TURN_USERNAME: missing
TURN_PASSWORD: missing
STUN_SERVER_URL: missing
```

Result: production LiveKit activation cannot be honestly confirmed from this environment until Railway authentication is restored or the variables are verified by an authorized operator.

## Provider Connectivity Result

Added/verified admin diagnostic:

```text
POST /api/admin/calls/test-config
```

The diagnostic now reports:

```text
provider
configured
url_present
api_key_present
api_secret_present
webhook_secret_present
turn_present
stun_present
missing
safe_mode
can_generate_token
can_create_test_room
can_cleanup_test_room
provider_error
```

When LiveKit config is missing, it returns a clean `config_missing` safe-mode response and does not fake success.

When LiveKit config is present, it verifies server-side token generation and attempts a temporary LiveKit room create/delete check through the LiveKit RoomService Twirp API. No provider secrets are returned.

## Audio Call QA Result

Status: blocked.

Reason: real two-user audio QA requires confirmed LiveKit provider credentials and two authenticated user sessions. This environment currently has no local LiveKit variables and Railway variable inspection is blocked by expired Railway auth.

Required proof still outstanding:

```text
User A starts audio call
User B receives incoming call overlay
User B accepts
Both join the same LiveKit room
Both publish/subscribe audio tracks
Both hear each other
Mute/unmute works both ways
End call cleans up state
```

## Video Call QA Result

Status: blocked.

Reason: same provider/auth blocker as audio QA. Camera and remote video behavior cannot be honestly marked passed until a real LiveKit room can be joined by two authenticated clients.

Required proof still outstanding:

```text
Local preview appears
Remote video appears
Remote audio works
Camera toggle works
Camera flip works where supported
Video-off state renders safely
End call records history
```

## Incoming Ring + Locked-Screen Notification QA

Backend hook status: present.

The communications engine creates `incoming_call` events through the central notification system with urgent priority, call category, call sound metadata, vibration metadata, and a conversation/call deep link.

Locked-screen push QA status: not completed here.

Reason: locked-screen push requires a configured device token/subscription and real incoming call event from a second authenticated user. This remains a live-device QA requirement after provider activation.

## Decline + Missed Call QA

Static/backend status: present.

Implemented behavior includes:

```text
decline route
end route
active call polling
ring timeout cleanup
missed call notification hook
conversation call history route
```

Real two-user decline/missed QA status: blocked until provider/authenticated-session QA is available.

## Reconnect QA

Frontend support status: present.

The call client handles LiveKit reconnect/reconnected/disconnected states, keeps the same call ID, and reports quality state changes.

Real network interruption QA status: not completed here because no configured two-user LiveKit session was available.

## Quality Reporting QA

Backend and frontend support status: present.

Verified surfaces:

```text
POST /api/calls/<call_id>/quality
communication_call_quality_reports table
frontend 30-second reporting interval
call_id/user_id-linked reports
```

Real metric population still needs a configured call session.

## Mobile QA Result

Static UI support is present from Phase 2:

```text
incoming overlay
active call screen
minimized call pill
mobile-safe controls
bottom-nav-safe shell
```

Real mobile media QA status: blocked by provider/session availability.

## Desktop QA Result

Static UI support is present from Phase 2:

```text
LiveKit client loading
remote track rendering
local preview
keyboard/tappable controls
quality status
```

Real desktop two-user media QA status: blocked by provider/session availability.

## Security Checks

Verified by code/audit:

```text
call routes require authenticated users
join-token validates call participant access
accept/decline/end require participant access
admin diagnostics require admin access
LiveKit token identity is generated server-side
LIVEKIT_API_SECRET is not exposed in frontend JS
webhook route verifies LIVEKIT_WEBHOOK_SECRET when configured
```

## Reliability Checks

Verified behavior:

```text
missing LiveKit config returns clean config_missing state
provider diagnostic does not crash when config is missing
health endpoints remain independent of optional provider failures
missed-call cleanup prevents ringing calls from staying active forever
call quality/reporting failures are isolated to call APIs
```

## Remaining Blockers

```text
BLOCKER:
Feature: Production LiveKit provider activation
What is missing: Authorized Railway variable inspection or confirmed LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, and LIVEKIT_WEBHOOK_SECRET presence
Why it is required: real two-user LiveKit room join cannot be proven without provider configuration
Where it should be configured: Railway service running PulseSoc web backend
Suggested variable/service/table: LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_WEBHOOK_SECRET
Can a safe fallback be built now: yes, already active through config_missing

BLOCKER:
Feature: Real two-user media QA
What is missing: two authenticated test users/devices and configured LiveKit backend
Why it is required: audio/video publish-subscribe, remote playback, permissions, and cleanup must be proven live
Where it should be configured: PulseSoc staging/production environment with LiveKit credentials
Suggested variable/service/table: admin call diagnostics + Messenger conversation between two test users
Can a safe fallback be built now: yes, provider-missing call attempts fail safely

BLOCKER:
Feature: Locked-screen incoming call push proof
What is missing: real device token/subscription, push provider readiness, and a live incoming call event
Why it is required: lock-screen behavior cannot be proven by static code alone
Where it should be configured: PulseSoc PWA/native push stack and test device
Suggested variable/service/table: notification device tokens and push provider config
Can a safe fallback be built now: in-app incoming overlay remains available
```

## Exact Next Steps

1. Re-authenticate Railway CLI or verify variables directly in Railway.
2. Run `POST /api/admin/calls/test-config` as an admin and confirm:
   - `configured=true`
   - `can_generate_token=true`
   - `can_create_test_room=true`
   - `can_cleanup_test_room=true`
3. Use two authenticated users to run audio and video call QA.
4. Confirm incoming call push on a registered locked device.
5. Record call IDs, event rows, quality report rows, and notification delivery jobs as production activation evidence.

## Production Readiness Decision

Not production-ready yet for real media claims.

The code path is prepared and guarded, but Phase 3 acceptance requires live two-user audio/video QA after provider configuration is verified.
