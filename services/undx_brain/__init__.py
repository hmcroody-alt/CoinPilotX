"""The UNDX Brain: the intelligence layer between a request and PulseSoc's systems.

PulseSoc is the body. UNDX is the mind. This package is the part of the mind that is
*structure* rather than behaviour — the layer that decides what UNDX knows, how well it
knows it, what it is allowed to do about it, and what it may claim afterwards.

Nothing here replaces the runtime that already works. ``services/undx_agent_runtime.py``,
``undx_tool_gateway``, ``undx_capability_registry``, ``undx_verification``,
``undx_agent_policy`` and their tests are the load-bearing execution path, and they stay
exactly where they are. :mod:`services.undx_brain.foundation` maps those existing
components into named Foundation responsibilities so the architecture is legible and so a
component going missing is a test failure rather than a surprise; it does not wrap them,
proxy them, or re-implement them.

What is genuinely new is the knowledge half. The source-derived corpus at
``backend/undx/config/undx_training_v6_source_corpus.yaml`` existed, was audited, and was
read by nothing. :mod:`~services.undx_brain.corpus` ingests it as untrusted, bounded,
provenance-carrying data, and :mod:`~services.undx_brain.knowledge` serves small
relevant slices of it.

The module worth reading first is :mod:`~services.undx_brain.truth`, because it holds the
distinction the rest of the package exists to protect: knowing how PulseSoc works and
knowing what happened to an account are different kinds of knowing, and no amount of the
first ever becomes the second.

Every stage is behind a flag that defaults off — see :mod:`~services.undx_brain.config`.
An unconfigured deployment behaves exactly as it does today.
"""

from __future__ import annotations

#: Every module in the package, in the order a request would meet them: what is
#: configured, what is known and how well, what one turn may claim, where knowledge
#: comes from, what the architecture owns, what is remembered, what that memory is still
#: worth and what the accumulated record of it says, then the bounds and the cognitive
#: stages, then what a proposed action would do before it is taken, then which of the
#: several things it could be is chosen — or the admission that the sentence does not
#: say — then who is eligible at all.
#: ``selection`` sits after ``prediction`` rather than before it, which is the one place
#: this list is not the order a request arrives in: a request meets the chooser first,
#: but the chooser decides by reading a prediction, so prediction has to be understood
#: first for the choosing to make any sense. ``execution`` then follows ``selection``
#: because it is what a chosen plan is walked through, and it is last of the acting
#: modules for the same reason it is the smallest: it decides nothing and performs
#: nothing, it only counts what a run has spent and stops it honestly.
#: Listed exhaustively so that a module added without being named here is visible as a
#: difference rather than merely absent — the test in ``tests/undx_brain`` walks the
#: directory and compares.
__all__ = [
    "config",
    "truth",
    "evidence",
    "corpus",
    "knowledge",
    "foundation",
    "memory",
    "facts",
    "learning",
    "bounds",
    "workspace",
    "attention",
    "goals",
    "prediction",
    "selection",
    "execution",
    "rollout",
]
