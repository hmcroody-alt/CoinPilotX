#!/usr/bin/env python3
"""Break Batch 20 on purpose, one property at a time, and check the suite notices.

A green suite proves the code passes the tests. It does not prove the tests would fail
if the code were wrong — and a test that cannot fail is documentation wearing a costume.
Each mode here removes exactly one property Batch 20 claims to hold, runs
``tests/undx_agent``, and reports whether anything went red.

    python3 outputs/mutate20.py            # every mode
    python3 outputs/mutate20.py scope      # one mode

A mode that reports SURVIVED is the useful output: it names a claim the suite is not
actually enforcing.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
ARCH = ROOT / "services" / "undx_architecture.py"
SERVICE = ROOT / "services" / "pulse_ai_service.py"

#: name -> (file, exact text to replace, replacement, what the mutation destroys)
MUTATIONS: dict[str, tuple[pathlib.Path, str, str, str]] = {
    "scope": (
        ARCH,
        "        WHERE token_hash=? AND user_id=? LIMIT 1\"\"\",\n"
        "        (token_hash, int(user_id)),",
        "        WHERE token_hash=? LIMIT 1\"\"\",\n"
        "        (token_hash,),",
        "drops the owner filter, so anybody holding a leaked token learns whether it "
        "was already spent — the disclosure the collapsed sentence was protecting",
    ),
    "spent_reads_as_lapsed": (
        ARCH,
        '    if status == "pending":\n'
        "        return APPROVAL_LIVE if clean(row[\"expires_at\"], 40) > now() else APPROVAL_EXPIRED",
        '    if clean(row["expires_at"], 40) <= now():\n'
        "        return APPROVAL_EXPIRED\n"
        '    if status == "pending":\n'
        "        return APPROVAL_LIVE",
        "checks the deadline before the status, so an approval that was redeemed and "
        "then lapsed reports 'expired' — telling the person nothing happened after it did",
    ),
    "echo_unknown_status": (
        ARCH,
        "    return _APPROVAL_TERMINAL.get(status, APPROVAL_UNKNOWN)",
        "    return _APPROVAL_TERMINAL.get(status, status)",
        "echoes an unrecognised row status straight out, so a column value invented by a "
        "later migration becomes a sentence with no wording behind it",
    ),
    "live_reads_as_spent": (
        ARCH,
        "    APPROVAL_LIVE: (\"That confirmation is still valid and has not been used, but UNDX \"\n"
        "                    \"cannot carry out that action right now, so nothing changed.\"),",
        "    APPROVAL_LIVE: (\"That confirmation expired, was already used, or belongs to \"\n"
        "                    \"another account.\"),",
        "answers the still-good approval with the old collapsed sentence, which is the "
        "same lie as the original defect pointed the other way",
    ),
    "consumed_says_nothing_happened": (
        ARCH,
        "    APPROVAL_CONSUMED: (\"That confirmation was already used, so what it authorised has \"\n"
        "                        \"already been attempted. Check where things stand before \"\n"
        "                        \"confirming it again.\"),",
        "    APPROVAL_CONSUMED: (\"That confirmation is no longer valid, so nothing changed. \"\n"
        "                        \"Ask again if you still want it.\"),",
        "tells a person whose write already ran that nothing happened — the exact "
        "sentence that invites them to run it a second time",
    ),
    "flat_message": (
        SERVICE,
        "            state = undx_architecture.approval_state(cur, int(user_id), token)\n"
        "            conn.rollback()\n"
        "            return {\"ok\": False, \"error\": \"confirmation_invalid\", \"reason\": state,\n"
        "                    \"message\": undx_architecture.APPROVAL_STATE_MESSAGE[state],",
        "            state = undx_architecture.approval_state(cur, int(user_id), token)\n"
        "            conn.rollback()\n"
        "            return {\"ok\": False, \"error\": \"confirmation_invalid\", \"reason\": state,\n"
        "                    \"message\": \"That confirmation expired, was already used, or \"\n"
        "                               \"belongs to another account.\",",
        "computes the state and then throws it away, restoring the one-size sentence "
        "behind a correct-looking reason field",
    ),
    "gate_before_state": (
        SERVICE,
        "            dead = undx_architecture.approval_state(cur, int(user_id), token) if token else \"\"\n"
        "            if dead in _DEAD_APPROVAL_STATES:",
        "            dead = undx_architecture.approval_state(cur, int(user_id), token) if token else \"\"\n"
        "            if False and dead in _DEAD_APPROVAL_STATES:",
        "puts the legacy executor's kill switch back in front of the dead-approval "
        "answer, which is where it was — and since that switch is off wherever the "
        "agent runs, it makes the whole batch unreachable in the shipping configuration",
    ),
    "stranger_gets_a_named_state": (
        SERVICE,
        "_DEAD_APPROVAL_STATES = frozenset({\n"
        "    undx_architecture.APPROVAL_CONSUMED,",
        "_DEAD_APPROVAL_STATES = frozenset({\n"
        "    undx_architecture.APPROVAL_UNKNOWN,\n"
        "    undx_architecture.APPROVAL_CONSUMED,",
        "answers an unknown token by name at the pre-gate branch, so a stranger "
        "presenting a leaked token can tell it apart from one they made up",
    ),
    "state_on_the_success_path": (
        SERVICE,
        "        if agent_outcome is not None:\n            conn.commit()",
        "        if agent_outcome is not None:\n"
        "            undx_architecture.approval_state(cur, int(user_id), token)\n"
        "            conn.commit()",
        "runs the diagnostic read on every successful redemption, putting a query that "
        "exists only to explain a failure into the hot path",
    ),
}


#: Where the untouched source is parked while a mutation is applied.
#:
#: A ``finally`` restores the file when this script exits. It does not restore it when
#: the process is *killed* — and this script was in fact killed mid-run by a harness
#: timeout, leaving ``live_reads_as_spent`` applied to a working tree that then looked
#: clean at a glance and failed three tests for no visible reason. Ten minutes went into
#: finding that. The sidecar makes the next occurrence self-healing: the original text is
#: on disk before the mutation is, so any later run can put it back without knowing what
#: happened, and :func:`heal` runs before anything else.
#:
#: Emptied rather than deleted when a run finishes cleanly. This file lives on a mount
#: whose directory belongs to another account, so ``unlink`` raises ``PermissionError``
#: — the same constraint that put the commit and push into a Finder script. Truncation
#: needs only write permission on the file, which is exactly what creating it proved.
PARKED = ROOT / "outputs" / ".mutate20-original"


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


def run_suite() -> tuple[bool, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests/undx_agent", "-t", ".", "-q"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return proc.returncode == 0, (proc.stderr or proc.stdout).strip().splitlines()[-1:]


def apply_one(name: str) -> bool:
    path, old, new, destroys = MUTATIONS[name]
    source = path.read_text()
    if source.count(old) != 1:
        print(f"  SKIPPED  {name}: anchor matched {source.count(old)} times, not once")
        print(f"           (the file moved under this script; fix the anchor)")
        return False
    park(path, source)
    path.write_text(source.replace(old, new, 1))
    try:
        passed, tail = run_suite()
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


def main() -> int:
    heal()
    wanted = sys.argv[1:] or list(MUTATIONS)
    unknown = [name for name in wanted if name not in MUTATIONS]
    if unknown:
        print(f"unknown mode(s): {', '.join(unknown)}")
        print(f"available: {', '.join(MUTATIONS)}")
        return 2

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
