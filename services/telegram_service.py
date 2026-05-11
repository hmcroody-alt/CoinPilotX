"""Telegram/account sync helpers."""

from . import pro_access, user_context


def linked_account(telegram_user_id):
    return user_context.get_user_by_telegram(telegram_user_id)


def telegram_access_label(user):
    if pro_access.has_pro_access(user or {}):
        status = ((user or {}).get("subscription_status") or "").lower()
        return "Pro Trial" if status == "trialing" else "Pro Active"
    return "Free"

