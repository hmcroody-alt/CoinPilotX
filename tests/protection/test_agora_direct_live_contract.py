import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ROOM = (ROOT / "mobile-native/src/live/useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
REELS_VIEWER = (ROOT / "mobile-native/src/components/reels/ReelLiveViewerSurface.tsx").read_text(encoding="utf-8")
LIVE_SCREEN = (ROOT / "mobile-native/src/screens/LiveScreen.tsx").read_text(encoding="utf-8")


def test_agora_publish_does_not_require_mux_media_push():
    route = BOT[BOT.index("def api_pulse_live_browser_publish"):BOT.index("def pulse_live_publish_replay_reel")]
    agora_branch = route[route.index('if provider == "agora":'):]
    assert "start_mux_bridge" not in agora_branch
    assert "agora_host_publishing" in agora_branch
    assert '"preferred_transport": "agora"' in agora_branch


def test_agora_live_start_skips_realtime_mux_stream():
    assert 'os.getenv("LIVE_RTC_PROVIDER"' not in BOT
    assert "post-Live VOD is created from" in BOT
    assert "finalized recording artifact" in BOT


def test_agora_live_start_has_its_agora_service_bound_without_livekit():
    """Regression: the start route created a Live, then failed building JSON."""
    imports = BOT[BOT.index("from services import ("):BOT.index("from services import (") + 16_000]
    route = BOT[BOT.index("def api_pulse_live_start"):BOT.index("def pulse_live_can_create_mux_stream")]

    assert "pulsesoc_communications_engine as call_engine" in imports
    assert '"configured": call_engine.agora_config_status().get("configured")' in route
    assert '"token_url": f"/api/pulse/live/{live_id}/rtc/token"' in route
    assert "pulse_livekit_config" not in route
    assert '"ok": True' in route


def test_audience_is_receive_only_and_auto_subscribes():
    assert "ClientRoleAudience" in ROOM
    assert "publishMicrophoneTrack: publish" in ROOM
    assert "publishCameraTrack: Boolean(localTrack)" in ROOM
    assert "autoSubscribeAudio: true" in ROOM
    assert "autoSubscribeVideo: true" in ROOM
    assert "onFirstRemoteAudioDecoded" in ROOM
    assert "onFirstRemoteVideoDecoded" in ROOM
    assert "muteAllRemoteAudioStreams(!enabled)" in ROOM


# ---------------------------------------------------------------------------
# The multi-guest wiring, pinned at the source level because it cannot be
# pinned at the behavioural one.
#
# `useAgoraLiveBroadcastRoom` resolves the SDK with `await import(...)` in five
# places. Under this project's Jest configuration that call raises
# "A dynamic import callback was invoked without --experimental-vm-modules"
# before any assertion runs -- verified empirically, not assumed -- so the hook
# cannot be rendered in a unit test at all. Every decision it makes is extracted
# into a pure module that IS tested (liveSeatReconciliation, liveAudioMatrix,
# liveStreamQuality, liveMusicMixing, liveParticipantRegistry), and those suites
# are cited in reports/agora_audio_test_relevance_audit.md.
#
# What no pure suite can cover is whether the hook still ASKS. A decision layer
# that returns the right answer to a caller that has stopped calling it is the
# most expensive kind of green test. These assertions are deliberately narrow:
# they pin the four seams where a correct decision reaches, or fails to reach,
# the engine. They are weaker than a behavioural test and are not a substitute
# for one -- see section 7 of reports/realtime_audio_verified_baseline.md for
# the physical validation that remains required.
# ---------------------------------------------------------------------------


def test_only_a_genuinely_different_seat_can_tear_the_engine_down():
    """The property the whole multi-guest feature rests on.

    ``rejoin`` is the only ``SeatAction`` that destroys the engine, stops the
    camera, drops the microphone and restarts the audio session, and
    ``reconcileLiveSeat`` returns it only when the channel or the RTC uid
    changed. That guarantee is worth nothing if ``connect`` reaches its teardown
    by some other route, so: the hook must consult the reconciler, the four
    non-disruptive verdicts must each return before the teardown, and the
    teardown itself must appear exactly once.
    """
    connect = ROOM[ROOM.index("const connect = useCallback"):ROOM.index("const setMicrophoneEnabled")]

    assert "reconcileLiveSeat(" in connect
    teardown = 'await disconnect("replaced_room")'
    assert connect.count(teardown) == 1, "more than one path tears the engine down"

    before_teardown = connect[: connect.index(teardown)]
    for verdict in ('action === "noop"', 'action === "renew_token"',
                    'action === "promote"', 'action === "demote"'):
        assert verdict in before_teardown, (
            f"{verdict} is no longer handled before the teardown, so a guest "
            "arriving or a token refreshing can now restart a live broadcast"
        )


def test_a_role_change_stays_inside_the_session():
    """Promotion and demotion are role changes in place, never a rejoin.

    ``updateChannelMediaOptions`` and ``setClientRole`` change what this client
    publishes without leaving the channel. Reaching for ``joinChannel`` here
    instead would be invisible in review -- the guest still ends up on stage --
    and would drop the host's audio for everyone mid-broadcast.
    """
    branch = ROOM[ROOM.index('if ((action === "promote" || action === "demote")'):
                  ROOM.index('if (engineRef.current) await disconnect("replaced_room")')]

    assert ".joinChannel" not in branch
    assert ".leaveChannel" not in branch
    assert ".release()" not in branch
    assert "setClientRole(agora.ClientRoleType.ClientRoleBroadcaster)" in branch
    assert "setClientRole(agora.ClientRoleType.ClientRoleAudience)" in branch
    # Stage 25: back in the audience this device publishes nothing at all.
    assert "publishMicrophoneTrack: false, publishCameraTrack: false" in branch


def test_microphone_gain_is_never_set_from_a_raw_ui_level():
    """``adjustRecordingSignalVolume`` is the microphone's gain, not the music's.

    The UI works in mix levels; Agora works in a 0-400 volume. ``liveMixMixing``
    owns that conversion and clamps it. Passing a level straight through would
    silence a host on a live broadcast while every meter in the app still read
    healthy, which is why every call site must go through the converter.
    """
    for line in ROOM.splitlines():
        if "adjustRecordingSignalVolume(" in line:
            call = line[line.index("adjustRecordingSignalVolume("):]
            assert call.startswith("adjustRecordingSignalVolume(liveMixLevelToAgoraVolume("), (
                f"microphone gain set from an unconverted value: {line.strip()}"
            )


def test_an_audio_scenario_change_always_restores_music_mixing():
    """Stage 35, pinned at the seam rather than in the pure function.

    Moving to the echo-control scenario reconfigures Agora's audio module and
    silently drops any mixing in flight -- and the move happens precisely when
    the first guest comes on stage. ``musicRestorationAfterAudioChange`` decides
    what to reapply; this asserts the hook still asks it, in the same branch,
    after the scenario actually changed.
    """
    body = ROOM[ROOM.index("setAudioScenario(scenario)"):]
    restoration = body.index("musicRestorationAfterAudioChange(")
    # Anything later than the next engine-level call would be a different branch.
    assert restoration < body.index("adjustRecordingSignalVolume("), (
        "the scenario change no longer restores mixing before touching volumes"
    )
    assert "adjustAudioMixingPublishVolume" in body
    assert "adjustAudioMixingPlayoutVolume" in body


def test_the_encoder_ladder_is_driven_by_the_stage_size_module():
    """Audio is never on the degradation ladder; video profile is.

    ``publisherVideoProfile`` is the protected reducer that encodes both rules.
    The hook must take its profile from there rather than computing one inline,
    or the "audio never degrades" invariant is enforced in a module nothing
    calls.
    """
    assert 'from "./liveStreamQuality"' in ROOM
    assert "publisherVideoProfile(" in ROOM


def test_reels_and_live_screen_choose_direct_agora_viewer():
    assert 'const credentials = await getLiveRtcToken(liveId, "viewer")' in REELS_VIEWER
    assert 'if (hlsUrl) {\n        setMode("hls")' not in REELS_VIEWER
    assert 'const currentProvider = "agora"' in LIVE_SCREEN
    assert "PulseSoc could not establish native playback for this Live." in LIVE_SCREEN


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
