"""Feature flags for Pulse Communications 2.0."""

from __future__ import annotations

import os


FLAG_NAME = "PULSE_COMMUNICATIONS_V2_ENABLED"


def is_enabled() -> bool:
    return os.getenv(FLAG_NAME, "false").strip().lower() in {"1", "true", "yes", "on"}


PULSE_COMMUNICATIONS_V2_ENABLED = is_enabled()
