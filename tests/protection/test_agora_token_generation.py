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

    def test_routes_recheck_pulsesoc_identity_and_guest_authorization(self) -> None:
        source = (ROOT / "bot.py").read_text(encoding="utf-8")
        route = source[source.index('def api_pulse_live_agora_token'):source.index('def api_pulse_live_rtc_token')]
        self.assertIn("api_account_user()", route)
        self.assertIn("pulse_live_active_guest", route)
        self.assertIn("NOT_AUTHORIZED", route)
        self.assertIn("TOKEN_MISSING_PUBLISH_PERMISSION", route)


if __name__ == "__main__":
    unittest.main()
