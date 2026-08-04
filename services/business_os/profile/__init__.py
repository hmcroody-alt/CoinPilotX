"""Business OS — the seller business profile: the authoritative identity layer.

Public surface is deliberately small. Callers go through ``service`` (decisions and
storage) or ``api`` (HTTP controller); ``schema`` is startup-only.
"""

from services.business_os.profile import api, schema, service  # noqa: F401

__all__ = ["api", "schema", "service"]
