"""Exercise the boot helper without importing the entire web application."""
import ast
import logging
from datetime import datetime
from pathlib import Path


def test_bootstrap_password_never_enters_log_record(caplog):
    tree = ast.parse((Path(__file__).parents[1] / "bot.py").read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "remember_owner_temp_password")
    state = {}
    namespace = {"OWNER_BOOTSTRAP_TEMP": state, "OWNER_ADMIN_EMAIL": "staging@example.invalid",
                 "datetime": datetime, "logging": logging}
    exec(compile(ast.Module(body=[function], type_ignores=[]), "boot-helper", "exec"), namespace)
    sentinel = "synthetic-password-not-a-real-credential"
    namespace[function.name](sentinel, "owner_admin_created")
    assert state["password"] == sentinel  # Preserve the authenticated one-time retrieval flow.
    assert state["display_available"]
    assert "GENERATED ONCE" in caplog.text
    assert sentinel not in caplog.text
    assert all(sentinel not in str(record.args) for record in caplog.records)
