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
    _model.PROVENANCE_USER_ASSERTED: 180,
    _model.PROVENANCE_INFERRED: 30,
    _model.PROVENANCE_ESTIMATED: 14,
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
    actor_user_id: int | None = None,
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
        return {"status": STATUS_REFRESHED, "fact_id": row_id, "fact_key": key,
                "sensitivity": resolved_sensitivity, "domain": resolved_domain}

    cur.execute(
        f"""INSERT INTO {_schema.FACTS_TABLE}
        (owner_user_id, fact_key, subject_type, subject_id, fact_type,
         value_type, typed_value, value_number, provenance_type, provenance_ref,
         confidence, observed_at, valid_from, valid_to, sensitivity, domain,
         lifecycle_state, conflict_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
        (
            owner, key, subject_kind, subject, kind, resolved_value_type,
            typed_value, value_number, source, ref, score, observed_iso,
            from_iso, to_iso, resolved_sensitivity, resolved_domain,
            _model.LIFECYCLE_ACTIVE, now_iso, now_iso,
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
    return {"status": STATUS_WRITTEN, "fact_id": fact_id, "fact_key": key,
            "sensitivity": resolved_sensitivity, "domain": resolved_domain}


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
    cur.execute(
        f"UPDATE {_schema.FACTS_TABLE} "
        f"SET lifecycle_state = ?, valid_to = COALESCE(valid_to, ?), updated_at = ? "
        f"WHERE owner_user_id = ? AND subject_type = ? AND subject_id = ? "
        f"AND fact_type = ? AND lifecycle_state = ? AND id != ?",
        (_model.LIFECYCLE_SUPERSEDED, now_iso, now_iso, owner, subject_kind,
         subject, kind[:MAX_FACT_TYPE], _model.LIFECYCLE_ACTIVE,
         int(keep_fact_id or 0)),
    )
    changed = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
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
