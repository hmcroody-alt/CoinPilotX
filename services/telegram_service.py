"""Telegram/account sync helpers."""

from . import pro_access, user_context


def linked_account(telegram_user_id):
    return user_context.get_user_by_telegram(telegram_user_id)


def telegram_access_label(user):
    if pro_access.has_pro_access(user or {}):
        return "Pro Trial" if pro_access.pro_access_type(user or {}) == "trial" else "Pro Active"
    return "Free"
