"""Private Office jobs — a metadata-only record of background work.

Document extraction, shield scans and briefing generation all share a shape:
the member (or a schedule) asks for work, the work runs, and the member must
later be able to see that it ran, when, and whether it succeeded. This module
is that record. It is *bookkeeping*, not a queue: the Procfile gains no new
worker for Private Office, so jobs here are executed eagerly by the code path
that created them, and the row exists so the execution is observable and
auditable — not so something else can pick it up.

The same identity-not-content rule as the audit table applies. A job carries a
type, a subject *ref* (the evidence vocabulary), and a short outcome note.
There is no payload column and no result blob: results land in the feature's
own tables (claims, findings, briefings), where they are gated like everything
else, and the job row just points at them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.private_office import evidence

JOBS_TABLE = "private_office_jobs"

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
STATUSES: tuple[str, ...] = (STATUS_QUEUED, STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED)

#: Closed job vocabulary, same reasoning as audit.ACTIONS: "show me every
#: extraction that failed" only works if every extraction spells its type the
#: same way.
JOB_DOCUMENT_EXTRACTION = "document_extraction"
JOB_SHIELD_SCAN = "shield_scan"
JOB_BRIEFING_GENERATION = "briefing_generation"
JOB_TYPES: tuple[str, ...] = (
    JOB_DOCUMENT_EXTRACTION,
    JOB_SHIELD_SCAN,
    JOB_BRIEFING_GENERATION,
)

JOBS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {JOBS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT '{STATUS_QUEUED}',
    subject_ref TEXT NOT NULL DEFAULT '',
    result_ref TEXT NOT NULL DEFAULT '',
    outcome_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT
)
"""

JOBS_INDEX_DDL = (
    f"CREATE INDEX IF NOT EXISTS idx_po_jobs_owner_type "
    f"ON {JOBS_TABLE} (owner_user_id, job_type, created_at)",
)


class PrivateJobRejected(ValueError):
    """A job request this module refuses to record."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_jobs_schema(cur) -> None:
    cur.execute(JOBS_TABLE_DDL)
    for ddl in JOBS_INDEX_DDL:
        cur.execute(ddl)


def _clean_note(note: object) -> str:
    """Outcome notes are short, single-line status text — never content.

    Truncation at 200 keeps a stack trace or an extracted paragraph from
    riding in on the error path, which is exactly how content columns are
    born.
    """
    return " ".join(str(note or "").split())[:200]


def _clean_ref(ref: object) -> str:
    parsed = evidence.parse_ref(ref)
    return f"{parsed[0]}:{parsed[1]}" if parsed else ""


def create_job(cur, *, owner_user_id: int, job_type: str, subject_ref: str = "") -> int:
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateJobRejected("owner_user_id is required")
    if job_type not in JOB_TYPES:
        raise PrivateJobRejected(f"unknown job_type: {job_type!r}")
    cur.execute(
        f"""INSERT INTO {JOBS_TABLE}
        (owner_user_id, job_type, status, subject_ref, created_at)
        VALUES (?, ?, ?, ?, ?)""",
        (owner, job_type, STATUS_QUEUED, _clean_ref(subject_ref), _now_iso()),
    )
    row_id = getattr(cur, "lastrowid", None)
    if row_id:
        return int(row_id)
    cur.execute(
        f"SELECT id FROM {JOBS_TABLE} WHERE owner_user_id=? ORDER BY id DESC LIMIT 1",
        (owner,),
    )
    row = cur.fetchone()
    return int(row["id"] if isinstance(row, dict) else row[0])


def start_job(cur, *, owner_user_id: int, job_id: int) -> bool:
    cur.execute(
        f"""UPDATE {JOBS_TABLE} SET status=?, started_at=?
        WHERE id=? AND owner_user_id=? AND status=?""",
        (STATUS_RUNNING, _now_iso(), int(job_id or 0), int(owner_user_id or 0), STATUS_QUEUED),
    )
    return bool(getattr(cur, "rowcount", 0))


def finish_job(cur, *, owner_user_id: int, job_id: int, result_ref: str = "",
               outcome_note: str = "") -> bool:
    cur.execute(
        f"""UPDATE {JOBS_TABLE} SET status=?, finished_at=?, result_ref=?, outcome_note=?
        WHERE id=? AND owner_user_id=? AND status IN (?, ?)""",
        (STATUS_SUCCEEDED, _now_iso(), _clean_ref(result_ref), _clean_note(outcome_note),
         int(job_id or 0), int(owner_user_id or 0), STATUS_QUEUED, STATUS_RUNNING),
    )
    return bool(getattr(cur, "rowcount", 0))


def fail_job(cur, *, owner_user_id: int, job_id: int, outcome_note: str = "") -> bool:
    cur.execute(
        f"""UPDATE {JOBS_TABLE} SET status=?, finished_at=?, outcome_note=?
        WHERE id=? AND owner_user_id=? AND status IN (?, ?)""",
        (STATUS_FAILED, _now_iso(), _clean_note(outcome_note),
         int(job_id or 0), int(owner_user_id or 0), STATUS_QUEUED, STATUS_RUNNING),
    )
    return bool(getattr(cur, "rowcount", 0))


def _project(row) -> dict[str, Any]:
    data = dict(row) if not isinstance(row, dict) else row
    return {
        "id": int(data.get("id") or 0),
        "job_type": data.get("job_type") or "",
        "status": data.get("status") or "",
        "subject_ref": data.get("subject_ref") or "",
        "result_ref": data.get("result_ref") or "",
        "outcome_note": data.get("outcome_note") or "",
        "created_at": data.get("created_at") or "",
        "started_at": data.get("started_at"),
        "finished_at": data.get("finished_at"),
    }


def get_job(cur, *, owner_user_id: int, job_id: int) -> dict[str, Any] | None:
    cur.execute(
        f"SELECT * FROM {JOBS_TABLE} WHERE id=? AND owner_user_id=?",
        (int(job_id or 0), int(owner_user_id or 0)),
    )
    row = cur.fetchone()
    return _project(row) if row is not None else None


def list_jobs(cur, *, owner_user_id: int, job_type: str = "",
              limit: int = 20) -> list[dict[str, Any]]:
    owner = int(owner_user_id or 0)
    bounded = max(1, min(int(limit or 20), 100))
    if job_type:
        if job_type not in JOB_TYPES:
            return []
        cur.execute(
            f"""SELECT * FROM {JOBS_TABLE} WHERE owner_user_id=? AND job_type=?
            ORDER BY id DESC LIMIT ?""",
            (owner, job_type, bounded),
        )
    else:
        cur.execute(
            f"SELECT * FROM {JOBS_TABLE} WHERE owner_user_id=? ORDER BY id DESC LIMIT ?",
            (owner, bounded),
        )
    return [_project(row) for row in cur.fetchall()]
