"""Stage 18 — the private audit trail, which records access and never content.

The distinction this module exists to hold
------------------------------------------
An audit log for a private fact store is in an awkward position: it must be
detailed enough to answer "who read my insurance policy, and why", and it must
not itself become the easiest place to steal an insurance policy from. Those
pull in opposite directions, and the usual compromise — a ``detail_json``
column "for context" — resolves them in the wrong direction every time, because
the field starts as ``{"reason": "undx_context"}`` and ends as
``{"policy_number": "…", "premium": 4200}`` six months later without anyone
deciding that should happen.

Stage 18 draws the line at **object identity, never object content**:

    good   actor=1 action=PRIVATE_FACT_READ object_type=INSURANCE_POLICY
           object_id=382 purpose=undx_context
    bad    policy_number=…  medical_data=…  net_worth=…

That line is enforced structurally rather than by convention. The table has no
free-text column and no JSON column — see ``schema.AUDIT_TABLE_DDL`` — so a
reviewer does not have to audit every call site to know values are not being
logged. There is nowhere to put them. This module's writers accept only the
fields the table has, and :func:`safe_object_id` refuses anything that is not a
short identifier, so a caller who passes a value where an id belongs gets a
rejected field rather than a stored secret.

Why writes are best-effort
--------------------------
:func:`record` swallows its own failures. That is the opposite of the usual
advice for audit logs and it is deliberate here: the alternative is that an
audit table which is full, locked, or missing takes down the read it was
supposed to observe. A denied-access decision that cannot be logged is still a
denied-access decision, and a member losing access to their own data because
the logger broke is a worse outcome than a gap in the log — which is itself
visible, because the failure is logged to the application logger under
``PRIVATE_AUDIT_WRITE_FAILED``.

Note the asymmetry that keeps this honest: nothing here can *grant* anything.
The audit path is downstream of every decision, so a failure in it can only
lose a record, never widen access.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from services.private_office import schema as _schema

LOGGER = logging.getLogger("private_office.audit")

# Stage 18 vocabulary. A closed set, because an audit log whose action names are
# free text cannot be queried — "show me every denied access" only works if
# every denial spells the action the same way.
ACTION_FACT_CREATE = "PRIVATE_FACT_CREATE"
ACTION_FACT_READ = "PRIVATE_FACT_READ"
ACTION_GRAPH_WRITE = "PRIVATE_GRAPH_WRITE"
ACTION_GRAPH_READ = "PRIVATE_GRAPH_READ"
ACTION_CONTEXT_RETRIEVED = "PRIVATE_CONTEXT_RETRIEVED"
ACTION_ACCESS_DENIED = "PRIVATE_ACCESS_DENIED"
ACTION_CONFLICT_DETECTED = "PRIVATE_CONFLICT_DETECTED"

# Second-lock vocabulary. Same rule as above — a closed set, and structurally
# metadata-only: there is no column a passcode, hash, or grant token could be
# written into, and these writers are never handed one.
ACTION_OFFICE_PASSCODE_CREATED = "OFFICE_PASSCODE_CREATED"
ACTION_OFFICE_UNLOCKED = "OFFICE_UNLOCKED"
ACTION_OFFICE_UNLOCK_FAILED = "OFFICE_UNLOCK_FAILED"
ACTION_OFFICE_LOCKED = "OFFICE_LOCKED"
ACTION_OFFICE_PASSCODE_CHANGED = "OFFICE_PASSCODE_CHANGED"
ACTION_OFFICE_PASSCODE_RESET = "OFFICE_PASSCODE_RESET"
ACTION_OFFICE_BIOMETRIC_ENABLED = "OFFICE_BIOMETRIC_ENABLED"
ACTION_OFFICE_BIOMETRIC_DISABLED = "OFFICE_BIOMETRIC_DISABLED"

# Batch C vocabulary — the six record primitives. One set of action names for
# all six rather than six sets, with the primitive carried in `object_type`
# (OBLIGATION, DOMAIN_EVENT, DECISION, REQUEST, RISK, OPPORTUNITY). Twenty-four
# action names would make "show me every write to this member's private office"
# a query somebody has to keep up to date, and the seventh primitive would be
# the one they forgot.
ACTION_RECORD_CREATE = "PRIVATE_RECORD_CREATE"
ACTION_RECORD_UPDATE = "PRIVATE_RECORD_UPDATE"
ACTION_RECORD_REVISE = "PRIVATE_RECORD_REVISE"
ACTION_RECORD_READ = "PRIVATE_RECORD_READ"
#: A masked field's real value was handed to somebody. This is the single most
#: consequential row this table holds — every other record action moves metadata
#: around, and this one is the moment a passport number left storage — so it is
#: its own action rather than a ``PRIVATE_RECORD_READ`` with a different purpose
#: string. "Show me every reveal" must be an equality filter, not a heuristic.
ACTION_RECORD_FIELD_REVEAL = "PRIVATE_RECORD_FIELD_REVEAL"

# Capability-completion vocabulary — documents, briefings, shield, concierge.
# Same shape as Batch C: one small set of verbs per capability with the
# specifics carried in `object_type`/`object_id`, not a verb per screen.
# CLAIM_REVIEWED covers accept and reject alike (the outcome column holds
# which); a review is one act with two outcomes, not two acts.
ACTION_DOCUMENT_CREATE = "PRIVATE_DOCUMENT_CREATE"
ACTION_DOCUMENT_READ = "PRIVATE_DOCUMENT_READ"
ACTION_DOCUMENT_DELETE = "PRIVATE_DOCUMENT_DELETE"
ACTION_CLAIM_REVIEWED = "PRIVATE_DOCUMENT_CLAIM_REVIEWED"
ACTION_BRIEFING_GENERATED = "PRIVATE_BRIEFING_GENERATED"
ACTION_BRIEFING_READ = "PRIVATE_BRIEFING_READ"
ACTION_SHIELD_SCAN = "PRIVATE_SHIELD_SCAN"
ACTION_SHIELD_READ = "PRIVATE_SHIELD_READ"
ACTION_SHIELD_FINDING_UPDATE = "PRIVATE_SHIELD_FINDING_UPDATE"
ACTION_CONCIERGE_MESSAGE = "PRIVATE_CONCIERGE_MESSAGE"

ACTIONS: tuple[str, ...] = (
    ACTION_FACT_CREATE,
    ACTION_FACT_READ,
    ACTION_GRAPH_WRITE,
    ACTION_GRAPH_READ,
    ACTION_CONTEXT_RETRIEVED,
    ACTION_ACCESS_DENIED,
    ACTION_CONFLICT_DETECTED,
    ACTION_OFFICE_PASSCODE_CREATED,
    ACTION_OFFICE_UNLOCKED,
    ACTION_OFFICE_UNLOCK_FAILED,
    ACTION_OFFICE_LOCKED,
    ACTION_OFFICE_PASSCODE_CHANGED,
    ACTION_OFFICE_PASSCODE_RESET,
    ACTION_OFFICE_BIOMETRIC_ENABLED,
    ACTION_OFFICE_BIOMETRIC_DISABLED,
    ACTION_RECORD_CREATE,
    ACTION_RECORD_UPDATE,
    ACTION_RECORD_REVISE,
    ACTION_RECORD_READ,
    ACTION_RECORD_FIELD_REVEAL,
    ACTION_DOCUMENT_CREATE,
    ACTION_DOCUMENT_READ,
    ACTION_DOCUMENT_DELETE,
    ACTION_CLAIM_REVIEWED,
    ACTION_BRIEFING_GENERATED,
    ACTION_BRIEFING_READ,
    ACTION_SHIELD_SCAN,
    ACTION_SHIELD_READ,
    ACTION_SHIELD_FINDING_UPDATE,
    ACTION_CONCIERGE_MESSAGE,
)

OUTCOME_OK = "ok"
OUTCOME_DENIED = "denied"
OUTCOME_ERROR = "error"

#: An object id is an identifier: digits, or a short type-qualified handle like
#: ``INSURANCE_POLICY:382``. Anything with a space, a currency symbol, an @, or
#: more than 64 characters is a *value* wearing an id's clothes, and is dropped.
#: This is the structural half of the "identity not content" rule — the schema
#: removes the obvious places to put a secret, this removes the sneaky one.
_SAFE_OBJECT_ID = re.compile(r"^[A-Za-z0-9_:.\-]{1,64}$")

#: Purposes are a closed-ish vocabulary for the same reason actions are. Unknown
#: purposes are stored as ``other`` rather than rejected: losing the row would
#: be worse than losing the label, and the label is not load-bearing.
PURPOSES: frozenset[str] = frozenset(
    {
        "undx_context",
        "user_request",
        "briefing_candidate",
        "owner_export",
        "admin_review",
        "system_maintenance",
        "document_processing",
        "shield_monitoring",
        "concierge_service",
        "other",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_object_id(value: object) -> str:
    """An id-shaped string, or ``""``.

    Returns empty rather than a truncated version of the input: a truncated
    secret is still a secret, and half a policy number in an audit log is worse
    than no policy number because it looks like it was sanitised.
    """
    text = str(value if value is not None else "").strip()
    return text if _SAFE_OBJECT_ID.match(text) else ""


def normalize_purpose(value: object) -> str:
    text = str(value or "").strip().lower()
    return text if text in PURPOSES else "other"


def record(
    cur,
    *,
    actor_user_id: int,
    owner_user_id: int,
    action: str,
    object_type: str = "",
    object_id: object = "",
    purpose: object = "",
    outcome: str = OUTCOME_OK,
    result_count: int = 0,
) -> bool:
    """Write one metadata-only audit row. Returns whether it landed.

    ``actor_user_id`` and ``owner_user_id`` are both recorded and are both
    needed. They are usually equal — a member reading their own data — and the
    rows where they are not are precisely the ones worth being able to find.
    """
    if action not in ACTIONS:
        # A misspelled action is a row nobody will ever find again, which is the
        # same as no row plus a false sense of coverage.
        LOGGER.warning("PRIVATE_AUDIT_UNKNOWN_ACTION action=%s", str(action)[:64])
        return False
    try:
        cur.execute(
            f"""INSERT INTO {_schema.AUDIT_TABLE}
            (actor_user_id, owner_user_id, action, object_type, object_id,
             purpose, outcome, result_count, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(actor_user_id or 0),
                int(owner_user_id or 0),
                action,
                str(object_type or "")[:64],
                safe_object_id(object_id),
                normalize_purpose(purpose),
                str(outcome or OUTCOME_OK)[:32],
                max(0, int(result_count or 0)),
                _now_iso(),
            ),
        )
        return True
    except Exception as exc:
        # Best-effort by design — see the module docstring. The gap is visible
        # here rather than being silent, and it can never widen access.
        LOGGER.warning("PRIVATE_AUDIT_WRITE_FAILED action=%s error=%s", action, exc)
        return False


def record_denied(
    cur,
    *,
    actor_user_id: int,
    owner_user_id: int,
    object_type: str = "",
    object_id: object = "",
    purpose: object = "",
) -> bool:
    """Record a refusal.

    Deliberately its own function. A denial is the most important row in this
    table — it is the one that shows an attempt — and giving it a name means a
    call site cannot record one by passing ``outcome="denied"`` to a function
    whose default is ``"ok"`` and getting the default wrong.

    Note that the refused *target* is recorded by type and id only. Stage 14
    requires that existence not leak to the caller; it does not require that the
    platform forget what was asked for, and an isolation gate you cannot audit
    is one you cannot prove.
    """
    return record(
        cur,
        actor_user_id=actor_user_id,
        owner_user_id=owner_user_id,
        action=ACTION_ACCESS_DENIED,
        object_type=object_type,
        object_id=object_id,
        purpose=purpose,
        outcome=OUTCOME_DENIED,
    )
