from __future__ import annotations


def validate():
    return {"ok": True, "status": "healthy", "provider": "in_app"}


def health():
    return validate()


def send(*_args, **_kwargs):
    return {"ok": True, "status": "created", "provider": "in_app"}


def retry(*_args, **_kwargs):
    return {"ok": True, "status": "no_retry_needed", "provider": "in_app"}


def rate_limit(*_args, **_kwargs):
    return {"ok": True, "allowed": True}
