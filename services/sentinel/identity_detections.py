"""Sentinel identity threat detections — Mission 3 (Stages 7–14, 19–20, 25, 27).

Every rule is SQL + arithmetic over the canonical event stream and the
platform's own session/device tables (read-only). No LLM participates
(SC2/SC8). No rule blocks, bans, locks out, invalidates a session, or
touches funds — the strongest thing this module can do is open an incident
and write a recommendation a HUMAN may act on (SC3, Stage 25/35).

Detection ≠ guilt. Thresholds are deliberately conservative (Stage 10):
false accusations erode trust in the whole system. Shared devices are a
fact, not an accusation — families, businesses, and support teams share
devices legitimately (Stage 11).

Correlation (Stage 20): all ATO-shaped signals for one subject collapse
into ONE ACCOUNT_TAKEOVER_SUSPECTED incident per subject per day; new
signals land as observations on the same incident, never as duplicates.

Exclusions (Stage 27): the only way to exempt a subject from a rule is an
explicit, versioned, time-bounded row in sentinel_detection_exclusions,
written to the evidence chain. There are no code-level exceptions.
"""

from __future__ import annotations

import json
import statistics
from datetime import datetime, timedelta, timezone

from services.sentinel import evidence, incidents, sequences, store
from services.sentinel.identity import SENTINEL_CORRELATOR
from services.sentinel.constitution import CONSTITUTION_VERSION

_TS = "%Y-%m-%d %H:%M:%S"

# --- The identity rulebook: explicit, conservative thresholds ---------------
STUFFING_FANOUT_MIN_ACCOUNTS = 6       # one network probing many accounts
STUFFING_FANOUT_MIN_FAILURES = 12
STUFFING_FANOUT_WINDOW_MINUTES = 30
STUFFING_FANIN_MIN_NETWORKS = 5        # many networks probing one account
STUFFING_FANIN_WINDOW_MINUTES = 30
RECOVERY_V2_MIN_TARGETS = 5            # one network requesting recovery widely
RECOVERY_V2_WINDOW_MINUTES = 60
SESSION_BURST_THRESHOLD = 6            # successful logins per subject per window
SESSION_BURST_WINDOW_MINUTES = 30
SHARED_DEVICE_MIN_USERS = 5            # conservative: 2–4 users is normal life
NETWORK_MANY_ACCOUNTS_THRESHOLD = 8    # distinct accounts active per network
NETWORK_MANY_ACCOUNTS_WINDOW_MINUTES = 60
ADMIN_UNSEEN_NETWORK_HISTORY_DAYS = 30
BASELINE_WINDOW_DAYS = 14              # rolling window for explainable baselines
BASELINE_DEVIATION_FACTOR = 3.0        # today >= 3 x median → deviation
BASELINE_MIN_HISTORY_DAYS = 5          # below this the baseline is honest: none


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime(_TS)


def _cutoff(minutes: int, now: datetime) -> str:
    return _fmt(now - timedelta(minutes=minutes))


def _day(now: datetime) -> str:
    return now.strftime("%Y-%m-%d")


def _rows(cur, sql: str, params=()):
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    except Exception:
        return None  # source absent → rule reports skipped, never crashes


# ---------------------------------------------------------------------------
# Exclusions (Stage 27) — explicit, versioned, audited, time-bounded
# ---------------------------------------------------------------------------

def add_exclusion(rule_id: str, subject_ref: str, reason: str,
                  created_by: str, ttl_minutes: int, conn=None) -> dict:
    """The ONLY exemption mechanism. Reason, author and expiry are mandatory;
    the exclusion itself becomes evidence — never a silent code branch."""
    if not str(reason or "").strip():
        raise ValueError("exclusion requires a reason (Stage 27)")
    if not str(created_by or "").strip():
        raise ValueError("exclusion requires an author (SC12)")
    ttl = int(ttl_minutes)
    if ttl <= 0 or ttl > 60 * 24 * 90:
        raise ValueError("exclusion expiry must be bounded (0, 90d]")
    expires = _fmt(_utcnow() + timedelta(minutes=ttl))
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """INSERT INTO sentinel_detection_exclusions
               (rule_id, subject_ref, reason, created_by, expires_at, policy_version)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(rule_id, subject_ref) DO UPDATE SET
               reason = excluded.reason, created_by = excluded.created_by,
               expires_at = excluded.expires_at,
               policy_version = excluded.policy_version""",
            (str(rule_id), str(subject_ref), str(reason)[:500],
             str(created_by), expires, CONSTITUTION_VERSION))
        evidence.append("detection_exclusion", created_by,
                        {"rule_id": rule_id, "subject_ref": subject_ref,
                         "reason": str(reason)[:500], "expires_at": expires},
                        conn=c)
    return {"rule_id": rule_id, "subject_ref": subject_ref, "expires_at": expires}


