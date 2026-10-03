#!/usr/bin/env python3
"""Prove the native federated tests fail when the protection is removed.

`tests/test_federated_native_auth.py` went green the first time it was run
against finished code, which is the situation where a suite most deserves to be
doubted: passing tests are evidence the code satisfies them, not evidence they
would notice if the code stopped protecting anything.

So each mutation below reintroduces one real vulnerability -- at runtime, by
patching the loaded module, never by editing source -- and the named test must
go red. A MISSED line means that test's name is a claim the test does not
actually hold.

Run from the repo root. Exit 0 means every mutation was caught.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SUITE = "tests/test_federated_native_auth.py"

#: Shared by the mutations that rewrite the request body. The consent check and
#: the subject check are both inline in their route rather than behind a seam, so
#: the honest way to model them being absent is to make the request arrive as if
#: it had already satisfied them. A before_request hook that primes Flask's
#: parsed-JSON cache does that without touching the route.
_INJECT = """
import bot

def _inject(extra):
    def hook():
        if "/auth/federated" not in bot.request.path:
            return None
        body = bot.request.get_json(silent=True) or {}
        body = dict(body)
        body.update(extra)
        # Flask caches the parsed body per request; priming it is how the route
        # comes to read something the client never sent.
        bot.request._cached_json = (body, body)
        return None
    return hook
"""

#: (label, test node id after the suite path, conftest-style patch source)
MUTATIONS = [
    (
        "a matching verified email is linked instead of refused",
        "AMatchingEmailIsStillNotAnAuthorisation::"
        "test_a_verified_email_matching_an_account_is_refused_not_linked",
        """
import bot
from services import external_identity

_real = external_identity.resolve

def mutant_resolve(provider, profile, conn=None):
    decision = _real(provider, profile, conn)
    if decision.get("decision") != "link_required":
        return decision
    # THE vulnerability: the provider asserted an address this platform already
    # knows, and that is treated as proof of the account behind it.
    owners = external_identity.accounts_matching_email(
        external_identity.normalize_email(profile.get("email")), conn
    )
    if not owners:
        return decision
    return {"decision": "sign_in", "reason": "email_matched",
            "user_id": owners[0], "identity": {"user_id": owners[0]}}

external_identity.resolve = mutant_resolve
bot.external_identity.resolve = mutant_resolve
""",
    ),
    (
        "the email collision that appears late is linked at the signup step",
        "AMatchingEmailIsStillNotAnAuthorisation::"
        "test_the_signup_step_re_asks_and_refuses_a_collision_that_appeared_late",
        """
import bot
from services import external_identity

_real = external_identity.resolve

def mutant_resolve(provider, profile, conn=None):
    # Models the signup step trusting the ticket's "this subject is new"
    # instead of re-resolving: a collision that appeared after minting is not
    # seen, so the account is created on top of somebody else's address.
    decision = _real(provider, profile, conn)
    if decision.get("decision") == "link_required":
        return {"decision": "create", "reason": "new_member", "user_id": 0,
                "email": external_identity.normalize_email(profile.get("email")),
                "email_verified": bool(profile.get("email_verified"))}
    return decision

external_identity.resolve = mutant_resolve
bot.external_identity.resolve = mutant_resolve
""",
    ),
    (
        "a banned or suspended member is admitted anyway",
        "TheGatesAreNotOptionalOnAPhone::test_a_suspended_account_is_refused_a_native_session",
        """
import bot
bot.account_login_restriction_message = lambda user: ""
""",
    ),
    (
        "an outstanding legal document does not pause the sign-in",
        "TheGatesAreNotOptionalOnAPhone::test_an_outstanding_document_pauses_the_sign_in",
        """
import bot
bot.mobile_legal_acceptance_challenge = lambda user_id: None
""",
    ),
    (
        "the agreement the signup collected is never recorded",
        "TheGatesAreNotOptionalOnAPhone::test_a_native_signup_records_the_agreement_it_collected",
        """
import bot
from services import legal_acceptance
bot.legal_acceptance.record = lambda *a, **k: None
legal_acceptance.record = lambda *a, **k: None
""",
    ),
    (
        "consent is not required to finish a signup",
        "TheGatesAreNotOptionalOnAPhone::test_consent_is_required_and_is_not_carried_by_the_ticket",
        _INJECT + """
bot.app.before_request(_inject({"age_confirmed": True, "terms_accepted": True}))
""",
    ),
    (
        "the client may name the subject it wants to be",
        "TheAssertionIsTheOnlyThingTrusted::test_a_client_supplied_subject_is_ignored",
        """
import bot

_real = bot.federated_native_profile

def mutant(provider, payload):
    profile, error = _real(provider, payload)
    if profile and payload.get("sub"):
        # Models the request body being allowed to override the signed token.
        profile = dict(profile)
        profile["subject"] = str(payload.get("sub"))
        if payload.get("email"):
            profile["email"] = str(payload.get("email"))
    return profile, error

