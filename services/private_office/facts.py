"""Stage 7 + 12 — the only way a private fact is written or read.

Why there is exactly one writer
-------------------------------
A fact store's guarantees are all *invariants across writes*: every row has a
provenance, every row has a sensitivity, every row is placed in time, no row
belongs to a user who is not its owner. None of those survive a second writer.
They do not survive it in a dramatic way either — the second writer is added by
someone shipping a feature, it omits ``sensitivity`` because the column is
nullable in their head, and six weeks later the retrieval layer's sensitivity
ceiling is silently letting rows through that were never classified.

So feature code does not ``INSERT INTO private_facts``. It calls
:func:`record_fact`. A static guard enforces this rather than a code review
habit; see ``tests/private_office/test_private_write_boundary.py``.

Normalization, and why the value is stored twice
------------------------------------------------
``typed_value`` holds the canonical text of the value and ``value_number``
holds it again as a float when the type is numerically comparable. The second
column is what makes Stage 13 possible.

``services/undx_brain/facts.py`` documents at length what the alternative looks
like in this codebase. The existing UNDX contradiction check compares claim
*strings*, so recording ``"btc alert threshold is 50000"`` from two independent
sources is flagged as a conflict — that is corroboration — while ``50000`` and
``60000`` from the same source are both filed active with nothing marking
either. Two claims that disagree are by construction different strings, so a
string comparison reliably detects agreement and lets disagreement through.

Stage 13 asks for the opposite, and asks it to distinguish an ownership share of
35% from 40% in the same period. That is a magnitude question. It is answerable
against ``value_number`` and unanswerable against text.

Dedupe, and what counts as "the same fact"
------------------------------------------
Two rows are the same fact when the owner, subject, fact type, value, source
and validity window all match. Everything in that list is in ``fact_key``.

Crucially **provenance is part of the key**, so two independent sources
asserting the same value produce two rows rather than one. That is not
redundancy — it is the difference between "one system said this" and "two
systems agree", which is exactly the signal the contradiction engine needs and
exactly the signal a naive dedupe destroys. A repeat from the *same* source
refreshes ``observed_at`` instead of inserting, because a second reading from
one system is a newer look at the same claim, not a second opinion.

Freshness is computed, not stored
---------------------------------
There is no ``is_stale`` column. Staleness is a function of ``observed_at`` and
the moment of the question, and a boolean written at insert time is wrong within
hours and stays wrong. :func:`staleness` answers it at read time, and retrieval
flags rather than hides — a fact that has aged past its horizon may still be
quoted, but only *as of* when it was observed, never as a description of how
things are now. Citing a six-week-old reading as current state is how a system
tells somebody their policy is set to a value they changed in between.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Sequence

from services.private_office import audit as _audit
from services.private_office import model as _model
from services.private_office import schema as _schema
from services.private_office import telemetry as _telemetry

LOGGER = logging.getLogger("private_office.facts")

MAX_TEXT_VALUE = 512
MAX_FACT_TYPE = 64
MAX_SUBJECT_ID = 128

SUBJECT_NODE = "NODE"

STATUS_WRITTEN = "written"
STATUS_REFRESHED = "refreshed"
STATUS_REJECTED = "rejected"

#: Outcomes of a lifecycle operation, mirroring
#: ``telemetry.LIFECYCLE_OUTCOME_VOCAB``.
OUTCOME_APPLIED = "applied"
OUTCOME_UNCHANGED = "unchanged"
OUTCOME_REFUSED = "refused"
OUTCOME_NOT_FOUND = "not_found"

#: Operation names, written to ``private_fact_history.operation`` and published
#: as telemetry. A closed set: history that could carry an arbitrary operation
#: string would be a free-text column wearing a different name.
OP_CREATE = "create"
OP_REFRESH = "refresh"
OP_CONFIRM = "confirm"
OP_REVISE = "revise"
OP_DISPUTE = "dispute"
OP_ARCHIVE = "archive"
OP_REVOKE = "revoke"
OP_EXPIRE = "expire"
OP_SUPERSEDE = "supersede"
#: Stamp a row as one side of a live disagreement. Driven by the contradiction
#: engine, not by a person, which is why it is the only operation here whose
#: normal actor is the system.
OP_FLAG_CONFLICT = "flag_conflict"
#: Settle a disagreement in this row's favour. An owner action, always.
OP_RESOLVE = "resolve"
#: Something checkable was produced in support of this fact. The promotion this
#: causes is the only route to EVIDENCE_SUPPORTED, and it is deliberately not
#: available to the member's own say-so: `confirm` is a person's opinion,
#: `attach_evidence` is a person's opinion plus an item somebody else can open.
OP_ATTACH_EVIDENCE = "attach_evidence"
#: The last of that support was taken away. Demotes rather than restoring
#: whatever the fact was before, because "we no longer know why we believed
#: this" is a state that deserves a human, not a silent reversion.
OP_DETACH_EVIDENCE = "detach_evidence"
#: Put this fact in front of the member. Machine-driven, like OP_FLAG_CONFLICT.
OP_FLAG_REVIEW = "flag_review"

FACT_OPERATIONS: tuple[str, ...] = (
    OP_CREATE, OP_REFRESH, OP_CONFIRM, OP_REVISE, OP_DISPUTE,
    OP_ARCHIVE, OP_REVOKE, OP_EXPIRE, OP_SUPERSEDE,
    # The conflict pair. Membership here is not decoration: `_write_history`
    # drops any operation it does not recognise — loudly, but without failing
    # the mutation — so an operation missing from this tuple applies to the
    # fact and then vanishes from its timeline. A contested row whose history
    # says only "created" is exactly the fact a member would most want
    # explained.
    OP_FLAG_CONFLICT, OP_RESOLVE,
    OP_ATTACH_EVIDENCE, OP_DETACH_EVIDENCE, OP_FLAG_REVIEW,
)

#: Who acted, as a class. Mirrors ``telemetry.ACTOR_TYPE_VOCAB``.
ACTOR_OWNER = "owner"
ACTOR_SYSTEM = "system"
ACTOR_PROVIDER = "provider"
ACTOR_UNDX = "undx"
ACTOR_DOCUMENT = "document"
ACTOR_UNKNOWN = "unknown"

ACTOR_TYPES: tuple[str, ...] = (
    ACTOR_OWNER, ACTOR_SYSTEM, ACTOR_PROVIDER, ACTOR_UNDX,
    ACTOR_DOCUMENT, ACTOR_UNKNOWN,
)

#: Why a lifecycle operation happened, as a closed vocabulary. Deliberately
#: coarse. The temptation with a reason field is to let the caller explain, and
#: an explanation of why a private fact was disputed is itself private content —
#: "the surveyor's figure was wrong because the extension isn't finished" is a
#: sentence about the member's house sitting in a table with no sensitivity
#: column. These codes say enough to drive a UI and nothing more.
REASON_CODES: tuple[str, ...] = (
    "", "owner_action", "owner_correction", "source_disagreement",
    "source_revoked", "document_review", "provider_refresh",
    "validity_window_closed", "conflict_resolution", "system_sweep",
    "replaced_by_newer",
    # A conflict the member judged not to be one. Distinct from
    # `conflict_resolution` because the two describe opposite conclusions —
    # "this source was right" and "these sources were never in disagreement" —
    # and a history that recorded both the same way would lose the difference
    # between a decision and a correction of the detector.
    "conflict_dismissed",
    # Evidence arrived, or was taken away. The second is the one that has to
    # exist as its own code: a fact that dropped to NEEDS_REVIEW needs its
    # history to say whether time passed or whether somebody removed the
    # document it was resting on, and `system_sweep` would tell the first story
    # for both.
    "evidence_attached",
    "evidence_withdrawn",
    # The staleness sweep, as distinct from `system_sweep`, which expiry
    # already uses. Expiry retires a fact because its stated validity window
    # closed — a fact about which the member said, in advance, when it would
    # stop being true. Staleness is the softer claim that nobody has looked in
    # a while, and the two must not read the same in a history.
    "freshness_horizon",
)

#: Fact types that must never be stored, however they are spelled.
#:
#: Section 53: secrets are not facts. "The Wi-Fi password is hunter2" is not a
#: statement about the world that can be verified, superseded or contradicted —
#: it is a credential, and a credential in this table would be a credential in
#: every retrieval payload, every export, every UNDX context window and every
#: backup, governed only by a sensitivity label rather than by encryption.
#: ``structured_records`` already refuses these; the fact ledger did not, which
#: is the asymmetry this closes.
#:
#: Matched against the fact *type*, not the value. Scanning values for things
#: that look like secrets would be both unreliable and a reason to read every
#: value on every write; refusing the types that exist to hold credentials is
#: precise and cheap. A member who genuinely wants to record that a password
#: exists can record ``password_last_rotated_at`` — a date is a fact, the
#: password is not.
SECRET_FACT_TYPE_TOKENS: frozenset[str] = frozenset({
    "password", "passcode", "passphrase", "pin", "secret", "api_key",
    "apikey", "private_key", "privatekey", "seed_phrase", "seedphrase",
    "mnemonic", "recovery_code", "recovery_phrase", "otp", "totp",
    "security_answer", "cvv", "cvc", "access_token", "refresh_token",
    "bearer_token", "credential", "credentials", "ssh_key",
})

#: How long a fact of each provenance may be quoted as current. These are
#: horizons for *citation*, not expiry: nothing is deleted, and a stale fact is
#: still returned. What changes past the horizon is that it must be presented
#: with its observation date attached.
#:
#: A verified read-back ages slowly because it was true of the system of record
#: at a known instant. An estimate ages fast because it was never a reading of
#: anything. A user assertion sits between: people are reliable about what they
#: own and unreliable about what a number currently is.
FRESHNESS_HORIZON_DAYS: dict[str, int] = {
    _model.PROVENANCE_VERIFIED: 90,
    _model.PROVENANCE_PROVIDER_ASSERTED: 60,
    _model.PROVENANCE_DOCUMENT_EXTRACTED: 365,
    # An observation the system made itself: it was true when observed, and the
    # system will observe again, so the citation horizon sits between a provider
    # read-back and a human claim.
    _model.PROVENANCE_SYSTEM_OBSERVED: 90,
    # A person affirmed this. Longer than USER_ASSERTED because an affirmation
    # is a second look, and shorter than VERIFIED because it is still a person
    # remembering rather than a record being read.
    _model.PROVENANCE_HUMAN_CONFIRMED: 270,
    _model.PROVENANCE_USER_ASSERTED: 180,
    # A transcript ages badly. What someone said in a meeting was true of that
    # meeting; treating it as durable for a year is how a stale intention gets
    # quoted back as a current arrangement.
    _model.PROVENANCE_MEETING_DERIVED: 60,
    _model.PROVENANCE_INFERRED: 30,
    _model.PROVENANCE_UNDX_PROPOSED: 14,
    _model.PROVENANCE_ESTIMATED: 14,
    # Zero, not a guess. A row whose origin is unknown has no basis on which to
    # claim any freshness window, so it reads as stale from the first query
    # rather than borrowing a horizon it did not earn. This is the honest
    # rendering of Section 118 in the read path.
    _model.PROVENANCE_LEGACY_UNKNOWN: 0,
    _model.PROVENANCE_STALE: 0,
    _model.PROVENANCE_CONFLICTING: 0,
}

_FACT_TYPE_RE = re.compile(r"^[a-z0-9][a-z0-9_.]{0,63}$")
_WHITESPACE_RE = re.compile(r"\s+")
_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


class PrivateFactRejected(ValueError):
    """A write that could not be made without breaking an invariant.

    Raised rather than returned for the *programming error* cases — an unknown
    domain, a missing owner, a value that will not normalize — because those are
    bugs in the caller, and a caller that silently ignores a ``{"status":
    "rejected"}`` return is how facts stop being written without anyone
    noticing. Business-level outcomes (duplicate, refreshed) are returned.
    """


# ---------------------------------------------------------------------------
# Provenance references (Stage 12)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ProvenanceRef:
    """Where a fact came from, precisely enough to go and look again.

    ``locator`` is the field that makes a document-derived fact checkable:
    ``"page=4;section=3.1"`` means a human can be shown the clause rather than
    being asked to trust the extraction. Stage 27 leaves extraction itself
    unbuilt — there is no OCR in this repository — but the *shape* of the
    reference exists now so that when a provider is connected the facts it
    produces are auditable from the first one, rather than being backfilled with
    provenance nobody can verify.
    """

    source_type: str = ""
    source_id: str = ""
    locator: str = ""
    observed_at: str = ""
    confidence: float = 0.0

    def encoded(self) -> str:
        """Deterministic serialization for the ``provenance_ref`` column.

        JSON with sorted keys, which is a *typed record with five named fields*
        rather than the generic JSON dumping the mission rules forbid: nothing
        outside this dataclass may add a key, and :func:`decode_provenance_ref`
        drops anything that appears anyway. Sorted keys matter because this
        string is part of ``fact_key`` — an unstable encoding would make the
        same fact hash differently on two calls and defeat dedupe entirely.
        """
        if not any((self.source_type, self.source_id, self.locator, self.observed_at)):
            return ""
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def decode_provenance_ref(value: object) -> ProvenanceRef:
    """Parse a stored ``provenance_ref``. Unparseable input yields an empty ref.

    Never raises: a row with a corrupted reference is still a fact, and refusing
    to read it would turn a cosmetic problem into a data-loss one. Unknown keys
    are dropped rather than carried, so a future writer cannot smuggle fields
    past the dataclass.
    """
    text = str(value or "").strip()
    if not text:
        return ProvenanceRef()
    try:
        raw = json.loads(text)
    except Exception:
        return ProvenanceRef()
    if not isinstance(raw, dict):
        return ProvenanceRef()
    try:
        confidence = float(raw.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    return ProvenanceRef(
        source_type=str(raw.get("source_type") or "")[:64],
        source_id=str(raw.get("source_id") or "")[:128],
        locator=str(raw.get("locator") or "")[:128],
        observed_at=str(raw.get("observed_at") or "")[:40],
        confidence=max(0.0, min(confidence, 1.0)),
    )


# ---------------------------------------------------------------------------
# Value normalization
# ---------------------------------------------------------------------------
def normalize_value(value: object, value_type: str) -> tuple[str, float | None] | None:
    """``(typed_value, value_number)`` for a value of ``value_type``, or ``None``.

    ``None`` means "this is not that type" and always ends the write. The
    tempting alternative — coerce to ``STRING`` and store it anyway — is how a
    money field ends up holding ``"about 400k"``, at which point every numeric
    comparison over that fact type silently stops working while the store still
    reports the row as present.
    """
    kind = _model.normalize_value_type(value_type)
    if not kind:
        return None

    if kind == _model.VALUE_BOOLEAN:
        if isinstance(value, bool):
            return ("true" if value else "false", 1.0 if value else 0.0)
        text = str(value or "").strip().lower()
        if text in {"true", "yes", "1"}:
            return ("true", 1.0)
        if text in {"false", "no", "0"}:
            return ("false", 0.0)
        return None

    if kind in _model.NUMERIC_VALUE_TYPES:
        try:
            # Currency symbols and thousands separators are presentation, and a
            # store that rejects "$1,200" while accepting "1200" pushes the
            # cleaning into every caller — where it will be done six ways.
            text = str(value).strip().replace(",", "").lstrip("$£€").rstrip("%")
            number = float(text)
        except (TypeError, ValueError):
            return None
        if number != number or number in (float("inf"), float("-inf")):
            # NaN and infinity compare falsely with everything, which would make
            # the contradiction engine quietly unable to see a disagreement.
            return None
        # `repr` of a float round-trips exactly, and an exact round-trip is what
        # keeps `fact_key` stable across processes.
        return (repr(number), number)

    if kind == _model.VALUE_DATE:
        text = str(value or "").strip()
        match = _DATE_RE.match(text)
        if not match:
            return None
        try:
            parsed = datetime(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
        # Stored as a plain date. The numeric twin is the ordinal, so "renews
        # before X" is answerable without re-parsing, but DATE is deliberately
        # excluded from `NUMERIC_VALUE_TYPES`: two renewal dates a day apart are
        # not nearly the same date, they are two answers to a one-answer
        # question, and a tolerance would swallow exactly the conflict Stage 21
        # exists to surface.
        return (parsed.date().isoformat(), float(parsed.date().toordinal()))

    text = _WHITESPACE_RE.sub(" ", str(value if value is not None else "")).strip()
    if not text:
        return None
    return (text[:MAX_TEXT_VALUE], None)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _parse_iso(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _iso(value: object, *, default: str | None = None) -> str | None:
    parsed = _parse_iso(value)
    if parsed is None:
        return default
    return parsed.astimezone(timezone.utc).isoformat()


def fact_key(
    *,
    subject_type: str,
    subject_id: str,
    fact_type: str,
    value_type: str,
    typed_value: str,
    provenance_type: str,
    provenance_ref: str,
    valid_from: str,
) -> str:
    """Stable identity of a fact. See the module docstring for what "same" means.

    ``valid_from`` here is the *explicitly requested* window start, not the
    stored one. A caller who names no window is asserting "this is true now",
    and two such assertions about the same subject, type, value and source are
    one fact observed twice — so they must hash alike. Feeding the stored
    ``valid_from`` in instead would default to ``observed_at`` and ultimately
    to the wall clock, giving every single write a fresh identity and making
    the refresh path unreachable for anyone who does not date their claims.
    Pass ``""`` for "no window stated".
    """
    raw = "\x1f".join(
        (
            subject_type, subject_id, fact_type, value_type, typed_value,
            provenance_type, provenance_ref, valid_from,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def staleness(row: dict, *, at: datetime | None = None) -> dict:
    """Whether a stored fact may still be quoted as current.

    Returns ``{"stale": bool, "age_days": int, "horizon_days": int,
    "observed_at": str}``. A row whose ``observed_at`` cannot be parsed is
    reported stale — an unknown age is not a young age, and the reading that
    treats it as young is the one that misleads.
    """
    moment = at or _now()
    observed = _parse_iso(row.get("observed_at"))
    horizon = FRESHNESS_HORIZON_DAYS.get(
        _model.normalize_provenance(row.get("provenance_type")) or "", 0
    )
    if observed is None:
        return {"stale": True, "age_days": -1, "horizon_days": horizon,
                "observed_at": str(row.get("observed_at") or "")}
    age = max(0, (moment - observed).days)
    return {
        "stale": age > horizon,
        "age_days": age,
        "horizon_days": horizon,
        "observed_at": observed.astimezone(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Secrets are not facts (Section 53)
# ---------------------------------------------------------------------------
_SECRET_SPLIT_RE = re.compile(r"[._]+")

#: The comparison set, with separators stripped. The literal set above is
#: written the way a person reads it — ``ssh_key``, ``recovery_code`` — but the
#: tokeniser splits on exactly those separators, so a multi-word entry could
#: never match anything it was compared against and would sit in the set
#: looking like protection while refusing nothing. Deriving the comparison set
#: keeps the readable spelling and makes every entry live.
_SECRET_MATCH_TOKENS: frozenset[str] = frozenset(
    _SECRET_SPLIT_RE.sub("", token) for token in SECRET_FACT_TYPE_TOKENS
)


def is_secret_fact_type(fact_type: object) -> bool:
    """Would storing this fact type put a credential in the ledger?

    Tokenised on ``.`` and ``_`` rather than substring-matched, because
    substring matching on ``pin`` would refuse ``shipping_address`` and
    substring matching on ``otp`` would refuse almost nothing reliably. The
    fact-type grammar is already ``[a-z0-9_.]``, so the tokens are exactly the
    words the caller chose.

    Compound tokens are also checked as joined pairs, so ``api.key``,
    ``api_key`` and ``apikey`` are all caught — a refusal that can be stepped
    around by changing a separator is a suggestion, not a rule.
    """
    text = str(fact_type or "").strip().lower()
    if not text:
        return False
    parts = [p for p in _SECRET_SPLIT_RE.split(text) if p]
    if any(p in _SECRET_MATCH_TOKENS for p in parts):
        return True
    joined = ["".join(pair) for pair in zip(parts, parts[1:])]
    return any(j in _SECRET_MATCH_TOKENS for j in joined)


# ---------------------------------------------------------------------------
# The verification state machine (Sections 16, 31, 32)
# ---------------------------------------------------------------------------
# Every operation names the states it may move a fact *out of*. Expressing the
# machine as "which starting states does this operation accept" rather than as
# a flat set of legal pairs is what makes each rule readable next to its reason,
# and it is the direction the code actually asks the question in: the caller
# knows the operation and the row knows its state.
#
# Two rules run through all of it:
#
# 1. Nothing transitions out of REVOKED or SUPERSEDED in place. A revoked fact
#    was withdrawn as never-true and a superseded fact has a successor; editing
#    either would erase the reason it stopped being current. Bringing one back
#    means writing a *new* fact, which leaves a trail.
#
# 2. Confirmation cannot reach VERIFIED or PROVIDER_VERIFIED. Those two states
#    assert that a system of record was read, and no amount of a person clicking
#    "yes, that's right" constitutes reading a bank. The owner's confirmation
#    lands on USER_CONFIRMED and stops there. This is the single most important
#    line in the file: without it, the review queue becomes a machine for
#    laundering assertions into verified truth one tap at a time.
_ALL_NON_TERMINAL: frozenset[str] = frozenset(
    s for s in _model.VERIFICATION_STATES
    if s not in _model.TERMINAL_VERIFICATION
)

VERIFICATION_TRANSITIONS: dict[str, tuple[frozenset[str], str]] = {
    OP_CONFIRM: (
        frozenset({
            _model.VERIFICATION_UNVERIFIED,
            _model.VERIFICATION_LEGACY_UNKNOWN,
            _model.VERIFICATION_NEEDS_REVIEW,
            _model.VERIFICATION_DISPUTED,
            _model.VERIFICATION_CONFLICTING,
            _model.VERIFICATION_EVIDENCE_SUPPORTED,
            _model.VERIFICATION_USER_CONFIRMED,
        }),
        _model.VERIFICATION_USER_CONFIRMED,
    ),
    OP_DISPUTE: (
        # Anything still live can be disputed, including a PROVIDER_VERIFIED
        # fact. A ledger in which the member cannot contradict their bank is a
        # ledger that has decided the bank is never wrong, and the whole point
        # of recording a dispute is that it is a *state*, not a deletion: the
        # provider's figure stays, flagged, until someone resolves it.
        _ALL_NON_TERMINAL,
        _model.VERIFICATION_DISPUTED,
    ),
    OP_FLAG_CONFLICT: (
        # Everything live can be flagged, including a PROVIDER_VERIFIED row, and
        # that is the uncomfortable part of this entry: flagging costs the row
        # its attestation, because the machine has no from-state memory and
        # CONFLICTING ranks at zero. It is still right. An attestation that is
        # contradicted by another source is no longer uncontested, and a ledger
        # that let the bank's figure keep reading as VERIFIED while a document
        # said otherwise would be answering "what does the bank say" to a member
        # who asked "what is true". The history table keeps the from-state, so
        # nothing is lost — only demoted, which is the safe direction.
        #
        # DISPUTED is the one exclusion. A dispute is a person saying "this is
        # wrong"; a conflict flag is a machine saying "these two disagree". The
        # first is more specific and was more expensive to obtain, and letting a
        # nightly detection sweep overwrite it would erase the member's own
        # judgement in favour of an observation they already knew about.
        _ALL_NON_TERMINAL - frozenset({_model.VERIFICATION_DISPUTED}),
        _model.VERIFICATION_CONFLICTING,
    ),
    OP_RESOLVE: (
        # Only from states that mean "contested or unchecked". Resolving is not
        # a general-purpose upgrade path: a VERIFIED or PROVIDER_VERIFIED row
        # that was never flagged has nothing to resolve, and accepting it here
        # would silently *downgrade* an attestation to USER_CONFIRMED on the way
        # past. The orchestration in `contradictions.resolve_conflict` flags
        # every competitor before nominating a winner, so by the time this runs
        # the winner is CONFLICTING and the entry it needs is the first one.
        frozenset({
            _model.VERIFICATION_CONFLICTING,
            _model.VERIFICATION_DISPUTED,
            _model.VERIFICATION_NEEDS_REVIEW,
            _model.VERIFICATION_UNVERIFIED,
            _model.VERIFICATION_LEGACY_UNKNOWN,
            _model.VERIFICATION_EVIDENCE_SUPPORTED,
            _model.VERIFICATION_USER_CONFIRMED,
        }),
        # The same ceiling as OP_CONFIRM, and for the same reason. A member
        # weighing two sources and picking one is exercising judgement, which is
        # the strongest thing a person can contribute and is still not a system
        # of record being read. If resolution could reach VERIFIED then every
        # conflict would become a doorway to the state the whole ledger is built
        # to keep closed — and it would be a doorway with a queue of members
        # being asked to walk through it.
        _model.VERIFICATION_USER_CONFIRMED,
    ),
    OP_ATTACH_EVIDENCE: (
        # Not from DISPUTED and not from CONFLICTING. Those two mean somebody —
        # a person in the first case, the detector in the second — has an open
        # objection to this row, and a document arriving afterwards does not
        # answer it. The member may well have attached that document *in order
        # to* settle the dispute, and the way to settle it is to settle it:
        # `resolve_fact` for a conflict, a fresh confirmation for a dispute.
        # Letting an attachment do it silently would mean a fact could be argued
        # out of contention by whoever uploaded a file last.
        #
        # The citation is still recorded in either case. `attach_evidence`
        # writes the link row first and only then asks whether the fact may be
        # promoted, so evidence gathered against a disputed fact is kept and
        # visible — it simply does not change the fact's standing on its own.
        #
        # Not from VERIFIED or PROVIDER_VERIFIED either, and that exclusion is
        # the important one: those rank at 90 and 100, EVIDENCE_SUPPORTED ranks
        # at 70, so accepting them here would let attaching a supporting
        # document *downgrade* a fact. A rule that punishes better sourcing is
        # a rule nobody will follow twice.
        frozenset({
            _model.VERIFICATION_UNVERIFIED,
            _model.VERIFICATION_LEGACY_UNKNOWN,
            _model.VERIFICATION_USER_CONFIRMED,
            _model.VERIFICATION_NEEDS_REVIEW,
            _model.VERIFICATION_EVIDENCE_SUPPORTED,
        }),
        _model.VERIFICATION_EVIDENCE_SUPPORTED,
    ),
    OP_DETACH_EVIDENCE: (
        # Only from EVIDENCE_SUPPORTED, because only a fact that was standing on
        # its evidence has anything to lose when the evidence goes. A fact the
        # member confirmed themselves keeps its confirmation; a fact a provider
        # attested keeps the attestation. `detach_evidence` calls this only when
        # the citation it removed was the last live one, so the ordinary case —
        # dropping one of three documents — moves nothing.
        frozenset({_model.VERIFICATION_EVIDENCE_SUPPORTED}),
        # Down to NEEDS_REVIEW, not back to UNVERIFIED. The difference is
        # whether anybody is told. A fact that quietly returned to UNVERIFIED
        # would keep being read, at rank 20, by a caller with no way to know
        # that the reason it was trusted has been withdrawn; NEEDS_REVIEW ranks
        # at zero, is in UNTRUSTWORTHY_VERIFICATION, and puts the row in front
        # of the member. Losing your justification is exactly the moment to ask.
        _model.VERIFICATION_NEEDS_REVIEW,
    ),
    OP_FLAG_REVIEW: (
        # Everything live except the states that already carry a stronger, more
        # specific objection. Flagging a DISPUTED or CONFLICTING fact for review
        # would replace "the member says this is wrong" or "two sources
        # disagree" with the vaguer "somebody should look at this", and all
        # three rank at zero, so the trade is a strict loss of information for
        # no gain in caution.
        #
        # A PROVIDER_VERIFIED fact *can* be flagged, and that is the point of
        # the staleness sweep: a bank balance read six months ago is still a
        # bank's own answer and is no longer current, and a ledger that let
        # rank 100 exempt a row from ageing would quote figures from last year
        # with total confidence.
        _ALL_NON_TERMINAL - frozenset({
            _model.VERIFICATION_DISPUTED,
            _model.VERIFICATION_CONFLICTING,
            _model.VERIFICATION_NEEDS_REVIEW,
        }),
        _model.VERIFICATION_NEEDS_REVIEW,
    ),
    OP_REVOKE: (_ALL_NON_TERMINAL, _model.VERIFICATION_REVOKED),
    OP_ARCHIVE: (_ALL_NON_TERMINAL, ""),
    OP_EXPIRE: (_ALL_NON_TERMINAL, _model.VERIFICATION_EXPIRED),
    OP_SUPERSEDE: (_ALL_NON_TERMINAL, _model.VERIFICATION_SUPERSEDED),
}

#: Lifecycle disposition each operation leaves the row in. ARCHIVE is the only
#: operation that changes lifecycle without asserting anything about belief —
#: putting a fact away is a filing decision, not a judgement that it was wrong —
#: which is why its target verification state above is the empty string,
#: meaning "leave whatever was there".
LIFECYCLE_AFTER: dict[str, str] = {
    OP_ARCHIVE: _model.LIFECYCLE_ARCHIVED,
    OP_REVOKE: _model.LIFECYCLE_REVOKED,
    OP_EXPIRE: _model.LIFECYCLE_EXPIRED,
    OP_SUPERSEDE: _model.LIFECYCLE_SUPERSEDED,
}

#: Operations that count as "someone looked at this and it held up", and so
#: move ``last_verified_at``. Note that DISPUTE is not one of them: a dispute is
#: attention, but it is the opposite of confirmation, and letting it refresh the
#: verification clock would make a contested fact look freshly checked.
#: RESOLVE is here for the same reason CONFIRM is — a member who weighed two
#: sources and chose one has demonstrably looked at the fact — and FLAG_CONFLICT
#: is not, for the same reason DISPUTE is not: the detector noticing a
#: disagreement is not evidence that anybody checked anything.
VERIFYING_OPERATIONS: frozenset[str] = frozenset({OP_CONFIRM, OP_RESOLVE})


def _normalize_actor_type(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in ACTOR_TYPES else ACTOR_UNKNOWN


def _normalize_reason(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in REASON_CODES else ""


def _stored_verification(value: object) -> str:
    """Read a row's verification state, treating empty as LEGACY_UNKNOWN.

    The column's default is the empty string and the backfill converts it, but
    a row inserted between the ``ADD COLUMN`` and the backfill in a concurrent
    process would still carry ``''``. Resolving it here rather than trusting the
    migration means the read path is correct even if the backfill never ran.
    """
    return _model.normalize_verification_state(value) or _model.VERIFICATION_LEGACY_UNKNOWN


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def _write_history(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    operation: str,
    from_verification: str = "",
    to_verification: str = "",
    from_lifecycle: str = "",
    to_lifecycle: str = "",
    provenance_type: str = "",
    related_fact_id: int = 0,
    actor_user_id: int = 0,
    actor_type: str = ACTOR_UNKNOWN,
    reason_code: str = "",
) -> None:
    """Append one history row. Never raises.

    Swallowing the exception is the right call here and it is worth being
    explicit about why, because "never raises" is usually a smell. History is a
    record *about* a mutation that has already been made and committed in the
    same transaction. If the history insert fails and we propagate, the caller
    sees an error for an operation that in fact succeeded, and the natural
    response — retry — applies the operation twice. Losing a history row is a
    gap in a timeline; raising is a double confirmation or a double revocation.
    The failure is logged loudly so the gap is not silent.
    """
    if operation not in FACT_OPERATIONS:
        LOGGER.error("PRIVATE_FACT_HISTORY_BAD_OP operation=%s", operation)
        return
    try:
        cur.execute(
            f"""INSERT INTO {_schema.FACT_HISTORY_TABLE}
            (owner_user_id, fact_id, operation, from_verification_state,
             to_verification_state, from_lifecycle_state, to_lifecycle_state,
             provenance_type, related_fact_id, actor_user_id, actor_type,
             reason_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(owner_user_id or 0), int(fact_id or 0), operation,
                str(from_verification or ""), str(to_verification or ""),
                str(from_lifecycle or ""), str(to_lifecycle or ""),
                str(provenance_type or ""), int(related_fact_id or 0),
                int(actor_user_id or 0), _normalize_actor_type(actor_type),
                _normalize_reason(reason_code), _now_iso(),
            ),
        )
    except Exception:
        LOGGER.exception(
            "PRIVATE_FACT_HISTORY_WRITE_FAILED fact_id=%s operation=%s",
            fact_id, operation,
        )


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def record_fact(cur, **kwargs) -> dict:
    """:func:`_record_fact`, with the Stage 38 rejection counter around it.

    A thin wrapper rather than an emit at each ``raise`` site. There are nine
    invariants below and every one of them raises; counting them individually
    would mean nine chances to add a tenth that is never counted, and a
    rejection rate that silently under-reports is worse than none because it
    reads as health.

    Only the *domain* and *sensitivity* the caller asked for are published, and
    only when they are recognised vocabulary — a rejection caused by an unknown
    domain reports ``other``, not the string that was rejected. The exception
    message, which does contain the caller's value, never reaches telemetry;
    it goes to the caller, which is where it is useful and where it is already
    permitted to be.
    """
    try:
        return _record_fact(cur, **kwargs)
    except PrivateFactRejected:
        _telemetry.emit(
            _telemetry.EVENT_FACT_WRITE, outcome=STATUS_REJECTED,
            domain=kwargs.get("domain"), sensitivity=kwargs.get("sensitivity"),
            provenance_type=kwargs.get("provenance_type"), superseded=False)
        raise


