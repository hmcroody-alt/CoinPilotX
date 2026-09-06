"""Governed private context for UNDX, shaped so the dishonest answer is unsayable.

Why this exists on top of ``retrieval``
---------------------------------------
``retrieval.retrieve`` already applies the five gates and already returns
``conflicts`` and ``stale_flags`` beside ``relevant_facts``. That is the right
contract for a *reader*. It is the wrong contract for a *language model*,
because it puts the fact and the reason to doubt the fact in two different
lists, and every prompt builder that has ever existed eventually ships a version
that iterates the first list and forgets the second.

The failure is specific and it is not hypothetical. Two facts say the property
is worth 900,000 and 1,200,000, both current, both from sources the member
trusts. ``retrieval`` returns both in ``relevant_facts`` and a conflict entry in
``conflicts``. A prompt that renders ``relevant_facts`` produces two contradictory
lines with no indication they are in tension, and the model — doing exactly what
models do — resolves the tension. It picks one, or it averages them, or it takes
the more recent. Any of those is the model deciding a question about the
member's own affairs that only the member can decide, and Stage 21 exists to
make that impossible rather than unlikely.

The shape, and why it is this shape
-----------------------------------
:func:`answer_context` returns three lists that a caller cannot accidentally
merge, because they do not have the same fields:

``assertions``
    Settled facts. These carry a ``value`` and may be stated plainly.

``disputed``
    Contradicted facts. **These carry no ``value`` field at all.** Not an empty
    string, not ``None`` — absent. A prompt template that reads ``entry["value"]``
    raises a ``KeyError`` on a disputed entry rather than rendering a blank or a
    null, and a model handed the payload has no scalar to copy. The competing
    values live under ``candidates``, ordered by ``fact_id``, which is stated in
    the payload itself to be an arbitrary order and not a ranking. There is no
    ``recommended``, no ``most_likely``, no ``best``, and no confidence ordering
    across candidates, because every one of those is this module picking the
    winner on the model's behalf and then blaming the model for using it.

``qualifications``
    Things that must be said if anything is said. Each carries ``must_say``.

Whether anything may be claimed at all
--------------------------------------
``completeness.may_claim_complete`` is the second guarantee and the one that
comes from this codebase's own scar tissue. "I could not look" and "there is
nothing there" must never produce the same sentence. A bound that bit, a denial,
a conflict scan that hit its own cap, a locked office — any of them and UNDX may
not say "that is everything you have recorded", because it does not know that.
The flag is computed by conjunction over every source, so a new source added
later that forgets to report completeness makes the claim *less* available
rather than more.

``answerable`` is separate from ``may_claim_complete`` and the difference
matters: a truncated read is answerable ("here are some of your properties") and
not complete ("that is all of them"). Collapsing the two would either silence a
usable answer or license an unsupportable one.

What this module does not do
----------------------------
It does not rank, summarise, choose between candidates, decide what is relevant,
or write anything. It does not talk to a model provider. It has no owner
argument that is not the actor — the authorization gate lives in ``retrieval``
and is not re-implemented here, because a second implementation of a
authorization rule is a second thing to keep in sync and the first one to drift
is always the copy.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from . import contradictions as _contradictions
from . import model as _model
from . import retrieval as _retrieval

LOGGER = logging.getLogger("private_office.undx_context")

#: Facts placed in front of a model in one turn. Deliberately far below
#: ``retrieval.MAX_FACTS``: 400 assertions is not context, it is a document, and
#: a model handed a document answers from the part of it that happened to land
#: in attention. When this bound bites the answer is still usable and
#: ``may_claim_complete`` goes false, which is the honest combination.
MAX_ASSERTIONS = 60

#: Disputed groups carried in one envelope. A member with more than this many
#: live contradictions has a review-queue problem, not a question-answering
#: problem, and the qualification says so rather than listing forty of them.
MAX_DISPUTED = 12

#: Competing values shown inside one dispute. Two is the common case; the cap
#: exists so a pathological group cannot dominate the envelope.
MAX_CANDIDATES = 6


# ---------------------------------------------------------------------------
# Why UNDX might have to stay quiet
# ---------------------------------------------------------------------------
# A closed vocabulary rather than free text, for the same reason every other
# vocabulary in this package is closed: this string reaches a copy layer that
# has to render a different sentence for each case, and a caller that has to
# pattern-match on prose will match on the wrong substring the first time the
# wording is improved.
SILENT_DENIED = "policy_denied"
SILENT_NO_CONTEXT = "no_context_found"
SILENT_STORE_UNREADABLE = "store_unreadable"

SILENCE_REASONS: tuple[str, ...] = (
    SILENT_DENIED,
    SILENT_NO_CONTEXT,
    SILENT_STORE_UNREADABLE,
)

# ---------------------------------------------------------------------------
# Things that must be said alongside an answer
# ---------------------------------------------------------------------------
QUALIFY_DISPUTED = "some_facts_are_disputed"
QUALIFY_STALE = "some_facts_are_stale"
QUALIFY_TRUNCATED = "not_everything_was_read"
QUALIFY_UNVERIFIED = "some_facts_are_unverified"
QUALIFY_CEILING = "some_material_was_out_of_scope"

QUALIFICATIONS: tuple[str, ...] = (
    QUALIFY_DISPUTED,
    QUALIFY_STALE,
    QUALIFY_TRUNCATED,
    QUALIFY_UNVERIFIED,
    QUALIFY_CEILING,
)

#: Verification states that mean nothing has corroborated the claim yet. Named
#: positively — the states that are *not* here are the ones that carry support —
#: so a verification state added later is treated as unsupported until somebody
#: decides otherwise, which is the failure direction that under-claims.
UNSUPPORTED_VERIFICATION: frozenset[str] = frozenset({
    _model.VERIFICATION_UNVERIFIED,
    _model.VERIFICATION_SELF_ASSERTED,
    _model.VERIFICATION_PENDING_REVIEW,
    _model.VERIFICATION_EXPIRED,
    _model.VERIFICATION_FAILED,
    _model.VERIFICATION_DISPUTED,
})


def _fact_identity(fact: dict) -> tuple[str, str, str]:
    """The key a conflict is grouped by: subject and fact type.

    Read off the fact rather than off the conflict so the two cannot drift. A
    conflict entry that named a subject differently from the fact it competes
    with would silently stop matching, and the visible symptom would be a
    disputed fact quietly appearing in ``assertions`` — the exact outcome this
    module exists to prevent, arriving through the back door.
    """
    return (
        str(fact.get("subject_type") or ""),
        str(fact.get("subject_id") or ""),
        str(fact.get("fact_type") or ""),
    )


def _candidate(row: dict) -> dict:
    """One competing value, with everything needed to choose and no choice made.

    Carries what a *member* needs to resolve the dispute — where it came from,
    when it was observed, whether anything backs it — and deliberately omits
    anything that reads as a recommendation. There is no score here, and the
    ``confidence`` that the store holds is passed through untouched rather than
    normalised into a ranking, because a normalised score across candidates is a
    ranking however it is labelled.
    """
    provenance = row.get("provenance")
    return {
        "fact_id": int(row.get("fact_id") or row.get("id") or 0),
        "value": "" if row.get("typed_value") is None else str(row.get("typed_value")),
        "value_type": str(row.get("value_type") or ""),
        "provenance_type": str(row.get("provenance_type") or ""),
        "source": (provenance or {}).get("kind", "") if isinstance(provenance, dict) else "",
        "observed_at": str(row.get("observed_at") or ""),
        "confidence": row.get("confidence"),
        "stale": bool((row.get("freshness") or {}).get("stale")),
    }


def _disputed_entry(conflict: dict) -> dict:
    """One contradiction, with no winner and no field a winner could go in.

    The absent ``value`` is the whole mechanism. A dispute entry is shaped so
    that the code path which renders an assertion cannot render it: there is
    nothing to interpolate. That is a stronger guarantee than a ``disputed: true``
    flag beside a value, because a flag has to be *read* to have any effect and
    the failure being defended against is precisely a caller that did not read it.
    """
    competing = list(conflict.get("competing") or ())[:MAX_CANDIDATES]
    return {
        "conflict_id": str(conflict.get("conflict_id") or ""),
        "fact_type": str(conflict.get("fact_type") or ""),
        "subject_type": str(conflict.get("subject_type") or ""),
        "reason": str(conflict.get("reason") or ""),
        # Stated in the payload, not only in this docstring, because the reader
        # that needs to know is a prompt template and it will not be reading
        # Python. A model that receives `ordering: arbitrary` alongside the list
        # has been told the order means nothing; one that receives a bare list
        # has been told nothing and will assume the first is the best.
        "ordering": "arbitrary",
        "candidates": sorted(
            (_candidate(row) for row in competing),
            key=lambda entry: entry["fact_id"]),
        "candidate_count": len(competing),
        "truncated_candidates": len(conflict.get("competing") or ()) > MAX_CANDIDATES,
        # What would settle it. The member resolves; nothing here does.
        "resolvable_by": "owner",
    }


def _assertion(fact: dict) -> dict:
    """One settled fact, safe to state plainly — with its caveats attached.

    ``stale`` and ``verification`` travel *on the assertion* rather than in a
    parallel list. A caveat in a side list is a caveat that gets dropped: the
    prompt builder iterates the facts, renders each one, and the second list
    never enters the string. Attaching them means the only way to render the
    value is to have the qualification in hand at the same moment.
    """
    freshness = dict(fact.get("freshness") or {})
    verification = str(fact.get("verification_state") or _model.DEFAULT_VERIFICATION)
    return {
        "fact_id": int(fact.get("id") or 0),
        "fact_type": str(fact.get("fact_type") or ""),
        "domain": str(fact.get("domain") or ""),
        "value": "" if fact.get("typed_value") is None else str(fact.get("typed_value")),
        "value_type": str(fact.get("value_type") or ""),
        "observed_at": str(fact.get("observed_at") or ""),
        "provenance_type": str(fact.get("provenance_type") or ""),
        "verification_state": verification,
        # Derived here rather than left to the caller, because "is this backed
        # by anything" is a question about a closed vocabulary and a caller
        # answering it from the raw state is a second authority on that
        # vocabulary that will drift when a state is added.
        "supported": verification not in UNSUPPORTED_VERIFICATION,
        "stale": bool(freshness.get("stale")),
        "age_days": freshness.get("age_days"),
    }


def _empty_envelope(*, owner: int, intent: str, silence: str,
                    denied: str = "") -> dict:
    """A refusal, shaped exactly like an answer so a caller cannot mishandle it.

    Every list is present and empty, every flag is present and false. The
    alternative — returning ``None``, or omitting keys on the refusal path — is
    how a caller ends up with an ``AttributeError`` in the one code path that
    runs when something has already gone wrong.

    ``may_claim_complete`` is false here and that is the important line in this
    function. A denial produces an empty ``assertions`` list, and an empty list
    is indistinguishable from "this member has recorded nothing" unless
    something says otherwise. Stage 176B in this repo was exactly that
    confusion, and it ran for weeks.
    """
    return {
        "owner_user_id": int(owner),
        "intent": str(intent or ""),
        "answerable": False,
        "silence_reason": silence,
        "denied": denied,
        "assertions": [],
        "disputed": [],
        "qualifications": [],
        "completeness": {
            "may_claim_complete": False,
            "reasons": [silence] if silence else [],
        },
        "counts": {
            "assertions": 0,
            "disputed": 0,
            "stale": 0,
            "unsupported": 0,
        },
    }


def answer_context(
    cur,
    *,
    owner_user_id: int,
    actor_user_id: int | None = None,
    intent: str = _retrieval.INTENT_GENERAL,
    purpose: str = "undx_answer",
    domains: Sequence[str] | None = None,
    fact_types: Sequence[str] | None = None,
    seed_node_ids: Sequence[object] | None = None,
    max_assertions: int = MAX_ASSERTIONS,
) -> dict:
    """Governed, conflict-aware private context for one question.

    Runs :func:`retrieval.retrieve` — which applies all five gates, writes the
    audit row and emits the telemetry — and then re-shapes the result so that a
    disputed fact cannot be stated as settled and an incomplete read cannot be
    stated as exhaustive.

    Never raises for a policy outcome. A denial is a normal result that the
    caller has to describe to the member, so it comes back as an envelope with
    ``answerable`` false and a ``silence_reason``. Genuine faults are allowed to
    propagate; a store this process cannot read is not a store with nothing in
    it, and swallowing that here would recreate the confusion the whole module
    is built to prevent.
    """
    owner = int(owner_user_id or 0)
    actor = int(actor_user_id if actor_user_id is not None else owner)
    wanted = str(intent or "").strip().lower()

    context = _retrieval.retrieve(
        cur,
        owner_user_id=owner,
        actor_user_id=actor,
        intent=wanted,
        purpose=purpose,
        domains=domains,
        fact_types=fact_types,
        seed_node_ids=seed_node_ids,
        include_conflicts=True,
    )

    denied = str(context.get("denied") or "")
    if denied:
        # The denial reason is carried through rather than translated, because
        # the caller needs to distinguish "you asked about something this intent
        # does not cover" from "you are not the owner of this store" and only
        # the first has a useful thing to say back to the member.
        return _empty_envelope(owner=owner, intent=wanted,
                               silence=SILENT_DENIED, denied=denied)

    facts = list(context.get("relevant_facts") or ())
    conflicts = list(context.get("conflicts") or ())

    # Group the conflicts by what they are about, so a fact can be tested for
    # dispute by identity rather than by scanning a list per fact — and, more to
    # the point, so the test is total. Every fact is looked up; a fact whose
    # group is present goes to `disputed` and cannot reach `assertions`, because
    # the two lists are built in one pass over the same collection rather than
    # by two independent filters that could both accept the same row.
    disputed_keys: dict[tuple[str, str, str], dict] = {}
    for conflict in conflicts:
        key = (
            str(conflict.get("subject_type") or ""),
            str(conflict.get("subject_id") or ""),
            str(conflict.get("fact_type") or ""),
        )
        disputed_keys.setdefault(key, conflict)

    assertions: list[dict] = []
    seen_disputes: dict[str, dict] = {}
    dropped_assertions = False

    for fact in facts:
        key = _fact_identity(fact)
        conflict = disputed_keys.get(key)
        if conflict is not None:
            entry = _disputed_entry(conflict)
            # Keyed by conflict_id so N competing facts produce one dispute
            # rather than N. Presenting the same disagreement twice would read
            # as two disagreements, which is its own species of wrong answer.
            seen_disputes.setdefault(entry["conflict_id"] or str(key), entry)
            continue
        if len(assertions) >= max(1, min(int(max_assertions), MAX_ASSERTIONS)):
            dropped_assertions = True
            continue
        assertions.append(_assertion(fact))

    disputed = list(seen_disputes.values())[:MAX_DISPUTED]
    dropped_disputes = len(seen_disputes) > MAX_DISPUTED

    stale_count = sum(1 for entry in assertions if entry["stale"])
    unsupported_count = sum(1 for entry in assertions if not entry["supported"])

    truncation = dict(context.get("truncated") or {})
    read_truncated = bool(truncation.get("nodes") or truncation.get("edges")
                          or truncation.get("facts"))

    qualifications: list[dict] = []
    if disputed:
        qualifications.append({
            "kind": QUALIFY_DISPUTED,
            "count": len(disputed),
            # `must_say` is the contract with the copy layer: a qualification
            # that a renderer may drop at its discretion is a qualification that
            # will be dropped when the sentence gets long.
            "must_say": True,
        })
    if stale_count:
        qualifications.append({"kind": QUALIFY_STALE, "count": stale_count,
                               "must_say": True})
    if read_truncated or dropped_assertions or dropped_disputes:
        qualifications.append({"kind": QUALIFY_TRUNCATED,
                               "count": len(assertions), "must_say": True})
    if unsupported_count:
        # Not `must_say`. Most private facts are things the member typed and
        # saying "you told me this" after each one is noise that trains people
        # to ignore the qualifications that matter. It is carried so a caller
        # that is being asked to *rely* on a fact can check.
        qualifications.append({"kind": QUALIFY_UNVERIFIED,
                               "count": unsupported_count, "must_say": False})

    complete_reasons: list[str] = []
    if read_truncated:
        complete_reasons.append(QUALIFY_TRUNCATED)
    if dropped_assertions or dropped_disputes:
        complete_reasons.append(QUALIFY_TRUNCATED)
    if disputed:
        complete_reasons.append(QUALIFY_DISPUTED)

    envelope = {
        "owner_user_id": owner,
        "intent": wanted,
        # Answerable and complete are deliberately different questions. A
        # truncated read still supports "here are some of your properties"; what
        # it does not support is "that is all of them".
        "answerable": bool(assertions or disputed),
        "silence_reason": "" if (assertions or disputed) else SILENT_NO_CONTEXT,
        "denied": "",
        "assertions": assertions,
        "disputed": disputed,
        "qualifications": qualifications,
        "completeness": {
            "may_claim_complete": not complete_reasons,
            "reasons": sorted(set(complete_reasons)),
        },
        "counts": {
            "assertions": len(assertions),
            "disputed": len(disputed),
            "stale": stale_count,
            "unsupported": unsupported_count,
        },
        # Passed through so a caller can say which ceiling produced a short
        # answer. Same reason the record views carry it: a narrow result read
        # without its ceiling looks like an empty office.
        "sensitivity_ceiling": str(context.get("sensitivity_ceiling") or ""),
        "domains": list(context.get("domains") or ()),
    }

    LOGGER.info(
        "UNDX_CONTEXT intent=%s assertions=%s disputed=%s stale=%s complete=%s",
        wanted, len(assertions), len(disputed), stale_count,
        envelope["completeness"]["may_claim_complete"])
    return envelope


def unresolved_for_owner(cur, *, owner_user_id: int,
                         limit: int = MAX_DISPUTED) -> list[dict]:
    """Every live contradiction in this owner's store, winner-free.

    Separate from :func:`answer_context` because it answers a different
    question: not "what can be said about this" but "what has the member not
    settled". It is what lets UNDX answer *"is there anything you are unsure
    about?"* without first inventing a topic to retrieve against.

    Returns the same ``disputed`` entries — no ``value``, candidates in
    arbitrary order — because a conflict listed for review has exactly the same
    reason not to carry a winner as a conflict listed beside an answer.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    found = _contradictions.detect_conflicts(cur, owner_user_id=owner)
    capped = max(1, min(int(limit or MAX_DISPUTED), MAX_DISPUTED))
    return [_disputed_entry(conflict) for conflict in found[:capped]]
