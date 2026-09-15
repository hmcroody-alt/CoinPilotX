"""Last-moment veto on a queued email, checked after the claim, before the send.

Why this exists
---------------
`failed_email_queue` is not only a retry buffer. Because the processors honour
`next_retry_at <= now`, a row with a far-future `next_retry_at` is a durable
timer — which is how meeting reminders are scheduled without standing up a
second scheduler. The consequence is that a queued email can sit in the table
for months, and the world it described when it was written may be gone by the
time it comes due: the meeting was cancelled, it moved to a different day, the
recipient was uninvited, or someone already sent this one.

Rewriting the queue on every one of those changes is the tempting fix and the
wrong one. It is a broadcast invalidation — every write has to know every
queued consequence of itself — and it still loses the race where a
cancellation commits while the row is already claimed.

So instead the *reader* asks. Each `email_type` may register one validator;
the processors call :func:`may_send` after claiming a row and skip the send if
it refuses. An email type with no validator is sent, so this changes nothing
for the mail the platform already sends.

Fails closed
------------
A validator that raises means "I could not confirm this is still valid", and
an unconfirmed send is refused. The alternative — send on error — would make
every one of these guards evaporate under exactly the conditions (a database
blip, a partially deployed change) where the wrong email is most likely.
"""

from __future__ import annotations

import logging
from typing import Callable

LOGGER = logging.getLogger("email_send_guard")

#: email_type -> validator. A validator takes the queue row (a plain dict) and
#: returns either a bool or a ``(ok, reason)`` pair. The reason is recorded on
#: the row so a skipped email can be explained later without guessing.
_VALIDATORS: dict[str, Callable[[dict], object]] = {}


def register(email_type: str, validator: Callable[[dict], object]) -> None:
    """Attach a validator to an email type. Last registration wins.

    Idempotent by design: modules register at import, and a module imported
    twice under two names (which happens in this codebase) must not end up
    with two validators or an error.
    """
    key = str(email_type or "").strip()
    if not key:
        raise ValueError("register() needs an email_type to attach to.")
    if not callable(validator):
        raise TypeError("A validator must be callable.")
    _VALIDATORS[key] = validator


def unregister(email_type: str) -> None:
    """Drop a validator. For tests; nothing in production should need it."""
    _VALIDATORS.pop(str(email_type or "").strip(), None)


def registered_types() -> tuple[str, ...]:
    return tuple(sorted(_VALIDATORS))


def may_send(row: dict) -> tuple[bool, str]:
    """``(ok, reason)`` for one claimed queue row.

    ``reason`` is empty when ok. When not ok it is a short machine-ish token
    (``meeting_cancelled``, ``schedule_changed``) suitable for a log line and
    for the row's ``last_error`` — never anything the recipient wrote.
    """
    try:
        email_type = str((row or {}).get("email_type") or "").strip()
    except AttributeError:
        return True, ""
    validator = _VALIDATORS.get(email_type)
    if validator is None:
        return True, ""
    try:
        verdict = validator(dict(row))
    except Exception:
        LOGGER.exception("EMAIL_SEND_GUARD_FAILED email_type=%s", email_type)
        return False, "validator_error"
    if isinstance(verdict, tuple):
        ok = bool(verdict[0])
        reason = str(verdict[1] if len(verdict) > 1 else "")[:200]
    else:
        ok, reason = bool(verdict), ""
    if ok:
        return True, ""
    return False, reason or "refused"
