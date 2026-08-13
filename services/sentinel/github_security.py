"""GitHub security adapter — strictly read-only (Mission 4, Stage 13).

Reads Dependabot alerts, code-scanning alerts, secret-scanning alerts
(NEVER the secret values) and artifact attestations, normalized into the
external-observation envelope.

This module deliberately contains NO function that could dismiss an alert,
update an alert, create a fix PR, merge anything, or write to GitHub in any
way. The transport is injected (``fetch=``) so tests run on fixtures; every
real call passes the enrichment policy gate first.
"""

from __future__ import annotations

import hashlib
import json

from services.sentinel import enrichment_policy, external_observations, external_providers, incidents

PROVIDER_ID = "github_security"
SENTINEL_ACTOR = "sentinel.github_security"

_SEV_MAP = {"critical": "critical", "high": "high", "medium": "medium",
            "moderate": "medium", "low": "low", "warning": "low",
            "note": "info", "error": "high"}


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:32]


def _sev(value) -> str:
    return _SEV_MAP.get(str(value or "").lower(), "unknown")


# --- Normalizers (pure; fixtures in tests) ----------------------------------

def normalize_dependabot_alert(alert: dict) -> dict:
    adv = (alert or {}).get("security_advisory") or {}
    dep = (alert or {}).get("dependency") or {}
    pkg = dep.get("package") or {}
    vuln = ((alert or {}).get("security_vulnerability") or {})
    return {
        "alert_number": int(alert.get("number", 0)),
        "state": str(alert.get("state", ""))[:30],
        "ecosystem": str(pkg.get("ecosystem", ""))[:50],
        "package": str(pkg.get("name", ""))[:200],
        "manifest_path": str(dep.get("manifest_path", ""))[:300],
        "scope": str(dep.get("scope", ""))[:30],
        "ghsa_id": str(adv.get("ghsa_id", ""))[:50],
        "cve_id": str(adv.get("cve_id") or "")[:50],
        "severity": _sev(adv.get("severity")),
        "summary": str(adv.get("summary", ""))[:500],
        "vulnerable_range": str((vuln.get("vulnerable_version_range") or ""))[:100],
        "fixed_version": str(((vuln.get("first_patched_version") or {})
                              .get("identifier") or ""))[:100],
        "created_at": str(alert.get("created_at", ""))[:30],
    }


def normalize_code_scanning_alert(alert: dict) -> dict:
    rule = (alert or {}).get("rule") or {}
    inst = (alert or {}).get("most_recent_instance") or {}
    loc = inst.get("location") or {}
    return {
        "alert_number": int(alert.get("number", 0)),
        "state": str(alert.get("state", ""))[:30],
        "rule_id": str(rule.get("id", ""))[:200],
        "rule_description": str(rule.get("description", ""))[:300],
        "severity": _sev(rule.get("security_severity_level") or rule.get("severity")),
        "path": str(loc.get("path", ""))[:300],
        "start_line": int(loc.get("start_line", 0) or 0),
        "ref": str(inst.get("ref", ""))[:100],
        "created_at": str(alert.get("created_at", ""))[:30],
    }


def normalize_secret_scanning_alert(alert: dict) -> dict:
    """Normalize a secret-scanning alert. THE SECRET VALUE IS NEVER READ:
    only the type, state and location metadata cross this boundary."""
    out = {
        "alert_number": int(alert.get("number", 0)),
        "state": str(alert.get("state", ""))[:30],
        "secret_type": str(alert.get("secret_type", ""))[:100],
        "secret_type_display": str(alert.get("secret_type_display_name", ""))[:200],
        "validity": str(alert.get("validity", ""))[:30],
        "publicly_leaked": bool(alert.get("publicly_leaked")),
        "created_at": str(alert.get("created_at", ""))[:30],
    }
    # Note: the raw payload is never stored — only the fields whitelisted
    # above cross this boundary, so a secret value present in the provider
    # response is dropped here by construction.
    return out


def normalize_attestation(att: dict) -> dict:
    bundle = (att or {}).get("bundle") or {}
    return {
        "repository_id": int(att.get("repository_id", 0) or 0),
        "bundle_present": bool(bundle),
        "media_type": str(bundle.get("mediaType", ""))[:100],
    }


_CAPABILITY_NORMALIZERS = {
    "dependabot_alerts": (normalize_dependabot_alert, "github_dependabot_alert"),
    "code_scanning_alerts": (normalize_code_scanning_alert, "github_code_scanning_alert"),
    "secret_scanning_alerts": (normalize_secret_scanning_alert, "github_secret_scanning_alert"),
    "artifact_attestations": (normalize_attestation, "github_artifact_attestation"),
}


