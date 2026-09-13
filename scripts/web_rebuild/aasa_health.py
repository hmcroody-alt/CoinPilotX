#!/usr/bin/env python3
"""Apple App Site Association health check — does the site hand off to the app?

**Why this is a Phase 0 gate.** The rebuilt website is required to promote the
iOS app throughout. The strongest form of that promotion is not a banner, it is
a *universal link*: a user taps `pulsesoc.com/pulse/post/812` in Messages and
the installed app opens on that post. That handoff is governed entirely by one
file, `/.well-known/apple-app-site-association`, and it fails silently in three
distinct ways that no browser test and no App Store review will catch:

1. **The endpoint stops serving.** `services/native_app_links.py` returns 503
   when `PULSESOC_APPLE_TEAM_ID` is unset. iOS caches the last good AASA for a
   while and then gives up; every universal link degrades to a web page with no
   error anywhere. A deploy that loses one env var kills every deep link in the
   product.
2. **The app claims a path the AASA does not.** `mobile-native/src/navigation/
   linking.ts` declares ~106 paths across 13 families. The served AASA claims
   two: `/pulse/*` and `/search*`. Every path in the other eleven families is a
   deep link the app knows how to open and will never be handed. The app is not
   broken and the site is not broken — the association between them is.
3. **The web rebuild invents a URL family.** A new `/explore` or `/watch`
   section is a URL the app has never heard of. That is fine, as long as it is
   a *decision*. This check makes it one: every family is either CLAIMED or
   WEB_ONLY with a stated reason, and an unclassified family fails.

The check runs against the live site (`--url`) or against the local builder
(default), so it works in CI before a deploy and as a probe after one.

Read-only. Makes one GET.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LINKING_TS = REPO / "mobile-native" / "src" / "navigation" / "linking.ts"

#: Every URL family the native app declares, and what the site does about it.
#:
#: CLAIMED   — the AASA must claim it. These are app-product surfaces; a link to
#:             one of them should open the app when the app is installed.
#: WEB_ONLY  — the AASA must NOT claim it, on purpose. These are the surfaces a
#:             user reaches *because the app is the problem*: the app won't open,
#:             the account is locked, they are reporting a scam, they are
#:             deciding whether to install at all. Capturing those into the app
#:             makes them unreachable exactly when they are needed. The native
#:             screens still exist and still work from in-app navigation and from
#:             the `pulsesoc://` scheme; only the https handoff is withheld.
DECISIONS = {
    "pulse": ("CLAIMED", "Every native object and workflow. The primary surface."),
    "search": ("CLAIMED", "Search results are the same objects as /pulse."),
    "dashboard": ("CLAIMED", "Creator/business dashboard. Nine declared screens."),
    "account": ("CLAIMED", "Account settings and security; /pulse/settings/* is already claimed, so withholding the alias would be inconsistent."),
    "settings": ("CLAIMED", "Resolved from the settings registry, not a static table."),
    "notifications": ("CLAIMED", "A notification deep link that opens the web page instead of the app is the single most visible failure of this file."),
    "saved": ("CLAIMED", "Top-level alias of /pulse/saved, which is claimed."),
    "education": ("CLAIMED", "Lesson detail; a course link should open the player."),
    "help": ("WEB_ONLY", "Support is what a user reaches when the app will not open. Handing it to the app closes the only door left."),
    "trust-center": ("WEB_ONLY", "Read before installing, and by people who are not users at all."),
    "security": ("WEB_ONLY", "Vulnerability reporting and the security posture page. Same argument as help."),
    "privacy-center": ("WEB_ONLY", "Must be readable without the app, including by regulators and by people deciding not to install."),
    "scam-shield": ("WEB_ONLY", "Reached from a suspicious link, often on a device where the app is compromised or absent."),
}


def declared_native_paths(text: str | None = None) -> dict[str, list[str]]:
    """Every path `linking.ts` declares, grouped by first segment.

    Two sources, because the file has two: the static `config.screens` tree, and
    `settingsDeepLink`, which resolves `settings/<id>` through the registry at
    runtime and therefore appears in no screen table.
    """
    text = text if text is not None else LINKING_TS.read_text()
    config = text.split("config:", 1)[1] if "config:" in text else text
    paths = set()
    for match in re.finditer(r'(?:path|[A-Za-z][A-Za-z0-9]*)\s*:\s*"([^"]+)"', config):
        value = match.group(1)
        if value.startswith(("pulsesoc:", "https:", "/")) or not value:
            continue
        paths.add(value)
    # Runtime-resolved, invisible to the screen table. See the docstring on
    # `settingsDeepLink` in linking.ts for why it lives outside the config.
    if "settingsDeepLink" in text:
        paths.add("settings/:id")

    families: dict[str, list[str]] = {}
    for path in sorted(paths):
        families.setdefault(path.split("/")[0], []).append(path)
    return families


def component_matches(pattern: str, path: str) -> bool:
    """Apple's `components[]["/"]` matching: `*` is any run, `?` is one char.

    Deliberately not `fnmatch`: `fnmatch`'s `*` does not cross a `/`, Apple's
    does. Using `fnmatch` here would report `/pulse/*` as failing to claim
    `/pulse/post/812`, which is the opposite of the truth.
    """
    regex = "".join(
        ".*" if ch == "*" else ("." if ch == "?" else re.escape(ch)) for ch in pattern
    )
    return re.fullmatch(regex, path) is not None


def component_lists(payload: dict) -> list[list[tuple[str, bool]]]:
    """One ordered (pattern, excluded) list per `details` entry -- per appID.

    Kept separate rather than concatenated. Each appID is matched independently,
    so flattening them puts the first app's patterns in front of the second's
    and lets one app's `exclude` suppress another app's claim. Today both
    entries share the same `APPLE_LINK_COMPONENTS` object and the answer is the
    same either way, which is precisely why the flattened version would survive
    review until the day the two lists differ.
    """
    lists = []
    for detail in payload.get("applinks", {}).get("details", []):
        entries = [
            (component["/"], bool(component.get("exclude")))
            for component in detail.get("components", [])
            if component.get("/")
        ]
        if entries:
            lists.append(entries)
    return lists


def ordered_components(payload: dict) -> list[tuple[str, bool]]:
    """The single ordered component list every associated app shares.

    Raises when the apps disagree, because past that point there is no one
    answer to "what does the site claim" and every caller here assumes there is.
    The production builder hands the same list to both bundle IDs.
    """
    lists = component_lists(payload)
    if not lists:
        return []
    if any(entries != lists[0] for entries in lists[1:]):
        raise ValueError(
            "associated apps declare different component lists; use "
            "component_lists() and decide which app you are asking about"
        )
    return lists[0]


def opens_in_app_anywhere(payload: dict, path: str) -> bool:
    """Does any associated app receive this URL?"""
    return any(opens_in_app(entries, path) for entries in component_lists(payload))


def opens_in_app(components: list[tuple[str, bool]], path: str) -> bool:
    """Does iOS hand this URL to the app? First match wins, exclusions included.

    iOS evaluates `components` top to bottom and stops at the first entry whose
    pattern matches; if that entry carries `"exclude": true` the URL goes to the
    browser. So a pattern's effect depends on everything above it, and an
    exclusion placed below the pattern it means to carve out is unreachable.

    `claimed_patterns` used to answer this by filtering the excluded entries out
    and matching against the rest. That inverts the answer for exactly the URLs
    an exclusion exists for: drop `/pulse/app (exclude)` from the list and
    `/pulse/*` matches it, so the checker reports the app receives a URL that
    iOS actually sends to Safari -- and reports it while looking at a correct
    file. The order has to be walked, not summarised.
    """
    for pattern, excluded in components:
        if component_matches(pattern, path):
            return not excluded
    return False


def claimed_patterns(payload: dict) -> list[str]:
    """Non-excluded patterns, order discarded.

    Kept for the shape check, which asks "does any pattern claim the whole
    site?" -- a question about the set, not about precedence. Anything asking
    whether a *specific URL* reaches the app must use `opens_in_app`.
    """
    return [pattern for pattern, excluded in ordered_components(payload) if not excluded]


def concrete_urls(declared: str) -> list[str]:
    """Turn a React Navigation path template into the URLs iOS will actually see.

    `pulse/safety/:section?` is two real URLs, not one — `/pulse/safety` and
    `/pulse/safety/privacy` — and an AASA pattern can easily claim the second
    and miss the first. Testing the template as a literal string would hide
    exactly that class of gap, which is the one this file exists to find.
    """
    segments = declared.strip("/").split("/")
    variants = [[]]
    for segment in segments:
        if segment.endswith("?"):
            # Optional: keep the shorter URL as a separate variant.
            variants = variants + [v + ["sample"] for v in variants]
        elif segment.startswith(":"):
            variants = [v + ["sample"] for v in variants]
        else:
            variants = [v + [segment] for v in variants]
    return sorted({"/" + "/".join(v) for v in variants if v})


def unclaimed_urls(paths: list[str], components: list[tuple[str, bool]]) -> list[str]:
    """Every concrete URL the app declares that iOS will not hand to the app.

    Takes ordered components rather than a flat pattern list so that a URL
    sitting under an `exclude` is reported as unclaimed, which is what iOS
    does with it.
    """
    missed = []
    for declared in paths:
        for url in concrete_urls(declared):
            if not opens_in_app(components, url):
                missed.append(url)
    return sorted(set(missed))


def fetch_live(url: str) -> tuple[dict | None, str]:
    target = url.rstrip("/") + "/.well-known/apple-app-site-association"
    try:
        request = urllib.request.Request(target, headers={"User-Agent": "PulseSoc-AASA-Health/1"})
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            content_type = response.headers.get("Content-Type", "")
            status = response.status
    except Exception as exc:
        return None, f"request failed: {exc}"
    if status != 200:
        return None, f"HTTP {status} — universal links are dead while this is true"
    if "json" not in content_type.lower():
        # Apple requires application/json and will not accept a file served as
        # text/plain. This is a real, and commonly hit, failure mode.
        return None, f"Content-Type is {content_type!r}, must be application/json"
    try:
        return json.loads(body), ""
    except Exception as exc:
        return None, f"body is not JSON: {exc}"


def build_local() -> tuple[dict | None, str]:
    sys.path.insert(0, str(REPO))
    os.environ.setdefault("PULSESOC_APPLE_TEAM_ID", "AAAAAAAAAA")
    from services.native_app_links import apple_app_site_association

    return apple_app_site_association()


def check_payload_shape(payload: dict) -> list[str]:
    """Structural faults that make the file valid JSON and useless to iOS."""
    problems = []
    applinks = payload.get("applinks")
    if not isinstance(applinks, dict):
        return ["no `applinks` object"]
    if "apps" not in applinks:
        problems.append("`applinks.apps` is missing; iOS requires the key even when empty")
    details = applinks.get("details") or []
    if not details:
        problems.append("`applinks.details` is empty — nothing is associated")
    for detail in details:
        app_id = detail.get("appID", "")
        if not re.fullmatch(r"[A-Z0-9]{10}\.[A-Za-z0-9.\-]+", app_id):
            problems.append(f"appID {app_id!r} is not <TeamID>.<BundleID>")
        if not detail.get("components"):
            problems.append(f"{app_id}: no components — claims nothing")
    for pattern in claimed_patterns(payload):
        if pattern in {"/", "/*", "*"}:
            problems.append(
                f"pattern {pattern!r} claims the entire site: no web page would "
                "ever open in a browser for a user with the app installed"
            )
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--url", help="Check the live site, e.g. https://pulsesoc.com")
    parser.add_argument("--json", action="store_true", help="Machine-readable output")
    args = parser.parse_args()

    if args.url:
        payload, error = fetch_live(args.url)
        source = args.url
    else:
        payload, error = build_local()
        source = "services/native_app_links.py"

    if payload is None:
        print(f"FAIL  {source}: {error}")
        return 1

    problems = check_payload_shape(payload)
    components = ordered_components(payload)
    families = declared_native_paths()

    rows = []
    for family, paths in sorted(families.items()):
        decision, reason = DECISIONS.get(family, (None, ""))
        missed = unclaimed_urls(paths, components)
        total = sum(len(concrete_urls(p)) for p in paths)
        if decision is None:
            status = "UNCLASSIFIED"
            problems.append(
                f"`{family}` is declared in linking.ts but has no entry in "
                f"DECISIONS. Decide whether the app should receive it, then say "
                f"so — silence here is how a deep link family goes missing."
            )
        elif decision == "CLAIMED" and missed:
            status = f"{len(missed)}/{total} MISSED"
            sample = ", ".join(missed[:4]) + (" …" if len(missed) > 4 else "")
            problems.append(
                f"`{family}` is CLAIMED but {len(missed)} of its {total} URLs "
                f"are not matched by any component — those links open the "
                f"website instead of the app: {sample}"
            )
        elif decision == "WEB_ONLY" and len(missed) < total:
            status = "OVER-CLAIMED"
            problems.append(
                f"`{family}` is WEB_ONLY ({reason}) but the AASA claims "
                f"{total - len(missed)} of its {total} URLs"
            )
        else:
            status = "ok"
        rows.append({"family": family, "urls": total, "unclaimed": missed,
                     "decision": decision, "status": status})

    # Rendered in file order, not sorted: the order is the configuration. A
    # sorted list would print an exclusion that iOS never reaches identically
    # to one that works.
    listing = [f"NOT {pattern}" if excluded else pattern for pattern, excluded in components]

    if args.json:
        print(json.dumps({"source": source, "components": listing,
                          "families": rows, "problems": problems}, indent=2))
        return 1 if problems else 0

    print(f"AASA source: {source}")
    print(f"claims (in order): {', '.join(listing) or '(nothing)'}\n")
    print(f"{'family':16} {'urls':>5} {'missed':>7}  {'decision':10} status")
    for row in rows:
        print(f"{row['family']:16} {row['urls']:5} {len(row['unclaimed']):7}  "
              f"{str(row['decision'] or '-'):10} {row['status']}")

    if problems:
        print(f"\n{len(problems)} problem(s):")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("\nOK — every declared family is either claimed or deliberately web-only.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
