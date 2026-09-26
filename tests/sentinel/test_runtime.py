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


class _PolledUnconfiguredRepository(BaseException):
    """Not an ``Exception``: ``sync_github_security`` catches those per capability.

    A trap that inherits from ``Exception`` would be swallowed into a "sync failed"
    record, turning the loudest possible failure into a status string.
    """


def _github_enabled(monkeypatch):
    monkeypatch.setenv(external_providers.MASTER_SWITCH, "1")
    monkeypatch.setenv("SENTINEL_GITHUB_SECURITY_ENABLED", "1")
    monkeypatch.setenv("SENTINEL_GITHUB_APP_TOKEN", "test-token")


def test_github_requires_explicit_repository_scope(conn, monkeypatch):
    # Supply the input rather than asking the environment what it thinks it should
    # be: an inherited value satisfies the very check this test exists to prove,
    # and the call then reaches all three capabilities for 3 results, not 1.
    monkeypatch.delenv("SENTINEL_GITHUB_REPOSITORY", raising=False)
    _github_enabled(monkeypatch)
    results = runtime.sync_github_security(conn=conn)
    assert len(results) == 1
    assert results[0].status == "degraded"
    assert "repository" in results[0].detail


def test_actions_ambient_repository_does_not_scope_sentinel(conn, monkeypatch):
    """The scope key has to stay prefixed, asserted rather than left to review.

    GitHub Actions sets a bare ``GITHUB_REPOSITORY`` on every step of every
    workflow. Sentinel used to read exactly that name, so in any job reaching
    ingestion the runner supplied a scope nobody configured and the fail-closed
    branch never ran — which is why the test above passed on every developer
    machine and failed only in CI. Reverting the read to the unprefixed name
    fails here, and fails loudly: the fetch is booby-trapped, so the failure
    names the real consequence rather than a result count.
    """
    monkeypatch.delenv("SENTINEL_GITHUB_REPOSITORY", raising=False)
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone-else/some-repo")
    _github_enabled(monkeypatch)
    monkeypatch.setattr(runtime, "github_fetch", lambda *_a, **_k: (_ for _ in ()).throw(
        _PolledUnconfiguredRepository("someone-else/some-repo")))
    results = runtime.sync_github_security(conn=conn)
    assert [item.status for item in results] == ["degraded"]
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
