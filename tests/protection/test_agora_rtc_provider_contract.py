import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "services" / "pulsesoc_communications_engine.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


class AgoraRtcProviderContractTests(unittest.TestCase):
    def test_provider_is_agora_only(self) -> None:
        tree = ast.parse(SOURCE, filename=str(SOURCE_PATH))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "rtc_provider")
        text = ast.unparse(function)
        self.assertIn("return 'agora'", text)
        self.assertNotIn("os.getenv", text)

    def test_agora_certificate_is_server_only(self) -> None:
        self.assertIn('os.getenv("AGORA_APP_CERTIFICATE"', SOURCE)
        native_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native" / "src").rglob("*.ts*"))
        self.assertNotIn("AGORA_APP_CERTIFICATE", native_source)

    def test_native_dependencies_are_agora_only(self) -> None:
        self.assertIn('return _generate_agora_token(room_name, user_id, call_type, participant_role)', SOURCE)
        package = (ROOT / "mobile-native" / "package.json").read_text(encoding="utf-8")
        self.assertNotIn('"@livekit/react-native"', package)
        self.assertIn('"react-native-agora": "4.6.2"', package)

    def test_native_adapters_renew_without_logging_tokens(self) -> None:
        """Both native adapters must refresh their Agora token and never log it.

        The call-side engine used to live in ``useAgoraCallRoom.ts``. It now
        lives in ``callSessionStore.ts``: the hook disconnected the engine on
        unmount, which ended a live call as soon as the user navigated away, so
        ownership moved to a module-scoped store and the hook became a thin
        subscription over it. Renewal moved with the engine — it was not
        dropped — so this test follows it to the store rather than being
        relaxed. The invariant is unchanged: a long call must refresh its token
        before expiry, and the token must never reach the log.
        """
        call_session_store = (ROOT / "mobile-native" / "src" / "calls" / "callSessionStore.ts").read_text(encoding="utf-8")
        call_adapter = (ROOT / "mobile-native" / "src" / "calls" / "useAgoraCallRoom.ts").read_text(encoding="utf-8")
        live_adapter = (ROOT / "mobile-native" / "src" / "live" / "useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        self.assertIn("renewToken(next.token)", call_session_store)
        self.assertIn("engineRef.current?.renewToken(next.token)", live_adapter)
        # The hook must stay a delegator: if it ever creates its own engine
        # again we are back to a screen owning the call.
        self.assertNotIn("createAgoraRtcEngine", call_adapter)
        self.assertIn("callSessionStore", call_adapter)
        self.assertNotIn("console.log", call_session_store + call_adapter + live_adapter)


if __name__ == "__main__":
    unittest.main()
