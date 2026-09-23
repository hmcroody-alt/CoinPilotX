"""Recording that a subject was exposed to an arm — as a log line, not a table.

The obvious implementation is an ``experiment_exposures`` table. It is not what
this does, for the reason stated in ``assignment``: production holds 111 event
and audit tables, 62 of them empty, and the defect this programme was chartered
against is fragmentation rather than missing ingest. A 112th would have been
written at a rate of roughly one row a week against today's traffic.

So exposure is an operational log line, the same shape as
``PULSE_ANALYTICS_FUNNEL_SERVED``:

    PULSE_EXPERIMENT_EXPOSURE experiment=… variant=… subject=… reason=…
      in_experiment=…

``reason`` is carried because ``variant=control`` is ambiguous on its own: it is
produced by an assignment, by a subject outside the rollout, by a disabled
experiment and by the global kill switch, and an operator reading a week of
these needs to tell a working experiment from a switched-off one.

What the subject field is
-------------------------

A salted hash, never the ``user_id``. It is stable, so the same person is the
same token across lines — which is exactly enough to count an arm and exactly
enough to be a behavioural profile if it is ever joined to something richer.
That is the tension ``commerce_discovery/subject.py`` already names about its
own table, and the answer here is the same: the hash is not reversible, it uses
this package's own salt, and it is written to a log rather than a queryable
store, so it accretes no commercial detail beside it.

Emission is at most once per subject per experiment per process
---------------------------------------------------------------

Not for volume. A caller checking a variant three times while rendering one
page would otherwise triple that subject's exposure count, and an arm's
denominator would then measure how many times the template asked rather than
how many people saw it. The cache is per-process and deliberately not shared:
its job is to suppress a burst within one request, not to be a durable record
of who has ever been exposed.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from services.pulse_experiments import config
from services.pulse_experiments.assignment import Assignment

LOGGER = logging.getLogger(__name__)

#: Per-process, bounded. Beyond this the cache stops admitting new entries
#: rather than growing without limit — a worker that has served this many
#: distinct subject/experiment pairs will re-log some of them, which overcounts
#: slightly and is a better failure than unbounded memory in a web process.
MAX_TRACKED = 50000

_seen: set[tuple[str, str]] = set()


def subject_token(subject: Any) -> str:
    """Stable, non-reversible reference for one subject under this package's salt."""
    raw = f"{config.assignment_salt()}:exposure:{subject}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def reset() -> None:
    """Clear the per-process suppression set. For tests and worker recycling."""
    _seen.clear()


def record(assignment: Assignment, subject: Any, *, repeat: bool = False) -> bool:
    """Log one exposure. Returns whether a line was written.

    ``repeat=True`` forces a line even if this pair has been seen, for a caller
    that genuinely wants every occurrence rather than every subject.
    """
    if assignment is None or not getattr(assignment, "experiment_key", ""):
        return False

    token = subject_token(subject)
    pair = (assignment.experiment_key, token)
    if not repeat:
        if pair in _seen:
            return False
        if len(_seen) < MAX_TRACKED:
            _seen.add(pair)

    LOGGER.info(
        "PULSE_EXPERIMENT_EXPOSURE experiment=%s variant=%s subject=%s reason=%s "
        "in_experiment=%s",
        assignment.experiment_key,
        assignment.variant,
        token,
        assignment.reason,
        "1" if assignment.in_experiment else "0",
    )
    return True
