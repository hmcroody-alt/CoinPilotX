import base64
import json
import os
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services import pulsesoc_communications_engine as calls


def _decode_payload(token: str) -> dict:
    payload = token.split(".")[1]
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))


class PulseSocCallLiveKitGrantTests(unittest.TestCase):
    def test_video_call_livekit_token_allows_microphone_and_camera(self):
        with patch.dict(os.environ, {"LIVEKIT_URL": "wss://livekit.example.test", "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-secret"}):
            token = calls._generate_livekit_token("room-video", 42, "video")

        self.assertTrue(token["ok"])
        grants = _decode_payload(token["token"])["video"]
        self.assertTrue(grants["roomJoin"])
        self.assertTrue(grants["canPublish"])
        self.assertTrue(grants["canSubscribe"])
        self.assertEqual(grants["canPublishSources"], ["microphone", "camera"])

    def test_audio_call_livekit_token_stays_microphone_only(self):
        with patch.dict(os.environ, {"LIVEKIT_URL": "wss://livekit.example.test", "LIVEKIT_API_KEY": "test-key", "LIVEKIT_API_SECRET": "test-secret"}):
            token = calls._generate_livekit_token("room-audio", 42, "audio")

        self.assertTrue(token["ok"])
        grants = _decode_payload(token["token"])["video"]
        self.assertEqual(grants["canPublishSources"], ["microphone"])


if __name__ == "__main__":
    unittest.main()
