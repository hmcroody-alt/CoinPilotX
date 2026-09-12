#!/usr/bin/env python3
"""Anti-vacuity battery for "what a vault failure tells the reader".

Why this battery exists
-----------------------
The defect it guards is the easiest one in this repository to re-introduce,
because putting it back *looks like cleanup*::

    def seal(bundle, **scope):
        try:
            ...                       # eight statements, six ways to fail
        except Exception:
            raise VaultError() from None

One handler, one answer: ``credential_vault_unavailable``, 503, "try again
later". A bundle with a misspelled field, a scope value that arrived as
``None``, a key that rotation retired, a tampered row and an empty keyring were
indistinguishable. Two of those are not outages, retrying them cannot help, and
the operator sent to check the deployment's keyring is looking in the wrong
place.

Worse, the blanket catch defeated the one diagnostic this package built for
exactly this problem. ``business_os_supplier_routes._origin`` reports the
innermost frame inside the supplier package so that many validators sharing one
opaque code can be told apart — and re-raising at the ``except`` line makes that
frame the ``raise`` statement. Measured before the fix: **six distinct causes,
two coordinates**, and a genuine outage byte-identical to a caller's typo.

So the invariants are:

* a call site's mistake answers 500 and is never sold to an operator as an
  outage,
* a stored row that will not open answers 409 and is never sold to a merchant as
  "try again",
* a real outage still answers 503, because the class that survived the split has
  to keep meaning what it meant,
* all three remain ``VaultError``, because nine assertions in the vault suite and
  two call sites in ``connections`` catch it,
* "no such reference" and "that reference is someone else's" stay
  indistinguishable, or the pair enumerates other tenants' credentials,
* and the failures are raised *where they happen*, so ``_origin`` separates them
  for free.

The last one is the load-bearing claim, and it is the one a passing test suite
would not otherwise pin: a test that only asserts ``pytest.raises(VaultError)``
is satisfied by the defect. Every mutation below is a plausible tidy-up that
puts some part of it back. A mutation that survives is reported as a hole in the
tests, not as a pass.

Usage::

    .venv/bin/python scripts/marketplace/vault_classification_mutation_battery.py

It never modifies the working tree. Each mutation runs in a throwaway overlay of
symlinks with the one mutated file materialised as a real copy — the same
mechanism as the other two batteries, imported rather than restated so they
cannot drift into different definitions of "applied".
"""

import os
import shutil
import subprocess
import sys
import tempfile

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from supplier_variant_mutation_battery import _apply, _overlay, REAL_DIRS  # noqa: E402

#: The vault suites live in `tests/business_os/`, which the shared overlay does
#: not deepen by default. Extended here rather than in the shared constant so the
#: other two batteries keep the exact tree they were measured against.
DIRS = REAL_DIRS | {"tests/business_os"}

VAULT_SUITE = "tests/business_os/test_cj_vault.py"
#: Deliberately not a judging suite, and the battery is how that was settled.
#: The compatibility claim was first written as "`connections` catches
#: `VaultError` in two places and must keep catching every class the split
#: produced", and the subclass mutation paired to it *survived*. It should have:
#: both of those assertions delete an environment variable, so both are genuine
#: outages that raise the base class, and neither can ever see a subclass. The
#: real stakeholder is this file's own suite, five of whose nine
#: `pytest.raises(VaultError)` assertions now catch `CredentialUnusable`. Pairing
#: is a claim about coverage; a survivor means the claim was wrong, and the
#: repair is to correct the claim rather than to quietly widen the judge.
CONNECTIONS_SUITE = "tests/business_os/test_cj_connections.py"

VAULT = "services/business_os/suppliers/vault.py"


