#!/usr/bin/env python3
"""The UNDX command benchmark, as a behavioural test rather than a metadata audit.

``pulsesoc_undx_phase2_benchmark.py`` reports 2,336 cases and asserts, for each one,
that the tool name and permission it copied out of the registry equal the tool name and
permission in the registry. That is a tautology: it cannot fail while the file that
generates it and the file it checks against are the same file. Its case count is built
from 73 command bodies times 32 prefixes, so twenty-nine thirtieths of the number is
courtesy words. It is kept in place as historical evidence for the reports that cite it,
and it is not what this one does.

Every case here is executed against the real code:

1. **Routing.** ``undx_agent_runtime.match_capability`` — the deterministic matcher the
   runtime actually uses when no planner supplied a capability — must resolve the
   command to the expected capability id. The phrasings in the corpus were written to
   be unlike the registry's own intent phrases, so passing requires generalisation
   rather than recall.

2. **Policy.** ``undx_agent_policy.evaluate`` must return the decision the capability's
   own risk, write flag and confirmation requirement imply. A read that needs
   confirmation, or a write that does not, is a governance defect that a routing-only
   benchmark would never see.

3. **Response.** For reads, a synthetic-but-canonical ``ToolResult`` is put through
   ``build_plan`` and ``render``, and the rendered answer must pass
   ``validate_consistency`` and must not be the last-resort sentence. This is the
   silent-degradation check applied at benchmark scale: an answer discarded by its own
   guard looks, from outside, exactly like a system with nothing to say.

And two things the old benchmark had no notion of:

4. **Negatives.** Small talk, questions about UNDX, and requests PulseSoc cannot serve
   must route to *nothing*. A matcher that always finds something acts on greetings.

5. **Write guards.** Phrasings that mention a write while not asking for one — "should I
   unfollow him", "do not delete alert 3" — must not reach the write. A benchmark made
   only of imperatives cannot discover this class of error, and it is the class that
   costs the person something they cannot undo by asking again.

And two more, added once the first five stopped finding anything:

6. **Intent reachability.** Every intent phrase the registry declares must route to the
   capability that declares it. This is the weakest demand the matcher could be put
   under, which is exactly why it is worth making: a phrase that loses to another
   capability is vocabulary the registry believes it has and does not, and no message a
   person could send would exercise it, because the closest possible message — the
   phrase itself — goes elsewhere. It fails for a different reason than check 1 does. A
   paraphrase failing means the matcher is too narrow; a declared phrase failing means
   two capabilities are competing for the same words and one of them always loses.

7. **Runnable or answerable.** Routing correctly is not the same as being usable.
   Forty-seven of the eighty capabilities declare a required field with no default, so
   a message can reach the right capability and then arrive at the gateway with that
   field empty — and come back as a schema error naming a field the person has never
   heard of. Every corpus body is put through ``resolve_arguments`` and must end in one
   of two states: runnable, or asking a question phrased in the words of the person's
   message. Never neither. The check is deliberately not "every field is always
   filled" — "update alert 1 with a new threshold" names no threshold, and demanding
   extraction there would push the system toward guessing at a number that will later
   fire. What is demanded is that something actionable comes back. The split between
   the two acceptable states is reported in ``extraction_summary`` rather than
   asserted, because the *rate* is where silent narrowing would surface.

Usage::

    python3 scripts/pulsesoc_undx_command_benchmark.py            # summary
    python3 scripts/pulsesoc_undx_command_benchmark.py --json     # full report
    python3 scripts/pulsesoc_undx_command_benchmark.py --failures # only what failed

Exit status is 0 only when every case passes and the corpus covers every capability in
the registry.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.undx_benchmark_corpus import (  # noqa: E402
    COMMANDS, NEGATIVES, PREFIXES, WRITE_GUARDS)
from services.undx_agent_contracts import (  # noqa: E402
    AgentOutcome, ToolResult, VerificationResult, VerificationState)
from services.undx_agent_runtime import (  # noqa: E402
    _CHOICE_LABELS, Reference, _ids_named_in, _tokens, answer_for_choice, answer_pending,
    match_capability, missing_required, resolve_arguments)
from services.undx_capability_registry import REGISTRY  # noqa: E402
import services.undx_response_intelligence as ri  # noqa: E402

LAST_RESORT = "could not put this into words"

#: Any positive id. Resolution needs one for its signature; nothing in the extraction
#: cases depends on which account it names, because the only branch that reads an
#: account is stubbed out by ``_stub_alert_reference``.
_BENCHMARK_USER = 7


@dataclass
class Case:
    case_id: str
    command: str
    check: str
    expected: str
    observed: str = ""
    ok: bool = True
    notes: list[str] = field(default_factory=list)


def _records(capability_id: str) -> list[dict[str, object]]:
    """Canonical records for a read, shaped by what the capability returns.

    Synthetic, and it has to be: this benchmark runs without a database, and pointing it
    at one would make it a test of fixture data. What matters is that the *contract* is
    the real one — the ten keys ``_fact`` emits — because the response layer reads those
    keys and nothing else, so a fixture that satisfies the contract exercises the same
    code path a live row would.
    """
    kinds = {
        "activity.daily_summary": ("post_created", "notification", "message_received"),
        "search.global": ("profile", "post", "message"),
        "search.activity": ("notification", "post_created", "new_follower"),
        "security.activity.summary": ("security_session", "security_event"),
        "notifications.group_summary": ("notification", "new_follower"),
        "profile.activity.summary": ("post_created", "reel_activity", "notification"),
    }.get(capability_id, ("thing",))
    return [
        {"kind": kind, "title": f"{kind.replace('_', ' ').title()} {index}",
         "detail": "", "source": "pulsesoc", "source_id": str(index),
         "timestamp": f"2026-07-{12 + index % 3:02d}T09:00:00Z",
         "authorization_scope": "self_account_only", "native_route": "/x",
         "confidence": "high", "data": {"read": index % 2 == 0}}
        for index, kind in enumerate(kinds * 2, 1)
    ]


def _route_cases() -> list[Case]:
    cases: list[Case] = []
    for capability_id, phrasings in COMMANDS.items():
        for body_index, body in enumerate(phrasings, 1):
            for prefix_index, prefix in enumerate(PREFIXES, 1):
                command = f"{prefix}{body}"
                match = match_capability(command)
                observed = match.capability_id if match else ""
                cases.append(Case(
                    case_id=f"ROUTE-{capability_id}-{body_index:02d}-{prefix_index}",
                    command=command, check="routes_to_capability",
                    expected=capability_id, observed=observed,
                    ok=observed == capability_id))
    return cases


def _policy_cases() -> list[Case]:
    """The governance properties a capability's own metadata implies.

    Checked against the spec rather than against a hand-written expectation, because a
    hand-written table would be a second copy of the registry and would drift from it.
    What is being tested is internal coherence — a contradiction inside one spec.

    ``confirmation`` is a *string* (``never`` / ``contextual`` / ``always``), not a flag.
    An earlier version of this function read it as a boolean, which made every read look
    like it demanded confirmation and produced sixty-four failures that said nothing.
    The rule below is the one that actually carries weight:

    * A read must confirm ``never``. A read that stops to ask has misdeclared itself as
      something that changes the account.
    * A write must have a verifier. Without a read-back there is no way to settle
      whether the write landed, and the runtime would have to take the tool's word.
    * A write may confirm ``never`` only when it is genuinely cheap to be wrong about:
      reversible risk, idempotent, and naming the capability that undoes it. Skipping
      the confirmation card is defensible for a like you can unlike; it is not
      defensible for anything the person cannot walk back by asking again.

    Everything softer than that is a note. Whether ``feed.posts.like`` *ought* to
    confirm when ``reels.like`` does is a governance decision belonging to the owner,
    and a benchmark that failed on it would be asserting a preference as a defect.
    """
    cases: list[Case] = []
    for capability_id, spec in REGISTRY.items():
        confirmation = spec.confirmation
        notes: list[str] = []
        if not spec.is_write:
            expected = "read_confirms_never"
            observed = ("read_confirms_never" if confirmation == "never"
                        else f"read_confirms_{confirmation}")
        else:
            expected = "write_is_governed"
            problems = []
            if not spec.verifier:
                problems.append("no verifier")
            if confirmation == "never":
                if spec.risk != "reversible_write":
                    problems.append(f"skips confirmation at risk={spec.risk}")
                if not spec.idempotent:
                    problems.append("skips confirmation while not idempotent")
                if not spec.undo_capability_id:
                    problems.append("skips confirmation with no undo capability")
            observed = "write_is_governed" if not problems else "; ".join(problems)
            if spec.undo_capability_id and spec.undo_capability_id not in REGISTRY:
                notes.append(f"undo points at a capability that does not exist: "
                             f"{spec.undo_capability_id}")
            if not spec.undo_capability_id and confirmation != "never":
                notes.append("write declares no undo capability, so the only way back "
                             "is whatever the person can do by hand")
        cases.append(Case(
            case_id=f"POLICY-{capability_id}", command=capability_id,
            check="governance_coherence", expected=expected,
            observed=observed, ok=observed == expected, notes=notes))
    return cases


#: Actions that mean the same thing to a person regardless of what they are applied to.
#: The registry is free to govern them differently, but the divergence should be visible
#: rather than discovered by someone who liked a post expecting to be asked first.
_SIBLING_ACTIONS = {
    "like": ("feed.posts.like", "reels.like"),
    "unlike": ("feed.posts.unlike", "reels.unlike"),
    "save": ("saved.post.set", "reels.save"),
}


def _overlapping_surfaces() -> list[tuple[str, str, str, str]]:
    """Intent phrases of one capability that are contained in another's, token-wise.

    "show alert" sits inside ``crypto.alerts.list``'s "show alerts" once stemming has
    erased the plural; "follow user" sits inside "unfollow user" only in the surface
    string, not in tokens, which is why this compares tokens rather than characters.

    Containment is not by itself a defect. Antonym pairs necessarily overlap, and the
    scorer separates them — that is what ``_intent_reachability_cases`` proves. It is
    reported because an overlap is the precondition for the failure: a phrase that is
    a subsequence of another capability's phrase can only win on the tie-break, and a
    tie-break is not a decision. Knowing where they are is how the next person adding
    an intent knows which additions are load-bearing.
    """
    surfaces = [(cid, phrase, tuple(_tokens(phrase)))
                for cid, spec in REGISTRY.items() for phrase in spec.intents]
    found = []
    for cid_a, phrase_a, tokens_a in surfaces:
        for cid_b, phrase_b, tokens_b in surfaces:
            if cid_a == cid_b or len(tokens_a) >= len(tokens_b):
                continue
            if any(tokens_b[i:i + len(tokens_a)] == tokens_a
                   for i in range(len(tokens_b) - len(tokens_a) + 1)):
                found.append((cid_a, phrase_a, cid_b, phrase_b))
    return sorted(found)


def _intent_reachability_cases() -> list[Case]:
    """Every intent phrase must route to the capability that declares it.

    This is the weakest thing the matcher could possibly be asked to do, and for that
    reason the most useful: a phrase that does not reach its own capability is
    vocabulary the registry believes it has and does not. There is no message a person
    could send that would exercise it, because the closest possible message — the
    phrase itself — goes somewhere else.

    It is not circular in the way registry-derived cases usually are. The corpus cases
    assert that *paraphrases* route, which is the real behaviour; this asserts only
    that the registry's own vocabulary is not dead on arrival. The two fail for
    different reasons: a paraphrase failing means the matcher is too narrow, a phrase
    failing means two capabilities are competing for the same words and one of them
    always loses.
    """
    cases: list[Case] = []
    for capability_id, spec in REGISTRY.items():
        for index, phrase in enumerate(spec.intents, 1):
            match = match_capability(phrase)
            observed = match.capability_id if match else "(nothing)"
            cases.append(Case(
                case_id=f"INTENT-{capability_id}-{index:02d}", command=phrase,
                check="intent_phrase_is_reachable", expected=capability_id,
                observed=observed, ok=observed == capability_id,
                notes=[] if observed == capability_id else
                ["the registry declares this phrase but another capability wins it"]))
    return cases


def _stub_alert_reference(user_id, text, *, explicit_id=None):
    """A resolved alert, without an account.

    Resolving "my Bitcoin alert" is a question about the caller's alerts, and the
    benchmark has no caller. Answering it with a fixed id is what lets the extraction
    cases ask their own question — does the *sentence* fill the capability's fields —
    rather than accidentally measuring fixture data. Ownership is enforced by the real
    resolver on the request path, which this never replaces; see ``resolve_arguments``.
    """
    return Reference(1, int(explicit_id or 5))


def _named_id_cases() -> list[Case]:
    """A body that writes an id into the sentence must resolve to *that* id.

    The defect this encodes was invisible for seven batches because the id never
    reached the resolver: ``explicit_id`` was the only road in, and it is empty on a
    first turn, since extraction runs after resolution. So "pause alert 4" fell through
    to the listing path, which answers a question about the account rather than about
    the sentence — reporting "more than one of your alerts matches" on an account with
    two, and, on an account with one, pausing that one and reading it back as proof.

    What the stub below replaces is the *account*, not the reading. It performs the same
    two steps the real resolver does — read the ids the sentence names, then ask whether
    the account owns them — and only the second is faked, because the benchmark has no
    database. So this is not a test of ``_ids_named_in``, which is unit-tested against
    its own negatives elsewhere; it is a test that the id survives the rest of
    resolution, which is where it could still be lost. Extraction runs *after* the
    reference is resolved and reads numbers out of the same sentence, so "change alert 3
    to trigger at 95000" is precisely the shape where a later pass could overwrite the
    id it was handed. The adversarial part is the fallback: anything the sentence does
    not name comes back ambiguous, so a body cannot pass by accident of the stub being
    generous.
    """
    def hostile(user_id, text, *, explicit_id=None):
        named = [int(explicit_id)] if explicit_id else _ids_named_in(text)
        if len(named) == 1:
            return Reference(1, named[0])
        if len(named) > 1:
            return Reference(len(named), 0,
                             [{"alert_id": value, "symbol": "BTC"} for value in named],
                             detail="I can change one alert at a time — which of those?")
        return Reference(len(_AMBIGUOUS), 0, [dict(row) for row in _AMBIGUOUS],
                         detail="More than one of your alerts matches that description.")

    cases: list[Case] = []
    for capability_id, bodies in COMMANDS.items():
        spec = REGISTRY[capability_id]
        if not any(item.name == "alert_id" for item in spec.fields):
            continue
        for index, body in enumerate(bodies, 1):
            named = _ids_named_in(body)
            if not named:
                # Says no id, so there is nothing here to get right or wrong. The
                # chooser cases already cover what these bodies do.
                continue
            expected = str(named[0]) if len(named) == 1 else "asks which"
            resolution = resolve_arguments(_BENCHMARK_USER, spec, body, {},
                                           reference_resolver=hostile)
            if len(named) > 1:
                observed = ("asks which" if resolution.choice_field == "alert_id"
                            and resolution.unresolved is not None else "picked one")
            else:
                observed = str(resolution.arguments.get("alert_id"))
            cases.append(Case(
                case_id=f"NAMEDID-{capability_id}-{index:02d}", command=body,
                check="a_named_id_selects_that_row", expected=expected,
                observed=observed, ok=observed == expected,
                notes=[f"ids named in the sentence: {named}"]))
    return cases


#: The chooser the ambiguity cases are answered against. Ids 7 and 4, and the choice of
#: numbers is the point: neither coincides with a position, so a reply read as an id and
#: a reply read as a position can never accidentally agree. A chooser with ids 1 and 2
#: would let a confusion between the two readings pass every case below.
_AMBIGUOUS = [
    {"alert_id": 7, "symbol": "BTC", "display_name": "BTC alert", "status": "active"},
    {"alert_id": 4, "symbol": "ETH", "display_name": "ETH alert", "status": "active"},
]

#: Replies a person types at a chooser, and the alert each must select. Fixed once,
#: shared by every capability, and not derived from the candidate list — a reply
#: generated from the answer it is supposed to produce would assert only that the code
#: agrees with itself.
_CHOOSER_REPLIES = (
    ("the first one", 7),
    ("the second one", 4),
    ("alert 4", 4),
    ("the ethereum one", 4),
    # And two that must select nothing: a number belonging to no candidate, and a
    # sentence about something else entirely.
    ("99", None),
    ("what is the weather", None),
)


def _ambiguous_alert_reference(user_id, text, *, explicit_id=None):
    """Two alerts match, every time. The other half of ``_stub_alert_reference``.

    An account with one alert never exercises the chooser, and an account is exactly
    what the benchmark does not have. Forcing ambiguity for every alert-bearing body
    is what turns "the mechanism works on the example someone thought of" into "every
    phrasing in the corpus that names an alert produces a chooser a person can answer".
    """
    if explicit_id:
        return Reference(1, int(explicit_id))
    return Reference(len(_AMBIGUOUS), 0, [dict(row) for row in _AMBIGUOUS],
                     detail="More than one of your alerts matches that description.")


def _chooser_cases() -> list[Case]:
    """Every "which one?" the corpus provokes must be answerable by the next message.

    Batch 6 proved that a question about a *missing field* has somewhere to land. This
    is the other road into the same question, and it was a dead end for exactly as long:
    a message that named an alert ambiguously rendered a list of candidates and then
    ignored every reply that pointed at one of them.

    Two things are asserted per body. That the turn stops with a chooser rather than a
    schema error — otherwise there is nothing to answer. And that the four replies below
    select the alert they name while the two decoys select nothing. The decoys carry
    most of the weight: a reader permissive enough to always return a candidate would
    satisfy the first four cases and fail these, which is the difference between reading
    an answer and guessing one.
    """
    cases: list[Case] = []
    for capability_id, bodies in COMMANDS.items():
        spec = REGISTRY[capability_id]
        if not any(item.name == "alert_id" for item in spec.fields):
            continue
        for index, body in enumerate(bodies, 1):
            resolution = resolve_arguments(_BENCHMARK_USER, spec, body, {},
                                           reference_resolver=_ambiguous_alert_reference)
            reference = resolution.unresolved
            if reference is None or not reference.candidates:
                observed = "no chooser was offered"
            elif resolution.choice_field != "alert_id":
                observed = f"chooser is waiting on {resolution.choice_field!r}"
            else:
                wrong = [f"{reply!r}->{answer_for_choice(reference.candidates, reply)}"
                         for reply, expected in _CHOOSER_REPLIES
                         if answer_for_choice(reference.candidates, reply) != expected]
                observed = "answerable" if not wrong else "misread " + ", ".join(wrong)
            cases.append(Case(
                case_id=f"CHOOSE-{capability_id}-{index:02d}", command=body,
                check="the_chooser_is_answerable", expected="answerable",
                observed=observed, ok=observed == "answerable",
                notes=[f"{len(_CHOOSER_REPLIES)} replies, "
                       f"{sum(1 for _, want in _CHOOSER_REPLIES if want is None)} of them decoys"]))
    return cases


def _extraction_cases() -> list[Case]:
    """A routed message must end up either runnable or answerable. Never neither.

    This is the check that catches the class of defect Batch 5 was built around. A
    capability can be present in the registry, matched correctly, and still unusable,
    because the sentence that reached it left a required field empty and the reply was
    a schema error naming that field. Routing cases cannot see this: they stop at the
    capability id, which is right. Response cases cannot see it either: they start from
    a tool result, which by then exists.

    The assertion is deliberately not "every field is always filled". Some sentences do
    not contain the value — "update alert 1 with a new threshold" names no threshold —
    and demanding extraction there would push the system toward guessing. What is
    demanded is that the person gets something they can act on: either the capability
    runs, or a question is asked in words that refer to their message rather than to
    the schema.
    """
    cases: list[Case] = []
    for capability_id, bodies in COMMANDS.items():
        spec = REGISTRY[capability_id]
        for index, body in enumerate(bodies, 1):
            resolution = resolve_arguments(_BENCHMARK_USER, spec, body, {},
                                           reference_resolver=_stub_alert_reference)
            notes: list[str] = []
            if not resolution.missing:
                observed = "runnable"
            elif resolution.unresolved is not None and resolution.unresolved.detail.strip():
                observed = "asks_a_question"
                notes.append(f"missing {', '.join(resolution.missing)}: "
                             f"{resolution.unresolved.detail}")
            else:
                observed = f"unusable: missing {', '.join(resolution.missing)}"
            cases.append(Case(
                case_id=f"EXTRACT-{capability_id}-{index:02d}", command=body,
                check="runnable_or_answerable",
                expected="runnable_or_answerable", observed=observed,
                ok=observed in {"runnable", "asks_a_question"}, notes=notes))
    return cases


def _extraction_summary() -> dict[str, object]:
    """How much of the corpus runs outright, and where the rest stops.

    Reported rather than asserted. A capability that has to ask is not broken — asking
    is the correct answer to "like this post" from a runtime that cannot see which post
    is open — but the *rate* is how silent narrowing would show up, so it is carried in
    the report where a change to it is visible.
    """
    runnable = 0
    asking: dict[str, int] = {}
    total = 0
    for capability_id, bodies in COMMANDS.items():
        spec = REGISTRY[capability_id]
        for body in bodies:
            total += 1
            resolution = resolve_arguments(_BENCHMARK_USER, spec, body, {},
                                           reference_resolver=_stub_alert_reference)
            if resolution.missing:
                asking[capability_id] = asking.get(capability_id, 0) + 1
            else:
                runnable += 1
    return {"bodies": total, "runnable": runnable, "asks_a_question": total - runnable,
            "by_capability": dict(sorted(asking.items(), key=lambda kv: -kv[1]))}


#: A reply of the shape each field kind calls for, written as a person would type it.
#:
#: Deliberately not derived from the expected answer. Generating "9" from the fact that
#: the field wants ``9`` would make the check assert that the code agrees with itself,
#: which is the exact defect the old phase-2 benchmark was retired for. These are five
#: fixed strings chosen once, by kind, and every capability with a field of that kind
#: gets the same one — so a capability that only passes because its reply was tailored
#: to it cannot exist.
_REPLIES = {
    "int": "9",
    "float": "95000",
    "identifier": "bitcoin",
    "str": "the thing i am looking for",
    "bool": "yes",
}


def _reply_for(field: object) -> str:
    """What a person would type in answer to the question about this field."""
    choices = tuple(getattr(field, "choices", ()) or ())
    if choices:
        # Answered in the words the question offered, not in the enum's internal
        # value. The question says "English"; a check that replied "en" would pass
        # while the product's own phrasing went unread.
        first = str(choices[0])
        return str(_CHOICE_LABELS.get(first, first))
    return _REPLIES.get(str(getattr(field, "kind", "") or ""), "yes")


def _continuation_cases() -> list[Case]:
    """Every question the corpus provokes must be answerable by the next message.

    Batch 5 stopped the runtime replying with schema errors and had it ask questions
    instead. This is the check that the question is not rhetorical. Fifty-three corpus
    bodies route correctly and stop to ask something; each of them is followed here by
    a reply of the shape the outstanding field calls for, and the pair must end with
    every required field filled.

    It is worth being clear about what this does and does not cover. The store — the
    row, its expiry, its single use, the fact that it cannot be redeemed as an approval
    — is proven in ``tests/undx_agent/test_continuation.py`` against a real database,
    because those are properties of SQL and a benchmark with no database would only be
    able to assert them against a fiction. What this adds is corpus scale: not "the
    mechanism works on the example someone thought of", but "of the eight hundred
    sentences in the corpus, every one that ends in a question ends in an answerable
    one". Those fail for different reasons and neither substitutes for the other.
    """
    cases: list[Case] = []
    for capability_id, bodies in COMMANDS.items():
        spec = REGISTRY[capability_id]
        fields = {item.name: item for item in spec.fields}
        for index, body in enumerate(bodies, 1):
            first = resolve_arguments(_BENCHMARK_USER, spec, body, {},
                                      reference_resolver=_stub_alert_reference)
            if not first.missing:
                continue
            reply = " ".join(_reply_for(fields[name]) for name in first.missing
                             if name in fields)
            filled = answer_pending(spec, first.arguments, first.missing, reply)
            if filled is None:
                observed = f"the reply {reply!r} was not read as an answer"
            elif missing_required(spec, filled):
                observed = ("still missing "
                            + ", ".join(missing_required(spec, filled)))
            else:
                observed = "answerable"
            cases.append(Case(
                case_id=f"CONTINUE-{capability_id}-{index:02d}", command=f"{body} / {reply}",
                check="the_question_is_answerable", expected="answerable",
                observed=observed, ok=observed == "answerable",
                notes=[f"asked for {', '.join(first.missing)}"]))
    return cases


def _governance_notes() -> list[dict[str, object]]:
    """Observations, not failures. Emitted so the owner decides, and decides knowingly."""
    notes: list[dict[str, object]] = []
    for action, ids in _SIBLING_ACTIONS.items():
        present = {cid: REGISTRY[cid].confirmation for cid in ids if cid in REGISTRY}
        if len(set(present.values())) > 1:
            notes.append({"kind": "sibling_confirmation_divergence",
                          "action": action, "confirmation": present})
    missing_undo = sorted(cid for cid, spec in REGISTRY.items()
                          if spec.is_write and not spec.undo_capability_id)
    if missing_undo:
        notes.append({"kind": "write_without_undo", "capabilities": missing_undo})
    overlaps = _overlapping_surfaces()
    if overlaps:
        # Across a write boundary is the subset worth looking at first: an overlap
        # between two reads costs an answer, an overlap between a read and a write
        # costs an action.
        crossing = [pair for pair in overlaps
                    if REGISTRY[pair[0]].is_write != REGISTRY[pair[2]].is_write]
        notes.append({"kind": "overlapping_intent_surface",
                      "count": len(overlaps), "crossing_a_write_boundary": crossing,
                      "pairs": overlaps})
    return notes


def _response_cases() -> list[Case]:
    """One rendered answer per read capability, through the real renderer and guard."""
    cases: list[Case] = []
    verification = VerificationResult(state=VerificationState.IMPOSSIBLE)
    for capability_id, spec in REGISTRY.items():
        if spec.is_write:
            continue
        for degraded in ((), ("pulse_notifications",)):
            result = ToolResult(
                ok=True, tool_name=spec.tool_name, capability_id=capability_id,
                records=_records(capability_id), degraded_sources=list(degraded))
            tag = "degraded" if degraded else "clean"
            try:
                plan = ri.build_plan(spec, AgentOutcome.COMPLETED, result, verification,
                                     question=COMMANDS[capability_id][0])
                text = ri.render(plan, spec, AgentOutcome.COMPLETED, result,
                                 verification)
                problems = ri.validate_consistency(plan, text)
                notes = list(problems)
                ok = not problems and LAST_RESORT not in text
                if LAST_RESORT in text:
                    notes.append("fell back to the last-resort sentence")
                observed = "consistent" if ok else "; ".join(notes) or "inconsistent"
            except Exception as exc:  # noqa: BLE001
                ok, observed, notes = False, repr(exc), ["render raised"]
            cases.append(Case(
                case_id=f"RESPONSE-{capability_id}-{tag}", command=capability_id,
                check="renders_a_consistent_answer", expected="consistent",
                observed=observed, ok=ok, notes=notes))
    return cases


def _negative_cases() -> list[Case]:
    cases: list[Case] = []
    for reason, messages in NEGATIVES.items():
        for index, message in enumerate(messages, 1):
            match = match_capability(message)
            observed = match.capability_id if match else ""
            cases.append(Case(
                case_id=f"NEGATIVE-{reason}-{index:02d}", command=message,
                check="routes_to_nothing", expected="", observed=observed,
                ok=not observed))
    return cases


def _write_guard_cases() -> list[Case]:
    """Landing on a read, or on nothing, is fine. Landing on the write is not."""
    cases: list[Case] = []
    for index, (message, forbidden) in enumerate(WRITE_GUARDS, 1):
        match = match_capability(message)
        observed = match.capability_id if match else ""
        ok = observed != forbidden
        notes = []
        if observed and REGISTRY[observed].is_write and observed != forbidden:
            notes.append(f"routed to a different write: {observed}")
            ok = False
        cases.append(Case(
            case_id=f"WRITEGUARD-{index:02d}", command=message,
            check="does_not_reach_a_write", expected=f"not {forbidden}",
            observed=observed or "(nothing)", ok=ok, notes=notes))
    return cases


def audit() -> dict:
    cases = (_route_cases() + _policy_cases() + _response_cases()
             + _negative_cases() + _write_guard_cases()
             + _intent_reachability_cases() + _extraction_cases()
             + _continuation_cases() + _chooser_cases() + _named_id_cases())
    uncovered = sorted(set(REGISTRY) - set(COMMANDS))
    failures = [asdict(case) for case in cases if not case.ok]
    by_check: dict[str, dict[str, int]] = {}
    for case in cases:
        bucket = by_check.setdefault(case.check, {"total": 0, "passed": 0})
        bucket["total"] += 1
        bucket["passed"] += int(case.ok)
    return {
        "ok": not failures and not uncovered,
        "case_count": len(cases),
        "distinct_commands": len({case.command for case in cases}),
        "distinct_command_bodies": sum(len(v) for v in COMMANDS.values()),
        "capabilities_in_registry": len(REGISTRY),
        "capabilities_covered": len(COMMANDS),
        "capabilities_uncovered": uncovered,
        "by_check": by_check,
        "extraction_summary": _extraction_summary(),
        "governance_notes": _governance_notes(),
        "failure_count": len(failures),
        "failures": failures,
    }


if __name__ == "__main__":
    report = audit()
    if "--json" in sys.argv:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif "--failures" in sys.argv:
        for failure in report["failures"]:
            print(f"{failure['case_id']}: {failure['command']!r} "
                  f"expected={failure['expected']!r} got={failure['observed']!r}")
        print(f"\n{report['failure_count']} failures of {report['case_count']} cases")
    else:
        print(json.dumps({k: v for k, v in report.items() if k != "failures"},
                         indent=2, sort_keys=True))
    raise SystemExit(0 if report["ok"] else 1)
