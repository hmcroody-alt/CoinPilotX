#!/usr/bin/env python3
"""Prove each CJ safety control is load-bearing by removing it and expecting red.

A green suite says the tests pass. It does not say the tests would notice if the
control they are named after were deleted. These are different claims, and only
the second one is worth anything on a provider integration where the failure
mode is CJ disabling the account.

So this script takes each control CJ's limits actually require, edits it out of
the source, runs the suites that claim to cover it, and asserts they go red. A
mutation that survives is reported as SURVIVED, and that is a failure of this
script's exit code, because a control nothing detects the absence of is a
comment with a syntax highlighter.

Two things this deliberately does not claim:

* It is not a general mutation-testing sweep. Each entry is a specific control
  named in the integration requirements, hand-written so the mutated code is
  something a person could plausibly write on a bad day -- a relaxed bound, a
  dropped scope check -- rather than a random operator flip.
* Killing the mutant does not prove the control is correct, only that it is
  observed. A test that pins the wrong behaviour will still go red when the
  wrong behaviour is removed.

Usage:  python3 scripts/protection/cj_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
QUOTA = "services/business_os/suppliers/quota.py"
CONNECTIONS = "services/business_os/suppliers/connections.py"
POLICY = "services/business_os/suppliers/policy.py"

# Each mutation: what control, what a lapse would look like in source, and which
# suites are supposed to be watching. Suites are listed narrowly on purpose --
# naming the whole directory would let an unrelated test take the credit for
# killing a mutant, which is the same blind spot one layer up.
MUTATIONS = [
    dict(
        name="account-cap-3-to-4",
        control="CJ allows three accounts per egress IP; quota.py refuses the fourth.",
        path=QUOTA,
        old="            if count >= 3:",
        new="            if count >= 4:",
        suites=["tests/business_os/test_cj_quota.py"],
    ),
    dict(
        name="egress-pacing-writer-disabled",
        control="Every admission advances the shared egress clock by EGRESS_MIN_INTERVAL.",
        path=QUOTA,
        old='(now + EGRESS_MIN_INTERVAL, self.egress_group))',
        new='(now, self.egress_group))',
        suites=["tests/business_os/test_cj_quota.py"],
    ),
    dict(
        name="limit-by-account-not-egress-ip",
        control=(
            "Admission consults the egress row, not just the account row. CJ counts "
            "calls per outbound IP, so per-token pacing alone lets three accounts "
            "sum past ten calls a second on one address."
        ),
        path=QUOTA,
        old='            retry = max(row["next_at"], row["blocked_until"], egress["next_at"], egress["blocked_until"]) - now',
        new='            retry = max(row["next_at"], row["blocked_until"]) - now',
        suites=["tests/business_os/test_cj_quota.py"],
    ),
    dict(
        name="cross-tenant-merchant-check-dropped",
        control="A connection row is refused unless it belongs to the authorized merchant.",
        path=CONNECTIONS,
        old='    if row is None or (merchant_id is not None and row["merchant_id"] != merchant_id):',
        new="    if row is None:",
        suites=["tests/business_os/test_cj_connections.py",
                "tests/business_os/test_cj_store_session_authority.py"],
    ),
    dict(
        name="secret-allowlist-replaced-by-passthrough",
        control=(
            "_public serializes an explicit allowlist, so a new DB column -- including "
            "credential_reference -- can never reach a response by being added."
        ),
        path=CONNECTIONS,
        old='    out = {key: row.get(key) for key in ("id", "merchant_id", "business_id", "store_id",',
        new='    out = dict(row) | {key: row.get(key) for key in ("id", "merchant_id", "business_id", "store_id",',
        suites=["tests/business_os/test_cj_connections.py",
                "tests/business_os/test_cj_routes.py"],
    ),
    dict(
        name="sandbox-orders-counted-as-real-activity",
        control=(
            "Sandbox orders do not reset CJ's thirty-day inactivity clock. Counting "
            "them would report a healthy connection right up until CJ disables it."
        ),
        path="services/business_os/suppliers/fulfillment.py",
        old="        if type(sandbox) is int and sandbox == 1:\n            continue",
        new="        pass",
        suites=["tests/business_os/test_cj_connections.py",
                "tests/business_os/test_cj_routes.py"],
    ),
    dict(
        name="inactivity-estimate-claims-false-precision",
        control=(
            "The countdown admits it may be late whenever it counts from connection "
            "creation, because CJ's clock may have started before ours."
        ),
        path=CONNECTIONS,
        old='        reference, may_be_late = "connection_created", True',
        new='        reference, may_be_late = "connection_created", False',
        suites=["tests/business_os/test_cj_connections.py",
                "tests/business_os/test_cj_routes.py"],
    ),
    dict(
        name="sandbox-flag-requirement-removed",
        control=(
            "CJ has no account-level sandbox switch; safety rests entirely on "
            "isSandbox=1 being present and integral on every order payload."
        ),
        path=POLICY,
        old='        raise SupplierError("sandbox_flag_required", http_status=400)',
        new="        pass",
        suites=["tests/business_os/test_cj_policy_gates.py",
                "tests/business_os/test_cj_fulfillment.py"],
    ),
]


def run_suites(suites, verbose):
    """Red if any suite fails. One process per file -- these suites share global
    DB state and batching them produces failures that belong to the batching."""
    for suite in suites:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "-x"],
            cwd=ROOT, capture_output=True, text=True,
            env={**__import__("os").environ, "PYTHONPATH": str(ROOT)},
        )
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {proc.stdout.strip().splitlines()[-1:]}")
        if proc.returncode != 0:
            return False, suite, proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    return True, None, ""


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    print("Baseline: suites must be green before a mutation means anything.\n")
    baseline = sorted({suite for m in MUTATIONS for suite in m["suites"]})
    green, suite, tail = run_suites(baseline, args.verbose)
    if not green:
        print(f"ABORT: {suite} is already failing ({tail}). Fix that first -- a red\n"
              f"baseline makes every mutation look killed.")
        return 2
    print(f"  {len(baseline)} suites green.\n")

    survivors = []
    for mutation in MUTATIONS:
        path = ROOT / mutation["path"]
        original = path.read_text()
        if mutation["old"] not in original:
            print(f"  DRIFTED  {mutation['name']}")
            print(f"           anchor no longer present in {mutation['path']}.")
            print(f"           The control may have moved or been rewritten; this entry\n"
                  f"           needs re-pointing before it can claim anything.")
            survivors.append(mutation["name"])
            continue
        if original.count(mutation["old"]) != 1:
            print(f"  AMBIGUOUS {mutation['name']}: anchor appears "
                  f"{original.count(mutation['old'])} times.")
            survivors.append(mutation["name"])
            continue
        path.write_text(original.replace(mutation["old"], mutation["new"]))
        try:
            still_green, _, _ = run_suites(mutation["suites"], args.verbose)
        finally:
            path.write_text(original)
        if still_green:
            print(f"  SURVIVED {mutation['name']}")
            print(f"           {mutation['control']}")
            print(f"           Removing it changed no test result. Nothing observes this.")
            survivors.append(mutation["name"])
        else:
            print(f"  killed   {mutation['name']}")

    print()
    if survivors:
        print(f"FAIL: {len(survivors)} of {len(MUTATIONS)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
