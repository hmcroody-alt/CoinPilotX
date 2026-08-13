"""OSV / NVD / CISA-KEV read-only adapters (Mission 4, Stages 10–12).

Pure normalizers over injectable transports. Rules:

- Read-only: no adapter here mutates anything anywhere — not locally beyond
  observation rows, and never at the provider.
- The transport is injected (``fetch=`` callable) so tests run on fixtures;
  nothing in this module opens a network connection by itself, and every
  real call must first pass ``enrichment_policy.evaluate``.
- DO NOT INVENT INSTALLED VERSIONS: adapters normalize what the provider
  said about a package/CVE. Whether PulseSoc *runs* that version is decided
  by supply_chain.py against the real dependency inventory, never here.
- An OSV/NVD finding is knowledge about an ecosystem, not proof of a
  deployed vulnerability. CVSS alone is not priority.
"""

from __future__ import annotations

import hashlib
import json

from services.sentinel import enrichment_policy, external_observations, external_providers


class AdapterError(ValueError):
    pass


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode()).hexdigest()[:32]


def _severity_from_cvss(score) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "unknown"
    if s >= 9.0:
        return "critical"
    if s >= 7.0:
        return "high"
    if s >= 4.0:
        return "medium"
    if s > 0.0:
        return "low"
    return "info"


# --- OSV (Stage 10) ---------------------------------------------------------

def normalize_osv_entry(entry: dict) -> dict:
    """Normalize one OSV vulnerability entry. Only fields OSV actually
    provides; nothing is guessed."""
    if not isinstance(entry, dict) or not entry.get("id"):
        raise AdapterError("OSV entry missing id")
    affected_out = []
    for aff in entry.get("affected") or []:
        pkg = (aff or {}).get("package") or {}
        fixed_versions = []
        for rng in (aff or {}).get("ranges") or []:
            for ev in (rng or {}).get("events") or []:
                if isinstance(ev, dict) and ev.get("fixed"):
                    fixed_versions.append(str(ev["fixed"])[:100])
        affected_out.append({
            "ecosystem": str(pkg.get("ecosystem", ""))[:50],
            "package": str(pkg.get("name", ""))[:200],
            # versions OSV explicitly lists as affected — may be empty; an
            # empty list means "OSV used ranges", NOT "nothing affected".
            "versions": [str(v)[:100] for v in ((aff or {}).get("versions") or [])[:200]],
            "fixed_versions": fixed_versions[:20],
        })
    severity = "unknown"
    max_score = None
    for sev in entry.get("severity") or []:
        try:
            # CVSS vector strings carry no plain score; OSV database_specific
            # sometimes does. Only numeric scores are used.
            max_score = max(float(sev.get("score")),
                            max_score if max_score is not None else 0.0)
        except (TypeError, ValueError):
            continue
    db_sev = str(((entry.get("database_specific") or {}).get("severity") or "")).lower()
    if max_score is not None:
        severity = _severity_from_cvss(max_score)
    elif db_sev in ("critical", "high", "medium", "moderate", "low"):
        severity = "medium" if db_sev == "moderate" else db_sev
    return {
        "vulnerability_id": str(entry["id"])[:100],
        "aliases": [str(a)[:100] for a in (entry.get("aliases") or [])[:50]],
        "summary": str(entry.get("summary", ""))[:500],
        "severity": severity,
        "affected": affected_out,
        "references": [str((r or {}).get("url", ""))[:300]
                       for r in (entry.get("references") or [])[:20]],
        "published": str(entry.get("published", ""))[:30],
        "modified": str(entry.get("modified", ""))[:30],
        "withdrawn": str(entry.get("withdrawn", ""))[:30],
    }


