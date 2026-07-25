"""Business OS — Advertising slice 7 frequency-cap service.

Server-authoritative per-viewer, per-campaign impression cap (spec §5). The cap
is DERIVED from the immutable ``business_os_ad_impression_events`` log — there is
no separate counter table to drift out of sync. A viewer is identified only by
``subject_ref`` (salted hash), so the cap works per-viewer WITHOUT storing any
raw user id and WITHOUT exposing cross-user history.

The count is ``impressions where campaign_id = ? AND subject_ref = ? AND
event_at >= (now - window)``. This is backed by the ``idx_ad_impr_freq`` index on
``(campaign_id, subject_ref, event_at)``. Duplicate/retried impressions collide
on ``dedup_key`` upstream, so they never inflate this count.

Defaults are conservative and safely overridable via env (see delivery_common):
``FREQ_CAP_MAX`` impressions per ``FREQ_CAP_WINDOW_SECONDS`` rolling window.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from . import delivery_common as _c


def _window_start_iso(window_seconds: Optional[int] = None) -> str:
    """ISO timestamp marking the start of the rolling window ending now."""
    win = _c.FREQ_CAP_WINDOW_SECONDS if window_seconds is None else window_seconds
    return _c.iso(_c.now_utc() - timedelta(seconds=win))


def impression_count(conn, campaign_id: str, subject_ref: str,
                     window_seconds: Optional[int] = None) -> int:
    """Number of impressions for this viewer+campaign within the rolling window.

    Derived directly from the immutable impression log. Only rows whose
    ``event_at`` is within the window are counted.
    """
    start = _window_start_iso(window_seconds)
    cur = conn.execute(
        "SELECT COUNT(*) AS n FROM business_os_ad_impression_events "
        "WHERE campaign_id = ? AND subject_ref = ? AND event_at >= ?",
        (_c.sid(campaign_id), _c.sid(subject_ref), start),
    )
    row = cur.fetchone()
    if row is None:
        return 0
    try:
        return int(row["n"])
    except Exception:
        # positional fallback for drivers without mapping access
        return int(row[0])


def cap_reached(conn, campaign_id: str, subject_ref: str,
                cap_max: Optional[int] = None,
                window_seconds: Optional[int] = None) -> bool:
    """True when the viewer has already reached the cap for this campaign.

    Called at candidate-selection time (before a NEW delivery is offered): if the
    viewer already has >= cap_max impressions in the window, no further delivery
    should be created for that campaign.
    """
    cap = _c.FREQ_CAP_MAX if cap_max is None else cap_max
    if cap <= 0:
        # A non-positive cap means "no frequency limit configured": never blocks.
        return False
    return impression_count(conn, campaign_id, subject_ref, window_seconds) >= cap


def frequency_state(conn, campaign_id: str, subject_ref: str,
                    cap_max: Optional[int] = None,
                    window_seconds: Optional[int] = None) -> dict:
    """Structured snapshot for eligibility/observability. No raw ids exposed."""
    cap = _c.FREQ_CAP_MAX if cap_max is None else cap_max
    win = _c.FREQ_CAP_WINDOW_SECONDS if window_seconds is None else window_seconds
    count = impression_count(conn, campaign_id, subject_ref, win)
    reached = (cap > 0) and (count >= cap)
    return {
        "count": count,
        "cap_max": cap,
        "window_seconds": win,
        "cap_reached": reached,
        "remaining": (max(cap - count, 0) if cap > 0 else None),
    }
