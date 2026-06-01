"""Feature flags for the staged Pulse Communications 2.0 foundation."""

from __future__ import annotations

import os


def _enabled(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


PULSE_COMMUNICATIONS_V2_ENABLED = _enabled("PULSE_COMMUNICATIONS_V2_ENABLED", "false")

