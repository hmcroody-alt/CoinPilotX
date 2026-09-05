"""Private Shield — internal exposure monitoring that never fabricates safety.

What this module is
-------------------
A deterministic scan over the member's own Private Office data, surfacing
conditions that already exist in their rows: overdue obligations, facts that
contradict each other, document claims nobody has reviewed, documents whose
content nothing has been able to read, and facts whose validity window has
lapsed. Each finding carries evidence refs back to the exact rows behind it,
lives in a lifecycle the member controls (open → acknowledged → resolved /
dismissed), and deduplicates across rescans instead of multiplying.

What this module is *not*
-------------------------
* Not breach monitoring. No external breach/identity/dark-web provider is
  integrated, and this module never renders a clean external state — the
  posture read reports that coverage as PROVIDER_REQUIRED, because "no
  breaches found" when nothing has looked is a fabricated assurance. The
  feature matrix row ``private_shield.breach_monitoring`` stays
  PROVIDER_REQUIRED and the tier suite pins it there.
* Not autonomous. Scans run when the member asks. The only write this module
  performs without an explicit member decision is bookkeeping on its own
  findings: refreshing ``last_seen_at``, and resolving a finding whose
  condition is no longer detected — with a note that says exactly that.
* Not a judge. A dismissed finding stays dismissed; the scan will not
  resurrect it while the same condition persists.

Writes: this module owns ``private_shield_findings`` (registered in
``evidence.KINDS``), the same way the vault owns its document tables. All
reads of member data go through each store's own gated readers.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from services.private_office import audit
from services.private_office import contradictions as contradictions_mod
from services.private_office import documents as documents_mod
from services.private_office import evidence
from services.private_office import facts as facts_mod
from services.private_office import jobs
from services.private_office import records as records_mod

FINDINGS_TABLE = "private_shield_findings"

KIND_OVERDUE_OBLIGATION = "OVERDUE_OBLIGATION"
KIND_FACT_CONTRADICTION = "FACT_CONTRADICTION"
KIND_UNREVIEWED_CLAIMS = "UNREVIEWED_CLAIMS"
KIND_EXTRACTION_GAP = "EXTRACTION_GAP"
KIND_EXPIRED_FACT = "EXPIRED_FACT"
KINDS: tuple[str, ...] = (
    KIND_OVERDUE_OBLIGATION, KIND_FACT_CONTRADICTION, KIND_UNREVIEWED_CLAIMS,
    KIND_EXTRACTION_GAP, KIND_EXPIRED_FACT,
)

SEVERITY_HIGH = "HIGH"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_LOW = "LOW"
SEVERITIES: tuple[str, ...] = (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW)

STATUS_OPEN = "OPEN"
STATUS_ACKNOWLEDGED = "ACKNOWLEDGED"
STATUS_RESOLVED = "RESOLVED"
STATUS_DISMISSED = "DISMISSED"
STATUSES: tuple[str, ...] = (
    STATUS_OPEN, STATUS_ACKNOWLEDGED, STATUS_RESOLVED, STATUS_DISMISSED)
OPEN_STATUSES: tuple[str, ...] = (STATUS_OPEN, STATUS_ACKNOWLEDGED)
#: What a member may move a finding to. OPEN is where scans put things;
#: nothing moves back to OPEN because "unseeing" an acknowledgement has no
#: meaning, and terminal states are terminal — a recurrence is a new finding.
MEMBER_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_OPEN: (STATUS_ACKNOWLEDGED, STATUS_RESOLVED, STATUS_DISMISSED),
    STATUS_ACKNOWLEDGED: (STATUS_RESOLVED, STATUS_DISMISSED),
}

CLEARED_NOTE = "no longer detected on rescan"

MAX_FINDINGS_PER_KIND = 25
MAX_LIST = 200
MAX_TEXT_CHARS = 200

#: Truthful external coverage. Rendered on every posture read so a screen can
#: never imply outside monitoring that does not exist.
EXTERNAL_COVERAGE = {
    "breach_monitoring": {
        "state": "PROVIDER_REQUIRED",
        "monitored": False,
        "note": (
            "No breach or identity-exposure provider is integrated. Nothing "
            "external has been checked, so no external all-clear can exist."
        ),
    },
}

FINDINGS_TABLE_DDL = f"""
CREATE TABLE IF NOT EXISTS {FINDINGS_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER NOT NULL,
    finding_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    evidence_refs TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT '{STATUS_OPEN}',
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT '',
    resolution_note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
)
"""

INDEX_DDL: tuple[str, ...] = (
    f"CREATE INDEX IF NOT EXISTS idx_po_shield_owner_status "
    f"ON {FINDINGS_TABLE} (owner_user_id, status, id)",
    f"CREATE INDEX IF NOT EXISTS idx_po_shield_owner_key "
    f"ON {FINDINGS_TABLE} (owner_user_id, finding_key)",
)

_SCHEMA_READY = False


class PrivateShieldRejected(ValueError):
    """A shield request this module refuses."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clip(text: object) -> str:
    return " ".join(str(text or "").split())[:MAX_TEXT_CHARS]


