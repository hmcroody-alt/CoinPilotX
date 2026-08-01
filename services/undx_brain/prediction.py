"""What a write will do, said before it happens and checked after.

:func:`services.undx_architecture.simulate_operation` is the function this replaces the
substance of, and it is worth being exact about what it does today, because the name
promises something the body never attempted. It confirms the tool is registered, redacts
argument values whose *key* contains ``token``/``secret``/``password``, and returns a
``predicted_outcome`` that is one of two string constants selected by whether the caller
passed a non-empty ``failure``. It reads no resource. It consults no registry beyond a
membership test. Asked about ``crypto.alerts.delete`` — which destroys a row and has no
undo capability — and about ``saved.post.set`` — which undoes itself exactly by negating
one boolean — it returns character-for-character the same answer.

That function is not wrong, and the distinction matters. Every field it returns is
either a fact (``production_write: False``) or a declared absence of knowledge
(``"Real outcome requires an authorized tool result."``). It never asserted a prediction
it had not made. So this module is additive: nothing has to be retracted first.

**The material was already there.** ``services.undx_capability_registry`` records, per
capability, the field naming the resource, which fields the verifier reads back, which
capability reverses this one, and how to build that capability's arguments from this
call's. :meth:`CapabilitySpec.undo_arguments` will already refuse to produce an argument
set it cannot honestly build. None of that had a reader that ran *before* the write, and
running before the write is the only moment at which any of it could change a decision.

**Four things become derivable, and they are different for different capabilities.**

*What the verifier should read back.* For every field in ``verified_fields`` that the
call also passes, the expected post-state is the passed value. That is not a
paraphrase of the request — it is the specific claim the verifier will shortly either
confirm or contradict, which is what makes it checkable. :func:`check` does the
checking, and a prediction no one ever checks is a description wearing a prediction's
name.

*Whether the call can be reversed at all, now.* Three write capabilities declare no undo
capability. One declares an undo whose arguments cannot be built until the write
verifies and yields a canonical id — so at the moment the question is being asked, that
call is not reversible, and saying "reversible" because a row in the registry names a
capability would be a promise the system cannot keep. See :class:`Reversal`.

*Which prior values are about to be destroyed.* ``crypto.alerts.update`` changes
``threshold`` and ``condition`` and has no undo. The old values are not stored anywhere.
Reversing it is possible only if something read them *first*, and the only moment to
learn that is before the write. :attr:`Prediction.pre_read_fields` names them.

*What reversal costs.* ``crypto.alerts.create`` undoes with ``crypto.alerts.delete``,
which is itself a ``consequential_write`` requiring explicit confirmation. An undo
affordance that silently launches a second confirmation prompt is not the cheap escape
hatch the word "undo" implies, and the difference is visible in the registry.

**Two deliberate refusals.**

This module does not read the database. It predicts from what a call *declares*, not
from what the resource currently holds, and it says so: :attr:`Prediction.assumes` lists
the assumptions that a pre-read would have to discharge. A predictor that quietly issued
its own queries would be doing retrieval under a name that promises reasoning, and its
answers would depend on a race with the write it is describing.

This module does not guess a value it cannot derive. ``reels.like`` declares
``verified_fields=('liked',)`` and no field named ``liked`` — the value the verifier
expects comes from which capability was chosen, not from an argument. Those land in
:attr:`Prediction.implied_fields` rather than in :attr:`Prediction.expected` with a
plausible ``True`` beside them. An expectation invented by the predictor would be
confirmed by :func:`check` against itself.

Everything is behind ``UNDX_BRAIN_PREDICTION_ENABLED``, which defaults off, and with it
off every entry point returns ``ok=False`` and predicts nothing. An unconfigured
deployment behaves exactly as it does today.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from services import undx_capability_registry as registry
from services.undx_agent_contracts import ConfirmationPolicy, RiskLevel
from services.undx_brain import config as brain_config

__all__ = [
    "Reversal",
    "Fidelity",
    "Expectation",
    "Prediction",
    "Outcome",
    "predict",
    "check",
]


class Reversal(str, Enum):
    """How, and whether, a call could be taken back — judged before it is made."""

    #: Not a write. Nothing to reverse.
    NOT_A_WRITE = "not_a_write"
    #: An undo capability exists and its arguments are fully derivable from this call
    #: as it stands. ``saved.post.set`` negating one boolean is the clean case.
    EXACT_INVERSE = "exact_inverse"
    #: An undo capability exists but needs the canonical id of a resource this call has
    #: not created yet. Reversibility is contingent on the write *verifying*; an
    #: unverified write leaves nothing to aim the undo at.
    PENDING_IDENTITY = "pending_identity"
    #: No undo capability, but the call overwrites fields rather than removing the
    #: resource. Recoverable if and only if the prior values were read beforehand.
    REQUIRES_PRE_READ = "requires_pre_read"
    #: No undo capability and nothing to overwrite: the call acts on the resource's
    #: existence. Re-creating it afterwards produces a different resource with a
    #: different id, which is not the same as reversing this.
    IRRECOVERABLE = "irrecoverable"

    @property
    def reversible_now(self) -> bool:
        """True only for the case where reversal needs nothing that does not yet exist."""
        return self is Reversal.EXACT_INVERSE


class Fidelity(str, Enum):
    """How a prediction fared against what was actually observed."""

    #: Every predicted field was read back with the predicted value.
    CONFIRMED = "confirmed"
    #: At least one predicted field was read back with a different value. This is the
    #: outcome worth having: the system said something falsifiable and it was falsified.
    CONTRADICTED = "contradicted"
    #: The observation did not include a field that was predicted. Not a hit and not a
    #: miss — scoring it either way would corrupt the record.
    UNOBSERVED = "unobserved"
    #: Nothing was predicted, so nothing can be scored.
    NOTHING_PREDICTED = "nothing_predicted"


@dataclass(frozen=True)
class Expectation:
    """One field, and the value the verifier should read back for it."""

    field_name: str
    value: Any
    #: Where the value came from. Always ``"argument"`` today; held as a field so that a
    #: future source (a pre-read, a default) is distinguishable at the point of use
    #: rather than inferred from the value's shape.
    source: str = "argument"


@dataclass(frozen=True)
class Prediction:
    """What a proposed call would do, derived entirely from what it declares."""

    ok: bool = False
    capability_id: str = ""
    is_write: bool = False
    risk: str = ""
    confirmation: str = ""
    #: The resource this call names, as a bare string. Empty for reads.
    target: str = ""
    #: Field-by-field post-state the verifier should confirm.
    expected: tuple[Expectation, ...] = ()
    #: Verified fields whose expected value is not derivable from the arguments, because
    #: it follows from *which capability was chosen* rather than from what was passed.
    #: Named rather than guessed — see the module docstring.
    implied_fields: tuple[str, ...] = ()
    reversal: Reversal = Reversal.NOT_A_WRITE
    undo_capability_id: str = ""
    #: The argument set that would reverse this call, or ``None`` when one cannot
    #: honestly be built yet. ``None`` and ``{}`` differ; see
    #: :meth:`CapabilitySpec.undo_arguments`.
    undo_arguments: dict[str, Any] | None = None
    undo_risk: str = ""
    undo_confirmation: str = ""
    #: True only when reversing costs no more than the original: an undo that is a
    #: reversible write and needs no confirmation of its own.
    undo_is_cheap: bool = False
    #: Fields whose current value will be overwritten and is recorded nowhere. Reading
    #: them before the write is the only thing that makes the write recoverable.
    pre_read_fields: tuple[str, ...] = ()
    #: Other declared writes against the same resource. Broad: shares audit category
    #: and target field.
    also_writes_this_resource: tuple[str, ...] = ()
    #: The narrower subset that would actually clobber this call's effect — overlapping
    #: verified fields, or a capability acting on the resource's existence.
    conflicting_writes: tuple[str, ...] = ()
    #: What this prediction takes on faith because it did not query anything.
    assumes: tuple[str, ...] = ()
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        """True when this call could be taken back with nothing that does not yet exist."""
        return self.ok and self.reversal.reversible_now


@dataclass(frozen=True)
class Outcome:
    """A prediction lined up against what was actually read back."""

    ok: bool = False
    capability_id: str = ""
    fidelity: Fidelity = Fidelity.NOTHING_PREDICTED
    #: Fields predicted and read back with the predicted value.
    confirmed: tuple[str, ...] = ()
    #: ``(field, predicted, observed)`` for each field that came back different.
    contradicted: tuple[tuple[str, Any, Any], ...] = ()
    #: Predicted fields absent from the observation. Neither hit nor miss.
    unobserved: tuple[str, ...] = ()
    #: True when the write claimed reversibility that the observation did not deliver —
    #: a ``PENDING_IDENTITY`` call that produced no canonical id. The undo affordance
    #: must be withheld, and the receipt should still record that one was expected.
    undo_expected_but_unavailable: bool = False
    reason: str = ""
    notes: tuple[str, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        """True only when something was predicted and nothing contradicted it."""
        return self.ok and self.fidelity is Fidelity.CONFIRMED


# --------------------------------------------------------------------------- predict --

def predict(
    capability_id: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Prediction:
    """Describe what ``capability_id`` would do with ``arguments``, before doing it.

    Returns a refusal rather than raising when the capability is unknown. A predictor
    that raises on an unregistered id turns "I cannot describe this" into an exception
    the caller must handle to avoid failing the whole request, and the honest answer to
    an unknown capability is a prediction that declines to predict.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Prediction(
            ok=False,
            capability_id=str(capability_id or ""),
            reason="prediction is disabled; UNDX_BRAIN_PREDICTION_ENABLED is off",
            notes=notes,
        )

    spec = registry.get(str(capability_id or ""))
    if spec is None:
        return Prediction(
            ok=False,
            capability_id=str(capability_id or ""),
            reason="capability is not registered, so nothing about it is declared",
            notes=notes,
        )

    passed = dict(arguments or {})
    declared = {item.name for item in spec.fields}

    if not spec.is_write:
        return Prediction(
            ok=True,
            capability_id=spec.capability_id,
            is_write=False,
            risk=spec.risk,
            confirmation=spec.confirmation,
            reversal=Reversal.NOT_A_WRITE,
            assumes=("the read reflects state at the moment it is served, not now",),
            reason="read-only capability; there is no post-state to predict",
            notes=notes,
        )

    # ------------------------------------------------------------------ post-state --
    expected: list[Expectation] = []
    implied: list[str] = []
    for name in spec.verified_fields:
        if name in passed:
            expected.append(Expectation(field_name=name, value=passed[name]))
        elif name in declared:
            # Declared but not passed. The coercion layer may supply a default, and
            # this module does not read defaults, because a default it read here and
            # the gateway applied differently is a prediction that confirms itself.
            implied.append(name)
        else:
            # Not an argument at all: the verifier reads back a field whose value comes
            # from the capability's identity. ``reels.like`` checking ``liked`` is this.
            implied.append(name)

    # ------------------------------------------------------------------- reversal ---
    undo_spec = registry.get(spec.undo_capability_id) if spec.undo_capability_id else None
    needs_identity = any(token == "@target" for _, token in spec.undo_argument_map)
    # Pre-write there is no canonical id, and passing none is the whole point: this asks
    # ``undo_arguments`` the question it will be asked later, at the only time the answer
    # can still change what happens.
    undo_args = spec.undo_arguments(passed, canonical_ids=[])

    pre_read: tuple[str, ...] = ()
    if spec.undo_capability_id and undo_args is not None:
        reversal = Reversal.EXACT_INVERSE
    elif spec.undo_capability_id and needs_identity:
        reversal = Reversal.PENDING_IDENTITY
    elif spec.undo_capability_id:
        # An undo is named but its arguments could not be built from what was passed —
        # a missing field the map depends on. Not reversible now, and not for the
        # identity reason, so it is the same practical position as having no undo.
        reversal = Reversal.REQUIRES_PRE_READ if spec.verified_fields else Reversal.IRRECOVERABLE
        pre_read = tuple(spec.verified_fields)
    elif spec.verified_fields:
        reversal = Reversal.REQUIRES_PRE_READ
        pre_read = tuple(spec.verified_fields)
    else:
        reversal = Reversal.IRRECOVERABLE

    undo_cheap = bool(
        undo_spec is not None
        and undo_spec.risk == RiskLevel.REVERSIBLE_WRITE
        and undo_spec.confirmation == ConfirmationPolicy.NEVER
        and reversal is Reversal.EXACT_INVERSE
    )

    # --------------------------------------------------------------- blast radius ---
    broad, narrow = _resource_neighbours(spec)

    assumes = _assumptions(spec, reversal, pre_read)

    return Prediction(
        ok=True,
        capability_id=spec.capability_id,
        is_write=True,
        risk=spec.risk,
        confirmation=spec.confirmation,
        target=spec.canonical_target(passed),
        expected=tuple(expected),
        implied_fields=tuple(implied),
        reversal=reversal,
        undo_capability_id=spec.undo_capability_id,
        undo_arguments=undo_args,
        undo_risk=undo_spec.risk if undo_spec else "",
        undo_confirmation=undo_spec.confirmation if undo_spec else "",
        undo_is_cheap=undo_cheap,
        pre_read_fields=pre_read,
        also_writes_this_resource=broad,
        conflicting_writes=narrow,
        assumes=assumes,
        reason=_reason_for(spec, reversal, expected, implied, pre_read),
        notes=notes,
    )


