"""Sentinel deterministic temporal sequence engine — Mission 3 (Stages 6–7).

Detects ORDERED chains of canonical events for one subject inside a bounded
time window. Pure SQL + arithmetic: no model participates in matching
(SC2/SC8), and firing a sequence only ever opens an incident — it never
blocks, bans, or invalidates anything (SC3).

Honesty rules:
- A chain whose OPTIONAL steps are missing fires as ``PARTIAL`` — the
  completeness is stored, never faked to FULL (Stage 7).
- A chain whose REQUIRED steps are missing does not fire at all.
- Every firing links the exact matched event ids (evidence linkage) and is
  deduplicated per (sequence, subject) with a cooldown so one episode cannot
  open an alert storm.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from services.sentinel import store

_TS = "%Y-%m-%d %H:%M:%S"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


@dataclass(frozen=True)
class SequenceStep:
    step_id: str
    event_types: tuple          # any of these event types satisfies the step
    min_count: int = 1          # occurrences required (e.g. failed-login burst)
    optional: bool = False      # missing optional step → PARTIAL, not silence


@dataclass(frozen=True)
class SequenceDefinition:
    sequence_id: str
    title: str
    steps: tuple                # ordered SequenceStep tuple
    window_minutes: int
    incident_type: str
    severity: str
    min_confidence: float = 0.0     # events below this are invisible to the chain
    cooldown_minutes: int = 240
    subject_scope: str = "subject_id"   # entity constraint: same subject
    description: str = ""

    def __post_init__(self):
        if not self.steps:
            raise ValueError("sequence needs at least one step")
        if all(s.optional for s in self.steps):
            raise ValueError("sequence needs at least one required step")
        if self.window_minutes <= 0 or self.window_minutes > 60 * 24 * 7:
            raise ValueError("window must be bounded (0, 7d]")


# ---------------------------------------------------------------------------
# ATO chain definitions (Stage 7). Event types are the REAL ones the bridge
# emits (security_center_bridge maps) — nothing here is a guessed name.
# ---------------------------------------------------------------------------

ATO_SEQUENCES = (
    SequenceDefinition(
        sequence_id="ATO1_RECOVERY_LOGIN_NEWDEVICE",
        title="Recovery followed by login and a never-seen device",
        steps=(
            SequenceStep("recovery", ("password_reset_requested",)),
            SequenceStep("login", ("login_succeeded",)),
            SequenceStep("new_device", ("unusual_device",), optional=True),
        ),
        window_minutes=120, incident_type="ACCOUNT_TAKEOVER_SUSPECTED",
        severity="high", cooldown_minutes=360,
        description="Classic takeover: reset the password, log in, appear on "
                    "a new device. Without the device event it is PARTIAL."),
    SequenceDefinition(
        sequence_id="ATO2_FAILBURST_LOGIN_NEWDEVICE",
        title="Failed-login burst, then success, then a never-seen device",
        steps=(
            SequenceStep("failed_burst", ("login_failed",), min_count=5),
            SequenceStep("login", ("login_succeeded",)),
            SequenceStep("new_device", ("unusual_device",), optional=True),
        ),
        window_minutes=90, incident_type="ACCOUNT_TAKEOVER_SUSPECTED",
        severity="high", cooldown_minutes=360,
        description="Guess-until-it-works followed by success."),
    SequenceDefinition(
        sequence_id="ATO3_RECOVERY_SESSION_PRIVCHANGE",
        title="Recovery, login, then a privileged account change",
        steps=(
            SequenceStep("recovery", ("password_reset_requested",)),
            SequenceStep("login", ("login_succeeded",)),
            SequenceStep("priv_change", ("email_changed", "email_change_failed")),
        ),
        window_minutes=240, incident_type="ACCOUNT_TAKEOVER_SUSPECTED",
        severity="high", cooldown_minutes=360,
        description="Takeover consolidation: recover, enter, rewire the "
                    "account's contact points."),
    SequenceDefinition(
        sequence_id="ATO4_NEWDEVICE_SENSITIVE",
        title="Never-seen device immediately touching sensitive account state",
        steps=(
            SequenceStep("new_device", ("unusual_device",)),
            SequenceStep("sensitive", ("email_changed", "sessions_revoked",
                                       "refresh_device_mismatch")),
        ),
        window_minutes=60, incident_type="ACCOUNT_TAKEOVER_SUSPECTED",
        severity="medium", cooldown_minutes=360,
        description="A brand-new device that goes straight for sensitive "
                    "state is worth a human look — not a verdict."),
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

def _load_events(c, seq: SequenceDefinition, now: datetime) -> dict[str, list]:
    """Bounded window read of candidate events, grouped by subject. Only the
    event types the sequence cares about are loaded (Stage 30)."""
    all_types = sorted({t for s in seq.steps for t in s.event_types})
    placeholders = ",".join("?" for _ in all_types)
    cutoff = _fmt(now - timedelta(minutes=seq.window_minutes))
    cur = c.cursor()
    try:
        cur.execute(
            f"SELECT event_id, event_type, subject_id, occurred_at, confidence "
            f"FROM sentinel_events WHERE event_type IN ({placeholders}) "
            f"AND occurred_at >= ? AND subject_id IS NOT NULL "
            f"ORDER BY occurred_at ASC LIMIT 5000",
            (*all_types, cutoff))
        rows = cur.fetchall()
    except Exception:
        return {}
    by_subject: dict[str, list] = {}
    for r in rows:
        if float(r[4] or 0.0) < seq.min_confidence:
            continue
        by_subject.setdefault(str(r[2]), []).append(
            {"event_id": str(r[0]), "event_type": str(r[1]),
             "occurred_at": str(r[3])})
    return by_subject


def _match_steps(seq: SequenceDefinition, timeline: list) -> dict | None:
    """Greedy in-order match. Returns None unless every REQUIRED step matches
    in order; optional gaps degrade completeness to PARTIAL."""
    idx = 0
    matched: list[dict] = []
    missing_optional: list[str] = []
    for step in seq.steps:
        count, step_events = 0, []
        j = idx
        while j < len(timeline) and count < step.min_count:
            if timeline[j]["event_type"] in step.event_types:
                step_events.append(timeline[j])
                count += 1
            j += 1
        if count >= step.min_count:
            matched.extend(step_events)
            # Order constraint: the next step must start after this step's
            # last matched event.
            idx = timeline.index(step_events[-1]) + 1
        elif step.optional:
            missing_optional.append(step.step_id)
        else:
            return None
    return {
        "completeness": "PARTIAL" if missing_optional else "FULL",
        "missing_optional_steps": missing_optional,
        "matched_event_ids": [e["event_id"] for e in matched],
        "first_at": matched[0]["occurred_at"] if matched else "",
        "last_at": matched[-1]["occurred_at"] if matched else "",
    }


def _in_cooldown(c, seq: SequenceDefinition, subject_ref: str, now: datetime) -> bool:
    cur = c.cursor()
    try:
        cur.execute(
            "SELECT 1 FROM sentinel_sequence_firings WHERE sequence_id = ? "
            "AND subject_ref = ? AND cooldown_until > ? LIMIT 1",
            (seq.sequence_id, subject_ref, _fmt(now)))
        return cur.fetchone() is not None
    except Exception:
        return False


def evaluate(seq: SequenceDefinition, conn=None, *,
             now: datetime | None = None) -> list[dict]:
    """Evaluate one sequence over the live event stream. Returns firings
    (dedup/cooldown already applied); does NOT open incidents — the caller
    (identity_detections) owns incident correlation so multiple chains can
    still collapse into ONE incident per subject (Stage 20)."""
    now = now or _utcnow()
    firings: list[dict] = []
    with store.connection(conn) as c:
        by_subject = _load_events(c, seq, now)
        for subject, timeline in by_subject.items():
            result = _match_steps(seq, timeline)
            if not result:
                continue
            subject_ref = f"user:{subject}" if ":" not in subject else subject
            if _in_cooldown(c, seq, subject_ref, now):
                continue
            cur = c.cursor()
            cur.execute(
                """INSERT INTO sentinel_sequence_firings
                   (sequence_id, subject_ref, fired_at, cooldown_until,
                    completeness, matched_event_ids_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (seq.sequence_id, subject_ref, _fmt(now),
                 _fmt(now + timedelta(minutes=seq.cooldown_minutes)),
                 result["completeness"],
                 json.dumps(result["matched_event_ids"])))
            firings.append({
                "sequence_id": seq.sequence_id, "title": seq.title,
                "subject_ref": subject_ref,
                "incident_type": seq.incident_type, "severity": seq.severity,
                **result})
    return firings


def evaluate_all(definitions=ATO_SEQUENCES, conn=None, *,
                 now: datetime | None = None) -> list[dict]:
    """Evaluate every definition; one failure never blocks the rest."""
    out: list[dict] = []
    for seq in definitions:
        try:
            out.extend(evaluate(seq, conn=conn, now=now))
        except Exception as exc:  # noqa: BLE001 — containment by design
            out.append({"sequence_id": seq.sequence_id, "error": str(exc)[:200],
                        "subject_ref": None})
    return out
