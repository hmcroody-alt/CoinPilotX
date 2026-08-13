"""Cloudflare / VirusTotal / device-intelligence contracts
(Mission 4, Stages 18–21).

- Cloudflare (Stage 18): gated per-indicator enrichment for security triage
  ONLY. There is deliberately no bulk entry point — "query every visitor"
  cannot be expressed here. A hosting-provider ASN is context, not malice.
- VirusTotal (Stage 21): hash/url/domain/ip LOOKUP only. File upload does
  not exist in this module, in the provider spec, or in the policy
  vocabulary; community verdicts carry COMMUNITY_INTELLIGENCE trust (≤0.5).
- Device intelligence (Stage 19–20): abstraction + evaluation only. NO SDK
  is installed; the adapter defines the server-side verification contract a
  future vendor must fit into, and returns honest NOT_CONFIGURED until the
  owner adopts one.

Every transport is injected; every call passes enrichment_policy.evaluate.
"""

from __future__ import annotations

import hashlib
import json

from services.sentinel import enrichment_policy, external_observations, external_providers

_VT_VERDICTS = {"malicious": "MALICIOUS", "suspicious": "SUSPICIOUS",
                "harmless": "BENIGN", "undetected": "UNKNOWN"}


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:32]


# --- Cloudflare (Stage 18) --------------------------------------------------

_CF_CAPABILITY_BY_TYPE = {"IP": "ip_intelligence", "DOMAIN": "domain_intelligence",
                          "ASN": "asn_intelligence"}


def cloudflare_enrich(indicator_type: str, indicator_ref: str, *, fetch,
                      purpose: str = "SECURITY_INCIDENT_ENRICHMENT",
                      incident_ref: str = "", conn=None) -> dict:
    """Enrich ONE indicator tied to a security purpose (and ideally an
    incident). Per-indicator by construction: no list parameter exists."""
    capability = _CF_CAPABILITY_BY_TYPE.get(indicator_type)
    if capability is None:
        return {"ok": False,
                "error": f"cloudflare enrichment supports {tuple(_CF_CAPABILITY_BY_TYPE)}, "
                         f"not {indicator_type!r}"}
    decision = enrichment_policy.evaluate(
        "cloudflare_intel", capability, indicator_type, indicator_ref,
        purpose, incident_ref=incident_ref, conn=conn)
    if not decision.allowed:
        return {"ok": False, "decision": decision, "cached": decision.cached}
    safe_payload, stripped = enrichment_policy.minimize(
        {"indicator": str(indicator_ref)})
    status, data, error = "completed", None, ""
    try:
        data = fetch(safe_payload) or {}
        external_providers.record_result("cloudflare_intel", success=True, conn=conn)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)[:300]
        external_providers.record_result("cloudflare_intel", success=False,
                                         detail=error, conn=conn)
    enrichment_policy.complete_request(decision.request_id, status=status, conn=conn)
    enrichment_policy.record_share_audit(
        provider_id="cloudflare_intel", capability=capability, purpose=purpose,
        indicator_type=indicator_type, indicator_ref=indicator_ref,
        data_classes_sent=["indicator_value"], stripped_fields=stripped,
        response_status=status, incident_ref=incident_ref, conn=conn)
    if status == "failed":
        return {"ok": False, "error": error,
                "note": "provider failure → UNKNOWN, not SAFE (Stage 8)"}
    risk_types = [str(t)[:100] for t in (data.get("risk_types") or [])[:20]]
    labels = risk_types + ([f"asn:{data['asn']}"] if data.get("asn") else [])
    verdict = "SUSPICIOUS" if risk_types else "UNKNOWN"
    stored = external_observations.record(
        provider_id="cloudflare_intel", provider_capability=capability,
        indicator_type=indicator_type, indicator_ref=indicator_ref,
        finding_type="cloudflare_intelligence", verdict=verdict,
        severity="medium" if risk_types else "info",
        confidence=0.6 if risk_types else 0.2,
        provider_labels=labels,
        provider_reasons=risk_types or ["no risk types reported"],
        negative_result=not risk_types,
        response_digest=_digest(data),
        metadata={"asn_description": str(data.get("asn_description", ""))[:200],
                  "hosting_note": "hosting/VPN ASN is context, never malice "
                                  "by itself (Stage 18)"},
        conn=conn)
    return {"ok": True, "verdict": verdict, "risk_types": risk_types,
            "observation_id": stored["observation_id"]}


