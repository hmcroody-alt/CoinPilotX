#!/usr/bin/env python3
"""Static regression audit for native Live audio + guest join repair."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    hook = read("mobile-native/src/live/useLiveBroadcastRoom.ts")
    session = read("mobile-native/src/live/liveSession.ts")
    api = read("mobile-native/src/api/live.ts")
    viewer = read("mobile-native/src/screens/LiveScreen.tsx")
    host = read("mobile-native/src/screens/LiveHostSessionScreen.tsx")
    session_test = read("mobile-native/src/live/__tests__/liveSession.test.ts")
    api_test = read("mobile-native/src/api/__tests__/live.test.ts")

    for token in (
        "setAppleAudioConfiguration",
        'audioCategory: "playAndRecord"',
        'audioMode: "videoChat"',
        "defaultToSpeaker",
        "setMicrophoneEnabled(true)",
        "setCameraEnabled(true)",
        "LIVE_LOCAL_AUDIO_NOT_PUBLISHED",
        "remoteAudioTrackCount",
        "setRemoteAudioEnabled",
    ):
        require(token in hook, f"Live broadcast hook missing audio publishing/playback repair token: {token}", failures)

    for token in (
        "canSubscribe",
        "canPublishData",
        "canUpdateOwnMetadata",
        "roomJoin",
        "guestId",
        "requestId",
        "participantName",
        "traceId",
    ):
        require(token in session, f"Live session domain missing co-host token field: {token}", failures)

    for token in (
        "getLiveJoinStatus",
        "confirmGuestPublishComplete",
        "/api/pulse/live/${liveId}/join-status",
        "/api/pulse/live/${liveId}/guests/${guestId}/publish-complete",
        'getLiveKitToken(liveId: number, role: LiveKitRole = "viewer")',
        'body: JSON.stringify({ role })',
    ):
        require(token in api, f"Live API missing production co-host route wrapper: {token}", failures)

    for token in (
        "requestToJoinLive",
        "cancelJoinRequest",
        "confirmGuestPublishComplete",
        'getLiveKitToken(activeLiveId, "cohost")',
        "connectLiveRoom(credentials, { publish: true })",
        "publishAsGuest",
        "Co-host request pending",
        "Guest Live",
        "PulseSoc confirms audio/video with the server",
    ):
        require(token in viewer, f"Native Live viewer missing guest join/publish behavior: {token}", failures)

    for token in (
        "listGuestManagement",
        "respondToJoinRequest",
        "muteGuest",
        "unmuteGuest",
        "removeGuest",
        "activeGuests",
        "Guest requests",
    ):
        require(token in host, f"Native Live host missing guest management behavior: {token}", failures)

    for token in (
        "normalizes server-verified co-host publishing claims",
        "canPublishData",
        "guestId: 91",
        "traceId: \"cohost-trace\"",
    ):
        require(token in session_test, f"Live session tests missing co-host credential regression: {token}", failures)

    for token in (
        "requests a co-host seat through the production join-request route",
        "normalizes join status with accepted guest data",
        "confirms published guest tracks through publish-complete",
        "/api/pulse/live/44/join-request",
        "/api/pulse/live/44/join-status",
        "/api/pulse/live/44/guests/91/publish-complete",
    ):
        require(token in api_test, f"Live API tests missing guest route regression: {token}", failures)

    if failures:
        print("PulseSoc Live audio/guest repair audit failed:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc Live audio/guest repair audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
