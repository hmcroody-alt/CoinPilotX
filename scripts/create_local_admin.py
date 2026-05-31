#!/usr/bin/env python3
"""Create a localhost-only development admin account.

This helper is intentionally guarded so it can only run in local/development
mode against the local SQLite database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import os
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from werkzeug.security import generate_password_hash  # noqa: E402


LOCAL_EMAIL = "localadmin@coinpilotx.test"
LOCAL_PASSWORD = os.getenv("COINPILOTX_LOCAL_ADMIN_PASSWORD", "TempLocal123!")
LOCAL_NAME = "Local Development Admin"


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def deployment_environment_enabled() -> bool:
    return any(os.getenv(key) for key in ("RAILWAY_ENVIRONMENT", "RAILWAY_PROJECT_ID", "RENDER", "FLY_APP_NAME")) or os.getenv("ENV", "").lower() == "production" or os.getenv("FLASK_ENV", "").lower() == "production"


def preflight_local_environment() -> None:
    load_env_file(ROOT / ".env.local")
    load_env_file(ROOT / ".env")
    mode_values = {os.getenv("ENV", ""), os.getenv("FLASK_ENV", "")}
    local_mode = any(str(value).strip().lower() in {"local", "dev", "development"} for value in mode_values)
    database_url = os.getenv("DATABASE_URL", "").strip().lower()
    if deployment_environment_enabled() or database_url.startswith(("postgres://", "postgresql://")) or not local_mode:
        raise SystemExit(
            "Refusing to create local admin outside local/development SQLite mode. "
            f"ENV={os.getenv('ENV', '')} FLASK_ENV={os.getenv('FLASK_ENV', '')} DATABASE_URL={'set' if database_url else 'empty'}"
        )


preflight_local_environment()

import bot  # noqa: E402


def is_local_mode() -> bool:
    mode_values = {
        os.getenv("ENV", ""),
        os.getenv("FLASK_ENV", ""),
        getattr(bot, "COINPILOTX_ENV_MODE", ""),
    }
    local_mode = any(str(value).strip().lower() in {"local", "dev", "development"} for value in mode_values)
    deployment_mode = bot._deployment_environment_enabled()
    sqlite_mode = bot.db_service.ENGINE_NAME == "sqlite"
    return local_mode and sqlite_mode and not deployment_mode


def require_local_mode() -> None:
    if is_local_mode():
        return
    raise SystemExit(
        "Refusing to create local admin outside local/development SQLite mode. "
        f"env_mode={getattr(bot, 'COINPILOTX_ENV_MODE', '')} db_engine={bot.db_service.ENGINE_NAME}"
    )


def table_columns(cur, table: str) -> set[str]:
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def upsert_by_email(cur, table: str, values: dict[str, object]) -> None:
    cur.execute(f"SELECT rowid FROM {table} WHERE lower(email)=lower(?) LIMIT 1", (LOCAL_EMAIL,))
    row = cur.fetchone()
    columns = table_columns(cur, table)
    filtered = {key: value for key, value in values.items() if key in columns}
    if row:
        assignments = ", ".join(f"{key}=?" for key in filtered if key != "email")
        params = [value for key, value in filtered.items() if key != "email"]
        params.append(row[0])
        cur.execute(f"UPDATE {table} SET {assignments} WHERE rowid=?", params)
        return
    names = ", ".join(filtered.keys())
    placeholders = ", ".join("?" for _ in filtered)
    cur.execute(f"INSERT INTO {table} ({names}) VALUES ({placeholders})", tuple(filtered.values()))


def create_local_admin() -> None:
    require_local_mode()
    bot.init_db()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    password_hash = generate_password_hash(LOCAL_PASSWORD)
    conn = bot.db()
    try:
        cur = conn.cursor()
        upsert_by_email(
            cur,
            "users",
            {
                "username": "localadmin",
                "display_name": LOCAL_NAME,
                "full_name": LOCAL_NAME,
                "email": LOCAL_EMAIL,
                "password_hash": password_hash,
                "account_status": "active",
                "email_verified": 1,
                "onboarding_complete": 1,
                "is_pro": 1,
                "pro_active": 1,
                "plan": "pro",
                "subscription_plan": "pro",
                "subscription_status": "active",
                "trial_status": "development-only",
                "premium_status": "active",
                "lifetime_premium": 1,
                "bio": "Development-only local admin account for localhost UNDX testing.",
                "created_at": now,
                "updated_at": now,
                "signup_time": now,
                "subscription_started_at": now,
                "pro_started_at": now,
            },
        )
        upsert_by_email(
            cur,
            "admin_users",
            {
                "full_name": LOCAL_NAME,
                "email": LOCAL_EMAIL,
                "phone": "",
                "password_hash": password_hash,
                "role": "super_admin",
                "status": "active",
                "job_title": "Local Development Admin",
                "company_role": "Development Only",
                "notes": "Development-only localhost admin. Do not use in production.",
                "must_change_password": 0,
                "password_changed_at": now,
                "created_at": now,
                "updated_at": now,
            },
        )
        conn.commit()
    finally:
        conn.close()
    print("Local development admin ready.")
    print(f"Email: {LOCAL_EMAIL}")
    print("Password: configured for local development only")
    print("Scope: localhost SQLite database only")


if __name__ == "__main__":
    create_local_admin()
