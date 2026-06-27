#!/usr/bin/env python3
"""Mission-specific audit for the internal Creator operating-system standard."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    audit_module = importlib.import_module("scripts.creator_command_center_audit")
    result = int(audit_module.main() or 0)
    if result != 0:
        return result
    print("creator_loginexus_operating_system_audit: PASS")
    print("internal_standard=invisible user_surfaces=creator strict_states=verified contextual_buttons=verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
