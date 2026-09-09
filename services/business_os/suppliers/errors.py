"""Privacy-safe errors at the supplier boundary (never provider response text)."""
from __future__ import annotations

import re


class SupplierError(Exception):
    def __init__(self, code: str, *, http_status: int = 502,
                 retry_after: float | None = None, ambiguous_write: bool = False,
                 endpoint: str | None = None, provider_code: int | None = None):
        # A provider message, URL or credential must never become an exception.
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", code) else "SUPPLIER_ERROR"
        self.http_status = http_status
        self.retry_after = retry_after
        self.ambiguous_write = bool(ambiguous_write)
        # Two diagnostic coordinates, both deliberately not content.
        #
        # `endpoint` is one of this repository's own approved CJ paths. It is
        # checked against PATHS before any request is issued, so by construction
        # it is a constant of our source -- the same class of fact as a line
        # number, and it is what distinguishes a dozen validators that share one
        # error code but not one call.
        #
        # `provider_code` is CJ's numeric business code, and it is the single
        # piece of provider-derived data kept anywhere in this package. An
        # integer is why: it cannot carry a message, a body, an address or a
        # credential, so the leak this package is built to prevent has nothing
        # to ride on. The alternative was to learn CJ's code by guessing one
        # rejection at a time against a live merchant account.
        self.endpoint = endpoint if isinstance(endpoint, str) and re.fullmatch(r"[A-Za-z0-9/]{1,64}", endpoint) else None
        self.provider_code = provider_code if type(provider_code) is int and 0 <= provider_code <= 99_999_999 else None
        super().__init__(self.code)

    def as_dict(self):
        return {"error": self.code, "retry_after": self.retry_after,
                "ambiguous_write": self.ambiguous_write}
