"""Sentinel external-provider registry (Mission 4, Stages 2–3, 8–9, 45).

Canonical registry of every external intelligence source Sentinel may talk
to. Three rules hold everywhere:

1. **CONFIGURED != FUNCTIONAL** — a token being present proves configuration,
   never health (SC7). A provider that has never been successfully called is
   UNKNOWN, not HEALTHY.
2. **Kill switches fail closed** — every provider has its own env switch plus
   the master ``SENTINEL_EXTERNAL_INTEL_ENABLED``. Absence of a switch means
   OFF. Paid/sensitive providers additionally require credentials to even be
   CONFIGURED.
3. **External source trust describes the SOURCE, not applicability** — a
   real CVE from an authoritative source may still not apply to the deployed
   version. Trust caps confidence; it never proves guilt or exposure.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

from services.sentinel import store
from services.sentinel.providers import CircuitBreaker

PROVIDER_TYPES = (
    "THREAT_INTELLIGENCE", "VULNERABILITY_DATABASE", "SOURCE_CONTROL_SECURITY",
    "DEVICE_INTELLIGENCE", "NETWORK_INTELLIGENCE", "FILE_URL_REPUTATION",
    "ARTIFACT_PROVENANCE",
)

PROVIDER_STATUSES = (
    "CONFIGURED", "REACHABLE", "AUTHENTICATED", "FUNCTIONAL",
    "RECENTLY_PROVEN", "DEGRADED", "FAILED", "UNKNOWN", "STALE",
)

# --- External source trust (Stage 3) ---------------------------------------
# Extends the Mission 2 vocabulary for facts obtained OUTSIDE PulseSoc.
# Ceilings sit strictly below internal AUTHORITATIVE/MEASURED (1.0): an
# external claim about our platform can never outrank our own measurement.
EXTERNAL_SOURCE_TRUST = (
    "AUTHORITATIVE_GOVERNMENT",   # e.g. CISA KEV
    "AUTHORITATIVE_ECOSYSTEM",    # e.g. OSV, NVD
    "AUTHORITATIVE_REPOSITORY",   # e.g. GitHub about its own repos
    "COMMERCIAL_INTELLIGENCE",    # e.g. Cloudflare, Fingerprint, MaxMind
    "COMMUNITY_INTELLIGENCE",     # e.g. VirusTotal community verdicts
    "DERIVED_EXTERNAL",           # computed from other external observations
    "UNVERIFIED_EXTERNAL",        # provenance unclear — fail closed
)

_EXTERNAL_CONFIDENCE_CEILINGS = {
    "AUTHORITATIVE_GOVERNMENT": 0.9,
    "AUTHORITATIVE_ECOSYSTEM": 0.85,
    "AUTHORITATIVE_REPOSITORY": 0.85,
    "COMMERCIAL_INTELLIGENCE": 0.7,
    "COMMUNITY_INTELLIGENCE": 0.5,
    "DERIVED_EXTERNAL": 0.6,
    "UNVERIFIED_EXTERNAL": 0.1,
}


class ExternalTrustError(ValueError):
    """Unknown external trust class (fail closed, SC15)."""


def validate_external_trust(trust: str) -> str:
    if trust not in EXTERNAL_SOURCE_TRUST:
        raise ExternalTrustError(f"unknown external source_trust {trust!r} (SC15)")
    return trust


def external_confidence_ceiling(trust: str) -> float:
    validate_external_trust(trust)
    return _EXTERNAL_CONFIDENCE_CEILINGS[trust]


# --- Kill switches (Stage 45) ----------------------------------------------

MASTER_SWITCH = "SENTINEL_EXTERNAL_INTEL_ENABLED"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


def master_enabled() -> bool:
    """Master gate for ALL external intelligence. Default OFF."""
    from services.sentinel import killswitches
    if killswitches.emergency_killed():
        return False
    return _truthy(os.getenv(MASTER_SWITCH))


def provider_enabled(provider_id: str) -> bool:
    """Provider gate = emergency clear AND master ON AND provider switch ON.
    Every layer defaults OFF (fail closed)."""
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        return False
    if not master_enabled():
        return False
    return _truthy(os.getenv(spec.kill_switch))


# --- Budgets (Stage 9) ------------------------------------------------------

@dataclass(frozen=True)
class RequestBudget:
    max_requests_per_minute: int
    max_requests_per_hour: int
    max_requests_per_day: int
    max_concurrency: int = 2
    timeout_seconds: float = 10.0
    retry_count: int = 2
    backoff_seconds: float = 2.0
    cache_ttl_minutes: int = 24 * 60
    cooldown_minutes: int = 5
    cost_category: str = "free"

    def as_dict(self) -> dict:
        return {
            "max_requests_per_minute": self.max_requests_per_minute,
            "max_requests_per_hour": self.max_requests_per_hour,
            "max_requests_per_day": self.max_requests_per_day,
            "max_concurrency": self.max_concurrency,
            "timeout_seconds": self.timeout_seconds,
            "retry_count": self.retry_count,
            "backoff_seconds": self.backoff_seconds,
            "cache_ttl_minutes": self.cache_ttl_minutes,
            "cooldown_minutes": self.cooldown_minutes,
            "cost_category": self.cost_category,
        }


# --- Provider specs ---------------------------------------------------------

@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    provider_name: str
    provider_type: str
    capabilities: tuple[str, ...]
    source_trust: str
    kill_switch: str
    authentication_mode: str = "none"          # none | api_key | github_app
    credential_envs: tuple[str, ...] = ()      # any present → CONFIGURED
    requires_credentials: bool = False         # paid/sensitive: no cred, no config
    data_classes_allowed: tuple[str, ...] = ("PUBLIC", "INTERNAL")
    data_regions: tuple[str, ...] = ("global",)
    budget: RequestBudget = field(default_factory=lambda: RequestBudget(10, 100, 500))
    deletion_capability: str = "unknown"
    privacy_policy_version: str = "sentinel-external-v1"
    adapter_version: str = "1"

    def __post_init__(self):
        if self.provider_type not in PROVIDER_TYPES:
            raise ValueError(f"unknown provider type {self.provider_type!r} (SC15)")
        validate_external_trust(self.source_trust)

    def configured(self) -> bool:
        """Credentials present where required. NOT a health claim (SC7).

        A provider that works keyless (``requires_credentials=False``) is
        CONFIGURED even without its optional key — the key only raises the
        budget (e.g. NVD). Paid/sensitive providers need a credential."""
        has_cred = any(os.getenv(e, "").strip() for e in self.credential_envs)
        return has_cred or not self.requires_credentials


PROVIDERS: dict[str, ProviderSpec] = {p.provider_id: p for p in (
    ProviderSpec(
        provider_id="osv", provider_name="OSV.dev",
        provider_type="VULNERABILITY_DATABASE",
        capabilities=("vulnerability_query", "batch_query"),
        source_trust="AUTHORITATIVE_ECOSYSTEM",
        kill_switch="SENTINEL_OSV_ENABLED",
        budget=RequestBudget(30, 500, 2000, cache_ttl_minutes=12 * 60)),
    ProviderSpec(
        provider_id="nvd", provider_name="NVD",
        provider_type="VULNERABILITY_DATABASE",
        capabilities=("cve_enrichment",),
        source_trust="AUTHORITATIVE_GOVERNMENT",
        kill_switch="SENTINEL_NVD_ENABLED",
        authentication_mode="api_key",
        credential_envs=("SENTINEL_NVD_API_KEY",),
        # NVD works keyless at a lower rate; a key raises the budget.
        budget=RequestBudget(5, 50, 300, cache_ttl_minutes=24 * 60)),
    ProviderSpec(
        provider_id="cisa_kev", provider_name="CISA KEV",
        provider_type="VULNERABILITY_DATABASE",
        capabilities=("kev_catalog_sync",),
        source_trust="AUTHORITATIVE_GOVERNMENT",
        kill_switch="SENTINEL_KEV_ENABLED",
        budget=RequestBudget(2, 4, 8, cache_ttl_minutes=24 * 60)),
    ProviderSpec(
        provider_id="github_security", provider_name="GitHub Security",
        provider_type="SOURCE_CONTROL_SECURITY",
        capabilities=("dependabot_alerts", "code_scanning_alerts",
                      "secret_scanning_alerts", "artifact_attestations"),
        source_trust="AUTHORITATIVE_REPOSITORY",
        kill_switch="SENTINEL_GITHUB_SECURITY_ENABLED",
        authentication_mode="github_app",
        credential_envs=("SENTINEL_GITHUB_APP_TOKEN",
                         "SENTINEL_GITHUB_FINE_GRAINED_TOKEN"),
        requires_credentials=True,
        budget=RequestBudget(10, 200, 1000, cache_ttl_minutes=6 * 60)),
    ProviderSpec(
        provider_id="cloudflare_intel", provider_name="Cloudflare Intelligence",
        provider_type="NETWORK_INTELLIGENCE",
        capabilities=("ip_intelligence", "domain_intelligence",
                      "asn_intelligence", "domain_history"),
        source_trust="COMMERCIAL_INTELLIGENCE",
        kill_switch="SENTINEL_CLOUDFLARE_INTEL_ENABLED",
        authentication_mode="api_key",
        credential_envs=("SENTINEL_CLOUDFLARE_INTEL_TOKEN",),
        requires_credentials=True,
        budget=RequestBudget(5, 50, 200, cost_category="paid"),
        deletion_capability="vendor_ticket"),
    ProviderSpec(
        provider_id="virustotal", provider_name="VirusTotal",
        provider_type="FILE_URL_REPUTATION",
        capabilities=("hash_lookup", "url_lookup", "domain_lookup", "ip_lookup"),
        source_trust="COMMUNITY_INTELLIGENCE",
        kill_switch="SENTINEL_VIRUSTOTAL_ENABLED",
        authentication_mode="api_key",
        credential_envs=("SENTINEL_VIRUSTOTAL_API_KEY",),
        requires_credentials=True,
        budget=RequestBudget(4, 100, 400, cost_category="paid"),
        deletion_capability="vendor_ticket"),
    ProviderSpec(
        provider_id="device_intel", provider_name="Device Intelligence (contract)",
        provider_type="DEVICE_INTELLIGENCE",
        capabilities=("device_verify",),
        source_trust="COMMERCIAL_INTELLIGENCE",
        kill_switch="SENTINEL_DEVICE_INTEL_ENABLED",
        authentication_mode="api_key",
        credential_envs=("SENTINEL_DEVICE_INTEL_API_KEY",),
        requires_credentials=True,
        budget=RequestBudget(10, 200, 1000, cost_category="paid"),
        deletion_capability="vendor_api"),
)}


# --- Registry persistence ---------------------------------------------------

def ensure_registered(conn=None) -> int:
    """Upsert every code-defined provider into the registry table. Idempotent.
    Dynamic fields (health, last_success) are preserved on update."""
    count = 0
    with store.connection(conn) as c:
        cur = c.cursor()
        for spec in PROVIDERS.values():
            cur.execute("SELECT id FROM sentinel_external_providers WHERE provider_id=?",
                        (spec.provider_id,))
            row = cur.fetchone()
            values = (
                spec.provider_name, spec.provider_type, spec.adapter_version,
                json.dumps(list(spec.capabilities)),
                1 if spec.configured() else 0,
                1 if provider_enabled(spec.provider_id) else 0,
                spec.authentication_mode,
                json.dumps(list(spec.data_regions)),
                json.dumps(list(spec.data_classes_allowed)),
                json.dumps(spec.budget.as_dict()),
                spec.privacy_policy_version, spec.deletion_capability,
                spec.kill_switch,
            )
            if row:
                cur.execute(
                    """UPDATE sentinel_external_providers SET provider_name=?,
                       provider_type=?, adapter_version=?, capabilities_json=?,
                       configured=?, enabled=?, authentication_mode=?,
                       data_regions_json=?, data_classes_allowed_json=?,
                       request_budget_json=?, privacy_policy_version=?,
                       deletion_capability=?, kill_switch=?,
                       updated_at=datetime('now') WHERE provider_id=?""",
                    (*values, spec.provider_id))
            else:
                cur.execute(
                    """INSERT INTO sentinel_external_providers
                       (provider_id, provider_name, provider_type, adapter_version,
                        capabilities_json, configured, enabled, authentication_mode,
                        data_regions_json, data_classes_allowed_json,
                        request_budget_json, privacy_policy_version,
                        deletion_capability, kill_switch)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (spec.provider_id, *values))
            count += 1
    return count


