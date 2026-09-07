"""Mission-scoped Railway operations. Secrets remain in subprocess memory/stdin.

Never run arbitrary Railway commands with credential-bearing JSON in output.
This helper refuses any project/environment/service outside this new staging stack.
It never reads production configuration, rotates an existing index key, or enables CJ.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import urlsplit, urlunsplit

PROJECT = "34d4cb5c-f3db-40bf-926e-2eaa80a91659"
ENVIRONMENT = "3a3f2632-bfc1-4ef4-b95a-e99e278d0fc1"
BACKEND = "db4ebbfb-68ea-42b0-8545-61dd075ac8b3"
WORKER = "29ca7c4b-e6e9-406d-9237-96a5f007cf38"
POSTGRES = "2c0900e4-1f72-46f5-98d5-77cf5407e5ac"
ROOT = Path(__file__).resolve().parents[1]
VAULT_NAMES = ("SUPPLIER_CREDENTIAL_KEYS", "SUPPLIER_CREDENTIAL_KEY_ACTIVE", "SUPPLIER_ACCOUNT_INDEX_KEY")


def api(query, variables=None):
    result = subprocess.run(["railway", "api", query, "--variables", "@-"],
                            input=json.dumps(variables or {}), text=True, capture_output=True,
                            cwd=ROOT, timeout=90)
    if result.returncode:
        raise RuntimeError("Railway operation failed; raw output withheld")
    data = json.loads(result.stdout)
    if data.get("errors"):
        raise RuntimeError("Railway API error; raw response withheld")
    return data["data"]


def variables(service):
    assert service in {BACKEND, WORKER, POSTGRES}
    return api("query($p:String!,$e:String!,$s:String!){variables(projectId:$p,environmentId:$e,serviceId:$s)}",
               {"p": PROJECT, "e": ENVIRONMENT, "s": service})["variables"]


def update(service, values):
    assert service in {BACKEND, WORKER}
    assert not any(name in values for name in ("CJ_API_KEY", "STRIPE_SECRET_KEY", "REDIS_URL"))
    result = api("mutation($input:VariableCollectionUpsertInput!){variableCollectionUpsert(input:$input)}",
                 {"input": {"projectId": PROJECT, "environmentId": ENVIRONMENT, "serviceId": service,
                            "skipDeploys": True, "replace": False, "variables": values}})
    assert result["variableCollectionUpsert"] is True


def provision():
    current = {service: variables(service) for service in (BACKEND, WORKER)}
    rings = [{name: values[name] for name in VAULT_NAMES if values.get(name)} for values in current.values()]
    if any(ring and len(ring) != len(VAULT_NAMES) for ring in rings):
        raise RuntimeError("Partial keyring exists; refusing an implicit rotation")
    if all(rings) and rings[0] != rings[1]:
        raise RuntimeError("Existing service keyrings differ; explicit reconciliation required")
    shared = next((ring for ring in rings if ring), None) or {
        "SUPPLIER_CREDENTIAL_KEYS": "staging-v1:" + secrets.token_hex(32),
        "SUPPLIER_CREDENTIAL_KEY_ACTIVE": "staging-v1",
        "SUPPLIER_ACCOUNT_INDEX_KEY": secrets.token_hex(32),
    }
    flags = {
        "DATABASE_URL": "${{pulsesoc-staging-postgres.DATABASE_URL}}",
        "BUSINESS_OS_SUPPLIERS_CJ": "ON", "CJ_NETWORK_ENABLED": "ON",
        "CJ_RECONCILIATION_ENABLED": "ON", "CJ_ENVIRONMENT_MODE": "SANDBOX",
        "PRODUCTION_CJ_FULFILLMENT_ENABLED": "OFF", "REAL_CJ_FUNDING_ENABLED": "OFF",
        "CJ_SUBSCRIPTION_MUTATIONS_ENABLED": "OFF", "CJ_HOSTED_CREDENTIALS_APPROVED": "OFF",
        "CJ_EGRESS_GROUP": "pulsesoc-isolated-staging-sfo-pool",
        "COINPILOTX_DISABLE_LOCAL_ENV": "1", "COINPILOTX_INIT_DB_ON_IMPORT": "0",
        "DB_POOL_SIZE": "2", "DB_MAX_OVERFLOW": "2", "DB_CONNECT_TIMEOUT_SECONDS": "10",
        "CJ_STAGING_ACCEPTANCE": "1", "WEB_CONCURRENCY": "1", "WEB_THREADS": "2",
        "SENTINEL_EXTERNAL_INTEL_ENABLED": "OFF", "PYTHONUNBUFFERED": "1",
        "PULSE_APP_URL": "https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app",
        "APP_BASE_URL": "https://pulsesoc-staging-backend-pulsesoc-cj-staging.up.railway.app",
    }
    for service in (BACKEND, WORKER):
        values = {**flags, **shared}
        if service == BACKEND:
            values["FLASK_SECRET_KEY"] = current[service].get("FLASK_SECRET_KEY") or secrets.token_hex(32)
        update(service, values)
    print(json.dumps({"provisioned": True, "services": 2, "vault_values": "withheld",
                      "existing_index_preserved": any(bool(ring) for ring in rings), "provider_approval": False}))


def verify():
    backend, worker, postgres = variables(BACKEND), variables(WORKER), variables(POSTGRES)
    for values in (backend, worker):
        assert all(values.get(name) for name in VAULT_NAMES)
        assert not values.get("CJ_API_KEY")
        assert values["DATABASE_URL"] == postgres["DATABASE_URL"]
        assert values["PRODUCTION_CJ_FULFILLMENT_ENABLED"] == "OFF"
        assert values["REAL_CJ_FUNDING_ENABLED"] == "OFF"
        assert values["CJ_HOSTED_CREDENTIALS_APPROVED"] == "OFF"
    assert all(backend[name] == worker[name] for name in VAULT_NAMES)
    print(json.dumps({"vault_configured": True, "service_keyrings_match": True,
                      "database_reference_is_staging_only": True, "production_fulfillment": False,
                      "funding": False, "global_cj_key": False, "provider_approval": False}))


def pgtest():
    values = variables(POSTGRES)
    # Explicit temporary TCP proxy created solely for this staging DB acceptance.
    # Credentials come from this staging service only, never CLI arguments/files.
    parts = urlsplit(values["DATABASE_URL"])
    userinfo = parts.netloc.rsplit("@", 1)[0]
    url = urlunsplit((parts.scheme, userinfo + "@ballast.proxy.rlwy.net:30014", parts.path, "sslmode=require", ""))
    env = dict(os.environ, CJ_ACCEPTANCE_DATABASE_URL=url, CJ_ACCEPTANCE_POSTGRES_APPROVED="1",
               COINPILOTX_DISABLE_LOCAL_ENV="1")
    result = subprocess.run([sys.executable, "scripts/verify_cj_postgres_acceptance.py"],
                            cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
    # Runner emits only a sanitized report. Redact all resolved DB values defensively.
    output = result.stdout + result.stderr
    for value in sorted(set(values.values()), key=lambda value: len(str(value)), reverse=True):
        if isinstance(value, str) and len(value) > 8:
            output = output.replace(value, "[REDACTED]")
    print(output, end="")
    return result.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("provision", "verify", "pgtest"))
    args = parser.parse_args()
    try:
        return {"provision": provision, "verify": verify, "pgtest": pgtest}[args.action]() or 0
    except Exception as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "details": "withheld"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
