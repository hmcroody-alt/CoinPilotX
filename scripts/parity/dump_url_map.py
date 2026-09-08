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

import argparse
import json
import os
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=pathlib.Path,
        help="write the JSON here instead of stdout. Importing bot prints ~117kB "
             "of boot banner and logging to stdout, so the documented "
             "`dump_url_map.py > snapshot.json` produces a file that starts with "
             "log lines and cannot be parsed. Redirecting stdout does not help — "
             "the noise is on stdout too. Prefer --out.")
    args = parser.parse_args()

    os.environ.setdefault("DATABASE_URL", "sqlite:////tmp/parity_url_map_probe.db")
    sys.path.insert(0, str(REPO_ROOT))
    import bot  # noqa: E402  (import must follow the env setup above)

    rules = sorted({rule.rule for rule in bot.webhook_app.url_map.iter_rules()})
    if args.out:
        args.out.write_text(json.dumps(rules, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {len(rules)} rules to {args.out}", file=sys.stderr)
        return 0
    # Kept for compatibility, but fenced so a redirect of this stream is at
    # least recoverable rather than silently invalid.
    sys.stdout.write("<<<URL_MAP>>>")
    json.dump(rules, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
