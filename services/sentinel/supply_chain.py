"""Supply-chain security: inventory, applicability, triage, incidents
(Mission 4, Stages 14–17).

- Stage 14: the dependency inventory is PARSED from the real manifests
  (requirements.txt, mobile-native/package-lock.json). Nothing here writes a
  manifest, upgrades a package, or invents a version.
- Stage 15: applicability states describe where a component actually lives:
  PRESENT_IN_REPO / PRESENT_IN_BUILD / DEPLOYED / NOT_DEPLOYED /
  NOT_APPLICABLE / UNKNOWN. A vulnerability in a repo-only devDependency is
  not a production fire.
- Stage 16: triage is explainable — priority + named reasons + confidence +
  recommended next step + required authority. Never an opaque number.
- Stage 17: findings become deduped incidents. A KEV match on a DEPLOYED
  component elevates severity and flags owner action; it never auto-patches
  (Stage 31).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone

from services.sentinel import incidents, store, vuln_adapters

APPLICABILITY_STATES = (
    "PRESENT_IN_REPO", "PRESENT_IN_BUILD", "DEPLOYED", "NOT_DEPLOYED",
    "NOT_APPLICABLE", "UNKNOWN",
)

PRIORITIES = ("P1", "P2", "P3", "P4", "INFO")

SENTINEL_ACTOR = "sentinel.supply_chain"

_TS = "%Y-%m-%d %H:%M:%S"

# requirement line: name[extras]==version (only pinned lines yield versions;
# unpinned lines are recorded with version 'unpinned' — never guessed).
_REQ_RE = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*"
    r"(==|>=|<=|~=|>|<|!=)?\s*([A-Za-z0-9.*+!_-]+)?")


class InventoryError(ValueError):
    pass


def _utcnow_s() -> str:
    return datetime.now(timezone.utc).strftime(_TS)


# --- Stage 14: inventory ----------------------------------------------------

def parse_requirements(text: str, *, manifest: str = "requirements.txt",
                       repository: str = "CoinPilotX",
                       service: str = "backend") -> list[dict]:
    """Parse a pip requirements file. Only what the file says: a pinned
    ``pkg==1.2.3`` yields that version; anything else is 'unpinned'."""
    out = []
    for line in (text or "").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "git+", "http")):
            continue
        m = _REQ_RE.match(line)
        if not m or not m.group(1):
            continue
        name, op, ver = m.group(1), m.group(2), m.group(3)
        version = ver if (op == "==" and ver) else "unpinned"
        out.append({"repository": repository, "manifest": manifest,
                    "ecosystem": "PyPI", "package": name.lower(),
                    "version": version, "scope": "runtime", "direct": True,
                    "service": service})
    return out


def parse_package_lock(lock_json: str | dict, *,
                       manifest: str = "mobile-native/package-lock.json",
                       repository: str = "CoinPilotX",
                       service: str = "mobile-native") -> list[dict]:
    """Parse an npm v2/v3 lockfile ``packages`` map. Exact resolved versions,
    dev flag preserved (dev-only deps are PRESENT_IN_REPO, not shipped)."""
    data = (json.loads(lock_json) if isinstance(lock_json, str) else lock_json) or {}
    packages = data.get("packages")
    if not isinstance(packages, dict):
        raise InventoryError("package-lock.json has no 'packages' map (lockfile v1?)")
    direct = set((packages.get("", {}).get("dependencies") or {}).keys()) | \
             set((packages.get("", {}).get("devDependencies") or {}).keys())
    out = []
    for path, meta in packages.items():
        if not path or not isinstance(meta, dict):
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        version = str(meta.get("version", "")).strip()
        if not name or not version:
            continue
        out.append({"repository": repository, "manifest": manifest,
                    "ecosystem": "npm", "package": name,
                    "version": version,
                    "scope": "dev" if meta.get("dev") else "runtime",
                    "direct": name in direct, "service": service})
    return out


def refresh_inventory(entries: list[dict], *, source_sha: str = "",
                      conn=None) -> dict:
    """Replace the inventory rows for each (repository, manifest) present in
    ``entries``. Reads manifests, writes ONLY the inventory table."""
    source_sha = source_sha or store.deployment_sha()
    now = _utcnow_s()
    manifests = {(e["repository"], e["manifest"]) for e in entries}
    with store.connection(conn) as c:
        cur = c.cursor()
        for repo, manifest in manifests:
            cur.execute(
                "DELETE FROM sentinel_dependency_inventory "
                "WHERE repository=? AND manifest=?", (repo, manifest))
        for e in entries:
            cur.execute(
                """INSERT OR IGNORE INTO sentinel_dependency_inventory
                   (repository, manifest, ecosystem, package, version, scope,
                    direct, service, source_sha, observed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (e["repository"], e["manifest"], e["ecosystem"], e["package"],
                 e["version"], e.get("scope", "runtime"),
                 1 if e.get("direct") else 0, e.get("service", ""),
                 source_sha, now))
    return {"ok": True, "count": len(entries),
            "manifests": sorted(f"{r}:{m}" for r, m in manifests),
            "source_sha": source_sha}


