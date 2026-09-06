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
from services.private_office import evidence as _evidence
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
#: "You asked for a state the row was already in." Distinct from WRITTEN so an
#: idempotent retry is visible as a no-op rather than reported as a second
#: change, which matters wherever a caller counts what it altered.
STATUS_EXISTING = "existing"

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
    # A person deliberately confirming a value ages like a person asserting one,
    # because it is the same kind of evidence — someone's understanding at a
    # moment — arrived at more carefully.
    _model.PROVENANCE_HUMAN_CONFIRMED: 180,
    _model.PROVENANCE_DOCUMENT_EXTRACTED: 365,
    # Shorter than a document and longer than an inference. A meeting captures
    # what was said, and what people say about their own arrangements goes out
    # of date faster than what a document records about them.
    _model.PROVENANCE_MEETING_DERIVED: 90,
    _model.PROVENANCE_USER_ASSERTED: 180,
    _model.PROVENANCE_INFERRED: 30,
    _model.PROVENANCE_ESTIMATED: 14,
    # Zero, so a proposal is never quotable as current state at any age. It is
    # not a reading that has gone stale; it was never a reading.
    _model.PROVENANCE_UNDX_PROPOSED: 0,
    # Zero for the same structural reason as an unparseable date: an unknown
    # origin is not a young one, and the reading that treats it as young is the
    # one that misleads.
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


