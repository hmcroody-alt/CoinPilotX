import base64
import ast
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import time
from typing import Any
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_token_contract() -> dict:
    source_path = ROOT / "services" / "pulsesoc_communications_engine.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    names = {"_env_enabled", "_env_enabled_with_legacy", "_realtime_audio_v2_status", "_generate_livekit_token"}
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    module = ast.Module(body=functions, type_ignores=[])
    namespace = {
        "Any": Any,
        "base64": base64,
        "datetime": datetime,
        "hashlib": hashlib,
        "hmac": hmac,
        "os": os,
        "time": time,
        "timezone": timezone,
        "_require_livekit": lambda: None,
        "_json_dumps": lambda value: json.dumps(value, separators=(",", ":")),
        "_base64url": lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii"),
        "_livekit_ws_url": lambda: "wss://qa-livekit.example",
    }
    exec(compile(module, str(source_path), "exec"), namespace)
    return namespace


CALLS = _load_token_contract()


def _decode_payload(token: str) -> dict:
    encoded = token.split(".")[1]
    encoded += "=" * (-len(encoded) % 4)
    return json.loads(base64.urlsafe_b64decode(encoded.encode("ascii")))


class CallLiveKitTokenGrantTests(unittest.TestCase):
    ENV = {
        "LIVEKIT_API_KEY": "qa-key",
        "LIVEKIT_API_SECRET": "qa-secret",
        "LIVEKIT_URL": "https://qa-livekit.example",
    }

    def test_audio_call_token_is_room_and_microphone_scoped(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            result = CALLS["_generate_livekit_token"]("pulsesoc-call-a", 42, "audio", "caller")
        payload = _decode_payload(result["token"])

        self.assertEqual(result["room_type"], "audio_call")
        self.assertEqual(result["participant_role"], "caller")
        self.assertEqual(result["can_publish_sources"], ["microphone"])
        self.assertEqual(payload["sub"], "user-42")
        self.assertEqual(payload["video"], {
            "roomJoin": True,
            "room": "pulsesoc-call-a",
            "canPublish": True,
            "canSubscribe": True,
            "canPublishData": True,
            "canPublishSources": ["microphone"],
        })
        self.assertEqual(json.loads(payload["metadata"]), {
            "room_type": "audio_call",
            "participant_role": "caller",
            "authenticated_user_id": 42,
        })
        self.assertFalse(result["realtime_audio_v2_enabled"])
        self.assertFalse(result["realtime_audio_shared_path_enabled"])
        self.assertTrue(result["realtime_audio_v2_fallback_enabled"])

    def test_video_call_token_allows_only_microphone_and_camera(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            result = CALLS["_generate_livekit_token"]("pulsesoc-call-v", 84, "video", "callee")
        payload = _decode_payload(result["token"])

        self.assertEqual(result["room_type"], "video_call")
        self.assertEqual(result["participant_role"], "callee")
        self.assertEqual(payload["video"]["canPublishSources"], ["microphone", "camera"])
        self.assertEqual(payload["video"]["room"], "pulsesoc-call-v")

    def test_untrusted_client_role_is_not_embedded_as_authority(self) -> None:
        with patch.dict(os.environ, self.ENV, clear=False):
            result = CALLS["_generate_livekit_token"]("pulsesoc-call-a", 42, "audio", "host")

        self.assertEqual(result["participant_role"], "member")
        self.assertEqual(json.loads(_decode_payload(result["token"])["metadata"])["participant_role"], "member")

    def test_v2_activation_requires_master_and_feature_flags(self) -> None:
        environment = {
            **self.ENV,
            "REALTIME_AUDIO_PLATFORM_V2_ENABLED": "true",
            "REALTIME_AUDIO_CALLS_V2_ENABLED": "true",
            "REALTIME_VIDEO_CALLS_V2_ENABLED": "false",
        }
        with patch.dict(os.environ, environment, clear=False):
            audio = CALLS["_generate_livekit_token"]("audio", 1, "audio", "caller")
            video = CALLS["_generate_livekit_token"]("video", 1, "video", "caller")

        self.assertTrue(audio["realtime_audio_v2_enabled"])
        self.assertFalse(video["realtime_audio_v2_enabled"])

    def test_shared_path_names_are_authoritative_over_legacy_aliases(self) -> None:
        environment = {
            **self.ENV,
            "REALTIME_AUDIO_PLATFORM_V2_ENABLED": "true",
            "REALTIME_AUDIO_CALLS_SHARED_PATH": "false",
            "REALTIME_AUDIO_CALLS_V2_ENABLED": "true",
        }
        with patch.dict(os.environ, environment, clear=False):
            result = CALLS["_generate_livekit_token"]("audio", 1, "audio", "caller")

        self.assertFalse(result["realtime_audio_shared_path_enabled"])
        self.assertFalse(result["realtime_audio_v2_enabled"])


if __name__ == "__main__":
    unittest.main()
