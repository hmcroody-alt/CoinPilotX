#!/usr/bin/env python3
"""Audit PulseSoc Native QA runtime stabilization evidence."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MOBILE = ROOT / "mobile-native"
PACKAGE_JSON = MOBILE / "package.json"
PACKAGE_LOCK = MOBILE / "package-lock.json"
STABILIZATION = ROOT / "reports" / "pulsesoc_native_qa_runtime_stabilization.md"
VISIBLE = ROOT / "reports" / "pulsesoc_native_visible_qa_runtime.md"
PROGRESS = ROOT / "reports" / "pulsesoc_native_progress.md"


REQUIRED_REPORT_TOKENS = [
    "QA Runtime Stabilization",
    "expo-modules-core",
    "nullthrows",
    "Root cause assessment",
    "Can visible QA now be trusted",
]

REQUIRED_VISIBLE_TOKENS = [
    "Visible QA Runtime",
    "Home",
    "Dashboard",
    "Composer",
    "Marketplace",
    "Messages",
]

REQUIRED_PROGRESS_TOKENS = [
    "Native QA Runtime Stabilization",
    "Visible QA can resume",
]


def read_text(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"Missing required file: {path.relative_to(ROOT)}")
    return path.read_text(encoding="utf-8")


def read_json(path: Path) -> dict:
    if not path.exists():
        raise AssertionError(f"Missing required file: {path.relative_to(ROOT)}")
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_tokens(label: str, text: str, tokens: list[str]) -> None:
    missing = [token for token in tokens if token not in text]
    if missing:
        raise AssertionError(f"{label} missing tokens: {', '.join(missing)}")


def node_resolve(module_name: str) -> str:
    proc = subprocess.run(
        [
            "node",
            "-e",
            f"console.log(require.resolve('{module_name}/package.json'))",
        ],
        cwd=MOBILE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"Node cannot resolve {module_name}: {proc.stderr.strip() or proc.stdout.strip()}"
        )
    return proc.stdout.strip()


def main() -> None:
    package = read_json(PACKAGE_JSON)
    lock = read_json(PACKAGE_LOCK)
    deps = package.get("dependencies", {})
    packages = lock.get("packages", {})

    require("expo-modules-core" not in deps, "expo-modules-core must remain managed by Expo, not direct")
    require(deps.get("nullthrows") == "1.1.1", "nullthrows must be an explicit Metro resolver dependency")
    require(
        packages.get("node_modules/expo-modules-core", {}).get("version") == "3.0.30",
        "package-lock must contain expo-modules-core 3.0.30",
    )
    require(
        packages.get("node_modules/nullthrows", {}).get("version") == "1.1.1",
        "package-lock must contain nullthrows 1.1.1",
    )

    for module in ["expo", "expo-modules-core", "react-native-web", "nullthrows"]:
        resolved = node_resolve(module)
        require(str(MOBILE / "node_modules") in resolved, f"{module} resolved outside mobile-native node_modules")

    stabilization = read_text(STABILIZATION)
    visible = read_text(VISIBLE)
    progress = read_text(PROGRESS)
    require_tokens("QA runtime stabilization report", stabilization, REQUIRED_REPORT_TOKENS)
    require_tokens("Visible QA runtime report", visible, REQUIRED_VISIBLE_TOKENS)
    require_tokens("Native progress report", progress, REQUIRED_PROGRESS_TOKENS)

    print("PulseSoc Native QA runtime audit passed.")
    print("Verified Expo-managed expo-modules-core, explicit nullthrows resolution, and runtime stabilization reports.")


if __name__ == "__main__":
    main()
