"""Business OS — the canonical execution-result contract for governed assistants.

One rule: **``ok`` means the canonical backend state confirms the action happened.**

The earlier contract returned ``{"ok": True, ..., "verified": False}`` after a write whose
read-after-write check failed. That is not a lie — ``verified`` was surfaced — but it is a
trap: the overwhelmingly common client idiom is ``if resp["ok"]:``, and such a client reads
an unconfirmed money movement as a completed one. A result shape whose most-read field can
be true while the action is unproven is a governance defect, not a cosmetic one.

So on every write path ``ok`` is derived from verification and never asserted
unconditionally. Because that is a narrowing of an existing contract, two compatibility
affordances are kept:

  * ``write_applied`` — True whenever the canonical verb actually ran. A caller that
    previously used the unconditional ``ok`` to mean "the verb was invoked" (rather than
    "it succeeded") has an explicit, correctly-named field to move to.
  * ``verified`` — unchanged, and still the field the UNDX action workflows read.

An unverified write is deliberately NOT reported as a plain failure either: the write may
have partially applied, so a client must not blindly retry it. ``code`` /
``retry_safe: False`` / ``message`` say exactly that.

This module owns no tables, does no I/O, and imports nothing from the subsystems, so both
assistants (and any future one) share one definition instead of three drifting copies.
"""

from __future__ import annotations

from typing import Any, Optional

# An action whose effect canonical state could not confirm. 409 keeps it in-family with
# the other governed refusals (confirmation_*, writes_disabled, illegal_transition) and,
# critically, is NOT a 2xx — so a client that only checks the HTTP status also cannot read
# an unconfirmed action as done.
CODE_VERIFICATION_FAILED = "verification_failed"
HTTP_VERIFICATION_FAILED = 409

_UNVERIFIED_MESSAGE = (
    "The canonical write was submitted but backend state does not confirm it. "
    "The action may have partially applied — do NOT retry blindly; re-read canonical "
    "state and reconcile."
)


def read_result(tool: str, result: Any) -> dict:
    """A read tool changed nothing, so ``ok`` is simply "the read ran"."""
    return {"ok": True, "tool": tool, "write": False, "result": result}


def write_result(tool: str, verified: bool, observed: Any, canonical: dict,
                 *, extra: Optional[dict] = None) -> dict:
    """Build the result of a write whose effect was checked against canonical state.

    ``verified`` MUST come from a read-after-write against the authoritative row — never
    from the verb's return value.
    """
    verified = bool(verified)
    out: dict = {
        "ok": verified,          # <- honest: only True when canonical state confirms it
        "tool": tool,
        "write": True,
        "write_applied": True,   # the canonical verb ran (see module docstring)
        "verified": verified,
        "observed": observed,
        "canonical_params": canonical,
    }
    if not verified:
        out["code"] = CODE_VERIFICATION_FAILED
        out["http_status"] = HTTP_VERIFICATION_FAILED
        out["retry_safe"] = False
        out["message"] = _UNVERIFIED_MESSAGE
    if extra:
        out.update(extra)
    return out


def envelope(result: dict) -> tuple:
    """Map an assistant result onto ``(http_status, body)`` for an API layer.

    A verified action (and every read) is 200. An unverified write is 409 with the
    envelope's own ``ok`` False, so neither ``body["ok"]`` nor the status code can be
    mistaken for success.
    """
    result = result or {}
    ok = bool(result.get("ok"))
    body = {"ok": ok, "result": result}
    if ok:
        return (200, body)
    status = int(result.get("http_status") or HTTP_VERIFICATION_FAILED)
    body["error"] = result.get("message") or _UNVERIFIED_MESSAGE
    body["code"] = result.get("code") or CODE_VERIFICATION_FAILED
    return (status, body)