def is_excluded(rule_id: str, subject_ref: str, conn=None, *,
                now: datetime | None = None) -> bool:
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT 1 FROM sentinel_detection_exclusions "
                     "WHERE rule_id = ? AND subject_ref = ? AND expires_at > ? LIMIT 1",
                     (str(rule_id), str(subject_ref), _fmt(now)))
    return bool(rows)


# ---------------------------------------------------------------------------
# Incident helper — exclusion-aware, evidence-linked
# ---------------------------------------------------------------------------

def _open(c, rule_id: str, subject: str, incident_type: str, severity: str,
          title: str, measurement: str, threshold: str, now: datetime,
          *, owner_action: bool = False, event_ids: tuple = (),
          extra_detail: dict | None = None,
          dedupe_components: tuple | None = None) -> dict | None:
    if is_excluded(rule_id, subject, conn=c, now=now):
        return None
    components = dedupe_components or (rule_id, subject, _day(now))
    key = incidents.dedupe_key(*components)
    detail = {"rule_id": rule_id, "subject": subject,
              "measurement": measurement, "threshold": threshold,
              "recommended_actions": recommend_actions(incident_type)}
    if extra_detail:
        detail.update(extra_detail)
    ref = incidents.open_incident(
        key, incident_type, severity, title, SENTINEL_CORRELATOR.actor_id,
        detail=detail, owner_action_required=owner_action,
        event_ids=event_ids, conn=c)
    return {"rule_id": rule_id, "subject": subject, "incident_key": key,
            "created": ref.created, "measurement": measurement,
            "threshold": threshold}


# ---------------------------------------------------------------------------
# Safe response recommendations (Stage 25) — documentation, never execution
# ---------------------------------------------------------------------------

# The complete vocabulary of things Sentinel may RECOMMEND. Nothing in this
# module (or anywhere in sentinel) executes any of them (Stage 35: every
# automation switch stays OFF).
SAFE_RECOMMENDATIONS = {
    "CREDENTIAL_STUFFING": (
        "rate-limit login attempts from the flagged network (human-applied)",
        "require step-up challenge for affected accounts on next login",
        "owner review of the flagged network's recent activity"),
    "RECOVERY_ABUSE": (
        "rate-limit recovery requests from the flagged source (human-applied)",
        "owner review of recovery volume; keep responses enumeration-safe"),
    "ACCOUNT_TAKEOVER_SUSPECTED": (
        "require re-authentication on next sensitive action (human-applied)",
        "owner review of the account's session/device timeline",
        "offer the account owner a guided credential rotation"),
    "SESSION_ANOMALY": (
        "owner review of the session timeline",
        "recommend re-authentication for the affected session (human-applied)"),
    "DEVICE_ANOMALY": (
        "owner review — shared devices are frequently legitimate "
        "(family/business/support); verify context before any action",),
    "NETWORK_ANOMALY": (
        "owner review of accounts active on the flagged network",
        "consider human-applied rate limits if abuse is confirmed"),
    "ADMIN_IDENTITY_ANOMALY": (
        "owner review of the admin's recent audited actions",
        "recommend re-authentication for the admin session (human-applied); "
        "never auto-lockout — a false positive must not lock out a real admin"),
    "COORDINATED_IDENTITY_ABUSE": (
        "owner review of the correlated cluster",
        "escalate to owner with full evidence timeline"),
}


def recommend_actions(incident_type: str) -> list[str]:
    return list(SAFE_RECOMMENDATIONS.get(incident_type, ("owner review",)))


# ---------------------------------------------------------------------------
# ATO correlation (Stages 7 + 20): many chains, ONE incident per subject/day
# ---------------------------------------------------------------------------

