"""Event ingest: envelope validation, deduplication, and data quality.

This is the front door of the whole subsystem, and everything downstream —
metrics, pacing, the interest graph, the ranker, billing vetoes — is a
projection of what this module accepts. So it is deliberately strict, and it
fails closed.

The shape of the problem
------------------------
Ad events arrive from a phone. A phone is an untrusted, unreliable narrator: it
retries, it batches, it comes back online with an hour-old queue, its clock is
wrong, and a modified client can say anything at all. Three separate defences,
because they fail in different ways:

* **Idempotency** (``dedup_key``, UNIQUE in the schema). Handles the *honest*
  duplicate — a retry after a dropped response. The uniqueness constraint is the
  authority, not a pre-check: two concurrent workers both passing a SELECT and
  then both inserting is exactly the race that inflates an impression count, so
  we let the database arbitrate and treat the IntegrityError as a normal,
  expected outcome rather than an error.

* **Envelope validation.** Handles the *malformed* event. Unknown event name,
  missing campaign, absent dedup key, a timestamp from next week: rejected at
  the boundary, never stored. A rejected event is counted in the batch record so
  a broken client release is visible as a spike in rejections rather than as an
  unexplained hole in a metric.

* **Data quality.** Handles the *plausible but wrong* event, which is the
  dangerous class because it stores cleanly. A viewable claiming 4% visibility,
  a dwell time of eleven minutes on a feed card, a click with no decision behind
  it. These are stored — throwing them away would destroy the evidence that the
  client is broken — but flagged, and a flagged event is excluded from billing.

Two rules that are not negotiable
---------------------------------
**A client may never assert a purchase.** ``CLIENT_FORBIDDEN_EVENTS`` are
rejected outright rather than stored as suspect, so there is no path by which one
could later be reclassified as valid. Conversions are derived server-side from
the canonical order records or they do not exist.

**This module cannot bill anything.** It computes a ``billable`` flag, which is
a *candidate* marker meaning "not disqualified by data quality". The canonical
billing path still decides. The flag can only ever subtract.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from services import db

from . import invalid_traffic, privacy, taxonomy
from .schema import ensure_schema, new_id, utc_now_iso

# How far out of step with the server an event's own timestamp may be.
#
# Backwards is generous: offline queues, airplane mode, and a phone that was in a
# tunnel all legitimately deliver hours late, and discarding those would bias
# every metric toward users with good connectivity.
#
# Forwards is tight: an event cannot happen in the future, so anything
# meaningfully ahead of the server clock is either a broken device clock or a
# forgery. Two minutes absorbs ordinary clock skew and nothing else.
MAX_EVENT_AGE_HOURS = 48
MAX_EVENT_FUTURE_MINUTES = 2

# Above this, a reported dwell time on a feed card is not a person looking at an
# ad — it is a screen left on, an app suspended without a lifecycle callback, or
# a counter that never stopped. Stored, flagged, not billed.
MAX_PLAUSIBLE_DURATION_MS = 300_000  # 5 minutes

MAX_BATCH_EVENTS = 500

_FAMILY_BY_EVENT = {}
for _family, _names in (
    ("opportunity", taxonomy.OPPORTUNITY_EVENTS),
    ("delivery", taxonomy.DELIVERY_EVENTS),
    ("engagement", taxonomy.ENGAGEMENT_EVENTS),
    ("negative", taxonomy.NEGATIVE_EVENTS),
    ("conversion", taxonomy.CONVERSION_EVENTS),
):
    for _n in _names:
        _FAMILY_BY_EVENT[_n] = _family


def event_family(event_name: str) -> str:
    return _FAMILY_BY_EVENT.get(str(event_name or ""), "unknown")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _row_to_dict(row) -> dict | None:
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return dict(row)
    except Exception:
        pass
    try:
        return {k: row[k] for k in row.keys()}
    except Exception:
        return None


def _as_int(value):
    """Int or None. Never raises — callers are parsing untrusted client JSON."""
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _parse_ts(value):
    """Parse an ISO-8601 timestamp into an aware UTC datetime, or None.

    Accepts the trailing ``Z`` the clients actually send, and treats a naive
    timestamp as UTC rather than as local time — the alternative silently shifts
    every event by the server's offset.
    """
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- #
# Envelope validation
# --------------------------------------------------------------------------- #

def validate_envelope(payload: dict, *, now: datetime | None = None,
                      ingest_source: str = "client") -> dict:
    """Check one raw event. Returns ``{"ok": bool, "reason": str|None, ...}``.

    ``ingest_source`` is the caller's trust level and is deliberately a keyword
    argument rather than a field read off ``payload``. If it were read from the
    payload, a modified client could simply declare ``"ingest_source": "server"``
    in its own JSON and mint ``ad_purchase_completed`` events — the forbidden
    check below would wave them straight through. Only the route knows whether
    it is handling an untrusted client batch or a server-derived event, so only
    the route may say so.

    Pure: no database access, no side effects, so it is cheap to call from a
    route before opening a connection and trivially testable.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(payload, dict):
        return {"ok": False, "reason": "SCHEMA_INVALID",
                "detail": "event is not an object"}

    name = str(payload.get("event_name") or "").strip()
    if name not in taxonomy.ALL_EVENT_NAMES:
        return {"ok": False, "reason": "SCHEMA_INVALID",
                "detail": f"unknown event_name {name!r}"}

    # A client asserting revenue is rejected, never stored. See module docstring.
    if name in taxonomy.CLIENT_FORBIDDEN_EVENTS and ingest_source == "client":
        return {"ok": False, "reason": "CLIENT_ASSERTED_CONVERSION",
                "detail": f"{name} may not be asserted by a client"}

    source = payload.get("signal_source")
    if source is not None and privacy.is_forbidden_source(source):
        return {"ok": False, "reason": "FORBIDDEN_SOURCE",
                "detail": "signal origin is barred from the ad system"}

    if not str(payload.get("dedup_key") or "").strip():
        return {"ok": False, "reason": "SCHEMA_INVALID",
                "detail": "dedup_key is required"}

    occurred = _parse_ts(payload.get("occurred_at"))
    if occurred is None:
        return {"ok": False, "reason": "SCHEMA_INVALID",
                "detail": "occurred_at is missing or unparseable"}
    if occurred > now + timedelta(minutes=MAX_EVENT_FUTURE_MINUTES):
        return {"ok": False, "reason": "IMPLAUSIBLE_TIMESTAMP",
                "detail": "occurred_at is in the future"}
    if occurred < now - timedelta(hours=MAX_EVENT_AGE_HOURS):
        return {"ok": False, "reason": "IMPLAUSIBLE_TIMESTAMP",
                "detail": "occurred_at is older than the accepted window"}

    # Everything except an opportunity is *about* a specific delivery, and
    # without a decision it cannot be joined to one. Storing it would produce a
    # row that inflates a total but explains nothing.
    if event_family(name) != "opportunity":
        if not str(payload.get("decision_id") or "").strip():
            return {"ok": False, "reason": "UNKNOWN_DECISION",
                    "detail": f"{name} requires a decision_id"}

    return {"ok": True, "reason": None, "occurred_at": occurred}