# ----------------------------------------------------------------------------- check --

def check(
    prediction: Prediction,
    observed: Mapping[str, Any] | None = None,
    *,
    canonical_ids: Iterable[str] = (),
    env: Mapping[str, str] | None = None,
) -> Outcome:
    """Score a prediction against what verification actually read back.

    ``observed`` is the read-back state, keyed by field name — the verifier's output,
    not the mutation's response. Comparing against the mutation response would score the
    prediction against the same optimism that produced it.
    """
    enabled, notes = _enabled(env)
    if not enabled:
        return Outcome(
            ok=False,
            capability_id=getattr(prediction, "capability_id", ""),
            reason="prediction is disabled; UNDX_BRAIN_PREDICTION_ENABLED is off",
            notes=notes,
        )
    if not getattr(prediction, "ok", False):
        return Outcome(
            ok=False,
            capability_id=getattr(prediction, "capability_id", ""),
            reason="the prediction being checked did not itself succeed",
            notes=notes,
        )

    seen = dict(observed or {})
    confirmed: list[str] = []
    contradicted: list[tuple[str, Any, Any]] = []
    unobserved: list[str] = []

    for item in prediction.expected:
        if item.field_name not in seen:
            unobserved.append(item.field_name)
        elif _same(seen[item.field_name], item.value):
            confirmed.append(item.field_name)
        else:
            contradicted.append((item.field_name, item.value, seen[item.field_name]))

    if contradicted:
        fidelity = Fidelity.CONTRADICTED
    elif not prediction.expected:
        fidelity = Fidelity.NOTHING_PREDICTED
    elif unobserved:
        fidelity = Fidelity.UNOBSERVED
    else:
        fidelity = Fidelity.CONFIRMED

    ids = [str(item).strip() for item in canonical_ids if str(item).strip()]
    undo_missing = bool(
        prediction.reversal is Reversal.PENDING_IDENTITY and not ids
    )

    return Outcome(
        ok=True,
        capability_id=prediction.capability_id,
        fidelity=fidelity,
        confirmed=tuple(confirmed),
        contradicted=tuple(contradicted),
        unobserved=tuple(unobserved),
        undo_expected_but_unavailable=undo_missing,
        reason=_outcome_reason(fidelity, confirmed, contradicted, unobserved, undo_missing),
        notes=notes,
    )