def inventory(*, ecosystem: str | None = None, package: str | None = None,
              limit: int = 500, conn=None) -> list[dict]:
    q = ("SELECT repository, manifest, ecosystem, package, version, scope, "
         "direct, service, source_sha, observed_at "
         "FROM sentinel_dependency_inventory")
    clauses, params = [], []
    if ecosystem:
        clauses.append("ecosystem=?"); params.append(ecosystem)
    if package:
        clauses.append("package=?"); params.append(package)
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY ecosystem, package LIMIT ?"
    params.append(max(1, min(int(limit), 20000)))
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(q, params)
        rows = cur.fetchall()
    return [{"repository": r[0], "manifest": r[1], "ecosystem": r[2],
             "package": r[3], "version": r[4], "scope": r[5],
             "direct": bool(r[6]), "service": r[7], "source_sha": r[8],
             "observed_at": r[9]} for r in rows]


def inventory_staleness_days(conn=None) -> float | None:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT MAX(observed_at) FROM sentinel_dependency_inventory")
        row = cur.fetchone()
    if not row or not row[0]:
        return None
    try:
        newest = datetime.strptime(str(row[0])[:19], _TS).replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - newest).total_seconds() / 86400.0


# --- Stage 15: applicability ------------------------------------------------

def assess_applicability(inv_row: dict, *, deployed_shas: set[str] | None = None) -> tuple[str, list[str]]:
    """Where does this component actually live? Honest about what Sentinel
    can and cannot know: without deployment metadata the answer is
    PRESENT_IN_BUILD/PRESENT_IN_REPO, not a guessed DEPLOYED."""
    reasons = []
    scope = str(inv_row.get("scope", "runtime"))
    if scope == "dev":
        reasons.append("dev-scope dependency: present in the repo, not shipped in builds")
        return "PRESENT_IN_REPO", reasons
    if inv_row.get("version") == "unpinned":
        reasons.append("unpinned requirement: installed version unknown — "
                       "applicability cannot be asserted (no invented versions)")
        return "UNKNOWN", reasons
    sha = str(inv_row.get("source_sha", "") or "")
    if deployed_shas and sha and sha in deployed_shas:
        reasons.append(f"manifest sha {sha[:12]} matches a deployed sha")
        return "DEPLOYED", reasons
    reasons.append("runtime-scope pinned dependency in the current tree; "
                   "deployment not independently confirmed")
    return "PRESENT_IN_BUILD", reasons


# --- Stage 16: explainable triage -------------------------------------------