class PrivateFactMissing(LookupError):
    """A named fact does not exist for this owner.

    A separate type from :class:`PrivateFactRejected` so the route layer can map
    it to 404 while a rejection maps to 400. They are genuinely different
    answers: "there is nothing here" versus "what you asked for is not allowed".

    Deliberately *not* separate from "it exists but belongs to somebody else" —
    every reader in this package scopes by ``owner_user_id``, so a foreign id
    raises exactly this, with exactly this message. Distinguishing the two would
    turn the error into an oracle for enumerating another account's id space.
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
# Write
# ---------------------------------------------------------------------------
#: How much the observation time must move before a refresh is worth a history
#: entry.
#:
#: A refresh is not a change — the claim, the source and the window are all
#: identical, and the fact row's own ``observed_at`` already records when it was
#: last seen. What history adds is corroboration over time: "this has been
#: re-confirmed by its source every month for a year" is a real signal and it is
#: not recoverable from a single timestamp.
#:
#: The threshold is what makes that affordable. A provider sync running hourly
#: would otherwise write twenty-four entries a day per fact, and a history tab
#: that is 99% "re-confirmed" is one nobody reads, which costs the member the
#: corrections buried in it. One entry per day per fact keeps the signal and
#: bounds the volume at something a member could actually scroll.
HISTORY_REFRESH_MIN_HOURS = 24


def _refresh_is_notable(previous: object, current: object) -> bool:
    """Whether a refresh moved the observation time far enough to record."""
    was = _parse_iso(previous)
    now = _parse_iso(current)
    if was is None or now is None:
        # An unparseable timestamp on either side means the comparison cannot be
        # made. Recording is the safe failure: a spurious history entry is
        # noise, a missing one is a gap in an append-only trail.
        return True
    return (now - was) >= timedelta(hours=HISTORY_REFRESH_MIN_HOURS)


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
    verification_state: object = None,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    allow_backfill: bool = False,
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
    if source in _model.BACKFILL_ONLY_PROVENANCE and not allow_backfill:
        # LEGACY_UNKNOWN is the honest label for a row written before provenance
        # was tracked, and it must stay hard to reach. If any caller could pass
        # it, "I would rather not say where this came from" becomes an available
        # option, and the ledger's central claim — that every fact can name its
        # origin — stops being true the first time somebody takes it. Only the
        # migration passes allow_backfill.
        raise PrivateFactRejected(
            f"{source} may only be written by the legacy backfill")

    # Verification is a separate axis and it is deliberately not settable here.
    #
    # A caller may open a fact at any *neutral* state — most write UNVERIFIED
    # and the review queue may open one at PENDING_REVIEW — but the states that
    # mean somebody checked something are unreachable from the create path. They
    # are reached by a verification transition, which is where the evidence
    # requirement is enforced. Allowing them here would let a writer assert
    # AUTHORITY_VERIFIED with nothing behind it, which is not a weaker claim
    # than a real one, it is an unfalsifiable one.
    verified = _model.normalize_verification(
        verification_state or _model.DEFAULT_VERIFICATION)
    if not verified:
        raise PrivateFactRejected(
            f"unknown verification_state: {verification_state!r}")
    if verified in _model.VERIFICATION_REQUIRES_EVIDENCE:
        raise PrivateFactRejected(
            f"{verified} is reached by verifying a fact, not by creating one")
    if verified in _model.VERIFICATION_NEGATIVE:
        # Same reasoning from the other end. "This failed verification" is the
        # outcome of a check, and a fact born FAILED records a check that never
        # happened.
        raise PrivateFactRejected(
            f"{verified} is the outcome of a check, not a starting state")

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
        f"SELECT id, observed_at, confidence FROM {_schema.FACTS_TABLE} "
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
        prior_seen = existing["observed_at"] if hasattr(existing, "keys") else existing[1]
        prior = existing["confidence"] if hasattr(existing, "keys") else existing[2]
        try:
            prior_score = float(prior or 0.0)
        except (TypeError, ValueError):
            prior_score = 0.0
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
        _telemetry.emit(
            _telemetry.EVENT_FACT_WRITE, outcome=STATUS_REFRESHED,
            domain=resolved_domain, sensitivity=resolved_sensitivity,
            provenance_type=source, superseded=False)
        if _refresh_is_notable(prior_seen, observed_iso):
            _history(cur, owner_user_id=owner, fact_id=row_id,
                     change_type=_model.CHANGE_REFRESHED,
                     actor_user_id=int(actor_user_id or owner),
                     note_key=_model.NOTE_SYSTEM)
        return {"status": STATUS_REFRESHED, "fact_id": row_id, "fact_key": key,
                "sensitivity": resolved_sensitivity, "domain": resolved_domain,
                # Reported but not written. A refresh is the same claim from the
                # same source arriving again; it is not a check, so whatever
                # verification the row already carries stands untouched.
                "verification_state": verified}

    cur.execute(
        f"""INSERT INTO {_schema.FACTS_TABLE}
        (owner_user_id, fact_key, subject_type, subject_id, fact_type,
         value_type, typed_value, value_number, provenance_type, provenance_ref,
         confidence, observed_at, valid_from, valid_to, sensitivity, domain,
         lifecycle_state, conflict_id, verification_state, verified_at,
         verified_by, supersedes_id, superseded_by_id, superseded_at,
         created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '',
                ?, '', 0, 0, 0, '', ?, ?)""",
        (
            owner, key, subject_kind, subject, kind, resolved_value_type,
            typed_value, value_number, source, ref, score, observed_iso,
            from_iso, to_iso, resolved_sensitivity, resolved_domain,
            _model.LIFECYCLE_ACTIVE, verified, now_iso, now_iso,
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
    _telemetry.emit(
        _telemetry.EVENT_FACT_WRITE, outcome=STATUS_WRITTEN,
        domain=resolved_domain, sensitivity=resolved_sensitivity,
        provenance_type=source, superseded=False)
    _history(cur, owner_user_id=owner, fact_id=fact_id,
             change_type=_model.CHANGE_CREATED,
             actor_user_id=int(actor_user_id or owner),
             to_state=verified,
             note_key=_model.NOTE_LEGACY_BACKFILL if allow_backfill else None)
    return {"status": STATUS_WRITTEN, "fact_id": fact_id, "fact_key": key,
            "sensitivity": resolved_sensitivity, "domain": resolved_domain,
            "verification_state": verified}


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
#: How far a chain walk will follow ``superseded_by_id`` before giving up.
#:
#: Not a guess at how many times a member might correct one fact — it is a
#: termination guarantee. The cycle check below is what *prevents* a loop, but a
#: reader that trusts the check and walks unbounded is one direct-SQL mistake or
#: one restored backup away from spinning forever inside a request. Sixty-four
#: corrections to a single fact is already far past anything meaningful, so a
#: walk that hits this limit has found a bug, and it reports one instead of
#: hanging.
MAX_CHAIN = 64


def _history(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    change_type: str,
    actor_user_id: int = 0,
    from_state: object = "",
    to_state: object = "",
    related_fact_id: int = 0,
    note_key: object = None,
) -> bool:
    """Append one immutable history entry. Returns whether it landed.

    Best-effort in the same sense as :mod:`audit`: a failure to record history
    is logged and swallowed rather than failing the write it describes. The
    alternative — letting a history failure roll back a legitimate correction —
    would mean the store loses the *fact* in order to protect the record of the
    fact, which is backwards.

    There is no value parameter and the table has no value column. See the DDL
    in :mod:`schema` for why. ``from_state``/``to_state`` carry vocabulary
    labels — lifecycle or verification states — and are truncated rather than
    trusted, because "it is only ever a state name" is exactly the assumption
    that holds until one caller passes something else.
    """
    change = _model.normalize_change_type(change_type)
    if not change:
        LOGGER.warning(
            "PRIVATE_FACT_HISTORY_UNKNOWN_CHANGE change=%s", str(change_type)[:64])
        return False
    note = ""
    if note_key is not None and str(note_key).strip():
        note = _model.normalize_note_key(note_key) or ""
        if not note:
            # Rejected rather than dropped-with-the-row: an unrecognised note is
            # a caller bug, and the entry itself is still worth having.
            LOGGER.warning(
                "PRIVATE_FACT_HISTORY_UNKNOWN_NOTE note=%s", str(note_key)[:64])
    try:
        cur.execute(
            f"""INSERT INTO {_schema.FACT_HISTORY_TABLE}
            (owner_user_id, fact_id, change_type, actor_user_id, from_state,
             to_state, related_fact_id, note_key, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(owner_user_id or 0), int(fact_id or 0), change,
                int(actor_user_id or 0), str(from_state or "")[:64],
                str(to_state or "")[:64], int(related_fact_id or 0), note,
                _now_iso(),
            ),
        )
        return True
    except Exception as exc:
        LOGGER.warning(
            "PRIVATE_FACT_HISTORY_WRITE_FAILED change=%s error=%s", change, exc)
        return False


