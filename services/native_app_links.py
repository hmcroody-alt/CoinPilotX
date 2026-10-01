"""Validated payload builders for Apple Universal Links and Android App Links."""

from __future__ import annotations

import os
import re


APPLE_TEAM_ID_PATTERN = re.compile(r"^[A-Z0-9]{10}$")
ANDROID_FINGERPRINT_PATTERN = re.compile(r"^(?:[A-F0-9]{2}:){31}[A-F0-9]{2}$")
BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9.-]+$")


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

    components = [
        {"/": "/pulse/*", "comment": "PulseSoc native objects and workflows"},
        {"/": "/search*", "comment": "PulseSoc search"},
    ]
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
