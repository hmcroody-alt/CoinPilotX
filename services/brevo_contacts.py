import logging
import os
import time
from datetime import datetime

import requests

from .user_context import connect

BREVO_BASE_URL = "https://api.brevo.com/v3"
DEFAULT_FOLDER_NAME = "CoinPilotXAI Inc."
LIST_NAMES = {
    "default": "Website Leads",
    "pro": "Pro Users",
    "telegram": "Telegram Users",
    "email": "Email Subscribers",
    "sms": "SMS Subscribers",
}


def _headers():
    api_key = os.getenv("BREVO_API_KEY", "").strip()
    if not api_key:
        return None
    return {
        "api-key": api_key,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _request(method, path, **kwargs):
    headers = _headers()
    if not headers:
        return {"ok": False, "status_code": None, "body": {"message": "BREVO_API_KEY is not loaded."}}
    try:
        response = requests.request(method, f"{BREVO_BASE_URL}{path}", headers=headers, timeout=18, **kwargs)
        try:
            body = response.json() if response.text else {}
        except Exception:
            body = {"raw": response.text}
        return {"ok": 200 <= response.status_code < 300, "status_code": response.status_code, "body": body}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": {"message": str(exc)}}


def _truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on", "active", "pro"}


def _is_pro(record):
    return _truthy(record.get("is_pro")) or str(record.get("plan") or "").lower() == "pro" or str(record.get("subscription_status") or "").lower() == "active"


def _telegram_linked(record):
    return bool(record.get("telegram_user_id") or record.get("telegram_username") or record.get("telegram_chat_id"))


def _clean_phone(phone):
    return (phone or "").strip()[:40]


def _safe_record(record):
    record = record or {}
    return {
        "email": (record.get("email") or "").strip().lower(),
        "full_name": (record.get("full_name") or record.get("display_name") or "").strip(),
        "phone": _clean_phone(record.get("phone")),
        "country": (record.get("country") or "").strip(),
        "email_opt_in": _truthy(record.get("email_opt_in")),
        "sms_opt_in": _truthy(record.get("sms_opt_in")),
        "source": (record.get("source") or "coinpilotxai").strip(),
        "signup_date": record.get("signup_date") or record.get("created_at") or record.get("signup_time") or datetime.now().isoformat(),
        "plan": (record.get("plan") or record.get("subscription_plan") or "free").strip(),
        "telegram_linked": _telegram_linked(record),
        "is_pro": _is_pro(record),
    }


def ensure_sync_table():
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS brevo_contact_sync_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_type TEXT,
            entity_id INTEGER,
            email TEXT,
            status TEXT,
            details TEXT,
            list_names TEXT,
            created_at TEXT
        )
        """
    )
    conn.commit()
    conn.close()


def log_sync(entity_type, entity_id, email, status, details="", list_names=None):
    try:
        ensure_sync_table()
        conn = connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO brevo_contact_sync_logs (entity_type, entity_id, email, status, details, list_names, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entity_type,
                entity_id,
                email,
                status,
                str(details)[:4000],
                ", ".join(list_names or [])[:1000],
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logging.info("Brevo contact sync log failed: %s", exc)


def _list_env_id(list_name):
    if list_name == LIST_NAMES["default"]:
        return os.getenv("BREVO_DEFAULT_LIST_ID")
    if list_name == LIST_NAMES["pro"]:
        return os.getenv("BREVO_PRO_LIST_ID")
    if list_name == LIST_NAMES["telegram"]:
        return os.getenv("BREVO_TELEGRAM_LIST_ID")
    return None


def get_or_create_folder():
    response = _request("GET", "/contacts/folders", params={"limit": 50, "offset": 0})
    if response["ok"]:
        for folder in response["body"].get("folders", []):
            if folder.get("name") == DEFAULT_FOLDER_NAME:
                return folder.get("id")
    created = _request("POST", "/contacts/folders", json={"name": DEFAULT_FOLDER_NAME})
    if created["ok"]:
        return created["body"].get("id")
    logging.info("Brevo folder lookup/create failed: %s", created)
    return None


def get_existing_lists():
    response = _request("GET", "/contacts/lists", params={"limit": 50, "offset": 0})
    if not response["ok"]:
        logging.info("Brevo list lookup failed: %s", response)
        return {}
    return {item.get("name"): item.get("id") for item in response["body"].get("lists", []) if item.get("name")}


def get_or_create_list_id(list_name):
    env_id = _list_env_id(list_name)
    if env_id:
        try:
            return int(env_id)
        except Exception:
            logging.info("Brevo list env var for %s is not numeric: %s", list_name, env_id)
    lists = get_existing_lists()
    if list_name in lists:
        return lists[list_name]
    folder_id = get_or_create_folder()
    if not folder_id:
        return None
    created = _request("POST", "/contacts/lists", json={"name": list_name, "folderId": folder_id})
    if created["ok"]:
        return created["body"].get("id")
    logging.info("Brevo list create failed for %s: %s", list_name, created)
    return None


def target_list_names(record):
    normalized = _safe_record(record)
    names = [LIST_NAMES["default"]]
    if normalized["email_opt_in"]:
        names.append(LIST_NAMES["email"])
    if normalized["sms_opt_in"]:
        names.append(LIST_NAMES["sms"])
    if normalized["is_pro"]:
        names.append(LIST_NAMES["pro"])
    if normalized["telegram_linked"]:
        names.append(LIST_NAMES["telegram"])
    return names


def target_list_ids(record):
    ids = []
    for name in target_list_names(record):
        list_id = get_or_create_list_id(name)
        if list_id:
            ids.append(int(list_id))
    return ids


def create_or_update_brevo_contact(record, list_ids=None):
    normalized = _safe_record(record)
    email = normalized["email"]
    if not email:
        return {"ok": False, "status_code": None, "body": {"message": "Missing email."}}
    attributes = {
        "FULL_NAME": normalized["full_name"],
        "PHONE": normalized["phone"],
        "COUNTRY": normalized["country"],
        "SOURCE": normalized["source"],
        "SIGNUP_DATE": normalized["signup_date"],
        "PLAN": normalized["plan"],
        "EMAIL_OPT_IN": normalized["email_opt_in"],
        "SMS_OPT_IN": normalized["sms_opt_in"],
        "TELEGRAM_LINKED": normalized["telegram_linked"],
    }
    if normalized["phone"]:
        attributes["SMS"] = normalized["phone"]
    payload = {
        "email": email,
        "attributes": attributes,
        "listIds": list_ids or target_list_ids(record),
        "updateEnabled": True,
        "emailBlacklisted": not normalized["email_opt_in"],
        "smsBlacklisted": not normalized["sms_opt_in"],
    }
    response = _request("POST", "/contacts", json=payload)
    if response["ok"]:
        return response
    # Some Brevo accounts do not have custom contact attributes configured yet.
    # Keep contact creation reliable, then campaigns can add attributes later.
    minimal = {
        "email": email,
        "listIds": payload["listIds"],
        "updateEnabled": True,
        "emailBlacklisted": payload["emailBlacklisted"],
        "smsBlacklisted": payload["smsBlacklisted"],
    }
    if normalized["full_name"] or normalized["phone"]:
        minimal["attributes"] = {}
        if normalized["full_name"]:
            minimal["attributes"]["FIRSTNAME"] = normalized["full_name"].split()[0]
        if normalized["phone"]:
            minimal["attributes"]["SMS"] = normalized["phone"]
    return _request("POST", "/contacts", json=minimal)


def add_brevo_contact_to_list(email, list_id):
    if not email or not list_id:
        return {"ok": False, "status_code": None, "body": {"message": "Missing email or list id."}}
    return _request("POST", f"/contacts/lists/{int(list_id)}/contacts/add", json={"emails": [email]})


def sync_user_to_brevo(record, entity_type="user", entity_id=None, retry=True):
    normalized = _safe_record(record)
    email = normalized["email"]
    if not email:
        return {"ok": False, "status": "skipped", "message": "Missing email."}
    if not _headers():
        details = {"message": "BREVO_API_KEY is not loaded."}
        log_sync(entity_type, entity_id, email, "failed", details, target_list_names(record))
        logging.warning("brevo contact sync failed: BREVO_API_KEY is not loaded.")
        return {"ok": False, "status": "failed", "response": details}
    names = target_list_names(record)
    logging.info("brevo contact sync started: entity_type=%s entity_id=%s email=%s", entity_type, entity_id, email)
    list_ids = target_list_ids(record)
    response = create_or_update_brevo_contact(record, list_ids=list_ids)
    if response["ok"]:
        for list_id in list_ids:
            add_brevo_contact_to_list(email, list_id)
        log_sync(entity_type, entity_id, email, "success", response.get("body"), names)
        logging.info("brevo contact sync success: entity_type=%s entity_id=%s email=%s", entity_type, entity_id, email)
        return {"ok": True, "status": "success", "list_ids": list_ids, "response": response}
    if retry:
        logging.warning("brevo contact sync failed; retrying once after 3 seconds: %s", response)
        time.sleep(3)
        return sync_user_to_brevo(record, entity_type=entity_type, entity_id=entity_id, retry=False)
    log_sync(entity_type, entity_id, email, "failed", response.get("body"), names)
    logging.warning("brevo contact sync failed: entity_type=%s entity_id=%s email=%s response=%s", entity_type, entity_id, email, response)
    return {"ok": False, "status": "failed", "response": response}
