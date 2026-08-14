"""Sentinel self-metrics and self-health (Stage 28 + Stage 24 containment).

Sentinel monitors itself with the same rigor it applies to the platform: if
its own store, evidence chain, or ingest path degrades, that is reported —
never hidden (SC5). All failures here are contained: a broken metrics write
must never take down a caller, so record() swallows and reports via return
value rather than raising into hot paths (fail-safe, Stage 24).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from services.sentinel import killswitches, store

log = logging.getLogger("sentinel.observability")

METRICS = (
    "events_ingested", "events_deduped", "incidents_opened",
    "runbooks_executed", "runbooks_denied", "verifications_passed",
    "verifications_failed", "injection_flags", "invariant_violations",
)


def record(metric: str, value: float = 1.0, conn=None) -> bool:
    """Best-effort metric write. Returns False instead of raising (Stage 24)."""
    if metric not in METRICS:
        log.warning("sentinel metric rejected (unknown): %s", metric)
        return False
    try:
        with store.connection(conn) as c:
            c.cursor().execute(
                "INSERT INTO sentinel_metrics (metric, value) VALUES (?, ?)",
                (metric, float(value)))
        return True
    except Exception as exc:
        log.warning("sentinel metric write failed metric=%s error=%s", metric, exc)
        return False


def summary(hours: int = 24, conn=None) -> dict:
    hours = max(1, min(int(hours), 24 * 7))
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)
                      ).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute(
                "SELECT metric, COUNT(*), COALESCE(SUM(value), 0) FROM sentinel_metrics "
                "WHERE recorded_at >= ? GROUP BY metric",
                (cutoff,))
            rows = cur.fetchall()
        return {str(r[0]): {"count": int(r[1]), "sum": float(r[2])} for r in rows}
    except Exception as exc:
        return {"error": str(exc)[:200]}


def self_health(conn=None) -> dict:
    """Sentinel's own vital signs. Every probe is independent so one failure
    cannot mask another."""
    health: dict = {"switches": killswitches.switch_state()}
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            cur.execute("SELECT COUNT(*) FROM sentinel_events")
            health["events_stored"] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM sentinel_incidents "
                        "WHERE state NOT IN ('RESOLVED','FALSE_POSITIVE','SUPPRESSED')")
            health["open_incidents"] = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM sentinel_evidence")
            health["evidence_records"] = int(cur.fetchone()[0])
        health["store_ok"] = True
    except Exception as exc:
        health["store_ok"] = False
        health["store_error"] = str(exc)[:200]
    try:
        from services.sentinel import evidence
        chain = evidence.verify_chain(conn=conn)
        health["evidence_chain_ok"] = bool(chain["ok"])
    except Exception as exc:
        health["evidence_chain_ok"] = False
        health["evidence_error"] = str(exc)[:200]
    health["ok"] = bool(health.get("store_ok") and health.get("evidence_chain_ok"))
    # Maturity ladder (Mission 2, Stage 25): CONFIGURED means the schema
    # exists, FUNCTIONAL means the probes pass, RECENTLY_PROVEN means the
    # pipeline actually processed an event in the last 24h. Configured is
    # never presented as working (SC7).
    maturity = "CONFIGURED"
    if health["ok"]:
        maturity = "FUNCTIONAL"
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)
                      ).strftime("%Y-%m-%d %H:%M:%S")
            with store.connection(conn) as c:
                cur = c.cursor()
                cur.execute("SELECT COUNT(*) FROM sentinel_events "
                            "WHERE received_at >= ?", (cutoff,))
                if int(cur.fetchone()[0]) > 0:
                    maturity = "RECENTLY_PROVEN"
        except Exception:
            pass  # stays FUNCTIONAL — never upgrade on a failed probe
    health["maturity"] = maturity
    health["identity_detection"] = _identity_self_health(conn=conn)
    health["external_intelligence"] = _external_self_health(conn=conn)
    health["financial_defense"] = _financial_self_health(conn=conn)
    return health


def _external_self_health(conn=None) -> dict:
    """Mission 4 (Stage 32): the external-intelligence pipeline's vitals.
    A provider with no token is CONFIGURED=false — not FAILED. A provider
    never called is 'unknown' — never HEALTHY."""
    out: dict = {"external_intelligence_status": "unknown",
                 "providers_configured": 0, "providers_functional": 0,
                 "providers_degraded": 0, "latest_vulnerability_sync": None,
                 "latest_kev_sync": None, "latest_github_security_sync": None,
                 "enrichment_backlog": 0, "circuit_breakers_open": 0,
                 "stale_external_observations": 0}
    try:
        from services.sentinel import external_observations, external_providers
        rows = external_providers.registry_health(conn=conn)
        out["providers_configured"] = sum(1 for r in rows if r.get("configured"))
        out["providers_functional"] = sum(
            1 for r in rows if r.get("health_status") == "FUNCTIONAL")
        out["providers_degraded"] = sum(
            1 for r in rows if r.get("health_status") in ("DEGRADED", "FAILED"))
        with store.connection(conn) as c:
            cur = c.cursor()
            for provider, key in (("osv", "latest_vulnerability_sync"),
                                  ("cisa_kev", "latest_kev_sync"),
                                  ("github_security", "latest_github_security_sync")):
                cur.execute("SELECT MAX(fetched_at) FROM "
                            "sentinel_external_observations WHERE provider_id=?",
                            (provider,))
                out[key] = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM sentinel_enrichment_requests "
                        "WHERE status='pending'")
            out["enrichment_backlog"] = int(cur.fetchone()[0])
        out["circuit_breakers_open"] = len(
            external_providers.open_circuits(conn=conn))
        out["stale_external_observations"] = external_observations.stale_count(
            conn=conn)
        if not external_providers.master_enabled():
            out["external_intelligence_status"] = "disabled_by_kill_switch"
        elif out["providers_degraded"]:
            out["external_intelligence_status"] = "degraded"
        elif out["providers_functional"]:
            out["external_intelligence_status"] = "active"
        elif out["providers_configured"]:
            out["external_intelligence_status"] = "configured_never_proven"
        else:
            out["external_intelligence_status"] = "not_configured"
    except Exception as exc:
        out["external_intelligence_status"] = "error"
        out["error"] = str(exc)[:200]
    return out


def _identity_self_health(conn=None) -> dict:
    """Mission 3 (Stage 33): the identity pipeline's own vitals. Absence of
    signal is reported as absence — never as health."""
    out: dict = {"identity_detection_status": "unknown",
                 "latest_identity_scan": None,
                 "sequence_engine": "unknown", "risk_engine": "unknown",
                 "baseline_engine": "unknown",
                 "identity_events_evaluated_24h": 0,
                 "identity_incidents_open": 0, "scan_lag_seconds": None}
    identity_types = ("CREDENTIAL_STUFFING", "RECOVERY_ABUSE",
                      "ACCOUNT_TAKEOVER_SUSPECTED", "SESSION_ANOMALY",
                      "DEVICE_ANOMALY", "NETWORK_ANOMALY",
                      "ADMIN_IDENTITY_ANOMALY", "COORDINATED_IDENTITY_ABUSE")
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)
                      ).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("SELECT COUNT(*) FROM sentinel_events "
                        "WHERE category IN ('AUTH','SESSION','SECURITY','ADMIN') "
                        "AND received_at >= ?", (cutoff,))
            out["identity_events_evaluated_24h"] = int(cur.fetchone()[0])
            marks = ",".join("?" for _ in identity_types)
            cur.execute(f"SELECT COUNT(*) FROM sentinel_incidents "
                        f"WHERE incident_type IN ({marks}) AND state NOT IN "
                        f"('RESOLVED','FALSE_POSITIVE','SUPPRESSED')", identity_types)
            out["identity_incidents_open"] = int(cur.fetchone()[0])
            # Engine probes: schema reachable = the engine CAN run (CONFIGURED
            # is stated as such, not upgraded to working — SC7).
            cur.execute("SELECT MAX(fired_at) FROM sentinel_sequence_firings")
            out["sequence_engine"] = "ready"
            cur.execute("SELECT MAX(observed_at) FROM sentinel_identity_risk")
            latest_risk = cur.fetchone()[0]
            out["risk_engine"] = "ready"
            out["baseline_engine"] = "ready"  # pure arithmetic over events
            out["latest_identity_scan"] = latest_risk
            if latest_risk:
                try:
                    then = datetime.strptime(str(latest_risk)[:19],
                                             "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    out["scan_lag_seconds"] = int(
                        (datetime.now(timezone.utc) - then).total_seconds())
                except ValueError:
                    pass
            out["identity_detection_status"] = (
                "active" if latest_risk or out["identity_events_evaluated_24h"]
                else "configured_no_signal")
    except Exception as exc:
        out["identity_detection_status"] = "error"
        out["error"] = str(exc)[:200]
    return out


_FINANCIAL_INCIDENT_TYPES = (
    "FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED", "PAYMENT_ABUSE_SUSPECTED",
    "REFUND_ABUSE_SUSPECTED", "PAYOUT_ABUSE_SUSPECTED",
    "MARKETPLACE_ABUSE_SUSPECTED", "COORDINATED_FINANCIAL_ABUSE",
    "AD_WALLET_INTEGRITY_ANOMALY", "ADVERTISING_FINANCIAL_ANOMALY",
    "FINANCIAL_LEDGER_MISMATCH", "FINANCIAL_WEBHOOK_REPLAY",
    "DUPLICATE_ECONOMIC_EFFECT_RISK", "FINANCIAL_PROVIDER_INCONSISTENCY")


def _financial_self_health(conn=None) -> dict:
    """Mission 5 (Stage 43): the financial-defense pipeline's own vitals.
    Absence of signal is reported as absence, never as health, and a
    disabled kill switch is reported as disabled, never as broken."""
    out: dict = {"financial_detection_status": "unknown",
                 "financial_events_24h": 0,
                 "financial_incidents_open": 0,
                 "reconciliation_mismatches": 0,
                 "reconciliation_stale_or_unknown": 0,
                 "active_high_risk_entities": 0,
                 "mutation_lock_clean": None,
                 "money_movement_capability": "NONE (structurally absent)"}
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)
                      ).strftime("%Y-%m-%d %H:%M:%S")
            cur.execute("SELECT COUNT(*) FROM sentinel_events "
                        "WHERE category IN ('PAYMENT','LEDGER','SETTLEMENT',"
                        "'PAYOUT','ADVERTISING') AND received_at >= ?",
                        (cutoff,))
            out["financial_events_24h"] = int(cur.fetchone()[0])
            marks = ",".join("?" for _ in _FINANCIAL_INCIDENT_TYPES)
            cur.execute(f"SELECT COUNT(*) FROM sentinel_incidents "
                        f"WHERE incident_type IN ({marks}) AND state NOT IN "
                        f"('RESOLVED','FALSE_POSITIVE','SUPPRESSED')",
                        _FINANCIAL_INCIDENT_TYPES)
            out["financial_incidents_open"] = int(cur.fetchone()[0])
            cur.execute("SELECT status, COUNT(*) FROM "
                        "sentinel_financial_reconciliations GROUP BY status")
            for status, n in cur.fetchall():
                if status == "MISMATCH":
                    out["reconciliation_mismatches"] = int(n)
                elif status in ("STALE", "UNKNOWN"):
                    out["reconciliation_stale_or_unknown"] += int(n)
            try:
                from services.sentinel import financial_risk
                out["active_high_risk_entities"] = len(
                    financial_risk.active_high_risk(conn=c))
            except Exception:
                pass  # stays 0 — honest default
        try:
            from services.sentinel import financial_mutation_lock
            out["mutation_lock_clean"] = bool(
                financial_mutation_lock.verify_module_surface()["clean"])
        except Exception:
            out["mutation_lock_clean"] = None  # unknown, never assumed clean
        if not killswitches.financial_detection_enabled():
            out["financial_detection_status"] = "disabled_by_kill_switch"
        elif out["financial_events_24h"] or out["financial_incidents_open"]:
            out["financial_detection_status"] = "active"
        else:
            out["financial_detection_status"] = "enabled_no_signal"
    except Exception as exc:
        out["financial_detection_status"] = "error"
        out["error"] = str(exc)[:200]
    return out


def owner_summary(conn=None) -> dict:
    """The owner-facing status contract (Mission 2, Stage 18): one call that
    answers 'is the platform okay and do I need to act'. Read-only, honest
    about unknowns — absence of signal is reported as unknown, not healthy."""
    from services.sentinel import health as health_mod
    from services.sentinel import incidents, store as store_mod

    summary_out: dict = {
        "overall_status": "unknown",
        "open_incidents": 0,
        "critical_incidents": 0,
        "owner_action_required_count": 0,
        "security_status": "unknown",
        "provider_status": "unknown",
        "worker_status": "unknown",
        "deployment_status": "unknown",
        "stale_signal_count": 0,
        # Mission 3 identity contract (Stage 23) — defaults are honest zeros
        # with an unknown status until real queries fill them in.
        "identity_risk_status": "unknown",
        "suspected_account_takeovers": 0,
        "credential_stuffing_incidents": 0,
        "recovery_abuse_incidents": 0,
        "high_risk_sessions": 0,
        "admin_identity_incidents": 0,
        # Mission 4 external-intelligence / supply-chain contract (Stage 28) —
        # real counts from real queries; zero means zero.
        "supply_chain_status": "unknown",
        "known_exploited_dependencies": 0,
        "deployed_vulnerabilities": 0,
        "repository_only_vulnerabilities": 0,
        "secret_scanning_findings": 0,
        "code_scanning_findings": 0,
        "external_threat_matches": 0,
        "external_provider_degradations": 0,
        "stale_external_intelligence": 0,
        # Mission 5 financial contract (Stage 41) — honest zero/unknown
        # defaults; real queries fill them in below. SUSPECTED != GUILTY.
        "financial_risk_status": "unknown",
        "financial_incidents_open": 0,
        "suspected_financial_ato": 0,
        "payment_abuse_suspected": 0,
        "refund_abuse_suspected": 0,
        "payout_abuse_suspected": 0,
        "marketplace_abuse_suspected": 0,
        "coordinated_abuse_suspected": 0,
        "ledger_mismatch_findings": 0,
        "ad_wallet_findings": 0,
        "owner_financial_review_required": 0,
        "estimated_potential_exposure": {"currency": "usd",
                                         "potential_cents": 0,
                                         "note": "POTENTIAL only — never "
                                                 "reported as confirmed"},
        "latest_deployment_sha": store_mod.deployment_sha() or None,
        "sentinel": self_health(conn=conn),
    }
    try:
        with store.connection(conn) as c:
            cur = c.cursor()
            open_states = "','".join(
                s for s in incidents.STATES
                if s not in ("RESOLVED", "FALSE_POSITIVE", "SUPPRESSED"))
            cur.execute(f"SELECT severity, incident_type, owner_action_required "
                        f"FROM sentinel_incidents WHERE state IN ('{open_states}')")
            rows = cur.fetchall()
            summary_out["open_incidents"] = len(rows)
            summary_out["critical_incidents"] = sum(
                1 for r in rows if str(r[0]) == "critical")
            summary_out["owner_action_required_count"] = sum(
                1 for r in rows if r[2])
            sec_types = {"SECURITY_INTRUSION", "ACCOUNT_TAKEOVER", "ABUSE",
                         "DATA_EXPOSURE", "AI_SAFETY"}
            sec_open = [r for r in rows if str(r[1]) in sec_types]
            summary_out["security_status"] = ("attention" if sec_open else "quiet")
            prov_open = [r for r in rows if str(r[1]) == "PROVIDER_OUTAGE"]

            # Provider status from recorded capabilities, not vibes.
            cur.execute("SELECT COUNT(*) FROM sentinel_provider_capabilities "
                        "WHERE status IN ('down','degraded')")
            degraded = int(cur.fetchone()[0])
            cur.execute("SELECT COUNT(*) FROM sentinel_provider_capabilities")
            total_caps = int(cur.fetchone()[0])
            if total_caps == 0:
                summary_out["provider_status"] = "unknown"  # no signal ≠ healthy
            elif degraded or prov_open:
                summary_out["provider_status"] = "degraded"
            else:
                summary_out["provider_status"] = "ok"

            # Worker + deployment status from health snapshots (freshness-aware).
            for prefix, key in (("worker:", "worker_status"),
                                ("deployment:", "deployment_status")):
                cur.execute("SELECT component FROM sentinel_health_snapshots "
                            "WHERE component LIKE ? GROUP BY component",
                            (prefix + "%",))
                components = [str(r[0]) for r in cur.fetchall()]
                if not components:
                    summary_out[key] = "unknown"
                    continue
                statuses = {health_mod.current(comp, conn=c)["status"]
                            for comp in components}
                if statuses & {"FAILED", "DEGRADED"}:
                    summary_out[key] = "degraded"
                elif statuses <= {"HEALTHY", "RECOVERING"}:
                    summary_out[key] = "ok"
                else:
                    summary_out[key] = "unknown"

            summary_out["stale_signal_count"] = health_mod.stale_count(conn=c)

            # Mission 3 (Stage 23): identity risk fields — REAL counts from
            # real queries; a field with no signal stays 0/unknown, it is
            # never invented.
            def _count_open(itype: str) -> int:
                cur.execute(
                    f"SELECT COUNT(*) FROM sentinel_incidents WHERE "
                    f"incident_type = ? AND state IN ('{open_states}')", (itype,))
                return int(cur.fetchone()[0])
            ato = _count_open("ACCOUNT_TAKEOVER_SUSPECTED")
            stuffing = _count_open("CREDENTIAL_STUFFING")
            recovery = _count_open("RECOVERY_ABUSE")
            admin_id_inc = (_count_open("ADMIN_IDENTITY_ANOMALY"))
            summary_out["suspected_account_takeovers"] = ato
            summary_out["credential_stuffing_incidents"] = stuffing
            summary_out["recovery_abuse_incidents"] = recovery
            summary_out["admin_identity_incidents"] = admin_id_inc
            try:
                from services.sentinel import identity_trust
                summary_out["high_risk_sessions"] = len(
                    identity_trust.active_high_risk(conn=c))
            except Exception:
                summary_out["high_risk_sessions"] = 0
            if ato or stuffing or admin_id_inc:
                summary_out["identity_risk_status"] = "attention"
            elif recovery or summary_out["high_risk_sessions"]:
                summary_out["identity_risk_status"] = "watch"
            else:
                summary_out["identity_risk_status"] = "quiet"

            # Mission 4 (Stage 28): supply-chain + external-intelligence counts.
            try:
                from services.sentinel import external_observations, supply_chain
                summary_out.update(supply_chain.summary_counts(conn=c))
                summary_out["secret_scanning_findings"] = _count_open(
                    "SECRET_EXPOSURE_FINDING")
                summary_out["code_scanning_findings"] = _count_open(
                    "CODE_SCANNING_FINDING")
                cur.execute(
                    "SELECT COUNT(*) FROM sentinel_external_observations "
                    "WHERE verdict IN ('MALICIOUS','SUSPICIOUS') "
                    "AND expires_at > datetime('now')")
                summary_out["external_threat_matches"] = int(cur.fetchone()[0])
                cur.execute(
                    "SELECT COUNT(*) FROM sentinel_external_providers "
                    "WHERE health_status IN ('DEGRADED','FAILED')")
                summary_out["external_provider_degradations"] = int(
                    cur.fetchone()[0])
                summary_out["stale_external_intelligence"] = (
                    external_observations.stale_count(conn=c))
                if summary_out["known_exploited_dependencies"]:
                    summary_out["supply_chain_status"] = "attention"
                elif (summary_out["deployed_vulnerabilities"]
                      or summary_out["secret_scanning_findings"]):
                    summary_out["supply_chain_status"] = "watch"
                else:
                    summary_out["supply_chain_status"] = "quiet"
            except Exception:
                pass  # fields keep their honest unknown/zero defaults

            # Mission 5 (Stage 41): financial fraud counts — REAL queries,
            # honest defaults on failure, and no field ever conflates
            # POTENTIAL exposure with CONFIRMED loss.
            try:
                fin_ato = _count_open("FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED")
                pay_abuse = _count_open("PAYMENT_ABUSE_SUSPECTED")
                ref_abuse = _count_open("REFUND_ABUSE_SUSPECTED")
                po_abuse = _count_open("PAYOUT_ABUSE_SUSPECTED")
                mkt_abuse = _count_open("MARKETPLACE_ABUSE_SUSPECTED")
                coord = _count_open("COORDINATED_FINANCIAL_ABUSE")
                ledger = (_count_open("FINANCIAL_LEDGER_MISMATCH")
                          + _count_open("DUPLICATE_ECONOMIC_EFFECT_RISK")
                          + _count_open("FINANCIAL_WEBHOOK_REPLAY")
                          + _count_open("FINANCIAL_PROVIDER_INCONSISTENCY"))
                ad_wallet = (_count_open("AD_WALLET_INTEGRITY_ANOMALY")
                             + _count_open("ADVERTISING_FINANCIAL_ANOMALY"))
                summary_out["suspected_financial_ato"] = fin_ato
                summary_out["payment_abuse_suspected"] = pay_abuse
                summary_out["refund_abuse_suspected"] = ref_abuse
                summary_out["payout_abuse_suspected"] = po_abuse
                summary_out["marketplace_abuse_suspected"] = mkt_abuse
                summary_out["coordinated_abuse_suspected"] = coord
                summary_out["ledger_mismatch_findings"] = ledger
                summary_out["ad_wallet_findings"] = ad_wallet
                fin_open = (fin_ato + pay_abuse + ref_abuse + po_abuse
                            + mkt_abuse + coord + ledger + ad_wallet)
                summary_out["financial_incidents_open"] = fin_open
                marks = ",".join("?" for _ in _FINANCIAL_INCIDENT_TYPES)
                cur.execute(
                    f"SELECT COUNT(*) FROM sentinel_incidents WHERE "
                    f"incident_type IN ({marks}) AND owner_action_required = 1 "
                    f"AND state IN ('{open_states}')",
                    _FINANCIAL_INCIDENT_TYPES)
                summary_out["owner_financial_review_required"] = int(
                    cur.fetchone()[0])
                from services.sentinel import financial_exposure
                exp = financial_exposure.totals(conn=c)
                summary_out["estimated_potential_exposure"] = {
                    "currency": "usd",
                    "potential_cents": int(exp.get("potential_cents") or 0),
                    "confirmed_cents": int(exp.get("confirmed_cents") or 0),
                    "disputed_cents": int(exp.get("disputed_cents") or 0),
                    "unknown_items": int(exp.get("unknown_items") or 0),
                    "note": "classes kept apart — potential is never "
                            "reported as confirmed"}
                if fin_ato or coord or ledger:
                    summary_out["financial_risk_status"] = "attention"
                elif fin_open:
                    summary_out["financial_risk_status"] = "watch"
                else:
                    summary_out["financial_risk_status"] = "quiet"
            except Exception:
                pass  # fields keep their honest unknown/zero defaults
    except Exception as exc:
        summary_out["error"] = str(exc)[:200]

    if not summary_out["sentinel"].get("ok"):
        summary_out["overall_status"] = "sentinel_impaired"
    elif summary_out["critical_incidents"]:
        summary_out["overall_status"] = "critical"
    elif summary_out["open_incidents"]:
        summary_out["overall_status"] = "attention"
    else:
        summary_out["overall_status"] = "ok"
    return summary_out
