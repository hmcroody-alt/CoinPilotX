#!/usr/bin/env python3
"""Provider-safe PulseSoc launch load smoke.

This script uses a temporary local database by default and creates one test
user. It exercises concurrent health/feed/auth/reset/notification/co-host
paths without sending email, SMS, push, Stripe, Mux, LiveKit, or Brevo calls.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT_JSON = ROOT / "reports" / "pulsesoc_launch_load_smoke.json"
TEMP_DIR = tempfile.TemporaryDirectory(prefix="pulsesoc-launch-load-")
LOCAL_DB = Path(TEMP_DIR.name) / "launch_load.db"

os.environ.setdefault("COINPILOTX_DISABLE_LOCAL_ENV", "1")
os.environ.setdefault("COINPILOTX_INIT_DB_ON_IMPORT", "0")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{LOCAL_DB}")
os.environ.setdefault("EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED", "0")
os.environ.setdefault("PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED", "0")
os.environ.setdefault("BREVO_EMAIL_ENABLED", "0")
os.environ.setdefault("PULSE_AI_ENABLED", "false")
os.environ.setdefault("PULSESOC_DISABLE_COHOST", "1")
os.environ.setdefault("SQLITE_BUSY_TIMEOUT_MS", "10000")
os.environ.setdefault("FLASK_SECRET_KEY", "launch-load-smoke-secret")
os.environ.setdefault("SESSION_SECRET", "launch-load-smoke-session")
os.environ.setdefault("SESSION_COOKIE_SECURE", "0")

sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import db as db_service  # noqa: E402


@dataclass
class SmokeResult:
    name: str
    ok: bool
    status_code: int
    elapsed_ms: float
    detail: str


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ensure_user() -> int:
    now = now_iso()
    conn = db_service.connect()
    cur = conn.cursor()
    cur.execute("SELECT user_id FROM users WHERE email=? LIMIT 1", ("launch-load@example.test",))
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
            "launch-load",
            "Launch Load",
            "Launch Load",
            "launch-load@example.test",
            bot.generate_password_hash("LaunchLoad!23456"),
            now,
            now,
            now,
        ),
    )
    user_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return user_id


def client(user_id: int | None = None):
    c = bot.webhook_app.test_client()
    with c.session_transaction() as session:
        session["csrf_token"] = "load-csrf"
        if user_id:
            session["account_user_id"] = user_id
    return c


def body_has_error(text: str) -> bool:
    return any(marker in text for marker in ("Traceback", "Internal Server Error", "SQL_EXECUTE_FAILED", "upstream error"))


def timed(name: str, fn) -> SmokeResult:
    start = time.perf_counter()
    try:
        status, detail = fn()
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        ok = status < 500
        return SmokeResult(name, ok, status, elapsed, detail)
    except Exception as exc:
        elapsed = round((time.perf_counter() - start) * 1000, 2)
        return SmokeResult(name, False, 0, elapsed, f"{type(exc).__name__}: {exc}")


def health_check() -> tuple[int, str]:
    r = bot.webhook_app.test_client().get("/health")
    return int(r.status_code), "/health"


def feed_load(route: str, user_id: int) -> tuple[int, str]:
    r = client(user_id).get(route)
    text = r.get_data(as_text=True)
    if body_has_error(text):
        return 500, f"{route} error marker"
    return int(r.status_code), route


def login_attempt(index: int) -> tuple[int, str]:
    c = client()
    password = "LaunchLoad!23456" if index % 2 == 0 else "WrongPassword!23456"
    r = c.post(
        "/login",
        data={
            "csrf_token": "load-csrf",
            "email": "launch-load@example.test",
            "password": password,
            "terms_accepted": "true",
        },
        follow_redirects=False,
    )
    return int(r.status_code), "login valid" if index % 2 == 0 else "login invalid"


def reset_request(index: int) -> tuple[int, str]:
    c = client()
    email = "launch-load@example.test" if index % 2 == 0 else f"missing-{index}@example.test"
    r = c.post("/forgot-password", data={"csrf_token": "load-csrf", "email": email})
    text = r.get_data(as_text=True)
    if email not in {"launch-load@example.test"} and email in text:
        return 500, "reset leaked account probe email"
    return int(r.status_code), "forgot-password generic"


def queue_notification(user_id: int, index: int) -> tuple[int, str]:
    conn = db_service.connect()
    cur = conn.cursor()
    try:
        bot.notify_user(
            cur,
            user_id,
            "launch_smoke",
            "Launch smoke",
            f"Launch queue smoke {index}",
            "/pulse/notifications",
            actor_user_id=user_id,
            entity_type="launch_smoke",
            entity_id=str(index),
            metadata={"trace": secrets.token_hex(4), "index": index},
        )
        conn.commit()
        return 200, "notification queued"
    finally:
        conn.close()


def cohost_disabled_dry_run(user_id: int) -> tuple[int, str]:
    c = client(user_id)
    r = c.post(
        "/api/pulse/live/1/cohost/request",
        json={"trace_id": "launch-smoke-cohost", "camera_ready": True, "mic_ready": True},
        headers={"X-Trace-Id": "launch-smoke-cohost"},
    )
    data = r.get_json(silent=True) or {}
    if r.status_code == 503 and data.get("error_code") == "COHOST_DISABLED":
        return 200, "co-host kill switch returned safe disabled state"
    return int(r.status_code), f"unexpected co-host response: {data}"


def run() -> list[SmokeResult]:
    bot.init_db()
    user_id = ensure_user()
    work = []
    work.extend((f"health-{i}", lambda i=i: health_check()) for i in range(16))
    for route in ("/pulse", "/pulse/reels", "/pulse/messages", "/pulse/notifications", "/pulse/live/studio"):
        work.extend((f"feed-{route}-{i}", lambda route=route: feed_load(route, user_id)) for i in range(4))
    work.extend((f"login-{i}", lambda i=i: login_attempt(i)) for i in range(8))
    work.extend((f"reset-{i}", lambda i=i: reset_request(i)) for i in range(8))
    work.extend((f"notify-{i}", lambda i=i: queue_notification(user_id, i)) for i in range(12))
    work.append(("cohost-disabled", lambda: cohost_disabled_dry_run(user_id)))

    results: list[SmokeResult] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(timed, name, fn) for name, fn in work]
        for future in as_completed(futures):
            results.append(future.result())
    return sorted(results, key=lambda item: item.name)


def main() -> int:
    results = run()
    failures = [item for item in results if not item.ok or item.elapsed_ms > 10_000]
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(
        json.dumps(
            {
                "generated_at": now_iso(),
                "total": len(results),
                "failures": len(failures),
                "max_elapsed_ms": max((item.elapsed_ms for item in results), default=0),
                "results": [asdict(item) for item in results],
                "provider_safe": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"pulsesoc launch load smoke: total={len(results)} failures={len(failures)} max_ms={max((item.elapsed_ms for item in results), default=0)}")
    print(f"report={REPORT_JSON.relative_to(ROOT)}")
    for item in failures:
        print(f"FAIL {item.name}: status={item.status_code} elapsed_ms={item.elapsed_ms} detail={item.detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
