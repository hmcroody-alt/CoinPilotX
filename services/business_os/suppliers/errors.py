"""Privacy-safe errors at the supplier boundary (never provider response text)."""
from __future__ import annotations

import re


class SupplierError(Exception):
    def __init__(self, code: str, *, http_status: int = 502,
                 retry_after: float | None = None, ambiguous_write: bool = False):
        # A provider message, URL or credential must never become an exception.
        self.code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", code) else "SUPPLIER_ERROR"
        self.http_status = http_status
        self.retry_after = retry_after
        self.ambiguous_write = bool(ambiguous_write)
        super().__init__(self.code)

    def as_dict(self):
        return {"error": self.code, "retry_after": self.retry_after,
                "ambiguous_write": self.ambiguous_write}