def reset_shield_schema_cache() -> None:
    global _SCHEMA_READY
    _SCHEMA_READY = False


def ensure_shield_schema(cur, *, force: bool = False) -> None:
    global _SCHEMA_READY
    if _SCHEMA_READY and not force:
        return
    cur.execute(FINDINGS_TABLE_DDL)
    for ddl in INDEX_DDL:
        cur.execute(ddl)
    jobs.ensure_jobs_schema(cur)
    _SCHEMA_READY = True


# ---------------------------------------------------------------------------
# Detection — reads only, through each store's own gated readers
# ---------------------------------------------------------------------------

def _detect_overdue_obligations(cur, owner: int, now_iso: str) -> list[dict]:
    spec = records_mod.SPECS[records_mod.TYPE_OBLIGATION]
    open_statuses = [s for s in spec["statuses"] if s not in spec["closing"]]
    rows = records_mod.list_records(
        cur, record_type=records_mod.TYPE_OBLIGATION, owner_user_id=owner,
        statuses=open_statuses, limit=records_mod.MAX_LIMIT)
    out = []
    for row in rows:
        due = str(row.get("due_at") or "").strip()
        if not due or due >= now_iso:
            continue
        ref = evidence.format_ref("obligation", int(row["id"]))
        out.append({
            "finding_key": f"{KIND_OVERDUE_OBLIGATION}:{ref}",
            "kind": KIND_OVERDUE_OBLIGATION,
            "severity": SEVERITY_HIGH,
            "title": _clip(row.get("title") or "Obligation"),
            "detail": _clip(f"open and overdue since {due}"),
            "refs": [ref],
        })
    return out[:MAX_FINDINGS_PER_KIND]


def _detect_contradictions(cur, owner: int) -> list[dict]:
    conflicts = contradictions_mod.detect_conflicts(cur, owner_user_id=owner)
    out = []
    for conflict in conflicts:
        refs = [evidence.format_ref("fact", int(fid))
                for fid in conflict.get("competing_fact_ids") or []]
        out.append({
            "finding_key": f"{KIND_FACT_CONTRADICTION}:{conflict['conflict_id']}",
            "kind": KIND_FACT_CONTRADICTION,
            "severity": SEVERITY_HIGH,
            "title": _clip(f"Contradictory facts: {conflict.get('fact_type')}"),
            "detail": _clip(conflict.get("reason") or "sources disagree"),
            "refs": refs,
        })
    return out[:MAX_FINDINGS_PER_KIND]