def provider_row(provider_id: str, conn=None) -> dict | None:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT provider_id, provider_name, provider_type, capabilities_json, "
            "configured, enabled, authentication_mode, health_status, "
            "last_success_at, last_failure_at, kill_switch, deletion_capability "
            "FROM sentinel_external_providers WHERE provider_id=?", (provider_id,))
        row = cur.fetchone()
    if not row:
        return None
    return {"provider_id": row[0], "provider_name": row[1],
            "provider_type": row[2], "capabilities": json.loads(row[3] or "[]"),
            "configured": bool(row[4]), "enabled": bool(row[5]),
            "authentication_mode": row[6], "health_status": row[7],
            "last_success_at": row[8], "last_failure_at": row[9],
            "kill_switch": row[10], "deletion_capability": row[11]}


def record_result(provider_id: str, *, success: bool, detail: str = "",
                  conn=None) -> None:
    """Record a real call outcome. Health moves on MEASURED outcomes only —
    never on configuration."""
    status = "FUNCTIONAL" if success else "DEGRADED"
    col = "last_success_at" if success else "last_failure_at"
    with store.connection(conn) as c:
        c.cursor().execute(
            f"UPDATE sentinel_external_providers SET health_status=?, "
            f"{col}=datetime('now'), updated_at=datetime('now') "
            f"WHERE provider_id=?", (status, provider_id))