def triage(vuln: dict, inv_row: dict, applicability: str,
           *, known_exploited: bool = False,
           app_reasons: list[str] | None = None) -> dict:
    """Deterministic, explainable triage. CVSS/severity is context; priority
    comes from severity × applicability × exploited-in-the-wild."""
    if applicability not in APPLICABILITY_STATES:
        raise ValueError(f"unknown applicability {applicability!r} (SC15)")
    severity = str(vuln.get("severity", "unknown"))
    reasons = list(app_reasons or [])
    reasons.append(f"provider severity: {severity}")

    live = applicability in ("DEPLOYED", "PRESENT_IN_BUILD")
    if known_exploited:
        reasons.append("listed in CISA KEV: exploited in the wild")
    if applicability == "DEPLOYED":
        reasons.append("component applicability: DEPLOYED")
    elif applicability == "PRESENT_IN_BUILD":
        reasons.append("component applicability: PRESENT_IN_BUILD "
                       "(deployment unconfirmed)")
    else:
        reasons.append(f"component applicability: {applicability} — "
                       "not a production exposure on current evidence")

    if known_exploited and live:
        priority, confidence = "P1", 0.85
    elif severity == "critical" and live:
        priority, confidence = "P2", 0.75
    elif severity == "high" and live:
        priority, confidence = "P2", 0.7
    elif severity in ("critical", "high"):
        priority, confidence = "P3", 0.6
    elif severity == "medium":
        priority, confidence = ("P3", 0.6) if live else ("P4", 0.5)
    elif applicability == "UNKNOWN":
        priority, confidence = "P3", 0.4
        reasons.append("applicability UNKNOWN is a reason to investigate, "
                       "not to dismiss")
    else:
        priority, confidence = "P4", 0.5

    fixed = [fv for aff in (vuln.get("affected") or [])
             for fv in aff.get("fixed_versions", [])]
    if fixed:
        next_step = (f"review and upgrade {inv_row.get('package')} "
                     f"{inv_row.get('version')} → {fixed[0]} (fix exists; "
                     f"upgrade is a human decision — Stage 31)")
    else:
        next_step = (f"review advisory for {inv_row.get('package')} "
                     f"{inv_row.get('version')}; no fixed version listed yet")
    return {
        "priority": priority,
        "confidence": confidence,
        "reasons": reasons,
        "recommended_next_step": next_step,
        "required_authority": "OWNER_APPROVAL" if priority in ("P1", "P2")
                              else "OWNER_REVIEW",
        "note": "priority derives from severity × applicability × KEV; "
                "no opaque scores (Stage 16)",
    }


# --- Stage 17: findings + incidents -----------------------------------------

def _matches_version(vuln: dict, inv_row: dict) -> bool:
    """True when the provider explicitly listed the installed version as
    affected, or when the provider returned this vuln for an exact-version
    query (OSV query semantics). Explicit version lists win when present."""
    version = str(inv_row.get("version", ""))
    listed = [v for aff in (vuln.get("affected") or [])
              for v in aff.get("versions", [])]
    return version in listed if listed else True


def record_finding(vuln: dict, inv_row: dict, *, observation_ids: list | None = None,
                   known_exploited: bool = False,
                   deployed_shas: set[str] | None = None,
                   environment: str = "production", conn=None) -> dict:
    """Store one vulnerability finding and open/bump its deduped incident."""
    applicability, app_reasons = assess_applicability(
        inv_row, deployed_shas=deployed_shas)
    if not _matches_version(vuln, inv_row):
        applicability = "NOT_APPLICABLE"
        app_reasons = [f"installed version {inv_row.get('version')} is not in "
                       "the provider's affected-version list"]
    verdict = triage(vuln, inv_row, applicability,
                     known_exploited=known_exploited, app_reasons=app_reasons)
    vuln_id = str(vuln.get("vulnerability_id", ""))[:100]
    fixed = [fv for aff in (vuln.get("affected") or [])
             for fv in aff.get("fixed_versions", [])]
    sha = str(inv_row.get("source_sha") or store.deployment_sha())

    incident_key = incidents.dedupe_key(
        inv_row.get("repository", "CoinPilotX"), inv_row.get("ecosystem", ""),
        inv_row.get("package", ""), vuln_id, inv_row.get("version", ""),
        sha, environment)

    finding_id = "vfind_" + uuid.uuid4().hex[:16]
    now = _utcnow_s()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_vulnerability_findings
               (finding_id, vulnerability_id, aliases_json, ecosystem, package,
                affected_version, fixed_version, repository, severity,
                known_exploited, applicability, deployment_sha, scope,
                priority, triage_reasons_json, observation_ids_json,
                incident_key, first_seen_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (finding_id, vuln_id,
             json.dumps([str(a)[:100] for a in (vuln.get("aliases") or [])[:50]]),
             inv_row.get("ecosystem", ""), inv_row.get("package", ""),
             inv_row.get("version", ""), (fixed[0] if fixed else "")[:100],
             inv_row.get("repository", "CoinPilotX"),
             str(vuln.get("severity", "unknown")),
             1 if known_exploited else 0, applicability, sha,
             inv_row.get("scope", "runtime"), verdict["priority"],
             json.dumps(verdict["reasons"]),
             json.dumps(list(observation_ids or [])[:50]),
             incident_key, now, now))

        if applicability != "NOT_APPLICABLE":
            if known_exploited and applicability in ("DEPLOYED", "PRESENT_IN_BUILD"):
                itype, severity, owner_action = ("KNOWN_EXPLOITED_DEPENDENCY",
                                                 "critical", True)
            else:
                itype = "VULNERABLE_DEPENDENCY"
                severity = {"P1": "critical", "P2": "high", "P3": "medium",
                            "P4": "low", "INFO": "info"}[verdict["priority"]]
                owner_action = verdict["priority"] in ("P1", "P2")
            incidents.open_incident(
                incident_key, itype, severity,
                f"{vuln_id} in {inv_row.get('package')} "
                f"{inv_row.get('version')} ({applicability})",
                SENTINEL_ACTOR,
                detail={"finding_id": finding_id, "priority": verdict["priority"],
                        "triage_reasons": verdict["reasons"],
                        "recommended_next_step": verdict["recommended_next_step"],
                        "required_authority": verdict["required_authority"],
                        "authority_note": "external evidence + inventory match; "
                                          "no automatic patching (Stage 31)"},
                conn=c, owner_action_required=owner_action)
    return {"ok": True, "finding_id": finding_id, "incident_key": incident_key,
            "applicability": applicability, "triage": verdict,
            "incident_opened": applicability != "NOT_APPLICABLE"}


