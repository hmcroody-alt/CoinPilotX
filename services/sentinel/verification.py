"""Sentinel independent verification (Stage 16).

An executor can never declare its own success (SC4). A runbook execution
lands in EXECUTED_UNVERIFIED; only this module — invoked with a verifier
identity DIFFERENT from the executor — can advance it to COMPLETED, and only
if the runbook's declared verifier function passes against the recorded
result.
"""

from __future__ import annotations

import json

from services.sentinel import evidence, runbooks, store


class VerificationError(ValueError):
    pass


def verify_execution(execution_id: str, verifier_id: str, params: dict | None = None,
                     conn=None) -> dict:
    """Independently verify one runbook execution.

    Rules:
    - execution must exist and be EXECUTED_UNVERIFIED,
    - verifier_id must differ from the executor_id (independence, SC4),
    - the runbook's declared verifier must return truthy on the recorded result.
    """
    if not str(verifier_id or "").strip():
        raise VerificationError("verifier_id is required (SC12)")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT runbook, executor_id, status, result_json "
            "FROM sentinel_runbook_executions WHERE execution_id = ?", (execution_id,))
        row = cur.fetchone()
        if not row:
            raise VerificationError(f"unknown execution {execution_id!r}")
        runbook_name, executor_id, status = str(row[0]), str(row[1]), str(row[2])
        result = json.loads(row[3] or "{}")
        if verifier_id == executor_id:
            raise VerificationError(
                "verifier must be independent of executor (SC4)")
        if status != "EXECUTED_UNVERIFIED":
            raise VerificationError(f"execution is {status}, not verifiable")
        spec = runbooks.get(runbook_name)
        if spec is None:
            raise VerificationError(f"runbook {runbook_name!r} no longer registered (SC15)")
        try:
            passed = bool(spec.verifier(dict(params or {}), result))
        except Exception as exc:
            passed = False
            result["verifier_error"] = str(exc)[:300]
        new_status = "COMPLETED" if passed else "VERIFICATION_FAILED"
        cur.execute(
            "UPDATE sentinel_runbook_executions SET status=?, verified=?, verifier_id=?, "
            "verification_note=? WHERE execution_id=?",
            (new_status, 1 if passed else 0, verifier_id,
             "verified independently" if passed else "verification failed", execution_id))
        evidence.append("runbook_verified", verifier_id,
                        {"execution_id": execution_id, "runbook": runbook_name,
                         "executor_id": executor_id, "passed": passed}, conn=c)
    return {"execution_id": execution_id, "status": new_status, "passed": passed}