def fact_history(cur, *, owner_user_id: int, fact_id: int, limit: int = 100) -> list[dict]:
    """History entries for one fact, oldest first, owner-scoped and bounded."""
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    if owner <= 0 or target <= 0:
        return []
    capped = max(1, min(int(limit or 100), 500))
    _schema.require_private_schema(cur)
    cur.execute(
        f"""SELECT id, fact_id, change_type, actor_user_id, from_state, to_state,
                   related_fact_id, note_key, created_at
        FROM {_schema.FACT_HISTORY_TABLE}
        WHERE owner_user_id = ? AND fact_id = ?
        ORDER BY id ASC LIMIT ?""",
        (owner, target, capped),
    )
    return [dict(row) for row in cur.fetchall()]


def fact_chain(cur, *, owner_user_id: int, fact_id: int) -> list[int]:
    """The supersession chain containing ``fact_id``, oldest first.

    Walks backwards to the head of the chain and then forwards to its tip, so
    the answer is the same list whichever link the caller happens to hold. Both
    walks are bounded by :data:`MAX_CHAIN` and both refuse to revisit an id, so
    a cycle that reached the table by some other path is reported as a truncated
    chain rather than hanging the request that found it.
    """
    owner = int(owner_user_id or 0)
    start = int(fact_id or 0)
    if owner <= 0 or start <= 0:
        return []
    _schema.require_private_schema(cur)

    def _link(target: int) -> tuple[int, int]:
        cur.execute(
            f"SELECT supersedes_id, superseded_by_id FROM {_schema.FACTS_TABLE} "
            f"WHERE owner_user_id = ? AND id = ?",
            (owner, target),
        )
        row = cur.fetchone()
        if row is None:
            return (0, 0)
        data = dict(row)
        return (int(data.get("supersedes_id") or 0),
                int(data.get("superseded_by_id") or 0))

    back, _forward = _link(start)
    if back == 0 and _forward == 0:
        cur.execute(
            f"SELECT 1 FROM {_schema.FACTS_TABLE} WHERE owner_user_id = ? AND id = ?",
            (owner, start),
        )
        return [start] if cur.fetchone() is not None else []

    seen = {start}
    head = start
    for _ in range(MAX_CHAIN):
        previous, _unused = _link(head)
        if previous <= 0 or previous in seen:
            break
        seen.add(previous)
        head = previous

    chain = [head]
    seen = {head}
    cursor = head
    for _ in range(MAX_CHAIN):
        _unused, following = _link(cursor)
        if following <= 0 or following in seen:
            break
        seen.add(following)
        chain.append(following)
        cursor = following
    return chain


def _reaches(cur, *, owner_user_id: int, start: int, target: int) -> bool:
    """Whether following ``superseded_by_id`` from ``start`` arrives at ``target``."""
    owner = int(owner_user_id or 0)
    cursor = int(start or 0)
    goal = int(target or 0)
    seen: set[int] = set()
    for _ in range(MAX_CHAIN):
        if cursor <= 0 or cursor in seen:
            return False
        if cursor == goal:
            return True
        seen.add(cursor)
        cur.execute(
            f"SELECT superseded_by_id FROM {_schema.FACTS_TABLE} "
            f"WHERE owner_user_id = ? AND id = ?",
            (owner, cursor),
        )
        row = cur.fetchone()
        if row is None:
            return False
        cursor = int(dict(row).get("superseded_by_id") or 0)
    # Ran out of budget without deciding, so answer "yes". The caller uses this
    # to refuse a link, and when the walk could not finish the safe outcome is
    # to refuse: declining a legitimate correction on a sixty-four-link chain is
    # an inconvenience the member can see and report, while permitting one that
    # closes an undetected ring is a read path that hangs.
    return True


