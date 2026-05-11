import logging
import os
import re
import sqlite3
import time
from collections.abc import Mapping
from contextlib import contextmanager
from urllib.parse import urlparse

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import declarative_base, scoped_session, sessionmaker
except Exception:  # pragma: no cover - Railway installs these from requirements.
    create_engine = None
    text = None
    declarative_base = None
    scoped_session = None
    sessionmaker = None


LOCAL_SQLITE_FILE = "coinpilotx.db"
DATABASE_URL_LOADED = bool(os.getenv("DATABASE_URL", "").strip())


def _raw_database_url():
    return os.getenv("DATABASE_URL", "").strip()


def _normalize_engine_url(url):
    if not url:
        return f"sqlite:///{LOCAL_SQLITE_FILE}"
    if url.startswith("postgres://"):
        return "postgresql+psycopg2://" + url[len("postgres://"):]
    if url.startswith("postgresql://") and "+psycopg2" not in url:
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


ENGINE_URL = _normalize_engine_url(_raw_database_url())
IS_POSTGRES = ENGINE_URL.startswith("postgresql")
ENGINE_NAME = "postgresql" if IS_POSTGRES else "sqlite"


def masked_database_url():
    url = ENGINE_URL
    if not url:
        return ""
    try:
        parsed = urlparse(url)
        if parsed.password:
            return url.replace(parsed.password, "***")
        return url
    except Exception:
        return "masked"


def database_name():
    if IS_POSTGRES:
        parsed = urlparse(ENGINE_URL)
        return (parsed.path or "").lstrip("/") or "unknown"
    return LOCAL_SQLITE_FILE


def _make_engine():
    if create_engine is None:
        if IS_POSTGRES:
            raise RuntimeError("DATABASE_URL is PostgreSQL but SQLAlchemy is not installed.")
        return None
    if IS_POSTGRES:
        return create_engine(
            ENGINE_URL,
            pool_pre_ping=True,
            pool_recycle=300,
            pool_size=int(os.getenv("DB_POOL_SIZE", "5")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "10")),
            future=True,
        )
    return create_engine(
        ENGINE_URL,
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
        future=True,
    )


engine = _make_engine()
SessionLocal = scoped_session(sessionmaker(bind=engine, autocommit=False, autoflush=False)) if engine and scoped_session else None
Base = declarative_base() if declarative_base else object


class CompatRow(Mapping):
    def __init__(self, keys, values):
        self._keys = list(keys or [])
        self._values = tuple(values or ())
        self._data = {key: self._values[index] for index, key in enumerate(self._keys)}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._data[key]

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()


AUTO_PK_TABLES = {
    "users": "user_id",
    "alerts_history": "id",
    "price_history": "id",
    "portfolio_snapshots": "id",
    "whale_alerts": "id",
    "ai_analyses": "id",
    "chat_memory": "id",
    "crypto_news_cache": "id",
    "engagement_events": "id",
    "portfolio_advice_history": "id",
    "whale_intelligence": "id",
    "transaction_history": "id",
    "connected_wallets": "id",
    "ai_chat_history": "id",
    "payment_verifications": "id",
    "email_logs": "id",
    "brevo_contact_sync_logs": "id",
    "leads": "id",
    "analytics_events": "id",
    "sessions": "id",
    "telegram_link_codes": "id",
    "password_reset_tokens": "id",
    "email_verification_tokens": "id",
    "account_recovery_tokens": "id",
    "day_signal_results": "id",
    "user_ai_interactions": "id",
    "saved_wallets": "id",
    "user_alerts": "id",
    "portfolio_items": "id",
    "watchlist_items": "id",
    "user_activity": "id",
    "telegram_notifications": "id",
    "referral_events": "id",
    "subscriptions": "id",
    "usage_events": "id",
    "referral_rewards": "id",
    "promo_codes": "id",
    "trial_email_events": "id",
    "admin_users": "id",
    "admin_audit_logs": "id",
    "stripe_events": "id",
    "payment_records": "id",
    "checkout_attempts": "id",
    "unmatched_payments": "id",
    "support_notes": "id",
    "auth_events": "id",
    "employees": "id",
    "departments": "id",
    "roles": "id",
    "permissions": "id",
    "role_permissions": "id",
    "support_tickets": "id",
}


def _replace_question_placeholders(sql):
    out = []
    in_single = False
    in_double = False
    escaped = False
    for char in sql:
        if char == "\\" and not escaped:
            escaped = True
            out.append(char)
            continue
        if char == "'" and not in_double and not escaped:
            in_single = not in_single
        elif char == '"' and not in_single and not escaped:
            in_double = not in_double
        if char == "?" and not in_single and not in_double:
            out.append("%s")
        else:
            out.append(char)
        escaped = False
    return "".join(out)


def _translate_create_table(sql):
    sql = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "SERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\b(\w+)\s+INTEGER\s+PRIMARY\s+KEY\b", r"\1 SERIAL PRIMARY KEY", sql, flags=re.I)
    return sql


