"""Does the replay suite actually hold the replay defence up?

A green suite proves nothing on its own. Every assertion in
`tests/test_federated_replay.py` could be satisfied by code that does not
defend anything, and the only way to find out is to break the defence on
purpose and check that the suite notices.

Each mutation below removes exactly one load-bearing piece:

  * the call site            -- the route stops consulting the ledger at all
  * the refusal              -- the ledger notices the replay and says nothing
  * the UNIQUE constraint    -- the database stops serialising the race
  * the purge boundary       -- retention deletes rows that still matter

A mutation that leaves the suite green is a hole in the suite, not a
harmless rewrite. Restores from an in-memory copy at the end of every
attempt, including on crash, because `services/federated_replay.py` is
untracked and `git checkout` cannot bring it back.

Run from the repository root:

    PYTHONPATH=. .venv/bin/python scripts/prove_replay_defence_is_load_bearing.py
"""

from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPLAY = os.path.join(ROOT, "services", "federated_replay.py")
BOT = os.path.join(ROOT, "bot.py")
SUITE = "tests/test_federated_replay.py"

PYTHON = os.environ.get(
    "REPLAY_MUTATION_PYTHON", "/Users/hmcherie/Desktop/CoinPilotX/.venv/bin/python"
)

# (name, file, what it breaks, old, new)
MUTATIONS = [
    (
        "the route never consults the ledger",
        BOT,
        "federated_native_profile stops spending the credential, which is the "
        "defect as it exists on main today",
        "        federated_replay.consume(\n"
        "            provider, assertion, expires_at_epoch=claims.get(\"exp\"),\n"
        "        )\n",
        "        pass  # MUTANT: the credential is never spent\n",
    ),
    (
        "a detected replay is not refused",
        REPLAY,
        "the ledger sees the conflict, records nothing new, and returns "
        "success anyway -- a silent single-use that is not single-use",
        "    if inserted != 1:\n        raise ReplayError(\"credential_replayed\", provider)",
        "    if inserted != 1:\n        pass  # MUTANT: conflict observed and ignored",
    ),
    (
        "the UNIQUE constraint is dropped",
        REPLAY,
        "the guarantee moves out of the database, so two concurrent inserts "
        "can both affect a row and both win",
        "                credential_hash TEXT NOT NULL UNIQUE,",
        "                credential_hash TEXT NOT NULL,  -- MUTANT: no UNIQUE",
    ),
    (
        "the ledger keys on something coarser than the credential",
        REPLAY,
        "keying on the member instead of the exact bytes -- which refuses a "
        "member's second genuine sign-in, the likelier and more damaging bug",
        "    return hashlib.sha256((credential or \"\").encode(\"utf-8\")).hexdigest()",
        "    return hashlib.sha256(b\"same-for-every-credential\").hexdigest()  # MUTANT",
    ),
    (
        "retention is unbounded",
        REPLAY,
        "a token claiming a far-future `exp` pins its row forever, so the "
        "table grows without a ceiling",
        "MAX_RETENTION_SECONDS = 24 * 3600",
        "MAX_RETENTION_SECONDS = 400 * 24 * 3600  # MUTANT: no real ceiling",
    ),
    (
        "an empty credential is consumed instead of refused",
        REPLAY,
        "one row for the digest of \"\" then consumes every later blank, so the "
        "first malformed request poisons all of them",
        "    if not credential:\n        raise ReplayError(\"missing_credential\")",
        "    if not credential:\n        credential = \"\"  # MUTANT: consumed, not refused",
    ),
    (
        "a storage failure is swallowed into a success",
        BOT,
        "the ledger cannot answer, so the route admits the credential anyway -- "
        "which turns a database outage into an unlimited replay window while "
        "leaving every replay test green, because in those the ledger works",
        "    except federated_replay.ReplayError as exc:\n",
        "    except RuntimeError:\n"
        "        return profile, None  # MUTANT: an outage admits everybody\n"
        "    except federated_replay.ReplayError as exc:\n",
    ),
    (
        "retention forgets rows that still matter",
        REPLAY,
        "the purge deletes on the wrong side of the boundary, erasing live "
        "credentials and making them replayable inside their own lifetime",
        "    cur = conn.execute(f\"DELETE FROM {TABLE} WHERE expires_at < ?\", (cutoff,))",
        "    cur = conn.execute(f\"DELETE FROM {TABLE} WHERE expires_at > ?\", (cutoff,))  # MUTANT",
    ),
]


def _run_suite() -> tuple[bool, str]:
    env = dict(os.environ, PYTHONPATH=ROOT)
    proc = subprocess.run(
        [PYTHON, "-m", "pytest", SUITE, "-q", "-p", "no:randomly", "--no-header", "-x"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=900,
    )
    tail = [line for line in proc.stdout.splitlines() if line.strip()]
    return proc.returncode == 0, " | ".join(tail[-3:])


def main() -> int:
    baseline_green, baseline_tail = _run_suite()
    print(f"baseline: {'GREEN' if baseline_green else 'RED'} -- {baseline_tail}")
    if not baseline_green:
        print("\nthe suite is already red; fix that before asking what it can detect")
        return 2

    survivors = []
    for name, path, breaks, old, new in MUTATIONS:
        with open(path, "r", encoding="utf-8") as handle:
            original = handle.read()
        occurrences = original.count(old)
        if occurrences != 1:
            print(f"\n{name}\n  SKIPPED: anchor matched {occurrences} times, not once")
            survivors.append((name, "anchor missed"))
            continue
        try:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(original.replace(old, new, 1))
            green, tail = _run_suite()
        finally:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(original)

        verdict = "SURVIVED" if green else "caught"
        print(f"\n{name}")
        print(f"  breaks: {breaks}")
        print(f"  suite:  {'GREEN -- mutation undetected' if green else 'RED'}")
        print(f"          {tail}")
        print(f"  {verdict}")
        if green:
            survivors.append((name, tail))

    restored_green, restored_tail = _run_suite()
    print(f"\nrestored: {'GREEN' if restored_green else 'RED'} -- {restored_tail}")

    print("\n" + "=" * 68)
    if survivors:
        print(f"{len(survivors)} of {len(MUTATIONS)} mutations survived:")
        for name, tail in survivors:
            print(f"  - {name}  ({tail})")
        print("the suite does not hold those pieces up")
        return 1
    if not restored_green:
        print("every mutation was caught, but the restore left the suite red")
        return 3
    print(f"all {len(MUTATIONS)} mutations turned the suite red, and it is green again.")
    print("the replay defence is load-bearing under this suite.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