def supersede_fact(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
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
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Correct a fact by writing a replacement and linking the two.

    This is the only supported way a stored fact's value changes, and it does
    not change one: the old row keeps its value, its provenance and its
    observation time, and moves to ``SUPERSEDED``. What the member sees as "I
    updated this" is two rows and a link, so the question "what did I believe
    before, and when did I stop" always has an answer.

    ``subject_type``, ``subject_id`` and ``fact_type`` are inherited from the
    superseded row and cannot be passed. A correction that changed the subject
    would not be a correction — it would be a new fact wearing the old one's
    history, and the chain would assert continuity between two claims about
    different things.

    Returns ``{"status", "fact_id", "superseded_fact_id", "fact_key",
    "sensitivity", "domain", "verification_state"}``.

    Raises :class:`PrivateFactMissing` when the target does not exist for this
    owner, and :class:`PrivateFactRejected` when the link would break the chain
    invariants or the replacement itself is not writable.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    old_id = int(fact_id or 0)
    if old_id <= 0:
        raise PrivateFactRejected("fact_id is required")

    _schema.require_private_schema(cur)
    cur.execute(
        f"""SELECT id, subject_type, subject_id, fact_type, sensitivity, domain,
                   lifecycle_state, superseded_by_id
        FROM {_schema.FACTS_TABLE} WHERE owner_user_id = ? AND id = ?""",
        (owner, old_id),
    )
    row = cur.fetchone()
    if row is None:
        # Owner-scoped, so a fact belonging to somebody else is reported exactly
        # as one that does not exist. The two must be indistinguishable or the
        # error message becomes an oracle for probing other accounts' id space.
        raise PrivateFactMissing(f"fact {old_id} not found")
    old = dict(row)

    if int(old.get("superseded_by_id") or 0) != 0:
        # A second correction of an already-corrected row is a fork, and a fork
        # is not a supersession — it is two claims about what replaced the same
        # thing, which is a contradiction and has its own home in `conflict_id`.
        # Refusing here keeps `superseded_by_id` honest as a single link; the
        # caller's remedy is to correct the tip of the chain.
        raise PrivateFactRejected(
            f"fact {old_id} is already superseded; correct the current fact instead")
    if _model.normalize_lifecycle(old.get("lifecycle_state")) == _model.LIFECYCLE_ARCHIVED:
        raise PrivateFactRejected(f"fact {old_id} is archived and cannot be corrected")

    written = _record_fact(
        cur,
        owner_user_id=owner,
        subject_type=old.get("subject_type"),
        subject_id=old.get("subject_id"),
        fact_type=old.get("fact_type"),
        value=value,
        value_type=value_type,
        provenance_type=provenance_type,
        provenance=provenance,
        confidence=confidence,
        observed_at=observed_at,
        valid_from=valid_from,
        valid_to=valid_to,
        # Inherited when the caller says nothing. A correction that silently
        # dropped to the default sensitivity would publish, to every reader
        # allowed at that level, a value the member had classified higher.
        sensitivity=sensitivity or old.get("sensitivity"),
        domain=domain or old.get("domain"),
        actor_user_id=actor_user_id,
        purpose=purpose,
    )
    new_id = int(written.get("fact_id") or 0)
    if new_id <= 0:
        raise PrivateFactRejected("replacement fact could not be written")

    if new_id == old_id:
        # Reachable, and this is the reason the check exists rather than being
        # obviously unnecessary: `_record_fact` returns `refreshed` pointing at
        # an existing row when the same claim from the same source in the same
        # window is written again. Correcting a fact to the value it already
        # holds lands right back on it, and without this the row would be
        # recorded as having superseded itself.
        raise PrivateFactRejected(
            "replacement is identical to the fact it would supersede")
    if _reaches(cur, owner_user_id=owner, start=new_id, target=old_id):
        # Same mechanism, one link further out: correcting A to B and then B
        # back to A's value re-uses A's row and would close the chain into a
        # ring that every forward walk spins in.
        raise PrivateFactRejected(
            "that correction would create a supersession cycle")

    cur.execute(
        f"SELECT supersedes_id, lifecycle_state FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND id = ?",
        (owner, new_id),
    )
    replacement = dict(cur.fetchone() or {})
    if int(replacement.get("supersedes_id") or 0) not in (0, old_id):
        raise PrivateFactRejected(
            f"fact {new_id} already corrects another fact")
    if _model.normalize_lifecycle(replacement.get("lifecycle_state")) != _model.LIFECYCLE_ACTIVE:
        raise PrivateFactRejected(
            f"fact {new_id} is not active and cannot supersede another fact")

    now_iso = _now_iso()
    cur.execute(
        f"""UPDATE {_schema.FACTS_TABLE}
        SET lifecycle_state = ?, superseded_by_id = ?, superseded_at = ?,
            updated_at = ?
        WHERE owner_user_id = ? AND id = ? AND superseded_by_id = 0""",
        (_model.LIFECYCLE_SUPERSEDED, new_id, now_iso, now_iso, owner, old_id),
    )
    if getattr(cur, "rowcount", 1) == 0:
        # The guard clause repeated as a WHERE predicate, so a concurrent
        # correction of the same row loses instead of overwriting the link the
        # winner just wrote. The read-then-write above is not atomic; this is.
        raise PrivateFactRejected(
            f"fact {old_id} was superseded by a concurrent correction")
    cur.execute(
        f"""UPDATE {_schema.FACTS_TABLE} SET supersedes_id = ?, updated_at = ?
        WHERE owner_user_id = ? AND id = ?""",
        (old_id, now_iso, owner, new_id),
    )

    actor = int(actor_user_id or owner)
    _history(cur, owner_user_id=owner, fact_id=old_id,
             change_type=_model.CHANGE_CORRECTED, actor_user_id=actor,
             from_state=_model.LIFECYCLE_ACTIVE,
             to_state=_model.LIFECYCLE_SUPERSEDED, related_fact_id=new_id,
             note_key=_model.NOTE_OWNER_ACTION)
    _history(cur, owner_user_id=owner, fact_id=new_id,
             change_type=_model.CHANGE_CORRECTS, actor_user_id=actor,
             to_state=_model.LIFECYCLE_ACTIVE, related_fact_id=old_id,
             note_key=_model.NOTE_OWNER_ACTION)

    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_FACT_SUPERSEDE,
        object_type=str(old.get("subject_type") or ""),
        object_id=old.get("subject_id"), purpose=purpose,
        outcome=_audit.OUTCOME_OK,
    )
    _telemetry.emit(
        _telemetry.EVENT_FACT_WRITE, outcome=STATUS_WRITTEN,
        domain=written.get("domain"), sensitivity=written.get("sensitivity"),
        provenance_type=provenance_type, superseded=True)

    result = dict(written)
    result["status"] = STATUS_WRITTEN
    result["superseded_fact_id"] = old_id
    return result


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------
#: Live evidence links one fact may carry.
#:
#: The same reasoning as ``evidence.MAX_REFS`` and a different number, because
#: this cap governs a different thing. That one bounds a citation list rendered
#: inline; this one bounds how many sources a member may attach to a single
#: claim over its whole life. Fifty is far past honest — a fact supported by
#: fifty documents is not better supported than one backed by three, it is a
#: fact somebody attached a folder to — and the cap is what stops one fact's
#: evidence tab from becoming an unbounded read.
MAX_EVIDENCE_PER_FACT = 50