def _record_fact(
    cur,
    *,
    owner_user_id: int,
    subject_type: str,
    subject_id: object,
    fact_type: str,
    value: object,
    value_type: str,
    provenance_type: str,
    provenance: ProvenanceRef | None = None,
    confidence: float | None = None,
    observed_at: object = None,
    valid_from: object = None,
    valid_to: object = None,
    sensitivity: object = None,
    domain: object = None,
    expires_at: object = None,
    actor_user_id: int | None = None,
    actor_type: str = ACTOR_OWNER,
    supersedes_id: int = 0,
    purpose: str = "user_request",
) -> dict:
    """Write one private fact. The only supported way to create one.

    Returns ``{"status", "fact_id", "fact_key", "sensitivity", "domain"}`` where
    status is ``written`` for a new row or ``refreshed`` when the identical fact
    from the identical source was already present and only its observation time
    moved.

    Raises :class:`PrivateFactRejected` when the write would break an invariant.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")

    subject_kind = str(subject_type or "").strip().upper()[:32]
    if not subject_kind:
        raise PrivateFactRejected("subject_type is required")
    subject = str(subject_id if subject_id is not None else "").strip()[:MAX_SUBJECT_ID]
    if not subject:
        raise PrivateFactRejected("subject_id is required")

    kind = str(fact_type or "").strip()
    if not _FACT_TYPE_RE.match(kind):
        # Validated as written, deliberately not lowercased first. Lowercasing
        # would accept "estimatedValue" and silently store it as
        # "estimatedvalue" — a *different* fact type from "estimated_value",
        # created by the store rather than by the caller, and invisible until a
        # reader asks for one and gets none of the other. Fact types are the
        # query surface; there is one spelling and it is the one below.
        raise PrivateFactRejected(
            f"fact_type must match {_FACT_TYPE_RE.pattern}: {fact_type!r}")
    kind = kind[:MAX_FACT_TYPE]

    if is_secret_fact_type(kind):
        # Section 53. Raised rather than returned as a rejection status, and
        # raised *before* the value is normalized so the credential is never
        # even copied into a local. The message names the fact type, which the
        # caller already has, and never the value.
        raise PrivateFactRejected(
            f"{kind!r} names a credential; secrets are not facts and this "
            f"ledger has no encryption for them. Record a fact *about* the "
            f"secret instead, e.g. when it was last rotated."
        )

    normalized = normalize_value(value, value_type)
    if normalized is None:
        raise PrivateFactRejected(
            f"value does not normalize as {value_type!r}")
    typed_value, value_number = normalized
    resolved_value_type = _model.normalize_value_type(value_type) or ""

    source = _model.normalize_provenance(provenance_type)
    if not source:
        raise PrivateFactRejected(f"unknown provenance_type: {provenance_type!r}")
    if source in _model.DEGRADED_PROVENANCE:
        # STALE and CONFLICTING are states this package moves a row *into*.
        # Accepting them as an origin would let a caller write a fact that is
        # born unusable, and worse, born with a provenance that cannot lose an
        # argument because it already ranks at zero.
        raise PrivateFactRejected(
            f"{source} is a derived state, not a source of a new fact")

    resolved_domain = _model.normalize_domain(domain or _model.DEFAULT_DOMAIN)
    if not resolved_domain:
        raise PrivateFactRejected(f"unknown domain: {domain!r}")
    resolved_sensitivity = _model.normalize_sensitivity(
        sensitivity or _model.DEFAULT_SENSITIVITY)
    if not resolved_sensitivity:
        raise PrivateFactRejected(f"unknown sensitivity: {sensitivity!r}")

    ref = (provenance or ProvenanceRef()).encoded()

    now_iso = _now_iso()
    observed_iso = _iso(observed_at, default=now_iso) or now_iso
    # Two readings of the window start. `explicit_from` is what the caller
    # actually stated and is what identity is built from; `from_iso` is what
    # gets stored, which falls back to the observation time so every row is
    # placeable in time for overlap checks.
    explicit_from = _iso(valid_from, default=None)
    from_iso = explicit_from or observed_iso
    to_iso = _iso(valid_to, default=None)
    if to_iso and to_iso < from_iso:
        # A window that closes before it opens overlaps nothing, so the fact
        # would be invisible to both retrieval and the contradiction engine —
        # stored, counted, and unable to participate in anything.
        raise PrivateFactRejected("valid_to precedes valid_from")

    expires_iso = _iso(expires_at, default=None) or ""
    if expires_iso and expires_iso < from_iso:
        # An expiry before the window even opens means the fact is born
        # expired, which is not a fact, it is a typo.
        raise PrivateFactRejected("expires_at precedes valid_from")

    try:
        score = 1.0 if confidence is None else float(confidence)
    except (TypeError, ValueError):
        raise PrivateFactRejected(f"confidence is not a number: {confidence!r}")
    score = max(0.0, min(score, 1.0))

    key = fact_key(
        subject_type=subject_kind, subject_id=subject, fact_type=kind,
        value_type=resolved_value_type, typed_value=typed_value,
        provenance_type=source, provenance_ref=ref, valid_from=explicit_from or "",
    )

    _schema.require_private_schema(cur)

    cur.execute(
        f"SELECT id, observed_at, confidence, lifecycle_state "
        f"FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND fact_key = ?",
        (owner, key),
    )
    existing = cur.fetchone()
    if existing is not None:
        # Same claim, same source, same window — a newer reading of one fact,
        # not a second opinion. Move the observation time forward and keep the
        # higher confidence; do not insert, and do not lower a confidence that
        # a stronger earlier read established.
        row_id = int(existing["id"] if hasattr(existing, "keys") else existing[0])
        prior = existing["confidence"] if hasattr(existing, "keys") else existing[2]
        state = str((existing["lifecycle_state"] if hasattr(existing, "keys")
                     else existing[3]) or "")
        try:
            prior_score = float(prior or 0.0)
        except (TypeError, ValueError):
            prior_score = 0.0
        if state == _model.LIFECYCLE_SUPERSEDED:
            # The identical claim, from the same source, arrived again *after*
            # being superseded: it is a live claim once more, not history. A
            # refresh that only moved `observed_at` would leave the store
            # swallowing a current assertion — the projector journey is a
            # resolved obligation reopening, or a sold holding re-bought at
            # the old quantity, where the "new" fact IS the retired row.
            # Reactivate it and give it the caller's validity window; the
            # `valid_to` the supersede stamped described the retirement, and
            # the retirement has just been undone.
            cur.execute(
                f"UPDATE {_schema.FACTS_TABLE} "
                f"SET observed_at = ?, confidence = ?, updated_at = ?, "
                f"lifecycle_state = ?, valid_to = ?, superseded_by_id = 0, "
                # The successor link goes too. A reactivated fact that still
                # pointed at its replacement would render as "superseded by
                # 4471" on a row the store is once again presenting as current,
                # and the detail screen would show a member two live facts each
                # claiming the other replaced it.
                f"verification_state = ? WHERE id = ?",
                (observed_iso, max(score, prior_score), now_iso,
                 _model.LIFECYCLE_ACTIVE, to_iso,
                 _model.DEFAULT_VERIFICATION_STATE, row_id),
            )
        else:
            cur.execute(
                f"UPDATE {_schema.FACTS_TABLE} "
                f"SET observed_at = ?, confidence = ?, updated_at = ? WHERE id = ?",
                (observed_iso, max(score, prior_score), now_iso, row_id),
            )
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_FACT_CREATE, object_type=subject_kind,
            object_id=subject, purpose=purpose, outcome=_audit.OUTCOME_OK,
        )
        # Stage 38. A refresh means the same claim arrived again from a source
        # at least as strong; the counter distinguishes that from a new claim,
        # which is the difference between an active store and a chatty one.
        _write_history(
            cur, owner_user_id=owner, fact_id=row_id, operation=OP_REFRESH,
            from_lifecycle=state, to_lifecycle=(
                _model.LIFECYCLE_ACTIVE if state == _model.LIFECYCLE_SUPERSEDED
                else state),
            provenance_type=source,
            actor_user_id=int(actor_user_id or owner), actor_type=actor_type,
        )
        _telemetry.emit(
            _telemetry.EVENT_FACT_WRITE, outcome=STATUS_REFRESHED,
            domain=resolved_domain, sensitivity=resolved_sensitivity,
            provenance_type=source, superseded=False)
        return {"status": STATUS_REFRESHED, "fact_id": row_id, "fact_key": key,
                "sensitivity": resolved_sensitivity, "domain": resolved_domain}

    cur.execute(
        f"""INSERT INTO {_schema.FACTS_TABLE}
        (owner_user_id, fact_key, subject_type, subject_id, fact_type,
         value_type, typed_value, value_number, provenance_type, provenance_ref,
         confidence, observed_at, valid_from, valid_to, sensitivity, domain,
         lifecycle_state, conflict_id, verification_state, last_verified_at,
         expires_at, supersedes_id, superseded_by_id, created_by_actor_type,
         created_by_actor_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                ?, '', ?, ?, 0, ?, ?, ?, ?)""",
        (
            owner, key, subject_kind, subject, kind, resolved_value_type,
            typed_value, value_number, source, ref, score, observed_iso,
            from_iso, to_iso, resolved_sensitivity, resolved_domain,
            _model.LIFECYCLE_ACTIVE,
            # Every fact is born UNVERIFIED regardless of how strong its source
            # is. A PROVIDER_ASSERTED fact has excellent provenance and has
            # still had nothing done to check it, and writing it in as
            # PROVIDER_VERIFIED would make the two axes identical again on the
            # very path that creates most rows. `last_verified_at` is empty for
            # the same reason: nothing has verified it yet.
            _model.DEFAULT_VERIFICATION_STATE,
            expires_iso, max(0, int(supersedes_id or 0)),
            _normalize_actor_type(actor_type), int(actor_user_id or owner),
            now_iso, now_iso,
        ),
    )
    cur.execute(
        f"SELECT id FROM {_schema.FACTS_TABLE} WHERE owner_user_id = ? AND fact_key = ?",
        (owner, key),
    )
    inserted = cur.fetchone()
    fact_id = int(inserted["id"] if hasattr(inserted, "keys") else inserted[0]) if inserted else 0

    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_FACT_CREATE, object_type=subject_kind,
        object_id=subject, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    _write_history(
        cur, owner_user_id=owner, fact_id=fact_id, operation=OP_CREATE,
        to_verification=_model.DEFAULT_VERIFICATION_STATE,
        to_lifecycle=_model.LIFECYCLE_ACTIVE, provenance_type=source,
        related_fact_id=max(0, int(supersedes_id or 0)),
        actor_user_id=int(actor_user_id or owner), actor_type=actor_type,
    )
    # Close the other half of the chain link. `_record_fact` writes
    # `supersedes_id` onto the new row above; without this the predecessor has
    # no idea it was replaced, and "what replaced this fact" — the question the
    # detail screen asks about every superseded row — would require scanning
    # every fact the member owns looking for one that points back.
    if int(supersedes_id or 0) > 0 and fact_id:
        _link_supersession(
            cur, owner_user_id=owner, predecessor_id=int(supersedes_id),
            successor_id=fact_id, actor_user_id=int(actor_user_id or owner),
            actor_type=actor_type, purpose=purpose,
        )
    _telemetry.emit(
        _telemetry.EVENT_FACT_WRITE, outcome=STATUS_WRITTEN,
        domain=resolved_domain, sensitivity=resolved_sensitivity,
        provenance_type=source,
        verification_state=_model.DEFAULT_VERIFICATION_STATE,
        superseded=bool(int(supersedes_id or 0)))
    return {"status": STATUS_WRITTEN, "fact_id": fact_id, "fact_key": key,
            "sensitivity": resolved_sensitivity, "domain": resolved_domain,
            "verification_state": _model.DEFAULT_VERIFICATION_STATE}


def supersede_facts(
    cur,
    *,
    owner_user_id: int,
    subject_type: str,
    subject_id: object,
    fact_type: str,
    keep_fact_id: int = 0,
    actor_user_id: int | None = None,
    purpose: str = "system_maintenance",
) -> int:
    """Mark prior ACTIVE facts of one (subject, fact_type) SUPERSEDED.

    This exists for *projections* — readings of an external system of record
    that this store mirrors. When the Portfolio says a quantity changed, the
    old quantity is not a second opinion to weigh against the new one; it is
    the previous state of the same ledger, and leaving both ACTIVE would hand
    the contradiction engine a conflict that is really just time passing.

    It is deliberately narrow: one owner, one subject, one fact type, and the
    row named by ``keep_fact_id`` survives. It cannot cross a subject or a
    type, so a projector cannot bulk-retire facts other writers recorded about
    other matters. Within one (subject, fact_type) it *does* retire rows from
    other sources — that is the point: the projection's fact type is its own
    namespace (e.g. ``portfolio.quantity``), and nothing else writes into it.

    Returns the number of rows superseded. Rejects rather than guessing when
    the scope is malformed, same as :func:`record_fact`.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    subject_kind = str(subject_type or "").strip().upper()[:32]
    subject = str(subject_id if subject_id is not None else "").strip()[:MAX_SUBJECT_ID]
    kind = str(fact_type or "").strip()
    if not subject_kind or not subject or not _FACT_TYPE_RE.match(kind):
        raise PrivateFactRejected("supersede scope must name a subject and fact_type")

    _schema.require_private_schema(cur)

    now_iso = _now_iso()
    keep = int(keep_fact_id or 0)

    # Read the affected ids before the UPDATE rather than counting rows after
    # it. `rowcount` says how many changed; history has to say *which*, and
    # after the UPDATE the predicate that selected them (lifecycle = ACTIVE) no
    # longer matches any of them. This is bounded by MAX_GROUP-sized fact sets
    # in practice — one subject, one fact type — so the extra SELECT is cheap.
    cur.execute(
        f"SELECT id, verification_state FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND subject_type = ? AND subject_id = ? "
        f"AND fact_type = ? AND lifecycle_state = ? AND id != ?",
        (owner, subject_kind, subject, kind[:MAX_FACT_TYPE],
         _model.LIFECYCLE_ACTIVE, keep),
    )
    doomed = [
        (
            int(r["id"] if hasattr(r, "keys") else r[0]),
            _stored_verification(r["verification_state"] if hasattr(r, "keys") else r[1]),
        )
        for r in (cur.fetchall() or [])
    ]

    cur.execute(
        f"UPDATE {_schema.FACTS_TABLE} "
        f"SET lifecycle_state = ?, valid_to = COALESCE(valid_to, ?), updated_at = ?, "
        f"verification_state = ?, superseded_by_id = ? "
        f"WHERE owner_user_id = ? AND subject_type = ? AND subject_id = ? "
        f"AND fact_type = ? AND lifecycle_state = ? AND id != ?",
        (_model.LIFECYCLE_SUPERSEDED, now_iso, now_iso,
         _model.VERIFICATION_SUPERSEDED, keep, owner, subject_kind,
         subject, kind[:MAX_FACT_TYPE], _model.LIFECYCLE_ACTIVE, keep),
    )
    changed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
    for fact_id, prior_state in doomed:
        _write_history(
            cur, owner_user_id=owner, fact_id=fact_id, operation=OP_SUPERSEDE,
            from_verification=prior_state,
            to_verification=_model.VERIFICATION_SUPERSEDED,
            from_lifecycle=_model.LIFECYCLE_ACTIVE,
            to_lifecycle=_model.LIFECYCLE_SUPERSEDED,
            related_fact_id=keep, actor_user_id=int(actor_user_id or owner),
            actor_type=ACTOR_SYSTEM, reason_code="replaced_by_newer",
        )
    if changed:
        _audit.record(
            cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
            action=_audit.ACTION_FACT_SUPERSEDE, object_type=subject_kind,
            object_id=subject, purpose=purpose, outcome=_audit.OUTCOME_OK,
            result_count=changed,
        )
        _telemetry.emit(
            _telemetry.EVENT_FACT_WRITE, outcome="superseded",
            domain=None, sensitivity=None, provenance_type=None,
            superseded=True)
    return changed