def detect_ato_chains(conn=None, *, now: datetime | None = None) -> dict:
    """Run the temporal ATO sequences and collapse all firings per subject
    into ONE ACCOUNT_TAKEOVER_SUSPECTED incident (Stage 20). Completeness is
    honest: chains missing optional steps are recorded PARTIAL."""
    now = now or _utcnow()
    findings = []
    with store.connection(conn) as c:
        firings = sequences.evaluate_all(conn=c, now=now)
        by_subject: dict[str, list] = {}
        for f in firings:
            if f.get("subject_ref"):
                by_subject.setdefault(f["subject_ref"], []).append(f)
        for subject, fs in by_subject.items():
            chains = sorted({f["sequence_id"] for f in fs})
            completeness = sorted({f["completeness"] for f in fs})
            event_ids = tuple(eid for f in fs for eid in f.get("matched_event_ids", ()))
            severity = "high" if any(f["severity"] == "high" for f in fs) else "medium"
            finding = _open(
                c, "ATO_CORRELATED", subject,
                "ACCOUNT_TAKEOVER_SUSPECTED", severity,
                f"Suspected account takeover pattern on {subject}: "
                f"{len(chains)} chain(s) matched ({', '.join(chains)})",
                f"chains={','.join(chains)} completeness={','.join(completeness)}",
                "no ATO-shaped event chain", now,
                owner_action=True, event_ids=event_ids,
                extra_detail={"chains": [
                    {"sequence_id": f["sequence_id"], "title": f["title"],
                     "completeness": f["completeness"],
                     "missing_optional_steps": f.get("missing_optional_steps", []),
                     "matched_event_ids": f.get("matched_event_ids", [])}
                    for f in fs]},
                dedupe_components=("ATO", subject, _day(now)))
            if finding:
                findings.append(finding)
    return {"rule": "ato_chains", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Credential stuffing (Stage 8): fan-out AND fan-in
# ---------------------------------------------------------------------------

def detect_credential_stuffing_fanout(conn=None, *, now: datetime | None = None) -> dict:
    """One network source failing logins across MANY distinct accounts."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT network_ref, COUNT(DISTINCT subject_id), COUNT(*) "
                     "FROM sentinel_events WHERE event_type = 'login_failed' "
                     "AND network_ref IS NOT NULL AND subject_id IS NOT NULL "
                     "AND occurred_at >= ? GROUP BY network_ref "
                     "HAVING COUNT(DISTINCT subject_id) >= ? AND COUNT(*) >= ?",
                     (_cutoff(STUFFING_FANOUT_WINDOW_MINUTES, now),
                      STUFFING_FANOUT_MIN_ACCOUNTS, STUFFING_FANOUT_MIN_FAILURES))
        if rows is None:
            return {"rule": "credential_stuffing_fanout", "skipped": True, "findings": []}
        findings = []
        for net, accounts, failures in rows:
            f = _open(c, "ID1_STUFFING_FANOUT", str(net),
                      "CREDENTIAL_STUFFING", "high",
                      f"{int(failures)} failed logins across {int(accounts)} "
                      f"distinct accounts from one network within "
                      f"{STUFFING_FANOUT_WINDOW_MINUTES}m",
                      f"accounts={int(accounts)} failures={int(failures)}",
                      f"< {STUFFING_FANOUT_MIN_ACCOUNTS} accounts and "
                      f"< {STUFFING_FANOUT_MIN_FAILURES} failures", now,
                      owner_action=True)
            if f:
                findings.append(f)
    return {"rule": "credential_stuffing_fanout", "skipped": False, "findings": findings}


def detect_credential_stuffing_fanin(conn=None, *, now: datetime | None = None) -> dict:
    """One account attacked from MANY distinct networks (distributed guessing)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT subject_id, COUNT(DISTINCT network_ref), COUNT(*) "
                     "FROM sentinel_events WHERE event_type = 'login_failed' "
                     "AND network_ref IS NOT NULL AND subject_id IS NOT NULL "
                     "AND occurred_at >= ? GROUP BY subject_id "
                     "HAVING COUNT(DISTINCT network_ref) >= ?",
                     (_cutoff(STUFFING_FANIN_WINDOW_MINUTES, now),
                      STUFFING_FANIN_MIN_NETWORKS))
        if rows is None:
            return {"rule": "credential_stuffing_fanin", "skipped": True, "findings": []}
        findings = []
        for subject, networks, failures in rows:
            f = _open(c, "ID2_STUFFING_FANIN", f"user:{subject}",
                      "CREDENTIAL_STUFFING", "high",
                      f"Account {subject} received {int(failures)} failed logins "
                      f"from {int(networks)} distinct networks within "
                      f"{STUFFING_FANIN_WINDOW_MINUTES}m",
                      f"networks={int(networks)} failures={int(failures)}",
                      f"< {STUFFING_FANIN_MIN_NETWORKS} distinct networks", now,
                      owner_action=True)
            if f:
                findings.append(f)
    return {"rule": "credential_stuffing_fanin", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Recovery abuse V2 (Stage 9) — enumeration resistance preserved
# ---------------------------------------------------------------------------

def detect_recovery_abuse_v2(conn=None, *, now: datetime | None = None) -> dict:
    """One source requesting recovery for MANY distinct targets — enumeration
    or targeted takeover prep. Counts only; no email or identifier of a
    probed target is echoed into the incident (enumeration resistance)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT network_ref, COUNT(DISTINCT subject_id), COUNT(*) "
                     "FROM sentinel_events WHERE event_type IN "
                     "('password_reset_requested','password_reset_invalid_email',"
                     "'password_reset_no_match') "
                     "AND network_ref IS NOT NULL AND occurred_at >= ? "
                     "GROUP BY network_ref HAVING COUNT(DISTINCT subject_id) >= ?",
                     (_cutoff(RECOVERY_V2_WINDOW_MINUTES, now),
                      RECOVERY_V2_MIN_TARGETS))
        if rows is None:
            return {"rule": "recovery_abuse_v2", "skipped": True, "findings": []}
        findings = []
        for net, targets, attempts in rows:
            f = _open(c, "ID3_RECOVERY_ABUSE_V2", str(net),
                      "RECOVERY_ABUSE", "medium",
                      f"One network issued recovery requests against "
                      f"{int(targets)} distinct targets within "
                      f"{RECOVERY_V2_WINDOW_MINUTES}m "
                      f"(target identifiers withheld — enumeration-safe)",
                      f"distinct_targets={int(targets)} attempts={int(attempts)}",
                      f"< {RECOVERY_V2_MIN_TARGETS} distinct targets", now)
            if f:
                findings.append(f)
    return {"rule": "recovery_abuse_v2", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Session anomalies (Stage 10) — conservative by design
# ---------------------------------------------------------------------------

def detect_session_compromise_indicators(conn=None, *, now: datetime | None = None) -> dict:
    """Platform-written compromise indicators: refresh-token reuse and device
    mismatch are DETERMINISTIC detections by bot.py's own revocation code —
    the strongest identity signal we have (AUTHORITATIVE source)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT subject_id, event_type, COUNT(*), MAX(event_id) "
                     "FROM sentinel_events WHERE event_type IN "
                     "('refresh_token_reuse','refresh_device_mismatch') "
                     "AND subject_id IS NOT NULL AND occurred_at >= ? "
                     "GROUP BY subject_id, event_type",
                     (_cutoff(24 * 60, now),))
        if rows is None:
            return {"rule": "session_compromise", "skipped": True, "findings": []}
        findings = []
        for subject, etype, count, last_event in rows:
            f = _open(c, "ID4_SESSION_COMPROMISE", f"user:{subject}",
                      "SESSION_ANOMALY", "high",
                      f"Platform detected {etype} on account {subject} "
                      f"({int(count)}x in 24h) — deterministic compromise indicator",
                      f"{etype}={int(count)}", "0 occurrences", now,
                      owner_action=True, event_ids=(str(last_event),))
            if f:
                findings.append(f)
    return {"rule": "session_compromise", "skipped": False, "findings": findings}


