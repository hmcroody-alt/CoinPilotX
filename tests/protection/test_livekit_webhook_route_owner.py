#!/usr/bin/env python3
"""Ensure the provider webhook cannot be shadowed by the Communications pack."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
COMM = (ROOT / "pulse_communications_v2" / "routes.py").read_text(encoding="utf-8")


def test_provider_webhook_has_one_canonical_owner() -> None:
    assert '@webhook_app.route("/api/livekit/webhook"' in BOT
    assert '@comm_v2_blueprint.post("/api/livekit/webhook")' not in COMM
    assert '@comm_v2_blueprint.post("/api/pulse/communications/v2/livekit/webhook")' in COMM


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