def sync_alerts(capability: str, repository: str, *, fetch,
                purpose: str = "SUPPLY_CHAIN_REVIEW", conn=None) -> dict:
    """Fetch + normalize + store one alert family for one repository.
    Read-only: alerts are recorded as observations and (for open alerts)
    deduped incidents. Nothing is dismissed, fixed, or merged."""
    if capability not in _CAPABILITY_NORMALIZERS:
        return {"ok": False,
                "error": f"unknown or non-read capability {capability!r} (SC15)"}
    normalizer, finding_type = _CAPABILITY_NORMALIZERS[capability]
    decision = enrichment_policy.evaluate(
        PROVIDER_ID, capability, "REPOSITORY", repository, purpose, conn=conn)
    if not decision.allowed:
        return {"ok": False, "decision": decision, "alerts": [],
                "cached": decision.cached}
    safe_payload, stripped = enrichment_policy.minimize(
        {"repository": repository, "capability": capability})
    status, normalized, error = "completed", [], ""
    try:
        raw = fetch(safe_payload) or []
        normalized = [normalizer(a) for a in raw]
        external_providers.record_result(PROVIDER_ID, success=True, conn=conn)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)[:300]
        external_providers.record_result(PROVIDER_ID, success=False,
                                         detail=error, conn=conn)
    enrichment_policy.complete_request(decision.request_id, status=status, conn=conn)
    enrichment_policy.record_share_audit(
        provider_id=PROVIDER_ID, capability=capability, purpose=purpose,
        indicator_type="REPOSITORY", indicator_ref=repository,
        data_classes_sent=["repository_name"], stripped_fields=stripped,
        response_status=status, conn=conn)
    if status == "failed":
        return {"ok": False, "error": error, "alerts": [],
                "note": "GitHub unavailable — alert state UNKNOWN, not clean (Stage 8)"}

    observation_ids, incident_keys = [], []
    for alert in normalized:
        severity = alert.get("severity", "unknown") \
            if capability != "secret_scanning_alerts" else "high"
        if capability == "secret_scanning_alerts":
            # Field names containing 'secret' are (correctly) rejected by the
            # observation envelope's smuggling defense — store the leak TYPE
            # under neutral names. The value itself never arrives here at all.
            metadata = {"leak_type": alert.get("secret_type", ""),
                        "leak_type_display": alert.get("secret_type_display", ""),
                        "validity": alert.get("validity", ""),
                        "publicly_leaked": alert.get("publicly_leaked", False),
                        "alert_state": alert.get("state", "")}
        else:
            metadata = dict(alert)
        stored = external_observations.record(
            provider_id=PROVIDER_ID, provider_capability=capability,
            indicator_type="REPOSITORY", indicator_ref=repository,
            finding_type=finding_type,
            verdict="VULNERABLE" if alert.get("state") == "open" else "UNKNOWN",
            severity=severity if severity in external_observations.SEVERITIES
                     else "unknown",
            confidence=0.85,
            provider_event_id=str(alert.get("alert_number", "")),
            provider_reasons=[alert.get("summary")
                              or alert.get("rule_description")
                              or alert.get("secret_type_display")
                              or ""][:1],
            related_cve_ids=[alert["cve_id"]] if alert.get("cve_id") else [],
            related_repository_refs=[repository],
            response_digest=_digest(alert), metadata=metadata, conn=conn)
        observation_ids.append(stored["observation_id"])

        if alert.get("state") == "open":
            key_extra = str(alert.get("alert_number", ""))
            if capability == "dependabot_alerts":
                itype = "VULNERABLE_DEPENDENCY"
                subject = f"{alert.get('package')} ({alert.get('ghsa_id')})"
            elif capability == "code_scanning_alerts":
                itype = "CODE_SCANNING_FINDING"
                subject = f"{alert.get('rule_id')} @ {alert.get('path')}"
            elif capability == "secret_scanning_alerts":
                itype = "SECRET_EXPOSURE_FINDING"
                subject = alert.get("secret_type_display") or alert.get("secret_type")
            else:
                continue
            incident_key = incidents.dedupe_key(
                repository, PROVIDER_ID, capability, key_extra)
            incidents.open_incident(
                incident_key, itype,
                "critical" if capability == "secret_scanning_alerts"
                else (severity if severity in ("critical", "high", "medium", "low")
                      else "medium"),
                f"GitHub {capability.replace('_', ' ')}: {subject}",
                SENTINEL_ACTOR,
                detail={"alert_number": alert.get("alert_number"),
                        "observation_id": stored["observation_id"],
                        "authority_note": "GitHub alert is evidence; triage and "
                                          "any fix are human decisions (Stage 13/31)"},
                conn=conn,
                owner_action_required=capability == "secret_scanning_alerts")
            incident_keys.append(incident_key)
    return {"ok": True, "alerts": normalized,
            "observation_ids": observation_ids,
            "incident_keys": incident_keys,
            "note": "read-only sync; no dismiss/update/autofix/merge exists "
                    "in this module (Stage 13)"}


def record_attestation_result(repository: str, artifact_digest: str, *,
                              present: bool, valid: bool | None = None,
                              environment: str = "production",
                              conn=None) -> dict:
    """Record a provenance check outcome for one built artifact. Opens the
    Stage 17 provenance incidents (deduped per repo+digest+env). Missing or
    invalid provenance blocks nothing automatically — it informs the owner."""
    if present and valid is not False:
        return {"ok": True, "incident_key": "", "provenance": "attested"}
    itype = "ARTIFACT_PROVENANCE_MISSING" if not present else "ARTIFACT_PROVENANCE_INVALID"
    incident_key = incidents.dedupe_key(repository, itype,
                                        str(artifact_digest)[:64], environment)
    incidents.open_incident(
        incident_key, itype, "high" if itype.endswith("INVALID") else "medium",
        f"artifact provenance {'invalid' if present else 'missing'} for "
        f"{repository} @ {str(artifact_digest)[:16]}",
        SENTINEL_ACTOR,
        detail={"artifact_digest": str(artifact_digest)[:64],
                "environment": environment,
                "authority_note": "provenance failure is evidence for review; "
                                  "no deploy is blocked automatically (Stage 44)"},
        conn=conn, owner_action_required=itype.endswith("INVALID"))
    return {"ok": True, "incident_key": incident_key,
            "provenance": "invalid" if present else "missing"}
