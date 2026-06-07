"""Redacted APNs and FCM readiness checks for native mobile push.

These helpers validate provider configuration without printing or returning
secret values. They are safe for local audits and Railway runtime checks.
"""

from __future__ import annotations

import os
from typing import Mapping


EXPECTED_APNS_BUNDLE_ID = "com.pulsesoc.app"


def _get(env: Mapping[str, str] | None, name: str) -> str:
    source = env if env is not None else os.environ
    return str(source.get(name) or "").strip()


def normalize_private_key(value: str) -> str:
    """Handle Railway-style escaped newlines without exposing key content."""
    value = str(value or "").strip().strip('"').strip("'")
    if "\\n" in value:
        value = value.replace("\\n", "\n")
    return value.strip()


def _private_key_format_ok(value: str) -> bool:
    value = normalize_private_key(value)
    return (
        bool(value)
        and value.startswith("-----BEGIN PRIVATE KEY-----")
        and value.endswith("-----END PRIVATE KEY-----")
        and "\n" in value
    )


def _pem_loads(value: str) -> bool:
    value = normalize_private_key(value)
    if not _private_key_format_ok(value):
        return False
    try:
        from cryptography.hazmat.primitives import serialization

        serialization.load_pem_private_key(value.encode("utf-8"), password=None)
        return True
    except Exception:
        return False


def apns_readiness(env: Mapping[str, str] | None = None) -> dict:
    bundle_id = _get(env, "APNS_BUNDLE_ID")
    private_key = _get(env, "APNS_PRIVATE_KEY")
    key_loaded = bool(private_key)
    newline_format_ok = "\\n" in private_key or "\n" in normalize_private_key(private_key)
    key_format_ok = _private_key_format_ok(private_key)
    key_parse_ok = _pem_loads(private_key)
    ready = all(
        [
            bool(_get(env, "APNS_KEY_ID")),
            bool(_get(env, "APNS_TEAM_ID")),
            bundle_id == EXPECTED_APNS_BUNDLE_ID,
            key_loaded,
            newline_format_ok,
            key_format_ok,
            key_parse_ok,
        ]
    )
    return {
        "apns_key_id_loaded": bool(_get(env, "APNS_KEY_ID")),
        "apns_team_id_loaded": bool(_get(env, "APNS_TEAM_ID")),
        "apns_bundle_id_loaded": bool(bundle_id),
        "apns_bundle_id_expected": bundle_id == EXPECTED_APNS_BUNDLE_ID,
        "apns_private_key_loaded": key_loaded,
        "apns_private_key_newline_format_ok": newline_format_ok,
        "apns_private_key_format_ok": key_format_ok,
        "apns_private_key_parse_ok": key_parse_ok,
        "apns_provider_initializable_safely": ready,
        "ready": ready,
    }


def fcm_readiness(env: Mapping[str, str] | None = None, initialize_admin: bool = True) -> dict:
    project_id = _get(env, "FCM_PROJECT_ID")
    client_email = _get(env, "FCM_CLIENT_EMAIL")
    private_key = _get(env, "FCM_PRIVATE_KEY")
    key_loaded = bool(private_key)
    key_format_ok = _private_key_format_ok(private_key)
    key_parse_ok = _pem_loads(private_key)
    admin_available = False
    admin_initialized = False
    can_try_admin = all([project_id, client_email, key_loaded, key_format_ok, key_parse_ok])
    if initialize_admin and can_try_admin:
        try:
            import firebase_admin
            from firebase_admin import credentials

            admin_available = True
            app_name = "pulse-push-readiness"
            try:
                firebase_admin.get_app(app_name)
                admin_initialized = True
            except ValueError:
                cred = credentials.Certificate(
                    {
                        "type": "service_account",
                        "project_id": project_id,
                        "private_key": normalize_private_key(private_key),
                        "client_email": client_email,
                        "token_uri": "https://oauth2.googleapis.com/token",
                    }
                )
                firebase_admin.initialize_app(cred, {"projectId": project_id}, name=app_name)
                admin_initialized = True
        except Exception:
            admin_initialized = False
    else:
        try:
            import firebase_admin  # noqa: F401

            admin_available = True
        except Exception:
            admin_available = False
    ready = all([project_id, client_email, key_loaded, key_format_ok, key_parse_ok, admin_available, admin_initialized])
    return {
        "fcm_project_id_loaded": bool(project_id),
        "fcm_client_email_loaded": bool(client_email),
        "fcm_private_key_loaded": key_loaded,
        "fcm_private_key_format_ok": key_format_ok,
        "fcm_private_key_parse_ok": key_parse_ok,
        "firebase_admin_available": admin_available,
        "firebase_admin_initializes_safely": admin_initialized,
        "ready": ready,
    }


def native_push_readiness(env: Mapping[str, str] | None = None, initialize_admin: bool = True) -> dict:
    apns = apns_readiness(env)
    fcm = fcm_readiness(env, initialize_admin=initialize_admin)
    return {
        "apns": apns,
        "fcm": fcm,
        "ready": bool(apns.get("ready") and fcm.get("ready")),
    }
