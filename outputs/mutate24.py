#!/usr/bin/env python3
"""Break Batch 24 on purpose, one property at a time, and check the suite notices.

Batch 24 is about traceability, which is the easiest kind of property to test badly.
A test that asserts "the response has a ``correlation_id``" passes against a stamp that
overwrites the id the payload already had, against a stamp minted separately from the
one the audit row uses, and against a log line that leaks the bearer token alongside it.
All three of those are worse than the defect being fixed.

So the modes below are chosen to fail exactly those cheap fixes. Two of them —
``refusal_log_carries_the_token`` and ``every_answer_is_logged_as_a_refusal`` — do not
restore the original defect at all. They restore the *mistakes the fix invites*, which
is the thing a mutation harness is actually for.

A mode that reports SURVIVED names a claim these tests are not enforcing, which is the
same shape of hole Batch 23 was.

    python3 outputs/mutate24.py                       # every mode
    python3 outputs/mutate24.py audit_row_gets_its_own_id   # one mode

``MUTATE24_BASELINE=0`` skips the unmutated run, for when the baseline has already been
established in this sitting.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "pulse_ai_service.py"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"

SUITE = "tests.undx_agent.test_confirm_trace"

#: The stamp. One line, and the whole of the response half of this batch.
STAMP = '    answer.setdefault("correlation_id", correlation_id)'

#: The guard that decides whether a refusal is logged.
REFUSAL_GUARD = '    if not answer.get("ok"):'

#: The refusal record, anchored on its format string plus its first argument, because
#: the format string alone would also match a mutation of the arguments below it.
REFUSAL_LOG = """        LOGGER.info(
            "UNDX_CONFIRM_REFUSED user_id=%s correlation_id=%s error=%s reason=%s http_status=%s",
            int(user_id), answer.get("correlation_id"), answer.get("error") or "","""

#: The id handed to the audit write. This is the original defect: a second random id for
#: the audit row of an operation that already had one.
AUDIT_ID = """                 "observed_value": actual, "proposed_value": proposed},"""

#: The resolver, and the precedence that is the entire point of it.
RESOLVER = '        return str(payload.get("correlation_id") or payload.get("trace_id") or fallback)'

#: The argument the timing line passes. Anchored with its trailing comma and newline so
#: it cannot match the identical call in the warning twelve lines below.
TIMING_ARG = """            _payload_trace(payload, trace_id),
        )
        if isinstance(payload, dict):"""

#: name -> (file, old, new, the test that must go red, what the mutation destroys)
MUTATIONS: dict[str, tuple[pathlib.Path, str, str, str, str]] = {
    "answer_is_not_stamped": (
        SERVICE,
        STAMP,
        "    pass",
        "test_the_answer_that_says_go_and_check_carries_the_id_to_check_by",
        "removes the stamp — the original defect, restored exactly. Seven of the nine "
        "return paths lose their id again, including the one whose own text tells the "
        "person to go and check where things stand",
    ),
    "stamp_overwrites_the_payloads_own_id": (
        SERVICE,
        STAMP,
        '    answer["correlation_id"] = correlation_id',
        "test_a_path_that_names_its_own_id_keeps_it",
        "turns setdefault into assignment. Every answer still has an id, so a test that "
        "only checks presence stays green — but a payload naming a downstream trace has "
        "that trace destroyed, and the pointer to whatever it described is gone",
    ),
    "stamp_mints_a_second_id": (
        SERVICE,
        STAMP,
        '    answer.setdefault("correlation_id", _trace())',
        "test_the_body_is_still_given_the_id_the_answer_is_stamped_with",
        "stamps a freshly minted id instead of the one the body used. Every answer has "
        "an id, and it matches nothing — not the gateway call, not the audit row, not "
        "the log line the body emitted. This is the defect being fixed, moved up a "
        "layer and made harder to see",
    ),
    "refusal_is_not_logged": (
        SERVICE,
        REFUSAL_GUARD,
        "    if False:",
        "test_a_refusal_emits_exactly_one_log_line",
        "stops recording refusals. The response is traceable and there is nothing on "
        "the server to trace it to, which is half the defect left standing",
    ),
    "every_answer_is_logged_as_a_refusal": (
        SERVICE,
        REFUSAL_GUARD,
        "    if True:",
        "test_a_successful_confirmation_is_not_logged_as_a_refusal",
        "logs every answer as a refusal. Every rejection is now recorded, so the "
        "cheapest reading of this batch is satisfied — and REFUSED next to a successful "
        "write is a false record, which is worse than no record",
    ),
    "refusal_log_carries_the_token": (
        SERVICE,
        REFUSAL_LOG,
        """        LOGGER.info(
            "UNDX_CONFIRM_REFUSED user_id=%s correlation_id=%s error=%s reason=%s http_status=%s token=%s",
            int(user_id), answer.get("correlation_id"), answer.get("error") or "",
            payload.get("confirmation_token") if isinstance(payload, dict) else "","""
        ,
        "test_the_refusal_log_never_contains_the_token",
        "writes the bearer token into the log line. This is not the old defect — it is "
        "the defect this batch's own fix invites, because the token is the most "
        "obviously useful thing in scope when somebody decides a refusal log is too "
        "thin. A pending approval token redeems a write for anyone holding it",
    ),
    "audit_row_gets_its_own_id": (
        SERVICE,
        AUDIT_ID,
        """                 "observed_value": actual, "proposed_value": proposed},
                correlation_id=_trace(),  # mutation: a second random id""",
        "test_the_audit_row_carries_the_id_the_person_was_given",
        "gives the audit row a second random id. The durable record of the write cannot "
        "be joined to the request that caused it or to the answer the person was given, "
        "so the one row that survives the log retention window points at nothing",
    ),
    "timing_line_reads_the_old_key": (
        ROUTES,
        TIMING_ARG,
        """            payload.get("trace_id") if isinstance(payload, dict) else trace_id,
        )
        if isinstance(payload, dict):""",
        "test_the_timing_line_no_longer_reads_a_key_the_services_never_emit",
        "puts the timing line back on a key the services never emit. The helper still "
        "exists and is still correct and is still called by the warning below, so the "
        "resolver tests all pass — and the one log line that runs on every request to "
        "the pulse_ai endpoints among the 88 routed through _timed_json reads "
        "trace_id=None again",
    ),
    "resolver_prefers_the_key_nobody_emits": (
        ROUTES,
        RESOLVER,
        '        return str(payload.get("trace_id") or payload.get("correlation_id") or fallback)',
        "test_correlation_id_wins_when_a_payload_carries_both",
        "swaps the precedence. Correct for the handful of payloads carrying only the "
        "old name and wrong for everything the services actually emit — the failure is "
        "invisible except on payloads carrying both, which is where it matters",
    ),
    "resolver_drops_the_routes_fallback": (
        ROUTES,
        RESOLVER,
        '        return str(payload.get("correlation_id") or payload.get("trace_id"))',
        "test_a_payload_carrying_neither_falls_back_to_the_id_the_route_minted",
        "drops the route's own id from the chain, so a payload carrying neither key "
        "logs the string 'None' — the literal defect, in a smaller blast radius, and "
        "the route's freshly computed id goes back to being dead code",
    ),
}


#: Where the untouched source is parked while a mutation is applied. Same sidecar as
#: mutate20/21/22/23, for the same reason: a run killed before its ``finally`` once left
#: a mutation applied to a tree that looked clean at a glance. Emptied rather than
#: deleted, because ``unlink`` raises ``PermissionError`` on this mount.
PARKED = ROOT / "outputs" / ".mutate24-original"


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
    log = pathlib.Path(tempfile.gettempdir()) / "mutate24-unittest.log"
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

    if os.environ.get("MUTATE24_BASELINE", "1") == "1":
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
