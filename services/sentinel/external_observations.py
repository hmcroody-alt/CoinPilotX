"""SentinelExternalObservationV1 (Mission 4, Stages 4, 7, 23, 24).

Normalized envelope for every fact obtained from an external provider, plus
the enrichment cache built on top of it. Rules:

- No raw file content, no secrets, no raw internal identifiers in the
  envelope. Metadata is redacted at the CONFIDENTIAL ceiling before storage
  and forbidden field names are rejected outright (secret smuggling defense).
- Every observation expires (Stage 24). An expired observation degrades to
  verdict UNKNOWN / trust STALE on read — stale intelligence never remains
  silently active.
- Disagreement is preserved (Stage 23): per-provider verdicts are returned
  side by side, never averaged into fake certainty.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone

from services.sentinel import classification, store
from services.sentinel.external_providers import (
    PROVIDERS, external_confidence_ceiling, validate_external_trust)

INDICATOR_TYPES = (
    "IP", "DOMAIN", "ASN", "URL", "FILE_HASH", "PACKAGE", "PACKAGE_VERSION",
    "COMMIT", "CVE", "REPOSITORY", "ARTIFACT_DIGEST", "DEVICE_PROVIDER_REF",
)

VERDICTS = ("MALICIOUS", "SUSPICIOUS", "BENIGN", "VULNERABLE", "NOT_AFFECTED",
            "UNKNOWN")

SEVERITIES = ("info", "low", "medium", "high", "critical", "unknown")

_TS = "%Y-%m-%d %H:%M:%S"

# Field names that must never appear in observation metadata (secret
# smuggling in provider payloads — Stage 36).
_FORBIDDEN_METADATA_KEYS = classification._FORBIDDEN_SUBSTRINGS + (
    "pulse_id", "internal_user_id", "email", "phone", "raw_ip",
)


class ObservationError(ValueError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


def _parse(ts) -> datetime | None:
    try:
        return datetime.strptime(str(ts)[:19], _TS).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def indicator_digest(indicator_type: str, indicator_ref: str) -> str:
    """Stable digest for cache keys and audit rows — the digest, not the raw
    indicator, is what most surfaces show."""
    basis = f"{indicator_type}|{str(indicator_ref).strip().lower()}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]


def _reject_forbidden(metadata: dict) -> None:
    def walk(d: dict, path: str = "") -> None:
        for k, v in d.items():
            name = str(k).lower()
            if any(s in name for s in _FORBIDDEN_METADATA_KEYS):
                raise ObservationError(
                    f"forbidden field {path + str(k)!r} in observation metadata (SC9)")
            if isinstance(v, dict):
                walk(v, path + str(k) + ".")
    walk(dict(metadata or {}))


def record(*, provider_id: str, provider_capability: str, indicator_type: str,
           indicator_ref: str, finding_type: str, verdict: str,
           severity: str = "unknown", confidence: float = 0.0,
           provider_score: str = "", provider_labels: list | None = None,
           provider_reasons: list | None = None, provider_event_id: str = "",
           first_seen_at: str | None = None, last_seen_at: str | None = None,
           ttl_minutes: int | None = None, provider_modified_at: str | None = None,
           catalog_version: str = "", source_trust: str | None = None,
           sharing_policy_version: str = "", request_evidence_id: str = "",
           response_digest: str = "", raw_response_reference: str = "",
           related_cve_ids: list | None = None,
           related_package_refs: list | None = None,
           related_repository_refs: list | None = None,
           related_deployment_shas: list | None = None,
           negative_result: bool = False, metadata: dict | None = None,
           conn=None) -> dict:
    """Validate and store one normalized external observation."""
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise ObservationError(f"unknown provider {provider_id!r} (SC15)")
    if indicator_type not in INDICATOR_TYPES:
        raise ObservationError(f"unknown indicator_type {indicator_type!r} (SC15)")
    if verdict not in VERDICTS:
        raise ObservationError(f"unknown verdict {verdict!r} (SC15)")
    if severity not in SEVERITIES:
        raise ObservationError(f"unknown severity {severity!r} (SC15)")
    trust = source_trust or spec.source_trust
    validate_external_trust(trust)
    ceiling = external_confidence_ceiling(trust)
    confidence = max(0.0, min(float(confidence), ceiling))
    metadata = dict(metadata or {})
    _reject_forbidden(metadata)
    safe_metadata = classification.redact(metadata, classification.Level.CONFIDENTIAL)

    now = _utcnow()
    ttl = int(ttl_minutes if ttl_minutes is not None
              else spec.budget.cache_ttl_minutes)
    ttl = max(1, min(ttl, 60 * 24 * 90))  # bounded: ≤ 90 days
    observation_id = "extobs_" + uuid.uuid4().hex[:20]
    digest = indicator_digest(indicator_type, indicator_ref)

    with store.connection(conn) as c:
        c.cursor().execute(
            """INSERT INTO sentinel_external_observations
               (observation_id, provider_id, provider_capability, provider_event_id,
                indicator_type, indicator_ref, indicator_digest, finding_type,
                verdict, severity, confidence, provider_score,
                provider_labels_json, provider_reasons_json, first_seen_at,
                last_seen_at, fetched_at, expires_at, provider_modified_at,
                catalog_version, source_trust, data_classification,
                sharing_policy_version, request_evidence_id, response_digest,
                raw_response_reference, related_cve_ids_json,
                related_package_refs_json, related_repository_refs_json,
                related_deployment_shas_json, negative_result, metadata_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (observation_id, provider_id, provider_capability,
             str(provider_event_id)[:200], indicator_type,
             str(indicator_ref)[:500], digest, str(finding_type)[:100],
             verdict, severity, confidence, str(provider_score)[:100],
             json.dumps([str(x)[:200] for x in (provider_labels or [])[:50]]),
             json.dumps([str(x)[:500] for x in (provider_reasons or [])[:50]]),
             first_seen_at, last_seen_at, _fmt(now),
             _fmt(now + timedelta(minutes=ttl)), provider_modified_at,
             str(catalog_version)[:100], trust, "CONFIDENTIAL",
             str(sharing_policy_version)[:50], str(request_evidence_id)[:100],
             str(response_digest)[:100], str(raw_response_reference)[:300],
             json.dumps([str(x)[:50] for x in (related_cve_ids or [])[:100]]),
             json.dumps([str(x)[:200] for x in (related_package_refs or [])[:100]]),
             json.dumps([str(x)[:200] for x in (related_repository_refs or [])[:50]]),
             json.dumps([str(x)[:64] for x in (related_deployment_shas or [])[:20]]),
             1 if negative_result else 0, json.dumps(safe_metadata, default=str)))
    return {"observation_id": observation_id, "indicator_digest": digest,
            "confidence": confidence, "source_trust": trust,
            "expires_at": _fmt(now + timedelta(minutes=ttl))}