def detect_session_burst(conn=None, *, now: datetime | None = None) -> dict:
    """Unusually many successful logins for one account in a short window."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT subject_id, COUNT(*) FROM sentinel_events "
                     "WHERE event_type = 'login_succeeded' AND subject_id IS NOT NULL "
                     "AND occurred_at >= ? GROUP BY subject_id HAVING COUNT(*) >= ?",
                     (_cutoff(SESSION_BURST_WINDOW_MINUTES, now),
                      SESSION_BURST_THRESHOLD))
        if rows is None:
            return {"rule": "session_burst", "skipped": True, "findings": []}
        findings = []
        for subject, count in rows:
            f = _open(c, "ID5_SESSION_BURST", f"user:{subject}",
                      "SESSION_ANOMALY", "medium",
                      f"{int(count)} successful logins for account {subject} "
                      f"within {SESSION_BURST_WINDOW_MINUTES}m",
                      f"logins={int(count)}",
                      f"< {SESSION_BURST_THRESHOLD} per "
                      f"{SESSION_BURST_WINDOW_MINUTES}m", now)
            if f:
                findings.append(f)
    return {"rule": "session_burst", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Device relationships (Stage 11) — shared ≠ malicious
# ---------------------------------------------------------------------------

def detect_shared_device_cluster(conn=None, *, now: datetime | None = None) -> dict:
    """Many distinct accounts on one device hash. Threshold is conservative
    (>= SHARED_DEVICE_MIN_USERS) because 2–4 users per device is ordinary
    life; the incident text says so explicitly."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT device_hash, COUNT(DISTINCT user_id) "
                     "FROM mobile_security_sessions WHERE device_hash IS NOT NULL "
                     "AND device_hash != '' AND last_seen_at >= ? "
                     "GROUP BY device_hash HAVING COUNT(DISTINCT user_id) >= ?",
                     (_fmt(now - timedelta(days=7)), SHARED_DEVICE_MIN_USERS))
        if rows is None:
            return {"rule": "shared_device_cluster", "skipped": True, "findings": []}
        findings = []
        for device_hash, users in rows:
            subject = f"device:{str(device_hash)[:32]}"
            f = _open(c, "ID6_SHARED_DEVICE", subject,
                      "DEVICE_ANOMALY", "medium",
                      f"{int(users)} distinct accounts active on one device in 7d. "
                      "NOTE: shared devices are frequently legitimate "
                      "(family/business/support) — review context before acting. "
                      "Signal quality: CLIENT_REPORTED (forgeable).",
                      f"distinct_users={int(users)}",
                      f"< {SHARED_DEVICE_MIN_USERS} users per device", now)
            if f:
                findings.append(f)
    return {"rule": "shared_device_cluster", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Network observations (Stage 5) — internal only, no external reputation
# ---------------------------------------------------------------------------

def detect_network_many_accounts(conn=None, *, now: datetime | None = None) -> dict:
    """Many distinct accounts logging in successfully from one network ref.
    Internal observation only — no third-party IP reputation participates
    (Mission 4 territory, Stage 34)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT network_ref, COUNT(DISTINCT subject_id) "
                     "FROM sentinel_events WHERE event_type = 'login_succeeded' "
                     "AND network_ref IS NOT NULL AND subject_id IS NOT NULL "
                     "AND occurred_at >= ? GROUP BY network_ref "
                     "HAVING COUNT(DISTINCT subject_id) >= ?",
                     (_cutoff(NETWORK_MANY_ACCOUNTS_WINDOW_MINUTES, now),
                      NETWORK_MANY_ACCOUNTS_THRESHOLD))
        if rows is None:
            return {"rule": "network_many_accounts", "skipped": True, "findings": []}
        findings = []
        for net, accounts in rows:
            f = _open(c, "ID7_NETWORK_MANY_ACCOUNTS", str(net),
                      "COORDINATED_IDENTITY_ABUSE", "medium",
                      f"{int(accounts)} distinct accounts active from one network "
                      f"within {NETWORK_MANY_ACCOUNTS_WINDOW_MINUTES}m. "
                      "NOTE: shared networks (campus/office/CGNAT) are common — "
                      "internal observation only, no external reputation used.",
                      f"distinct_accounts={int(accounts)}",
                      f"< {NETWORK_MANY_ACCOUNTS_THRESHOLD} accounts", now)
            if f:
                findings.append(f)
    return {"rule": "network_many_accounts", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Admin identity risk (Stage 12) — no auto-lockout, ever
# ---------------------------------------------------------------------------

def detect_admin_unseen_network(conn=None, *, now: datetime | None = None) -> dict:
    """Admin activity from a network hash never seen for that admin in the
    trailing history window. Opens an incident for HUMAN review; a false
    positive here must never lock out a real admin (Stage 12)."""
    now = now or _utcnow()
    history_cutoff = _fmt(now - timedelta(days=ADMIN_UNSEEN_NETWORK_HISTORY_DAYS))
    recent_cutoff = _cutoff(24 * 60, now)
    with store.connection(conn) as c:
        cur = c.cursor()
        recent = _rows(cur,
                       "SELECT DISTINCT admin_id, ip_hash FROM admin_session_logs "
                       "WHERE created_at >= ? AND ip_hash IS NOT NULL AND ip_hash != ''",
                       (recent_cutoff,))
        if recent is None:
            return {"rule": "admin_unseen_network", "skipped": True, "findings": []}
        findings = []
        for admin_id, ip_hash in recent:
            prior = _rows(cur,
                          "SELECT 1 FROM admin_session_logs WHERE admin_id = ? "
                          "AND ip_hash = ? AND created_at >= ? AND created_at < ? LIMIT 1",
                          (admin_id, ip_hash, history_cutoff, recent_cutoff))
            if prior:   # seen before → nothing to report
                continue
            any_history = _rows(cur,
                                "SELECT 1 FROM admin_session_logs WHERE admin_id = ? "
                                "AND created_at < ? LIMIT 1", (admin_id, recent_cutoff))
            if not any_history:   # brand-new admin: no baseline → no accusation
                continue
            f = _open(c, "ID8_ADMIN_UNSEEN_NETWORK", f"admin:{admin_id}",
                      "ADMIN_IDENTITY_ANOMALY", "medium",
                      f"Admin {admin_id} active from a network hash not seen in "
                      f"{ADMIN_UNSEEN_NETWORK_HISTORY_DAYS}d of history. Human "
                      "review only — never auto-lockout.",
                      f"new_ip_hash={str(ip_hash)[:16]}…",
                      f"ip_hash seen within {ADMIN_UNSEEN_NETWORK_HISTORY_DAYS}d",
                      now, owner_action=True)
            if f:
                findings.append(f)
    return {"rule": "admin_unseen_network", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Explainable baselines (Stages 13–14) — median arithmetic, no ML
# ---------------------------------------------------------------------------

def _daily_counts(c, sql: str, params, days: int, now: datetime) -> list[int]:
    rows = _rows(c.cursor(), sql, params)
    if rows is None:
        return []
    by_day = {str(r[0]): int(r[1]) for r in rows}
    return [by_day.get(_day(now - timedelta(days=i)), 0) for i in range(1, days + 1)]


def admin_baseline(admin_id: str, conn=None, *, now: datetime | None = None) -> dict:
    """Rolling-median baseline of audited admin actions. Fully explainable:
    method, window, and every input are in the output. Label: DERIVED —
    a computed judgment, not a measurement (Stage 13)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        history = _daily_counts(
            c, "SELECT substr(occurred_at, 1, 10), COUNT(*) FROM sentinel_events "
               "WHERE category = 'ADMIN' AND actor_id = ? AND occurred_at >= ? "
               "GROUP BY substr(occurred_at, 1, 10)",
            (f"admin:{admin_id}", _fmt(now - timedelta(days=BASELINE_WINDOW_DAYS + 1))),
            BASELINE_WINDOW_DAYS, now)
        today_rows = _rows(c.cursor(),
                           "SELECT COUNT(*) FROM sentinel_events WHERE category = 'ADMIN' "
                           "AND actor_id = ? AND substr(occurred_at, 1, 10) = ?",
                           (f"admin:{admin_id}", _day(now)))
        today = int(today_rows[0][0]) if today_rows else 0
    active_days = sum(1 for v in history if v > 0)
    if active_days < BASELINE_MIN_HISTORY_DAYS:
        return {"subject": f"admin:{admin_id}", "baseline_available": False,
                "reason": f"only {active_days} active days of history "
                          f"(need {BASELINE_MIN_HISTORY_DAYS}) — no baseline, "
                          "no accusation", "source_trust": "DERIVED"}
    median = statistics.median([v for v in history if v > 0])
    threshold = max(median * BASELINE_DEVIATION_FACTOR, 10.0)
    return {"subject": f"admin:{admin_id}", "baseline_available": True,
            "method": f"median of active days over rolling {BASELINE_WINDOW_DAYS}d, "
                      f"deviation = today >= {BASELINE_DEVIATION_FACTOR} x median "
                      "(floor 10)",
            "median_daily_actions": float(median), "today_actions": today,
            "deviation_threshold": float(threshold),
            "deviates": today >= threshold,
            "history_daily_counts": history, "source_trust": "DERIVED"}


def user_login_baseline(user_id: str, conn=None, *, now: datetime | None = None) -> dict:
    """Security-relevant user baseline ONLY: login cadence. No messages, no
    interests, no ad data, no social graph — ever (Stage 14)."""
    now = now or _utcnow()
    with store.connection(conn) as c:
        history = _daily_counts(
            c, "SELECT substr(occurred_at, 1, 10), COUNT(*) FROM sentinel_events "
               "WHERE event_type = 'login_succeeded' AND subject_id = ? "
               "AND occurred_at >= ? GROUP BY substr(occurred_at, 1, 10)",
            (str(user_id), _fmt(now - timedelta(days=BASELINE_WINDOW_DAYS + 1))),
            BASELINE_WINDOW_DAYS, now)
        today_rows = _rows(c.cursor(),
                           "SELECT COUNT(*) FROM sentinel_events "
                           "WHERE event_type = 'login_succeeded' AND subject_id = ? "
                           "AND substr(occurred_at, 1, 10) = ?",
                           (str(user_id), _day(now)))
        today = int(today_rows[0][0]) if today_rows else 0
    active_days = sum(1 for v in history if v > 0)
    if active_days < BASELINE_MIN_HISTORY_DAYS:
        return {"subject": f"user:{user_id}", "baseline_available": False,
                "reason": f"only {active_days} active days of history "
                          f"(need {BASELINE_MIN_HISTORY_DAYS})",
                "scope": "security-relevant signals only",
                "source_trust": "DERIVED"}
    median = statistics.median([v for v in history if v > 0])
    threshold = max(median * BASELINE_DEVIATION_FACTOR, 6.0)
    return {"subject": f"user:{user_id}", "baseline_available": True,
            "method": f"median logins/day over rolling {BASELINE_WINDOW_DAYS}d, "
                      f"deviation = today >= {BASELINE_DEVIATION_FACTOR} x median "
                      "(floor 6)",
            "median_daily_logins": float(median), "today_logins": today,
            "deviation_threshold": float(threshold),
            "deviates": today >= threshold,
            "scope": "security-relevant signals only",
            "source_trust": "DERIVED"}


def detect_admin_baseline_deviation(conn=None, *, now: datetime | None = None) -> dict:
    """Admins whose audited-action volume today deviates from their own
    explainable baseline. Anomalous ≠ guilty."""
    now = now or _utcnow()
    findings = []
    with store.connection(conn) as c:
        rows = _rows(c.cursor(),
                     "SELECT DISTINCT actor_id FROM sentinel_events "
                     "WHERE category = 'ADMIN' AND substr(occurred_at, 1, 10) = ? "
                     "LIMIT 100", (_day(now),))
        if rows is None:
            return {"rule": "admin_baseline_deviation", "skipped": True, "findings": []}
        for (actor_id,) in rows:
            admin_id = str(actor_id).partition(":")[2] or str(actor_id)
            baseline = admin_baseline(admin_id, conn=c, now=now)
            if not baseline.get("baseline_available") or not baseline.get("deviates"):
                continue
            f = _open(c, "ID9_ADMIN_BASELINE_DEVIATION", f"admin:{admin_id}",
                      "ADMIN_IDENTITY_ANOMALY", "medium",
                      f"Admin {admin_id}: {baseline['today_actions']} actions today "
                      f"vs median {baseline['median_daily_actions']:.1f}/day "
                      f"({baseline['method']})",
                      f"today={baseline['today_actions']} "
                      f"median={baseline['median_daily_actions']:.1f}",
                      f"today < {baseline['deviation_threshold']:.1f}", now,
                      owner_action=True, extra_detail={"baseline": baseline})
            if f:
                findings.append(f)
    return {"rule": "admin_baseline_deviation", "skipped": False, "findings": findings}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

ALL_IDENTITY_RULES = (
    detect_ato_chains,
    detect_credential_stuffing_fanout,
    detect_credential_stuffing_fanin,
    detect_recovery_abuse_v2,
    detect_session_compromise_indicators,
    detect_session_burst,
    detect_shared_device_cluster,
    detect_network_many_accounts,
    detect_admin_unseen_network,
    detect_admin_baseline_deviation,
)


def run_identity_detections(conn=None, *, now: datetime | None = None) -> list[dict]:
    """Run every identity rule; one rule's failure never blocks the others."""
    results = []
    for rule in ALL_IDENTITY_RULES:
        try:
            results.append(rule(conn=conn, now=now))
        except Exception as exc:  # noqa: BLE001 — containment by design
            results.append({"rule": rule.__name__, "skipped": True,
                            "error": str(exc)[:200], "findings": []})
    return results