def osv_query_package(ecosystem: str, package: str, version: str, *,
                      fetch, purpose: str = "VULNERABILITY_TRIAGE",
                      conn=None) -> dict:
    """Query OSV for one REAL (ecosystem, package, version) triple taken from
    the dependency inventory. ``fetch(payload) -> dict`` is the injected
    transport (fixtures in tests, HTTP in production)."""
    indicator_ref = f"{ecosystem}:{package}:{version}"
    decision = enrichment_policy.evaluate(
        "osv", "vulnerability_query", "PACKAGE_VERSION", indicator_ref,
        purpose, conn=conn)
    if not decision.allowed:
        return {"ok": False, "decision": decision, "vulnerabilities": [],
                "cached": decision.cached}
    payload = {"package": {"ecosystem": str(ecosystem), "name": str(package)},
               "version": str(version)}
    safe_payload, stripped = enrichment_policy.minimize(payload)
    status, normalized, error = "completed", [], ""
    try:
        raw = fetch(safe_payload) or {}
        entries = raw.get("vulns") or []
        normalized = [normalize_osv_entry(e) for e in entries]
        external_providers.record_result("osv", success=True, conn=conn)
    except Exception as exc:  # noqa: BLE001 — provider failure is data, not a crash
        status, error = "failed", str(exc)[:300]
        external_providers.record_result("osv", success=False, detail=error, conn=conn)
    enrichment_policy.complete_request(decision.request_id, status=status, conn=conn)
    enrichment_policy.record_share_audit(
        provider_id="osv", capability="vulnerability_query", purpose=purpose,
        indicator_type="PACKAGE_VERSION", indicator_ref=indicator_ref,
        data_classes_sent=["package_coordinates"], stripped_fields=stripped,
        response_status=status, conn=conn)
    if status == "failed":
        return {"ok": False, "error": error, "vulnerabilities": [],
                "note": "provider failure means intelligence is UNKNOWN, not SAFE (Stage 8)"}

    observation_ids = []
    if normalized:
        for vuln in normalized:
            stored = external_observations.record(
                provider_id="osv", provider_capability="vulnerability_query",
                indicator_type="PACKAGE_VERSION", indicator_ref=indicator_ref,
                finding_type="osv_vulnerability", verdict="VULNERABLE",
                severity=vuln["severity"], confidence=0.85,
                provider_event_id=vuln["vulnerability_id"],
                provider_reasons=[vuln["summary"]] if vuln["summary"] else [],
                provider_modified_at=vuln["modified"] or None,
                related_cve_ids=[a for a in ([vuln["vulnerability_id"]] + vuln["aliases"])
                                 if a.upper().startswith("CVE-")],
                related_package_refs=[indicator_ref],
                response_digest=_digest(vuln),
                metadata={"fixed_versions": [fv for aff in vuln["affected"]
                                             for fv in aff["fixed_versions"]][:20],
                          "withdrawn": vuln["withdrawn"]},
                conn=conn)
            observation_ids.append(stored["observation_id"])
    else:
        # Negative result is cached knowledge too (Stage 7).
        stored = external_observations.record(
            provider_id="osv", provider_capability="vulnerability_query",
            indicator_type="PACKAGE_VERSION", indicator_ref=indicator_ref,
            finding_type="osv_vulnerability", verdict="NOT_AFFECTED",
            severity="info", confidence=0.85, negative_result=True,
            response_digest=_digest({"vulns": []}), conn=conn)
        observation_ids.append(stored["observation_id"])
    return {"ok": True, "vulnerabilities": normalized,
            "observation_ids": observation_ids,
            "note": "an OSV finding is repository knowledge; applicability is "
                    "decided against the real inventory (Stage 10)"}


# --- NVD (Stage 11) ---------------------------------------------------------

