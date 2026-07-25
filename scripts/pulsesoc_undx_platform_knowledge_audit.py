#!/usr/bin/env python3
"""Executable release gate for UNDX source-derived platform knowledge."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"PASS: {message}")


def main() -> None:
    generator = (ROOT / "scripts/generate_pulsesoc_platform_manifest.py").read_text(encoding="utf-8")
    runtime = (ROOT / "services/undx_platform_knowledge.py").read_text(encoding="utf-8")
    service = (ROOT / "services/pulse_ai_service.py").read_text(encoding="utf-8")
    require("deterministic_source_inventory" in generator, "manifest generation is deterministic and source-derived")
    require("MAX_RESULTS = 6" in runtime and "MAX_CONTEXT_CHARS = 3600" in runtime,
            "runtime retrieval has hard item and character bounds")
    require('"source":' not in runtime.split("def retrieve", 1)[1],
            "prompt-ready retrieval does not serialize source provenance")
    require("undx_platform_knowledge.retrieve(body)" in service,
            "source knowledge reuses the existing UNDX Messenger retrieval pipeline")
    require("complete manifest" in service and "complete manifest" in generator,
            "implementation explicitly prohibits full-manifest prompt injection")
    print("PASS: UNDX source-derived platform knowledge release gate")


if __name__ == "__main__":
    main()
