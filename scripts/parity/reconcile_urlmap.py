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
# fails if it rises. Measured 2026-09-08 against the live url_map, after the
# Private Office web surface took ten of them, orders/Pages/Account Health took
# seven more, the Activity inbox took four, Seller Store, Presence and
# Start-a-chat took three, and the dashboard-module router took one.
#
# One of the two left -- `/pulse/calls/:callId?` -- is not a gap to close. A web
# call surface would be a second real-time audio publication path, which
# `docs/realtime_audio_change_policy.md` forbids outright, so this number cannot
# honestly reach zero by building. It is BLOCKED, not pending; the floor is 1.
#
# The other is pending work: `/pulse/undx/actions` needs a client that can
# render six lists at once and can tell "the feature is switched off" apart from
# "you have nothing waiting" -- a fourth state, since the subsystem sits behind
# an env flag and 404s when it is off. Flattening the response server-side for
# the browser would be a web-only backend authority, so it waits for a real
# client capability rather than a workaround.
BROKEN_DEEP_LINK_BUDGET = 2


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


def _base_path(path: str) -> str:
    """The path up to its first parameter, e.g. /pulse/orders/:id -> /pulse/orders."""
    head = path.split("/:", 1)[0]
    return head.rstrip("/") or "/"


#: The whole app, not just the navigation folder. Scanning only the two routing
#: files misses where most values are actually minted: the camera's five modes
#: live on `providerRoute` fields in CameraStudioScreen, and dashboard module
#: lists carry `route:` targets. A narrow scan is not merely incomplete, it is
#: unsound in the dangerous direction -- the verdict below clears a row when
#: every value found resolves, so a value the scan cannot see is a value that
#: cannot contribute its 404, and the row clears on a subset.
NATIVE_LITERAL_ROOT = os.path.join("mobile-native", "src")
NATIVE_LITERAL_EXCLUDED_DIRS = ("__tests__", "__mocks__", "node_modules")

#: A quoted absolute path, with any query string or fragment dropped the same
#: way the app drops them (`notificationRouting.ts` does
#: `.split("?")[0].split("#")[0]`). Without this, `"/pulse/camera/photo?target=
#: feed"` matches nothing at all and a real, produced value is silently lost.
NATIVE_LITERAL = re.compile(r'"(/[A-Za-z0-9][A-Za-z0-9/_-]*)(?:[?#][^"\s]*)?"')

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
#: `(?<!:)` so the `//` in a `https://` literal is not read as a comment.
_LINE_COMMENT = re.compile(r"(?<!:)//[^\n]*")

#: Known-good samples. Each must survive the scan or the extractor has quietly
#: stopped matching something it used to match -- a failure that produces no
#: error and no wrong number, just a census that looks more uncertain than it is.
NATIVE_LITERAL_CANARIES = ("/scam-shield/scan", "/pulse/camera/photo",
                           "/pulse/settings/devices")


def native_literal_paths() -> set[str]:
    """Every concrete URL path spelled out anywhere in the app's own source.

    These are real values, not invented ones: they are paths the app itself
    navigates to or matches on. Used to answer "does this hub-only row mean a
    broken share link, or did we only fail a made-up test value".

    Comments are stripped first. `notificationRouting.ts` explains itself with
    the example `"/pulse/foo/123?token=x"`, and an illustration in prose is not
    a route the app can produce.
    """
    root = os.path.join(REPO, NATIVE_LITERAL_ROOT)
    if not os.path.isdir(root):
        raise SystemExit(
            f"{NATIVE_LITERAL_ROOT} is gone; the hub-only rows are classified "
            "from the literal paths under it, so that classification would "
            "silently weaken to 'unproven' for every row.")
    found: set[str] = set()
    for folder, subdirs, names in os.walk(root):
        subdirs[:] = [d for d in subdirs if d not in NATIVE_LITERAL_EXCLUDED_DIRS]
        for name in names:
            if not name.endswith((".ts", ".tsx")):
                continue
            text = census._read(os.path.join(folder, name))
            text = _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))
            found |= set(NATIVE_LITERAL.findall(text))
    missing = [c for c in NATIVE_LITERAL_CANARIES if c not in found]
    if missing:
        raise SystemExit(
            "these known paths are no longer extracted from "
            f"{NATIVE_LITERAL_ROOT}: {missing} — the extractor has stopped "
            "matching rather than the app having stopped routing.")
    return found


