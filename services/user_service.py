"""User account helpers shared by site, bot, and admin workflows."""

from . import pro_access, user_context


def get_user(user_id):
    return user_context.get_user_by_id(user_id)


def get_telegram_user(telegram_user_id):
    return user_context.get_user_by_telegram(telegram_user_id)


def account_summary(user):
    user = user or {}
    return {
        "user_id": user.get("user_id"),
        "name": user.get("full_name") or user.get("display_name") or "PulseSoc user",
        "email": user_context.mask_email(user.get("email")),
        "plan": "pro" if pro_access.has_pro_access(user) else "free",
        "subscription_status": user.get("subscription_status") or "inactive",
        "telegram_linked": bool(user.get("telegram_user_id")),
    }

