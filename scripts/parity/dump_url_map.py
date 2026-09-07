#!/usr/bin/env python3
"""Dump Flask's real ``url_map`` — the only authoritative web route list.

Static analysis of ``bot.py`` undercounts by ~240 rules because optional route
packs are registered as blueprints inside ``except Exception`` blocks, so the
rules exist only once the module has actually imported. Booting is also the
only way to observe a pack that failed to register in this environment.

Importing ``bot`` runs ``init_db()``. Point ``DATABASE_URL`` at a throwaway
SQLite file — never a real database — because the import seeds owner-admin
credentials as a side effect.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/parity_url_map_probe.db")
    sys.path.insert(0, str(REPO_ROOT))
    import bot  # noqa: E402  (import must follow the env setup above)

    rules = sorted({rule.rule for rule in bot.webhook_app.url_map.iter_rules()})
    json.dump(rules, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
