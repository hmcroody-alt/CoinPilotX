#!/usr/bin/env python3
"""Join the native and web inventories into a parity matrix.

The join is exact rather than impressionistic because both sides speak the same
language: ``linking.ts`` declares ``https://pulsesoc.com`` as a universal-link
prefix, so every native deep-link path is literally a URL the Flask app is
expected to answer. A native path and a Flask rule match when their segments
match one-for-one, treating ``:param`` (native) and ``<converter:name>`` (Flask)
as wildcards.

Classification per native destination:

``PARITY``            a web rule matches and it renders HTML.
``API_ONLY``          a web rule matches but only returns JSON — a shared link
                      lands on data, not a page.
``REDIRECT_ONLY``     a web rule matches but only redirects.
``MISSING``           no web rule matches the path at all.

``MISSING`` is the number that matters: a native share sheet can emit that URL
and the website has nothing to serve.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
HERE = pathlib.Path(__file__).resolve().parent

HTML_HELPERS = {
    "pulse_social_shell", "admin_page_html", "arena_page_shell", "render_account_page",
    "arena_simple_page", "render_seo_landing", "pulse_page_html", "dashboard_network_shell",
    "dashboard_creator_shell", "pulse_security_settings_page", "trust_public_page",
    "dashboard_account_shell", "render_ads_landing_page", "education_shell",
    "education_feature_page", "dashboard_creator_subsystem_page", "render_template",
    "_verification_admin_shell", "search_pages", "pulse_gateway_card_html",
}

FLASK_PARAM = re.compile(r"<[^>]+>")


def run(script: str) -> object:
    result = subprocess.run(
        [sys.executable, str(HERE / script)],
        capture_output=True, text=True, check=True,
    )
    return json.loads(result.stdout)


def segments(path: str) -> list[str]:
    return [s for s in path.strip("/").split("/") if s]


def normalize_native(path: str) -> list[str]:
    out = []
    for seg in segments(path.split("?")[0].split("#")[0]):
        out.append("*" if seg.startswith(":") else seg)
    return out


def normalize_web(rule: str) -> list[str]:
    return ["*" if FLASK_PARAM.fullmatch(seg) else seg for seg in segments(rule)]


def matches(native: list[str], web: list[str]) -> bool:
    """Web rule answers the native path.

    A native optional segment (``:id?``) is normalised to a wildcard, so a
    native path may legitimately be one segment longer or shorter than the
    rule; require equal length here and let the caller try both forms.
    """
    if len(native) != len(web):
        return False
    return all(n == "*" or w == "*" or n == w for n, w in zip(native, web))


def classify(web_rows: list[dict]) -> str:
    if any(r["templates"] or (set(r["calls"]) & HTML_HELPERS) for r in web_rows):
        return "PARITY"
    if any(r["jsonify"] for r in web_rows):
        return "API_ONLY"
    if any(r["redirects"] for r in web_rows):
        return "REDIRECT_ONLY"
    return "PARITY"  # renders something else (Response/string)


def main() -> int:
    native = run("extract_native_surfaces.py")
    web = run("extract_web_routes.py")

    # Existence is judged against the booted url_map when one is supplied,
    # because ~240 rules come from blueprints that static analysis cannot see.
    # Render behaviour still comes from the AST rows: url_map knows the rule
    # exists but not whether the handler returns a page or JSON.
    url_map: set[tuple[str, ...]] = set()
    if "--url-map" in sys.argv:
        path = pathlib.Path(sys.argv[sys.argv.index("--url-map") + 1])
        url_map = {tuple(normalize_web(r)) for r in json.loads(path.read_text())}

    by_norm: dict[tuple[str, ...], list[dict]] = {}
    for row in web:
        by_norm.setdefault(tuple(normalize_web(row["rule"])), []).append(row)

    def lookup(path: str) -> tuple[str, list[dict]]:
        base = normalize_native(path)
        candidates = [base]
        if base and base[-1] == "*":
            candidates.append(base[:-1])
        for cand in candidates:
            for key, rows in by_norm.items():
                if matches(cand, list(key)):
                    return classify(rows), rows
        for cand in candidates:
            if any(matches(cand, list(key)) for key in url_map):
                # Registered by a blueprint; rule exists but its handler body
                # was not statically reachable, so render behaviour is unknown.
                return "BLUEPRINT_UNVERIFIED", []
        return "MISSING", []

    matrix: list[dict] = []

    for label, route, status, section in (
        (a["label"], a["route"], a["status"], a["section"]) for a in native["master_navigation"]
    ):
        verdict, rows = lookup(route)
        matrix.append({
            "source": "master_navigation",
            "id": label,
            "section": section,
            "native_route": route,
            "native_status": status,
            "web_rules": sorted({r["rule"] for r in rows}),
            "web_handlers": sorted({r["handler"] for r in rows}),
            "parity": verdict,
        })

    for screen, path in sorted(native["links"].items()):
        verdict, rows = lookup(path)
        matrix.append({
            "source": "deep_link",
            "id": screen,
            "section": "",
            "native_route": "/" + path,
            "native_status": "native",
            "web_rules": sorted({r["rule"] for r in rows}),
            "web_handlers": sorted({r["handler"] for r in rows}),
            "parity": verdict,
        })

    json.dump({"matrix": matrix}, sys.stdout, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
