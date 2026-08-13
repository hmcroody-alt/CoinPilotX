"""Regression contracts for keeping the stable Pulse ID out of public surfaces."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = next(item for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == name)
    return ast.get_source_segment(source, node) or ""


def test_public_profile_dtos_do_not_serialize_raw_pulse_id():
    bot = ROOT / "bot.py"
    for function_name in ("pulse_mobile_user_payload", "pulse_search_users", "pulse_native_profile_payload"):
        function = _function_source(bot, function_name)
        assert '"pulse_id":' not in function


def test_legacy_identity_resolution_does_not_echo_raw_pulse_id():
    function = _function_source(ROOT / "bot.py", "api_pulse_identity")
    assert "pulse_id_service.resolve_user_id" in function
    assert '"pulse_id":' not in function
    assert '"canonical_profile_key":' in function


def test_native_public_surfaces_do_not_render_raw_pulse_id_badges():
    paths = (
        "components/ProfileHeader.tsx",
        "screens/SearchScreen.tsx",
        "screens/NewChatScreen.tsx",
        "screens/PulseShareScreen.tsx",
        "screens/BusinessBuyerPreviewScreen.tsx",
        "screens/PulseIdentityScreen.tsx",
    )
    for relative in paths:
        source = (ROOT / "mobile-native" / "src" / relative).read_text(encoding="utf-8")
        assert "PulseIdBadge" not in source, relative
        assert "profile.pulse_id" not in source, relative