def _detect_unreviewed_claims(cur, owner: int) -> list[dict]:
    claims = documents_mod.list_claims(
        cur, owner_user_id=owner, status=documents_mod.CLAIM_PROPOSED)
    per_doc: dict[int, int] = {}
    for claim in claims:
        doc_id = int(claim.get("document_id") or 0)
        per_doc[doc_id] = per_doc.get(doc_id, 0) + 1
    out = []
    for doc_id, count in sorted(per_doc.items()):
        ref = evidence.format_ref("document", doc_id)
        out.append({
            "finding_key": f"{KIND_UNREVIEWED_CLAIMS}:{ref}",
            "kind": KIND_UNREVIEWED_CLAIMS,
            "severity": SEVERITY_MEDIUM,
            "title": _clip(f"{count} extracted claim{'s' if count != 1 else ''} awaiting review"),
            "detail": "proposed from a document; nothing lands as fact until reviewed",
            "refs": [ref],
        })
    return out[:MAX_FINDINGS_PER_KIND]


def _detect_extraction_gaps(cur, owner: int) -> list[dict]:
    documents = documents_mod.list_documents(cur, owner_user_id=owner)
    out = []
    for doc in documents:
        state = str(doc.get("extraction_state") or "")
        if state not in (documents_mod.EXTRACTION_PROVIDER_REQUIRED,
                         documents_mod.EXTRACTION_FAILED):
            continue
        ref = evidence.format_ref("document", int(doc.get("id") or 0))
        out.append({
            "finding_key": f"{KIND_EXTRACTION_GAP}:{ref}",
            "kind": KIND_EXTRACTION_GAP,
            "severity": SEVERITY_MEDIUM,
            "title": _clip(doc.get("title") or doc.get("original_name") or "Document"),
            "detail": _clip(
                "stored but unread — extraction "
                + ("needs a provider" if state == documents_mod.EXTRACTION_PROVIDER_REQUIRED
                   else "failed")),
            "refs": [ref],
        })
    return out[:MAX_FINDINGS_PER_KIND]


def _detect_expired_facts(cur, owner: int, now_iso: str) -> list[dict]:
    rows = facts_mod.list_facts(cur, owner_user_id=owner, limit=100)
    out = []
    for row in rows:
        valid_to = str(row.get("valid_to") or "").strip()
        if not valid_to or valid_to >= now_iso:
            continue
        ref = evidence.format_ref("fact", int(row["id"]))
        out.append({
            "finding_key": f"{KIND_EXPIRED_FACT}:{ref}",
            "kind": KIND_EXPIRED_FACT,
            "severity": SEVERITY_LOW,
            "title": _clip(f"Fact past its validity window: {row.get('fact_type')}"),
            "detail": _clip(f"valid_to {valid_to} has passed and nothing newer replaced it"),
            "refs": [ref],
        })
    return out[:MAX_FINDINGS_PER_KIND]


def _detect_all(cur, owner: int) -> list[dict]:
    now_iso = _now_iso()
    detected: list[dict] = []
    detected.extend(_detect_overdue_obligations(cur, owner, now_iso))
    detected.extend(_detect_contradictions(cur, owner))
    detected.extend(_detect_unreviewed_claims(cur, owner))
    detected.extend(_detect_extraction_gaps(cur, owner))
    detected.extend(_detect_expired_facts(cur, owner, now_iso))
    return detected


# ---------------------------------------------------------------------------
# Scan — job-wrapped, deduplicating, audited
# ---------------------------------------------------------------------------

def _all_findings(cur, owner: int) -> list[dict]:
    cur.execute(
        f"SELECT * FROM {FINDINGS_TABLE} WHERE owner_user_id=? ORDER BY id DESC",
        (owner,))
    return [dict(row) for row in cur.fetchall()]