# --------------------------------------------------------------------------- #
# Data quality
# --------------------------------------------------------------------------- #

def assess_quality(payload: dict, *, event_name: str) -> dict:
    """Flag plausible-but-wrong events. Returns status, notes, and billability.

    Never rejects — the caller stores the event either way. The output is
    advisory in exactly one direction: it can withhold ``billable``, it can
    never grant it beyond what the taxonomy already allows.
    """
    notes = []
    status = "ok"

    percent = _as_int(payload.get("percent_visible"))
    duration = _as_int(payload.get("duration_ms"))
    is_video = bool(payload.get("is_video"))
    foreground = payload.get("foreground")
    foreground = True if foreground is None else bool(foreground)

    if event_name == "ad_viewable":
        # The client already applies a stricter threshold than the server
        # requires, so a viewable that fails the server contract means the
        # client is lying or broken. Either way it must not be billed.
        if not taxonomy.viewability_met(percent, duration, is_video=is_video,
                                        foreground=foreground):
            status = "suspect"
            notes.append(
                f"viewability contract not met (percent={percent}, "
                f"duration_ms={duration}, video={is_video}, fg={foreground})")

    if percent is not None and not (0 <= percent <= 100):
        status = "suspect"
        notes.append(f"percent_visible out of range: {percent}")

    if duration is not None:
        if duration < 0:
            status = "suspect"
            notes.append(f"negative duration_ms: {duration}")
        elif duration > MAX_PLAUSIBLE_DURATION_MS:
            status = "suspect"
            notes.append(f"implausible duration_ms: {duration}")

    value_cents = _as_int(payload.get("value_cents"))
    if value_cents is not None and value_cents < 0:
        status = "suspect"
        notes.append(f"negative value_cents: {value_cents}")

    billable = (event_name in taxonomy.BILLABLE_CANDIDATE_EVENTS
                and status == "ok")
    return {"quality_status": status,
            "quality_notes": "; ".join(notes) or None,
            "billable": billable}


