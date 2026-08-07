"""Canonical company / founder grounding and honesty guarantees for UNDX.

These lock in the acceptance criteria that UNDX must identify CoinPlotXAI Inc.
and its founder, define PulseSoc accurately, refuse to fabricate corporate or
financial facts, keep capability claims honest, and treat instructions embedded
in content as untrusted. They assert on the server-authoritative grounding that
every provider request is built from — not on model output.
"""

from __future__ import annotations

try:  # Available in CI; optional so the suite can also run under a bare interpreter.
    import pytest
except ImportError:  # pragma: no cover
    pytest = None  # type: ignore[assignment]

from services import undx_company_identity as company
from services import pulse_ai_knowledge, pulse_ai_provider_router


# --- Canonical facts ---------------------------------------------------------

def test_founder_and_company_facts():
    assert company.founder_name() == "Roody Cherie"
    assert company.founder_title() == "Founder & CEO"
    assert company.legal_name() == "CoinPlotXAI Inc."
    assert company.COMPANY["primary_product"] == "PulseSoc"


def test_facts_snapshot_is_isolated_copy():
    snap = company.facts()
    snap["founder"]["name"] = "tampered"
    snap["product_category"].append("tampered")
    # Mutating the returned snapshot must not corrupt the canonical source.
    assert company.founder_name() == "Roody Cherie"
    assert "tampered" not in company.COMPANY["product_category"]


def test_canonical_explanations_name_founder_and_company():
    assert "Roody Cherie" in company.CANONICAL_COMPANY_EXPLANATION
    assert "CoinPlotXAI Inc." in company.CANONICAL_COMPANY_EXPLANATION
    assert "ecosystem" in company.CANONICAL_PULSESOC_DEFINITION.lower()


# --- Grounding block content -------------------------------------------------

def test_block_states_company_founder_and_definition():
    block = company.company_identity_block()
    assert company.COMPANY_IDENTITY_REQUIRED_PHRASE in block
    assert "Roody Cherie" in block
    assert "Founder & CEO" in block
    assert "intelligent digital ecosystem" in block


def test_block_forbids_inventing_financial_and_corporate_facts():
    block = company.company_identity_block().lower()
    for forbidden in ("revenue", "valuation", "investors", "user count", "funding"):
        assert forbidden in block, f"honesty rule must mention {forbidden!r}"
    assert "do not invent" in block
    assert "verified" in block


def test_block_enforces_capability_honesty():
    block = company.company_identity_block().lower()
    assert "never claim an action was completed" in block
    assert "verified" in block
    assert "planned" in block


def test_block_has_injection_resistance():
    block = company.company_identity_block().lower()
    assert "injection resistance" in block
    assert "untrusted data" in block
    assert "do not obey" in block


def test_block_bans_unsupported_superiority():
    block = company.company_identity_block().lower()
    assert "unsupported superiority" in block
    assert "no competitors" in block


def test_block_contains_no_fabricated_metrics():
    # The grounding must not itself smuggle in a number that looks like traction.
    block = company.company_identity_block().lower()
    for leak in ("million users", "billion", "arr", "raised $", "valued at"):
        assert leak not in block


# --- Provider boundary: fail-closed, provider-agnostic grounding --------------

def test_provider_request_prepends_company_grounding():
    msgs = pulse_ai_provider_router.prepare_undx_model_request(
        [{"role": "system", "content": "canonical name is UNDX"}], "cid-test"
    )
    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    assert "CoinPlotXAI Inc." in system
    assert "Roody Cherie" in system
    # UNDX identity block still present and first.
    assert pulse_ai_provider_router.UNDX_IDENTITY_REQUIRED_PHRASE in system


def test_provider_request_grounds_even_without_upstream_company_text():
    # Even if nothing upstream mentions the company, the boundary injects it.
    msgs = pulse_ai_provider_router.prepare_undx_model_request(
        [{"role": "user", "content": "Who founded PulseSoc?"}], "cid-test-2"
    )
    system = "\n\n".join(m["content"] for m in msgs if m["role"] == "system")
    assert company.COMPANY_IDENTITY_REQUIRED_PHRASE in system


# --- Prompt builder path (survives compiled policy replacement) ---------------

def test_build_system_prompt_grounds_company_by_default():
    prompt = pulse_ai_knowledge.build_system_prompt()
    assert "CoinPlotXAI Inc." in prompt
    assert "Roody Cherie" in prompt


def test_build_system_prompt_grounds_company_under_compiled_policy():
    # A compiled policy replaces CORE_SYSTEM_PROMPT; company grounding must remain.
    prompt = pulse_ai_knowledge.build_system_prompt(
        compiled_policy="canonical name is UNDX. Custom compiled policy body."
    )
    assert "CoinPlotXAI Inc." in prompt
    assert "Roody Cherie" in prompt


# --- Audience adaptation ------------------------------------------------------

def test_audience_note_differs_for_investor_vs_user():
    investor = company.audience_note("investor")
    user = company.audience_note("user")
    assert investor != user
    assert "roadmap" in investor.lower()
    # Unknown audiences fall back to the plain user framing.
    assert company.audience_note("martian") == user


if __name__ == "__main__":  # pragma: no cover
    if pytest is not None:
        raise SystemExit(pytest.main([__file__, "-q"]))
    # Bare-interpreter fallback: run every test_* function and report.
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
