#!/usr/bin/env python3
"""Audit native APNs/FCM push credential readiness without exposing secrets."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.native_push_readiness import native_push_readiness  # noqa: E402


def _tracked_p8_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        return ["git-ls-files-unavailable"]
    return [line for line in result.stdout.splitlines() if line.endswith(".p8")]


def _summarize(result: dict, tracked_p8: list[str]) -> dict:
    return {
        "apns": result["apns"],
        "fcm": result["fcm"],
        "tracked_p8_file_count": len(tracked_p8),
        "tracked_p8_files_present": bool(tracked_p8),
        "ready": bool(result["ready"] and not tracked_p8),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allow-missing-runtime-env",
        action="store_true",
        help="Do not fail when runtime credentials are absent in the local shell.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print sanitized JSON instead of a short text summary.",
    )
    args = parser.parse_args()

    result = native_push_readiness()
    tracked_p8 = _tracked_p8_files()
    summary = _summarize(result, tracked_p8)

    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print("push credentials readiness audit")
        print(f"APNs provider initializable safely: {summary['apns']['apns_provider_initializable_safely']}")
        print(f"APNs bundle id expected: {summary['apns']['apns_bundle_id_expected']}")
        print(f"Firebase Admin initializes safely: {summary['fcm']['firebase_admin_initializes_safely']}")
        print(f"Tracked APNs .p8 files committed: {summary['tracked_p8_file_count']}")
        print(f"Overall ready: {summary['ready']}")

    if summary["ready"]:
        return
    if args.allow_missing_runtime_env and not tracked_p8:
        return

    failures = []
    for key, value in summary["apns"].items():
        if key != "ready" and value is False:
            failures.append(f"APNs check failed: {key}")
    for key, value in summary["fcm"].items():
        if key != "ready" and value is False:
            failures.append(f"FCM check failed: {key}")
    if tracked_p8:
        failures.append("tracked .p8 private key file is present")
    raise SystemExit("push credentials readiness audit failed:\n- " + "\n- ".join(failures))


if __name__ == "__main__":
    main()
