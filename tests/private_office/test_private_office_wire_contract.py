"""The Private Office wire vocabulary, checked against the client that consumes it.

    python -m pytest tests/private_office/test_private_office_wire_contract.py
    python tests/private_office/test_private_office_wire_contract.py

Why this file exists
--------------------
``/api/private-office/overview`` embeds ``office.product_state()`` verbatim as
``private_office``. The native client matches ``state`` against a closed
whitelist and degrades anything it does not recognise to ``ENTRY_UNKNOWN``;
``PremiumCenterScreen`` renders no row for that state. So a vocabulary drift
between the two sides does not raise, does not log, and does not 500 — it
removes the Private Office entry from Premium for every member at every tier,
and the only symptom is a row that is not there.

That is exactly what shipped: the server emitted the bare words ``AVAILABLE`` /
``UPGRADE_REQUIRED`` / ``UNAVAILABLE`` / ``UNKNOWN`` while the client had always
whitelisted the ``ENTRY_``-prefixed forms.

Why it reads the client's source instead of restating the values
----------------------------------------------------------------
A test that asserted ``ENTRY_AVAILABLE == "ENTRY_AVAILABLE"`` would be a
restatement of the server's own constant. It would have passed every day of the
outage, because both halves of the comparison were the thing that was wrong.
The only assertion with any power here is one that reads the *other* side of the
wire, so the two vocabularies are compared rather than one being echoed.

The client file is parsed as text on purpose: it is TypeScript, there is no
build artifact to import from Python, and the whitelist literals are the actual
runtime source of truth rather than a mirror of it.

Two vocabularies, deliberately different
----------------------------------------
The entry ``state`` is ``ENTRY_``-prefixed. The per-child ``reason`` is bare
(``AVAILABLE``, ``UPGRADE_REQUIRED``, …). They overlap in wording and share no
values, which is precisely why a well-meaning "make these consistent" edit is
dangerous: it fixes the half you are looking at and silently breaks the other.
Both are pinned below.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.private_office import office  # noqa: E402

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CLIENT = os.path.join(_REPO_ROOT, "mobile-native", "src", "api", "privateOffice.ts")


def _whitelist(source: str, const_name: str) -> set:
    """The string literals in a ``const NAME: readonly T[] = [ ... ];`` array.

    Scoped to the single declaration rather than scanning the whole file, so an
    unrelated array elsewhere cannot pad the set and turn a real drift green.
    """
    match = re.search(
        r"const\s+" + re.escape(const_name) + r"\s*:[^=]*=\s*\[(.*?)\]\s*;",
        source,
        re.DOTALL,
    )
    if not match:
        raise AssertionError(
            f"Could not find `const {const_name}` in {_CLIENT}. If it was renamed "
            "or restructured, this contract test must be updated in the same "
            "change — do not delete the assertion to make the suite pass."
        )
    return set(re.findall(r'"([^"]+)"', match.group(1)))


class WireVocabularyContract(unittest.TestCase):
    """The server's emitted words are exactly the words the client accepts."""

    @classmethod
    def setUpClass(cls):
        if not os.path.exists(_CLIENT):
            raise unittest.SkipTest(f"native client not present at {_CLIENT}")
        with open(_CLIENT, "r", encoding="utf-8") as handle:
            cls.source = handle.read()

    def test_entry_states_match_the_client_whitelist(self):
        server = {
            office.ENTRY_AVAILABLE,
            office.ENTRY_UPGRADE_REQUIRED,
            office.ENTRY_UNAVAILABLE,
            office.ENTRY_UNKNOWN,
        }
        client = _whitelist(self.source, "ENTRY_STATES")
        self.assertEqual(
            server,
            client,
            "Private Office entry states have drifted from the native client. "
            "Anything the client does not whitelist becomes ENTRY_UNKNOWN, and "
            "PremiumCenterScreen renders no row for ENTRY_UNKNOWN — so this "
            "drift hides the Private Office entry for everyone, silently.\n"
            f"  server: {sorted(server)}\n  client: {sorted(client)}",
        )

    def test_every_reachable_state_is_one_the_client_accepts(self):
        """Reached through product_state() rather than read off the constants.

        The constants could agree with the client while product_state assembled
        its payload from something else. This walks the real tiers plus the
        degraded-resolver path and checks what the route would actually send.
        """
        client = _whitelist(self.source, "ENTRY_STATES")
        emitted = {
            office.product_state(tier)["state"]
            for tier in ("FREE", "PREMIUM", "PRIVATE", "PRIVATE_OFFICE", "", "WIZARD")
        }
        emitted.add(office.product_state("PRIVATE_OFFICE", resolver_ok=False)["state"])

        unknown = emitted - client
        self.assertEqual(
            set(),
            unknown,
            f"product_state() emits {sorted(unknown)}, which the client cannot "
            "parse and will degrade to ENTRY_UNKNOWN (no row rendered).",
        )

    def test_child_reasons_stay_bare(self):
        """The per-child vocabulary is NOT prefixed, and must not be 'tidied'.

        Pinned because the obvious repair for the entry-state drift — prefixing
        the Private Office words — would break this half if applied broadly.
        """
        client = _whitelist(self.source, "REASON_WORDS")
        reasons = set()
        for tier in ("FREE", "PREMIUM", "PRIVATE", "PRIVATE_OFFICE"):
            state = office.product_state(tier)
            for child in state["available"] + state["unavailable"]:
                reasons.add(child["reason"])

        unknown = reasons - client
        self.assertEqual(
            set(),
            unknown,
            f"Child reason(s) {sorted(unknown)} are not in the client's "
            "REASON_WORDS whitelist.",
        )

    def test_the_two_vocabularies_do_not_overlap(self):
        """A value that parses as both is a value that can be confused for both."""
        entry = _whitelist(self.source, "ENTRY_STATES")
        reasons = _whitelist(self.source, "REASON_WORDS")
        self.assertEqual(
            set(),
            entry & reasons,
            "Entry states and child reasons share a value; they are matched in "
            "different places and must stay distinguishable.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