def scan_inventory_against_kev(conn=None) -> dict:
    """Cross-reference cached OSV findings with the cached KEV set. Pure
    local computation — no network."""
    kev = vuln_adapters.kev_cve_ids(conn=conn)
    elevated = []
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT finding_id, vulnerability_id, aliases_json, incident_key "
            "FROM sentinel_vulnerability_findings WHERE known_exploited=0")
        rows = cur.fetchall()
        for finding_id, vuln_id, aliases_json, incident_key in rows:
            ids = {str(vuln_id).upper()} | {
                str(a).upper() for a in json.loads(aliases_json or "[]")}
            if ids & kev:
                cur.execute(
                    "UPDATE sentinel_vulnerability_findings "
                    "SET known_exploited=1, updated_at=? WHERE finding_id=?",
                    (_utcnow_s(), finding_id))
                if incident_key:
                    incidents.record_observation(
                        incident_key, SENTINEL_ACTOR,
                        note=f"CVE now listed in CISA KEV ({sorted(ids & kev)[0]})",
                        conn=c)
                elevated.append(finding_id)
    return {"ok": True, "elevated_findings": elevated, "kev_size": len(kev)}


def findings(*, priority: str | None = None, limit: int = 200,
             conn=None) -> list[dict]:
    q = ("SELECT finding_id, vulnerability_id, aliases_json, ecosystem, package, "
         "affected_version, fixed_version, repository, severity, known_exploited, "
         "applicability, priority, triage_reasons_json, incident_key, "
         "first_seen_at, updated_at FROM sentinel_vulnerability_findings")
    params: list = []
    if priority:
        q += " WHERE priority=?"; params.append(priority)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(q, params)
        rows = cur.fetchall()
    return [{"finding_id": r[0], "vulnerability_id": r[1],
             "aliases": json.loads(r[2] or "[]"), "ecosystem": r[3],
             "package": r[4], "affected_version": r[5], "fixed_version": r[6],
             "repository": r[7], "severity": r[8],
             "known_exploited": bool(r[9]), "applicability": r[10],
             "priority": r[11], "triage_reasons": json.loads(r[12] or "[]"),
             "incident_key": r[13], "first_seen_at": r[14],
             "updated_at": r[15]} for r in rows]


def summary_counts(conn=None) -> dict:
    """Real counts for the owner summary (Stage 28). Zero means zero."""
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) FROM sentinel_vulnerability_findings "
                    "WHERE known_exploited=1 AND applicability IN "
                    "('DEPLOYED','PRESENT_IN_BUILD')")
        kev_deployed = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM sentinel_vulnerability_findings "
                    "WHERE applicability IN ('DEPLOYED','PRESENT_IN_BUILD') "
                    "AND known_exploited=0")
        deployed = int(cur.fetchone()[0])
        cur.execute("SELECT COUNT(*) FROM sentinel_vulnerability_findings "
                    "WHERE applicability='PRESENT_IN_REPO'")
        repo_only = int(cur.fetchone()[0])
    return {"known_exploited_dependencies": kev_deployed,
            "deployed_vulnerabilities": deployed,
            "repository_only_vulnerabilities": repo_only}
