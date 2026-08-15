"""Server-authoritative PulseSoc locale, time-zone, currency, and date preferences."""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from services import db
from services.schema_guard import run_once_per_process


LOCALE_PATTERN = re.compile(r"^[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
DATE_FORMATS = {"auto", "mdy", "dmy", "ymd"}
SUPPORTED_CURRENCIES = {
    "AED", "ARS", "AUD", "BRL", "CAD", "CHF", "CLP", "CNY", "COP", "CZK",
    "DKK", "EGP", "EUR", "GBP", "GHS", "HKD", "HTG", "HUF", "IDR", "ILS",
    "INR", "JPY", "KES", "KRW", "MXN", "MYR", "NGN", "NOK", "NZD", "PEN",
    "PHP", "PKR", "PLN", "RON", "RUB", "SAR", "SEK", "SGD", "THB", "TRY",
    "TWD", "UAH", "USD", "VND", "ZAR",
}


class RegionPreferenceError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@run_once_per_process
def ensure_schema(conn=None) -> None:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_region_preferences (
                user_id INTEGER PRIMARY KEY,
                preferred_locale TEXT,
                preferred_timezone TEXT,
                preferred_currency TEXT,
                preferred_date_format TEXT NOT NULL DEFAULT 'auto',
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pulse_region_preference_events (
                event_id TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                changed_fields TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pulse_region_events_user "
            "ON pulse_region_preference_events (user_id, created_at)"
        )
        if owned:
            conn.commit()
    finally:
        if owned:
            conn.close()


def get_preferences(user_id: int, *, conn=None) -> dict[str, Any]:
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        row = conn.execute(
            """
            SELECT preferred_locale,preferred_timezone,preferred_currency,
                   preferred_date_format,updated_at
            FROM pulse_region_preferences WHERE user_id=? LIMIT 1
            """,
            (int(user_id),),
        ).fetchone()
        if not row:
            return _payload()
        return _payload(
            locale=row["preferred_locale"],
            time_zone=row["preferred_timezone"],
            currency=row["preferred_currency"],
            date_format=row["preferred_date_format"],
            updated_at=row["updated_at"],
        )
    finally:
        if owned:
            conn.close()


def update_preferences(user_id: int, values: dict[str, Any], *, conn=None) -> dict[str, Any]:
    if not isinstance(values, dict):
        raise RegionPreferenceError("invalid_preferences", "Region preferences must be an object.")
    allowed = {"locale", "preferred_locale", "time_zone", "timezone", "preferred_timezone",
               "currency", "preferred_currency", "date_format", "preferred_date_format"}
    if not set(values).issubset(allowed):
        raise RegionPreferenceError("unsupported_preference", "One or more region preferences are unsupported.")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)
        current = get_preferences(int(user_id), conn=conn)
        next_values = {
            "locale": current["preferred_locale"],
            "time_zone": current["preferred_timezone"],
            "currency": current["preferred_currency"],
            "date_format": current["preferred_date_format"],
        }
        changed: list[str] = []
        aliases = {
            "locale": ("locale", "preferred_locale"),
            "time_zone": ("time_zone", "timezone", "preferred_timezone"),
            "currency": ("currency", "preferred_currency"),
            "date_format": ("date_format", "preferred_date_format"),
        }
        normalizers = {
            "locale": _locale,
            "time_zone": _time_zone,
            "currency": _currency,
            "date_format": _date_format,
        }
        for field, names in aliases.items():
            supplied = next((name for name in names if name in values), None)
            if supplied is None:
                continue
            normalized = normalizers[field](values.get(supplied))
            if normalized != next_values[field]:
                next_values[field] = normalized
                changed.append(field)

        now = _now()
        existing = conn.execute(
            "SELECT user_id FROM pulse_region_preferences WHERE user_id=? LIMIT 1",
            (int(user_id),),
        ).fetchone()
        params = (
            next_values["locale"] or None,
            next_values["time_zone"] or None,
            next_values["currency"] or None,
            next_values["date_format"],
            now,
            int(user_id),
        )
        if existing:
            conn.execute(
                """
                UPDATE pulse_region_preferences
                SET preferred_locale=?,preferred_timezone=?,preferred_currency=?,
                    preferred_date_format=?,updated_at=?
                WHERE user_id=?
                """,
                params,
            )
        else:
            conn.execute(
                """
                INSERT INTO pulse_region_preferences
                (preferred_locale,preferred_timezone,preferred_currency,
                 preferred_date_format,updated_at,user_id)
                VALUES (?,?,?,?,?,?)
                """,
                params,
            )
        if changed:
            conn.execute(
                """
                INSERT INTO pulse_region_preference_events
                (event_id,user_id,changed_fields,created_at) VALUES (?,?,?,?)
                """,
                (uuid.uuid4().hex, int(user_id), ",".join(sorted(changed)), now),
            )
        if owned:
            conn.commit()
        return _payload(
            locale=next_values["locale"],
            time_zone=next_values["time_zone"],
            currency=next_values["currency"],
            date_format=next_values["date_format"],
            updated_at=now,
            changed_fields=changed,
        )
    finally:
        if owned:
            conn.close()


def _payload(
    *,
    locale: Any = "",
    time_zone: Any = "",
    currency: Any = "",
    date_format: Any = "auto",
    updated_at: Any = None,
    changed_fields: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "preferred_locale": str(locale or ""),
        "preferred_timezone": str(time_zone or ""),
        "preferred_currency": str(currency or ""),
        "preferred_date_format": str(date_format or "auto"),
        "automatic": {
            "locale": not bool(locale),
            "timezone": not bool(time_zone),
            "currency": not bool(currency),
            "date_format": str(date_format or "auto") == "auto",
        },
        "updated_at": updated_at,
        "changed_fields": changed_fields or [],
    }


def _automatic(value: Any) -> bool:
    return str(value or "").strip().lower() in {"", "auto", "device", "system"}


def _locale(value: Any) -> str:
    if _automatic(value):
        return ""
    normalized = str(value).strip().replace("_", "-")
    if len(normalized) > 35 or not LOCALE_PATTERN.fullmatch(normalized):
        raise RegionPreferenceError("invalid_locale", "Choose a valid locale.")
    return normalized


def _time_zone(value: Any) -> str:
    if _automatic(value):
        return ""
    normalized = str(value).strip()
    if len(normalized) > 80:
        raise RegionPreferenceError("invalid_timezone", "Choose a valid time zone.")
    try:
        ZoneInfo(normalized)
    except (ZoneInfoNotFoundError, ValueError):
        raise RegionPreferenceError("invalid_timezone", "Choose a valid time zone.")
    return normalized


def _currency(value: Any) -> str:
    if _automatic(value):
        return ""
    normalized = str(value).strip().upper()
    if not CURRENCY_PATTERN.fullmatch(normalized) or normalized not in SUPPORTED_CURRENCIES:
        raise RegionPreferenceError("invalid_currency", "Choose a supported currency.")
    return normalized


def _date_format(value: Any) -> str:
    normalized = str(value or "auto").strip().lower()
    if normalized not in DATE_FORMATS:
        raise RegionPreferenceError("invalid_date_format", "Choose automatic, month/day/year, day/month/year, or year/month/day.")
    return normalized
