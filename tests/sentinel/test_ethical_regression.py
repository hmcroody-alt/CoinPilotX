"""Stage 22: non-destructive ethical security regression suite.

Static self-inspection of the Sentinel codebase — no live attacking, no
network calls, no fuzzing of production surfaces. These tests prevent the
foundation from regressing into the failure modes the constitution forbids.
"""

import inspect
import pathlib

from services import sentinel as sentinel_pkg
from services.sentinel import api as sentinel_api
from services.sentinel import runbooks

SENTINEL_DIR = pathlib.Path(sentinel_pkg.__file__).parent


def _all_source() -> str:
    return "\n".join(p.read_text() for p in sorted(SENTINEL_DIR.glob("*.py")))


class TestNoSuperKey:
    def test_no_super_key_anywhere(self):
        source = _all_source()
        assert "SENTINEL_SUPER_KEY" not in source
        assert "SUPER_ADMIN_TOKEN" not in source

    def test_no_hardcoded_secrets(self):
        source = _all_source()
        for marker in ("sk_live_", "sk_test_", "AKIA", "-----BEGIN"):
            assert marker not in source


class TestNoDangerousExecution:
    def test_no_shell_or_eval_in_sentinel(self):
        for path in sorted(SENTINEL_DIR.glob("*.py")):
            text = path.read_text()
            for banned in ("subprocess", "os.system", "eval(", "exec(", "__import__("):
                assert banned not in text, f"{path.name} contains {banned}"

    def test_registry_has_no_forbidden_runbooks(self):
        for spec in runbooks.all_runbooks():
            for pattern in runbooks.FORBIDDEN_NAME_PATTERNS:
                assert pattern not in spec.name.lower()


class TestApiIsReadOnly:
    def test_no_mutation_http_methods(self):
        source = inspect.getsource(sentinel_api)
        for verb in (".post(", ".put(", ".delete(", ".patch(",
                     "methods=[\"POST\"", "methods=['POST'"):
            assert verb not in source, f"mutation surface found: {verb}"

    def test_blueprint_not_wired_into_bot(self):
        bot_py = SENTINEL_DIR.parent.parent / "bot.py"
        if bot_py.exists():
            text = bot_py.read_text(errors="ignore")
            assert "sentinel_bp" not in text
            assert "services.sentinel.api" not in text


class TestFinancialSafety:
    def test_sentinel_never_writes_financial_tables(self):
        source = _all_source()
        financial_tables = ("creator_ledger_entries", "seller_payouts",
                            "pulse_ad_wallets", "treasury_transactions",
                            "settlement_batches", "escrow_holds")
        for table in financial_tables:
            for stmt in ("INSERT INTO " + table, "UPDATE " + table,
                         "DELETE FROM " + table):
                assert stmt not in source, f"financial write found: {stmt}"

    def test_only_sentinel_tables_are_written(self):
        import re
        source = _all_source()
        writes = re.findall(r"(?:INSERT INTO|UPDATE|DELETE FROM)\s+([a-z_]+)", source)
        for table in writes:
            assert table.startswith("sentinel_"), f"non-sentinel write target: {table}"


class TestProtectedSystemsUntouched:
    def test_no_audio_or_livekit_references(self):
        source = _all_source()
        for banned in ("AVAudioSession", "setAudioModeAsync", "livekit",
                       "LiveKit", "expo-av"):
            assert banned not in source
