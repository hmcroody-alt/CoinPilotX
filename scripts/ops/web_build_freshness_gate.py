#!/usr/bin/env python3
"""Do the committed web artifacts match the committed web source?

The failure this blocks
-----------------------
`nixpacks.toml` installs `python311` and `ffmpeg`. There is no Node in the
production image, and Railway deploys from git, so the built SPA has to be
committed. That is a sound decision with one sharp edge: **a committed artifact
can go stale, and it does so silently.**

Edit `web/src/`, forget to rebuild, commit. Every signal stays green -- the
Python tests pass, the app boots, `/health` answers 200, the page renders. It
just renders last week's bundle. That is the same shape as the TestFlight build
5 failure this repo has already shipped once: two halves disagreeing while each
half looks healthy on its own.

Nothing else can catch it. A unit test runs the source; a browser runs the
artifact; no existing suite compares the two.

What is compared
----------------
Source inputs, not output bytes. `web/scripts/fingerprint.mjs` records a
sha256 per source file at build time; this recomputes those hashes from the
working tree and diffs the two maps.

Comparing the emitted bundle instead would make the gate depend on the bundler
being byte-reproducible across machines and Node versions. It mostly is, and
"mostly" is exactly what produces a false alarm on a Node minor bump -- which
is how a check gets switched off. Hashing inputs is just as strong for the
failure being prevented and is deterministic by construction.

Three outcomes, not two
-----------------------
`0` fresh · `1` stale · `3` could not check. The third exists because every way
this gate can break -- a missing record, an unreadable file, a source tree that
resolves to nothing -- makes it *quieter* rather than louder. A gate that
cannot see the tree must not report that the tree is fine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
WEB = REPO / "web"
RECORD = REPO / "static" / "app" / "build-source-hash.json"

EXIT_OK = 0
EXIT_STALE = 1
EXIT_NO_DATA = 3

# Must stay in step with `web/scripts/fingerprint.mjs`. The two lists are
# asserted equal by tests/protection/test_web_build_freshness_gate.py, because
# a silent drift here would narrow the gate's coverage without failing
# anything: files quietly stop being watched and stale artifacts start passing.
SOURCE_DIRS = ["src"]
SOURCE_FILES = [
    "index.html",
    "vite.config.ts",
    "tsconfig.json",
    "package.json",
    "package-lock.json",
    "scripts/fingerprint.mjs",
]


def sha256(path: pathlib.Path) -> str:
    """Raw bytes, matching the JS side exactly.

    No line-ending normalisation: `web/.gitattributes` pins `eol=lf` so the
    bytes on disk are identical on every checkout, which is safer than two
    independent implementations of the same normalisation rule.
    """
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_sources(web: pathlib.Path) -> dict[str, str]:
    """Current source hashes, keyed by repo-relative POSIX path."""
    out: dict[str, str] = {}
    for rel_dir in SOURCE_DIRS:
        root = web / rel_dir
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.is_file():
                out[path.relative_to(REPO).as_posix()] = sha256(path)
    for rel_file in SOURCE_FILES:
        path = web / rel_file
        if path.is_file():
            out[path.relative_to(REPO).as_posix()] = sha256(path)
    return out


def fingerprint_of(files: dict[str, str]) -> str:
    names = sorted(files)
    joined = "\n".join(f"{n}:{files[n]}" for n in names)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def load_record(path: pathlib.Path):
    """Returns (record, None) or (None, reason)."""
    if not path.exists():
        return None, (
            f"no build record at {path.relative_to(REPO)}. The web client has "
            f"never been built, or the build output was not committed")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"build record is unreadable: {exc}"
    if not isinstance(record, dict) or not isinstance(record.get("files"), dict):
        return None, "build record has no 'files' map; it was not written by fingerprint.mjs"
    return record, None


def compare(recorded: dict[str, str], current: dict[str, str]):
    """(changed, added, removed) -- all repo-relative paths."""
    changed = sorted(n for n in recorded if n in current and recorded[n] != current[n])
    added = sorted(n for n in current if n not in recorded)
    removed = sorted(n for n in recorded if n not in current)
    return changed, added, removed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not WEB.is_dir():
        print(f"web-build: no web/ directory at {WEB}. Nothing to check, which "
              f"is not the same as nothing being wrong.", file=sys.stderr)
        return EXIT_NO_DATA

    current = collect_sources(WEB)
    if not current:
        # The single most important branch in this file. An empty source map
        # compares equal to an empty record, so without this the gate reports
        # a clean pass at exactly the moment it has stopped looking at anything.
        print("web-build: found no source files under web/. That is a broken "
              "check, not a clean tree.", file=sys.stderr)
        return EXIT_NO_DATA

    record, reason = load_record(RECORD)
    if record is None:
        print(f"web-build: {reason}", file=sys.stderr)
        return EXIT_NO_DATA

    recorded = {k: v for k, v in record["files"].items() if isinstance(v, str)}
    changed, added, removed = compare(recorded, current)
    fresh = not (changed or added or removed)

    if args.json:
        print(json.dumps({
            "fresh": fresh,
            "recorded_files": len(recorded),
            "current_files": len(current),
            "changed": changed,
            "added": added,
            "removed": removed,
            "recorded_fingerprint": record.get("fingerprint"),
            "current_fingerprint": fingerprint_of(current),
        }, indent=2))
        return EXIT_OK if fresh else EXIT_STALE

    if fresh:
        print(f"web-build: committed artifacts match {len(current)} source "
              f"file(s). Fingerprint {fingerprint_of(current)[:16]}…")
        return EXIT_OK

    print("web-build: the committed SPA was NOT built from the committed source.")
    for name in changed:
        print(f"  changed since the build : {name}")
    for name in added:
        print(f"  new, never built        : {name}")
    for name in removed:
        print(f"  gone since the build    : {name}")
    print("\nThe deployed bundle would be the older one, and nothing else would "
          "report it: the page renders, the app boots, the tests pass.")
    print("\nRebuild and commit the result:\n\n  cd web && npm ci && npm run build\n")
    print("Then commit web/ together with static/app/. They are one change; "
          "committing either alone is what this gate exists to catch.")
    return EXIT_STALE


if __name__ == "__main__":
    raise SystemExit(main())