def registry_health(conn=None) -> list[dict]:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT provider_id, provider_type, configured, enabled, "
            "health_status, last_success_at, last_failure_at "
            "FROM sentinel_external_providers ORDER BY provider_id")
        rows = cur.fetchall()
    return [{"provider_id": r[0], "provider_type": r[1],
             "configured": bool(r[2]), "enabled": bool(r[3]),
             "health_status": r[4], "last_success_at": r[5],
             "last_failure_at": r[6]} for r in rows]


# --- Budget accounting (Stage 9) --------------------------------------------

_WINDOWS = (("minute", "-1 minutes", "max_requests_per_minute"),
            ("hour", "-1 hours", "max_requests_per_hour"),
            ("day", "-1 days", "max_requests_per_day"))


def budget_available(provider_id: str, conn=None) -> tuple[bool, str]:
    """Count real ledger rows against the spec budget. Fail closed on an
    unknown provider."""
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        return False, f"unknown provider {provider_id!r}"
    budget = spec.budget.as_dict()
    with store.connection(conn) as c:
        cur = c.cursor()
        for label, modifier, key in _WINDOWS:
            cur.execute(
                "SELECT COUNT(*) FROM sentinel_enrichment_requests "
                "WHERE provider_id=? AND requested_at >= datetime('now', ?)",
                (provider_id, modifier))
            used = int(cur.fetchone()[0])
            if used >= int(budget[key]):
                return False, f"budget exhausted: {used}/{budget[key]} per {label}"
    return True, "budget available"