def run_scan(cur, *, owner_user_id: int,
             actor_user_id: int | None = None) -> dict[str, Any]:
    owner = int(owner_user_id or 0)
    if owner <= 0:
        raise PrivateShieldRejected("owner_user_id is required")
    ensure_shield_schema(cur)

    job_id = jobs.create_job(cur, owner_user_id=owner, job_type=jobs.JOB_SHIELD_SCAN)
    jobs.start_job(cur, owner_user_id=owner, job_id=job_id)
    try:
        detected = _detect_all(cur, owner)
    except Exception:
        jobs.fail_job(cur, owner_user_id=owner, job_id=job_id,
                      outcome_note="detection failed")
        raise

    now = _now_iso()
    existing = _all_findings(cur, owner)
    latest_by_key: dict[str, dict] = {}
    for row in existing:  # newest-first, so the first row per key wins
        latest_by_key.setdefault(str(row["finding_key"]), row)

    new = refreshed = suppressed = 0
    seen_keys: set[str] = set()
    for candidate in detected:
        key = candidate["finding_key"]
        seen_keys.add(key)
        current = latest_by_key.get(key)
        if current is not None and current["status"] in OPEN_STATUSES:
            cur.execute(
                f"""UPDATE {FINDINGS_TABLE}
                SET last_seen_at=?, detail=?, evidence_refs=?
                WHERE id=? AND owner_user_id=?""",
                (now, candidate["detail"],
                 evidence.pack_refs(evidence.normalize_refs(candidate["refs"])),
                 int(current["id"]), owner))
            refreshed += 1
            continue
        if current is not None and current["status"] == STATUS_DISMISSED:
            # The member said "stop telling me this" — honored while the same
            # condition (same key) persists.
            suppressed += 1
            continue
        cur.execute(
            f"""INSERT INTO {FINDINGS_TABLE}
            (owner_user_id, finding_key, kind, severity, title, detail,
             evidence_refs, status, first_seen_at, last_seen_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (owner, key, candidate["kind"], candidate["severity"],
             candidate["title"], candidate["detail"],
             evidence.pack_refs(evidence.normalize_refs(candidate["refs"])),
             STATUS_OPEN, now, now, now))
        new += 1

    cleared = 0
    for row in existing:
        if row["status"] in OPEN_STATUSES and str(row["finding_key"]) not in seen_keys:
            cur.execute(
                f"""UPDATE {FINDINGS_TABLE}
                SET status=?, resolved_at=?, resolution_note=?
                WHERE id=? AND owner_user_id=? AND status IN (?, ?)""",
                (STATUS_RESOLVED, now, CLEARED_NOTE, int(row["id"]), owner,
                 STATUS_OPEN, STATUS_ACKNOWLEDGED))
            cleared += 1

    jobs.finish_job(cur, owner_user_id=owner, job_id=job_id)
    audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=audit.ACTION_SHIELD_SCAN, object_type="SHIELD_SCAN",
        object_id=str(job_id), purpose="user_request",
        result_count=new + refreshed,
    )
    return {
        "job_id": job_id,
        "scanned_at": now,
        "new": new,
        "refreshed": refreshed,
        "cleared": cleared,
        "suppressed": suppressed,
        "open_findings": list_findings(
            cur, owner_user_id=owner, statuses=list(OPEN_STATUSES)),
    }


# ---------------------------------------------------------------------------
# Findings lifecycle
# ---------------------------------------------------------------------------

def _project(row) -> dict[str, Any]:
    data = dict(row)
    finding_id = int(data.get("id") or 0)
    return {
        "id": finding_id,
        "ref": evidence.format_ref("finding", finding_id),
        "kind": data.get("kind") or "",
        "severity": data.get("severity") or "",
        "title": data.get("title") or "",
        "detail": data.get("detail") or "",
        "status": data.get("status") or "",
        "first_seen_at": data.get("first_seen_at") or "",
        "last_seen_at": data.get("last_seen_at") or "",
        "resolved_at": data.get("resolved_at") or "",
        "resolution_note": data.get("resolution_note") or "",
        "evidence": evidence.unpack_refs(data.get("evidence_refs")),
    }


def list_findings(cur, *, owner_user_id: int, statuses: list[str] | None = None,
                  limit: int = MAX_LIST) -> list[dict[str, Any]]:
    ensure_shield_schema(cur)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return []
    wanted = [s for s in (statuses or []) if s in STATUSES]
    bounded = max(1, min(int(limit or MAX_LIST), MAX_LIST))
    if wanted:
        marks = ",".join("?" for _ in wanted)
        cur.execute(
            f"""SELECT * FROM {FINDINGS_TABLE}
            WHERE owner_user_id=? AND status IN ({marks})
            ORDER BY id DESC LIMIT ?""",
            (owner, *wanted, bounded))
    else:
        cur.execute(
            f"""SELECT * FROM {FINDINGS_TABLE}
            WHERE owner_user_id=? ORDER BY id DESC LIMIT ?""",
            (owner, bounded))
    return [_project(row) for row in cur.fetchall()]


def get_finding(cur, *, owner_user_id: int, finding_id: int) -> dict[str, Any] | None:
    ensure_shield_schema(cur)
    owner = int(owner_user_id or 0)
    if owner <= 0:
        return None
    cur.execute(
        f"SELECT * FROM {FINDINGS_TABLE} WHERE id=? AND owner_user_id=?",
        (int(finding_id or 0), owner))
    row = cur.fetchone()
    return None if row is None else _project(row)


def update_finding(cur, *, owner_user_id: int, finding_id: int, status: str,
                   note: object = "",
                   actor_user_id: int | None = None) -> dict[str, Any] | None:
    """A member decision about one finding. The only path that moves status."""
    ensure_shield_schema(cur)
    owner = int(owner_user_id or 0)
    current = get_finding(cur, owner_user_id=owner, finding_id=finding_id)
    if current is None:
        return None
    wanted = str(status or "").strip().upper()
    allowed = MEMBER_TRANSITIONS.get(current["status"], ())
    if wanted not in allowed:
        if not allowed:
            raise PrivateShieldRejected(
                f"a {current['status']} finding is final; a recurrence opens a new one")
        raise PrivateShieldRejected(
            f"status must be one of: {', '.join(allowed)}")
    now = _now_iso()
    resolved_at = now if wanted in (STATUS_RESOLVED, STATUS_DISMISSED) else ""
    cur.execute(
        f"""UPDATE {FINDINGS_TABLE}
        SET status=?, resolved_at=?, resolution_note=?
        WHERE id=? AND owner_user_id=?""",
        (wanted, resolved_at, _clip(note), int(finding_id), owner))
    audit.record(
        cur, actor_user_id=int(actor_user_id or owner), owner_user_id=owner,
        action=audit.ACTION_SHIELD_FINDING_UPDATE, object_type="FINDING",
        object_id=str(int(finding_id)), purpose="user_request", result_count=1,
    )
    return get_finding(cur, owner_user_id=owner, finding_id=finding_id)


# ---------------------------------------------------------------------------
# Posture — what is true right now, internal and external
# ---------------------------------------------------------------------------

def posture(cur, *, owner_user_id: int) -> dict[str, Any]:
    ensure_shield_schema(cur)
    owner = int(owner_user_id or 0)
    open_rows = list_findings(
        cur, owner_user_id=owner, statuses=list(OPEN_STATUSES))
    by_severity = {severity: 0 for severity in SEVERITIES}
    for row in open_rows:
        if row["severity"] in by_severity:
            by_severity[row["severity"]] += 1
    scans = jobs.list_jobs(cur, owner_user_id=owner, job_type=jobs.JOB_SHIELD_SCAN)
    last = scans[0] if scans else None
    return {
        "open_findings": len(open_rows),
        "by_severity": by_severity,
        "last_scan": last,
        "checks": list(KINDS),
        "external": EXTERNAL_COVERAGE,
    }
