#!/usr/bin/env python3
"""Audit the UNDX source-derived training corpus."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "backend" / "undx" / "config" / "undx_training_v6_source_corpus.yaml"
GENERATOR = ROOT / "scripts" / "generate_undx_source_training_yaml.py"

SECRET_PATTERNS = [
    r"BEGIN (RSA|OPENSSH|PRIVATE)",
    r"xox[baprs]-",
    r"AKIA[0-9A-Z]",
    r"APP_STORE_CONNECT_API_KEY",
    r"STRIPE_SECRET",
    r"SECRET_KEY\s*=",
    r"PASSWORD\s*=",
    r"TOKEN\s*=",
    r"Bearer [A-Za-z0-9_\-\.]{20,}",
]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def scalar_int(text: str, key: str) -> int:
    match = re.search(rf"(?m)^\s*{re.escape(key)}:\s+([0-9]+)\s*$", text)
    require(match is not None, f"Missing numeric key: {key}")
    return int(match.group(1))


def main() -> int:
    require(GENERATOR.exists(), "Generator script is missing")
    require(CORPUS.exists(), "UNDX v6 source corpus is missing")
    text = CORPUS.read_text(encoding="utf-8")
    for required in [
        'schema_version: "6.0"',
        'system_name: "UNDX"',
        'codename: "PULSESOC SOURCE CORPUS"',
        "safety_policy:",
        "repository_inventory:",
        "backend_routes:",
        "api_endpoint_mentions:",
        "database_contracts:",
        "source_records:",
        "training_guidance:",
    ]:
        require(required in text, f"Missing required corpus section: {required}")
    require(scalar_int(text, "source_files_indexed") >= 1000, "Corpus indexed too few source files")
    require(scalar_int(text, "count") >= 1000, "Corpus route count appears too low")
    require("node_modules/" not in text, "Dependency tree leaked into corpus")
    require("not_a_secret_dump: true" in text, "Safety policy is missing secret-dump guard")
    for pattern in SECRET_PATTERNS:
        require(re.search(pattern, text, re.IGNORECASE) is None, f"Potential secret pattern found: {pattern}")
    print("PASS: UNDX source training corpus audit")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
