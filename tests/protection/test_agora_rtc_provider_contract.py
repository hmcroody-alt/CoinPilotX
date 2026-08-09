import ast
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE_PATH = ROOT / "services" / "pulsesoc_communications_engine.py"
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


class AgoraRtcProviderContractTests(unittest.TestCase):
    def test_provider_defaults_to_livekit_and_is_allowlisted(self) -> None:
        tree = ast.parse(SOURCE, filename=str(SOURCE_PATH))
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "rtc_provider")
        text = ast.unparse(function)
        self.assertIn("os.getenv('RTC_PROVIDER', 'livekit')", text)
        self.assertIn("{'livekit', 'agora'}", text)

    def test_agora_certificate_is_server_only(self) -> None:
        self.assertIn('os.getenv("AGORA_APP_CERTIFICATE"', SOURCE)
        native_source = "\n".join(path.read_text(encoding="utf-8") for path in (ROOT / "mobile-native" / "src").rglob("*.ts*"))
        self.assertNotIn("AGORA_APP_CERTIFICATE", native_source)

    def test_existing_livekit_token_path_remains_selectable(self) -> None:
        self.assertIn("def _generate_livekit_token", SOURCE)
        self.assertIn('return _generate_livekit_token(room_name, user_id, call_type, participant_role)', SOURCE)
        package = (ROOT / "mobile-native" / "package.json").read_text(encoding="utf-8")
        self.assertIn('"@livekit/react-native"', package)
        self.assertIn('"react-native-agora": "4.6.2"', package)


if __name__ == "__main__":
    unittest.main()
