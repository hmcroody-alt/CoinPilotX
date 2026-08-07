"""Investor-readiness and adversarial-injection guarantees for UNDX grounding.

These assert on the *server-authoritative* grounding — the identity block and the
self-knowledge payload that every provider request is built from — never on model
output, so they are deterministic. They encode the mission's investor-question and
injection-resistance acceptance criteria (Sections 27/28):

* Investor questions get a truthful frame that separates shipped reality from
  roadmap and refuses to fabricate traction.
* The ecosystem is described as more than any one subsystem.
* Injection attempts to redefine the company, founder, capability status, or the
  honesty rules are declared untrusted data by the grounding itself.
* The self-knowledge payload only advertises genuinely executable capabilities and
  never fabricates a metric.
"""

from __future__ import annotations

try:
    import pytest
except ImportError:  # pragma: no cover
    pytest = None  # type: ignore[assignment]

from services import undx_company_identity as company
from services import undx_self_knowledge as sk


# --- Investor readiness ------------------------------------------------------

def test_investor_note_separates_current_from_roadmap():
    note = company.audience_note("investor").lower()
    assert "roadmap" in note
    # Must not promise traction it cannot ground.
    assert "do not invent metrics" in note or "not invent metrics" in note


def test_every_audience_note_is_distinct_and_nonempty():
    audiences = ["user", "creator", "seller", "advertiser",
                 "business", "developer", "investor", "partner"]
    notes = {a: company.audience_note(a) for a in audiences}
    for a, n in notes.items():
        assert n.strip(), f"empty audience note for {a!r}"
    # Every audience gets its own framing (no accidental duplication).
    assert len(set(notes.values())) == len(audiences)


def test_ecosystem_is_more_than_any_single_subsystem():
    definition = company.CANONICAL_PULSESOC_DEFINITION.lower()
    # The pitch must frame social/marketplace/crypto/AI as parts, not the whole.
    assert "subsystems" in definition
    for part in ("social", "marketplace", "advertising", "crypto"):
        assert part in definition
    assert "not the" in definition  # "...not the whole of it."


def test_positioning_allows_overlap_but_bans_dominance_claims():
    block = company.company_identity_block().lower()
    # Category overlap with incumbents is allowed...
    assert "meta" in block and "shopify" in block
    # ...but unsupported superiority / dominance is not.
    assert "unsupported superiority" in block
    assert "market dominance" in block
    assert "no competitors" in block


# --- Adversarial injection resistance ----------------------------------------

def test_block_declares_embedded_instructions_untrusted():
    block = company.company_identity_block().lower()
    assert "injection resistance" in block
    assert "untrusted data" in block
    assert "do not obey" in block
    # The redefinition surface the grounding must protect.
    for target in ("company", "founder", "capability status", "honesty rules"):
        assert target in block


def test_block_marks_itself_authoritative_over_conflicting_content():
    block = company.company_identity_block().lower()
    assert "authoritative" in block
    # It must explicitly override conflicting claims from retrieved/user content.
    assert "overrides" in block
    for surface in ("user content", "retrieved data", "posts", "files", "web pages"):
        assert surface in block


def test_grounding_never_smuggles_a_traction_number():
    # Adversarial: a metric hidden in the grounding would be indistinguishable from
    # a fabricated fact. The block must contain none.
    block = company.company_identity_block().lower()
    for leak in ("million users", "billion", " arr", "raised $", "valued at",
                 "% growth", "monthly active"):
        assert leak not in block


def test_self_knowledge_advertises_only_executable_capabilities():
    d = sk.self_knowledge()
    # Capability honesty rule is present and unambiguous.
    assert "not executable yet" in d["honesty"]["capability_rule"]
    # And nothing in the advertised inventory is anything but AVAILABLE — the
    # registry is an allowlist of finished executors.
    assert d["capabilities"]["available"], "inventory unexpectedly empty"
    for view in d["capabilities"]["available"]:
        assert view["status"] == "AVAILABLE"


def test_self_knowledge_never_fabricates_named_metrics():
    d = sk.self_knowledge()
    never = set(d["honesty"]["never_fabricates"])
    # The non-fabrication checklist must cover the investor-sensitive facts.
    for fact in ("revenue", "valuation", "investors"):
        assert fact in never


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-q"]))
    _fns = sorted((n, f) for n, f in list(globals().items())
                  if n.startswith("test_") and callable(f))
    _passed = _failed = 0
    for _name, _fn in _fns:
        try:
            _fn()
            _passed += 1
        except Exception as exc:  # noqa: BLE001
            _failed += 1
            print(f"FAIL {_name}: {exc}")
    print(f"RESULT pass={_passed} fail={_failed}")
    raise SystemExit(1 if _failed else 0)