# --------------------------------------------------------------------------- #
# Ingest
# --------------------------------------------------------------------------- #

def _merge_notes(*parts) -> str | None:
    """Join note fragments without letting one finding erase another.

    Quality and validity are separate judgements about the same row and both
    are worth keeping: "viewability contract not met" plus "acted 40ms after
    the impression" describes a broken client, while either alone is ambiguous.
    """
    kept = [str(p).strip() for p in parts if p and str(p).strip()]
    return " | ".join(kept) or None


def _insert_event(conn, payload: dict, validation: dict, *,
                  batch_key: str | None, ingest_source: str) -> str | None:
    """Insert one validated event. Returns the id, or None when it is a dupe.

    The UNIQUE violation on ``dedup_key`` is the expected path for a retry, not
    an error condition — see the module docstring on why the database, and not a
    pre-check, is the authority here.
    """
    name = str(payload["event_name"]).strip()
    quality = assess_quality(payload, event_name=name)
    occurred = validation["occurred_at"]
    now_iso = utc_now_iso()

    # Screen for invalid traffic before the row exists rather than only in the
    # later sweep. The sweep is the safety net, not the control: an event that
    # is stored valid and billable is visible to the billing path in the
    # interval before the sweep runs, and that interval is exactly when a
    # click-farm is at its most productive. Screening is advisory in one
    # direction only — like `assess_quality`, it can withhold billability and
    # can never grant it.
    verdict = invalid_traffic.screen(conn, {**payload, "event_name": name},
                                     now=validation.get("occurred_at"))
    validity = verdict.get("validity") or "valid"
    invalid_reason = verdict.get("reason")
    billable = bool(quality["billable"]) and validity == "valid"

    subject = payload.get("subject_ref")
    if subject is None and payload.get("user_id") is not None:
        subject = privacy.subject_ref(payload.get("user_id"))
    session = payload.get("session_ref")
    if session is None and payload.get("session_id") is not None:
        session = privacy.session_ref(payload.get("session_id"))

    meta = payload.get("meta")
    try:
        meta_json = json.dumps(meta) if meta is not None else None
    except (TypeError, ValueError):
        meta_json = None

    event_id = new_id()
    try:
        conn.execute(
            """
            INSERT INTO ads_intel_events (
                event_id, dedup_key, event_name, event_family, occurred_at,
                received_at, subject_ref, session_ref, decision_id, campaign_id,
                creative_id, placement_key, platform, app_version, surface,
                percent_visible, duration_ms, value_cents, currency, validity,
                invalid_reason, billable, quality_status, quality_notes,
                schema_version, processing_version, ingest_source, batch_key,
                meta_json, privacy_class, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                str(payload["dedup_key"]).strip(),
                name,
                event_family(name),
                occurred.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
                now_iso,
                subject,
                session,
                (str(payload.get("decision_id")).strip()
                 if payload.get("decision_id") else None),
                (str(payload.get("campaign_id")).strip()
                 if payload.get("campaign_id") else None),
                (str(payload.get("creative_id")).strip()
                 if payload.get("creative_id") else None),
                payload.get("placement_key"),
                payload.get("platform"),
                payload.get("app_version"),
                payload.get("surface"),
                _as_int(payload.get("percent_visible")),
                _as_int(payload.get("duration_ms")),
                _as_int(payload.get("value_cents")),
                payload.get("currency"),
                validity,
                invalid_reason,
                1 if billable else 0,
                quality["quality_status"],
                _merge_notes(quality["quality_notes"], verdict.get("detail")),
                taxonomy.EVENT_SCHEMA_VERSION,
                taxonomy.PROCESSING_VERSION,
                ingest_source,
                batch_key,
                meta_json,
                # Pinned at write time. The class is derivable from the event
                # name, but deriving it on read would let a future
                # reclassification retroactively widen what an already-collected
                # signal may be used for.
                privacy.classify_event(name),
                now_iso,
            ),
        )
        return event_id
    except Exception as exc:
        # Distinguish "already seen" from a genuine failure. The engines phrase
        # the constraint violation differently, so match on the shared shape
        # rather than on an engine-specific exception class.
        text = str(exc).lower()
        if "unique" in text or "duplicate" in text:
            return None
        raise


def ingest_batch(payload: dict, *, conn=None, ingest_source: str = "client") -> dict:
    """Ingest a batch of events idempotently.

    Returns counts plus per-event rejection reasons. Batch-level idempotency is
    separate from event-level: a client that retries a whole batch because it
    never saw the response is answered from ``ads_intel_ingest_batches`` without
    re-parsing, while a partially-overlapping batch still gets each event
    arbitrated individually by the UNIQUE constraint.
    """
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        ensure_schema(conn)

        events = payload.get("events")
        if not isinstance(events, list):
            return {"ok": False, "error": "events must be a list",
                    "received": 0, "accepted": 0, "duplicate": 0, "rejected": 0}
        if len(events) > MAX_BATCH_EVENTS:
            return {"ok": False, "error": f"batch exceeds {MAX_BATCH_EVENTS} events",
                    "received": len(events), "accepted": 0, "duplicate": 0,
                    "rejected": 0}

        batch_key = str(payload.get("batch_key") or "").strip() or None
        if batch_key:
            existing = _row_to_dict(conn.execute(
                "SELECT * FROM ads_intel_ingest_batches WHERE batch_key = ?",
                (batch_key,)).fetchone())
            if existing:
                # Replay of an already-processed batch: answer from the record.
                return {"ok": True, "replayed": True,
                        "received": int(existing.get("received_count") or 0),
                        "accepted": int(existing.get("accepted_count") or 0),
                        "duplicate": int(existing.get("duplicate_count") or 0),
                        "rejected": int(existing.get("rejected_count") or 0),
                        "reject_reasons": json.loads(
                            existing.get("reject_reasons_json") or "{}")}

        now = datetime.now(timezone.utc)
        accepted = duplicate = rejected = 0
        reasons: dict[str, int] = {}
        accepted_ids = []

        for raw in events:
            validation = validate_envelope(raw, now=now,
                                           ingest_source=ingest_source)
            if not validation["ok"]:
                rejected += 1
                key = validation["reason"]
                reasons[key] = reasons.get(key, 0) + 1
                continue
            try:
                event_id = _insert_event(conn, raw, validation,
                                         batch_key=batch_key,
                                         ingest_source=ingest_source)
            except Exception:
                # One bad row must not discard the rest of the batch; the client
                # would retry the whole thing and we would lose the good events
                # again on the next pass.
                logging.exception("ADS_INTEL_EVENT_INSERT_FAILED")
                rejected += 1
                reasons["SCHEMA_INVALID"] = reasons.get("SCHEMA_INVALID", 0) + 1
                continue
            if event_id is None:
                duplicate += 1
            else:
                accepted += 1
                accepted_ids.append(event_id)

        subject = None
        if events:
            first = events[0] if isinstance(events[0], dict) else {}
            subject = first.get("subject_ref")
            if subject is None and first.get("user_id") is not None:
                subject = privacy.subject_ref(first.get("user_id"))

        if batch_key:
            try:
                conn.execute(
                    """
                    INSERT INTO ads_intel_ingest_batches (
                        batch_id, batch_key, subject_ref, received_count,
                        accepted_count, duplicate_count, rejected_count,
                        reject_reasons_json, ingest_source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (new_id(), batch_key, subject, len(events), accepted,
                     duplicate, rejected, json.dumps(reasons), ingest_source,
                     utc_now_iso()),
                )
            except Exception:
                # Two concurrent deliveries of the same batch: the events were
                # already arbitrated by their own UNIQUE keys, so losing the
                # batch record is harmless bookkeeping, not data loss.
                logging.info("ADS_INTEL_BATCH_RECORD_EXISTS batch_key=%s", batch_key)

        if owned:
            conn.commit()
        return {"ok": True, "replayed": False, "received": len(events),
                "accepted": accepted, "duplicate": duplicate,
                "rejected": rejected, "reject_reasons": reasons,
                "event_ids": accepted_ids}
    finally:
        if owned:
            try:
                conn.close()
            except Exception:
                pass


def record_event(payload: dict, *, conn=None, ingest_source: str = "server") -> dict:
    """Ingest a single event. Thin wrapper over :func:`ingest_batch`.

    Server-derived events (notably conversions, which a client may not assert)
    come through here with ``ingest_source='server'``.
    """
    result = ingest_batch({"events": [payload]}, conn=conn,
                          ingest_source=ingest_source)
    if not result.get("ok"):
        return result
    return {
        "ok": True,
        "accepted": result["accepted"] == 1,
        "duplicate": result["duplicate"] == 1,
        "rejected": result["rejected"] == 1,
        "reject_reasons": result.get("reject_reasons") or {},
        "event_id": (result.get("event_ids") or [None])[0],
    }
