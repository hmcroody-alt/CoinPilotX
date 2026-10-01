"""Production-safe Sentinel provider runtime.

This is deliberately a small orchestration layer over the existing policy,
adapter, evidence and circuit-breaker contracts.  It does not create a second
worker, never writes to a provider, and remains inert until the master and
provider kill switches are explicitly enabled in Railway.
"""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from services.sentinel import external_providers, github_security, health, store, vuln_adapters


class ProviderTransportError(RuntimeError):
    """Safe provider failure; its message must never include credentials."""


def _url(name: str, default: str) -> str:
    return str(os.getenv(name, default)).rstrip("/")


def _request_json(url: str, *, headers: dict[str, str] | None = None,
                  method: str = "GET", body: dict | None = None,
                  timeout: float = 10.0, attempts: int = 2):
    """Bounded JSON request with retry/backoff and redacted errors.

    ``url`` values are constructed from configured base URLs and validated
    identifiers; no provider-controlled URL is fetched.
    """
    headers = {"Accept": "application/json", **(headers or {})}
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    if payload is not None:
        headers["Content-Type"] = "application/json"
    last_error = "request failed"
    for attempt in range(max(1, attempts)):
        try:
            req = Request(url, data=payload, headers=headers, method=method)
            with urlopen(req, timeout=timeout) as response:  # nosec B310: bases are configuration-owned
                raw = response.read(2_000_000)
            parsed = json.loads(raw.decode("utf-8"))
            return parsed
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}"
            if exc.code not in (408, 429, 500, 502, 503, 504):
                break
        except (URLError, TimeoutError, ValueError, ProviderTransportError):
            last_error = "provider request failed"
        if attempt + 1 < max(1, attempts):
            time.sleep(min(2.0, 0.25 * (2 ** attempt)) + random.random() * 0.1)
    raise ProviderTransportError(last_error)


def osv_fetch(payload: dict) -> dict:
    return _request_json(f"{_url('OSV_API_BASE_URL', 'https://api.osv.dev/v1')}/query",
                         method="POST", body=payload)


def nvd_fetch(payload: dict) -> dict:
    cve_id = quote(str(payload.get("cveId", "")), safe="-")
    headers = {}
    if os.getenv("NVD_API_KEY"):
        headers["apiKey"] = os.environ["NVD_API_KEY"]
    return _request_json(f"{_url('NVD_API_BASE_URL', 'https://services.nvd.nist.gov/rest/json/cves/2.0')}?cveId={cve_id}",
                         headers=headers)


def kev_fetch(_payload: dict) -> dict:
    return _request_json(os.getenv(
        "CISA_KEV_FEED_URL",
        "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"))


def github_fetch(repository: str, capability: str, page: int) -> dict:
    token = os.getenv("SENTINEL_GITHUB_APP_TOKEN") or os.getenv("SENTINEL_GITHUB_FINE_GRAINED_TOKEN")
    if not token:
        raise ProviderTransportError("GitHub credential unavailable")
    paths = {
        "dependabot_alerts": "dependabot/alerts",
        "code_scanning_alerts": "code-scanning/alerts",
        "secret_scanning_alerts": "secret-scanning/alerts",
    }
    path = paths.get(capability)
    if not path:
        raise ProviderTransportError("unsupported GitHub capability")
    url = f"{_url('GITHUB_API_BASE_URL', 'https://api.github.com')}/repos/{quote(repository, safe='/')}/{path}?state=open&per_page=100&page={int(page)}"
    result = _request_json(url, headers={"Authorization": f"Bearer {token}",
                                         "X-GitHub-Api-Version": "2022-11-28"})
    if not isinstance(result, list):
        raise ProviderTransportError("GitHub returned malformed alert list")
    return result


@dataclass(frozen=True)
class RunResult:
    provider: str
    status: str
    detail: str = ""


def _record(provider: str, status: str, detail: str = "", conn=None) -> RunResult:
    health.record(health.HealthSnapshot(
        component=f"provider:{provider}", status=status.upper(),
        source_trust="MEASURED", measurement=detail[:300]), conn=conn)
    return RunResult(provider, status, detail[:300])


def sync_public_feeds(conn=None) -> list[RunResult]:
    """Refresh bounded public sources. Provider gates are checked by adapters."""
    results: list[RunResult] = []
    if external_providers.provider_enabled("cisa_kev"):
        try:
            outcome = vuln_adapters.kev_sync(fetch=kev_fetch, conn=conn)
            results.append(_record("cisa_kev", "healthy" if outcome.get("ok") else "degraded", conn=conn))
        except Exception:
            results.append(_record("cisa_kev", "degraded", "sync failed", conn=conn))
    return results


def sync_github_security(conn=None) -> list[RunResult]:
    """Poll open GitHub findings only when explicitly enabled and scoped."""
    if not external_providers.provider_enabled("github_security"):
        return []
    repository = str(os.getenv("GITHUB_REPOSITORY", "")).strip()
    if not repository:
        return [_record("github_security", "degraded", "repository not configured", conn=conn)]
    results = []
    for capability in ("dependabot_alerts", "code_scanning_alerts", "secret_scanning_alerts"):
        try:
            outcome = github_security.sync_alerts(
                capability, repository,
                fetch=lambda _payload, c=capability: github_fetch(repository, c, 1), conn=conn)
            results.append(_record("github_security", "healthy" if outcome.get("ok") else "degraded", conn=conn))
        except Exception:
            results.append(_record("github_security", "degraded", "sync failed", conn=conn))
    return results


def run_scheduled_ingestion(conn=None) -> list[RunResult]:
    """Called by the existing alert worker; provider failures stay isolated."""
    if not external_providers.master_enabled():
        return []
    external_providers.ensure_registered(conn)
    return sync_public_feeds(conn=conn) + sync_github_security(conn=conn)
