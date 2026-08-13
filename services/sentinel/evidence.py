"""Sentinel tamper-evident evidence (Stage 17).

Append-only hash chain: each record's hash covers its canonical body plus the
previous record's hash, so any retroactive edit or deletion breaks every
subsequent link (SC5 — never alter or hide security evidence). Bodies are
redacted before hashing so secrets never enter the chain (SC9).

There is deliberately no update or delete function in this module.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from services.sentinel import classification, store
from services.sentinel.constitution import CONSTITUTION_VERSION

GENESIS_HASH = "0" * 64


def _canonical(body: dict) -> str:
    return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)


def _hash(prev_hash: str, seq: int, kind: str, actor_id: str,
          created_at: str, body_canonical: str) -> str:
    basis = "|".join((prev_hash, str(seq), kind, actor_id, created_at, body_canonical))
    return hashlib.sha256(basis.encode()).hexdigest()


def append(kind: str, actor_id: str, body: dict, conn=None) -> dict:
    """Append one evidence record and return its metadata."""
    if not str(kind or "").strip() or not str(actor_id or "").strip():
        raise ValueError("evidence requires kind and actor_id (SC12)")
    safe_body = classification.redact(dict(body or {}), classification.Level.CONFIDENTIAL)
    created_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    body_canonical = _canonical(safe_body)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT seq, record_hash FROM sentinel_evidence ORDER BY seq DESC LIMIT 1")
        row = cur.fetchone()
        prev_seq, prev_hash = (int(row[0]), str(row[1])) if row else (0, GENESIS_HASH)
        seq = prev_seq + 1
        record_hash = _hash(prev_hash, seq, kind, actor_id, created_at, body_canonical)
        cur.execute(
            """INSERT INTO sentinel_evidence
               (seq, record_hash, prev_hash, kind, actor_id, created_at,
                deployment_sha, policy_version, body_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (seq, record_hash, prev_hash, kind, actor_id, created_at,
             store.deployment_sha(), CONSTITUTION_VERSION, body_canonical))
    return {"seq": seq, "record_hash": record_hash, "prev_hash": prev_hash,
            "created_at": created_at}


def verify_chain(conn=None) -> dict:
    """Recompute every link. Returns {'ok': bool, 'records': n, 'broken_at': seq|None}."""
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT seq, record_hash, prev_hash, kind, actor_id, created_at, body_json "
            "FROM sentinel_evidence ORDER BY seq ASC")
        rows = cur.fetchall()
    expected_prev = GENESIS_HASH
    expected_seq = 1
    for r in rows:
        seq, record_hash, prev_hash = int(r[0]), str(r[1]), str(r[2])
        if seq != expected_seq or prev_hash != expected_prev:
            return {"ok": False, "records": len(rows), "broken_at": seq}
        recomputed = _hash(prev_hash, seq, str(r[3]), str(r[4]), str(r[5]), str(r[6]))
        if recomputed != record_hash:
            return {"ok": False, "records": len(rows), "broken_at": seq}
        expected_prev = record_hash
        expected_seq = seq + 1
    return {"ok": True, "records": len(rows), "broken_at": None}
