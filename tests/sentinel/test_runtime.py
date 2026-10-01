"""Runtime wiring tests: no provider side effects by default."""

from services.sentinel import external_providers, health, runtime


def test_scheduled_ingestion_is_inert_without_master_switch(conn, monkeypatch):
    monkeypatch.setattr(runtime, "kev_fetch", lambda _payload: (_ for _ in ()).throw(AssertionError("network")))
    assert runtime.run_scheduled_ingestion(conn=conn) == []


def test_public_kev_sync_records_measured_health(conn, monkeypatch):
    monkeypatch.setenv(external_providers.MASTER_SWITCH, "1")
    monkeypatch.setenv("SENTINEL_KEV_ENABLED", "1")
    monkeypatch.setattr(runtime, "kev_fetch", lambda _payload: {"catalogVersion": "test", "vulnerabilities": []})
    results = runtime.run_scheduled_ingestion(conn=conn)
    assert [(item.provider, item.status) for item in results] == [("cisa_kev", "healthy")]
    assert health.current("provider:cisa_kev", conn=conn)["status"] == "HEALTHY"


def test_github_requires_explicit_repository_scope(conn, monkeypatch):
    monkeypatch.setenv(external_providers.MASTER_SWITCH, "1")
    monkeypatch.setenv("SENTINEL_GITHUB_SECURITY_ENABLED", "1")
    monkeypatch.setenv("SENTINEL_GITHUB_APP_TOKEN", "test-token")
    results = runtime.sync_github_security(conn=conn)
    assert len(results) == 1
    assert results[0].status == "degraded"
    assert "repository" in results[0].detail


def test_transport_failure_never_echoes_authorization(monkeypatch):
    def boom(*_args, **_kwargs):
        raise runtime.URLError("Bearer super-secret-token")

    monkeypatch.setattr(runtime, "urlopen", boom)
    try:
        runtime._request_json("https://example.invalid", headers={"Authorization": "Bearer super-secret-token"}, attempts=1)
    except runtime.ProviderTransportError as exc:
        assert "super-secret-token" not in str(exc)
        assert str(exc) == "provider request failed"
    else:
        raise AssertionError("expected ProviderTransportError")
