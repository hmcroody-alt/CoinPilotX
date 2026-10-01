#!/usr/bin/env python3
"""PulseSoc App Store launch readiness gate.

This gate is intentionally provider-safe by default. Without
PULSESOC_LAUNCH_BASE_URL it runs against a temporary local SQLite database and
Flask test client so it can verify routes, schema, queues, kill switches, and
static safety contracts without touching production providers.

Set PULSESOC_LAUNCH_BASE_URL=https://pulsesoc.com to add live HTTP checks.
Set PULSESOC_LAUNCH_STRICT=1 to make unverified production/manual gates fail
the command.
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
REPORT_MD = ROOT / "reports" / "pulsesoc_launch_readiness.md"
REPORT_JSON = ROOT / "reports" / "pulsesoc_launch_readiness_audit.json"
TEMP_DIR = tempfile.TemporaryDirectory(prefix="pulsesoc-launch-readiness-")
LOCAL_DB = Path(TEMP_DIR.name) / "launch_readiness.db"

if not os.getenv("PULSESOC_LAUNCH_USE_CURRENT_DB"):
    os.environ.setdefault("COINPILOTX_DISABLE_LOCAL_ENV", "1")
    os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "0")
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{LOCAL_DB}")
os.environ.setdefault("EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED", "0")
os.environ.setdefault("PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED", "0")
os.environ.setdefault("BREVO_EMAIL_ENABLED", "0")
os.environ.setdefault("PULSE_AI_ENABLED", "false")
os.environ.setdefault("SQLITE_BUSY_TIMEOUT_MS", "10000")
os.environ.setdefault("FLASK_SECRET_KEY", "launch-readiness-audit-secret")
os.environ.setdefault("SESSION_SECRET", "launch-readiness-audit-session")
os.environ.setdefault("SESSION_COOKIE_SECURE", "0")

sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from pulse_communications_v2 import models as comm_v2_models  # noqa: E402
from services import db as db_service  # noqa: E402
from services import dashboard_crypto_command_center  # noqa: E402
from services import pulse_security_core  # noqa: E402


PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
MANUAL = "MANUAL"


@dataclass
class Gate:
    category: str
    name: str
    status: str
    detail: str
    evidence: str = ""
    release_blocker: bool = False


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def read(path: str) -> str:
    target = ROOT / path
    if not target.exists():
        raise AssertionError(f"missing required file: {path}")
    return target.read_text(encoding="utf-8", errors="ignore")


def env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def gate(category: str, name: str, condition: bool, detail: str, evidence: str = "", *, release_blocker: bool = True) -> Gate:
    return Gate(category, name, PASS if condition else FAIL, detail, evidence, release_blocker=release_blocker and not condition)


def manual_gate(category: str, name: str, detail: str, evidence: str = "") -> Gate:
    return Gate(category, name, MANUAL, detail, evidence, release_blocker=True)


def warn_gate(category: str, name: str, detail: str, evidence: str = "") -> Gate:
    return Gate(category, name, WARN, detail, evidence, release_blocker=False)


def table_exists(cur, table: str) -> bool:
    return bool(bot.table_exists(cur, table))


def table_columns(cur, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    return set(str(col) for col in bot.table_columns(cur, table))


def sqlite_indexes(cur, table: str) -> set[str]:
    if not table_exists(cur, table):
        return set()
    try:
        cur.execute(f"PRAGMA index_list({table})")
        return {str(row[1]) for row in cur.fetchall()}
    except Exception:
        return set()


def ensure_launch_user() -> int:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE email=? LIMIT 1", ("launch-readiness@example.test",))
    row = cur.fetchone()
    if row:
        conn.close()
        return int(row[0])
    cur.execute(
        """
        INSERT INTO users
        (username, display_name, full_name, email, password_hash, email_verified, account_status,
         access_enabled, login_enabled, onboarding_complete, signup_time, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, 1, 'active', 1, 1, 1, ?, ?, ?)
        """,
        (
            "launch-readiness",
            "Launch Readiness",
            "Launch Readiness",
            "launch-readiness@example.test",
            bot.generate_password_hash("LaunchReady!23456"),
            now,
            now,
            now,
        ),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def client_with_user(user_id: int):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
        session["csrf_token"] = "launch-csrf"
    return client


def response_has_error(body: str) -> bool:
    markers = (
        "Traceback",
        "Internal Server Error",
        "PulseSoc hit a temporary system issue",
        "SQL_EXECUTE_FAILED",
        "upstream error",
    )
    return any(marker in body for marker in markers)


def test_client_routes() -> list[Gate]:
    user_id = ensure_launch_user()
    client = client_with_user(user_id)
    checks: list[Gate] = []
    routes = [
        ("health", "/health", {200}),
        ("homepage", "/", {200, 302}),
        ("signup", "/signup", {200, 302}),
        ("login", "/login", {200, 302}),
        ("forgot password", "/forgot-password", {200}),
        ("PulseSoc Home", "/pulse", {200}),
        ("Reels", "/pulse/reels", {200}),
        ("Messages", "/pulse/messages", {200, 302}),
        ("Notifications", "/pulse/notifications", {200}),
        ("Live Studio", "/pulse/live/studio", {200, 302}),
    ]
    for label, route, expected in routes:
        start = time.perf_counter()
        try:
            response = client.get(route)
            body = response.get_data(as_text=True)
            ok = response.status_code in expected and not response_has_error(body)
            detail = f"HTTP {response.status_code}, {round((time.perf_counter() - start) * 1000, 1)}ms"
        except Exception as exc:
            ok = False
            detail = f"raised {type(exc).__name__}: {exc}"
        checks.append(gate("route health", label, ok, detail, route))

    reset_client = bot.webhook_app.test_client()
    with reset_client.session_transaction() as session:
        session["csrf_token"] = "launch-reset-csrf"
    response = reset_client.post(
        "/forgot-password",
        data={"csrf_token": "launch-reset-csrf", "email": "nobody-launch@example.test"},
    )
    body = response.get_data(as_text=True)
    checks.append(
        gate(
            "auth stability",
            "forgot password generic response",
            response.status_code in {200, 429} and "nobody-launch@example.test" not in body and not response_has_error(body),
            f"HTTP {response.status_code}; account existence not exposed",
            "/forgot-password",
        )
    )
    return checks


def database_gates() -> list[Gate]:
    conn = db_service.connect()
    cur = conn.cursor()
    required_tables = {
        "users": {"user_id", "email", "password_hash", "account_status"},
        "sessions": {"id", "session_id", "last_seen_at"},
        "password_reset_tokens": {"user_id", "token", "expires_at"},
        "email_verification_tokens": {"user_id", "token", "expires_at"},
        "failed_email_queue": {"id", "status", "retry_count", "max_attempts", "next_retry_at", "idempotency_key"},
        "pulse_notifications": {"id", "user_id", "type", "is_read"},
        "notification_delivery_logs": {"id", "user_id", "channel", "status"},
        "push_delivery_jobs": {"id", "status", "attempts", "max_attempts", "next_retry_at", "job_id"},
        "user_device_tokens": {"id", "user_id", "push_token", "enabled"},
        "conversations": {"id"},
        "pulse_conversations": {"id"},
        "pulse_messages": {"id"},
        "comm_v2_conversations": {"id"},
        "comm_v2_messages": {"id"},
        "pulse_live_sessions": {"id", "user_id", "status"},
        "pulse_live_guest_requests": {"id", "live_id", "user_id", "status"},
        "pulse_live_viewers": {"id", "live_id"},
        "pulse_live_chat": {"id", "live_id"},
        "pulse_posts": {"id", "user_id"},
        "crypto_alerts": {"id", "user_id", "status"},
    }
    checks: list[Gate] = []
    for table, columns in required_tables.items():
        present = table_exists(cur, table)
        missing_columns = sorted(columns - table_columns(cur, table)) if present else sorted(columns)
        checks.append(
            gate(
                "database schema",
                f"{table} table",
                present and not missing_columns,
                "ready" if present and not missing_columns else f"missing columns: {', '.join(missing_columns) or 'table missing'}",
                table,
            )
        )
    index_checks = {
        "push_delivery_jobs": {"idx_push_delivery_jobs_status_retry", "idx_push_delivery_jobs_user_created"},
        "failed_email_queue": {"idx_failed_email_queue_due", "idx_failed_email_queue_idempotency"},
        "pulse_live_sessions": {"idx_pulse_live_sessions_user_status", "idx_pulse_live_sessions_discovery"},
        "pulse_live_guest_requests": {"idx_pulse_live_guest_requests_live_status", "idx_pulse_live_guest_requests_user"},
        "pulse_notifications": {"idx_pulse_notifications_user_read_created", "idx_pulse_notifications_user_created"},
    }
    for table, indexes in index_checks.items():
        available = sqlite_indexes(cur, table)
        missing = sorted(indexes - available)
        checks.append(
            gate(
                "database indexes",
                f"{table} hot indexes",
                not missing,
                "ready" if not missing else f"missing indexes: {', '.join(missing)}",
                table,
            )
        )
    sms_ready = table_exists(cur, "sms_outbox") or not env_bool("BREVO_SMS_ENABLED")
    checks.append(
        Gate(
            "queue safety",
            "SMS launch posture",
            PASS if sms_ready else FAIL,
            "SMS outbox exists or SMS provider is disabled-safe" if sms_ready else "BREVO_SMS_ENABLED is on but no sms_outbox table was found",
            "BREVO_SMS_ENABLED",
            release_blocker=not sms_ready,
        )
    )
    conn.close()
    return checks


def static_safety_gates() -> list[Gate]:
    bot_source = read("bot.py")
    push_source = read("services/push_service.py")
    notification_source = read("services/notification_service.py")
    security_source = read("services/pulse_security_core.py")
    flag_source = "\n".join(
        [
            bot_source,
            security_source,
            notification_source,
            push_source,
            read("services/premium_capability_engine.py"),
            read("services/dashboard_crypto_command_center.py"),
            read("services/command_center_client.py"),
            read("services/command_center_worker/ai_messaging.py"),
            read("services/pulse_ad_payments.py"),
            read("services/pulse_advertiser_portal.py"),
        ]
    )
    sw_source = read("static/sw.js")
    service_worker_source = read("static/service-worker.js")
    payment_provider = read("services/payment_provider.py")
    app_tsx = read("mobile/pulse-react-native/App.tsx")
    app_json = read("mobile/pulse-react-native/app.json")

    checks: list[Gate] = []
    checks.append(gate("security", "production debug mode", "debug=True" not in bot_source and "webhook_app.run(host=\"0.0.0.0\", port=PORT)" in bot_source, "Flask run path does not enable debug=True", "bot.py"))
    secret_patterns = [
        r"sk_live_[A-Za-z0-9]",
        r"STRIPE_SECRET_KEY\s*=\s*['\"][^'\"]+",
        r"BREVO_API_KEY\s*=\s*['\"][^'\"]+",
        r"OPENAI_API_KEY\s*=\s*['\"][^'\"]+",
        r"LIVEKIT_API_SECRET\s*=\s*['\"][^'\"]+",
    ]
    combined = "\n".join([bot_source, push_source, notification_source, app_tsx, app_json])
    checks.append(gate("security", "no obvious committed secrets", not any(re.search(pattern, combined) for pattern in secret_patterns), "secret-like assignment patterns were not found", "static scan"))
    checks.append(gate("security", "Stripe webhook signature", "stripe.Webhook.construct_event" in bot_source and "STRIPE_WEBHOOK_SECRET missing. Refusing unsigned live Stripe webhook" in bot_source and "verify_webhook_signature" in payment_provider, "unsigned live webhooks are rejected", "bot.py/services/payment_provider.py"))
    checks.append(gate("security", "PulseShell secrets", all(token not in app_tsx for token in ["STRIPE_SECRET", "BREVO_API_KEY", "LIVEKIT_API_SECRET", "DATABASE_URL", "FCM_PRIVATE_KEY"]), "mobile shell does not expose server secrets", "mobile/pulse-react-native/App.tsx"))
    checks.append(gate("security", "account deletion reachable", "/account/delete" in bot_source and "Permanently Delete Account" in read("templates/account.html"), "delete account UI/API are present", "templates/account.html"))
    checks.append(gate("security", "report/block reachable", "Report Profile" in bot_source and "Block User" in bot_source and "block_user" in read("pulse_communications_v2/routes.py"), "report/block surfaces remain wired", "bot.py/pulse_communications_v2/routes.py"))

    required_switches = {
        "signup": "PULSESOC_DISABLE_SIGNUP",
        "live": "PULSESOC_DISABLE_LIVE",
        "cohost": "PULSESOC_DISABLE_COHOST",
        "payments": "PULSESOC_FREEZE_PAYMENTS",
        "messaging": "PULSESOC_THROTTLE_MESSAGING",
        "uploads": "PULSESOC_DISABLE_UPLOADS",
        "premium": "PULSE_PREMIUM_DISABLED",
        "ai": "PULSE_AI_ENABLED",
        "sms": "BREVO_SMS_ENABLED",
        "email": "BREVO_EMAIL_ENABLED",
        "push": "PUSH_ASYNC_DELIVERY_ENABLED",
        "marketplace billing": "PULSE_ADS_BILLING_ENABLED",
        "crypto AI": "PULSE_CRYPTO_AI_ENABLED",
    }
    for label, env_name in required_switches.items():
        checks.append(gate("kill switches", label, env_name in flag_source, f"{env_name} is referenced", env_name, release_blocker=False))
    checks.append(gate("kill switches", "server kill switch map", set(pulse_security_core.KILL_SWITCH_ENV.values()) >= {"PULSESOC_DISABLE_SIGNUP", "PULSESOC_DISABLE_LIVE", "PULSESOC_DISABLE_COHOST", "PULSESOC_FREEZE_PAYMENTS", "PULSESOC_THROTTLE_MESSAGING", "PULSESOC_DISABLE_UPLOADS"}, "high-risk request switches are enforced in before_request", "services/pulse_security_core.py"))

    checks.append(gate("notification safety", "durable push queue", "def enqueue_push" in push_source and "def process_push_delivery_jobs" in push_source and "dead_letter" in push_source and "max_attempts" in push_source, "push uses durable queued worker with bounded retries", "services/push_service.py"))
    checks.append(gate("notification safety", "durable email queue", "enqueue_platform_email" in bot_source and "process_email_delivery_jobs" in bot_source and "dead_letter" in bot_source and "idempotency_key" in bot_source, "email uses outbox queue with bounded retries and idempotency", "bot.py"))
    checks.append(gate("notification safety", "push payload deep links", "deepLink" in sw_source and "badge" in sw_source and "sound" in push_source, "push payloads support badge/sound/deep links", "static/sw.js/services/push_service.py"))

    checks.append(gate("live safety", "Live Studio route", 'route("/pulse/live/studio"' in bot_source and 'route("/api/pulse/live/start"' in bot_source, "Studio and start API exist", "bot.py"))
    checks.append(gate("live safety", "co-host launch flag", "PULSESOC_DISABLE_COHOST" in security_source and "COHOST_DISABLED" in bot_source, "co-host can be disabled without deploy", "services/pulse_security_core.py"))
    checks.append(gate("live safety", "server-generated LiveKit tokens", "def pulse_livekit_access_token" in bot_source and 'hmac.new(config["api_secret"].encode("utf-8")' in bot_source and "PULSE_COHOST_TOKEN_CLAIMS" in bot_source, "LiveKit JWT generation/claim logging stays server-side", "bot.py"))

    cache_names = re.findall(r'CACHE_NAME\s*=\s*"([^"]+)"', sw_source + "\n" + service_worker_source)
    fresh_cache = bool(cache_names) and all("launch-readiness" in name for name in cache_names)
    checks.append(gate("performance", "service worker cache version", fresh_cache, f"cache names: {', '.join(cache_names)}", "static/sw.js/static/service-worker.js"))
    checks.append(gate("performance", "runtime JS no-store", "isRuntimeAsset" in sw_source and 'fetch(request, { cache: "no-store" })' in sw_source and "skipWaiting" in sw_source, "runtime JS/CSS fetches bypass stale cache", "static/sw.js"))
    checks.append(gate("mobile", "PulseShell App Review audit surface", "PULSESHELL_NATIVE_CALL" in app_tsx and "NSCameraUsageDescription" in app_json and "POST_NOTIFICATIONS" in app_json, "native shell has bridge and permission strings", "mobile/pulse-react-native"))

    return checks


def live_http_gates() -> list[Gate]:
    base_url = os.getenv("PULSESOC_LAUNCH_BASE_URL", "").strip().rstrip("/")
    strict = env_bool("PULSESOC_LAUNCH_STRICT")
    if not base_url:
        detail = "Set PULSESOC_LAUNCH_BASE_URL=https://pulsesoc.com and rerun before release."
        return [manual_gate("production", "production HTTP gate", detail) if strict else warn_gate("production", "production HTTP gate not run", detail)]
    routes = ["/health", "/", "/login", "/signup", "/forgot-password", "/pulse/reels", "/pulse/messages", "/pulse/notifications", "/pulse/live/studio"]
    checks: list[Gate] = []
    for route in routes:
        url = urljoin(base_url + "/", route.lstrip("/"))
        try:
            request = Request(url, headers={"User-Agent": "PulseSocLaunchReadiness/1.0"})
            start = time.perf_counter()
            with urlopen(request, timeout=8) as response:
                body = response.read(100_000).decode("utf-8", errors="ignore")
                status = int(response.status)
            checks.append(gate("production", route, status < 500 and not response_has_error(body), f"HTTP {status}, {round((time.perf_counter() - start) * 1000, 1)}ms", url))
        except HTTPError as exc:
            body = exc.read(100_000).decode("utf-8", errors="ignore")
            checks.append(gate("production", route, exc.code < 500 and not response_has_error(body), f"HTTP {exc.code}", url))
        except (URLError, TimeoutError, OSError) as exc:
            checks.append(Gate("production", route, FAIL, f"{type(exc).__name__}: {exc}", url, release_blocker=True))
    return checks


def manual_release_gates() -> list[Gate]:
    checks: list[Gate] = []
    manual_items = {
        "Railway runtime/log watch": "Set PULSESOC_RAILWAY_WATCH_VERIFIED=1 after checking restarts, memory/CPU, 5xx rate, DB locks, and tracebacks.",
        "physical iPhone/PulseShell QA": "Set PULSESOC_PHYSICAL_DEVICE_QA_VERIFIED=1 after launch, login, feed, Reels, Live Studio, push, upload, and deep-link checks pass on the approved build.",
        "App Store Connect release status": "Set PULSESOC_APP_STORE_READY=1 only when the approved version is Pending Developer Release or Ready for Distribution.",
        "provider credentials": "Set PULSESOC_PROVIDERS_READY=1 after Brevo, push, Stripe, Mux/LiveKit, and Cloudflare/Railway status are confirmed.",
        "monitoring staffing": "Set PULSESOC_RELEASE_MONITOR_READY=1 when someone is watching the 0-15m, 15-60m, 1-6h, and 24h windows.",
    }
    for name, detail in manual_items.items():
        env_name = re.search(r"Set ([A-Z0-9_]+)=1", detail)
        if env_name and env_bool(env_name.group(1)):
            checks.append(Gate("manual release", name, PASS, "verified by environment gate", env_name.group(1), release_blocker=False))
        else:
            checks.append(manual_gate("manual release", name, detail))
    return checks


def recommendation(checks: list[Gate]) -> str:
    if any(item.status == FAIL and item.release_blocker for item in checks):
        return "DO NOT RELEASE"
    if any(item.status == MANUAL and item.release_blocker for item in checks):
        return "DO NOT RELEASE"
    return "RELEASE"


def write_reports(checks: list[Gate]) -> str:
    REPORT_MD.parent.mkdir(parents=True, exist_ok=True)
    rec = recommendation(checks)
    summary = {
        "generated_at": now_iso(),
        "recommendation": rec,
        "counts": {
            "pass": sum(1 for item in checks if item.status == PASS),
            "warn": sum(1 for item in checks if item.status == WARN),
            "manual": sum(1 for item in checks if item.status == MANUAL),
            "fail": sum(1 for item in checks if item.status == FAIL),
            "release_blockers": sum(1 for item in checks if item.release_blocker),
        },
        "checks": [asdict(item) for item in checks],
        "release_step_if_green": "App Store Connect -> PulseSoc -> approved version -> Release This Version -> Confirm.",
    }
    REPORT_JSON.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    blockers = [item for item in checks if item.release_blocker]
    lines = [
        "# PulseSoc App Store Launch Readiness",
        "",
        f"- Generated: {summary['generated_at']}",
        f"- Recommendation: **{rec}**",
        f"- Passing checks: {summary['counts']['pass']}",
        f"- Warnings: {summary['counts']['warn']}",
        f"- Manual/unverified gates: {summary['counts']['manual']}",
        f"- Failed gates: {summary['counts']['fail']}",
        f"- Release blockers: {summary['counts']['release_blockers']}",
        "",
        "## Release Decision",
        "",
        "Release is allowed only when this report says RELEASE and production/manual gates are verified.",
        "",
        "## Blockers",
        "",
    ]
    if blockers:
        lines.extend(f"- **{item.category} / {item.name}**: {item.detail}" for item in blockers)
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Monitoring Plan",
            "",
            "- 0-15 minutes: Railway status, restart count, request latency, HTTP 5xx rate, signup/login/reset errors.",
            "- 15-60 minutes: DB locks, queue backlog, push/email/SMS provider errors, Live start errors.",
            "- 1-6 hours: memory/CPU, bot traffic, provider throttling, Stripe webhook retries, Cloudflare/Railway rate limits.",
            "- First 24 hours: support queue, App Store rollout metrics, crash reports, stale asset/cache complaints.",
            "",
            "## Rollback Plan",
            "",
            "- Redeploy the last known good commit from Git/Railway if crash loops or sustained 5xx appear.",
            "- Use environment kill switches: PULSESOC_DISABLE_SIGNUP, PULSESOC_DISABLE_LIVE, PULSESOC_DISABLE_COHOST, PULSESOC_FREEZE_PAYMENTS, PULSESOC_THROTTLE_MESSAGING, PULSESOC_DISABLE_UPLOADS.",
            "- Pause providers with BREVO_EMAIL_ENABLED=0, BREVO_SMS_ENABLED=0, PUSH_ASYNC_DELIVERY_ENABLED=0, EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED=0, PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED=0.",
            "- Disable risky optional layers with PULSE_AI_ENABLED=false, PULSE_CRYPTO_AI_ENABLED=false, PULSE_ADS_BILLING_ENABLED=false, PULSE_PREMIUM_DISABLED=true.",
            "- Keep /health and /health/database under watch before returning traffic to full features.",
            "",
            "## App Store Connect Step When Green",
            "",
            "App Store Connect -> PulseSoc -> approved version -> Release This Version -> Confirm.",
            "",
            "## Detailed Checks",
            "",
            "| Category | Gate | Status | Detail | Evidence |",
            "| --- | --- | --- | --- | --- |",
        ]
    )
    for item in checks:
        lines.append(f"| {item.category} | {item.name} | {item.status} | {item.detail.replace('|', '/')} | `{item.evidence}` |")
    REPORT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rec


def main() -> int:
    bot.init_db()
    conn = db_service.connect()
    cur = conn.cursor()
    comm_v2_models.ensure_schema(cur)
    conn.commit()
    dashboard_crypto_command_center.ensure_tables(conn)
    conn.close()
    checks: list[Gate] = []
    checks.extend(test_client_routes())
    checks.extend(database_gates())
    checks.extend(static_safety_gates())
    checks.extend(live_http_gates())
    checks.extend(manual_release_gates())
    rec = write_reports(checks)
    counts = {
        "pass": sum(1 for item in checks if item.status == PASS),
        "warn": sum(1 for item in checks if item.status == WARN),
        "manual": sum(1 for item in checks if item.status == MANUAL),
        "fail": sum(1 for item in checks if item.status == FAIL),
        "release_blockers": sum(1 for item in checks if item.release_blocker),
    }
    print(f"pulsesoc launch readiness: recommendation={rec} pass={counts['pass']} warn={counts['warn']} manual={counts['manual']} fail={counts['fail']} blockers={counts['release_blockers']}")
    print(f"report={REPORT_MD.relative_to(ROOT)}")
    local_failures = [item for item in checks if item.status == FAIL and item.category != "production"]
    strict_failures = [item for item in checks if item.release_blocker] if env_bool("PULSESOC_LAUNCH_STRICT") else []
    for item in local_failures + [f for f in strict_failures if f not in local_failures]:
        print(f"BLOCKER {item.category}/{item.name}: {item.detail}")
    return 1 if local_failures or strict_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
