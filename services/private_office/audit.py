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
ACTION_FACT_SUPERSEDE = "PRIVATE_FACT_SUPERSEDE"
ACTION_FACT_READ = "PRIVATE_FACT_READ"
# Ledger-core vocabulary — one verb per durable fact operation rather than a
# single PRIVATE_FACT_UPDATE with a free-text discriminator. `action` is queried
# by equality, so "show me everything the owner confirmed" and "show me
# everything that was revoked" have to be filters, not string parsing over a
# details column. Confirm and dispute in particular are the two halves of the
# review loop and collapsing them would make the loop unmeasurable.
ACTION_FACT_CONFIRM = "PRIVATE_FACT_CONFIRM"
ACTION_FACT_REVISE = "PRIVATE_FACT_REVISE"
ACTION_FACT_DISPUTE = "PRIVATE_FACT_DISPUTE"
ACTION_FACT_ARCHIVE = "PRIVATE_FACT_ARCHIVE"
#: Withdrawal of the belief itself, not merely filing it away. Separate from
#: ARCHIVE because archive means "no longer shown" and revoke means "was never
#: safe to rely on" — the second is a signal about the *source*, and a source
#: whose facts keep getting revoked is a finding.
ACTION_FACT_REVOKE = "PRIVATE_FACT_REVOKE"
#: The system retired a fact because its validity window closed. Distinct from
#: every other verb here in that no human did it; folding it into ARCHIVE would
#: make the sweep indistinguishable from owner action.
ACTION_FACT_EXPIRE = "PRIVATE_FACT_EXPIRE"
ACTION_FACT_HISTORY_READ = "PRIVATE_FACT_HISTORY_READ"
#: Evidence was attached to a fact, or taken away from it. Two verbs rather than
#: one with a direction field, because the questions they answer are asked
#: separately and the second one is the alarming one: attachments accumulating
#: is a store getting better sourced, detachments accumulating is somebody
#: dismantling the support under facts that are still being quoted. Detachment
#: also demotes the fact, so this is the audit record of a *downgrade* — and a
#: downgrade with no verb of its own is a downgrade nobody can query for.
ACTION_FACT_EVIDENCE_ATTACH = "PRIVATE_FACT_EVIDENCE_ATTACH"
ACTION_FACT_EVIDENCE_DETACH = "PRIVATE_FACT_EVIDENCE_DETACH"
#: A fact was put in front of the member for review. Almost always the staleness
#: sweep rather than a person, which is precisely why it is not folded into
#: ACTION_FACT_REVISE: a review queue that grows because the system flagged
#: things and a review queue that grows because the member is flagging things by
#: hand are opposite health signals wearing the same number.
ACTION_FACT_REVIEW_FLAG = "PRIVATE_FACT_REVIEW_FLAG"
#: A detected conflict was settled. Pairs with ACTION_CONFLICT_DETECTED below —
#: detection without resolution is an open question, and only having the first
#: verb makes the backlog impossible to measure.
ACTION_CONFLICT_RESOLVED = "PRIVATE_CONFLICT_RESOLVED"
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
#: A closed record was deliberately returned to its working state. Separate from
#: ``PRIVATE_RECORD_UPDATE`` because it is the one status move that discards a
#: closure stamp: the ``resolved_at`` on an obligation, the ``completed_at`` on a
#: request. "Which of my closed matters were reopened, by whom, and when" has to
#: be an equality filter over one action, not a reconstruction from update rows
#: that do not carry the old status.
ACTION_RECORD_REOPEN = "PRIVATE_RECORD_REOPEN"
#: A status move the type's transition contract forbids. Written on the refusal
#: path, with ``OUTCOME_DENIED``, because a rejected transition is a fact about
#: how the record was *nearly* changed and it is the only trace of an attempt
#: that left no row behind. A store that logs only successful writes cannot tell
#: a quiet system from one where something is repeatedly trying to force a
#: resolved obligation back open.
ACTION_RECORD_TRANSITION_DENIED = "PRIVATE_RECORD_TRANSITION_DENIED"
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
# Concierge reads get their own verb because they are the one place in the
# Office where actor and owner routinely differ: an operator reading a
# member's request queue must be findable as exactly that.
ACTION_CONCIERGE_READ = "PRIVATE_CONCIERGE_READ"

