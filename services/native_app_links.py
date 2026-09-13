"""Validated payload builders for Apple Universal Links and Android App Links."""

from __future__ import annotations

import os
import re


APPLE_TEAM_ID_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
ANDROID_FINGERPRINT_PATTERN = re.compile(r"^(?:[A-F0-9]{2}:){31}[A-F0-9]{2}$")
BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")


#: The paths iOS will hand to the app instead of to Safari.
#:
#: These mirror `mobile-native/src/navigation/linking.ts`, which declares ~106
#: paths across 13 families. Anything declared there and absent here is a deep
#: link the app knows how to open and will never be given — the app is not
#: broken and the site is not broken, the association between them is. That
#: divergence is checked by `scripts/web_rebuild/aasa_health.py` and locked by
#: `tests/web_parity/test_aasa_claims.py`; do not edit this list without running
#: them, because nothing else in the system will notice.
#:
#: **`/pulse` and `/pulse/*` are two entries on purpose.** Apple's `*` matches a
#: run of characters, but the literal `/` before it still has to be there, so
#: `/pulse/*` does not match `/pulse` — the Home tab, and the most-shared URL in
#: the product. `/search*` has no such problem because it has no slash. A
#: one-character difference between the two spellings decides whether the app's
#: front door opens.
#:
#: **What is deliberately left out.** `/help`, `/trust-center`, `/security`,
#: `/privacy-center` and `/scam-shield` all have native screens and are all
#: withheld. They are the surfaces a person reaches *because* the app is the
#: problem: it will not open, the account is locked, they are checking whether a
#: link is a scam, or they have not installed anything and are deciding whether
#: to. Handing those to the app closes the last door at the moment it is needed.
#: The screens still work from in-app navigation and from `pulsesoc://`; only the
#: https handoff is withheld.
APPLE_LINK_COMPONENTS = [
    {"/": "/pulse", "comment": "PulseSoc home"},
    {"/": "/pulse/*", "comment": "PulseSoc native objects and workflows"},
    {"/": "/search*", "comment": "PulseSoc search"},
    {"/": "/dashboard", "comment": "Creator and business dashboard"},
    {"/": "/dashboard/*", "comment": "Dashboard modules, orders, account"},
    {"/": "/account/*", "comment": "Account settings and security"},
    {"/": "/settings/*", "comment": "Settings destinations resolved from the registry"},
    {"/": "/notifications", "comment": "Notification centre"},
    {"/": "/saved", "comment": "Saved items"},
    {"/": "/education/*", "comment": "Lessons and courses"},
]


def _csv(name: str, default: str = "") -> list[str]:
    return [part.strip() for part in os.getenv(name, default).split(",") if part.strip()]


def apple_app_site_association() -> tuple[dict | None, str]:
    team_id = os.getenv("PULSESOC_APPLE_TEAM_ID", "").strip().upper()
    if not APPLE_TEAM_ID_PATTERN.fullmatch(team_id):
        return None, "PULSESOC_APPLE_TEAM_ID must be a 10-character Apple Team ID."

    bundle_ids = _csv(
        "PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS",
        "com.pulsesoc.app,com.pulsesoc.nativeapp.dev",
    )
    bundle_ids = [bundle_id for bundle_id in bundle_ids if BUNDLE_ID_PATTERN.fullmatch(bundle_id)]
    if not bundle_ids:
        return None, "At least one valid Apple bundle ID is required."

    components = APPLE_LINK_COMPONENTS
    return {
        "applinks": {
            "apps": [],
            "details": [
                {"appID": f"{team_id}.{bundle_id}", "components": components}
                for bundle_id in bundle_ids
            ],
        }
    }, ""


def android_asset_links() -> tuple[list[dict] | None, str]:
    fingerprints = [
        value.upper()
        for value in _csv("PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS")
        if ANDROID_FINGERPRINT_PATTERN.fullmatch(value.upper())
    ]
    if not fingerprints:
        return None, "PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS must contain a valid SHA-256 certificate fingerprint."

    package_names = _csv(
        "PULSESOC_ANDROID_ASSOCIATED_PACKAGES",
        "com.pulsesoc.app,com.pulsesoc.nativeapp",
    )
    package_names = [name for name in package_names if BUNDLE_ID_PATTERN.fullmatch(name)]
    if not package_names:
        return None, "At least one valid Android package name is required."

    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": package_name,
                "sha256_cert_fingerprints": fingerprints,
            },
        }
        for package_name in package_names
    ], ""