def _load_fact(cur, owner: int, fact_id: int, columns: str) -> dict:
    """One owner-scoped fact row, or raise :class:`PrivateFactMissing`."""
    cur.execute(
        f"SELECT {columns} FROM {_schema.FACTS_TABLE} "
        f"WHERE owner_user_id = ? AND id = ?",
        (owner, fact_id),
    )
    row = cur.fetchone()
    if row is None:
        raise PrivateFactMissing(f"fact {fact_id} not found")
    return dict(row)


def link_evidence(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    source_ref: object,
    relation: object = None,
    note_key: object = None,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Attach a source to a fact. Returns ``{"status", "evidence_id", ...}``.

    ``source_ref`` is a canonical ``kind:id`` reference in the :mod:`evidence`
    vocabulary and must resolve, *for this owner*, at the moment it is linked.
    Refusing an unresolvable ref at write time is the cheap half of reference
    integrity: the expensive half — a source that disappears later — cannot be
    prevented and is reported at read time as SOURCE_UNAVAILABLE instead.

    Re-linking a source already attached is not an error and does not duplicate;
    it returns ``existing``. A member who taps "attach" twice has expressed one
    intention, and a second row would double the apparent support behind a fact
    without adding a second source.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateFactRejected("owner_user_id is required")
    target = int(fact_id or 0)
    if target <= 0:
        raise PrivateFactRejected("fact_id is required")

    parsed = _evidence.parse_ref(source_ref)
    if parsed is None:
        raise PrivateFactRejected(f"not a usable evidence reference: {source_ref!r}")
    kind, _row_id = parsed
    ref = f"{kind}:{_row_id}"

    link_relation = _model.normalize_evidence_relation(
        relation or _model.DEFAULT_EVIDENCE_RELATION)
    if not link_relation:
        raise PrivateFactRejected(f"unknown evidence relation: {relation!r}")

    note = ""
    if note_key is not None and str(note_key).strip():
        note = _model.normalize_note_key(note_key) or ""
        if not note:
            raise PrivateFactRejected(f"unknown note_key: {note_key!r}")

    _schema.require_private_schema(cur)
    _load_fact(cur, owner, target, "id")

    resolved = _evidence.resolve_refs(cur, owner, [ref])
    if not resolved or not resolved[0].get("exists"):
        # Owner-scoped in the resolver, so a ref naming somebody else's document
        # is refused with the same words as one naming nothing.
        raise PrivateFactRejected(f"evidence source {ref} is not available")

    cur.execute(
        f"SELECT id, relation, detached_at FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? AND source_ref = ? "
        f"ORDER BY id DESC LIMIT 1",
        (owner, target, ref),
    )
    prior = cur.fetchone()
    now_iso = _now_iso()
    actor = int(actor_user_id or owner)
    if prior is not None:
        previous = dict(prior)
        if not str(previous.get("detached_at") or ""):
            return {"status": STATUS_EXISTING, "evidence_id": int(previous["id"]),
                    "source_ref": ref, "relation": previous.get("relation"),
                    "fact_id": target}
        # Detached earlier and now being re-attached. A new row rather than
        # clearing `detached_at`, because the earlier attachment and its
        # withdrawal both happened and un-detaching would erase the withdrawal.

    cur.execute(
        f"SELECT COUNT(*) AS live FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? AND detached_at = ''",
        (owner, target),
    )
    live = int(dict(cur.fetchone() or {}).get("live") or 0)
    if live >= MAX_EVIDENCE_PER_FACT:
        raise PrivateFactRejected(
            f"a fact may carry at most {MAX_EVIDENCE_PER_FACT} live sources")

    cur.execute(
        f"""INSERT INTO {_schema.FACT_EVIDENCE_TABLE}
        (owner_user_id, fact_id, source_ref, source_kind, relation, note_key,
         linked_by, linked_at, detached_at, detached_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', 0, ?)""",
        (owner, target, ref, kind, link_relation, note, actor, now_iso, now_iso),
    )
    cur.execute(
        f"SELECT id FROM {_schema.FACT_EVIDENCE_TABLE} "
        f"WHERE owner_user_id = ? AND fact_id = ? AND source_ref = ? "
        f"ORDER BY id DESC LIMIT 1",
        (owner, target, ref),
    )
    evidence_id = int(dict(cur.fetchone() or {}).get("id") or 0)

    _history(cur, owner_user_id=owner, fact_id=target,
             change_type=_model.CHANGE_EVIDENCE_LINKED, actor_user_id=actor,
             to_state=link_relation, note_key=note or _model.NOTE_OWNER_ACTION)
    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_FACT_EVIDENCE_LINK, object_type="fact",
        object_id=target, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return {"status": STATUS_WRITTEN, "evidence_id": evidence_id,
            "source_ref": ref, "relation": link_relation, "fact_id": target}


def unlink_evidence(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    source_ref: object,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
) -> dict:
    """Detach a source from a fact. The link survives, marked withdrawn.

    Deleting would erase the answer to "what was this verified against, at the
    time it was verified" — which is the one question a badge has to be able to
    answer even after the member changes their mind about the source.

    Detaching the last supporting source does **not** silently revoke a
    verification state. The fact is left holding a badge with nothing live
    behind it, which the integrity sweep reports and the review queue surfaces.
    Quietly downgrading it would be this package making a truth judgement on its
    own, which is exactly what it is not allowed to do.
    """
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    if owner <= 0 or target <= 0:
        raise PrivateFactRejected("owner_user_id and fact_id are required")
    parsed = _evidence.parse_ref(source_ref)
    if parsed is None:
        raise PrivateFactRejected(f"not a usable evidence reference: {source_ref!r}")
    ref = f"{parsed[0]}:{parsed[1]}"

    _schema.require_private_schema(cur)
    _load_fact(cur, owner, target, "id")

    now_iso = _now_iso()
    actor = int(actor_user_id or owner)
    cur.execute(
        f"""UPDATE {_schema.FACT_EVIDENCE_TABLE}
        SET detached_at = ?, detached_by = ?
        WHERE owner_user_id = ? AND fact_id = ? AND source_ref = ?
          AND detached_at = ''""",
        (now_iso, actor, owner, target, ref),
    )
    detached = int(getattr(cur, "rowcount", 0) or 0)
    if detached <= 0:
        return {"status": STATUS_EXISTING, "detached": 0, "source_ref": ref,
                "fact_id": target}

    _history(cur, owner_user_id=owner, fact_id=target,
             change_type=_model.CHANGE_EVIDENCE_UNLINKED, actor_user_id=actor,
             note_key=_model.NOTE_OWNER_ACTION)
    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_FACT_EVIDENCE_UNLINK, object_type="fact",
        object_id=target, purpose=purpose, outcome=_audit.OUTCOME_OK,
    )
    return {"status": STATUS_WRITTEN, "detached": detached, "source_ref": ref,
            "fact_id": target}