def normalize_nvd_cve(entry: dict) -> dict:
    """Normalize one NVD CVE record (the ``cve`` object of the 2.0 API)."""
    cve = entry.get("cve") if isinstance(entry, dict) and "cve" in entry else entry
    if not isinstance(cve, dict) or not cve.get("id"):
        raise AdapterError("NVD entry missing cve.id")
    score, vector = None, ""
    metrics = cve.get("metrics") or {}
    for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
        for m in metrics.get(key) or []:
            data = (m or {}).get("cvssData") or {}
            try:
                s = float(data.get("baseScore"))
            except (TypeError, ValueError):
                continue
            if score is None or s > score:
                score, vector = s, str(data.get("vectorString", ""))[:100]
    descriptions = [d.get("value", "") for d in (cve.get("descriptions") or [])
                    if (d or {}).get("lang") == "en"]
    cwes = []
    for w in cve.get("weaknesses") or []:
        for d in (w or {}).get("description") or []:
            val = str((d or {}).get("value", ""))
            if val.startswith("CWE-"):
                cwes.append(val[:20])
    return {
        "cve_id": str(cve["id"])[:50],
        "status": str(cve.get("vulnStatus", ""))[:50],
        "description": str(descriptions[0] if descriptions else "")[:500],
        "cvss_score": score,
        "cvss_vector": vector,
        "severity": _severity_from_cvss(score),
        "cwe_ids": sorted(set(cwes))[:10],
        "references": [str((r or {}).get("url", ""))[:300]
                       for r in (cve.get("references") or [])[:20]],
        "published": str(cve.get("published", ""))[:30],
        "modified": str(cve.get("lastModified", ""))[:30],
        "note": "CVSS is severity context, never priority by itself (Stage 11)",
    }


def nvd_enrich_cve(cve_id: str, *, fetch,
                   purpose: str = "VULNERABILITY_TRIAGE", conn=None) -> dict:
    """Enrich one CVE id via NVD. Read-only; fixture-driven in tests."""
    cve_id = str(cve_id or "").strip().upper()
    if not cve_id.startswith("CVE-"):
        raise AdapterError(f"not a CVE id: {cve_id!r}")
    decision = enrichment_policy.evaluate(
        "nvd", "cve_enrichment", "CVE", cve_id, purpose, conn=conn)
    if not decision.allowed:
        return {"ok": False, "decision": decision, "cve": None,
                "cached": decision.cached}
    safe_payload, stripped = enrichment_policy.minimize({"cveId": cve_id})
    status, normalized, error = "completed", None, ""
    try:
        raw = fetch(safe_payload) or {}
        vulns = raw.get("vulnerabilities") or []
        if vulns:
            normalized = normalize_nvd_cve(vulns[0])
        external_providers.record_result("nvd", success=True, conn=conn)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)[:300]
        external_providers.record_result("nvd", success=False, detail=error, conn=conn)
    enrichment_policy.complete_request(decision.request_id, status=status, conn=conn)
    enrichment_policy.record_share_audit(
        provider_id="nvd", capability="cve_enrichment", purpose=purpose,
        indicator_type="CVE", indicator_ref=cve_id,
        data_classes_sent=["cve_id"], stripped_fields=stripped,
        response_status=status, conn=conn)
    if status == "failed":
        return {"ok": False, "error": error, "cve": None,
                "note": "NVD unavailable — enrichment UNKNOWN, not SAFE (Stage 8)"}
    if normalized is None:
        stored = external_observations.record(
            provider_id="nvd", provider_capability="cve_enrichment",
            indicator_type="CVE", indicator_ref=cve_id,
            finding_type="nvd_cve", verdict="UNKNOWN", severity="unknown",
            confidence=0.0, negative_result=True,
            response_digest=_digest({"vulnerabilities": []}), conn=conn)
        return {"ok": True, "cve": None, "observation_id": stored["observation_id"],
                "note": "NVD returned no record for this id"}
    stored = external_observations.record(
        provider_id="nvd", provider_capability="cve_enrichment",
        indicator_type="CVE", indicator_ref=cve_id,
        finding_type="nvd_cve", verdict="VULNERABLE",
        severity=normalized["severity"], confidence=0.9,
        provider_score=str(normalized["cvss_score"] or ""),
        provider_event_id=normalized["cve_id"],
        provider_reasons=[normalized["description"]] if normalized["description"] else [],
        provider_modified_at=normalized["modified"] or None,
        related_cve_ids=[normalized["cve_id"]],
        response_digest=_digest(normalized),
        metadata={"cvss_vector": normalized["cvss_vector"],
                  "cwe_ids": normalized["cwe_ids"],
                  "vuln_status": normalized["status"]},
        conn=conn)
    return {"ok": True, "cve": normalized,
            "observation_id": stored["observation_id"]}