_ROW_COLS = (
    "observation_id, provider_id, provider_capability, indicator_type, "
    "indicator_ref, indicator_digest, finding_type, verdict, severity, "
    "confidence, provider_labels_json, provider_reasons_json, fetched_at, "
    "expires_at, source_trust, negative_result, related_cve_ids_json, "
    "related_package_refs_json, related_deployment_shas_json, metadata_json")


def _row_to_dict(r, *, now: datetime | None = None) -> dict:
    now = now or _utcnow()
    out = {"observation_id": r[0], "provider_id": r[1],
           "provider_capability": r[2], "indicator_type": r[3],
           "indicator_ref": r[4], "indicator_digest": r[5],
           "finding_type": r[6], "verdict": r[7], "severity": r[8],
           "confidence": float(r[9]),
           "provider_labels": json.loads(r[10] or "[]"),
           "provider_reasons": json.loads(r[11] or "[]"),
           "fetched_at": r[12], "expires_at": r[13], "source_trust": r[14],
           "negative_result": bool(r[15]),
           "related_cve_ids": json.loads(r[16] or "[]"),
           "related_package_refs": json.loads(r[17] or "[]"),
           "related_deployment_shas": json.loads(r[18] or "[]"),
           "metadata": json.loads(r[19] or "{}"), "expired": False}
    expires = _parse(out["expires_at"])
    if expires is not None and now >= expires:
        # Stage 24: stale intelligence degrades loudly — UNKNOWN, not SAFE,
        # and never the original verdict.
        out.update({"expired": True, "verdict": "UNKNOWN",
                    "source_trust": "UNVERIFIED_EXTERNAL",
                    "confidence": 0.0,
                    "staleness_note": "observation past expires_at; original "
                                      "verdict withheld (Stage 24)"})
    return out


def cache_lookup(provider_id: str, capability: str, indicator_type: str,
                 indicator_ref: str, conn=None) -> dict | None:
    """Return the newest FRESH cached observation for this cache key, or None.
    Negative results are cached too — 'provider said nothing' is an answer."""
    digest = indicator_digest(indicator_type, indicator_ref)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            f"SELECT {_ROW_COLS} FROM sentinel_external_observations "
            f"WHERE provider_id=? AND provider_capability=? AND indicator_type=? "
            f"AND indicator_digest=? ORDER BY id DESC LIMIT 1",
            (provider_id, capability, indicator_type, digest))
        row = cur.fetchone()
    if not row:
        return None
    out = _row_to_dict(row)
    return None if out["expired"] else out


def for_indicator(indicator_type: str, indicator_ref: str, *, limit: int = 50,
                  conn=None) -> list[dict]:
    """All observations (newest first, all providers) for one indicator.
    Expired rows are included but degraded."""
    digest = indicator_digest(indicator_type, indicator_ref)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            f"SELECT {_ROW_COLS} FROM sentinel_external_observations "
            f"WHERE indicator_type=? AND indicator_digest=? "
            f"ORDER BY id DESC LIMIT ?",
            (indicator_type, digest, max(1, min(int(limit), 200))))
        rows = cur.fetchall()
    return [_row_to_dict(r) for r in rows]


def get(observation_id: str, conn=None) -> dict | None:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            f"SELECT {_ROW_COLS} FROM sentinel_external_observations "
            f"WHERE observation_id=?", (observation_id,))
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def disagreement(indicator_type: str, indicator_ref: str, conn=None) -> dict:
    """Stage 23: per-provider verdicts side by side. The newest unexpired
    verdict per provider is preserved verbatim; nothing is averaged."""
    rows = for_indicator(indicator_type, indicator_ref, conn=conn)
    by_provider: dict[str, dict] = {}
    for row in rows:
        if row["provider_id"] not in by_provider and not row["expired"]:
            by_provider[row["provider_id"]] = {
                "verdict": row["verdict"], "confidence": row["confidence"],
                "source_trust": row["source_trust"],
                "fetched_at": row["fetched_at"],
                "observation_id": row["observation_id"]}
    verdicts = {v["verdict"] for v in by_provider.values()}
    return {"indicator_type": indicator_type,
            "indicator_digest": indicator_digest(indicator_type, indicator_ref),
            "providers": by_provider,
            "disagreement": len(verdicts) > 1,
            "note": "verdicts preserved per provider; no averaging (Stage 23)"}


def stale_count(conn=None) -> int:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM sentinel_external_observations "
            "WHERE expires_at < datetime('now')")
        return int(cur.fetchone()[0])
