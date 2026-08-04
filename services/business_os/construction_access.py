import os


TRUTHY = {"1", "true", "yes", "on"}


def flag_enabled(value):
    return str(value or "").strip().lower() in TRUTHY


def configured_owner_ids(value=None):
    raw = os.getenv("PULSESOC_BUSINESS_OS_OWNER_USER_IDS", "") if value is None else value
    owner_ids = set()
    for token in str(raw or "").split(","):
        try:
            owner_id = int(token.strip())
        except (TypeError, ValueError):
            continue
        if owner_id > 0:
            owner_ids.add(owner_id)
    return owner_ids


def resolve_business_construction_access(user, *, is_owner_admin=False, public_release=None, owner_ids=None):
    user_id = int((user or {}).get("user_id") or 0)
    released = flag_enabled(
        os.getenv("PULSESOC_BUSINESS_OS_PUBLIC_RELEASE_ENABLED", "")
        if public_release is None else public_release
    )
    explicit_owner = user_id > 0 and user_id in (
        configured_owner_ids() if owner_ids is None else set(owner_ids)
    )
    developer = bool(user_id and (explicit_owner or is_owner_admin))
    allowed = bool(user_id and (released or developer))
    return {
        "ok": True,
        "mode": "public" if released else ("development" if developer else "construction"),
        "can_access_private_business_os": allowed,
        "construction_mode": not released,
        "developer_mode": developer,
        "developer_badge": developer,
    }