def fact_evidence(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    include_detached: bool = False,
) -> list[dict]:
    """Evidence attached to one fact, each entry carrying its availability.

    Availability is resolved now, not read from a column. A source the member
    deleted last week reads ``SOURCE_UNAVAILABLE`` here even though the link row
    is unchanged, because the question the evidence tab asks is "can I go and
    look at this", and the only honest way to answer it is to try.
    """
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    if owner <= 0 or target <= 0:
        return []
    _schema.require_private_schema(cur)
    clause = "" if include_detached else " AND detached_at = ''"
    cur.execute(
        f"""SELECT id, fact_id, source_ref, source_kind, relation, note_key,
                   linked_by, linked_at, detached_at
        FROM {_schema.FACT_EVIDENCE_TABLE}
        WHERE owner_user_id = ? AND fact_id = ?{clause}
        ORDER BY id ASC LIMIT ?""",
        (owner, target, MAX_EVIDENCE_PER_FACT * 4),
    )
    rows = [dict(row) for row in cur.fetchall()]
    if not rows:
        return []

    availability = {
        entry["ref"]: entry
        for entry in _evidence.resolve_refs(
            cur, owner, [row["source_ref"] for row in rows][:_evidence.MAX_REFS])
    }
    out: list[dict] = []
    for row in rows:
        resolved = availability.get(row["source_ref"])
        # A ref past the resolver's own cap was never checked. Reporting it as
        # available would be asserting something nobody looked at; reporting it
        # unavailable would be claiming the source is gone. It is neither —
        # `checked` is what separates the two, and the read model shows an
        # unchecked source as present but unconfirmed.
        checked = resolved is not None
        available = bool(resolved and resolved.get("exists"))
        row["checked"] = checked
        row["available"] = available
        row["status"] = (_model.SOURCE_AVAILABLE if available
                         else _model.SOURCE_UNAVAILABLE)
        row["label"] = (resolved or {}).get("label", "")
        row["detached"] = bool(str(row.get("detached_at") or ""))
        row["supports"] = (_model.evidence_supports(row.get("relation"))
                           and not row["detached"] and available)
        out.append(row)
    return out


