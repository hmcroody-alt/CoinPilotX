#!/usr/bin/env python3
"""Secret-safe Railway UNDX variable inventory.

Input is the JSON emitted by ``railway variable list --json``. Values are used only
to validate type and presence and are never included in output. The audit searches
the repository for literal environment consumers and reports names, locations, safe
defaults, placement, and whether a restart is required.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.undx_brain.config import BY_NAME


BOOL_TRUE = {"1", "true", "yes", "on", "y", "t", "enabled"}
BOOL_FALSE = {"0", "false", "no", "off", "n", "f", "disabled", ""}
SENSITIVE_PARTS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL", "QA_USER_IDS")
EQUIVALENTS = {
    "UNDX_METRICS_ENABLED": "UNDX_BRAIN_METRICS_ENABLED",
    "UNDX_V4_DISABLE_WRITES": "UNDX_AGENT_DISABLE_WRITES",
    "UNDX_V5_QA_USER_IDS": "UNDX_AGENT_QA_USER_IDS",
}
WEB_ONLY_PREFIXES = ("UNDX_HTTP_", "UNDX_NATIVE_CONTEXT_", "UNDX_V5_")
WORKER_ONLY_PREFIXES = ("UNDX_WORKER_",)
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "build", "dist", "Pods", "DerivedData"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--environment", default="production")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    return parser.parse_args()


def _variables(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict):
        if isinstance(payload.get("variables"), dict):
            payload = payload["variables"]
        return {str(k): "" if v is None else str(v) for k, v in payload.items()}
    if isinstance(payload, list):
        out = {}
        for row in payload:
            if isinstance(row, dict) and row.get("name"):
                out[str(row["name"])] = "" if row.get("value") is None else str(row.get("value"))
        return out
    raise ValueError("unsupported Railway variable JSON shape")


def _source_files(root: Path) -> list[Path]:
    paths = []
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.parts):
            continue
        relative = path.relative_to(root)
        top = relative.parts[0]
        runtime_source = (
            top in {"services", "pulse_communications_v2", "mobile-native"}
            or len(relative.parts) == 1
            or path.name in {"Procfile", "Dockerfile", "railway.json", "railway.toml"}
        )
        if not runtime_source or top in {"tests", "scripts", "backend"}:
            continue
        if path.suffix in {".py", ".toml", ".json", ".yaml", ".yml", ".sh"} or path.name in {
            "Procfile", "Dockerfile",
        }:
            paths.append(path)
    return paths


def _consumers(root: Path, names: set[str]) -> dict[str, list[str]]:
    found = {name: [] for name in names}
    for path in _source_files(root):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, 1):
            if "UNDX_" not in line:
                continue
            for name in names:
                if name not in line:
                    continue
                # Catalog declarations are metadata, not a runtime consumer by
                # themselves. A second call site that reads the resolved dictionary
                # is reported separately and makes the variable used.
                if path.as_posix().endswith("services/undx_brain/config.py"):
                    continue
                found[name].append(f"{path.relative_to(root)}:{number}")
    return found


def _kind(name: str) -> str:
    if name in BY_NAME:
        return BY_NAME[name].kind
    if name.endswith(("_ENABLED", "_DISABLE_WRITES", "_KILL_SWITCH", "_QA_ONLY", "_REQUIRED", "_ALLOWED")):
        return "bool"
    if any(token in name for token in ("_MAX_", "_SECONDS", "_RETRIES", "_PERCENT")):
        return "int"
    if name.endswith("_USER_IDS"):
        return "csv_int"
    return "str"


def _valid(name: str, raw: str, kind: str) -> tuple[bool, str]:
    text = str(raw).strip()
    if kind == "bool":
        return text.lower() in BOOL_TRUE | BOOL_FALSE, "boolean"
    if kind == "int":
        if not re.fullmatch(r"[+-]?[0-9]+", text):
            return False, "integer"
        value = int(text)
        spec = BY_NAME.get(name)
        if spec and ((spec.minimum is not None and value < spec.minimum)
                     or (spec.maximum is not None and value > spec.maximum)):
            return False, "bounded_integer"
        return True, "integer"
    if kind == "csv_int":
        valid = all(part.strip().isdigit() for part in text.split(",") if part.strip())
        return valid, "sensitive_csv_identifier_set"
    return True, "string"


def _placement(name: str) -> str:
    if name.startswith(WEB_ONLY_PREFIXES):
        return "CoinPilotX"
    if name.startswith(WORKER_ONLY_PREFIXES):
        return "coinpilotx-undx-worker"
    return "shared_when_consumed"


def main() -> int:
    args = parse_args()
    payload = json.load(sys.stdin)
    variables = {name: value for name, value in _variables(payload).items() if name.startswith("UNDX_")}
    consumers = _consumers(args.repo.resolve(), set(variables))
    rows = []
    for name in sorted(variables):
        raw = variables[name]
        kind = _kind(name)
        valid, effective_type = _valid(name, raw, kind)
        locations = consumers.get(name) or []
        equivalent = EQUIVALENTS.get(name, "")
        if not valid:
            classification = "INVALID_VALUE"
        elif locations:
            classification = "EXISTS_AND_USED"
        elif equivalent:
            classification = "EXISTS_UNDER_EQUIVALENT_NAME"
        elif name.startswith("UNDX_"):
            classification = "FUTURE_CAPABILITY_ONLY"
        else:
            classification = "EXISTS_BUT_UNUSED"
        spec = BY_NAME.get(name)
        rows.append({
            "variable": name,
            "service": args.service,
            "environment": args.environment,
            "scope": "service-specific",
            "secret_or_sensitive": any(part in name for part in SENSITIVE_PARTS),
            "consumer": locations,
            "default_when_absent": spec.default if spec else "consumer-defined-or-none",
            "effective_runtime_type": effective_type,
            "deployment_required": bool(spec.redeploy) if spec else True,
            "placement": _placement(name),
            "classification": classification,
            "equivalent_name": equivalent,
            "present": True,
        })
    print(json.dumps({"service": args.service, "environment": args.environment, "rows": rows}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
