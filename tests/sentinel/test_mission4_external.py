"""Mission 4 test suite: external threat intelligence + supply-chain security.

Fixture/mock transports only — no test opens a network connection. Covers:
adversarial cases (Stage 36), OSV/NVD/KEV correctness (Stage 37), GitHub
read-only behavior (Stage 38), privacy/minimization (Stage 39–40), and
false-positive protection (Stage 41).
"""

import json
import time

import pytest

from services.sentinel import (classification, enrichment_policy,
                               external_contracts, external_fusion,
                               external_observations, external_providers,
                               github_security, incidents, observability,
                               supply_chain, undx_interface, vuln_adapters)


def _enable(monkeypatch, *switches):
    monkeypatch.setenv(external_providers.MASTER_SWITCH, "1")
    for s in switches:
        monkeypatch.setenv(s, "1")


# --- Stage 2/3: registry + trust --------------------------------------------

class TestRegistryAndTrust:
    def test_all_kill_switches_default_off(self):
        assert not external_providers.master_enabled()
        for pid in external_providers.PROVIDERS:
            assert not external_providers.provider_enabled(pid)

    def test_registry_upsert_idempotent(self, conn):
        assert external_providers.ensure_registered(conn) == len(
            external_providers.PROVIDERS)
        assert external_providers.ensure_registered(conn) == len(
            external_providers.PROVIDERS)
        rows = external_providers.registry_health(conn)
        assert len(rows) == len(external_providers.PROVIDERS)

    def test_configured_is_not_functional(self, conn):
        external_providers.ensure_registered(conn)
        row = external_providers.provider_row("osv", conn)
        assert row["configured"] is True          # OSV needs no credentials
        assert row["health_status"] == "UNKNOWN"  # never called ≠ healthy

    def test_credentialed_provider_unconfigured_without_token(self, conn):
        spec = external_providers.PROVIDERS["github_security"]
        assert spec.configured() is False  # CONFIGURED=false, not FAILED

    def test_external_confidence_ceilings_below_internal_authority(self):
        for trust in external_providers.EXTERNAL_SOURCE_TRUST:
            assert external_providers.external_confidence_ceiling(trust) < 1.0
        assert external_providers.external_confidence_ceiling(
            "COMMUNITY_INTELLIGENCE") == 0.5

    def test_unknown_trust_fails_closed(self):
        with pytest.raises(external_providers.ExternalTrustError):
            external_providers.validate_external_trust("TOTALLY_TRUSTED")


# --- Stage 5/6: policy gate + minimization ----------------------------------