def native_sample_values(link_path: str, every_link_path: set[str],
                         literals: set[str]) -> list[str]:
    """Real single-segment values the app can produce for one parameterised link.

    A literal sitting under the same base is *not* automatically a value for the
    parameter. `/pulse/marketplace/create` is its own registered deep link
    (MarketplaceCreateGateway) — a sibling route, not a listing id. Counting it
    would have "proved" the marketplace row fine while sharing a listing still
    404s, which is the exact false clearance this whole census exists to stop.
    So anything that is itself a declared deep-link path is excluded.
    """
    base = _base_path(link_path)
    if base == link_path or len(PARAM.findall(link_path)) != 1:
        return []
    prefix = base.rstrip("/") + "/"
    values = []
    for literal in literals:
        if not literal.startswith(prefix):
            continue
        tail = literal[len(prefix):]
        if not tail or "/" in tail:
            continue
        if literal in every_link_path:
            continue
        values.append(tail)
    return sorted(values)


def deep_link_status(app) -> tuple[list, list, list]:
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

    Three outcomes, because two would lie. A parameterised link is tested with a
    dummy value, and the web often enumerates its valid values instead of taking
    a wildcard: `/pulse/settings/:section` fails on a made-up section while
    `/pulse/settings/security` and seven siblings are served. Calling that
    BROKEN would have sent someone to rebuild a settings router that exists.

    So when the dummy fails, the parameterless base is tried:

      HUB_ONLY  the hub is served but item links are not -- /pulse/marketplace
                works, /pulse/marketplace/<listing> does not, so sharing a
                specific listing 404s while browsing is fine.
      BROKEN    neither form resolves; the feature has no web surface at all.
    """
    adapter = app.url_map.bind("pulsesoc.com")
    links = census.parse_linking(census._read(census.LINKING))

    def resolves(path: str) -> bool:
        try:
            adapter.match(path, method="GET")
        except Exception:
            return False
        return True

    seen: set[str] = set()
    resolved, hub_only, broken = [], [], []
    for link in links:
        if link.path in seen:
            continue
        seen.add(link.path)
        if resolves(_concrete(link.path)):
            resolved.append(link)
            continue
        base = _base_path(link.path)
        if base != link.path and resolves(base):
            hub_only.append(link)
        else:
            broken.append(link)
    return resolved, hub_only, broken


def classify_hub_only(app, hub_only: list, every_link_path: set[str]) -> dict:
    """Re-test each hub-only row with values the app can really produce.

    HUB_ONLY is assigned when a *dummy* parameter fails, which conflates two
    different things:

      the web enumerates its valid values, and only the invented one 404s
      the web genuinely has no detail route, and sharing an item is broken

    Telling them apart was prose in this document ("check before building") and
    prose does not get re-checked when the routing table moves. Where the app's
    own sources spell out real values, they are probed instead.

    Returns path -> (verdict, [(value, resolved)]). `unproven` is deliberately
    not `fine`: it means no real value could be derived, so the row still needs
    a human.
    """
    adapter = app.url_map.bind("pulsesoc.com")
    literals = native_literal_paths()

    def resolves(path: str) -> bool:
        try:
            adapter.match(path, method="GET")
        except Exception:
            return False
        return True

    verdicts = {}
    for link in hub_only:
        values = native_sample_values(link.path, every_link_path, literals)
        if not values:
            verdicts[link.path] = ("unproven", [])
            continue
        base = _base_path(link.path).rstrip("/")
        probed = [(value, resolves(f"{base}/{value}")) for value in values]
        verdicts[link.path] = (
            "enumerated" if all(ok for _, ok in probed) else "gap", probed)
    return verdicts


DEEP_LINK_DOC = os.path.join(REPO, "PULSESOC_DEEPLINK_PARITY.md")

# Broken links that must never be built, and why.
#
# The generated table cannot tell "nobody has got to this yet" apart from "this
# is forbidden", and it prints them as identical rows. That is not a cosmetic
# problem: a reader working the list down would eventually reach the call link
# and build a web call surface, which is exactly the change
# `docs/realtime_audio_change_policy.md` forbids outright. The artifact has to
# say so itself, because the artifact is what gets read.
#
# These never move a path out of the broken count. A blocked gap is still a gap
# a member hits, and hiding it in the total would be faking parity -- the whole
# point of marking BLOCKED is that it is honest about a thing that stays broken.
BLOCKED_DEEP_LINKS = {
    "/pulse/calls/:callId?": (
        "BLOCKED, not pending — do not build. A web call surface would be a "
        "second real-time audio publication path, which "
        "`docs/realtime_audio_change_policy.md` forbids regardless of "
        "justification. This is why the broken-link budget cannot honestly "
        "reach zero: its floor is 1."),
}


#: Hub-only rows this script cannot clear on its own, cleared elsewhere by a
#: test that re-checks the claim. The scraper's evidence is concrete paths, so a
#: parameter whose values are a *vocabulary* rather than paths is invisible to
#: it and lands in `unproven` — correct, but it would leave a resolved row
#: reading like an open question forever. An entry here is not an assertion that
#: the row is fine; it is a pointer to the thing that keeps checking.
PROVEN_ELSEWHERE = {
    "/pulse/private-office/:view": (
        "tests/web_parity/test_private_office_views.py",
        "`:view` takes the six-entry RECORD_VIEWS vocabulary, not a path. The "
        "web serves all six via an `any(...)` enumeration that the test "
        "compares member-for-member against the app's own list."),
    "/dashboard/:legacyGroup/:legacyModule/:legacySubmodule?": (
        "tests/web_parity/test_dashboard_legacy_aliases.py",
        "Two parameters, so single-segment sampling declines it rather than "
        "guessing. The values are still derivable, just not by scraping paths: "
        "native resolves these through `findLegacyDashboardAlias`, so the set "
        "of URLs it answers is the product of `DASHBOARD_LEGACY_GROUPS` and "
        "each group's module aliases. The test enumerates all 135 and probes "
        "the responses — which found two groups the web had never served."),
}


def write_deep_link_doc(resolved: list, hub_only: list, broken: list,
                        verdicts: dict) -> str:
    """Record the share-link gap as a reviewable artifact.

    Kept separate from the census documents because it can only be produced by
    booting the app: it is a statement about the routing table Flask actually
    built, not about what the source appears to declare.
    """
    total = len(resolved) + len(hub_only) + len(broken)
    # A rationale for a link the app no longer publishes reads as a live policy
    # decision about a live route. Fail rather than carry it.
    orphaned = sorted(set(BLOCKED_DEEP_LINKS) - {link.path for link in broken})
    if orphaned:
        raise SystemExit(
            "BLOCKED_DEEP_LINKS names paths that are no longer broken links: "
            + ", ".join(orphaned) + " — delete the entry, or fix the path. If "
            "one of these now resolves, check it was not closed by building the "
            "thing the entry forbids.")
    blocked = [link for link in broken if link.path in BLOCKED_DEEP_LINKS]
    pending = [link for link in broken if link.path not in BLOCKED_DEEP_LINKS]
    enumerated = [l for l in hub_only
                  if verdicts.get(l.path, ("unproven",))[0] == "enumerated"]
    still_open = [l for l in hub_only if l not in enumerated]

    # The same reasoning, for a pointer at a proof instead of a prohibition —
    # with one extra condition. A pointer may only silence `unproven`, never a
    # `gap`: if this script probed a value the app really produces and the web
    # 404ed on it, an entry here would be hiding a concrete failure behind a
    # test that was written when the row still looked fine. That is the false
    # clearance the whole sampling rule exists to prevent, so it fails loudly.
    unproven = {l.path for l in still_open
                if verdicts.get(l.path, ("unproven",))[0] == "unproven"}
    for path, (test, _why) in sorted(PROVEN_ELSEWHERE.items()):
        if path not in unproven:
            raise SystemExit(
                "PROVEN_ELSEWHERE names " + path + ", which is no longer an "
                "unproven hub-only link. Either it resolves outright now and the "
                "entry is dead weight, or this script found a real value that "
                "404s on it — in which case the entry would be hiding a gap it "
                "was never written to cover. Delete it, or re-check " + test + ".")
        if not os.path.exists(os.path.join(REPO, test)):
            raise SystemExit(
                "PROVEN_ELSEWHERE points " + path + " at " + test + ", which "
                "does not exist. The row would read as cleared with nothing left "
                "doing the checking.")
    proven = [l for l in still_open if l.path in PROVEN_ELSEWHERE]
    still_open = [l for l in still_open if l.path not in PROVEN_ELSEWHERE]
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
        f"- Hub served, item links 404: **{len(still_open)}** "
        f"({sum(1 for l in still_open if verdicts.get(l.path, ('unproven',))[0] == 'gap')}"
        f" confirmed, "
        f"{sum(1 for l in still_open if verdicts.get(l.path, ('unproven',))[0] == 'unproven')}"
        f" unproven)",
        f"- Hub served, all real values resolve: **{len(enumerated)}**",
        f"- Hub served, cleared by a test elsewhere: **{len(proven)}**",
        f"- No web surface at all: **{len(broken)}** "
        f"({len(pending)} pending, {len(blocked)} blocked by policy)",
        "",
        "## Broken share links — pending work",
        "",
        "| Path | Native screen |",
        "|---|---|",
    ]
    lines += [f"| `{link.path}` | {link.screen} |" for link in pending]
    lines += [
        "",
        "## Broken share links — BLOCKED, do not build",
        "",
        "Still broken, and still counted above: a member following one of these "
        "gets a 404. They are listed apart because closing them is forbidden, so "
        "nobody should pick them up off the pending list.",
        "",
        "| Path | Native screen | Why |",
        "|---|---|---|",
    ]
    lines += [f"| `{link.path}` | {link.screen} | {BLOCKED_DEEP_LINKS[link.path]} |"
              for link in blocked]
    def evidence(link) -> str:
        probed = verdicts.get(link.path, ("unproven", []))[1]
        if not probed:
            return "no real value derivable from the app's sources"
        return ", ".join(f"`{v}` {'ok' if ok else '**404**'}" for v, ok in probed)

    lines += [
        "",
        "## Hub served — and every value found in the app's source resolves",
        "",
        "These land in the hub-only bucket only because the pattern is probed with "
        "an invented parameter. Re-probed with the concrete paths spelled out in "
        "`mobile-native/src`, they pass. The web often enumerates its values as "
        "separate routes rather than taking a wildcard — `/pulse/settings/account` "
        "is its own Flask rule, not a `<section>` match — which is exactly why a "
        "made-up value proves nothing here.",
        "",
        "This is evidence of no gap, not proof of none: a scan can only speak for "
        "values it can see spelled out. A value the app computes at runtime, or "
        "one that only ever arrives from the server, would not appear above. Treat "
        "these as \"no reason to build a detail route\", not as \"verified "
        "complete\".",
        "",
        "| Path | Native screen | Values probed |",
        "|---|---|---|",
    ]
    lines += [f"| `{link.path}` | {link.screen} | {evidence(link)} |"
              for link in enumerated]
    lines += [
        "",
        "## Hub served — cleared by a test, not by this script",
        "",
        "This script's evidence is concrete paths scraped from the app's sources, "
        "so a parameter whose values are a *vocabulary* rather than paths is "
        "structurally invisible to it and lands in `unproven` — correctly, but "
        "permanently. These rows were resolved by comparing the two authorities "
        "directly instead. The named test is what keeps the answer true; it is "
        "run by the normal suite, and this document fails to generate if the file "
        "is gone or if the row stops being unproven.",
        "",
        "| Path | Native screen | Proof | Why this script cannot say |",
        "|---|---|---|---|",
    ]
    lines += [f"| `{link.path}` | {link.screen} | `{PROVEN_ELSEWHERE[link.path][0]}` "
              f"| {PROVEN_ELSEWHERE[link.path][1]} |" for link in proven]
    lines += [
        "",
        "## Hub served, deep links into it 404",
        "",
        "Browsing works; sharing a specific item does not. A row with values probed "
        "is a confirmed gap: the app produces that value and the web 404s on it. A "
        "row with none is unproven — the parameter is an id or a value no source "
        "spells out, so it still needs a human before anyone builds anything.",
        "",
        "| Path | Native screen | Values probed |",
        "|---|---|---|",
    ]
    lines += [f"| `{link.path}` | {link.screen} | {evidence(link)} |"
              for link in still_open]
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

    resolved, hub_only, broken = deep_link_status(app)
    print(f"\nnative deep links       : {len(resolved) + len(hub_only) + len(broken)}")
    print(f"  resolve on the web    : {len(resolved)}")
    print(f"  hub only (item 404s)  : {len(hub_only)}")
    print(f"  no web surface at all : {len(broken)}")
    if broken:
        print("\n-- shareable app URLs with no web route at all:")
        for link in broken:
            mark = " [BLOCKED]" if link.path in BLOCKED_DEEP_LINKS else ""
            print(f"   {link.path:52} {link.screen}{mark}")
    every_link_path = {link.path for link in resolved + hub_only + broken}
    verdicts = classify_hub_only(app, hub_only, every_link_path)
    if hub_only:
        print("\n-- hub served, deep links into it 404:")
        for link in hub_only:
            verdict, probed = verdicts.get(link.path, ("unproven", []))
            detail = ",".join(f"{v}={'ok' if ok else '404'}" for v, ok in probed)
            if not detail and link.path in PROVEN_ELSEWHERE:
                detail = "proven in " + PROVEN_ELSEWHERE[link.path][0]
            print(f"   {link.path:52} {link.screen:24} [{verdict}]"
                  + (f" {detail}" if detail else ""))

    if not args.check:
        print("\nwrote " + write_deep_link_doc(resolved, hub_only, broken, verdicts))

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
