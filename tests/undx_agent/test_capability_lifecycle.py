"""Capability lifecycle projection: AVAILABLE/LIMITED/TRAINING/PLANNED/DISABLED.

Locks in the mission's capability-honesty guarantees: lifecycle status is a
pure projection over the registry, knowledge map, and live server policy;
TRAINING/PLANNED can never surface as AVAILABLE; the grounding block carries
the canonical Section-21 sentences; and the provider boundary fails closed if
capability grounding is missing.
"""

from __future__ import annotations

import os
from unittest import mock

from services import undx_capability_lifecycle as lifecycle
from services import undx_capability_registry as registry
from services import undx_knowledge_map as knowledge_map
from services import undx_self_knowledge, pulse_ai_provider_router


def test_status_and_mode_vocabularies():
    assert lifecycle.CapabilityStatus.ALL == {
        "AVAILABLE", "LIMITED", "TRAINING", "PLANNED", "DISABLED"
    }
    assert lifecycle.ExecutionMode.ALL == {"READ", "RECOMMEND", "DRAFT", "EXECUTE"}


def test_unregistered_mapping_is_total_and_never_available():
    """Every non-verified implementation status maps deterministically, never to AVAILABLE."""
    statuses = {
        knowledge_map.ImplementationStatus.IMPLEMENTED_UNVERIFIED,
        knowledge_map.ImplementationStatus.PARTIALLY_IMPLEMENTED,
        knowledge_map.ImplementationStatus.SERVICE_MISSING,
        knowledge_map.ImplementationStatus.INTENTIONALLY_DISABLED,
        knowledge_map.ImplementationStatus.UNSUPPORTED,
    }
    assert set(lifecycle._UNREGISTERED_STATUS) == statuses
    assert lifecycle.CapabilityStatus.AVAILABLE not in set(
        lifecycle._UNREGISTERED_STATUS.values()
    )


def test_inventory_covers_registry_and_knowledge_map():
    views = lifecycle.lifecycle_inventory()
    ids = {view["capability_id"] for view in views}
    for capability_id in registry.REGISTRY:
        assert capability_id in ids
    for record in knowledge_map.RECORDS:
        assert record.capability_id in ids
    for view in views:
        assert view["status"] in lifecycle.CapabilityStatus.ALL
        assert view["executionMode"] in lifecycle.ExecutionMode.ALL
        assert view["canonicalLanguage"] == lifecycle.CANONICAL_STATUS_LANGUAGE[view["status"]]


def test_unregistered_records_never_claim_execution():
    views = {view["capability_id"]: view for view in lifecycle.lifecycle_inventory()}
    for record in knowledge_map.RECORDS:
        if record.capability_id in registry.REGISTRY:
            continue
        view = views[record.capability_id]
        assert view["status"] in {"TRAINING", "PLANNED", "DISABLED"}
        assert view["executionMode"] != "EXECUTE"
        assert view["requiresVerification"] is False
        assert view["receiptRequired"] is False


def test_policy_denylist_demotes_registered_capability_to_disabled():
    if not registry.REGISTRY:
        return
    capability_id = sorted(registry.REGISTRY)[0]
    with mock.patch.dict(os.environ, {"UNDX_AGENT_DISABLED_CAPABILITIES": capability_id}):
        views = {v["capability_id"]: v for v in lifecycle.lifecycle_inventory()}
        assert views[capability_id]["status"] == "DISABLED"


def test_grounding_block_always_reports_every_count():
    block = lifecycle.capability_lifecycle_block()
    assert "UNDX capability state" in block
    # Counts are unconditional, zeroes included: a zero is information, and the block
    # would be misleading if a status could vanish from it entirely.
    for status in lifecycle.CapabilityStatus.ALL:
        assert status in block
    assert "Never present a TRAINING or PLANNED capability as complete" in block


def _block_with_counts(**counts):
    full = {status: 0 for status in lifecycle.CapabilityStatus.ALL}
    full.update(counts)
    with mock.patch.object(lifecycle, "lifecycle_counts", return_value=full):
        return lifecycle.capability_lifecycle_block()


def test_a_status_sentence_is_offered_only_when_something_holds_that_status():
    """The fix for a model refusing an action it was allowed to take.

    This block is prepended to every conversational system prompt. The version that
    listed all five canonical sentences unconditionally handed the model the words
    "final execution still requires the current PulseSoc interface" even on a
    deployment where nothing was LIMITED — and a model reaching for the most fluent
    excuse sitting in its own context is the predictable failure, not a surprising one.
    """
    limited = lifecycle.CANONICAL_STATUS_LANGUAGE["LIMITED"]
    assert "current PulseSoc interface" in limited, "the sentence under test moved"

    healthy = _block_with_counts(AVAILABLE=40, TRAINING=30, PLANNED=19)
    assert limited not in healthy
    assert "current PulseSoc interface" not in healthy
    assert lifecycle.CANONICAL_STATUS_LANGUAGE["AVAILABLE"] in healthy
    assert lifecycle.CANONICAL_STATUS_LANGUAGE["TRAINING"] in healthy
    assert lifecycle.CANONICAL_STATUS_LANGUAGE["PLANNED"] in healthy

    # And the converse, which is the half that keeps this from being a gag order: when
    # policy really has suspended part of a capability the sentence is true, and the
    # model needs it.
    suspended = _block_with_counts(LIMITED=120, TRAINING=30)
    assert limited in suspended
    assert lifecycle.CANONICAL_STATUS_LANGUAGE["AVAILABLE"] not in suspended


def test_the_grounding_block_survives_every_status_being_empty():
    """No capabilities at all is a degenerate but reachable state (policy off, empty
    registry). It must produce a block, not an empty framing clause or a stray colon."""
    block = _block_with_counts()
    assert "UNDX capability state" in block
    assert "When describing what you can do" not in block
    for sentence in lifecycle.CANONICAL_STATUS_LANGUAGE.values():
        assert sentence not in block


def test_self_knowledge_exposes_lifecycle():
    data = undx_self_knowledge.self_knowledge()
    capabilities = data["capabilities"]
    assert len(capabilities["lifecycle"]) >= len(capabilities["available"])
    assert set(capabilities["lifecycle_counts"]) == lifecycle.CapabilityStatus.ALL
    assert capabilities["canonical_language"] == lifecycle.CANONICAL_STATUS_LANGUAGE
    # Client-safe: never leak executors/verifiers/schemas.
    for view in capabilities["lifecycle"]:
        assert "executor" not in view and "verifier" not in view and "schema" not in view


def test_provider_request_carries_capability_grounding():
    messages = pulse_ai_provider_router.prepare_undx_model_request(
        [{"role": "user", "content": "what can you do?"}], "test-lifecycle"
    )
    system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
    assert "UNDX capability state" in system_text
    assert lifecycle.CANONICAL_STATUS_LANGUAGE["TRAINING"] in system_text


def test_provider_fails_closed_when_capability_grounding_breaks():
    with mock.patch.object(
        pulse_ai_provider_router.undx_capability_lifecycle,
        "capability_lifecycle_block",
        side_effect=RuntimeError("boom"),
    ):
        try:
            pulse_ai_provider_router.prepare_undx_model_request(
                [{"role": "user", "content": "hi"}], "test-fail-closed"
            )
        except pulse_ai_provider_router.PulseAIProviderError as error:
            assert error.reason == "capability_grounding_error"
        else:  # pragma: no cover
            raise AssertionError("expected PulseAIProviderError")