class TestEnrichmentPolicy:
    def test_default_deny_master_switch_off(self, conn):
        d = enrichment_policy.evaluate("osv", "vulnerability_query",
                                       "PACKAGE_VERSION", "PyPI:flask:2.0.0",
                                       "VULNERABILITY_TRIAGE", conn=conn)
        assert not d.allowed
        assert any("off (default)" in r for r in d.reasons)

    def test_disallowed_purpose_denied_even_when_enabled(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_OSV_ENABLED")
        for purpose in enrichment_policy.DISALLOWED_PURPOSES:
            d = enrichment_policy.evaluate("osv", "vulnerability_query",
                                           "PACKAGE_VERSION", "PyPI:flask:2.0.0",
                                           purpose, conn=conn)
            assert not d.allowed

    def test_file_upload_capability_denied_unconditionally(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_VIRUSTOTAL_ENABLED")
        monkeypatch.setenv("SENTINEL_VIRUSTOTAL_API_KEY", "k")
        for cap in enrichment_policy.FORBIDDEN_CAPABILITIES:
            d = enrichment_policy.evaluate("virustotal", cap, "FILE_HASH",
                                           "a" * 64, "THREAT_TRIAGE", conn=conn)
            assert not d.allowed
            assert "upload is disabled" in d.reasons[0]

    def test_no_provider_spec_offers_upload(self):
        for spec in external_providers.PROVIDERS.values():
            assert not (set(spec.capabilities)
                        & set(enrichment_policy.FORBIDDEN_CAPABILITIES))

    def test_unknown_provider_and_indicator_fail_closed(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_OSV_ENABLED")
        assert not enrichment_policy.evaluate(
            "shadow_provider", "x", "IP", "1.2.3.4", "THREAT_TRIAGE",
            conn=conn).allowed
        assert not enrichment_policy.evaluate(
            "osv", "vulnerability_query", "BROWSING_HISTORY", "x",
            "THREAT_TRIAGE", conn=conn).allowed

    def test_single_flight_blocks_identical_request(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_OSV_ENABLED")
        first = enrichment_policy.evaluate(
            "osv", "vulnerability_query", "PACKAGE_VERSION", "PyPI:flask:2.0.0",
            "VULNERABILITY_TRIAGE", conn=conn)
        assert first.allowed
        second = enrichment_policy.evaluate(
            "osv", "vulnerability_query", "PACKAGE_VERSION", "PyPI:flask:2.0.0",
            "VULNERABILITY_TRIAGE", conn=conn)
        assert not second.allowed
        assert "in flight" in second.reasons[0]

    def test_budget_exhaustion_denies(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_KEV_ENABLED")
        # cisa_kev budget: 2/minute.
        for _ in range(2):
            d = enrichment_policy.evaluate("cisa_kev", "kev_catalog_sync",
                                           "CVE", "kev-catalog",
                                           "VULNERABILITY_TRIAGE", conn=conn)
            assert d.allowed
            enrichment_policy.complete_request(d.request_id, conn=conn)
        d = enrichment_policy.evaluate("cisa_kev", "kev_catalog_sync", "CVE",
                                       "kev-catalog", "VULNERABILITY_TRIAGE",
                                       conn=conn)
        assert not d.allowed
        assert "budget exhausted" in d.reasons[0]

    def test_open_circuit_denies_and_unknown_not_safe(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_OSV_ENABLED")
        breaker = external_providers.load_circuit("osv", "vulnerability_query",
                                                  conn=conn)
        for _ in range(breaker.failure_threshold):
            breaker.record_failure(now=time.time())
        external_providers.save_circuit("osv", "vulnerability_query", breaker,
                                        conn=conn)
        d = enrichment_policy.evaluate(
            "osv", "vulnerability_query", "PACKAGE_VERSION", "PyPI:flask:2.0.0",
            "VULNERABILITY_TRIAGE", conn=conn)
        assert not d.allowed
        assert "UNKNOWN, not SAFE" in d.reasons[0]

    def test_minimize_strips_forbidden_fields_recursively(self):
        safe, stripped = enrichment_policy.minimize({
            "indicator": "1.2.3.4",
            "user_id": 42, "email": "a@b.c",
            "nested": {"session_token": "x", "keep": 1},
            "items": [{"api_key": "k", "ok": 2}],
        })
        assert safe == {"indicator": "1.2.3.4", "nested": {"keep": 1},
                        "items": [{"ok": 2}]}
        assert set(stripped) == {"user_id", "email", "nested.session_token",
                                 "items.api_key"}

    def test_share_audit_stores_digest_not_raw_indicator(self, conn):
        enrichment_policy.record_share_audit(
            provider_id="osv", capability="vulnerability_query",
            purpose="VULNERABILITY_TRIAGE", indicator_type="PACKAGE_VERSION",
            indicator_ref="PyPI:flask:2.0.0", data_classes_sent=["pkg"],
            stripped_fields=["email"], response_status="completed", conn=conn)
        rows = enrichment_policy.audit_rows(conn=conn)
        assert len(rows) == 1
        assert rows[0]["indicator_digest"] != "PyPI:flask:2.0.0"
        assert "flask" not in json.dumps(rows[0])
        assert rows[0]["stripped_fields"] == ["email"]


# --- Stage 4/7/23/24: observation envelope ----------------------------------

class TestObservationEnvelope:
    def test_confidence_capped_at_trust_ceiling(self, conn):
        stored = external_observations.record(
            provider_id="virustotal", provider_capability="hash_lookup",
            indicator_type="FILE_HASH", indicator_ref="a" * 64,
            finding_type="virustotal_reputation", verdict="MALICIOUS",
            severity="high", confidence=1.0, conn=conn)
        assert stored["confidence"] == 0.5  # COMMUNITY_INTELLIGENCE ceiling

    def test_secret_smuggling_in_metadata_rejected(self, conn):
        for bad in ({"api_key": "x"}, {"nested": {"session_token": 1}},
                    {"pulse_id": 9}, {"email": "a@b"}):
            with pytest.raises(external_observations.ObservationError):
                external_observations.record(
                    provider_id="osv", provider_capability="vulnerability_query",
                    indicator_type="PACKAGE_VERSION", indicator_ref="p",
                    finding_type="t", verdict="UNKNOWN", metadata=bad, conn=conn)

    def test_expired_observation_degrades_to_unknown(self, conn):
        stored = external_observations.record(
            provider_id="osv", provider_capability="vulnerability_query",
            indicator_type="PACKAGE_VERSION", indicator_ref="PyPI:flask:2.0.0",
            finding_type="osv_vulnerability", verdict="VULNERABLE",
            severity="high", confidence=0.8, conn=conn)
        conn.execute("UPDATE sentinel_external_observations SET "
                     "expires_at='2020-01-01 00:00:00' WHERE observation_id=?",
                     (stored["observation_id"],))
        row = external_observations.get(stored["observation_id"], conn=conn)
        assert row["expired"] is True
        assert row["verdict"] == "UNKNOWN"
        assert row["confidence"] == 0.0
        assert row["source_trust"] == "UNVERIFIED_EXTERNAL"
        # And an expired row is not served from cache:
        assert external_observations.cache_lookup(
            "osv", "vulnerability_query", "PACKAGE_VERSION",
            "PyPI:flask:2.0.0", conn=conn) is None

    def test_negative_result_is_cached_knowledge(self, conn):
        external_observations.record(
            provider_id="osv", provider_capability="vulnerability_query",
            indicator_type="PACKAGE_VERSION", indicator_ref="PyPI:safe:1.0",
            finding_type="osv_vulnerability", verdict="NOT_AFFECTED",
            negative_result=True, confidence=0.8, conn=conn)
        cached = external_observations.cache_lookup(
            "osv", "vulnerability_query", "PACKAGE_VERSION", "PyPI:safe:1.0",
            conn=conn)
        assert cached is not None and cached["negative_result"] is True

    def test_disagreement_preserved_never_averaged(self, conn):
        external_observations.record(
            provider_id="virustotal", provider_capability="ip_lookup",
            indicator_type="IP", indicator_ref="203.0.113.9",
            finding_type="virustotal_reputation", verdict="MALICIOUS",
            confidence=0.5, conn=conn)
        external_observations.record(
            provider_id="cloudflare_intel", provider_capability="ip_intelligence",
            indicator_type="IP", indicator_ref="203.0.113.9",
            finding_type="cloudflare_intelligence", verdict="BENIGN",
            confidence=0.6, conn=conn)
        dis = external_observations.disagreement("IP", "203.0.113.9", conn=conn)
        assert dis["disagreement"] is True
        verdicts = {p["verdict"] for p in dis["providers"].values()}
        assert verdicts == {"MALICIOUS", "BENIGN"}  # both preserved verbatim


# --- Stage 10–12: OSV / NVD / KEV -------------------------------------------

OSV_FIXTURE = {"vulns": [{
    "id": "GHSA-xxxx-yyyy-zzzz", "aliases": ["CVE-2026-11111"],
    "summary": "flask session fixation", "modified": "2026-07-01T00:00:00Z",
    "affected": [{"package": {"ecosystem": "PyPI", "name": "flask"},
                  "versions": ["2.0.0", "2.0.1"],
                  "ranges": [{"events": [{"introduced": "0"},
                                         {"fixed": "2.0.2"}]}]}],
    "database_specific": {"severity": "HIGH"},
}]}


class TestVulnAdapters:
    def test_osv_normalization_no_invented_versions(self):
        vuln = vuln_adapters.normalize_osv_entry(OSV_FIXTURE["vulns"][0])
        assert vuln["vulnerability_id"] == "GHSA-xxxx-yyyy-zzzz"
        assert vuln["aliases"] == ["CVE-2026-11111"]
        assert vuln["affected"][0]["versions"] == ["2.0.0", "2.0.1"]
        assert vuln["affected"][0]["fixed_versions"] == ["2.0.2"]
        assert vuln["severity"] == "high"

    def test_osv_query_records_observations_and_caches(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_OSV_ENABLED")
        calls = []

        def fetch(payload):
            calls.append(payload)
            return OSV_FIXTURE

        out = vuln_adapters.osv_query_package("PyPI", "flask", "2.0.0",
                                              fetch=fetch, conn=conn)
        assert out["ok"] and len(out["vulnerabilities"]) == 1
        assert len(calls) == 1
        # Second query: served from cache, transport NOT called again.
        again = vuln_adapters.osv_query_package("PyPI", "flask", "2.0.0",
                                                fetch=fetch, conn=conn)
        assert not again["ok"] and again["cached"] is not None
        assert len(calls) == 1

    def test_osv_provider_failure_is_unknown_not_safe(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_OSV_ENABLED")

        def broken(payload):
            raise ConnectionError("osv down")

        out = vuln_adapters.osv_query_package("PyPI", "flask", "2.0.0",
                                              fetch=broken, conn=conn)
        assert not out["ok"]
        assert "UNKNOWN, not SAFE" in out["note"]
        external_providers.ensure_registered(conn)
        # failure recorded as DEGRADED, not hidden
        # (row exists because record_result ran after ensure? order: record_result
        # ran before registration → assert via registry after re-run)
        vuln_adapters.osv_query_package("PyPI", "flask", "2.0.1",
                                        fetch=broken, conn=conn)
        row = external_providers.provider_row("osv", conn)
        assert row["health_status"] == "DEGRADED"

    def test_nvd_normalization_and_negative(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_NVD_ENABLED")
        fixture = {"vulnerabilities": [{"cve": {
            "id": "CVE-2026-22222", "vulnStatus": "Analyzed",
            "descriptions": [{"lang": "en", "value": "buffer overflow"}],
            "metrics": {"cvssMetricV31": [{"cvssData": {
                "baseScore": 9.8, "vectorString": "CVSS:3.1/AV:N"}}]},
            "weaknesses": [{"description": [{"value": "CWE-787"}]}],
            "published": "2026-01-01", "lastModified": "2026-02-01"}}]}
        out = vuln_adapters.nvd_enrich_cve("CVE-2026-22222",
                                           fetch=lambda p: fixture, conn=conn)
        assert out["ok"]
        assert out["cve"]["severity"] == "critical"
        assert out["cve"]["cwe_ids"] == ["CWE-787"]
        empty = vuln_adapters.nvd_enrich_cve(
            "CVE-2026-99999", fetch=lambda p: {"vulnerabilities": []}, conn=conn)
        assert empty["ok"] and empty["cve"] is None

    def test_kev_sync_and_id_set(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_KEV_ENABLED")
        fixture = {"catalogVersion": "2026.08.12", "vulnerabilities": [
            {"cveID": "CVE-2026-33333", "vendorProject": "Acme",
             "product": "Widget", "vulnerabilityName": "RCE",
             "dateAdded": "2026-08-01", "dueDate": "2026-08-22",
             "knownRansomwareCampaignUse": "Known",
             "requiredAction": "Apply updates"}]}
        out = vuln_adapters.kev_sync(fetch=lambda p: fixture, conn=conn)
        assert out["ok"] and out["entries"][0]["known_exploited"] is True
        assert vuln_adapters.kev_cve_ids(conn=conn) == {"CVE-2026-33333"}


# --- Stage 14–17: supply chain ----------------------------------------------

class TestSupplyChain:
    def test_parse_requirements_pinned_and_unpinned(self):
        rows = supply_chain.parse_requirements(
            "Flask==2.0.1\nrequests>=2.28\n# comment\n-r extra.txt\n")
        by_pkg = {r["package"]: r for r in rows}
        assert by_pkg["flask"]["version"] == "2.0.1"
        assert by_pkg["requests"]["version"] == "unpinned"  # never invented

    def test_parse_package_lock_scopes_and_direct(self):
        lock = {"packages": {
            "": {"dependencies": {"react": "^19"}, "devDependencies": {"jest": "^29"}},
            "node_modules/react": {"version": "19.0.0"},
            "node_modules/jest": {"version": "29.7.0", "dev": True},
            "node_modules/lodash": {"version": "4.17.21"}}}
        rows = supply_chain.parse_package_lock(lock)
        by_pkg = {r["package"]: r for r in rows}
        assert by_pkg["react"]["direct"] and by_pkg["react"]["scope"] == "runtime"
        assert by_pkg["jest"]["scope"] == "dev"
        assert not by_pkg["lodash"]["direct"]

    def test_real_repo_manifests_parse(self):
        import pathlib
        root = pathlib.Path(__file__).resolve().parents[2]
        reqs = supply_chain.parse_requirements(
            (root / "requirements.txt").read_text())
        assert len(reqs) > 10
        lock = supply_chain.parse_package_lock(
            (root / "mobile-native" / "package-lock.json").read_text())
        assert len(lock) > 100

    def test_applicability_states(self):
        dev = {"scope": "dev", "version": "1.0.0"}
        assert supply_chain.assess_applicability(dev)[0] == "PRESENT_IN_REPO"
        unpinned = {"scope": "runtime", "version": "unpinned"}
        assert supply_chain.assess_applicability(unpinned)[0] == "UNKNOWN"
        pinned = {"scope": "runtime", "version": "1.0.0", "source_sha": "abc"}
        assert supply_chain.assess_applicability(pinned)[0] == "PRESENT_IN_BUILD"
        assert supply_chain.assess_applicability(
            pinned, deployed_shas={"abc"})[0] == "DEPLOYED"

    def test_triage_is_explainable_and_kev_elevates(self):
        vuln = {"severity": "high", "affected": [{"fixed_versions": ["2.0.2"]}]}
        inv = {"package": "flask", "version": "2.0.0"}
        quiet = supply_chain.triage(vuln, inv, "PRESENT_IN_REPO")
        assert quiet["priority"] in ("P3", "P4")
        hot = supply_chain.triage(vuln, inv, "DEPLOYED", known_exploited=True)
        assert hot["priority"] == "P1"
        assert any("KEV" in r for r in hot["reasons"])
        assert hot["required_authority"] == "OWNER_APPROVAL"
        assert "human decision" in hot["recommended_next_step"]

    def test_version_mismatch_is_not_applicable_no_incident(self, conn):
        vuln = vuln_adapters.normalize_osv_entry(OSV_FIXTURE["vulns"][0])
        inv = {"repository": "CoinPilotX", "ecosystem": "PyPI",
               "package": "flask", "version": "3.1.0", "scope": "runtime",
               "source_sha": "abc"}
        out = supply_chain.record_finding(vuln, inv, conn=conn)
        assert out["applicability"] == "NOT_APPLICABLE"
        assert out["incident_opened"] is False
        assert incidents.get(out["incident_key"], conn=conn) is None

    def test_matching_finding_opens_deduped_incident(self, conn):
        vuln = vuln_adapters.normalize_osv_entry(OSV_FIXTURE["vulns"][0])
        inv = {"repository": "CoinPilotX", "ecosystem": "PyPI",
               "package": "flask", "version": "2.0.0", "scope": "runtime",
               "source_sha": "abc"}
        first = supply_chain.record_finding(vuln, inv, conn=conn)
        assert first["incident_opened"]
        second = supply_chain.record_finding(vuln, inv, conn=conn)
        assert second["incident_key"] == first["incident_key"]
        inc = incidents.get(first["incident_key"], conn=conn)
        assert inc["incident_type"] == "VULNERABLE_DEPENDENCY"
        assert inc["observation_count"] == 2  # deduped, counted

    def test_kev_match_on_deployed_elevates_incident(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_KEV_ENABLED")
        vuln = vuln_adapters.normalize_osv_entry(OSV_FIXTURE["vulns"][0])
        inv = {"repository": "CoinPilotX", "ecosystem": "PyPI",
               "package": "flask", "version": "2.0.0", "scope": "runtime",
               "source_sha": "abc"}
        out = supply_chain.record_finding(vuln, inv, known_exploited=True,
                                          deployed_shas={"abc"}, conn=conn)
        inc = incidents.get(out["incident_key"], conn=conn)
        assert inc["incident_type"] == "KNOWN_EXPLOITED_DEPENDENCY"
        assert inc["severity"] == "critical"
        assert inc["owner_action_required"] is True

    def test_kev_scan_elevates_existing_findings(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_KEV_ENABLED")
        vuln = vuln_adapters.normalize_osv_entry(OSV_FIXTURE["vulns"][0])
        inv = {"repository": "CoinPilotX", "ecosystem": "PyPI",
               "package": "flask", "version": "2.0.0", "scope": "runtime",
               "source_sha": "abc"}
        supply_chain.record_finding(vuln, inv, conn=conn)
        vuln_adapters.kev_sync(fetch=lambda p: {
            "catalogVersion": "v", "vulnerabilities": [
                {"cveID": "CVE-2026-11111", "vendorProject": "x",
                 "product": "y", "vulnerabilityName": "z",
                 "dateAdded": "", "dueDate": "",
                 "knownRansomwareCampaignUse": "",
                 "requiredAction": ""}]}, conn=conn)
        out = supply_chain.scan_inventory_against_kev(conn=conn)
        assert len(out["elevated_findings"]) == 1
        assert supply_chain.findings(conn=conn)[0]["known_exploited"] is True

    def test_no_auto_patching_surface_exists(self):
        for forbidden in ("upgrade_dependency", "auto_patch", "apply_fix",
                          "write_manifest", "bump_version"):
            assert not hasattr(supply_chain, forbidden)


# --- Stage 13: GitHub read-only ---------------------------------------------

class TestGitHubSecurity:
    def _enable_github(self, monkeypatch):
        _enable(monkeypatch, "SENTINEL_GITHUB_SECURITY_ENABLED")
        monkeypatch.setenv("SENTINEL_GITHUB_APP_TOKEN", "test-token")

    def test_no_mutation_functions_exist(self):
        for name in ("dismiss_alert", "update_alert", "create_fix_pr",
                     "merge_pr", "autofix", "resolve_alert", "close_alert"):
            assert not hasattr(github_security, name)

    def test_requires_credentials(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_GITHUB_SECURITY_ENABLED")
        out = github_security.sync_alerts(
            "dependabot_alerts", "pulsesoc/coinpilotx",
            fetch=lambda p: [], conn=conn)
        assert not out["ok"]
        assert any("CONFIGURED=false" in r for r in out["decision"].reasons)

    def test_dependabot_sync_opens_incidents(self, conn, monkeypatch):
        self._enable_github(monkeypatch)
        alerts = [{"number": 7, "state": "open",
                   "dependency": {"package": {"ecosystem": "npm",
                                              "name": "lodash"},
                                  "manifest_path": "package-lock.json",
                                  "scope": "runtime"},
                   "security_advisory": {"ghsa_id": "GHSA-aaaa", "cve_id":
                                         "CVE-2026-44444", "severity": "high",
                                         "summary": "prototype pollution"},
                   "security_vulnerability": {
                       "vulnerable_version_range": "< 4.17.21",
                       "first_patched_version": {"identifier": "4.17.21"}},
                   "created_at": "2026-08-01"}]
        out = github_security.sync_alerts("dependabot_alerts",
                                          "pulsesoc/coinpilotx",
                                          fetch=lambda p: alerts, conn=conn)
        assert out["ok"] and len(out["incident_keys"]) == 1
        inc = incidents.get(out["incident_keys"][0], conn=conn)
        assert inc["incident_type"] == "VULNERABLE_DEPENDENCY"

    def test_secret_scanning_never_stores_secret_values(self, conn, monkeypatch):
        self._enable_github(monkeypatch)
        alerts = [{"number": 3, "state": "open", "secret_type": "github_pat",
                   "secret_type_display_name": "GitHub Personal Access Token",
                   "validity": "active", "publicly_leaked": False,
                   "secret": "ghp_SUPERSECRETVALUE123",  # adversarial payload
                   "created_at": "2026-08-01"}]
        out = github_security.sync_alerts("secret_scanning_alerts",
                                          "pulsesoc/coinpilotx",
                                          fetch=lambda p: alerts, conn=conn)
        assert out["ok"] and len(out["incident_keys"]) == 1
        cur = conn.execute("SELECT metadata_json, provider_reasons_json FROM "
                           "sentinel_external_observations")
        blob = json.dumps(cur.fetchall())
        assert "ghp_SUPERSECRETVALUE123" not in blob
        inc = incidents.get(out["incident_keys"][0], conn=conn)
        assert inc["incident_type"] == "SECRET_EXPOSURE_FINDING"
        assert inc["owner_action_required"] is True

    def test_provenance_incidents(self, conn):
        ok = github_security.record_attestation_result(
            "pulsesoc/coinpilotx", "sha256:abc", present=True, valid=True,
            conn=conn)
        assert ok["provenance"] == "attested" and not ok["incident_key"]
        missing = github_security.record_attestation_result(
            "pulsesoc/coinpilotx", "sha256:def", present=False, conn=conn)
        inc = incidents.get(missing["incident_key"], conn=conn)
        assert inc["incident_type"] == "ARTIFACT_PROVENANCE_MISSING"
        invalid = github_security.record_attestation_result(
            "pulsesoc/coinpilotx", "sha256:ghi", present=True, valid=False,
            conn=conn)
        inc2 = incidents.get(invalid["incident_key"], conn=conn)
        assert inc2["incident_type"] == "ARTIFACT_PROVENANCE_INVALID"


# --- Stage 18–21: Cloudflare / VirusTotal / device --------------------------

class TestExternalContracts:
    def test_cloudflare_gated_and_per_indicator(self, conn, monkeypatch):
        # Denied without credentials/kill switch:
        out = external_contracts.cloudflare_enrich(
            "IP", "203.0.113.5", fetch=lambda p: {}, conn=conn)
        assert not out["ok"]
        # No bulk entry point exists:
        assert not hasattr(external_contracts, "cloudflare_enrich_batch")
        _enable(monkeypatch, "SENTINEL_CLOUDFLARE_INTEL_ENABLED")
        monkeypatch.setenv("SENTINEL_CLOUDFLARE_INTEL_TOKEN", "t")
        out = external_contracts.cloudflare_enrich(
            "IP", "203.0.113.5",
            fetch=lambda p: {"risk_types": [], "asn": 13335,
                             "asn_description": "CLOUDFLARENET"},
            conn=conn)
        assert out["ok"] and out["verdict"] == "UNKNOWN"  # hosting ASN ≠ malice

    def test_virustotal_lookup_only_no_upload(self, conn, monkeypatch):
        assert not hasattr(external_contracts, "virustotal_upload")
        assert not hasattr(external_contracts, "virustotal_submit_file")
        _enable(monkeypatch, "SENTINEL_VIRUSTOTAL_ENABLED")
        monkeypatch.setenv("SENTINEL_VIRUSTOTAL_API_KEY", "k")
        out = external_contracts.virustotal_lookup(
            "FILE_HASH", "b" * 64,
            fetch=lambda p: {"last_analysis_stats": {
                "malicious": 40, "suspicious": 2, "harmless": 20,
                "undetected": 5}},
            conn=conn)
        assert out["ok"] and out["verdict"] == "MALICIOUS"
        obs = external_observations.get(out["observation_id"], conn=conn)
        assert obs["confidence"] <= 0.5  # community ceiling

    def test_device_adapter_honest_not_configured(self, conn):
        adapter = external_contracts.DeviceIntelligenceAdapter()
        out = adapter.verify("vendor-req-1", conn=conn)
        assert not out["ok"] and out["status"] == "NOT_CONFIGURED"

    def test_device_adapter_server_verified_contract(self, conn, monkeypatch):
        _enable(monkeypatch, "SENTINEL_DEVICE_INTEL_ENABLED")
        monkeypatch.setenv("SENTINEL_DEVICE_INTEL_API_KEY", "k")
        adapter = external_contracts.DeviceIntelligenceAdapter(
            fetch=lambda p: {"signals": ["emulator"], "confidence": 0.99})
        out = adapter.verify("vendor-req-2", conn=conn)
        assert out["ok"] and out["verdict"] == "SUSPICIOUS"
        obs = external_observations.get(out["observation_id"], conn=conn)
        assert obs["confidence"] <= 0.7  # commercial ceiling


# --- Stage 22–23: fusion + external-evidence ceiling ------------------------

class TestFusion:
    def _seed_hostile(self, conn):
        external_observations.record(
            provider_id="virustotal", provider_capability="ip_lookup",
            indicator_type="IP", indicator_ref="198.51.100.7",
            finding_type="virustotal_reputation", verdict="MALICIOUS",
            confidence=0.5, conn=conn)
        external_observations.record(
            provider_id="cloudflare_intel", provider_capability="ip_intelligence",
            indicator_type="IP", indicator_ref="198.51.100.7",
            finding_type="cloudflare_intelligence", verdict="SUSPICIOUS",
            confidence=0.7, conn=conn)

    def test_external_alone_never_reaches_high_risk(self, conn):
        self._seed_hostile(conn)
        fused = external_fusion.fuse("IP", "198.51.100.7", conn=conn)
        assert fused["risk_score"] <= external_fusion.EXTERNAL_ONLY_RISK_CAP
        assert fused["risk_band"] != "HIGH"
        assert fused["enforcement"] == "NONE"
        assert any("never" in r and "HIGH_RISK" in r for r in fused["reasons"]) \
            or fused["external_score"] <= external_fusion.EXTERNAL_ONLY_RISK_CAP

    def test_internal_corroboration_can_cross_the_line(self, conn):
        self._seed_hostile(conn)
        fused = external_fusion.fuse(
            "IP", "198.51.100.7",
            internal_corroboration=[{"source": "sentinel.identity",
                                     "weight": 0.9,
                                     "reason": "credential-stuffing burst from "
                                               "this address (internal events)"}],
            conn=conn)
        assert fused["risk_score"] > external_fusion.EXTERNAL_ONLY_RISK_CAP
        assert any("internal corroboration" in r for r in fused["reasons"])

    def test_fusion_is_explainable_and_flags_disagreement(self, conn):
        external_observations.record(
            provider_id="virustotal", provider_capability="ip_lookup",
            indicator_type="IP", indicator_ref="198.51.100.8",
            finding_type="virustotal_reputation", verdict="MALICIOUS",
            confidence=0.5, conn=conn)
        external_observations.record(
            provider_id="cloudflare_intel", provider_capability="ip_intelligence",
            indicator_type="IP", indicator_ref="198.51.100.8",
            finding_type="cloudflare_intelligence", verdict="BENIGN",
            confidence=0.6, conn=conn)
        fused = external_fusion.fuse("IP", "198.51.100.8", conn=conn)
        assert fused["disagreement"] is True
        assert any("DISAGREE" in r for r in fused["reasons"])
        assert len(fused["reasons"]) >= 3  # named contributions, not a bare score

    def test_benign_consensus_scores_zero(self, conn):
        external_observations.record(
            provider_id="virustotal", provider_capability="domain_lookup",
            indicator_type="DOMAIN", indicator_ref="example.org",
            finding_type="virustotal_reputation", verdict="BENIGN",
            confidence=0.5, negative_result=True, conn=conn)
        fused = external_fusion.fuse("DOMAIN", "example.org", conn=conn)
        assert fused["risk_score"] == 0.0
        assert fused["risk_band"] == "NONE"


# --- Stage 27–29, 32: surfaces ----------------------------------------------

class TestSurfaces:
    def test_owner_summary_carries_mission4_counts(self, conn):
        out = observability.owner_summary(conn=conn)
        for key in ("known_exploited_dependencies", "deployed_vulnerabilities",
                    "repository_only_vulnerabilities", "secret_scanning_findings",
                    "code_scanning_findings", "external_threat_matches",
                    "external_provider_degradations",
                    "stale_external_intelligence", "supply_chain_status"):
            assert key in out
        assert out["known_exploited_dependencies"] == 0  # zero means zero

    def test_self_health_external_block_honest_defaults(self, conn):
        health = observability.self_health(conn=conn)
        ext = health["external_intelligence"]
        assert ext["external_intelligence_status"] == "disabled_by_kill_switch"
        assert ext["providers_functional"] == 0
        assert ext["latest_kev_sync"] is None  # never called ≠ healthy

    def test_undx_external_threat_context_read_only(self, conn):
        external_observations.record(
            provider_id="nvd", provider_capability="cve_enrichment",
            indicator_type="CVE", indicator_ref="CVE-2026-22222",
            finding_type="nvd_cve", verdict="VULNERABLE", severity="critical",
            confidence=0.9, conn=conn)
        out = undx_interface.read("external_threat_context",
                                  subject="CVE:CVE-2026-22222", conn=conn)
        assert out["ok"]
        row = out["rows"][0]
        assert row["fusion"]["risk_band"] in external_fusion.RISK_BANDS
        assert row["observations"][0]["verdict"] == "VULNERABLE"
        assert "ADVISORY" in out["authority_note"]

    def test_undx_context_rejects_unknown_indicator(self, conn):
        out = undx_interface.read("external_threat_context",
                                  subject="BROWSING:user-42", conn=conn)
        assert not out["ok"]

    def test_confidence_stays_visible_but_secrets_redacted(self, conn):
        payload = {"verdict": "MALICIOUS", "confidence": 0.5,
                   "api_key": "leak-me"}
        redacted = classification.redact(payload, classification.Level.INTERNAL)
        assert redacted["verdict"] == "MALICIOUS"
        assert redacted["api_key"] == classification.REDACTED


# --- Stage 26: deletion honesty ---------------------------------------------

class TestDeletionDesign:
    def test_no_fake_vendor_deletion_function(self):
        for name in ("mark_vendor_deleted", "confirm_vendor_deletion",
                     "delete_provider_side"):
            assert not hasattr(enrichment_policy, name)
            assert not hasattr(external_providers, name)

    def test_local_expiry_is_real(self, conn):
        stored = external_observations.record(
            provider_id="osv", provider_capability="vulnerability_query",
            indicator_type="PACKAGE_VERSION", indicator_ref="PyPI:old:1.0",
            finding_type="osv_vulnerability", verdict="VULNERABLE",
            ttl_minutes=1, confidence=0.8, conn=conn)
        assert stored["expires_at"] is not None
        conn.execute("UPDATE sentinel_external_observations SET "
                     "expires_at='2020-01-01 00:00:00' WHERE observation_id=?",
                     (stored["observation_id"],))
        assert external_observations.stale_count(conn=conn) == 1
