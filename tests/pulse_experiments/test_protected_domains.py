"""The modules that own money, identity and authorisation must not import this one.

``registry`` refuses to *name* an experiment after a protected domain, and that
is a rule about strings. It does not stop an author calling something
``checkout.button_copy`` and branching on the result to skip a fraud check.

This is the test that closes the gap, and it closes it structurally: a gate that
cannot be reached from the payment path cannot alter the payment path, whatever
the experiment is called. Import is the reachability edge, so the assertion is
that no module owning one of these concerns imports ``pulse_experiments`` at
all.

Scans source rather than importing. Importing ``bot.py`` boots a 111k-line
Flask monolith with live integrations, and importing the payment modules pulls
in Stripe.

When this test fails
--------------------

Someone has wired an experiment into a protected surface. The fix is not to add
the file to the allowlist — there is deliberately no allowlist — it is to move
the varying part out of the protected module. If a variant genuinely needs to
change what a buyer *sees* on a checkout page, the presentation belongs in a
view module that the payment module does not import.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SERVICES = ROOT / "services"

#: Filename fragments that mark a module as owning a protected concern. Matched
#: against the stem, so ``marketplace_payout_worker`` is caught by ``payout``.
PROTECTED_STEMS = (
    "auth",
    "billing",
    "checkout",
    "entitlement",
    "fraud",
    "ledger",
    "order",
    "payment",
    "payout",
    "privacy",
    "refund",
    "stripe",
    "treasury",
)

_IMPORT_RE = re.compile(
    r"^\s*(?:from\s+services\.pulse_experiments|"
    r"import\s+services\.pulse_experiments|"
    r"from\s+\.\s*pulse_experiments|"
    r"from\s+services\s+import\s+[^\n]*pulse_experiments)",
    re.MULTILINE,
)


def _protected_modules() -> list[pathlib.Path]:
    out = []
    for path in sorted(SERVICES.rglob("*.py")):
        stem = path.stem.lower()
        if any(fragment in stem for fragment in PROTECTED_STEMS):
            out.append(path)
    return out


def test_the_scan_actually_finds_protected_modules():
    """Guards the guard. A rename upstream could empty this list silently.

    Without this, the parametrised test below would pass with zero cases and
    report the protected domains as enforced while checking nothing.
    """
    found = _protected_modules()

    assert len(found) >= 15, [p.name for p in found]
    names = {p.name for p in found}
    for expected in ("auth_service.py", "stripe_service.py", "payment_provider.py"):
        assert expected in names, f"{expected} missing — has the tree moved?"


@pytest.mark.parametrize(
    "path", _protected_modules(), ids=lambda p: p.relative_to(SERVICES).as_posix()
)
def test_a_protected_module_does_not_import_pulse_experiments(path):
    source = path.read_text(encoding="utf-8", errors="replace")

    match = _IMPORT_RE.search(source)

    assert match is None, (
        f"{path.relative_to(ROOT)} imports pulse_experiments at "
        f"line {source[:match.start()].count(chr(10)) + 1}. An experiment must not be "
        "reachable from money, identity or authorisation. Move the varying part "
        "into a module this one does not import."
    )


def test_bot_py_does_not_gate_a_payment_route_on_an_experiment():
    """The monolith is scanned by content, since every concern shares one file.

    `bot.py` cannot be excluded by filename, so the check is narrower: the
    package may be imported there, but no line may call into it within a
    payment, payout, refund or auth context on the same line.
    """
    source = (ROOT / "bot.py").read_text(encoding="utf-8", errors="replace")

    offenders = [
        (index + 1, line.strip())
        for index, line in enumerate(source.splitlines())
        if ("variant_for" in line or "assignment_for" in line)
        and re.search(r"payment|payout|refund|stripe|auth|fraud|entitle", line, re.I)
    ]

    assert offenders == [], offenders