# --------------------------------------------------------------------------- private --

def _enabled(env: Mapping[str, str] | None) -> tuple[bool, tuple[str, ...]]:
    resolution = brain_config.resolve(dict(env) if env is not None else None)
    values = resolution.values
    on = bool(values.get("UNDX_BRAIN_ENABLED", False)) and bool(
        values.get("UNDX_BRAIN_PREDICTION_ENABLED", False)
    )
    return on, tuple(resolution.notes)


def _resource_neighbours(spec: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Other writes against the same resource, broadly and then narrowly.

    Broad is "declared against the same thing": same audit category and same target
    field. Narrow is "would actually clobber this": overlapping verified fields, or a
    capability whose effect is on the resource's existence rather than on one of its
    fields — which contends with everything, because a resource that is gone has no
    fields left to disagree about.
    """
    broad: list[str] = []
    narrow: list[str] = []
    mine = set(spec.verified_fields)
    for other_id in registry.write_capability_ids():
        if other_id == spec.capability_id:
            continue
        other = registry.REGISTRY[other_id]
        if other.audit_category != spec.audit_category:
            continue
        if other.target_field != spec.target_field:
            continue
        broad.append(other_id)
        theirs = set(other.verified_fields)
        if not mine or not theirs or (mine & theirs):
            narrow.append(other_id)
    return tuple(sorted(broad)), tuple(sorted(narrow))


def _assumptions(spec: Any, reversal: Reversal, pre_read: tuple[str, ...]) -> tuple[str, ...]:
    items = [
        "nothing was queried; this describes what the call declares, not what the "
        "resource currently holds",
        f"the resource named by {spec.target_field!r} exists and belongs to the caller",
    ]
    if reversal is Reversal.PENDING_IDENTITY:
        items.append(
            "reversal is available only if the write verifies and returns a canonical "
            "id; an unverified write leaves the undo with nothing to aim at"
        )
    if pre_read:
        items.append(
            "the prior values of " + ", ".join(pre_read) + " are not recorded anywhere, "
            "so they are unrecoverable unless read before this call"
        )
    return tuple(items)


def _reason_for(
    spec: Any,
    reversal: Reversal,
    expected: list[Expectation],
    implied: list[str],
    pre_read: tuple[str, ...],
) -> str:
    parts = [f"{len(expected)} field(s) predicted"]
    if implied:
        parts.append(f"{len(implied)} implied by the capability rather than the arguments")
    parts.append(f"reversal={reversal.value}")
    if pre_read:
        parts.append("pre-read required for " + ", ".join(pre_read))
    return "; ".join(parts)


def _outcome_reason(
    fidelity: Fidelity,
    confirmed: list[str],
    contradicted: list[tuple[str, Any, Any]],
    unobserved: list[str],
    undo_missing: bool,
) -> str:
    parts = [fidelity.value]
    if confirmed:
        parts.append(f"{len(confirmed)} confirmed")
    if contradicted:
        parts.append(
            "contradicted: "
            + "; ".join(f"{name} predicted {want!r} observed {got!r}" for name, want, got in contradicted)
        )
    if unobserved:
        parts.append("not read back: " + ", ".join(unobserved))
    if undo_missing:
        parts.append("undo was expected to become available and did not")
    return "; ".join(parts)


def _same(observed: Any, predicted: Any) -> bool:
    """Equality that treats a bool and its string spelling as the same claim.

    Read-back paths cross a JSON boundary and a SQLite boundary, and both of them turn
    ``True`` into something that is not ``True``. Comparing with ``==`` alone would
    report a contradiction every time a prediction was in fact correct, which trains
    whoever reads the record to ignore it.
    """
    if isinstance(observed, bool) or isinstance(predicted, bool):
        return _truthy(observed) == _truthy(predicted)
    if isinstance(observed, (int, float)) and isinstance(predicted, (int, float)):
        return float(observed) == float(predicted)
    return str(observed).strip().lower() == str(predicted).strip().lower()


_TRUE_WORDS = frozenset({"1", "true", "yes", "on", "t", "y"})


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in _TRUE_WORDS