# Private Meetings vocabulary — mission §48's list, verbatim, plus the few
# verbs the lifecycle needs to be queryable (LEAVE, CANCEL, READ). Same
# metadata-only discipline: `object_id` is `MEETING:<id>`, never a title, a
# code, or a channel name — a meeting code in an audit row is a join link.
ACTION_MEETING_CREATE = "PRIVATE_MEETING_CREATE"
ACTION_MEETING_START = "PRIVATE_MEETING_START"
ACTION_MEETING_JOIN = "PRIVATE_MEETING_JOIN"
ACTION_MEETING_ADMIT = "PRIVATE_MEETING_ADMIT"
ACTION_MEETING_DENY = "PRIVATE_MEETING_DENY"
ACTION_MEETING_REMOVE = "PRIVATE_MEETING_REMOVE"
ACTION_MEETING_LOCK = "PRIVATE_MEETING_LOCK"
ACTION_MEETING_UNLOCK = "PRIVATE_MEETING_UNLOCK"
ACTION_MEETING_ROLE_CHANGE = "PRIVATE_MEETING_ROLE_CHANGE"
ACTION_MEETING_RECORDING_START = "PRIVATE_MEETING_RECORDING_START"
ACTION_MEETING_RECORDING_STOP = "PRIVATE_MEETING_RECORDING_STOP"
ACTION_MEETING_END = "PRIVATE_MEETING_END"
ACTION_MEETING_CANCEL = "PRIVATE_MEETING_CANCEL"
ACTION_MEETING_LEAVE = "PRIVATE_MEETING_LEAVE"
ACTION_MEETING_INVITE = "PRIVATE_MEETING_INVITE"
ACTION_MEETING_MESSAGE = "PRIVATE_MEETING_MESSAGE"
ACTION_MEETING_READ = "PRIVATE_MEETING_READ"
ACTION_MEETING_CODE_ROTATED = "PRIVATE_MEETING_CODE_ROTATED"
ACTION_MEETING_ARTIFACT_SAVE = "PRIVATE_MEETING_ARTIFACT_SAVE"

# Private Conversations vocabulary. The classification layer writes no message
# rows, so there is deliberately no PRIVATE_CONVERSATION_MESSAGE action here —
# message activity belongs to the canonical messaging system, and a second copy
# in this log would be a second, diverging history of the same thread. What this
# package owns is the classification and the cross-domain links, which is what
# these five verbs cover. `object_id` is `CONVERSATION:<id>`; a title or a
# member's name would put conversation content into a metadata-only log.
ACTION_CONVERSATION_CLASSIFY = "PRIVATE_CONVERSATION_CLASSIFY"
ACTION_CONVERSATION_SENSITIVITY_CHANGE = "PRIVATE_CONVERSATION_SENSITIVITY_CHANGE"
ACTION_CONVERSATION_LINK = "PRIVATE_CONVERSATION_LINK"
ACTION_CONVERSATION_UNLINK = "PRIVATE_CONVERSATION_UNLINK"
ACTION_CONVERSATION_READ = "PRIVATE_CONVERSATION_READ"

