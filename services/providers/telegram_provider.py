from __future__ import annotations

import os


def validate():
    return {
        "ok": bool(os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN")),
        "status": "healthy" if os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("BOT_TOKEN") else "missing_config",
        "provider": "telegram",
    }


def health():
    return validate()


def send(*_args, **_kwargs):
    if not validate()["ok"]:
        return {"ok": False, "status": "not_configured", "provider": "telegram"}
    return {"ok": True, "status": "queued", "provider": "telegram"}


def retry(*args, **kwargs):
    return send(*args, **kwargs)


def rate_limit(*_args, **_kwargs):
    return {"ok": True, "allowed": True}
