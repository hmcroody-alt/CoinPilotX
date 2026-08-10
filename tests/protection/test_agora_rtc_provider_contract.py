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
        call_adapter = (ROOT / "mobile-native" / "src" / "calls" / "useAgoraCallRoom.ts").read_text(encoding="utf-8")
        live_adapter = (ROOT / "mobile-native" / "src" / "live" / "useAgoraLiveBroadcastRoom.ts").read_text(encoding="utf-8")
        self.assertIn("engine.renewToken(next.token)", call_adapter)
        self.assertIn("engineRef.current?.renewToken(next.token)", live_adapter)
        self.assertNotIn("console.log", call_adapter + live_adapter)


if __name__ == "__main__":
    unittest.main()
