#!/usr/bin/env python3
"""Break Batch 22 on purpose, one property at a time, and check the suite notices.

Batch 22 is two props. That is a small enough change that mutation testing looks like
ceremony, and it is exactly why it is not: the defect *was* an absent prop, the suite
cannot press a button through the native responder system, and so the assertions are
contract assertions. A contract assertion that does not fail when the contract is broken
is decoration. These modes break it four ways and check.

    python3 outputs/mutate22.py                     # every mode
    python3 outputs/mutate22.py rail_on_the_default # one mode

`MUTATE22_BASELINE=0` skips the unmutated run, for when the baseline has already been
established in this sitting.

A mode that reports SURVIVED is the useful output: it names a claim the suite is not
actually enforcing.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "mobile-native"
CHAT = APP / "src" / "screens" / "ChatScreen.tsx"

TAPS = "src/screens/__tests__/undxKeyboardTaps.test.tsx"
SUITES = [TAPS]

#: The rail's prop, with enough of its surroundings to be unique. The comment above it is
#: long and the FlatList carries the same prop name eight hundred lines away, so the
#: anchor is taken from the line below it rather than from the prop alone.
RAIL_PROP = '          keyboardShouldPersistTaps="handled"\n          style={styles.undxActionRailViewport}'
LIST_PROP = '          keyboardShouldPersistTaps="handled"\n          keyExtractor={(item) =>'

#: name -> (file, old, new, suites, the test that must go red, what the mutation destroys)
MUTATIONS: dict[str, tuple[pathlib.Path, str, str, list[str], str, str]] = {
    "rail_on_the_default": (
        CHAT,
        RAIL_PROP,
        "          style={styles.undxActionRailViewport}",
        [TAPS],
        "does not leave the rail on the default",
        "removes the prop from the action rail, restoring the exact defect found on the "
        "simulator: the first press of Confirm with the keyboard up is consumed to "
        "dismiss the keyboard and never reaches the button",
    ),
    "rail_says_never": (
        CHAT,
        RAIL_PROP,
        '          keyboardShouldPersistTaps="never"\n          style={styles.undxActionRailViewport}',
        [TAPS],
        "gives the action rail a value that delivers the touch",
        "spells the default out loud instead of leaving it implied — the same swallowed "
        "press, now looking deliberate to anyone reading the file",
    ),
    "rail_says_always": (
        CHAT,
        RAIL_PROP,
        '          keyboardShouldPersistTaps="always"\n          style={styles.undxActionRailViewport}',
        [TAPS],
        "keeps the rail at handled",
        "loosens the rail to always, which does deliver the touch and also stops the "
        "keyboard closing on a tap no control claims — the one gesture that means put it "
        "away. Fixes the bug and leaves a smaller one",
    ),
    "list_on_the_default": (
        CHAT,
        LIST_PROP,
        "          keyExtractor={(item) =>",
        [TAPS],
        "gives the message list a value that delivers the touch",
        "removes the prop from the message list, so Retry on a message that failed to "
        "send is unpressable while the composer that produced it still has focus",
    ),
}


#: Where the untouched source is parked while a mutation is applied. Same sidecar as
#: mutate20/21, for the same reason: a run killed before its ``finally`` once left a
#: mutation applied to a tree that looked clean at a glance. Emptied rather than deleted,
#: because ``unlink`` raises ``PermissionError`` on this mount.
PARKED = ROOT / "outputs" / ".mutate22-original"


def park(path: pathlib.Path, source: str) -> None:
    PARKED.write_text(f"{path}\n{source}")


def release() -> None:
    PARKED.write_text("")


def heal() -> None:
    """Undo a mutation left behind by a run that was killed before its ``finally``."""
    if not PARKED.exists() or not PARKED.read_text().strip():
        return
    name, _, source = PARKED.read_text().partition("\n")
    path = pathlib.Path(name)
    if path.read_text() != source:
        path.write_text(source)
        print(f"Restored {path.relative_to(ROOT)} from an interrupted run.\n")
    release()


def run_suite(suites: list[str] | None = None, guard: str | None = None) -> tuple[bool, list[str]]:
    # Output goes to a file rather than through `capture_output`.
    #
    # `--forceExit` ends jest's own process while a worker it spawned can still be holding
    # the write end of an inherited pipe, and `subprocess.run` then blocks reading a pipe
    # nobody will ever close — the run finishes, the harness kills the script at its wall
    # clock, and the verdict never prints. A file has no such end to hold open.
    log = pathlib.Path(tempfile.gettempdir()) / "mutate22-jest.log"
    with log.open("w") as sink:
        proc = subprocess.run(
            ["./node_modules/.bin/jest", *(suites or SUITES), "--silent", "--cacheDirectory=/tmp/jestcache",
             *(["-t", guard] if guard else []),
             "--forceExit"],
            cwd=APP, stdout=sink, stderr=subprocess.STDOUT, text=True,
        )
    out = log.read_text().strip().splitlines()
    tail = [line for line in out if line.startswith("Tests:")] or out[-1:]
    # A named test that matches nothing runs zero tests and exits green, which would read
    # as SURVIVED. Refuse the run rather than report on it.
    if guard and not any("Tests:" in line for line in tail):
        raise SystemExit(f"the named test {guard!r} matched nothing — fix the name")
    return proc.returncode == 0, tail


def apply_one(name: str) -> bool:
    path, old, new, suites, guard, destroys = MUTATIONS[name]
    source = path.read_text()
    if source.count(old) != 1:
        print(f"  SKIPPED  {name}: anchor matched {source.count(old)} times, not once")
        print(f"           (the file moved under this script; fix the anchor)")
        return False
    park(path, source)
    path.write_text(source.replace(old, new, 1))
    try:
        passed, tail = run_suite(suites, guard)
    finally:
        path.write_text(source)
        release()
    verdict = "SURVIVED" if passed else "caught"
    print(f"  {verdict:9s} {name}")
    print(f"            {destroys}")
    if passed:
        print("            NOTHING FAILED. This property is not actually being tested.")
    else:
        print(f"            {' '.join(tail)}")
    return not passed


def step(argv: list[str]) -> int:
    verb = argv[0]
    if verb == "restore":
        heal()
        return 0
    if verb == "apply":
        name = argv[1]
        path, old, new, _suites, _guard, _destroys = MUTATIONS[name]
        source = path.read_text()
        if source.count(old) != 1:
            print(f"anchor for {name} matched {source.count(old)} times, not once")
            return 1
        park(path, source)
        path.write_text(source.replace(old, new, 1))
        print(f"applied {name} to {path.name}")
        return 0
    if verb == "check":
        name = argv[1]
        _path, _old, _new, suites, guard, destroys = MUTATIONS[name]
        passed, tail = run_suite(suites, guard)
        verdict = "SURVIVED" if passed else "caught"
        print(f"  {verdict:9s} {name}")
        print(f"            {destroys}")
        print("            NOTHING FAILED. This property is not actually being tested."
              if passed else f"            {' '.join(tail)}")
        return 1 if passed else 0
    print(f"unknown step: {verb}")
    return 2


def main() -> int:
    if sys.argv[1:2] and sys.argv[1] in {"apply", "check", "restore"}:
        return step(sys.argv[1:])
    heal()
    wanted = sys.argv[1:] or list(MUTATIONS)
    unknown = [name for name in wanted if name not in MUTATIONS]
    if unknown:
        print(f"unknown mode(s): {', '.join(unknown)}")
        print(f"available: {', '.join(MUTATIONS)}")
        return 2

    if os.environ.get("MUTATE22_BASELINE", "1") == "1":
        print("Baseline (unmutated):")
        passed, tail = run_suite()
        if not passed:
            print(f"  suite is already failing — fix that first: {' '.join(tail)}")
            return 1
        print(f"  {' '.join(tail)}\n")

    caught = sum(apply_one(name) for name in wanted)
    print(f"\n{caught}/{len(wanted)} mutations caught.")
    return 0 if caught == len(wanted) else 1


if __name__ == "__main__":
    raise SystemExit(main())
