from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
ROOM = (ROOT / "mobile-native/src/live/useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
REELS_VIEWER = (ROOT / "mobile-native/src/components/reels/ReelLiveViewerSurface.tsx").read_text(encoding="utf-8")
LIVE_SCREEN = (ROOT / "mobile-native/src/screens/LiveScreen.tsx").read_text(encoding="utf-8")


def test_agora_publish_does_not_require_mux_media_push():
    route = BOT[BOT.index("def api_pulse_live_browser_publish"):BOT.index("def pulse_live_publish_replay_reel")]
    agora_branch = route[route.index('if provider == "agora":'):route.index("existing_egress =")]
    assert "start_mux_bridge" not in agora_branch
    assert "agora_host_publishing" in agora_branch
    assert '"preferred_transport": "agora"' in agora_branch


def test_agora_live_start_skips_realtime_mux_stream():
    assert 'os.getenv("LIVE_RTC_PROVIDER", "livekit")' in BOT
    assert "post-Live VOD is created from the finalized" in BOT


def test_audience_is_receive_only_and_auto_subscribes():
    assert "ClientRoleAudience" in ROOM
    assert "publishMicrophoneTrack: publish" in ROOM
    assert "publishCameraTrack: Boolean(localTrack)" in ROOM
    assert "autoSubscribeAudio: true" in ROOM
    assert "autoSubscribeVideo: true" in ROOM
    assert "onFirstRemoteAudioDecoded" in ROOM
    assert "onFirstRemoteVideoDecoded" in ROOM
    assert "muteAllRemoteAudioStreams(!enabled)" in ROOM


def test_reels_and_live_screen_choose_direct_agora_viewer():
    assert 'const credentials = await getLiveKitToken(liveId, "viewer")' in REELS_VIEWER
    assert 'if (hlsUrl) {\n        setMode("hls")' not in REELS_VIEWER
    assert 'currentProvider === "agora"' in LIVE_SCREEN
    assert "PulseSoc could not establish native playback for this Live." in LIVE_SCREEN
