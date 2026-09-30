"""Two limits that a per-path request counter cannot express.

``bot.basic_abuse_guard`` counts *requests per path per client*. That shape is
right for ``/login`` and wrong for both of the account-identity endpoints, in
opposite directions -- and the wrongness is what left an enumeration oracle and
an email-bombing vector in production at the same time as a limiter sat on the
file two thousand lines above them.

WHY A REQUEST COUNT IS THE WRONG SHAPE HERE
-------------------------------------------
``/api/mobile/auth/confirmation-status`` answers "has this address confirmed
yet". ``VerifyEmailStep.tsx`` polls it every 4 seconds for as long as the user
keeps the screen open (``POLL_INTERVAL_MS = 4000``), so one honest signup makes
~75 requests per 300 seconds. The obvious fix -- adding it to
``ABUSE_GUARD_PROTECTED`` at ``(6, 300)`` next to ``/forgot-password`` -- would
have refused every legitimate signup 24 seconds in. A request count cannot be
set low enough to stop an enumerator without breaking the only real client.

What separates the two is not volume, it is *variety*. Those 75 requests ask
about **one** address. An enumerator asking 75 questions asks about 75. So the
quantity to bound is the number of distinct subjects one client may name inside
a window, and a client that keeps asking about the same address may ask forever.
``distinct_subject_refused`` is that limit.

The resend endpoints have the mirror-image problem. There the harm is not
learning something, it is *sending* something: each accepted call puts mail in
somebody else's inbox. Bounding it per client is the wrong key, because the
attacker is not the victim -- rotating source addresses defeats a per-client cap
completely while the mail keeps arriving at one mailbox. So the cap has to be
per *subject*, counted across all callers. ``subject_event_refused`` is that
limit, and it is deliberately actor-independent.

SUBJECTS ARE SALTED, NOT HASHED TO A DERIVED KEY
------------------------------------------------
Both tables are keyed by ``HMAC-SHA256(process_salt, subject)`` rather than by
the address, so a core dump or a debugger attached to a worker does not hand
over a list of the email addresses that recently touched the system.

The salt is 32 random bytes generated at import and never written down --
deliberately not a ``services.signing_keys`` purpose. A derived key would be
stable across processes, and stability buys nothing here: these tables are
per-process (see the fleet-multiplier note below), so a token minted in one
worker is never compared against one minted in another. An unguessable salt that
dies with the process is strictly the stronger choice, and it needs no
environment variable, no ``.env.example`` entry and no rotation story.

THE FLEET MULTIPLIER, STATED RATHER THAN IMPLIED
------------------------------------------------
This state is module-level, and the Procfile runs
``gunicorn --workers ${WEB_CONCURRENCY:-4}``. So a limit of 4 here permits up to
16 across the fleet, depending on which worker the balancer picks. That is the
same property ``bot.RATE_LIMIT_BUCKETS`` has had since it was written, and it is
recorded here for the same reason it is recorded there: the number in the table
is not the number an attacker meets. It still closes both holes -- unbounded
becomes bounded, which is the whole of the change -- and the shared-counter
second pass that ``services/sentinel/rate_limit.py`` provides for
``ABUSE_GUARD_PROTECTED`` can be pointed at these scopes later without
restating any policy, because the policy lives in the caller's table.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

#: Regenerated on every import. See the module docstring: stability across
#: processes would be a cost, not a feature, because the tables below are
#: per-process and never compared with another worker's.
_SALT = secrets.token_bytes(32)

#: actor token -> {subject token: newest sighting}. A subject already present is
#: refreshed rather than counted again, which is what lets an honest 4-second
#: poll run indefinitely.
_SUBJECTS_BY_ACTOR: dict[str, dict[str, float]] = {}

#: subject token -> [stamps]. Actor-independent on purpose.
_EVENTS_BY_SUBJECT: dict[str, list[float]] = {}

#: name -> (last sweep, widest window seen). Mirrors
#: ``security_guard.sweep_expired``'s bookkeeping; see ``_sweep``.
_SWEEPS: dict[str, tuple[float, float]] = {}

_SWEEP_MIN_INTERVAL = 60.0


@dataclass(frozen=True)
class Refusal:
    """Why the call was refused, in the terms the caller logs.

    ``count`` is what was already on record, not including the call being
    refused, so it reads as "you already had N of your N".
    """

    count: int
    limit: int
    window_seconds: int
    retry_after: int


def subject_token(subject: str) -> str:
    """The salted lookup key for one subject. Empty input yields empty output.

    Returning ``""`` rather than hashing the empty string matters: it gives the
    caller a value it can test, so a route that failed to extract a subject
    cannot accidentally file every such request under one shared bucket and
    refuse them all.
    """
    normalized = str(subject or "").strip().lower()
    if not normalized:
        return ""
    return hmac.new(_SALT, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def _absent(value: str) -> bool:
    """Whether a key component is missing.

    A named predicate rather than a bare ``not``, because the two callers below
    use it for the one decision this module cannot get wrong quietly: a missing
    component must not fall back to a shared bucket. The consequence is not a
    missed refusal, it is a *wrong* refusal -- junk requests, which are free to
    send, would spend the budget of whatever real client happens to be filed
    under the same empty key.
    """
    return not str(value or "").strip()


def _sweep(name: str, buckets: dict, window_seconds: float, now: float, newest) -> int:
    """Reclaim keys nothing can still be counting. Returns how many went.

    Not a policy change: a key whose newest entry is older than the widest
    window this table has been asked about filters to empty on its next read
    anyway, so dropping it produces the identical decision and returns the
    memory. ``widest`` only ever grows, so a 900-second key cannot be swept by a
    300-second call that arrives first.
    """
    last_sweep, widest = _SWEEPS.get(name, (0.0, 0.0))
    widest = max(widest, float(window_seconds))
    if now - last_sweep < _SWEEP_MIN_INTERVAL:
        _SWEEPS[name] = (last_sweep, widest)
        return 0
    horizon = now - widest
    stale = [key for key, value in list(buckets.items())
             if not value or newest(value) <= horizon]
    for key in stale:
        buckets.pop(key, None)
    _SWEEPS[name] = (now, widest)
    return len(stale)


def distinct_subject_refused(
    actor: str, subject: str, limit: int, window_seconds: int, *, now: float | None = None
) -> Refusal | None:
    """Bound how many *different* subjects one actor may name in a window.

    A subject this actor has already named inside the window is always allowed,
    and asking again slides its expiry forward. That is the property the polling
    client depends on and the property an enumerator cannot use: repetition is
    free, variety is not.

    An empty ``actor`` or ``subject`` is allowed through untouched. Both are the
    caller's to supply, and inventing a shared bucket for "no subject" would
    make one malformed request refuse the next honest one.
    """
    token = subject_token(subject)
    if _absent(actor) or _absent(token):
        return None
    now = time.time() if now is None else now
    window = max(1, int(window_seconds))

    seen = {
        held: stamp
        for held, stamp in _SUBJECTS_BY_ACTOR.get(actor, {}).items()
        if now - stamp < window
    }
    if token not in seen and len(seen) >= max(1, int(limit)):
        # Do not record the refused subject. Recording it would let an
        # enumerator keep its own oldest entry alive by retrying, so the window
        # would never drain and the actor would stay refused forever -- a
        # self-inflicted permanent block on whatever IP a real user shares with
        # the prober.
        _SUBJECTS_BY_ACTOR[actor] = seen
        oldest = min(seen.values())
        return Refusal(
            count=len(seen),
            limit=max(1, int(limit)),
            window_seconds=window,
            retry_after=max(1, int(window - (now - oldest))),
        )

    seen[token] = now
    _SUBJECTS_BY_ACTOR[actor] = seen
    _sweep("auth_subject_guard.subjects", _SUBJECTS_BY_ACTOR, window, now,
           lambda value: max(value.values()))
    return None


def subject_event_refused(
    subject: str, limit: int, window_seconds: int, *, now: float | None = None
) -> Refusal | None:
    """Bound how many times a thing may happen *to* one subject, by any caller.

    Used for the account-confirmation mail: the address being mailed is the
    party at risk, so it is the key. Counted across every actor, because an
    attacker who rotates source addresses is still filling one inbox.
    """
    token = subject_token(subject)
    if _absent(token):
        return None
    now = time.time() if now is None else now
    window = max(1, int(window_seconds))

    stamps = [stamp for stamp in _EVENTS_BY_SUBJECT.get(token, []) if now - stamp < window]
    if len(stamps) >= max(1, int(limit)):
        _EVENTS_BY_SUBJECT[token] = stamps
        return Refusal(
            count=len(stamps),
            limit=max(1, int(limit)),
            window_seconds=window,
            retry_after=max(1, int(window - (now - min(stamps)))),
        )

    stamps.append(now)
    _EVENTS_BY_SUBJECT[token] = stamps
    _sweep("auth_subject_guard.events", _EVENTS_BY_SUBJECT, window, now, max)
    return None


def reset() -> None:
    """Drop all state. For tests; nothing in a request path calls this."""
    _SUBJECTS_BY_ACTOR.clear()
    _EVENTS_BY_SUBJECT.clear()
    _SWEEPS.clear()
