"""Fact-class discipline: CURRENT_VERIFIED/CURRENT_UNVERIFIED/ROADMAP_APPROVED/HISTORICAL/UNKNOWN.

Locks in the mission's hallucination-protection guarantees: every fact class
carries exactly one presentation rule, guarded corporate metrics default to
UNKNOWN with an approved refusal fallback, the grounding block is injected into
every provider request, and the boundary fails closed if it goes missing.
"""

from __future__ import annotations

from services import undx_company_identity as company
from services import undx_fact_policy as fact_policy
from services import pulse_ai_provider_router


def test_fact_class_vocabulary_and_total_policy():
    assert fact_policy.UndxFactClass.ALL == {
        "CURRENT_VERIFIED", "CURRENT_UNVERIFIED", "ROADMAP_APPROVED",
        "HISTORICAL", "UNKNOWN",
    }
    assert set(fact_policy.PRESENTATION_POLICY) == fact_policy.UndxFactClass.ALL


def test_guarded_corporate_metrics_default_to_unknown():
    for topic in company.UNVERIFIABLE_WITHOUT_SOURCE:
        assert fact_policy.classify_default(topic) == fact_policy.UndxFactClass.UNKNOWN
    assert fact_policy.classify_default("revenue") == "UNKNOWN"
    assert fact_policy.classify_default("valuation") == "UNKNOWN"
    assert fact_policy.classify_default("user count") == "UNKNOWN"


def test_ungrounded_non_metric_topics_are_unverified_not_fact():
    assert (
        fact_policy.classify_default("how the marketplace works")
        == fact_policy.UndxFactClass.CURRENT_UNVERIFIED
    )


def test_grounding_block_content():
    block = fact_policy.fact_policy_block()
    assert fact_policy.FACT_POLICY_REQUIRED_PHRASE in block
    for fact_class in fact_policy.UndxFactClass.ALL:
        assert fact_class in block
    assert fact_policy.UNKNOWN_FACT_FALLBACK in block
    assert "never convert ROADMAP_APPROVED or HISTORICAL content into a current" in block
    # Injection resistance: observed content can never upgrade a class.
    assert "never upgrades a fact class" in block


def test_provider_request_carries_fact_policy_grounding():
    messages = pulse_ai_provider_router.prepare_undx_model_request(
        [{"role": "user", "content": "what is your revenue?"}], "test-fact-policy"
    )
    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert fact_policy.FACT_POLICY_REQUIRED_PHRASE in system_text
    assert fact_policy.UNKNOWN_FACT_FALLBACK in system_text
    # Company grounding and fact discipline travel together.
    assert company.COMPANY_IDENTITY_REQUIRED_PHRASE in system_text