ACTIONS: tuple[str, ...] = (
    ACTION_FACT_CREATE,
    ACTION_FACT_SUPERSEDE,
    ACTION_FACT_READ,
    ACTION_FACT_CONFIRM,
    ACTION_FACT_REVISE,
    ACTION_FACT_DISPUTE,
    ACTION_FACT_ARCHIVE,
    ACTION_FACT_REVOKE,
    ACTION_FACT_EXPIRE,
    ACTION_FACT_HISTORY_READ,
    ACTION_FACT_EVIDENCE_ATTACH,
    ACTION_FACT_EVIDENCE_DETACH,
    ACTION_FACT_REVIEW_FLAG,
    ACTION_CONFLICT_RESOLVED,
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
    ACTION_RECORD_REOPEN,
    ACTION_RECORD_TRANSITION_DENIED,
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
    ACTION_CONCIERGE_READ,
    ACTION_MEETING_CREATE,
    ACTION_MEETING_START,
    ACTION_MEETING_JOIN,
    ACTION_MEETING_ADMIT,
    ACTION_MEETING_DENY,
    ACTION_MEETING_REMOVE,
    ACTION_MEETING_LOCK,
    ACTION_MEETING_UNLOCK,
    ACTION_MEETING_ROLE_CHANGE,
    ACTION_MEETING_RECORDING_START,
    ACTION_MEETING_RECORDING_STOP,
    ACTION_MEETING_END,
    ACTION_MEETING_CANCEL,
    ACTION_MEETING_LEAVE,
    ACTION_MEETING_INVITE,
    ACTION_MEETING_MESSAGE,
    ACTION_MEETING_READ,
    ACTION_MEETING_CODE_ROTATED,
    ACTION_MEETING_ARTIFACT_SAVE,
    ACTION_CONVERSATION_CLASSIFY,
    ACTION_CONVERSATION_SENSITIVITY_CHANGE,
    ACTION_CONVERSATION_LINK,
    ACTION_CONVERSATION_UNLINK,
    ACTION_CONVERSATION_READ,
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


#: The write actions over the six record primitives, in the order a reader would
#: want them explained. ``READ`` and ``FIELD_REVEAL`` are excluded on purpose: an
#: activity feed is what happened *to* the member's affairs, and folding in every
#: time something was looked at would bury six real changes under six hundred
#: views. Who looked is a different question with a different surface.
RECORD_ACTIVITY_ACTIONS: tuple[str, ...] = (
    ACTION_RECORD_CREATE,
    ACTION_RECORD_UPDATE,
    ACTION_RECORD_REVISE,
    ACTION_RECORD_REOPEN,
)

MAX_ACTIVITY_ROWS = 200


def recent_record_activity(
    cur,
    *,
    owner_user_id: int,
    limit: int = 25,
    actions: tuple[str, ...] = RECORD_ACTIVITY_ACTIONS,
) -> list[dict]:
    """What has actually happened to this owner's records, newest first.

    Read from the audit trail rather than reconstructed from the records
    themselves. The difference is not cosmetic: a current row carries
    ``updated_at``, which says a change happened but not what kind, and it
    carries nothing at all about a change that was later superseded. Ordering
    rows by ``updated_at`` produces a feed that calls every event "updated" and
    silently drops the history that made the record interesting.

    What this deliberately does *not* claim: which status an update moved the
    record *to*. The audit table stores metadata only — no detail column, by
    design — so the row records that a status changed, not that it changed to
    COMPLETED. Inferring the target from the record's present status would be
    exactly the reconstruction this function exists to avoid, and would relabel
    every historical change with today's outcome.

    Owner-scoped, bounded, and never raises: a failure to read the trail returns
    an empty list, because activity is context beside the numbers rather than
    one of them. The caller distinguishes "no activity" from "counts failed" on
    the counts, which do raise.
    """
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    wanted = tuple(a for a in actions if a in ACTIONS)
    if not wanted:
        return []
    bounded = max(1, min(int(limit or 25), MAX_ACTIVITY_ROWS))
    try:
        cur.execute(
            f"SELECT action, object_type, object_id, actor_user_id, outcome, "
            f"created_at FROM {_schema.AUDIT_TABLE} "
            f"WHERE owner_user_id = ? AND action IN "
            f"({', '.join('?' for _ in wanted)}) "
            f"ORDER BY id DESC LIMIT {bounded}",
            tuple([owner] + list(wanted)),
        )
        rows = cur.fetchall() or []
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("PRIVATE_AUDIT_ACTIVITY_READ_FAILED error=%s", exc)
        return []

    out: list[dict] = []
    for row in rows:
        data = dict(row) if hasattr(row, "keys") else {
            "action": row[0], "object_type": row[1], "object_id": row[2],
            "actor_user_id": row[3], "outcome": row[4], "created_at": row[5],
        }
        out.append({
            "action": str(data.get("action") or ""),
            "record_type": str(data.get("object_type") or ""),
            "record_id": str(data.get("object_id") or ""),
            # Who, as self-or-other. The actor id itself is not returned: the
            # owner already knows their own id, and a provider's id is somebody
            # else's identifier travelling on the member's wire.
            "by_owner": int(data.get("actor_user_id") or 0) == owner,
            "outcome": str(data.get("outcome") or ""),
            "at": str(data.get("created_at") or ""),
        })
    return out
