"""The knowledge map's evidence must resolve against the code it cites.

The map is the input to the readiness gate, and the gate is what decides which
capabilities may be wired. Its authority rests entirely on the evidence strings
being checkable, so a citation that points at an unrelated line is worse than no
citation: it reads as verification and supplies none.

That is not hypothetical. Six of the twelve ``bot.py`` line citations in this map
had drifted — one named a ``return jsonify(...)`` in an unrelated reel handler while
claiming to be the friend-accept route — because ``bot.py`` is a hundred thousand
lines long and edits above a cited line move it silently. Nothing failed. The map
simply began describing a file that no longer existed in that shape.

These tests resolve every citation against the real file, in the same spirit as
``SchemaGroundingTests``: a claim about the application is checked against the
application, never against a fixture that agrees with it by construction.
"""

from __future__ import annotations

import inspect
import os
import re
import unittest

from tests.undx_agent import bootstrap as _bootstrap  # noqa: F401
from services import undx_knowledge_map as km
from services.undx_knowledge_map import (
    AuthorizationScope,
    ImplementationStatus,
    ReadinessClass,
    classify_readiness,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BOT = os.path.join(_ROOT, "bot.py")

#: ``bot.py:12345 some description``. The description is what makes the citation
#: checkable, so it is captured rather than skipped.
_CITATION = re.compile(r"bot\.py:(\d+)([^\"')]*)")

#: Words that describe the citation's role rather than the code at it.
_NOISE = frozenset({"handler", "route", "renders", "page", "for", "contrast",
                    "the", "nearest", "json", "web", "dashboard", "is", "a", "an"})

#: ``<path:status_id>`` -> ``<status_id>``. Flask converter prefixes are a property of
#: the declaration, not of the route, and a citation that writes the route the way a
#: user would type it is not thereby wrong. Normalising both sides keeps the match on
#: the whole path — which is what makes it strict — without failing on that difference.
_CONVERTER = re.compile(r"<(?:int|float|path|string|uuid|any)\s*:\s*([\w]+)>")


def _normalize(text: str) -> str:
    return _CONVERTER.sub(r"<\1>", text)


def _significant(label: str) -> list[str]:
    """The parts of a citation label that should be findable in the source.

    A label naming a route is reduced to that route as a single literal, not to its
    segments. Splitting was tried first and was too weak to be worth having: ``bot.py``
    embeds the entire web client as minified JavaScript, so words like ``status`` and
    ``live`` occur on thousands of unrelated lines and two citations that were off by
    a thousand lines each still passed. A whole path is specific enough that finding
    it near the cited line means the citation is right.
    """
    path = re.search(r"(/[\w/<>:.\-]+)", label)
    if path:
        return [_normalize(path.group(1).rstrip(".,"))]
    return [word for word in re.findall(r"[a-z_]{4,}", label.lower()) if word not in _NOISE]


class KnowledgeMapCitationTests(unittest.TestCase):
    """Every ``bot.py:N`` in the map must point at code matching its description."""

    @classmethod
    def setUpClass(cls) -> None:
        with open(_BOT, "r", encoding="utf-8", errors="replace") as handle:
            cls.lines = handle.read().splitlines()
        with open(os.path.join(_ROOT, "services", "undx_knowledge_map.py"),
                  "r", encoding="utf-8") as handle:
            cls.citations = sorted(set(_CITATION.findall(handle.read())))

    def test_there_are_citations_to_check(self) -> None:
        """Guards the regex itself: a test that silently matches nothing proves nothing."""
        self.assertGreaterEqual(len(self.citations), 10)

    def test_every_cited_line_exists(self) -> None:
        for number, label in self.citations:
            with self.subTest(citation=f"bot.py:{number}{label}"):
                self.assertLessEqual(int(number), len(self.lines),
                                     f"bot.py has {len(self.lines)} lines")

    def test_every_cited_line_matches_its_description(self) -> None:
        """The cited line, or its immediate neighbourhood, must contain the thing named.

        A window rather than an exact line, because a citation naming a route
        legitimately points at either the decorator or the function beneath it, and
        pinning it tighter would make the test fail on formatting. The window is
        small enough that an unrelated line cannot drift into it by accident — the
        six citations this test was written against were off by hundreds of lines.
        """
        for number, label in self.citations:
            keywords = _significant(label)
            if not keywords:
                continue
            index = int(number) - 1
            window = _normalize(" ".join(self.lines[max(0, index - 3):index + 4]).lower())
            with self.subTest(citation=f"bot.py:{number}{label}"):
                missing = [word for word in keywords if word.lower() not in window]
                self.assertFalse(
                    missing,
                    f"bot.py:{number} does not mention {missing}; "
                    f"line reads: {self.lines[index].strip()[:120]!r}",
                )


class ReadinessPrecedenceTests(unittest.TestCase):
    """The gate must name the most severe blocker, not the most convenient one."""

    #: The mandated order. Earlier entries outrank later ones.
    PRECEDENCE = (
        ReadinessClass.AUTHORIZATION_DEFECT,
        ReadinessClass.DOMAIN_SERVICE_REQUIRED,
        ReadinessClass.TOGGLE_HAZARD,
        ReadinessClass.VERIFIER_REQUIRED,
        ReadinessClass.NATIVE_CONTEXT_REQUIRED,
        ReadinessClass.UNSUPPORTED,
        ReadinessClass.READY_TO_WIRE,
    )

    def test_precedence_covers_every_class(self) -> None:
        self.assertEqual(set(self.PRECEDENCE), set(ReadinessClass.ALL))

    def test_the_classifier_tests_conditions_in_the_mandated_order(self) -> None:
        """The order itself, asserted — not just the set of names in it.

        Checking membership was the weaker test that let this through: the tuple
        above listed ``DOMAIN SERVICE REQUIRED`` before ``TOGGLE HAZARD`` while the
        classifier tested them the other way round, and every set-based assertion
        passed. A precedence test that cannot see order is not a precedence test.

        This reads the source rather than synthesising records, because a synthetic
        record has to be built from the same field semantics the classifier uses,
        and getting those wrong produces a test that agrees with itself. The order
        the branches appear in is the order they are evaluated in.
        """
        source = inspect.getsource(classify_readiness)
        body = source.split('"""', 2)[-1]
        seen: list[str] = []
        for name in ReadinessClass.ALL:
            attribute = next(k for k, v in vars(ReadinessClass).items() if v == name)
            position = body.find(f"ReadinessClass.{attribute}")
            if position >= 0:
                seen.append((position, name))
        ordered = [name for _, name in sorted(seen)]
        self.assertEqual(ordered, list(self.PRECEDENCE))

    def test_an_unsupported_capability_still_reports_its_authorization_defect(self) -> None:
        """The specific inversion this precedence was written to prevent.

        ``UNSUPPORTED`` used to be tested first, so a record that was both
        unsupported and unscoped classified as unsupported and the defect left the
        matrix. "Not building this yet" gets skimmed; "a caller can reach rows they
        do not own" does not — and it stays true the day the capability ships.
        """
        for record in km.RECORDS:
            unsupported = record.implementation_status in (
                ImplementationStatus.UNSUPPORTED,
                ImplementationStatus.INTENTIONALLY_DISABLED,
            )
            defective = record.authorization_scope in (
                AuthorizationScope.EXISTENCE_ORACLE,
                AuthorizationScope.UNSCOPED,
                AuthorizationScope.PRIVILEGED,
            )
            if unsupported and defective:
                with self.subTest(capability=record.capability_id):
                    self.assertEqual(classify_readiness(record),
                                     ReadinessClass.AUTHORIZATION_DEFECT)

    def test_the_inversion_is_actually_exercised(self) -> None:
        """A guard on the test above, which passes vacuously if no such record exists."""
        overlapping = [
            r.capability_id for r in km.RECORDS
            if r.implementation_status in (ImplementationStatus.UNSUPPORTED,
                                           ImplementationStatus.INTENTIONALLY_DISABLED)
            and r.authorization_scope in (AuthorizationScope.EXISTENCE_ORACLE,
                                          AuthorizationScope.UNSCOPED,
                                          AuthorizationScope.PRIVILEGED)
        ]
        self.assertTrue(overlapping,
                        "no record is both unsupported and authorization-defective, "
                        "so the precedence test above proves nothing")

    def test_every_record_classifies_into_a_declared_class(self) -> None:
        for record in km.RECORDS:
            with self.subTest(capability=record.capability_id):
                self.assertIn(classify_readiness(record), ReadinessClass.ALL)

    def test_ready_to_wire_means_no_blocker_applies(self) -> None:
        """``READY TO WIRE`` is the absence of every other condition, not a default."""
        for record in km.RECORDS:
            if classify_readiness(record) != ReadinessClass.READY_TO_WIRE:
                continue
            with self.subTest(capability=record.capability_id):
                self.assertNotIn(record.authorization_scope,
                                 (AuthorizationScope.EXISTENCE_ORACLE,
                                  AuthorizationScope.UNSCOPED,
                                  AuthorizationScope.PRIVILEGED))
                self.assertNotIn(record.implementation_status,
                                 (ImplementationStatus.UNSUPPORTED,
                                  ImplementationStatus.INTENTIONALLY_DISABLED,
                                  ImplementationStatus.SERVICE_MISSING))
                self.assertFalse(record.toggle_semantics)
                self.assertFalse(record.requires_native_context)
                self.assertFalse(record.read_back_missing)
                if record.is_write:
                    self.assertTrue(record.verifier)
                    self.assertTrue(record.domain_operation)


if __name__ == "__main__":
    unittest.main()
