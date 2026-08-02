#!/usr/bin/env python3
"""Change-detection gate for the protected real-time audio boundary.

Compares a diff range against ``config/realtime-audio-protected-paths.json``. If
nothing protected moved, it says so and exits 0 — the whole point is that ordinary
work is not slowed down. If something protected did move, it demands a change
declaration and reports the validation that must run before the change can merge.

Why a declaration and not just tests: the tests prove the invariants still hold in
a simulator. They cannot prove a human heard audio on a phone. The declaration is
where the author states which physical validation they owe, what the rollback is,
and why the change was necessary at all. A protected change that merges without one
is a change nobody can reconstruct after it breaks production audio.

Exit codes
    0  no protected change, or protected change with a valid declaration
    1  protected change with a missing, stale, or incomplete declaration
    2  the gate could not run (bad range, missing manifest)

Usage
    python3 scripts/realtime_audio_change_gate.py --base <sha> --head <sha>
    python3 scripts/realtime_audio_change_gate.py --changed-files-from <file>
    python3 scripts/realtime_audio_change_gate.py --base <sha> --head <sha> --json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "config" / "realtime-audio-protected-paths.json"

# bot.py is one enormous module. Protecting the whole file would mean every
# backend change anywhere in the product needs an audio declaration, which is the
# kind of over-broad rule developers learn to route around. Instead a bot.py diff
# counts as protected only when the diff text itself mentions an audio symbol.
BACKEND_FILE = "bot.py"

# Present in the committed template; must be removed by whoever fills it in.
TEMPLATE_MARKER = "TEMPLATE-NOT-YET-FILLED"


def load_manifest() -> dict:
    if not MANIFEST.exists():
        print(f"::error::protected-path manifest missing at {MANIFEST}", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"::error::git {' '.join(args)} failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return result.stdout


def changed_files(base: str, head: str) -> list[str]:
    out = git("diff", "--name-only", f"{base}...{head}")
    return [line.strip() for line in out.splitlines() if line.strip()]


def backend_diff_is_audio_related(base: str, head: str, patterns: Iterable[str]) -> list[str]:
    """Return the audio patterns that appear in changed bot.py lines."""
    diff = git("diff", "--unified=0", f"{base}...{head}", "--", BACKEND_FILE)
    touched = []
    for line in diff.splitlines():
        if not (line.startswith("+") or line.startswith("-")):
            continue
        if line.startswith("+++") or line.startswith("---"):
            continue
        for pattern in patterns:
            if pattern in line and pattern not in touched:
                touched.append(pattern)
    return touched


def protected_paths(manifest: dict) -> dict[str, str]:
    """Map every protected path to the category that protects it."""
    mapping: dict[str, str] = {}
    for category in manifest["categories"]:
        for path in category["paths"]:
            mapping[path] = category["id"]
    for path in manifest["dependency_watch"]["files"]:
        mapping.setdefault(path, "dependency_watch")
    return mapping


def validate_declaration(manifest: dict, hits: list[str], base: str | None, head: str | None) -> list[str]:
    """Return a list of problems with the change declaration. Empty means valid."""
    spec = manifest["declaration"]
    path = ROOT / spec["path"]
    problems: list[str] = []

    if not path.exists():
        return [
            f"{spec['path']} does not exist. Protected real-time audio paths changed, "
            "so a change declaration is required before this can merge."
        ]

    text = path.read_text(encoding="utf-8")

    # An unfilled template passes a naive "does it contain the headings" check
    # while saying nothing. The marker is the author's acknowledgement that they
    # replaced the placeholder text rather than committing the skeleton.
    if TEMPLATE_MARKER in text:
        problems.append(
            f"{spec['path']} is still the unfilled template. Remove the "
            f"'{TEMPLATE_MARKER}' marker and describe this specific change."
        )

    # A declaration left over from an earlier change is worse than none: it looks
    # like the author considered this change when they did not. It must be
    # touched by the same diff that touched the protected files.
    if base and head:
        if spec["path"] not in changed_files(base, head):
            problems.append(
                f"{spec['path']} exists but was not modified in this change. "
                "A declaration must describe this change, not a previous one."
            )

    for section in spec["required_sections"]:
        if section.lower() not in text.lower():
            problems.append(f"{spec['path']} is missing the required section: {section}")

    # The declaration has to name the files it is declaring. Otherwise "some audio
    # files changed" passes the gate.
    unnamed = [h for h in hits if h != BACKEND_FILE and h not in text]
    if unnamed:
        problems.append(
            f"{spec['path']} does not name these changed protected files: " + ", ".join(sorted(unnamed))
        )

    return problems


REQUIRED_VALIDATION = [
    ("critical audio tests", "npm run test:realtime-audio-critical"),
    ("full audio suite", "npm run test:realtime-audio"),
    ("architecture tests (native)", "npm run test:realtime-audio-architecture"),
    (
        "architecture tests (backend)",
        "python3 -m unittest tests.protection.test_realtime_audio_architecture",
    ),
    (
        "backend token tests",
        "python3 -m unittest tests.protection.test_call_livekit_token_grants "
        "tests.protection.test_livestream_audio_token_grants "
        "tests.protection.test_livekit_webhook_route_owner",
    ),
    ("TypeScript compilation", "npm run typecheck"),
    ("native build verification", "npx expo prebuild --platform ios --no-install (or an EAS build)"),
    ("physical audible validation", "see reports/realtime_audio_verified_baseline.md section 7"),
]


def emit_github_output(**values: object) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            handle.write(f"{key}={json.dumps(value) if not isinstance(value, str) else value}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="base commit or ref")
    parser.add_argument("--head", default="HEAD", help="head commit or ref")
    parser.add_argument(
        "--changed-files-from",
        help="read the changed-file list from a file, one path per line, instead of git",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable output")
    parser.add_argument(
        "--skip-declaration",
        action="store_true",
        help="report protected changes without failing on a missing declaration (local use)",
    )
    args = parser.parse_args()

    manifest = load_manifest()

    if args.changed_files_from:
        files = [
            line.strip()
            for line in Path(args.changed_files_from).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        base = head = None
    elif args.base:
        base, head = args.base, args.head
        files = changed_files(base, head)
    else:
        parser.error("one of --base or --changed-files-from is required")
        return 2

    mapping = protected_paths(manifest)
    hits: list[str] = []
    reasons: dict[str, str] = {}

    for path in files:
        if path in mapping:
            hits.append(path)
            reasons[path] = mapping[path]

    if BACKEND_FILE in files and base and head:
        patterns = backend_diff_is_audio_related(base, head, manifest["backend_diff_patterns"])
        if patterns:
            hits.append(BACKEND_FILE)
            reasons[BACKEND_FILE] = "backend_token_and_room_policy (" + ", ".join(patterns) + ")"

    protected = bool(hits)

    if not protected:
        message = (
            f"No protected real-time audio path changed ({len(files)} file(s) inspected). "
            "Audio validation is not required for this change."
        )
        if args.json:
            print(json.dumps({"protected": False, "changed_files": len(files), "hits": []}, indent=2))
        else:
            print(message)
        emit_github_output(protected="false", hit_count="0")
        return 0

    problems = [] if args.skip_declaration else validate_declaration(manifest, hits, base, head)

    if args.json:
        print(
            json.dumps(
                {
                    "protected": True,
                    "hits": [{"path": p, "category": reasons[p]} for p in sorted(hits)],
                    "declaration": manifest["declaration"]["path"],
                    "declaration_problems": problems,
                    "required_validation": [
                        {"name": n, "command": c} for n, c in REQUIRED_VALIDATION
                    ],
                    "label": manifest["declaration"]["label"],
                },
                indent=2,
            )
        )
    else:
        print("PROTECTED REAL-TIME AUDIO PATHS CHANGED")
        print("")
        for path in sorted(hits):
            print(f"  {path}")
            print(f"      protected by: {reasons[path]}")
        print("")
        print(f"Apply the '{manifest['declaration']['label']}' label and run all of:")
        for name, command in REQUIRED_VALIDATION:
            print(f"  - {name}: {command}")
        print("")
        if problems:
            print("DECLARATION NOT ACCEPTED")
            for problem in problems:
                print(f"  - {problem}")
                print(f"::error::{problem}")
        else:
            print(f"Declaration accepted: {manifest['declaration']['path']}")

    emit_github_output(
        protected="true",
        hit_count=str(len(hits)),
        declaration_ok="false" if problems else "true",
    )

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