# --- CISA KEV (Stage 12) ----------------------------------------------------

def normalize_kev_entry(entry: dict) -> dict:
    if not isinstance(entry, dict) or not entry.get("cveID"):
        raise AdapterError("KEV entry missing cveID")
    return {
        "cve_id": str(entry["cveID"])[:50],
        "vendor": str(entry.get("vendorProject", ""))[:100],
        "product": str(entry.get("product", ""))[:100],
        "name": str(entry.get("vulnerabilityName", ""))[:200],
        "date_added": str(entry.get("dateAdded", ""))[:30],
        "due_date": str(entry.get("dueDate", ""))[:30],
        "known_ransomware": str(entry.get("knownRansomwareCampaignUse", ""))
                            .lower() == "known",
        "required_action": str(entry.get("requiredAction", ""))[:300],
        "known_exploited": True,
    }


def kev_sync(*, fetch, purpose: str = "VULNERABILITY_TRIAGE", conn=None) -> dict:
    """Sync the CISA KEV catalog. The catalog itself is public and carries no
    PulseSoc data outbound. A KEV match against a DEPLOYED component elevates
    an incident (supply_chain.py) — it never auto-upgrades anything."""
    decision = enrichment_policy.evaluate(
        "cisa_kev", "kev_catalog_sync", "CVE", "kev-catalog", purpose, conn=conn)
    if not decision.allowed:
        return {"ok": False, "decision": decision, "entries": [],
                "cached": decision.cached}
    status, normalized, catalog_version, error = "completed", [], "", ""
    try:
        raw = fetch({}) or {}
        catalog_version = str(raw.get("catalogVersion", ""))[:100]
        normalized = [normalize_kev_entry(e)
                      for e in (raw.get("vulnerabilities") or [])]
        external_providers.record_result("cisa_kev", success=True, conn=conn)
    except Exception as exc:  # noqa: BLE001
        status, error = "failed", str(exc)[:300]
        external_providers.record_result("cisa_kev", success=False, detail=error,
                                         conn=conn)
    enrichment_policy.complete_request(decision.request_id, status=status, conn=conn)
    enrichment_policy.record_share_audit(
        provider_id="cisa_kev", capability="kev_catalog_sync", purpose=purpose,
        indicator_type="CVE", indicator_ref="kev-catalog",
        data_classes_sent=[], stripped_fields=[],
        response_status=status, conn=conn)
    if status == "failed":
        return {"ok": False, "error": error, "entries": [],
                "note": "KEV sync failed — exploited-set knowledge is STALE, not empty"}
    observation_ids = []
    for kev in normalized:
        stored = external_observations.record(
            provider_id="cisa_kev", provider_capability="kev_catalog_sync",
            indicator_type="CVE", indicator_ref=kev["cve_id"],
            finding_type="kev_known_exploited", verdict="VULNERABLE",
            severity="critical" if kev["known_ransomware"] else "high",
            confidence=0.9, provider_event_id=kev["cve_id"],
            provider_reasons=[kev["name"]] if kev["name"] else [],
            catalog_version=catalog_version,
            related_cve_ids=[kev["cve_id"]],
            response_digest=_digest(kev),
            metadata={"date_added": kev["date_added"],
                      "due_date": kev["due_date"],
                      "known_ransomware": kev["known_ransomware"]},
            conn=conn)
        observation_ids.append(stored["observation_id"])
    return {"ok": True, "entries": normalized, "catalog_version": catalog_version,
            "observation_ids": observation_ids,
            "note": "KEV membership elevates a DEPLOYED match; it never "
                    "auto-upgrades a dependency (Stage 12/31)"}


def kev_cve_ids(conn=None) -> set[str]:
    """The set of known-exploited CVE ids currently cached (fresh rows only)."""
    from services.sentinel import store
    out: set[str] = set()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT indicator_ref FROM sentinel_external_observations "
            "WHERE provider_id='cisa_kev' AND finding_type='kev_known_exploited' "
            "AND expires_at > datetime('now')")
        out = {str(r[0]).upper() for r in cur.fetchall()}
    return out
