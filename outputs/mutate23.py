#!/usr/bin/env python3
"""Break Batch 23 on purpose, one property at a time, and check the suite notices.

Batch 23 exists because a suite that looked like it covered single-use approvals did not.
`test_confirm_path.py::test_token_cannot_be_replayed` has asserted single use since the
gateway was written, and passes, and passed throughout the entire period in which a
`CONTEXTUAL` approval could be replayed for its whole TTL. It only ever ran against an
`ALWAYS` capability, which takes a different branch.

So the point of this file is narrower than usual. It is not "do the new tests fail when
the new code breaks" — it is "do they fail for the *specific* reason the old ones did
not". Each mode below restores one component of the original defect and names the single
test that must go red. A mode that reports SURVIVED names a claim these tests are not
actually enforcing, which is the same shape of hole Batch 23 was.

    python3 outputs/mutate23.py                          # every mode
    python3 outputs/mutate23.py redemption_under_policy  # one mode

`MUTATE23_BASELINE=0` skips the unmutated run, for when the baseline has already been
established in this sitting.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
GATEWAY = ROOT / "services" / "undx_tool_gateway.py"
ARCH = ROOT / "services" / "undx_architecture.py"

SUITE = "tests.undx_agent.test_spent_approval"

#: The redemption block, anchored on the `if presented:` line plus enough of its body to
#: be unique. The `_checkpoint(cur)` call appears elsewhere in the function, so the
#: anchor cannot be taken from it alone.
REDEEM = """    if presented:
        grant = _redeem(cur, int(user_id), spec, arguments, confirmation_token)"""

#: The refusal that follows a token which cannot be redeemed. Kept separate from the
#: block above because the two failures are different: ignoring a token and accepting a
#: dead one are not the same defect and must not share a mutation.
REFUSE = """        if not grant:"""

CONFIRMED_ARG = "                confirmed=bool(grant))"

CLAUSES = """    confirmation_state = ("confirmed" if evidence["bound"]
                          else "not_required" if not required
                          else "missing" if not evidence["present"]
                          else "rejected:" + evidence["reason"])"""

MINT_GUARD = "    if decision.needs_confirmation and not presented:"

#: name -> (file, old, new, the test that must go red, what the mutation destroys)
MUTATIONS: dict[str, tuple[pathlib.Path, str, str, str, str]] = {
    "redemption_under_policy": (
        GATEWAY,
        REDEEM,
        """    if decision.needs_confirmation:
        grant = _redeem(cur, int(user_id), spec, arguments, confirmation_token)""",
        "test_a_contextual_approval_is_marked_consumed_once_it_is_acted_on",
        "puts redemption back under the policy engine's verdict — the original defect, "
        "restored exactly. A CONTEXTUAL approval that is presented is never looked at, "
        "so the row stays pending and replayable for the rest of its TTL",
    ),
    "presented_token_is_ignored": (
        GATEWAY,
        REDEEM,
        """    if False:
        grant = _redeem(cur, int(user_id), spec, arguments, confirmation_token)""",
        "test_a_contextual_token_cannot_be_replayed",
        "stops redeeming altogether, so a token can be presented any number of times "
        "and each press performs the write again",
    ),
    "dead_token_executes_anyway": (
        GATEWAY,
        REFUSE,
        "        if False:",
        "test_the_gateway_refuses_a_dead_token_presented_directly_to_it",
        "burns the grant but lets an unredeemable token through to the executor — the "
        "failure that would pass a replay test asserted at the response and still run "
        "the write twice. This is why that test is asserted at the audit table",
    ),
    "audit_forgets_the_grant": (
        GATEWAY,
        CONFIRMED_ARG,
        "                confirmed=False)",
        "test_the_gateway_tells_the_audit_layer_a_grant_was_redeemed",
        "stops telling the audit layer that a grant was redeemed, so a write a person "
        "explicitly approved cannot answer 'authorised by what'",
    ),
    "confirmed_becomes_a_constant": (
        GATEWAY,
        CONFIRMED_ARG,
        "                confirmed=True)",
        "test_an_unapproved_write_is_not_begun_as_an_approved_one",
        "labels every write confirmed, which makes the column green everywhere and "
        "therefore worthless — the mode that catches a fix applied by flattening",
    ),
    "registry_outranks_the_grant": (
        ARCH,
        CLAUSES,
        """    confirmation_state = ("not_required" if not required
                          else "confirmed" if evidence["bound"]
                          else "missing" if not evidence["present"]
                          else "rejected:" + evidence["reason"])""",
        "test_the_audit_row_names_the_grant_it_was_confirmed_against",
        "asks the tool registry first again. A fact about the tool overwrites a fact "
        "about this operation, so an approved contextual write is recorded identically "
        "to one nobody was ever asked about",
    ),
    "every_write_demands_a_card": (
        GATEWAY,
        MINT_GUARD,
        "    if not presented:",
        "test_an_unhedged_contextual_request_still_needs_no_card",
        "mints an approval for everything, which does make every token get redeemed and "
        "is the cheap way to pass the rest of this file. The guard that stops the fix "
        "being bought by demanding approval for actions that are their own approval",
    ),
}


#: Where the untouched source is parked while a mutation is applied. Same sidecar as
#: mutate20/21/22, for the same reason: a run killed before its ``finally`` once left a
#: mutation applied to a tree that looked clean at a glance. Emptied rather than deleted,
#: because ``unlink`` raises ``PermissionError`` on this mount.
PARKED = ROOT / "outputs" / ".mutate23-original"


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


def run_suite(guard: str | None = None) -> tuple[bool, list[str]]:
    # Output to a file rather than through `capture_output`, for the reason recorded in
    # mutate22: a child holding the write end of an inherited pipe can block the read
    # forever after the parent has exited. A file has no end to hold open.
    log = pathlib.Path(tempfile.gettempdir()) / "mutate23-unittest.log"
    with log.open("w") as sink:
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", SUITE, *(["-k", guard] if guard else []), "-v"],
            cwd=ROOT, stdout=sink, stderr=subprocess.STDOUT, text=True,
        )
    out = log.read_text().strip().splitlines()
    tail = [line for line in out if line.startswith("Ran ")] or out[-1:]
    # `unittest -k` that matches nothing runs zero tests and exits 0, which would print
    # as SURVIVED — a mis-typed guard name would read as a hole in the suite. Refuse the
    # run rather than report on it.
    if guard and any(line.startswith("Ran 0 tests") for line in tail):
        raise SystemExit(f"the named test {guard!r} matched nothing — fix the name")
    return proc.returncode == 0, tail


def apply_one(name: str) -> bool:
    path, old, new, guard, destroys = MUTATIONS[name]
    source = path.read_text()
    if source.count(old) != 1:
        print(f"  SKIPPED  {name}: anchor matched {source.count(old)} times, not once")
        print("           (the file moved under this script; fix the anchor)")
        return False
    park(path, source)
    path.write_text(source.replace(old, new, 1))
    try:
        passed, tail = run_suite(guard)
    finally:
        path.write_text(source)
        release()
    verdict = "SURVIVED" if passed else "caught"
    print(f"  {verdict:9s} {name}")
    print(f"            {destroys}")
    if passed:
        print("            NOTHING FAILED. This property is not actually being tested.")
    else:
        print(f"            {guard} -- {' '.join(tail)}")
    return not passed


def step(argv: list[str]) -> int:
    """One phase per process, for when the whole run will not fit inside a wall clock."""
    verb = argv[0]
    if verb == "restore":
        heal()
        return 0
    if verb == "apply":
        name = argv[1]
        path, old, new, _guard, _destroys = MUTATIONS[name]
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
        _path, _old, _new, guard, destroys = MUTATIONS[name]
        passed, tail = run_suite(guard)
        verdict = "SURVIVED" if passed else "caught"
        print(f"  {verdict:9s} {name}")
        print(f"            {destroys}")
        print("            NOTHING FAILED. This property is not actually being tested."
              if passed else f"            {guard} -- {' '.join(tail)}")
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

    if os.environ.get("MUTATE23_BASELINE", "1") == "1":
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
