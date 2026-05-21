from __future__ import annotations

import os

from services import push_service


def validate():
    return {
        "ok": bool(os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY")),
        "status": "healthy" if os.getenv("VAPID_PUBLIC_KEY") and os.getenv("VAPID_PRIVATE_KEY") else "missing_config",
        "provider": "webpush",
    }


def health():
    return validate()


def send(user_id, title, body, data=None, **kwargs):
    return push_service.send_push(user_id, title, body, data or {}, push_type=kwargs.get("push_type") or "general")


def retry(*args, **kwargs):
    return send(*args, **kwargs)


def rate_limit(*_args, **_kwargs):
    return {"ok": True, "allowed": True}