# ---------------------------------------------------------------------------
# Lifecycle operations (Sections 4, 16, 31, 32)
# ---------------------------------------------------------------------------
# Every operation below goes through one implementation. The alternative —
# `confirm_fact`, `dispute_fact` and `revoke_fact` each with their own SELECT,
# their own owner check and their own UPDATE — is how the owner predicate ends
# up missing from exactly one of them. There is one place the predicate is
# written and one place it can be wrong, and the tests point at that place.
_LIFECYCLE_AUDIT_ACTION: dict[str, str] = {
    OP_CONFIRM: _audit.ACTION_FACT_CONFIRM,
    OP_DISPUTE: _audit.ACTION_FACT_DISPUTE,
    OP_ARCHIVE: _audit.ACTION_FACT_ARCHIVE,
    OP_REVOKE: _audit.ACTION_FACT_REVOKE,
    OP_EXPIRE: _audit.ACTION_FACT_EXPIRE,
    OP_FLAG_CONFLICT: _audit.ACTION_CONFLICT_DETECTED,
    OP_RESOLVE: _audit.ACTION_CONFLICT_RESOLVED,
    OP_ATTACH_EVIDENCE: _audit.ACTION_FACT_EVIDENCE_ATTACH,
    OP_DETACH_EVIDENCE: _audit.ACTION_FACT_EVIDENCE_DETACH,
    OP_FLAG_REVIEW: _audit.ACTION_FACT_REVIEW_FLAG,
}


