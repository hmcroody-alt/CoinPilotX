#!/usr/bin/env python3
"""Prove the re-assertion tests fail when the protection is removed.

A green suite is evidence that the code passes its tests, not that the tests
would notice if the code stopped protecting anything. So each mutation below
reintroduces one real vulnerability -- at runtime, by patching the loaded
module, never by editing source -- and the named test must go red.

Run from the repo root. Exit 0 means every mutation was caught.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUITE = "tests/test_federated_signin_routes.py"
CLASS = "AFederatedAccountCanStillBeDeleted"

#: (label, test name that must fail, source of a conftest-style patch)
MUTATIONS = [
    (
        "a confirmation is never required",
        "test_without_the_confirmation_the_same_request_is_refused",
        """
import bot
bot.consume_federated_reassertion = lambda user_id: True
""",
    ),
    (
        "any provider account may confirm, not only the linked one",
        "test_confirming_with_a_provider_account_that_is_not_this_members_does_nothing",
        """
import bot
from services import external_identity

def mutant_lookup(provider, subject, conn=None):
    # Models the identity check being absent: whatever subject the token
    # carries is reported as belonging to whoever is signed in.
    return {"user_id": bot.session.get("account_user_id"),
            "provider": provider, "subject": subject}

bot.external_identity.lookup = mutant_lookup
external_identity.lookup = mutant_lookup
""",
    ),
    (
        "a confirmation never goes stale",
        "test_a_day_old_confirmation_does_not_authorise",
        """
import bot
bot.FEDERATED_REASSERTION_TTL_SECONDS = 10 ** 9
""",
    ),
    (
        "a confirmation is not bound to the member it was minted for",
        "test_a_confirmation_minted_for_another_member_does_not_authorise",
        """
import bot

def mutant_consume(user_id):
    proof = bot.session.pop(bot.FEDERATED_REASSERTION_SESSION_KEY, None)
    return isinstance(proof, dict)

bot.consume_federated_reassertion = mutant_consume
""",
    ),
    (
        "a verify handshake may be completed with no session",
        "test_signing_out_mid_flow_grants_nothing_and_signs_nobody_in",
        """
import bot

_real = bot.require_account

def mutant_require_account(*a, **k):
    # Models the session re-check being absent at the completing GET: the
    # handshake's own `link_user_id` is trusted instead of the live session.
    user = _real(*a, **k)
    if user:
        return user
    row_user = getattr(bot, "_MUTANT_LAST_LINK_USER", 0)
    return bot.load_account_by_id(row_user) if row_user else None

def mutant_confirm(provider, row, profile):
    bot._MUTANT_LAST_LINK_USER = int(row.get("link_user_id") or 0)
    return _real_confirm(provider, row, profile)

_real_confirm = bot.federated_confirm_identity
bot.federated_confirm_identity = mutant_confirm
bot.require_account = mutant_require_account
""",
    ),
    (
        "a passwordless account still gets a required password field",
        "test_the_form_does_not_demand_a_password_the_member_cannot_have",
        """
import bot
_real = bot.render_account_page

def mutant(page, title, **context):
    context["has_password"] = True
    return _real(page, title, **context)

bot.render_account_page = mutant
""",
    ),
]


def run_one(label: str, test: str, patch: str) -> bool:
    """True if the mutation was caught (the named test failed)."""

    directory = tempfile.mkdtemp(prefix="mutate_reassert_")
    conftest = os.path.join(directory, "conftest.py")
    with open(conftest, "w") as handle:
        handle.write(patch)

    # The patch has to land after `bot` is imported by the suite and before the
    # test runs. A plugin loaded with -p does exactly that: pytest imports it
    # after collection, so `import bot` inside it finds the module the suite
    # already set up against its temp database.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = directory + os.pathsep + environment.get("PYTHONPATH", "")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", f"{SUITE}::{CLASS}::{test}",
         "-q", "-p", "no:cacheprovider", "-p", "conftest"],
        cwd=ROOT, env=environment, capture_output=True, text=True,
    )
    caught = completed.returncode != 0
    print(f"  {'caught ' if caught else 'MISSED '}  <- {label}")
    if not caught:
        print("    the test passed with the protection removed, so it is not")
        print("    testing what its name claims. Output:")
        for line in completed.stdout.strip().splitlines()[-12:]:
            print(f"      {line}")
    return caught


def main() -> int:
    print(f"mutating {SUITE}::{CLASS}\n")
    results = [run_one(*mutation) for mutation in MUTATIONS]
    missed = results.count(False)
    print(f"\n{len(results) - missed}/{len(results)} mutations caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