bot.federated_native_profile = mutant
""",
    ),
    (
        "a signup ticket's signature is not checked",
        "TheAssertionIsTheOnlyThingTrusted::"
        "test_a_well_formed_signup_ticket_with_a_wrong_signature_creates_nothing",
        """
import base64
import json
import bot

def mutant_read(raw):
    # Decodes the payload and believes it. This is the whole of what a signature
    # is for: without it the ticket is a client-authored claim of identity.
    try:
        body = str(raw or "").split(".")[0]
        padded = body + "=" * (-len(body) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception:
        return {}

bot.read_federated_signup_ticket = mutant_read
""",
    ),
    (
        "a signup ticket never expires",
        "TheAssertionIsTheOnlyThingTrusted::test_an_expired_signup_ticket_creates_nothing",
        """
import bot

_real = bot.read_federated_signup_ticket

def mutant_read(raw):
    claims = _real(raw)
    if claims:
        return claims
    # Re-reads with the clock wound back, which is what ignoring `exp` amounts
    # to: a ticket that verified once verifies forever.
    import time
    real_time = time.time
    try:
        time.time = lambda: 0.0
        return _real(raw)
    finally:
        time.time = real_time

bot.read_federated_signup_ticket = mutant_read
""",
    ),
    (
        "a ticket minted for another purpose is spendable as a signup",
        "TheAssertionIsTheOnlyThingTrusted::"
        "test_a_validly_signed_ticket_of_another_purpose_is_refused",
        """
import base64
import json
import bot

_real = bot.read_federated_signup_ticket
_real_purpose = bot.FEDERATED_SIGNUP_TICKET_PURPOSE

def mutant_read(raw):
    # Models the purpose field not being among the things verified: whatever
    # purpose the ticket declares is accepted, so any ticket this server ever
    # signed with this key becomes spendable as a signup. The signature, expiry
    # and provider checks are all left intact -- re-reading through the real
    # function with the constant temporarily set to the ticket's own declared
    # purpose removes exactly one check and nothing else.
    claims = _real(raw)
    if claims:
        return claims
    try:
        body = str(raw or "").partition(".")[0]
        padded = body + "=" * (-len(body) % 4)
        declared = json.loads(base64.urlsafe_b64decode(padded.encode()))["p"]
    except Exception:
        return {}
    bot.FEDERATED_SIGNUP_TICKET_PURPOSE = declared
    try:
        return _real(raw)
    finally:
        bot.FEDERATED_SIGNUP_TICKET_PURPOSE = _real_purpose

bot.read_federated_signup_ticket = mutant_read
""",
    ),
    (
        "an unconfigured native audience falls back to the web audience",
        "TheAssertionIsTheOnlyThingTrusted::"
        "test_a_build_with_no_native_audience_configured_refuses_everything",
        """
import bot

_real = bot.federated_native_profile

def mutant(provider, payload):
    # Models the fail-closed guard being absent. Without it `audiences()` still
    # contains the web client id, so a token minted for the website verifies
    # when replayed from a phone.
    from services import google_identity
    real_native = google_identity.native_client_ids
    google_identity.native_client_ids = lambda: ["placeholder"]
    try:
        return _real(provider, payload)
    finally:
        google_identity.native_client_ids = real_native

bot.federated_native_profile = mutant
""",
    ),
    (
        "one subject may be recorded against two accounts",
        "TappingTwiceDoesNotCreateTwoAccounts::test_the_same_ticket_spent_twice_yields_one_account",
        """
import bot
from services import external_identity

def mutant_lookup(provider, subject, conn=None):
    # Models the subject lookup missing on the second pass, which is how one
    # provider account becomes two PulseSoc accounts: the retry is treated as a
    # first sight of the same identity.
    return None

external_identity.lookup = mutant_lookup
bot.external_identity.lookup = mutant_lookup
""",
    ),
]


def run_one(label: str, test: str, patch: str) -> bool:
    """True if the mutation was caught (the named test failed)."""

    directory = tempfile.mkdtemp(prefix="mutate_native_")
    with open(os.path.join(directory, "conftest.py"), "w") as handle:
        handle.write(patch)

    # The patch has to land after `bot` is imported by the suite and before the
    # test runs. A plugin loaded with -p does exactly that: pytest imports it
    # after collection, so `import bot` inside it finds the module the suite
    # already set up against its temp database.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = directory + os.pathsep + environment.get("PYTHONPATH", "")

    completed = subprocess.run(
        [sys.executable, "-m", "pytest", f"{SUITE}::{test}",
         "-q", "-p", "no:cacheprovider", "-p", "no:warnings", "-p", "conftest"],
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
    print(f"mutating {SUITE}\n")
    results = [run_one(*mutation) for mutation in MUTATIONS]
    missed = results.count(False)
    print(f"\n{len(results) - missed}/{len(results)} mutations caught")
    return 1 if missed else 0


if __name__ == "__main__":
    sys.exit(main())
