#!/usr/bin/env python3
"""Reconcile the static route census against Flask's live url_map.

The census in `web_native_census.py` is a static scan, and a static scan of a
120k-line monolith is a claim that needs checking. This script boots the app and
compares what Flask actually registered against what the scanner found.

It exists because every extractor bug found so far was invisible from inside the
scanner. The scanner cannot notice that it skipped 106 method-shortcut routes;
it just reports a smaller number with equal confidence. Only the url_map can
tell you. `/pulse/intelligence` was reported as having no web surface at all
while Flask was serving it the whole time, and that single false MISSING was
enough to justify rebuilding a page that already existed.

Two directions matter, and they mean different things:

  in url_map, not in census   the scanner is blind to a route form. Always a
                              bug in the scanner.
  in census, not in url_map   a route exists in source but never reaches the
                              app -- a dead route, or a route pack whose
                              registration failed. Worth knowing either way,
                              because bot.py registers optional packs inside
                              `except Exception` and a subsystem can silently
                              vanish.

Comparison is on the *set* of paths, not the multiset: several functions may
share a path with different methods, and Flask merges those into one rule.

Usage:
    .venv/bin/python scripts/parity/reconcile_urlmap.py
    .venv/bin/python scripts/parity/reconcile_urlmap.py --check   # non-zero on drift
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_native_census as census  # noqa: E402

REPO = census.REPO

# Registered by Flask itself, not by any line of application code, so no source
# scan can or should find it.
FRAMEWORK_RULES = {"/static/<path:filename>"}


def live_rules() -> set[str]:
    """Boot the app against a throwaway database and read its url_map."""
    os.environ.setdefault(
        "DATABASE_URL",
        "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="parity-"), "census.db"))
    # Sync mode so a schema failure surfaces here rather than on a daemon thread
    # whose traceback would only reach the log.
    os.environ.setdefault("COINPILOTX_DB_INIT_STARTUP_MODE", "sync")
    if REPO not in sys.path:
        sys.path.insert(0, REPO)
    logging.disable(logging.CRITICAL)
    import bot  # noqa: PLC0415 -- import must follow the env setup above
    return {rule.rule for rule in bot.webhook_app.url_map.iter_rules()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the scanner is blind to any live route")
    args = parser.parse_args()

    scanned = census.collect_web_routes()
    static_paths = {route.path for route in scanned}
    source_of = {}
    for route in scanned:
        source_of.setdefault(route.path, f"{route.file}:{route.line}")

    live = live_rules() - FRAMEWORK_RULES
    unseen = sorted(live - static_paths)
    unrouted = sorted(static_paths - live)

    print(f"live url_map rules      : {len(live)}")
    print(f"statically scanned paths: {len(static_paths)}")
    print(f"live but unseen by scan : {len(unseen)}")
    print(f"scanned but not routed  : {len(unrouted)}")

    if unseen:
        print("\n-- live, invisible to the scanner (scanner bug):")
        for path in unseen:
            print(f"   {path}")
    if unrouted:
        print("\n-- in source, never registered (dead route or failed pack):")
        for path in unrouted:
            print(f"   {path:60} {source_of[path]}")

    if args.check and unseen:
        print("\nRECONCILE_FAIL: the census cannot see routes the app is serving.",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
