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
import re
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web_native_census as census  # noqa: E402

REPO = census.REPO

# Registered by Flask itself, not by any line of application code, so no source
# scan can or should find it.
FRAMEWORK_RULES = {"/static/<path:filename>"}

# Deep links that currently 404 on the web. Lower this as gaps close; the gate
# fails if it rises. Measured 2026-09-08 against the live url_map.
BROKEN_DEEP_LINK_BUDGET = 42


def boot_app():
    """Boot the app against a throwaway database and hand back the Flask object."""
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
    return bot.webhook_app


def live_rules(app) -> set[str]:
    return {rule.rule for rule in app.url_map.iter_rules()}


# React Navigation writes `:param` and `:param?`; Werkzeug needs a concrete URL
# to match. An optional segment is tested in its *present* form because that is
# the shape a shared link actually carries.
PARAM = re.compile(r':([A-Za-z0-9_]+)\??')


def _concrete(path: str) -> str:
    return PARAM.sub(
        lambda m: "1" if m.group(1).lower().endswith("id") else "x", path)


def deep_link_status(app) -> tuple[list, list]:
    """Resolve every `linking.ts` path against the live routing table.

    This is the check that matters most to a user, and the destination matrix
    cannot make it. `linking.ts` declares `https://pulsesoc.com` as a universal
    link prefix, so every path in it is a URL the app will happily put on the
    clipboard. If Flask has no rule for it, sharing that screen produces a 404
    for the recipient -- and for the sender's own browser.

    Matching is done by Werkzeug's own matcher rather than by string equality so
    that parameterised routes are judged the way the server judges them. A
    NotFound here means no rule exists at all; it is not an auth or data
    outcome, so it cannot be explained away as "you had to be logged in".
    """
    adapter = app.url_map.bind("pulsesoc.com")
    links = census.parse_linking(census._read(census.LINKING))
    seen: set[str] = set()
    resolved, broken = [], []
    for link in links:
        if link.path in seen:
            continue
        seen.add(link.path)
        try:
            adapter.match(_concrete(link.path), method="GET")
        except Exception:
            broken.append(link)
        else:
            resolved.append(link)
    return resolved, broken


DEEP_LINK_DOC = os.path.join(REPO, "PULSESOC_DEEPLINK_PARITY.md")


def write_deep_link_doc(resolved: list, broken: list) -> str:
    """Record the share-link gap as a reviewable artifact.

    Kept separate from the census documents because it can only be produced by
    booting the app: it is a statement about the routing table Flask actually
    built, not about what the source appears to declare.
    """
    total = len(resolved) + len(broken)
    lines = [
        "# PulseSoc deep-link parity (native -> web)",
        "",
        "GENERATED by `scripts/parity/reconcile_urlmap.py` — do not hand-edit.",
        "",
        "`mobile-native/src/navigation/linking.ts` registers `https://pulsesoc.com`",
        "as a universal-link prefix, so every path below is a URL the app can put on",
        "a user's clipboard. Each one is resolved against the *live* Flask url_map",
        "using Werkzeug's own matcher, so a BROKEN row means no rule exists at all —",
        "not that the visitor was signed out.",
        "",
        f"- Deep-link paths: **{total}**",
        f"- Resolve on the web: **{len(resolved)}**",
        f"- Return 404 on the web: **{len(broken)}**",
        "",
        "## Broken share links",
        "",
        "| Path | Native screen |",
        "|---|---|",
    ]
    lines += [f"| `{link.path}` | {link.screen} |" for link in broken]
    lines += ["", "## Resolving", "", "| Path | Native screen |", "|---|---|"]
    lines += [f"| `{link.path}` | {link.screen} |" for link in resolved]
    with open(DEEP_LINK_DOC, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")
    return os.path.basename(DEEP_LINK_DOC)


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

    app = boot_app()
    live = live_rules(app) - FRAMEWORK_RULES
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

    resolved, broken = deep_link_status(app)
    print(f"\nnative deep links       : {len(resolved) + len(broken)}")
    print(f"  resolve on the web    : {len(resolved)}")
    print(f"  404 on the web        : {len(broken)}")
    if broken:
        print("\n-- shareable app URLs with no web route (broken share links):")
        for link in broken:
            print(f"   {link.path:52} {link.screen}")

    if not args.check:
        print("\nwrote " + write_deep_link_doc(resolved, broken))

    failures = []
    if unseen:
        failures.append("the census cannot see routes the app is serving.")
    # A ratchet, not a target. The gap is a product fact today, so failing on
    # any breakage would just mean a permanently red gate that everyone learns
    # to ignore. Failing when it *grows* keeps new 404-able links from being
    # added while the existing ones are worked off, and the number has to be
    # lowered by hand as they close -- which is the point.
    if len(broken) > BROKEN_DEEP_LINK_BUDGET:
        failures.append(
            f"{len(broken)} deep links 404 on the web, above the recorded "
            f"budget of {BROKEN_DEEP_LINK_BUDGET}. A new app URL was published "
            "that the site cannot serve.")

    if args.check and failures:
        for failure in failures:
            print(f"\nRECONCILE_FAIL: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