def supporting_evidence_count(cur, *, owner_user_id: int, fact_id: int) -> int:
    """How many live, resolvable, *supporting* sources stand behind a fact.

    All four adjectives are load bearing, and each one is a way the count could
    otherwise lie: a detached link is one the member withdrew, an unresolvable
    one names a source that is gone, and a CONTRADICTS link is the strongest
    reason to doubt the fact rather than a reason to believe it.
    """
    return sum(1 for row in fact_evidence(
        cur, owner_user_id=owner_user_id, fact_id=fact_id) if row["supports"])


# ---------------------------------------------------------------------------
# Verification transitions
# ---------------------------------------------------------------------------
def verification_status(row: object, *, at: datetime | None = None) -> dict:
    """The verification axis of one fact row, with expiry computed at read.

    Returns ``{"state", "effective_state", "verified_at", "expires_at",
    "expired", "positive"}``. ``state`` is what is stored; ``effective_state``
    is what a reader should act on, and the two differ exactly when a positive
    verification has aged past its horizon.

    Nothing writes EXPIRED. A sweeper that stamped it would leave a window
    between the horizon passing and the sweep running in which the database says
    verified and the truth is that nobody has checked in over a year — and that
    window is precisely when a member acts on the badge.
    """
    data = dict(row) if not isinstance(row, dict) else row
    stored = _model.normalize_verification(data.get("verification_state"))
    if not stored:
        # An unreadable state is not a mild problem on this axis: it is a claim
        # about how well-checked something is that nobody can interpret. It
        # reads as UNVERIFIED, which is the floor rather than a guess.
        stored = _model.DEFAULT_VERIFICATION
    verified_at = str(data.get("verified_at") or "")
    horizon = _model.VERIFICATION_HORIZON_DAYS.get(stored, 0)
    moment = at or _now()
    expires_at = ""
    expired = False
    if horizon > 0 and verified_at:
        checked = _parse_iso(verified_at)
        if checked is not None:
            deadline = checked + timedelta(days=horizon)
            expires_at = deadline.isoformat()
            expired = moment >= deadline
        else:
            # A positive state whose timestamp cannot be read cannot be shown to
            # be current, and "cannot be shown to be current" is the definition
            # of expired here. The alternative is a badge that never ages
            # because its date is corrupt.
            expired = True
    effective = _model.VERIFICATION_EXPIRED if expired else stored
    return {
        "state": stored,
        "effective_state": effective,
        "verified_at": verified_at,
        "expires_at": expires_at,
        "expired": expired,
        "positive": _model.verification_is_positive(effective),
    }


def set_verification(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    verification_state: object,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    note_key: object = None,
) -> dict:
    """Move one fact along the verification axis. The only way a badge is set.

    The rule this function exists to enforce, in one line: **a positive
    verification state requires at least one live, resolvable, supporting piece
    of evidence, checked at the moment the state is set.** §50 — no orphan
    Verified badge with no evidence trail.

    Provenance is not touched. Recording that somebody checked a fact must not
    destroy the record of where it came from, and a fact can be
    DOCUMENT_EXTRACTED in origin and AUTHORITY_VERIFIED in checking at once —
    those are two true statements about two different things.

    ``EXPIRED`` is not settable. Expiry is computed from ``verified_at`` and the
    state's horizon, so writing it would be storing a derived value that then
    goes stale on its own.
    """
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    if owner <= 0 or target <= 0:
        raise PrivateFactRejected("owner_user_id and fact_id are required")

    wanted = _model.normalize_verification(verification_state)
    if not wanted:
        raise PrivateFactRejected(
            f"unknown verification_state: {verification_state!r}")
    if wanted == _model.VERIFICATION_EXPIRED:
        raise PrivateFactRejected(
            "EXPIRED is computed from verified_at, not set")

    note = ""
    if note_key is not None and str(note_key).strip():
        note = _model.normalize_note_key(note_key) or ""
        if not note:
            raise PrivateFactRejected(f"unknown note_key: {note_key!r}")

    _schema.require_private_schema(cur)
    current = _load_fact(
        cur, owner, target,
        "id, verification_state, verified_at, lifecycle_state, subject_type, subject_id")

    if _model.normalize_lifecycle(current.get("lifecycle_state")) != _model.LIFECYCLE_ACTIVE:
        # Verifying a superseded fact would attach a fresh check to a value the
        # member has already replaced, and the detail screen would show a
        # recently-verified badge on the row it is telling them is out of date.
        raise PrivateFactRejected(
            f"fact {target} is not active and cannot be verified")

    if wanted in _model.VERIFICATION_REQUIRES_EVIDENCE:
        supporting = supporting_evidence_count(
            cur, owner_user_id=owner, fact_id=target)
        if supporting <= 0:
            raise PrivateFactRejected(
                f"{wanted} requires at least one live supporting source")

    before = _model.normalize_verification(
        current.get("verification_state")) or _model.DEFAULT_VERIFICATION
    now_iso = _now_iso()
    actor = int(actor_user_id or owner)
    # `verified_at` records when this check happened, for every state including
    # the negative ones: "this failed verification eighteen months ago" is a
    # different statement from "this failed verification this morning", and only
    # a timestamp tells them apart.
    cur.execute(
        f"""UPDATE {_schema.FACTS_TABLE}
        SET verification_state = ?, verified_at = ?, verified_by = ?,
            updated_at = ?
        WHERE owner_user_id = ? AND id = ?""",
        (wanted, now_iso, actor, now_iso, owner, target),
    )
    _history(cur, owner_user_id=owner, fact_id=target,
             change_type=_model.CHANGE_VERIFICATION_SET, actor_user_id=actor,
             from_state=before, to_state=wanted,
             note_key=note or _model.NOTE_OWNER_ACTION)
    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_FACT_VERIFY,
        object_type=str(current.get("subject_type") or ""),
        object_id=current.get("subject_id"), purpose=purpose,
        outcome=_audit.OUTCOME_OK,
    )
    return {"status": STATUS_WRITTEN, "fact_id": target, "from_state": before,
            "verification_state": wanted, "verified_at": now_iso}


