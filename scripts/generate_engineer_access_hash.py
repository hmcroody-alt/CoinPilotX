#!/usr/bin/env python3
"""Generate the ENGINEER_ACCESS_PASSCODE_HASH value for Railway.

The passcode is read from an interactive no-echo prompt, never from argv — a
command-line argument would land in shell history, in `ps` output, and in any
process-listing telemetry on the box.

Usage:
    python3 scripts/generate_engineer_access_hash.py

Paste the printed value into the Railway variable ENGINEER_ACCESS_PASSCODE_HASH.
Nothing is written to disk by this script.
"""

import getpass
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.business_os.engineer_access import (  # noqa: E402
    PASSCODE_LENGTH,
    PBKDF2_ITERATIONS,
    hash_passcode,
    passcode_is_well_formed,
    verify_passcode,
)


def main() -> int:
    entered = getpass.getpass("Engineer passcode ({} digits, input hidden): ".format(PASSCODE_LENGTH))
    if not passcode_is_well_formed(entered):
        print("Passcode must be exactly {} digits.".format(PASSCODE_LENGTH), file=sys.stderr)
        return 1
    if getpass.getpass("Confirm passcode: ") != entered:
        print("Entries did not match.", file=sys.stderr)
        return 1

    encoded = hash_passcode(entered, iterations=PBKDF2_ITERATIONS)
    # Prove the stored value actually validates the passcode before the operator
    # commits it to the environment; a typo here locks the owner out of the gate.
    if not verify_passcode(entered, encoded):
        print("Self-check failed — do not use this value.", file=sys.stderr)
        return 2

    del entered
    print("\nSet this in Railway (and in your local .env for development):\n")
    print("ENGINEER_ACCESS_PASSCODE_HASH={}".format(encoded))
    print("\nAlso required:")
    print("ENGINEER_ACCESS_ENABLED=true")
    print("ENGINEER_ACCESS_GRANT_SECRET=<output of: python3 -c \"import secrets;print(secrets.token_urlsafe(48))\">")
    print("PULSESOC_BUSINESS_OS_OWNER_USER_IDS=<your immutable numeric user_id>")
    print("\nThe raw passcode was not written to disk and is not recoverable from the hash.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