# --- Persistent circuit breakers (Stage 8) ----------------------------------

def load_circuit(provider_id: str, capability: str, conn=None) -> CircuitBreaker:
    """Rehydrate the persisted breaker for one provider capability. External
    enrichment ONLY — nothing in auth/checkout consults these."""
    spec = PROVIDERS.get(provider_id)
    threshold = 5
    recovery = max(60.0, (spec.budget.cooldown_minutes * 60.0) if spec else 300.0)
    breaker = CircuitBreaker(name=f"{provider_id}:{capability}",
                             failure_threshold=threshold,
                             recovery_timeout_seconds=recovery)
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT state, failures, opened_at FROM sentinel_provider_circuits "
            "WHERE provider_id=? AND capability=?", (provider_id, capability))
        row = cur.fetchone()
    if row:
        breaker.state = str(row[0])
        breaker._failures = int(row[1])
        breaker._opened_at = float(row[2])
    return breaker


def save_circuit(provider_id: str, capability: str, breaker: CircuitBreaker,
                 conn=None) -> None:
    snap = breaker.snapshot()
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT id FROM sentinel_provider_circuits WHERE provider_id=? AND capability=?",
            (provider_id, capability))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE sentinel_provider_circuits SET state=?, failures=?, "
                "opened_at=?, updated_at=datetime('now') WHERE id=?",
                (snap["state"], snap["failures"], breaker._opened_at, int(row[0])))
        else:
            cur.execute(
                "INSERT INTO sentinel_provider_circuits "
                "(provider_id, capability, state, failures, opened_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (provider_id, capability, snap["state"], snap["failures"],
                 breaker._opened_at))


def circuit_state(provider_id: str, capability: str, conn=None) -> str:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT state FROM sentinel_provider_circuits "
            "WHERE provider_id=? AND capability=?", (provider_id, capability))
        row = cur.fetchone()
    return str(row[0]) if row else "closed"


def open_circuits(conn=None) -> list[dict]:
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT provider_id, capability, state, failures FROM "
            "sentinel_provider_circuits WHERE state != 'closed'")
        rows = cur.fetchall()
    return [{"provider_id": r[0], "capability": r[1], "state": r[2],
             "failures": int(r[3])} for r in rows]
