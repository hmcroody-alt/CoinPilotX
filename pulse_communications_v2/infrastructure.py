"""Safe infrastructure diagnostics for Pulse Communications 2.0."""

from __future__ import annotations

import os

from . import flags


TWILIO_ENV = ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER")
R2_ENV = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET", "R2_PUBLIC_BASE_URL")
MUX_ENV = ("MUX_TOKEN_ID", "MUX_TOKEN_SECRET", "MUX_WEBHOOK_SECRET")


def _configured(name: str) -> bool:
    return bool((os.environ.get(name) or "").strip())


def _group(names: tuple[str, ...]) -> dict:
    return {
        "configured": all(_configured(name) for name in names),
        "fields": {name: {"configured": _configured(name)} for name in names},
        "missing_fields": [name for name in names if not _configured(name)],
    }


def diagnostics() -> dict:
    """Return yes/no diagnostics without exposing secret values."""

    return {
        "feature_flag": {
            "name": "PULSE_COMMUNICATIONS_V2_ENABLED",
            "enabled": flags.is_enabled(),
            "default_enabled": False,
        },
        "twilio": _group(TWILIO_ENV),
        "cloudflare_r2": _group(R2_ENV),
        "mux": _group(MUX_ENV),
        "notifications": {
            "enabled": _configured("COMM_V2_TWILIO_NOTIFICATIONS_ENABLED")
            and (os.environ.get("COMM_V2_TWILIO_NOTIFICATIONS_ENABLED") or "").strip().lower() == "true",
            "dry_run": (os.environ.get("COMM_V2_TWILIO_DRY_RUN") or "true").strip().lower() != "false",
        },
    }