#: (name, file, old, new[, suite]). ``old`` must appear exactly once in the file.
BLANKET_MUTATIONS: list[tuple] = [
    (
        "seal goes back to one handler for every failure",
        VAULT,
        [("    cipher = _aesgcm()\n"
          "    _validate(bundle, CredentialRequestInvalid)\n"
          "    aad = _scoped_aad(scope)\n"
          "    ring, active = _ring()\n"
          "    try:\n"
          "        nonce = os.urandom(12)",
          "    try:\n"
          "        cipher = _aesgcm()\n"
          "        _validate(bundle, CredentialRequestInvalid)\n"
          "        aad = _scoped_aad(scope)\n"
          "        ring, active = _ring()\n"
          "        nonce = os.urandom(12)")],
        None,
    ),
    (
        "unseal goes back to one handler for every failure",
        VAULT,
        [("    cipher = _aesgcm()\n"
          "    aad = _scoped_aad(scope)\n"
          "    ring, _ = _ring()\n"
          "    if not isinstance(ciphertext, str) or not ciphertext.startswith(\"v1.\"):\n"
          "        raise CredentialUnusable()",
          "    try:\n"
          "        cipher = _aesgcm()\n"
          "        aad = _scoped_aad(scope)\n"
          "        ring, _ = _ring()\n"
          "        if not isinstance(ciphertext, str) or not ciphertext.startswith(\"v1.\"):\n"
          "            raise CredentialUnusable()\n"
          "    except Exception:\n"
          "        raise VaultError() from None")],
        None,
    ),
    (
        "the retired-key check is folded back into the decrypt handler",
        VAULT,
        "    if key_id not in ring:\n"
        "        # Sealed under a key that has since left the ring. Rotation retires\n"
        "        # ciphertext; it does not resurrect it.\n"
        "        raise CredentialUnusable()\n",
        "",
    ),
    (
        "the base64 and json steps share the decrypt handler's coordinate",
        VAULT,
        "    try:\n"
        "        raw = base64.b64decode(ciphertext[3:], validate=True)\n"
        "    except Exception:\n"
        "        raise CredentialUnusable() from None\n"
        "    if len(raw) < 29:\n"
        "        raise CredentialUnusable()\n"
        "    try:\n"
        "        plain = cipher(ring[key_id]).decrypt(raw[:12], raw[12:], aad)",
        "    try:\n"
        "        raw = base64.b64decode(ciphertext[3:], validate=True)\n"
        "        if len(raw) < 29:\n"
        "            raise ValueError\n"
        "        plain = cipher(ring[key_id]).decrypt(raw[:12], raw[12:], aad)",
    ),
]

CLASSIFICATION_MUTATIONS: list[tuple] = [
    (
        "a malformed bundle is an outage again",
        VAULT,
        "    _validate(bundle, CredentialRequestInvalid)",
        "    _validate(bundle, VaultError)",
    ),
    (
        "_validate ignores what the caller said a violation means",
        VAULT,
        "    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:\n"
        "        raise error()",
        "    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:\n"
        "        raise VaultError()",
    ),
    (
        "a non-string scope value is an outage again",
        VAULT,
        "    if any(not isinstance(v, str) or not v for v in parts):\n"
        "        raise CredentialRequestInvalid()",
        "    if any(not isinstance(v, str) or not v for v in parts):\n"
        "        raise VaultError()",
    ),
    (
        "a misspelled scope keyword is an outage again",
        VAULT,
        "    except TypeError:\n        raise CredentialRequestInvalid() from None",
        "    except TypeError:\n        raise VaultError() from None",
    ),
    (
        "a bad fingerprint namespace is an outage again",
        VAULT,
        "        raise CredentialRequestInvalid()\n    material =",
        "        raise VaultError()\n    material =",
    ),
    (
        "a missing credential row is an outage again",
        VAULT,
        "    if row is None:\n        raise CredentialUnusable()",
        "    if row is None:\n        raise VaultError()",
    ),
    (
        "a credential held by another tenant is an outage again",
        VAULT,
        "            # The reference exists and belongs to a different scope. Answered\n"
        "            # identically to \"no such reference\" on the wire, deliberately.\n"
        "            raise CredentialUnusable()",
        "            raise VaultError()",
    ),
]