def archive_fact(
    cur,
    *,
    owner_user_id: int,
    fact_id: int,
    actor_user_id: int | None = None,
    purpose: str = "user_request",
    note_key: str | None = None,
) -> dict:
    """Retire a fact so the store stops asserting it. Idempotent.

    Archiving is not deletion and not supersession. The row stays, keeps its
    value, its provenance and its whole evidence trail, and remains readable as
    history — what changes is that it is no longer *claimed*. That distinction
    is the reason this exists as its own transition rather than as a lifecycle
    argument on some other writer: "this was replaced by a better value" and
    "this should never have been asserted" are different statements about the
    past, and only one of them leaves a successor.

    Deliberately **not** reachable as a side effect of detection. A conflict
    engine that could archive would be a conflict engine that decides which of
    two claims is wrong, and this package's central rule is that it does not.
    The caller here is always executing an instruction a person gave.

    Idempotent because the realistic caller is a conflict resolution rejecting
    several facts at once, where a partial failure part-way through would leave
    a decision half-applied — and a retry of that is far more useful than an
    error about the rows that already succeeded.
    """
    owner = int(owner_user_id or 0)
    target = int(fact_id or 0)
    if owner <= 0 or target <= 0:
        raise PrivateFactRejected("owner_user_id and fact_id are required")

    note = ""
    if note_key is not None and str(note_key).strip():
        note = _model.normalize_note_key(note_key) or ""
        if not note:
            raise PrivateFactRejected(f"unknown note_key: {note_key!r}")

    _schema.require_private_schema(cur)
    current = _load_fact(
        cur, owner, target, "id, lifecycle_state, subject_type, subject_id")
    before = _model.normalize_lifecycle(current.get("lifecycle_state"))
    if before == _model.LIFECYCLE_ARCHIVED:
        return {"status": STATUS_EXISTING, "fact_id": target,
                "lifecycle_state": _model.LIFECYCLE_ARCHIVED}

    now_iso = _now_iso()
    actor = int(actor_user_id or owner)
    # `superseded_by_id` is untouched. An archived fact has no successor — that
    # is what makes it archived rather than corrected — and writing one here
    # would forge a supersession chain link the member never created.
    cur.execute(
        f"""UPDATE {_schema.FACTS_TABLE}
        SET lifecycle_state = ?, updated_at = ?
        WHERE owner_user_id = ? AND id = ?""",
        (_model.LIFECYCLE_ARCHIVED, now_iso, owner, target),
    )
    _history(cur, owner_user_id=owner, fact_id=target,
             change_type=_model.CHANGE_ARCHIVED, actor_user_id=actor,
             from_state=before or "", to_state=_model.LIFECYCLE_ARCHIVED,
             note_key=note or _model.NOTE_OWNER_ACTION)
    _audit.record(
        cur, actor_user_id=actor, owner_user_id=owner,
        action=_audit.ACTION_FACT_ARCHIVE,
        object_type=str(current.get("subject_type") or ""),
        object_id=current.get("subject_id"), purpose=purpose,
        outcome=_audit.OUTCOME_OK,
    )
    return {"status": STATUS_WRITTEN, "fact_id": target, "from_state": before,
            "lifecycle_state": _model.LIFECYCLE_ARCHIVED}


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------
def _row_to_fact(row) -> dict:
    data = dict(row)
    data["provenance"] = asdict(decode_provenance_ref(data.get("provenance_ref")))
    data["freshness"] = staleness(data)
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