# --- VirusTotal (Stage 21) --------------------------------------------------

_VT_CAPABILITY_BY_TYPE = {"FILE_HASH": "hash_lookup", "URL": "url_lookup",
                          "DOMAIN": "domain_lookup", "IP": "ip_lookup"}


def virustotal_lookup(indicator_type: str, indicator_ref: str, *, fetch,
                      purpose: str = "THREAT_TRIAGE",
                      incident_ref: str = "", conn=None) -> dict:
    """Reputation LOOKUP by hash/url/domain/ip. Uploading a file for analysis
    is not possible: no such function, capability, or purpose exists, and the
    policy gate independently rejects upload capabilities (Stage 6)."""
    capability = _VT_CAPABILITY_BY_TYPE.get(indicator_type)
    if capability is None:
        return {"ok": False,
                "error": f"virustotal lookup supports {tuple(_VT_CAPABILITY_BY_TYPE)}, "
                         f"not {indicator_type!r} — file CONTENT is never sent"}
    decision = enrichment_policy.evaluate(
        "virustotal", capability, indicator_type, indicator_ref,
        purpose, incident_ref=incident_ref, conn=conn)
    if not decision.allowed:
        return {"ok": False, "decision": decision, "cached": decision.cached}
    safe_payload, stripped = enrichment_policy.minimize(
        {"indicator": str(indicator_ref)})
    status, data, error = "completed", None, ""
    try:
        data = fetch(safe_payload) or {}
        external_providers.record_result("virustotal", success=True, conn=conn)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)[:300]
        external_providers.record_result("virustotal", success=False,
                                         detail=error, conn=conn)
    enrichment_policy.complete_request(decision.request_id, status=status, conn=conn)
    enrichment_policy.record_share_audit(
        provider_id="virustotal", capability=capability, purpose=purpose,
        indicator_type=indicator_type, indicator_ref=indicator_ref,
        data_classes_sent=["indicator_value"], stripped_fields=stripped,
        response_status=status, incident_ref=incident_ref, conn=conn)
    if status == "failed":
        return {"ok": False, "error": error,
                "note": "provider failure → UNKNOWN, not SAFE (Stage 8)"}
    stats = (data.get("last_analysis_stats") or {})
    malicious = int(stats.get("malicious", 0) or 0)
    suspicious = int(stats.get("suspicious", 0) or 0)
    total = sum(int(stats.get(k, 0) or 0) for k in
                ("malicious", "suspicious", "harmless", "undetected"))
    if malicious >= 3:
        verdict, severity = "MALICIOUS", "high"
    elif malicious or suspicious:
        verdict, severity = "SUSPICIOUS", "medium"
    elif total:
        verdict, severity = "BENIGN", "info"
    else:
        verdict, severity = "UNKNOWN", "unknown"
    stored = external_observations.record(
        provider_id="virustotal", provider_capability=capability,
        indicator_type=indicator_type, indicator_ref=indicator_ref,
        finding_type="virustotal_reputation", verdict=verdict, severity=severity,
        confidence=0.5 if verdict in ("MALICIOUS", "SUSPICIOUS") else 0.2,
        provider_score=f"{malicious}/{total}" if total else "",
        provider_reasons=[f"{malicious} malicious, {suspicious} suspicious "
                          f"of {total} engines"] if total else [],
        negative_result=verdict == "BENIGN",
        response_digest=_digest(stats),
        metadata={"engine_stats": {k: int(stats.get(k, 0) or 0) for k in
                                   ("malicious", "suspicious", "harmless",
                                    "undetected")},
                  "trust_note": "community verdict — COMMUNITY_INTELLIGENCE "
                                "trust, ceiling 0.5 (Stage 21)"},
        conn=conn)
    return {"ok": True, "verdict": verdict,
            "engine_stats": {"malicious": malicious, "suspicious": suspicious,
                             "total": total},
            "observation_id": stored["observation_id"]}


