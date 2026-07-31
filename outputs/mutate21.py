#!/usr/bin/env python3
"""Break Batch 21 on purpose, one property at a time, and check the suite notices.

Batch 21 is a *client* change, and the reason it exists is that Batch 20's six carefully
distinguished sentences were already correct on the wire and still invisible on the
screen. That failure mode — a correct value that nothing draws — is exactly the kind a
green suite is worst at catching, because the value really is correct everywhere the
test looks.

So each mode here removes one property Batch 21 claims to hold, runs the suite that is
supposed to hold it, and reports whether anything went red.

    python3 outputs/mutate21.py                 # every mode
    python3 outputs/mutate21.py banner_only     # one mode

`MUTATE21_BASELINE=0` skips the unmutated run, for when the baseline has already been
established in this sitting and the modes are being worked through a few at a time.

A mode that reports SURVIVED is the useful output: it names a claim the suite is not
actually enforcing.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
APP = ROOT / "mobile-native"
CHAT = APP / "src" / "screens" / "ChatScreen.tsx"
CARDS = APP / "src" / "undx" / "actionCards.ts"

#: The two suites written against the two files that hold the batch.
UNIT = "src/undx/__tests__/tapOutcome.test.ts"
CARD = "src/screens/__tests__/undxTapOutcomeCard.test.tsx"
SUITES = [UNIT, CARD]

#: Each mutation names both the suite and the single test that *claims* the property it
#: destroys, and only that test is run for it. That is not a shortcut around a slow harness — though it is also
#: that; a jest start on this mounted tree costs twenty-four seconds before a single test
#: runs, and the command that drives this script is capped at forty-five. It is the
#: sharper check: a mutation caught by some unrelated test would tell me only that
#: something noticed, whereas naming the assertion in advance and watching that one go red
#: says the test I wrote for the property is the test that holds it.
#:
#: A named test that matches nothing would run zero tests and exit green, which would read
#: as SURVIVED — so :func:`run_suite` refuses a run that executed no tests rather than
#: reporting on it.
#:
#: name -> (file, old, new, suites, the test that must go red, what the mutation destroys)
MUTATIONS: dict[str, tuple[pathlib.Path, str, str, list[str], str, str]] = {
    "banner_only": (
        CHAT,
        "              {outcome ? (\n"
        '                <Text accessibilityLabel="UNDX action outcome" style={styles.undxActionOutcome}>',
        "              {outcome && !keyboardVisible ? (\n"
        '                <Text accessibilityLabel="UNDX action outcome" style={styles.undxActionOutcome}>',
        [CARD],
        "draws the sentence with the keyboard up",
        "gates the sentence on the keyboard being down, which is the original defect "
        "exactly: a person taps Confirm on a card they summoned by typing, so the "
        "keyboard is up and the sentence is never drawn",
    ),
    "no_outcome_at_all": (
        CHAT,
        "              {outcome ? (\n"
        '                <Text accessibilityLabel="UNDX action outcome" style={styles.undxActionOutcome}>\n'
        "                  {outcome.message}\n"
        "                </Text>\n"
        "              ) : null}",
        "              {null}",
        [CARD],
        "draws the expired sentence where the person is looking",
        "removes the sentence from the card entirely, leaving the refusal to the status "
        "banner alone — the state the batch was opened to fix",
    ),
    "outcome_not_matched_to_card": (
        CHAT,
        "              card.confirmationToken && undxTapOutcome?.token === card.confirmationToken ? undxTapOutcome : null;",
        "              card.confirmationToken ? undxTapOutcome : null;",
        [CARD],
        "answers the card that was pressed, and not the one beside it",
        "drops the token match, so a rail holding two confirmations draws one card's "
        "refusal underneath the other one — an answer attached to the wrong question",
    ),
    "no_way_out": (
        CHAT,
        "              {card.confirmationToken && outcome && !outcome.retryable ? (",
        "              {false && card.confirmationToken && outcome && !outcome.retryable ? (",
        [CARD],
        "leaves a way out of a card the server called dead",
        "removes the Dismiss branch, so a card the server called dead keeps a disabled "
        "Confirm and a disabled Cancel and can never be cleared",
    ),
    "dead_card_keeps_its_buttons": (
        CHAT,
        "        setUndxTapOutcome({ ...outcome, token });\n"
        "        setStatusMessage(outcome.message);\n"
        "      })\n"
        "      .finally(() => setUndxActionBusy(false));",
        "        setUndxTapOutcome({ ...outcome, token: `${token}-elsewhere` });\n"
        "        setStatusMessage(outcome.message);\n"
        "      })\n"
        "      .finally(() => setUndxActionBusy(false));",
        [CARD],
        "draws the expired sentence where the person is looking",
        "files the outcome under a token no card carries, so the sentence is computed, "
        "stored and never matched — green everywhere except on screen",
    ),
    "spent_token_swallows_the_retry": (
        CHAT,
        "        if (outcome.retryable) undxSpentTokens.current.delete(token);",
        "        if (false) undxSpentTokens.current.delete(token);",
        [CARD],
        "keeps Confirm alive when the request did not complete",
        "leaves an unreachable-request token in the spent set, so Confirm looks alive "
        "and the second press is dropped before it reaches the network",
    ),
    "everything_retries": (
        CARDS,
        '  return { message, retryable: code === "request_unreachable" };',
        "  return { message, retryable: true };",
        [UNIT],
        "does not re-arm any state the server answered with",
        "re-arms Confirm for every refusal including the six the server answered, so a "
        "person told their write already ran is handed a live button to run it again",
    ),
    "nothing_retries": (
        CARDS,
        '  return { message, retryable: code === "request_unreachable" };',
        "  return { message, retryable: false };",
        [UNIT],
        "re-arms when the request did not complete",
        "never re-arms, so a press that never reached a server at all is treated as an "
        "answered refusal and the approval is stranded while still perfectly good",
    ),
    "retry_by_status": (
        CARDS,
        '  const code = (error as { code?: unknown } | null)?.code;\n'
        '  return { message, retryable: code === "request_unreachable" };',
        "  const status = (error as { status?: unknown } | null)?.status;\n"
        "  return { message, retryable: status === 503 };",
        [UNIT],
        "tells the two 503s apart by code, not by status",
        "keys the retry on the status instead of the code, so the 503 a reachable "
        "server sends when the executor is switched off re-arms a button that server "
        "has already refused",
    ),
    "client_rewrites_the_sentence": (
        CARDS,
        "  const message = error instanceof Error && error.message ? error.message : UNDX_TAP_FALLBACK_MESSAGE;",
        "  const message = UNDX_TAP_FALLBACK_MESSAGE;",
        [UNIT],
        "carries each dead-approval sentence through unaltered",
        "discards the sentence the server sent and shows one generic line, collapsing "
        "all six states again at the last layer that could lose them — Batch 20's "
        "defect restored from the client side",
    ),
}


#: Where the untouched source is parked while a mutation is applied.
#:
#: Same reasoning as ``outputs/.mutate20-original``: a ``finally`` restores the file when
#: this script exits and does not restore it when the process is killed, and a killed run
#: once left a mutation applied to a tree that looked clean. The original text is written
#: to disk before the mutation is, so any later run can put it back without knowing what
#: happened, and :func:`heal` runs before anything else.
#:
#: Emptied rather than deleted on a clean finish: this file lives on a mount whose
#: directory belongs to another account, where ``unlink`` raises ``PermissionError``.
PARKED = ROOT / "outputs" / ".mutate21-original"


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
    proc = subprocess.run(
        # The cache goes to the sandbox's own disk rather than the mounted tree: every
        # mode pays a fresh jest start, and the mount is where that cost lives.
        #
        # --bail stops at the first red test. The question a mutation asks is whether
        # anything notices, not how many things do, and a failing render test prints the
        # entire component tree — several of those in one run is most of a minute spent
        # formatting output nobody reads. The count in the summary line is therefore a
        # lower bound: "1 failed" means at least one, not exactly one.
        ["./node_modules/.bin/jest", *(suites or SUITES), "--silent", "--cacheDirectory=/tmp/jestcache",
         *(["-t", guard] if guard else []),
         # The screen keeps a sync interval alive; a run whose test failed before
         # unmount can otherwise sit in teardown waiting for it.
         "--forceExit"],
        cwd=APP, capture_output=True, text=True,
    )
    out = (proc.stderr or proc.stdout).strip().splitlines()
    tail = [line for line in out if line.startswith("Tests:")] or out[-1:]
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


#: Applying, running and restoring as three separate invocations.
#:
#: One pass over the render suite costs about forty seconds on this mount and the command
#: that drives this script is killed at forty-five, so the all-in-one path above cannot
#: finish a card-suite mode. These three do the same work across three invocations, and
#: they are safe to interleave for exactly the reason the sidecar exists: the untouched
#: source is on disk from the moment ``apply`` writes the mutation, so ``restore`` needs
#: no memory of what ``apply`` did and a run abandoned between the two heals itself.
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

    # A single run of these two suites takes the better part of a minute on the mounted
    # tree, and the harness that drives this script caps a command at 45 seconds — so the
    # ten modes are run a few at a time across several invocations, and the baseline is
    # established once rather than paid for again on every one of them.
    if os.environ.get("MUTATE21_BASELINE", "1") == "1":
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