def _apply_lifecycle(
    cur,
    *,
    operation: str,
    owner_user_id: int,
    fact_id: int,
    actor_user_id: int | None = None,
    actor_type: str = ACTOR_OWNER,
    reason_code: str = "",
    purpose: str = "user_request",
) -> dict:
    """Move one fact through one lifecycle transition.

    Returns ``{"status", "fact_id", "from_state", "to_state", "reason"}``.
    ``status`` is one of ``applied``, ``unchanged``, ``refused`` or
    ``not_found``, and the four are kept distinct on purpose:

    ``not_found``  no such fact *for this owner*. Deliberately the same answer
                   as "no such fact at all" — telling a caller that a fact
                   exists but belongs to someone else leaks its existence,
                   which Section 14 treats as the leak that matters.
    ``refused``    the fact exists and the state machine forbids the move. The
                   caller gets the current state back so a UI can say why.
    ``unchanged``  the fact is already in the target state. Not an error, and
                   not counted as work.
    ``applied``    the transition happened.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    target_id = int(fact_id or 0)
    if target_id <= 0:
        raise PrivateFactRejected("fact_id is required")
    if operation not in VERIFICATION_TRANSITIONS:
        raise PrivateFactRejected(f"unknown lifecycle operation: {operation!r}")

    _schema.require_private_schema(cur)

    allowed_from, to_state = VERIFICATION_TRANSITIONS[operation]
    actor = int(actor_user_id or owner)
    actor_class = _normalize_actor_type(actor_type)
    reason = _normalize_reason(reason_code)

    # The owner predicate is in the SELECT, not applied to the result. A query
    # that fetches by id and then compares owners has already read another
    # member's row into this process, and the timing difference between "no
    # row" and "row, wrong owner" is itself an existence oracle.
    cur.execute(
        f"SELECT id, verification_state, lifecycle_state, domain, sensitivity, "
        f"provenance_type, subject_type, subject_id "
        f"FROM {_schema.FACTS_TABLE} WHERE id = ? AND owner_user_id = ?",
        (target_id, owner),
    )
    row = cur.fetchone()
    if row is None:
        return {"status": OUTCOME_NOT_FOUND, "fact_id": target_id,
                "from_state": "", "to_state": "", "reason": "no_such_fact"}

    def _col(name: str, index: int):
        return row[name] if hasattr(row, "keys") else row[index]

    from_state = _stored_verification(_col("verification_state", 1))
    from_lifecycle = str(_col("lifecycle_state", 2) or "")
    domain = str(_col("domain", 3) or "")
    sensitivity = str(_col("sensitivity", 4) or "")
    provenance = str(_col("provenance_type", 5) or "")
    subject_kind = str(_col("subject_type", 6) or "")
    subject = str(_col("subject_id", 7) or "")

    resolved_to = to_state or from_state
    to_lifecycle = LIFECYCLE_AFTER.get(operation, from_lifecycle)

    def _emit(outcome: str) -> None:
        _telemetry.emit(
            _telemetry.EVENT_FACT_LIFECYCLE, operation=operation,
            outcome=outcome, actor_type=actor_class, domain=domain,
            sensitivity=sensitivity, provenance_type=provenance,
            from_state=from_state, to_state=resolved_to,
            lifecycle_state=to_lifecycle,
            chained=bool(operation == OP_SUPERSEDE),
        )

    if from_state not in allowed_from:
        # Includes every attempt to move out of REVOKED or SUPERSEDED, because
        # `_ALL_NON_TERMINAL` excludes them and no operation lists them.
        _audit.record(
            cur, actor_user_id=actor, owner_user_id=owner,
            action=_LIFECYCLE_AUDIT_ACTION.get(operation, _audit.ACTION_FACT_REVISE),
            object_type=subject_kind, object_id=subject, purpose=purpose,
            outcome=_audit.OUTCOME_DENIED,
        )
        _emit(OUTCOME_REFUSED)
        return {"status": OUTCOME_REFUSED, "fact_id": target_id,
                "from_state": from_state, "to_state": resolved_to,
                "reason": "transition_not_permitted"}

    if from_state == resolved_to and from_lifecycle == to_lifecycle:
        _emit(OUTCOME_UNCHANGED)
        return {"status": OUTCOME_UNCHANGED, "fact_id": target_id,
                "from_state": from_state, "to_state": resolved_to,
                "reason": "already_in_state"}

    now_iso = _now_iso()
    sets = ["verification_state = ?", "lifecycle_state = ?", "updated_at = ?"]
    params: list[object] = [resolved_to, to_lifecycle, now_iso]
    if operation in VERIFYING_OPERATIONS:
        sets.append("last_verified_at = ?")
        params.append(now_iso)
    if operation in LIFECYCLE_AFTER:
        # Closing the validity window is what makes a retired fact invisible to
        # the contradiction engine's overlap check. Without it a revoked fact
        # keeps arguing with its replacement forever. COALESCE so a window the
        # member actually stated is not overwritten by the retirement date.
        sets.append("valid_to = COALESCE(valid_to, ?)")
        params.append(now_iso)
    params.extend([target_id, owner])

    cur.execute(
        f"UPDATE {_schema.FACTS_TABLE} SET {', '.join(sets)} "
        f"WHERE id = ? AND owner_user_id = ?",
        tuple(params),
    )

    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_LIFECYCLE_AUDIT_ACTION.get(operation, _audit.ACTION_FACT_REVISE),
        object_type=subject_kind, object_id=subject, purpose=purpose,
        outcome=_audit.OUTCOME_OK,
    )
    _write_history(
        cur, owner_user_id=owner, fact_id=target_id, operation=operation,
        from_verification=from_state, to_verification=resolved_to,
        from_lifecycle=from_lifecycle, to_lifecycle=to_lifecycle,
        provenance_type=provenance, actor_user_id=actor,
        actor_type=actor_class, reason_code=reason,
    )
    _emit(OUTCOME_APPLIED)
    return {"status": OUTCOME_APPLIED, "fact_id": target_id,
            "from_state": from_state, "to_state": resolved_to, "reason": reason}


def confirm_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Record that someone affirmed this fact. Lands on USER_CONFIRMED.

    Never on VERIFIED. See the note above :data:`VERIFICATION_TRANSITIONS`:
    confirmation is a person saying they believe it, verification is a system of
    record being read, and a store that lets the first become the second turns
    its review queue into a laundering machine.
    """
    kwargs.setdefault("reason_code", "owner_action")
    return _apply_lifecycle(
        cur, operation=OP_CONFIRM, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def dispute_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Flag this fact as contested. The row stays; nothing is deleted."""
    kwargs.setdefault("reason_code", "source_disagreement")
    return _apply_lifecycle(
        cur, operation=OP_DISPUTE, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def flag_conflicting_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Mark this row as one side of a live disagreement.

    Called by the contradiction engine, which is why the actor defaults to the
    system rather than the owner: nobody clicked anything, a scan noticed two
    sources saying different things about the same subject in the same period.

    The row keeps its provenance. That separation is the point of the whole
    second axis — "your insurer's record says March, the policy document says
    April" is only sayable while both rows still remember where they came from,
    and the pre-ledger schema could not say it because CONFLICTING was filed as
    though it were a *source*.
    """
    kwargs.setdefault("reason_code", "source_disagreement")
    kwargs.setdefault("actor_type", ACTOR_SYSTEM)
    kwargs.setdefault("purpose", "system_maintenance")
    return _apply_lifecycle(
        cur, operation=OP_FLAG_CONFLICT, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def resolve_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Settle a disagreement in this row's favour. Lands on USER_CONFIRMED.

    Never on VERIFIED — see :data:`VERIFICATION_TRANSITIONS`. Choosing between
    two sources is judgement, and judgement is the ceiling of what a person can
    add to a fact's standing.

    This moves one row. Nominating a winner without doing anything about the
    rows it beat would leave the conflict half-settled and re-detected on the
    next scan, so callers should go through
    :func:`services.private_office.contradictions.resolve_conflict`, which moves
    the losers and writes the durable resolution record in the same breath.
    """
    kwargs.setdefault("reason_code", "conflict_resolution")
    return _apply_lifecycle(
        cur, operation=OP_RESOLVE, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def retire_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Retire one row as SUPERSEDED. Terminal.

    Distinct from :func:`supersede_facts`, which retires everything a projection
    replaced within one (subject, fact_type) scope and is driven by a system of
    record changing. This retires a single named row because a person decided it
    lost an argument — the losing side of a resolved conflict, when the member
    judged it a stale reading rather than a rival account still worth keeping in
    view.

    Terminal, and that is why it is not the default disposition anywhere. The
    validity window closes, so the row stops competing in detection and cannot
    be brought back in place; correcting the decision means recording a new
    fact, which leaves a trail.
    """
    kwargs.setdefault("reason_code", "replaced_by_newer")
    return _apply_lifecycle(
        cur, operation=OP_SUPERSEDE, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def archive_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Put a fact away without judging it.

    The verification state is left exactly as it was, which is the whole
    distinction between archiving and revoking: a fact the member no longer
    wants to see is not a fact the member says was wrong, and an archive that
    quietly rewrote the verification state would destroy that difference on the
    way to the same-looking outcome.
    """
    kwargs.setdefault("reason_code", "owner_action")
    return _apply_lifecycle(
        cur, operation=OP_ARCHIVE, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def revoke_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Withdraw a fact as never-having-been-true. Terminal.

    The row is retained. Deleting it would leave the audit trail pointing at an
    id nothing can describe, and would remove the evidence that the fact was
    ever asserted — which is precisely what someone correcting a record most
    needs to be able to show.
    """
    kwargs.setdefault("reason_code", "owner_correction")
    return _apply_lifecycle(
        cur, operation=OP_REVOKE, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def expire_fact(cur, *, owner_user_id: int, fact_id: int, **kwargs) -> dict:
    """Mark a fact as past its stated validity. Not terminal.

    Distinct from staleness, which is computed and reversible by a fresh
    observation. Expiry is a statement the fact made about itself — a policy
    that runs to a date, a licence that lapses — and it is recorded rather than
    computed because the date came from the world, not from a horizon table.
    """
    kwargs.setdefault("reason_code", "validity_window_closed")
    return _apply_lifecycle(
        cur, operation=OP_EXPIRE, owner_user_id=owner_user_id,
        fact_id=fact_id, **kwargs)


def _link_supersession(
    cur,
    *,
    owner_user_id: int,
    predecessor_id: int,
    successor_id: int,
    actor_user_id: int | None = None,
    actor_type: str = ACTOR_OWNER,
    purpose: str = "user_request",
) -> bool:
    """Point a retired fact at the one that replaced it.

    Refuses three shapes, each of which produces a chain a reader cannot walk:

    * a self-link, which is a one-row infinite loop;
    * a link whose predecessor belongs to a different owner, which would let a
      forged id splice a stranger's fact into this member's timeline;
    * a link that closes a cycle, checked by walking the predecessor's own
      ancestor chain looking for the successor. The walk is bounded — a chain
      longer than :data:`MAX_CHAIN_WALK` is treated as already broken rather
      than followed forever, because the failure mode of an unbounded walk in a
      request handler is a hung worker, not a wrong answer.

    It also refuses to re-point a predecessor that already has a *different*
    successor. Overwriting would fork the chain and lose the first replacement
    silently, which is the one outcome worse than refusing: the timeline would
    still read as complete.

    Returns True when the link was written.
    """
    owner = int(owner_user_id or 0)
    prior = int(predecessor_id or 0)
    successor = int(successor_id or 0)
    if owner <= 0 or prior <= 0 or successor <= 0:
        return False
    if prior == successor:
        LOGGER.warning("PRIVATE_FACT_CHAIN_SELF_LINK fact_id=%s", prior)
        return False
    # Walked from the predecessor, not from the successor. The successor row
    # already carries ``supersedes_id = prior`` — it was written at insert — so
    # a walk starting there finds the predecessor on its first hop every time
    # and would report the edge being formalised as a loop, refusing every
    # legitimate revision. The question that actually distinguishes a cycle is
    # whether the successor is *already behind* the predecessor.
    if _chain_would_cycle(cur, owner_user_id=owner, start_id=prior,
                          target_id=successor):
        LOGGER.warning(
            "PRIVATE_FACT_CHAIN_CYCLE predecessor=%s successor=%s",
            prior, successor)
        return False

    cur.execute(
        f"SELECT verification_state, lifecycle_state, provenance_type, "
        f"superseded_by_id "
        f"FROM {_schema.FACTS_TABLE} WHERE id = ? AND owner_user_id = ?",
        (prior, owner),
    )
    row = cur.fetchone()
    if row is None:
        return False
    from_state = _stored_verification(
        row["verification_state"] if hasattr(row, "keys") else row[0])
    from_lifecycle = str(
        (row["lifecycle_state"] if hasattr(row, "keys") else row[1]) or "")
    provenance = str(
        (row["provenance_type"] if hasattr(row, "keys") else row[2]) or "")
    existing_successor = int(
        (row["superseded_by_id"] if hasattr(row, "keys") else row[3]) or 0)
    if existing_successor and existing_successor != successor:
        LOGGER.warning(
            "PRIVATE_FACT_CHAIN_FORK predecessor=%s existing=%s attempted=%s",
            prior, existing_successor, successor)
        return False
    if existing_successor == successor:
        # The link is already there. Returning early rather than rewriting it
        # keeps the history honest: a retry, a replayed request, or a caller
        # being careful must not add a second "superseded" event to a timeline
        # where the fact was superseded once.
        return True

    now_iso = _now_iso()
    cur.execute(
        f"UPDATE {_schema.FACTS_TABLE} SET superseded_by_id = ?, "
        f"lifecycle_state = ?, verification_state = ?, "
        f"valid_to = COALESCE(valid_to, ?), updated_at = ? "
        f"WHERE id = ? AND owner_user_id = ?",
        (successor, _model.LIFECYCLE_SUPERSEDED,
         _model.VERIFICATION_SUPERSEDED, now_iso, now_iso, prior, owner),
    )
    _write_history(
        cur, owner_user_id=owner, fact_id=prior, operation=OP_SUPERSEDE,
        from_verification=from_state,
        to_verification=_model.VERIFICATION_SUPERSEDED,
        from_lifecycle=from_lifecycle, to_lifecycle=_model.LIFECYCLE_SUPERSEDED,
        provenance_type=provenance, related_fact_id=successor,
        actor_user_id=int(actor_user_id or owner), actor_type=actor_type,
        reason_code="replaced_by_newer",
    )
    _audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=_audit.ACTION_FACT_SUPERSEDE, object_type="FACT",
        object_id=str(prior), purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return True


#: How far a chain walk follows ``supersedes_id`` before declaring the chain
#: unusable. Real chains are short — a valuation revised a dozen times is a busy
#: fact — so this is a runaway guard, not a limit anyone should reach.
MAX_CHAIN_WALK = 64


def _chain_would_cycle(cur, *, owner_user_id: int, start_id: int,
                       target_id: int) -> bool:
    """Is ``target_id`` already an ancestor of ``start_id``?

    Walks ``supersedes_id`` backwards from ``start_id``. A True answer means the
    two rows are already ordered the other way round, so adding the requested
    link would put one of them both before and after itself.

    ``start_id`` itself is not a match — the caller has already rejected the
    self-link case, and treating the starting row as a hit would refuse every
    chain of length one.
    """
    owner = int(owner_user_id or 0)
    target = int(target_id or 0)
    seen: set[int] = set()
    current = int(start_id or 0)
    for _ in range(MAX_CHAIN_WALK):
        if current <= 0:
            return False
        if current in seen:
            # The existing chain is already looped. Not the link's doing, but
            # not something to add an edge to either.
            LOGGER.warning("PRIVATE_FACT_CHAIN_LOOPED at=%s", current)
            return True
        seen.add(current)
        cur.execute(
            f"SELECT supersedes_id FROM {_schema.FACTS_TABLE} "
            f"WHERE id = ? AND owner_user_id = ?",
            (current, owner),
        )
        row = cur.fetchone()
        if row is None:
            return False
        current = int((row["supersedes_id"] if hasattr(row, "keys") else row[0]) or 0)
        if current == target:
            return True
    LOGGER.warning("PRIVATE_FACT_CHAIN_TOO_LONG start=%s", start_id)
    # A chain this long is already malformed. Refusing the link is the
    # conservative answer: it leaves the data as it is rather than adding an
    # edge to a structure nothing can walk.
    return True


def expire_due_facts(
    cur,
    *,
    owner_user_id: int,
    now: object = None,
    limit: int = 200,
) -> dict:
    """Move facts whose ``expires_at`` has passed into EXPIRED.

    Owner-scoped like everything else — there is no "sweep all members" call,
    because a cross-owner UPDATE in this package is exactly the statement the
    write-boundary guard exists to make impossible to add casually.

    Returns ``{"scanned", "expired"}``. Both numbers are real counts, and an
    unreadable table raises rather than reporting zero: a sweep that reports
    ``{"scanned": 0, "expired": 0}`` when the table could not be read is the
    exact failure this package's schema module was written to prevent.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    _schema.require_private_schema(cur)

    moment = _iso(now, default=None) or _now_iso()
    cur.execute(
        f"SELECT id FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ? "
        f"AND expires_at != '' AND expires_at <= ? "
        f"ORDER BY expires_at LIMIT ?",
        (owner, _model.LIFECYCLE_ACTIVE, moment, max(1, min(int(limit or 200), 1000))),
    )
    due = [int(r["id"] if hasattr(r, "keys") else r[0]) for r in (cur.fetchall() or [])]

    expired = 0
    for fact_id in due:
        result = expire_fact(
            cur, owner_user_id=owner, fact_id=fact_id,
            actor_type=ACTOR_SYSTEM, reason_code="system_sweep",
            purpose="system_maintenance",
        )
        if result.get("status") == OUTCOME_APPLIED:
            expired += 1
    return {"scanned": len(due), "expired": expired}


#: The history projection, in SELECT order. One tuple so the column list and
#: the key names cannot drift apart.
_HISTORY_FIELDS: tuple[str, ...] = (
    "operation",
    "from_verification_state",
    "to_verification_state",
    "from_lifecycle_state",
    "to_lifecycle_state",
    "provenance_type",
    "related_fact_id",
    "actor_type",
    "reason_code",
    "created_at",
)


def list_fact_history(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    limit: int = 100,
) -> list[dict]:
    """The recorded events for one fact, newest first. Owner-scoped.

    Carries no values — see the note on the table DDL in ``schema``. The
    previous value of a revised fact is the superseded row itself, which
    :func:`list_facts` returns with ``include_superseded=True``.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    _schema.require_private_schema(cur)
    cur.execute(
        f"SELECT {', '.join(_HISTORY_FIELDS)} "
        f"FROM {_schema.FACT_HISTORY_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? "
        f"ORDER BY created_at DESC, id DESC LIMIT ?",
        (owner, int(fact_id or 0), max(1, min(int(limit or 100), 500))),
    )
    # Built from the explicit field tuple rather than ``dict(row)``. Callers
    # hand us whatever cursor they already have, and only a `sqlite3.Row`
    # factory makes a row mapping-like — on a plain tuple cursor ``dict(row)``
    # raises, which would make the history readable in tests and broken in the
    # one place it matters. Zipping the names we asked for works on both.
    return [
        {name: row[index] for index, name in enumerate(_HISTORY_FIELDS)}
        for row in (cur.fetchall() or [])
    ]


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def _row_to_fact(row) -> dict:
    data = dict(row)
    data["provenance"] = asdict(decode_provenance_ref(data.get("provenance_ref")))
    data["freshness"] = staleness(data)
    if "verification_state" in data:
        # Resolved rather than passed through, so a row the backfill has not
        # reached yet reads as LEGACY_UNKNOWN rather than as an empty string
        # that a client would have to decide how to render — and would
        # probably render as nothing at all, which reads as "verified" to
        # anyone who is not looking for the absence.
        data["verification_state"] = _stored_verification(
            data.get("verification_state"))
        # Two flags, not one, because they answer different questions and a
        # reader given only the first will use it for both. `trusted` means
        # "nothing is known to be wrong with this" — true of a plain assertion
        # nobody has checked. `affirmed` means "something checked it". A badge
        # driven by `trusted` alone would read as endorsement on a fact whose
        # entire provenance is that somebody typed it.
        data["trusted"] = _model.verification_is_trustworthy(
            data["verification_state"])
        data["affirmed"] = _model.verification_is_affirmed(
            data["verification_state"])
    return data


def list_facts(
    cur,
    *,
    owner_user_id: int,
    subject_type: str | None = None,
    subject_id: object = None,
    fact_types: Sequence[str] | None = None,
    domains: Sequence[str] | None = None,
    sensitivity_ceiling: object = _model.SENSITIVITY_RESTRICTED,
    include_superseded: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """Facts belonging to ``owner_user_id``. Never crosses an owner boundary.

    The owner predicate is not optional and not a keyword with a default — it is
    a required argument that goes into the ``WHERE`` clause of every query in
    this module. Stage 14 makes cross-owner reads a P0 gate, and the way that
    gate is actually held is by there being no code path here that can produce
    a query without it.

    ``sensitivity_ceiling`` defaults to ``RESTRICTED`` (everything) because the
    owner reading their own store is the common case; callers acting on behalf
    of a subsystem pass a lower ceiling. An unrecognised ceiling releases
    nothing, per :func:`model.sensitivity_within`.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    _schema.require_private_schema(cur)

    clauses = ["owner_user_id = ?"]
    params: list[Any] = [owner]

    if not include_superseded:
        clauses.append("lifecycle_state = ?")
        params.append(_model.LIFECYCLE_ACTIVE)
    if subject_type:
        clauses.append("subject_type = ?")
        params.append(str(subject_type).strip().upper()[:32])
    if subject_id is not None and str(subject_id).strip():
        clauses.append("subject_id = ?")
        params.append(str(subject_id).strip()[:MAX_SUBJECT_ID])

    wanted_types = [
        str(name).strip().lower()[:MAX_FACT_TYPE]
        for name in (fact_types or ())
        if str(name or "").strip()
    ]
    if wanted_types:
        clauses.append(f"fact_type IN ({','.join('?' * len(wanted_types))})")
        params.extend(wanted_types)

    wanted_domains = [d for d in (_model.normalize_domain(x) for x in (domains or ())) if d]
    if domains and not wanted_domains:
        # The caller asked for domains and named none this package recognises.
        # Returning everything would be the exact inversion of the request.
        return []
    if wanted_domains:
        clauses.append(f"domain IN ({','.join('?' * len(wanted_domains))})")
        params.extend(wanted_domains)

    ceiling = _model.normalize_sensitivity(sensitivity_ceiling)
    if not ceiling:
        return []
    releasable = [
        name for name in _model.SENSITIVITIES
        if _model.SENSITIVITY_RANK[name] <= _model.SENSITIVITY_RANK[ceiling]
    ]
    clauses.append(f"sensitivity IN ({','.join('?' * len(releasable))})")
    params.extend(releasable)

    # Bounded by construction (Stage 37). An unbounded read of a private store
    # is a full export waiting for one caller to forget a limit.
    bounded = max(1, min(int(limit or 100), 500))
    params.extend([bounded, max(0, int(offset or 0))])

    cur.execute(
        f"SELECT * FROM {_schema.FACTS_TABLE} WHERE {' AND '.join(clauses)} "
        f"ORDER BY observed_at DESC, id DESC LIMIT ? OFFSET ?",
        params,
    )
    return [_row_to_fact(row) for row in cur.fetchall()]


#: How many subjects one batched read may name. Bounded for the same reason
#: every other read here is bounded, and set above the Stage 16 node ceiling of
#: 100 so a full traversal resolves in one query rather than two.
MAX_SUBJECT_BATCH = 200


def list_facts_for_subjects(
    cur,
    *,
    owner_user_id: int,
    subject_type: str,
    subject_ids: Sequence[object],
    fact_types: Sequence[str] | None = None,
    domains: Sequence[str] | None = None,
    sensitivity_ceiling: object = _model.SENSITIVITY_RESTRICTED,
    include_superseded: bool = False,
    limit: int = 500,
) -> list[dict]:
    """Facts for many subjects at once, with the same owner and sensitivity rules.

    This exists because retrieval walks up to 100 nodes and then wants their
    facts. Calling :func:`list_facts` once per node would be correct and would
    also be the N+1 explosion Stage 37 names — 100 round trips to answer one
    question, each one re-checking a schema that has not changed. The filters
    below are deliberately identical to ``list_facts``; if they ever drift, the
    quieter of the two paths becomes the one an attacker prefers.

    Subject ids are de-duplicated and capped at :data:`MAX_SUBJECT_BATCH`. The
    cap is applied to the *input*, not the output, so the caller can tell that
    it bit: they asked about more subjects than one read will answer for.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    kind = str(subject_type or "").strip().upper()[:32]
    if not kind:
        return []

    seen: list[str] = []
    for value in subject_ids or ():
        text = str(value if value is not None else "").strip()[:MAX_SUBJECT_ID]
        if text and text not in seen:
            seen.append(text)
    if not seen:
        return []
    seen = seen[:MAX_SUBJECT_BATCH]

    _schema.require_private_schema(cur)

    clauses = ["owner_user_id = ?", "subject_type = ?",
               f"subject_id IN ({','.join('?' * len(seen))})"]
    params: list[Any] = [owner, kind, *seen]

    if not include_superseded:
        clauses.append("lifecycle_state = ?")
        params.append(_model.LIFECYCLE_ACTIVE)

    wanted_types = [
        str(name).strip().lower()[:MAX_FACT_TYPE]
        for name in (fact_types or ())
        if str(name or "").strip()
    ]
    if wanted_types:
        clauses.append(f"fact_type IN ({','.join('?' * len(wanted_types))})")
        params.extend(wanted_types)

    wanted_domains = [d for d in (_model.normalize_domain(x) for x in (domains or ())) if d]
    if domains and not wanted_domains:
        return []
    if wanted_domains:
        clauses.append(f"domain IN ({','.join('?' * len(wanted_domains))})")
        params.extend(wanted_domains)

    ceiling = _model.normalize_sensitivity(sensitivity_ceiling)
    if not ceiling:
        return []
    releasable = [
        name for name in _model.SENSITIVITIES
        if _model.SENSITIVITY_RANK[name] <= _model.SENSITIVITY_RANK[ceiling]
    ]
    clauses.append(f"sensitivity IN ({','.join('?' * len(releasable))})")
    params.extend(releasable)

    bounded = max(1, min(int(limit or 500), 1000))
    params.append(bounded)

    cur.execute(
        f"SELECT * FROM {_schema.FACTS_TABLE} WHERE {' AND '.join(clauses)} "
        f"ORDER BY observed_at DESC, id DESC LIMIT ?",
        params,
    )
    return [_row_to_fact(row) for row in cur.fetchall()]


def count_facts(cur, *, owner_user_id: int) -> int:
    """How many active facts this owner has. Owner-scoped like every read here.

    Stage 14 lists count endpoints explicitly as a leakage surface, and this is
    why: a count is the cheapest possible oracle. ``COUNT(*)`` without the owner
    predicate tells an attacker how many facts exist in the entire platform, and
    a count that responds to a subject id tells them whether that subject
    exists. Both are answered here only within one owner.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return 0
    _schema.require_private_schema(cur)
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ?",
        (owner, _model.LIFECYCLE_ACTIVE),
    )
    row = cur.fetchone()
    if row is None:
        return 0
    return int(row["n"] if hasattr(row, "keys") else row[0])


def count_facts_by_domain(
    cur,
    *,
    owner_user_id: int,
    sensitivity_ceiling: str = _model.SENSITIVITY_RESTRICTED,
) -> dict:
    """Active fact count per domain, for one owner, as a complete map.

    The landing surface groups facts by domain, and it must be able to say
    "LEGAL — no information yet" as confidently as it says "FINANCIAL — 2".
    A reader that returned only the domains that happen to have rows would
    force the screen to invent the missing keys, and a screen that invents
    vocabulary is a second authority on what a domain is. So every domain in
    :data:`model.DOMAINS` is present in the result, zeros included.

    One ``GROUP BY`` rather than one query per domain. The alternative costs
    seven round trips to answer a question the database answers in one, and
    seven chances for the owner predicate to be dropped from one of them.

    The sensitivity ceiling is applied here as well as in :func:`list_facts`.
    If it were not, a count would report facts the caller is not cleared to
    read — the summary would say FINANCIAL 3, the list would return 1, and the
    difference would be a working oracle for the existence of the other two.
    """
    owner = int(owner_user_id or 0)
    summary = {name: 0 for name in _model.DOMAINS}
    if owner <= 0:
        return summary

    ceiling = _model.normalize_sensitivity(sensitivity_ceiling)
    if not ceiling:
        return summary
    releasable = [
        name for name in _model.SENSITIVITIES
        if _model.SENSITIVITY_RANK[name] <= _model.SENSITIVITY_RANK[ceiling]
    ]

    _schema.require_private_schema(cur)
    params = [owner, _model.LIFECYCLE_ACTIVE, *releasable]
    cur.execute(
        f"SELECT domain, COUNT(*) AS n FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ? "
        f"AND sensitivity IN ({','.join('?' * len(releasable))}) "
        f"GROUP BY domain",
        params,
    )
    for row in cur.fetchall():
        keyed = hasattr(row, "keys")
        name = _model.normalize_domain(row["domain"] if keyed else row[0])
        # A row whose domain no longer normalizes is counted nowhere rather
        # than under a guessed heading. Silently folding it into GENERAL would
        # attribute a fact to a domain its writer never chose.
        if name in summary:
            summary[name] = int(row["n"] if keyed else row[1])
    return summary


# ---------------------------------------------------------------------------
# Evidence (Section 16 — what makes EVIDENCE_SUPPORTED reachable)
# ---------------------------------------------------------------------------
# `record_fact` can write any verification state the caller names, and the
# lifecycle operations above can reach every state except two. EVIDENCE_SUPPORTED
# and NEEDS_REVIEW are the exceptions, and they are exceptions on purpose: the
# first is only earned by producing something a third party can open, and the
# second is only reached by losing that support or by ageing past a horizon.
# Neither is something a caller should be able to assert. So the only routes to
# them are the four functions below, and each of them does the work first and
# asks about the fact's standing second.

MAX_EVIDENCE_REF = 128
MAX_EVIDENCE_LOCATOR = 128

#: The evidence projection, in SELECT order. One tuple so the column list and
#: the key names cannot drift apart, exactly as with :data:`_HISTORY_FIELDS`.
#: ``evidence_ref`` is an opaque identifier, not a title — resolving it to
#: something a person can read is the owning package's job, under its own owner
#: predicate, so that a citation never becomes a way to learn the name of a
#: document you are not entitled to open.
_EVIDENCE_FIELDS: tuple[str, ...] = (
    "id",
    "fact_id",
    "evidence_type",
    "evidence_ref",
    "locator",
    "attached_by_actor_type",
    "attached_by_actor_id",
    "attached_at",
    "detached_at",
    "detached_by_actor_type",
)

#: Outcomes of an evidence mutation. ``unchanged`` is distinct from ``attached``
#: because re-sending an attachment that is already live is an idempotent no-op,
#: and reporting it as a fresh attachment would make the telemetry say support
#: was gathered when nothing happened.
EVIDENCE_ATTACHED = "attached"
EVIDENCE_DETACHED = "detached"
EVIDENCE_UNCHANGED = "unchanged"


def _evidence_natural_key(
    *, evidence_type: str, evidence_ref: object, locator: object
) -> tuple[str, str, str]:
    """The tuple that identifies one citation, normalized the way it is stored.

    Trimmed and truncated *before* the lookup rather than after, so the SELECT
    that decides "is this a re-attachment" compares the same bytes the INSERT
    would write. Doing it in the other order is how a citation with a trailing
    space becomes a second row that the UNIQUE constraint then rejects.
    """
    return (
        evidence_type,
        str(evidence_ref or "").strip()[:MAX_EVIDENCE_REF],
        str(locator or "").strip()[:MAX_EVIDENCE_LOCATOR],
    )


def _count_live_evidence(cur, *, owner_user_id: int, fact_id: int) -> int:
    """How many citations currently support this fact.

    ``detached_at = ''`` is the live predicate throughout this package; see the
    note under the table DDL for why detachment is a soft delete.
    """
    cur.execute(
        f"SELECT COUNT(*) AS n FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? AND detached_at = ''",
        (int(owner_user_id), int(fact_id)),
    )
    row = cur.fetchone()
    if row is None:
        return 0
    return int(row["n"] if hasattr(row, "keys") else row[0])


def _load_fact_for_evidence(cur, *, owner_user_id: int, fact_id: int) -> dict | None:
    """The few fact columns an evidence mutation needs, or ``None``.

    Owner predicate in the SELECT, not applied to the result, for the reason
    given at length in :func:`_apply_lifecycle`: a query that fetches by id and
    compares owners afterwards has already read another member's row.
    """
    cur.execute(
        f"SELECT id, verification_state, lifecycle_state, domain, "
        f"subject_type, subject_id "
        f"FROM {_schema.FACTS_TABLE} WHERE id = ? AND owner_user_id = ?",
        (int(fact_id), int(owner_user_id)),
    )
    row = cur.fetchone()
    if row is None:
        return None
    keyed = hasattr(row, "keys")

    def _col(name: str, index: int):
        return row[name] if keyed else row[index]

    return {
        "id": int(_col("id", 0) or 0),
        "verification_state": _stored_verification(_col("verification_state", 1)),
        "lifecycle_state": str(_col("lifecycle_state", 2) or ""),
        "domain": str(_col("domain", 3) or ""),
        "subject_type": str(_col("subject_type", 4) or ""),
        "subject_id": str(_col("subject_id", 5) or ""),
    }


def list_evidence(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    include_detached: bool = False,
) -> list[dict]:
    """The citations attached to one fact, newest first. Owner-scoped.

    ``include_detached`` exists for one screen: the explanation of why a fact
    that used to be well-supported now needs review. Everywhere else the answer
    to "what supports this" must exclude withdrawn citations, which is why the
    default is the live set — a reader who forgot the flag gets the safe answer.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    target = int(fact_id or 0)
    if target <= 0:
        raise PrivateFactRejected("fact_id is required")
    _schema.require_private_schema(cur)

    clauses = ["owner_user_id = ?", "fact_id = ?"]
    params: list[Any] = [owner, target]
    if not include_detached:
        clauses.append("detached_at = ''")

    cur.execute(
        f"SELECT {', '.join(_EVIDENCE_FIELDS)} "
        f"FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE {' AND '.join(clauses)} "
        f"ORDER BY attached_at DESC, id DESC LIMIT 200",
        tuple(params),
    )
    # Built from the explicit field tuple rather than ``dict(row)``, for the
    # reason spelled out in :func:`list_fact_history`: only a `sqlite3.Row`
    # factory makes a row mapping-like, and this must work on a plain cursor.
    rows = []
    for row in (cur.fetchall() or []):
        item = {name: row[index] for index, name in enumerate(_EVIDENCE_FIELDS)}
        item["live"] = not str(item.get("detached_at") or "").strip()
        # Whether a person could be *shown* this, as opposed to merely told it
        # exists. A STATEMENT is somebody's word and an EXTERNAL reference may
        # be a link nobody in this system can open; a reviewer deciding whether
        # a fact is backed needs that distinction on the citation, not in a
        # separate lookup table they will forget to consult.
        item["resolvable"] = str(item.get("evidence_type") or "") in _model.RESOLVABLE_EVIDENCE
        rows.append(item)
    return rows


def attach_evidence(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    evidence_type: str,
    evidence_ref: str = "",
    locator: str = "",
    actor_user_id: int | None = None,
    actor_type: str = ACTOR_OWNER,
    purpose: str = "user_request",
) -> dict:
    """Record that something checkable was produced in support of this fact.

    Returns ``{"status", "fact_id", "evidence_id", "promoted", "live_evidence",
    "verification_state"}``.

    **The citation is written before the promotion is attempted, and a refused
    promotion is not an error.** That ordering is the whole design. Attaching a
    document to a DISPUTED fact is a legitimate thing to do — it is often the
    first step in settling the dispute — and
    :data:`VERIFICATION_TRANSITIONS` deliberately refuses to let it clear the
    dispute on its own. If the promotion ran first, or if a refusal raised, the
    evidence would be discarded precisely in the cases where the member was
    doing the right thing. So the link row lands, the fact's standing is asked
    about second, and ``promoted: False`` reports the difference honestly.

    An unrecognised ``evidence_type`` raises rather than defaulting. A default
    kind would let a typo attach evidence of a type nobody chose and still
    promote the fact to EVIDENCE_SUPPORTED — the citation would exist, be
    unopenable, and have moved the fact anyway.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    target = int(fact_id or 0)
    if target <= 0:
        raise PrivateFactRejected("fact_id is required")

    kind = _model.normalize_evidence_type(evidence_type)
    if not kind:
        raise PrivateFactRejected(f"unknown evidence type: {evidence_type!r}")

    _schema.require_private_schema(cur)

    actor = int(actor_user_id or owner)
    actor_class = _normalize_actor_type(actor_type)
    kind, ref, loc = _evidence_natural_key(
        evidence_type=kind, evidence_ref=evidence_ref, locator=locator)

    fact = _load_fact_for_evidence(cur, owner_user_id=owner, fact_id=target)
    if fact is None:
        # Same answer as "no such fact at all", per Section 14.
        return {"status": OUTCOME_NOT_FOUND, "fact_id": target, "evidence_id": 0,
                "promoted": False, "live_evidence": 0, "verification_state": "",
                "reason": "no_such_fact"}
    if fact["lifecycle_state"] != _model.LIFECYCLE_ACTIVE:
        # Citing a retired fact is not a partial success to be recorded with a
        # warning. An archived, revoked, expired or superseded row is not
        # something the member is still asserting, and letting evidence accrue
        # against it would build a support trail for a claim nobody is making.
        _audit.record(
            cur, actor_user_id=actor, owner_user_id=owner,
            action=_audit.ACTION_FACT_EVIDENCE_ATTACH,
            object_type=fact["subject_type"], object_id=fact["subject_id"],
            purpose=purpose, outcome=_audit.OUTCOME_DENIED,
        )
        return {"status": OUTCOME_REFUSED, "fact_id": target, "evidence_id": 0,
                "promoted": False, "live_evidence": 0,
                "verification_state": fact["verification_state"],
                "reason": "fact_not_active"}

    now_iso = _now_iso()

    # SELECT-then-UPDATE-or-INSERT rather than ``INSERT OR IGNORE``. The latter
    # reads as the obvious idempotent write and is not available here:
    # ``services.db`` rewrites it for PostgreSQL, and the rewrite cannot know
    # which constraint to name, so the statement that works locally is the one
    # that fails in production. It would also be wrong even if it worked —
    # ignoring the insert would leave a previously *detached* citation detached,
    # so re-attaching the document a member just re-uploaded would silently do
    # nothing while reporting success.
    cur.execute(
        f"SELECT id, detached_at FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? AND evidence_type = ? "
        f"AND evidence_ref = ? AND locator = ?",
        (owner, target, kind, ref, loc),
    )
    existing = cur.fetchone()

    if existing is not None:
        keyed = hasattr(existing, "keys")
        evidence_id = int(existing["id"] if keyed else existing[0])
        was_live = not str(
            (existing["detached_at"] if keyed else existing[1]) or "").strip()
        if was_live:
            # Already supporting this fact. Nothing to write and nothing to
            # promote — but still audited, because "the member sent this again"
            # is an access to their store and the audit trail is not a log of
            # state changes, it is a log of who touched what.
            live_now = _count_live_evidence(cur, owner_user_id=owner, fact_id=target)
            _audit.record(
                cur, actor_user_id=actor, owner_user_id=owner,
                action=_audit.ACTION_FACT_EVIDENCE_ATTACH,
                object_type=fact["subject_type"], object_id=fact["subject_id"],
                purpose=purpose, outcome=_audit.OUTCOME_OK,
            )
            _telemetry.emit(
                _telemetry.EVENT_EVIDENCE_LINKED, evidence_type=kind,
                actor_type=actor_class, domain=fact["domain"],
                attached=True, promoted=False, live_evidence=live_now,
            )
            return {"status": EVIDENCE_UNCHANGED, "fact_id": target,
                    "evidence_id": evidence_id, "promoted": False,
                    "live_evidence": live_now,
                    "verification_state": fact["verification_state"],
                    "reason": "already_attached"}
        # A withdrawn citation coming back. `detached_by_actor_type` is cleared
        # along with the timestamp: leaving it set would describe a live row as
        # having been detached by somebody, which is the kind of residue that
        # makes an audit trail unreadable a year later.
        cur.execute(
            f"UPDATE {_schema.FACT_EVIDENCE_TABLE} SET detached_at = '', "
            f"detached_by_actor_type = '', attached_at = ?, "
            f"attached_by_actor_type = ?, attached_by_actor_id = ?, "
            f"updated_at = ? WHERE id = ? AND owner_user_id = ?",
            (now_iso, actor_class, actor, now_iso, evidence_id, owner),
        )
    else:
        cur.execute(
            f"""INSERT INTO {_schema.FACT_EVIDENCE_TABLE}
            (owner_user_id, fact_id, evidence_type, evidence_ref, locator,
             attached_by_actor_type, attached_by_actor_id, attached_at,
             detached_at, detached_by_actor_type, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', '', ?, ?)""",
            (owner, target, kind, ref, loc, actor_class, actor, now_iso,
             now_iso, now_iso),
        )
        # The id comes back through the unique key rather than ``lastrowid``,
        # which is ``None`` on PostgreSQL for these tables — the same approach
        # `records._insert` takes, for the same reason.
        cur.execute(
            f"SELECT id FROM {_schema.FACT_EVIDENCE_TABLE} "
            f"WHERE owner_user_id = ? AND fact_id = ? AND evidence_type = ? "
            f"AND evidence_ref = ? AND locator = ?",
            (owner, target, kind, ref, loc),
        )
        inserted = cur.fetchone()
        evidence_id = 0
        if inserted is not None:
            evidence_id = int(
                inserted["id"] if hasattr(inserted, "keys") else inserted[0])

    live_now = _count_live_evidence(cur, owner_user_id=owner, fact_id=target)

    # Only now, with the citation safely recorded, is the fact's standing
    # reconsidered. `refused` here means the state machine declined to promote —
    # a DISPUTED or CONFLICTING fact, or one already ranked above
    # EVIDENCE_SUPPORTED — and none of those are failures of this call.
    outcome = _apply_lifecycle(
        cur, operation=OP_ATTACH_EVIDENCE, owner_user_id=owner, fact_id=target,
        actor_user_id=actor, actor_type=actor_class,
        reason_code="evidence_attached", purpose=purpose,
    )
    promoted = outcome.get("status") == OUTCOME_APPLIED

    _telemetry.emit(
        _telemetry.EVENT_EVIDENCE_LINKED, evidence_type=kind,
        actor_type=actor_class, domain=fact["domain"],
        attached=True, promoted=promoted, live_evidence=live_now,
    )
    return {
        "status": EVIDENCE_ATTACHED,
        "fact_id": target,
        "evidence_id": evidence_id,
        "promoted": promoted,
        "live_evidence": live_now,
        "verification_state": outcome.get("to_state") or fact["verification_state"],
        "reason": outcome.get("reason") or "",
    }


def detach_evidence(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    evidence_type: str,
    evidence_ref: str = "",
    locator: str = "",
    actor_user_id: int | None = None,
    actor_type: str = ACTOR_OWNER,
    purpose: str = "user_request",
) -> dict:
    """Withdraw one citation. Demotes the fact only if it was the last one.

    Returns the same shape as :func:`attach_evidence`, with ``demoted`` in place
    of ``promoted``.

    Two things here are deliberate and easy to get wrong in the other direction.

    The row is kept and ``detached_at`` is stamped, rather than deleted. A
    detached citation is the only surviving explanation for why a fact that used
    to sit at EVIDENCE_SUPPORTED is suddenly in NEEDS_REVIEW; hard-deleting it
    would leave the demotion in the fact's history with its cause erased.

    And the demotion only fires when the live count reaches zero. Dropping one
    of three supporting documents does not mean the fact has lost its
    justification, and a rule that demoted on every detachment would train
    members never to tidy up their citations.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    target = int(fact_id or 0)
    if target <= 0:
        raise PrivateFactRejected("fact_id is required")

    kind = _model.normalize_evidence_type(evidence_type)
    if not kind:
        raise PrivateFactRejected(f"unknown evidence type: {evidence_type!r}")

    _schema.require_private_schema(cur)

    actor = int(actor_user_id or owner)
    actor_class = _normalize_actor_type(actor_type)
    kind, ref, loc = _evidence_natural_key(
        evidence_type=kind, evidence_ref=evidence_ref, locator=locator)

    fact = _load_fact_for_evidence(cur, owner_user_id=owner, fact_id=target)
    if fact is None:
        return {"status": OUTCOME_NOT_FOUND, "fact_id": target, "evidence_id": 0,
                "demoted": False, "live_evidence": 0, "verification_state": "",
                "reason": "no_such_fact"}

    cur.execute(
        f"SELECT id, detached_at FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? AND evidence_type = ? "
        f"AND evidence_ref = ? AND locator = ?",
        (owner, target, kind, ref, loc),
    )
    row = cur.fetchone()
    if row is None:
        return {"status": OUTCOME_NOT_FOUND, "fact_id": target, "evidence_id": 0,
                "demoted": False,
                "live_evidence": _count_live_evidence(
                    cur, owner_user_id=owner, fact_id=target),
                "verification_state": fact["verification_state"],
                "reason": "no_such_evidence"}

    keyed = hasattr(row, "keys")
    evidence_id = int(row["id"] if keyed else row[0])
    already_detached = bool(
        str((row["detached_at"] if keyed else row[1]) or "").strip())
    live_now = _count_live_evidence(cur, owner_user_id=owner, fact_id=target)

    if already_detached:
        # Idempotent. Reporting this as a detachment would emit a second
        # `attached=False` event for one withdrawal and, worse, would run the
        # zero-count check again — demoting a fact whose evidence was removed
        # last week because somebody clicked the button twice.
        _telemetry.emit(
            _telemetry.EVENT_EVIDENCE_LINKED, evidence_type=kind,
            actor_type=actor_class, domain=fact["domain"],
            attached=False, promoted=False, live_evidence=live_now,
        )
        return {"status": EVIDENCE_UNCHANGED, "fact_id": target,
                "evidence_id": evidence_id, "demoted": False,
                "live_evidence": live_now,
                "verification_state": fact["verification_state"],
                "reason": "already_detached"}

    now_iso = _now_iso()
    cur.execute(
        f"UPDATE {_schema.FACT_EVIDENCE_TABLE} SET detached_at = ?, "
        f"detached_by_actor_type = ?, updated_at = ? "
        f"WHERE id = ? AND owner_user_id = ?",
        (now_iso, actor_class, now_iso, evidence_id, owner),
    )

    live_now = _count_live_evidence(cur, owner_user_id=owner, fact_id=target)

    demoted = False
    outcome: dict = {}
    if live_now == 0:
        outcome = _apply_lifecycle(
            cur, operation=OP_DETACH_EVIDENCE, owner_user_id=owner,
            fact_id=target, actor_user_id=actor, actor_type=actor_class,
            reason_code="evidence_withdrawn", purpose=purpose,
        )
        demoted = outcome.get("status") == OUTCOME_APPLIED
    else:
        # No lifecycle call means no audit row from `_apply_lifecycle`, and a
        # withdrawal that left the fact standing is still an act on the member's
        # store that has to appear in the trail.
        _audit.record(
            cur, actor_user_id=actor, owner_user_id=owner,
            action=_audit.ACTION_FACT_EVIDENCE_DETACH,
            object_type=fact["subject_type"], object_id=fact["subject_id"],
            purpose=purpose, outcome=_audit.OUTCOME_OK,
        )

    _telemetry.emit(
        _telemetry.EVENT_EVIDENCE_LINKED, evidence_type=kind,
        actor_type=actor_class, domain=fact["domain"],
        attached=False, promoted=demoted, live_evidence=live_now,
    )
    return {
        "status": EVIDENCE_DETACHED,
        "fact_id": target,
        "evidence_id": evidence_id,
        "demoted": demoted,
        "live_evidence": live_now,
        "verification_state": outcome.get("to_state") or fact["verification_state"],
        "reason": outcome.get("reason") or "",
    }


# ---------------------------------------------------------------------------
# Review (Sections 16, 32 — putting a fact in front of the member)
# ---------------------------------------------------------------------------
#: Why a fact is in the review queue. A closed set, and a superset of
#: :data:`telemetry.REVIEW_REASON_VOCAB` — the sweep only ever publishes
#: ``stale``, because that is the only reason a *sweep* produces, while the
#: queue also has to explain rows that were flagged for reasons the sweep did
#: not cause. Keeping the two lists separate is what stops the queue's richer
#: vocabulary from leaking into telemetry, where every extra enum value is a
#: new dimension on a metric nobody asked for.
REVIEW_STALE = "stale"
REVIEW_EVIDENCE_REMOVED = "evidence_removed"
REVIEW_DISPUTED = "disputed"
REVIEW_CONFLICTING = "conflicting"
REVIEW_FLAGGED = "flagged"

REVIEW_REASONS: tuple[str, ...] = (
    REVIEW_STALE, REVIEW_EVIDENCE_REMOVED, REVIEW_DISPUTED,
    REVIEW_CONFLICTING, REVIEW_FLAGGED,
)

#: How many rows a queue read may examine to fill one page. Staleness is
#: computed rather than stored — see the module docstring — so it cannot appear
#: in a ``WHERE`` clause, and the queue has to scan and filter in Python. The
#: multiplier bounds that scan: without it, "give me 50 rows to review" on a
#: store with no stale facts would read the entire table to prove it.
REVIEW_SCAN_MULTIPLIER = 10
REVIEW_SCAN_CEILING = 1000

#: Live states that already carry a more specific objection than "somebody
#: should look at this", and so are not candidates for the staleness sweep.
#: Mirrors the exclusions in :data:`VERIFICATION_TRANSITIONS` for
#: ``OP_FLAG_REVIEW``; kept as its own tuple because the sweep uses it in SQL,
#: and a sweep that selected rows the state machine would then refuse would do
#: its work by generating refusals.
_UNSWEEPABLE_STATES: tuple[str, ...] = (
    _model.VERIFICATION_DISPUTED,
    _model.VERIFICATION_CONFLICTING,
    _model.VERIFICATION_NEEDS_REVIEW,
)


def sweep_stale_facts(
    cur,
    *,
    owner_user_id: int,
    now: object = None,
    limit: int = 200,
) -> dict:
    """Flag facts that have aged past their citation horizon. Owner-scoped.

    Returns ``{"scanned", "flagged"}``. Both numbers are real counts, and an
    unreadable table raises rather than reporting zero — the same contract as
    :func:`expire_due_facts`, for the same reason: a sweep that reports
    ``{"scanned": 0, "flagged": 0}`` when it could not read the table is
    indistinguishable from a healthy one.

    This is not expiry and must not read like it. Expiry retires a fact whose
    stated validity window closed — something the member said in advance would
    stop being true. Staleness is the softer claim that nobody has looked in a
    while: the fact stays ACTIVE, stays readable, and is merely moved to
    NEEDS_REVIEW so it is presented with its observation date attached rather
    than as a description of how things are now. Hence ``freshness_horizon``
    rather than ``system_sweep`` in the history.

    The actor is the system and the purpose is maintenance, so this never
    stamps ``last_verified_at`` — ``OP_FLAG_REVIEW`` is not in
    :data:`VERIFYING_OPERATIONS`. A sweep noticing a fact is old is not a person
    looking at it, and a clock that a background job could refresh would make
    every fact permanently fresh and the horizon meaningless.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    _schema.require_private_schema(cur)

    moment = _parse_iso(now) or _now()
    bounded = max(1, min(int(limit or 200), REVIEW_SCAN_CEILING))

    cur.execute(
        f"SELECT id, observed_at, provenance_type FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ? "
        f"AND verification_state NOT IN ({','.join('?' * len(_UNSWEEPABLE_STATES))}) "
        f"ORDER BY observed_at ASC, id ASC LIMIT ?",
        (owner, _model.LIFECYCLE_ACTIVE, *_UNSWEEPABLE_STATES, bounded),
    )
    candidates = []
    for row in (cur.fetchall() or []):
        keyed = hasattr(row, "keys")
        candidates.append({
            "id": int(row["id"] if keyed else row[0]),
            "observed_at": row["observed_at"] if keyed else row[1],
            "provenance_type": row["provenance_type"] if keyed else row[2],
        })

    flagged = 0
    for candidate in candidates:
        if not staleness(candidate, at=moment)["stale"]:
            continue
        result = _apply_lifecycle(
            cur, operation=OP_FLAG_REVIEW, owner_user_id=owner,
            fact_id=candidate["id"], actor_type=ACTOR_SYSTEM,
            reason_code="freshness_horizon", purpose="system_maintenance",
        )
        if result.get("status") == OUTCOME_APPLIED:
            flagged += 1

    # Both halves, always. See the field comment on ``EVENT_REVIEW_SWEEP``:
    # `flagged` alone cannot distinguish a sweep that found nothing wrong from
    # a sweep that read no rows at all.
    _telemetry.emit(
        _telemetry.EVENT_REVIEW_SWEEP, reason=REVIEW_STALE,
        actor_type=ACTOR_SYSTEM, scanned=len(candidates), flagged=flagged,
    )
    return {"scanned": len(candidates), "flagged": flagged}


def review_queue(cur, *, owner_user_id: int, limit: int = 50) -> list[dict]:
    """Facts that need the member's attention, with a reason for each.

    Two populations, deliberately merged into one list. The first is every live
    fact in an :data:`model.UNTRUSTWORTHY_VERIFICATION` state — disputed,
    conflicting, already flagged. The second is facts that have aged past their
    horizon but which no sweep has reached yet.

    Including the second is what makes this read model correct rather than
    merely convenient. Staleness is computed at read time, so a queue built only
    from stored state would show an empty list on a store whose sweep has not
    run — which is precisely the store most in need of a review queue. The
    member should not have to know whether a background job is healthy in order
    to see that their facts are old.

    Each row carries a ``review_reason`` from :data:`REVIEW_REASONS`. The reason
    is not decoration: "this document was withdrawn" and "nobody has looked at
    this since March" call for completely different actions, and a queue that
    said only "needs review" for both would send the member hunting for what
    changed.

    Read-only. Nothing here flags, promotes or stamps anything —
    :func:`sweep_stale_facts` is the writer, and a read model that mutated on
    display would make the queue's contents depend on who opened it last.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    _schema.require_private_schema(cur)

    bounded = max(1, min(int(limit or 50), 200))
    scan = min(bounded * REVIEW_SCAN_MULTIPLIER, REVIEW_SCAN_CEILING)

    # Oldest observation first. That is both the natural review order and the
    # order that makes the bounded scan find stale rows rather than proving
    # their absence one fresh fact at a time.
    cur.execute(
        f"SELECT * FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND lifecycle_state = ? "
        f"ORDER BY observed_at ASC, id ASC LIMIT ?",
        (owner, _model.LIFECYCLE_ACTIVE, scan),
    )
    rows = [_row_to_fact(row) for row in (cur.fetchall() or [])]

    needs_review_ids = [
        int(row.get("id") or 0) for row in rows
        if row.get("verification_state") == _model.VERIFICATION_NEEDS_REVIEW
    ]
    # One grouped query rather than one per row. The question — "did this fact
    # lose its evidence, or just its freshness" — is answerable from the
    # citation table alone: a fact with detached citations and no live ones is
    # a fact whose support was withdrawn.
    evidence_removed: set[int] = set()
    if needs_review_ids:
        placeholders = ",".join("?" * len(needs_review_ids))
        cur.execute(
            f"SELECT fact_id, "
            f"SUM(CASE WHEN detached_at = '' THEN 1 ELSE 0 END) AS live_n, "
            f"COUNT(*) AS total_n "
            f"FROM {_schema.FACT_EVIDENCE_TABLE} "
            f"WHERE owner_user_id = ? AND fact_id IN ({placeholders}) "
            f"GROUP BY fact_id",
            (owner, *needs_review_ids),
        )
        for row in (cur.fetchall() or []):
            keyed = hasattr(row, "keys")
            fact_id = int(row["fact_id"] if keyed else row[0])
            live_n = int((row["live_n"] if keyed else row[1]) or 0)
            total_n = int((row["total_n"] if keyed else row[2]) or 0)
            if total_n > 0 and live_n == 0:
                evidence_removed.add(fact_id)

    queue: list[dict] = []
    for row in rows:
        state = row.get("verification_state") or ""
        stale = bool((row.get("freshness") or {}).get("stale"))
        untrustworthy = state in _model.UNTRUSTWORTHY_VERIFICATION

        if not untrustworthy and not stale:
            continue

        if state == _model.VERIFICATION_DISPUTED:
            # The member said this is wrong. More specific than anything the
            # machine could add, and it outranks staleness: telling somebody
            # their disputed fact is also old is not the next thing they need.
            reason = REVIEW_DISPUTED
        elif state == _model.VERIFICATION_CONFLICTING:
            reason = REVIEW_CONFLICTING
        elif state == _model.VERIFICATION_NEEDS_REVIEW:
            if int(row.get("id") or 0) in evidence_removed:
                reason = REVIEW_EVIDENCE_REMOVED
            elif stale:
                reason = REVIEW_STALE
            else:
                reason = REVIEW_FLAGGED
        else:
            # Trustworthy but past its horizon — the population a stored-state
            # query would miss entirely.
            reason = REVIEW_STALE

        row["review_reason"] = reason
        # Whether the sweep has caught up with this row yet. A UI can use it to
        # distinguish "we flagged this" from "we are telling you now because you
        # asked", which is honest about how the queue works.
        row["flagged"] = state == _model.VERIFICATION_NEEDS_REVIEW
        queue.append(row)
        if len(queue) >= bounded:
            break

    return queue
