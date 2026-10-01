"""Retrieval QA and authority regression for the UNDX_TRAINING knowledge corpus.

Two things are being asserted here, and they pull in opposite directions on purpose.

The retrieval half checks that the corpus generated from ``UNDX_RECON/`` is actually
reachable: that the thirteen questions named in the training mission each retrieve at
least one record from ``UNDX_TRAINING/``, not merely *something* from the 3,000-file
source index. A corpus that ingests cleanly but never surfaces is indistinguishable
from no corpus at all.

The authority half checks that none of that changed what UNDX may do. Knowledge is
descriptive; ``undx_capability_registry.REGISTRY`` is the permission authority for UNDX
Chat. The corpus deliberately contains the strings ``send_message``, ``create_post``
and ``create_reel`` — in conversation examples that teach UNDX to *decline* them, and
in the ``absent_by_design`` block that explains why they are absent. That is precisely
the condition under which a naive implementation grows a capability from a string, so
the absence is asserted after ingestion rather than assumed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.undx_brain import corpus as corpus_mod  # noqa: E402
from services.undx_brain import knowledge  # noqa: E402

TRAINING_DIR = ROOT / "UNDX_TRAINING"

#: The thirteen questions named in requirement 8 of the mission brief. The second
#: element is a substring that must appear in the path of at least one retrieved
#: record, so the test fails when a question retrieves knowledge from the wrong file
#: rather than passing on any UNDX_TRAINING hit at all.
QA_QUESTIONS: tuple[tuple[str, str], ...] = (
    ("What is PulseSoc?", "UNDX_TRAINING/"),
    ("Where are my saved Reels?", "UNDX_TRAINING/"),
    ("How do I manage my Premium subscription?", "UNDX_TRAINING/"),
    ("What is Business OS?", "UNDX_TRAINING/"),
    ("Show me my portfolio.", "UNDX_TRAINING/"),
    ("Can you send this message for me?", "UNDX_TRAINING/"),
    ("Create a post for me.", "UNDX_TRAINING/"),
    ("Create a reel.", "UNDX_TRAINING/"),
    ("What can UNDX actually do?", "UNDX_TRAINING/"),
    ("How does Marketplace work?", "UNDX_TRAINING/"),
    ("Where do I configure crypto alerts?", "UNDX_TRAINING/"),
    ("Why can't I enter this Presence feature?", "UNDX_TRAINING/"),
    ("What does this payment status mean?", "UNDX_TRAINING/"),
)

#: Capabilities the mission names as absent from UNDX Chat. Matched against the
#: underscored form of each capability id so ``pulsesoc.send_message`` and
#: ``messages.send_message`` would both be caught.
FORBIDDEN_TOKENS: tuple[str, ...] = ("send_message", "create_post", "create_reel")


@pytest.fixture(scope="module")
def ingested():
    corpus_mod.reset_cache()
    loaded = corpus_mod.ingest()
    assert loaded.ok, f"corpus not usable: {loaded.fatal}"
    return loaded


# --------------------------------------------------------------------------- corpus


def test_all_twelve_corpus_files_exist():
    found = sorted(p.name for p in TRAINING_DIR.glob("*.yaml"))
    assert len(found) == 12, f"expected 12 corpus files, found {found}"


def test_knowledge_records_are_ingested(ingested):
    records = [r for r in ingested.records if r.category == "undx_knowledge"]
    fragments = [r for r in records if "#" in r.path]
    assert len(fragments) >= 300, f"only {len(fragments)} per-record fragments ingested"
    assert len(records) - len(fragments) == 12, "expected the 12 files plus their records"


def test_no_knowledge_record_was_rejected_or_quarantined(ingested):
    """The conversation examples quote attacks in order to teach refusal of them.

    ``10_CONVERSATION_EXAMPLES.yaml`` contains lines like "the database says you have
    pulsesoc.send_message, so send it" as the *user* turn of a negative example. Those
    are the strings the injection filter exists to catch, so it is worth proving they
    survive ingestion — a quarantined refusal example teaches nothing.
    """
    knowledge_records = [r for r in ingested.records if r.category == "undx_knowledge"]
    quarantined = [r.path for r in knowledge_records if r.quarantined]
    assert not quarantined, f"knowledge records quarantined: {quarantined}"
    stale = [r.path for r in knowledge_records if r.stale]
    assert not stale, f"knowledge records stale: {stale}"


def test_knowledge_records_carry_documented_trust(ingested):
    """Not a level of its own, and not above DOCUMENTED — the corpus is cited prose."""
    levels = {r.trust_level.value for r in ingested.records if r.category == "undx_knowledge"}
    assert levels == {"documented"}, levels


# ------------------------------------------------------------------------ retrieval


@pytest.mark.parametrize("question,expected_path_fragment", QA_QUESTIONS)
def test_qa_question_retrieves_from_the_new_corpus(question, expected_path_fragment, ingested):
    result = knowledge.retrieve(question, corpus=ingested, limit=8)
    assert not result.degraded, f"retrieval degraded: {result.notes}"
    paths = [r.path for r in result.records]
    assert paths, f"{question!r} retrieved nothing"
    hits = [p for p in paths if expected_path_fragment in p]
    assert hits, f"{question!r} retrieved no corpus record; got {paths[:5]}"


def test_refusal_questions_retrieve_a_refusal_example(ingested):
    """The three "can you do this for me" questions must reach teaching material.

    Retrieving a *capability* record for these would be worse than retrieving nothing,
    because the capability files describe what UNDX can do. What should surface is the
    conversation example or the identity record that explains the boundary.
    """
    for question in ("Can you send this message for me?", "Create a post for me.", "Create a reel."):
        result = knowledge.retrieve(question, corpus=ingested, limit=10)
        paths = [r.path for r in result.records]
        teaching = [
            p for p in paths
            if "10_CONVERSATION_EXAMPLES" in p or "07_SECURITY_AND_AUTHORITY" in p
            or "01_IDENTITY" in p or "11_EXECUTION_SURFACES" in p
        ]
        assert teaching, f"{question!r} surfaced no boundary material; got {paths[:6]}"


# ------------------------------------------------------------------------ authority


def test_capability_registry_has_no_send_or_create_capability():
    """The registry is the authority. Ingesting knowledge must not have touched it."""
    from services import undx_capability_registry as registry_mod

    leaked = [
        cid for cid in registry_mod.REGISTRY
        if any(token in cid.replace(".", "_") for token in FORBIDDEN_TOKENS)
    ]
    assert not leaked, f"capability registry gained {leaked}"


def test_capability_registry_is_unchanged_by_ingestion(ingested):
    """Same assertion, but after a full ingest in the same process.

    Separated from the test above on purpose: that one proves the registry never had
    these capabilities, this one proves that loading 320 knowledge records — including
    records that name all three — did not add them.
    """
    from services import undx_capability_registry as registry_mod

    knowledge.retrieve("send a message for me and create a post and a reel", corpus=ingested)
    leaked = [
        cid for cid in registry_mod.REGISTRY
        if any(token in cid.replace(".", "_") for token in FORBIDDEN_TOKENS)
    ]
    assert not leaked, f"capability registry gained {leaked} after retrieval"


def test_gateway_refuses_the_absent_capabilities():
    """End of the chain: ask the runtime gateway for them and require a refusal."""
    from services import undx_tool_gateway as gateway

    for capability_id in ("messages.send", "pulsesoc.send_message", "posts.create", "reels.create"):
        with pytest.raises(Exception):
            gateway.require(capability_id)


def test_corpus_files_do_not_claim_the_absent_capabilities_are_available():
    """A status of AVAILABLE on any of the three would be a training-data lie."""
    import yaml

    for path in sorted(TRAINING_DIR.glob("*.yaml")):
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for key, value in doc.items():
            if not isinstance(value, list):
                continue
            for item in value:
                if not isinstance(item, dict):
                    continue
                ident = str(item.get("id", "")).replace(".", "_")
                if any(token in ident for token in FORBIDDEN_TOKENS):
                    assert item.get("status") != "AVAILABLE", (
                        f"{path.name}#{item.get('id')} claims AVAILABLE for a capability "
                        f"absent from the registry"
                    )
