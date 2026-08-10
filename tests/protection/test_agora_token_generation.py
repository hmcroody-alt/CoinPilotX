import os
from pathlib import Path
import unittest
from unittest.mock import patch

from services import pulsesoc_communications_engine as rtc


ROOT = Path(__file__).resolve().parents[2]


class AgoraTokenGenerationTests(unittest.TestCase):
    ENV = {"AGORA_APP_ID": "a" * 32, "AGORA_APP_CERTIFICATE": "b" * 32, "AGORA_TOKEN_TTL_SECONDS": "900"}

    def test_call_token_is_bound_to_server_channel_and_authenticated_uid(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            result = rtc._generate_agora_token("pulsesoc-call-authorized", 42, "video", "callee")
        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "agora")
        self.assertEqual(result["channel_name"], "pulsesoc-call-authorized")
        self.assertEqual(result["uid"], 42)
        self.assertEqual(result["can_publish_sources"], ["microphone", "camera"])
        self.assertNotIn(self.ENV["AGORA_APP_CERTIFICATE"], result["token"])

    def test_live_viewer_cannot_publish_and_cohost_can_only_use_authorized_role(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            viewer = rtc.generate_agora_live_token("pulse-live-1", 5, "viewer", live_id=1, host_user_id=9)
            cohost = rtc.generate_agora_live_token("pulse-live-1", 6, "cohost", live_id=1, guest_id=7, request_id=8, host_user_id=9)
        self.assertFalse(viewer["can_publish"])
        self.assertEqual(viewer["can_publish_sources"], [])
        self.assertTrue(cohost["can_publish"])
        self.assertEqual(cohost["guest_id"], 7)

    def test_host_and_viewer_share_channel_but_have_unique_positive_uids(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            host = rtc.generate_agora_live_token("pulse-live-88", 41, "host", live_id=88, host_user_id=41)
            viewer = rtc.generate_agora_live_token("pulse-live-88", 42, "viewer", live_id=88, host_user_id=41)
        self.assertEqual(host["channel_name"], viewer["channel_name"])
        self.assertGreater(host["uid"], 0)
        self.assertGreater(viewer["uid"], 0)
        self.assertNotEqual(host["uid"], viewer["uid"])

    def test_routes_recheck_pulsesoc_identity_and_guest_authorization(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        route = source[source.index('def api_pulse_live_agora_token'):source.index('def api_pulse_live_rtc_token')]
        self.assertIn("api_account_user()", route)
        self.assertIn("pulse_live_active_guest", route)
        self.assertIn("NOT_AUTHORIZED", route)
        self.assertIn("TOKEN_MISSING_PUBLISH_PERMISSION", route)

    def test_canonical_live_route_is_agora_only(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        selector = source[source.index('def api_pulse_live_rtc_token'):source.index('@webhook_app.route("/api/pulse/live/<int:live_id>/guests/')]
        self.assertIn('return api_pulse_live_agora_token(live_id)', selector)
        self.assertNotIn('os.getenv("LIVE_RTC_PROVIDER"', selector)

    def test_native_agora_live_quality_and_publish_confirmation_contract(self) -> None:
        adapter = (ROOT / "mobile-native/src/live/useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        host = (ROOT / "mobile-native/src/screens/LiveHostSessionScreen.tsx").read_text(encoding="utf-8")
        self.assertIn("AudioProfileMusicHighQuality", adapter)
        self.assertIn("width: 720, height: 1280", adapter)
        self.assertIn("MaintainBalanced", adapter)
        self.assertIn("StreamFallbackOptionAudioOnly", adapter)
        self.assertIn("onFirstLocalAudioFramePublished", adapter)
        self.assertIn("onFirstLocalVideoFramePublished", adapter)
        self.assertIn('room.provider === "agora" && (audioTracks <= 0 || videoTracks <= 0)', host)

    def test_deployment_audit_catches_missing_canonical_rtc_route(self) -> None:
        audit = (ROOT / "scripts/pulsesoc_agora_live_route_audit.py").read_text(encoding="utf-8")
        self.assertIn('/api/pulse/live/{args.live_id}/rtc/token', audit)
        self.assertIn('status == 401 and error_code == "NOT_AUTHENTICATED"', audit)
        self.assertNotIn("Authorization", audit)

    def test_viewer_adapter_waits_for_real_join_and_remote_media(self) -> None:
        adapter = (ROOT / "mobile-native/src/live/useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        wrapper = (ROOT / "mobile-native/src/live/useLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        reels = (ROOT / "mobile-native/src/components/reels/ReelLiveViewerSurface.tsx").read_text(encoding="utf-8")
        self.assertIn("autoSubscribeAudio: true", adapter)
        self.assertIn("autoSubscribeVideo: true", adapter)
        self.assertIn("ClientRoleAudience", adapter)
        self.assertIn("onFirstRemoteAudioDecoded", adapter)
        self.assertIn("onFirstRemoteVideoDecoded", adapter)
        self.assertIn("muteAllRemoteAudioStreams", adapter)
        self.assertIn("Promise.race([joinOutcome", adapter)
        self.assertIn("12_000", adapter)
        self.assertIn("return useAgoraLiveBroadcastRoom()", wrapper)
        self.assertIn("viewer_media_timeout", reels)
        self.assertIn("15_000", reels)

    def test_agora_cohost_uses_provider_ready_state_and_server_confirmed_tracks(self) -> None:
        backend = (ROOT / "bot.py").read_text(encoding="utf-8")
        ready = backend[backend.index("def pulse_live_cohost_live_status"):backend.index("def pulse_live_guest_request_payload")]
        confirmation = backend[backend.index("def api_pulse_live_guest_publish_complete"):backend.index('@webhook_app.route("/api/pulse/live/<int:live_id>/debug-event"')]
        adapter = (ROOT / "mobile-native/src/live/useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        screen = (ROOT / "mobile-native/src/screens/LiveScreen.tsx").read_text(encoding="utf-8")
        self.assertIn('provider == "agora"', ready)
        self.assertIn('publish_state == "agora_host_publishing"', ready)
        self.assertIn('stream_health == "agora_connected"', ready)
        self.assertIn('is_agora_live', confirmation)
        self.assertIn('audio_track_count <= 0 or video_track_count <= 0', confirmation)
        self.assertIn('not in {"accepted", "joining", "joined", "publishing"}', confirmation)
        self.assertIn("setClientRole(agora.ClientRoleType.ClientRoleBroadcaster)", adapter)
        self.assertIn("setClientRole(agora.ClientRoleType.ClientRoleAudience)", adapter)
        self.assertIn('await leaveGuest(activeLiveId, currentGuest.guestId)', screen)
        self.assertNotIn('room.error || "PulseSoc is joining the existing LiveKit room', screen)


if __name__ == "__main__":
    unittest.main()