def _translate_sql(sql):
    translated = str(sql)
    translated = _translate_create_table(translated)
    translated = translated.replace("datetime('now')", "CURRENT_TIMESTAMP")
    translated = translated.replace('datetime("now")', "CURRENT_TIMESTAMP")
    translated = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", translated, flags=re.I)
    translated = _replace_question_placeholders(translated)
    return translated


def _insert_table(sql):
    match = re.match(r"\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)\b", sql, flags=re.I)
    return match.group(1).lower() if match else ""


def _has_conflict_clause(sql):
    return bool(re.search(r"\bON\s+CONFLICT\b", sql, flags=re.I))


class CompatCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self.lastrowid = None
        self._pending_rows = []
        self.description = None

    @property
    def rowcount(self):
        return getattr(self._cursor, "rowcount", -1)

    def execute(self, sql, params=None):
        translated = _translate_sql(sql)
        params = tuple(params or ())
        table = _insert_table(translated)
        append_do_nothing = re.search(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", str(sql), flags=re.I) and not _has_conflict_clause(translated)
        returning_pk = None
        if table and table in AUTO_PK_TABLES and "RETURNING" not in translated.upper() and not append_do_nothing:
            returning_pk = AUTO_PK_TABLES[table]
            translated = f"{translated.rstrip().rstrip(';')} RETURNING {returning_pk}"
        if append_do_nothing:
            translated = f"{translated.rstrip().rstrip(';')} ON CONFLICT DO NOTHING"
        self._cursor.execute(translated, params)
        self.description = self._cursor.description
        self.lastrowid = None
        self._pending_rows = []
        if returning_pk:
            row = self._cursor.fetchone()
            if row:
                self.lastrowid = row[0]
        return self

    def fetchone(self):
        if self._pending_rows:
            return self._pending_rows.pop(0)
        row = self._cursor.fetchone()
        return self._wrap_row(row)

    def fetchall(self):
        rows = self._cursor.fetchall()
        return [self._wrap_row(row) for row in rows]

    def _wrap_row(self, row):
        if row is None:
            return None
        keys = [col[0] for col in (self._cursor.description or [])]
        return CompatRow(keys, row)

    def close(self):
        return self._cursor.close()


class CompatConnection:
    row_factory = None

    def __init__(self, raw_connection):
        self._conn = raw_connection

    def cursor(self):
        return CompatCursor(self._conn.cursor())

    def execute(self, sql, params=None):
        cur = self.cursor()
        cur.execute(sql, params or ())
        return cur

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        return self._conn.close()


def connect():
    if IS_POSTGRES:
        if engine is None:
            raise RuntimeError("PostgreSQL DATABASE_URL is configured but SQLAlchemy engine is unavailable.")
        return CompatConnection(engine.raw_connection())
    path = LOCAL_SQLITE_FILE
    raw_url = _raw_database_url()
    uri = False
    if raw_url.startswith("sqlite:///"):
        path = raw_url.replace("sqlite:///", "", 1)
    elif raw_url.startswith("file:"):
        path = raw_url
        uri = True
    conn = sqlite3.connect(path, uri=uri)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def session_scope():
    if SessionLocal is None:
        raise RuntimeError("SQLAlchemy session is unavailable.")
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        SessionLocal.remove()


def health_check():
    start = time.time()
    result = {
        "connected": False,
        "db_engine": ENGINE_NAME,
        "database_url_loaded": DATABASE_URL_LOADED,
        "database_name": database_name(),
        "engine_url_masked": masked_database_url(),
        "latency_ms": None,
        "tables_detected": [],
        "error": "",
    }
    try:
        if IS_POSTGRES:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
                db_name = conn.execute(text("SELECT current_database()")).scalar()
                rows = conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema='public' ORDER BY table_name LIMIT 80"
                    )
                ).fetchall()
                result["database_name"] = db_name
                result["tables_detected"] = [row[0] for row in rows]
        else:
            conn = connect()
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name LIMIT 80")
            result["tables_detected"] = [row[0] for row in cur.fetchall()]
            conn.close()
        result["connected"] = True
    except Exception as exc:
        result["error"] = str(exc)[:500]
        logging.exception("Database health check failed: %s", exc)
    result["latency_ms"] = round((time.time() - start) * 1000, 2)
    return result


def log_startup_diagnostics():
    if IS_POSTGRES:
        logging.warning("USING POSTGRESQL PRODUCTION DATABASE")
    else:
        logging.warning("USING SQLITE LOCAL DATABASE")
    logging.warning("DATABASE_URL loaded: %s", DATABASE_URL_LOADED)
    logging.warning("SQLAlchemy engine URL masked: %s", masked_database_url())
    diagnostics = health_check()
    logging.warning(
        "Database startup diagnostics engine=%s connected=%s database=%s tables=%s latency_ms=%s error=%s",
        diagnostics.get("db_engine"),
        diagnostics.get("connected"),
        diagnostics.get("database_name"),
        len(diagnostics.get("tables_detected") or []),
        diagnostics.get("latency_ms"),
        diagnostics.get("error") or "",
    )
    return diagnostics