# --- Device intelligence (Stages 19–20) -------------------------------------

class DeviceIntelligenceAdapter:
    """Server-verified device-intelligence abstraction (Stage 20).

    The contract a future vendor (Fingerprint, etc.) must fit:
    - the CLIENT sends only a vendor request id; the SERVER verifies it with
      the vendor over an authenticated channel (no trusting client-supplied
      verdicts);
    - the result is recorded as a DEVICE_PROVIDER_REF observation with
      COMMERCIAL_INTELLIGENCE trust (ceiling 0.7) — signal, never sentence;
    - no SDK is installed by this mission (Stage 43): until the owner adopts
      a vendor per docs/sentinel/device_intelligence_provider_evaluation.md,
      verify() reports NOT_CONFIGURED honestly.
    """

    provider_id = "device_intel"
    capability = "device_verify"

    def __init__(self, fetch=None):
        self._fetch = fetch  # injected vendor transport; None = not adopted

    def verify(self, vendor_request_id: str, *, purpose: str = "THREAT_TRIAGE",
               incident_ref: str = "", conn=None) -> dict:
        ref = str(vendor_request_id or "").strip()
        if not ref:
            return {"ok": False, "error": "vendor_request_id required"}
        if self._fetch is None:
            return {"ok": False, "status": "NOT_CONFIGURED",
                    "note": "no device-intelligence vendor adopted; evaluation "
                            "doc governs adoption (Stage 19), no SDK installed "
                            "(Stage 43)"}
        decision = enrichment_policy.evaluate(
            self.provider_id, self.capability, "DEVICE_PROVIDER_REF", ref,
            purpose, incident_ref=incident_ref, conn=conn)
        if not decision.allowed:
            return {"ok": False, "decision": decision, "cached": decision.cached}
        safe_payload, stripped = enrichment_policy.minimize({"request_id": ref})
        status, data, error = "completed", None, ""
        try:
            data = self._fetch(safe_payload) or {}
            external_providers.record_result(self.provider_id, success=True,
                                             conn=conn)
        except Exception as exc:  # noqa: BLE001
            status, error = "failed", str(exc)[:300]
            external_providers.record_result(self.provider_id, success=False,
                                             detail=error, conn=conn)
        enrichment_policy.complete_request(decision.request_id, status=status,
                                           conn=conn)
        enrichment_policy.record_share_audit(
            provider_id=self.provider_id, capability=self.capability,
            purpose=purpose, indicator_type="DEVICE_PROVIDER_REF",
            indicator_ref=ref, data_classes_sent=["vendor_request_id"],
            stripped_fields=stripped, response_status=status,
            incident_ref=incident_ref, conn=conn)
        if status == "failed":
            return {"ok": False, "error": error,
                    "note": "vendor unavailable → device signal UNKNOWN; "
                            "auth/checkout MUST proceed on internal signals "
                            "(Stage 8)"}
        signals = [str(s)[:100] for s in (data.get("signals") or [])[:20]]
        verdict = "SUSPICIOUS" if signals else "UNKNOWN"
        stored = external_observations.record(
            provider_id=self.provider_id, provider_capability=self.capability,
            indicator_type="DEVICE_PROVIDER_REF", indicator_ref=ref,
            finding_type="device_intelligence", verdict=verdict,
            severity="medium" if signals else "info",
            confidence=min(0.7, float(data.get("confidence", 0.0) or 0.0)),
            provider_labels=signals,
            provider_reasons=signals or ["no risk signals reported"],
            negative_result=not signals,
            response_digest=_digest(data),
            metadata={"authority_note": "device signal informs risk fusion; it "
                                        "never blocks or bans by itself (SC2)"},
            conn=conn)
        return {"ok": True, "verdict": verdict, "signals": signals,
                "observation_id": stored["observation_id"]}