CONTRACT_MUTATIONS: list[tuple] = [
    (
        "an unusable credential tells the merchant to retry",
        VAULT,
        "    code = \"credential_unusable\"\n    http_status = 409",
        "    code = \"credential_unusable\"\n    http_status = 503",
    ),
    (
        "a call-site bug tells the operator to check the deployment",
        VAULT,
        "    code = \"credential_request_invalid\"\n    http_status = 500",
        "    code = \"credential_request_invalid\"\n    http_status = 503",
    ),
    (
        "a real outage stops saying retry",
        VAULT,
        "    code = \"credential_vault_unavailable\"\n    http_status = 503",
        "    code = \"credential_vault_unavailable\"\n    http_status = 500",
    ),
    (
        "the new classes stop being VaultError, so every existing caller misses them",
        VAULT,
        "class CredentialUnusable(VaultError):",
        "class CredentialUnusable(RuntimeError):",
    ),
    (
        "a request-invalid failure stops being VaultError",
        VAULT,
        "class CredentialRequestInvalid(VaultError):",
        "class CredentialRequestInvalid(RuntimeError):",
    ),
    (
        "load helpfully distinguishes 'not yours' from 'no such reference'",
        VAULT,
        "    if row is None:\n        raise CredentialUnusable()",
        "    if row is None:\n"
        "        if conn.execute(\"SELECT 1 FROM business_os_supplier_credential_vault \"\n"
        "                        \"WHERE credential_reference=?\",\n"
        "                        (credential_reference,)).fetchone():\n"
        "            raise CredentialRequestInvalid()\n"
        "        raise CredentialUnusable()",
    ),
    (
        # Two edits, because one is behaviour-preserving and therefore not a
        # mutation at all: widening `__init__` only *permits* a leak. The first
        # draft of this battery stopped there, and it survived for that reason
        # — correctly. A mutation has to actually change what the code does.
        "the failure message carries the bundle, so a secret reaches a log",
        VAULT,
        [("    def __init__(self):\n        super().__init__(self.message)",
          "    def __init__(self, detail=None):\n"
          "        super().__init__(self.message if detail is None\n"
          "                         else self.message + \" \" + repr(detail))"),
         ("    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:\n"
          "        raise error()\n"
          "    if any(not isinstance(v, str) or not v or len(v) > 16384 "
          "for v in bundle.values()):\n"
          "        raise error()",
          "    if not isinstance(bundle, Mapping) or set(bundle) != _FIELDS:\n"
          "        raise error(bundle)\n"
          "    if any(not isinstance(v, str) or not v or len(v) > 16384 "
          "for v in bundle.values()):\n"
          "        raise error(bundle)")],
        None,
    ),
]


def _run(root: str, suite: str) -> int:
    env = dict(os.environ)
    # Neither suite reads DATABASE_URL — both build sqlite in memory — but an
    # inherited PostgreSQL URL failing every mutation for the same irrelevant
    # reason would read as a clean sweep and prove nothing.
    env.pop("DATABASE_URL", None)
    env["PYTHONPATH"] = root
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", suite, "-q", "-x", "--no-header",
         "-p", "no:cacheprovider"],
        cwd=root, env=env, capture_output=True, text=True, timeout=900,
    )
    return proc.returncode


#: (label, default suite, mutations). Order is presentation only.
GROUPS: list[tuple[str, str, list[tuple]]] = [
    ("The blanket catch, and the coordinate it destroys", VAULT_SUITE, BLANKET_MUTATIONS),
    ("What a failure is", VAULT_SUITE, CLASSIFICATION_MUTATIONS),
    ("What a failure promises", VAULT_SUITE, CONTRACT_MUTATIONS),
]


def main() -> int:
    suites: list[str] = []
    for _, default, mutations in GROUPS:
        for suite in [default] + [m[4] for m in mutations
                                  if len(m) > 4 and m[4] is not None]:
            if suite not in suites:
                suites.append(suite)

    print(f"Baseline: all {len(suites)} judging suites must pass unmutated.")
    base = _overlay(tempfile.mkdtemp(prefix="vault_mut_base_"), DIRS)
    try:
        for suite in suites:
            if _run(base, suite) != 0:
                print(f"  FAIL — {suite} is already red; mutation results "
                      f"judged by it would be noise.")
                return 1
            print(f"  PASS  {suite}")
    finally:
        shutil.rmtree(base, ignore_errors=True)
    print()

    survived: list[str] = []
    unapplied: list[str] = []
    total = 0
    for label, default, mutations in GROUPS:
        print(f"{label} ({len(mutations)} mutations, judged by "
              f"{os.path.basename(default)}):")
        for mutation in mutations:
            name, path, old, new = mutation[:4]
            suite = mutation[4] if len(mutation) > 4 and mutation[4] else default
            total += 1
            root = _overlay(tempfile.mkdtemp(prefix="vault_mut_"), DIRS)
            try:
                if not _apply(root, path, old, new):
                    unapplied.append(name)
                    print(f"  UNAPPLIED {name}  <- anchor missed; proves nothing")
                    continue
                caught = _run(root, suite) != 0
            finally:
                shutil.rmtree(root, ignore_errors=True)
            if caught:
                print(f"  CAUGHT    {name}")
            else:
                survived.append(name)
                print(f"  SURVIVED  {name}  <- hole in {suite}")
        print()

    print("=" * 68)
    if unapplied or survived:
        for name in unapplied:
            print(f"UNAPPLIED — {name}")
        for name in survived:
            print(f"SURVIVED  — {name}")
        print(f"FAIL — {len(survived)} survived, {len(unapplied)} never applied, "
              f"of {total}.")
        return 1
    print(f"PASS — all {total} mutations were caught.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
