#!/usr/bin/env python3
"""Mutation matrix for the admin Communications surface (§29 of PULSE COMMS OPS).

Each entry removes one control that the Communications page and its snapshot
feed depend on, then checks the suite notices. The controls here are the ones
whose failure is silent in production: an authorisation check that still returns
a page, a privacy boundary that still renders, a failed query that still shows a
number. None of those announce themselves -- the page keeps loading and the
figures keep looking plausible -- so a test is the only thing standing between
them and a quiet regression.

Two of these entries exist because the bug was real. ``active_count_from_len``
reinstates a defect that shipped in the first draft of this module: the count of
calls in progress was ``len()`` of a ``LIMIT``-ed list, so the fiftieth call and
the five-hundredth looked identical. ``failed_section_reads_ready`` reinstates
the shape the mission brief names explicitly -- a query that failed reported as
health -- which is the failure mode that makes an operations page worse than no
page at all, because it answers "is anything wrong?" with a confident no.

    python3 scripts/protection/comms_ops_mutation_matrix.py [--verbose]

Exit 0 only if every mutation was caught.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from mutation_harness import run_matrix  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]

OPS = "services/pulsesoc_comms_ops.py"
SUITE = "tests/comms_ops/test_comms_ops_surface.py"

MUTATIONS = [
    dict(
        name="page_authz",
        control="The Communications page requires the system.view permission, not merely a "
                "signed-in session and not merely a URL under /admin.",
        path="bot.py",
        old='def admin_communications_page():\n    admin, denied = require_admin_page("system.view")',
        new='def admin_communications_page():\n    admin, denied = {"id": 0, "email": "anyone@example.com", '
            '"role": "owner", "admin_role": "owner"}, None',
        suites=[SUITE],
    ),
    dict(
        name="feed_authz",
        control="The snapshot feed is authorised independently of the page. A polling endpoint "
                "that trusts the page's check is reachable directly.",
        path="bot.py",
        old='def admin_communications_snapshot_json():\n    """Polling feed for the Communications page.',
        new='def admin_communications_snapshot_json():\n    globals()["require_admin_api"] = lambda *a, **k: '
            '({"id": 0, "email": "anyone@example.com", "role": "owner"}, None)\n'
            '    """Polling feed for the Communications page.',
        suites=[SUITE],
    ),
    dict(
        name="active_filter",
        control="The active-calls list is filtered to statuses that mean a call is in progress. "
                "Without the filter every call ever made reads as happening now.",
        path=OPS,
        old="        WHERE status IN ({active_marks})\n        ORDER BY id DESC",
        new="        WHERE status IN ({active_marks}) OR 1=1\n        ORDER BY id DESC",
        suites=[SUITE],
    ),
    dict(
        name="ended_is_active",
        control="ACTIVE_CALL_STATUSES holds only statuses the call engine writes while a call is "
                "live. Admitting a terminal status inflates every active figure on the page.",
        path=OPS,
        old='    "created", "ringing", "accepted", "connecting", "connected", "active", "reconnecting",\n)',
        new='    "created", "ringing", "accepted", "connecting", "connected", "active", "reconnecting",\n'
            '    "ended",\n)',
        suites=[SUITE],
    ),
    dict(
        name="active_count_from_len",
        control="active_count is a COUNT over the active statuses, not the length of the capped "
                "list. Derived from len() it saturates at the row cap, so an overloaded system "
                "and a busy one report the same number.",
        path=OPS,
        old='        "active_count": active_count,\n        "live_count": live_count,',
        new='        "active_count": len(active),\n        "live_count": sum(1 for e in active if e["live"]),',
        suites=[SUITE],
    ),
    dict(
        name="message_body",
        control="The chat section selects metadata columns only. A message body in the snapshot "
                "turns an operations page into a surveillance page.",
        path=OPS,
        old='    return {\n        "windows": windows,\n        "by_type": by_type,\n        "active": active,',
        new='    cur.execute("SELECT body FROM comm_v2_messages ORDER BY id DESC LIMIT 1")\n'
            '    _leaked = _one(cur)\n'
            '    return {\n        "windows": windows,\n        "by_type": by_type,\n        "active": active,\n'
            '        "last_message_body": str(_leaked.get("body") or ""),',
        suites=[SUITE],
    ),
    dict(
        name="room_credential",
        control="room_name is withheld from the active-call entry. It is the joinable handle for a "
                "call in progress, which makes it an access credential rather than an identifier.",
        path=OPS,
        old='            "status": str(row.get("status") or "") or "unknown",\n'
            '            "live": str(row.get("status") or "") in LIVE_CALL_STATUSES,',
        new='            "status": str(row.get("status") or "") or "unknown",\n'
            '            "room": str(row.get("room_name") or ""),\n'
            '            "live": str(row.get("status") or "") in LIVE_CALL_STATUSES,',
        suites=[SUITE],
    ),
    dict(
        name="quality_counts_placeholders_as_measurements",
        control="A quality report whose latency, jitter and score are all zero is a row written, "
                "not a network observed. Counted as a measurement, the panel reports a perfect "
                "network at the moment it knows nothing -- and in production 452 of 542 rows are "
                "that shape, so this is the ordinary case and not an edge one.",
        path=OPS,
        old="                        THEN 1 ELSE 0 END) AS measured",
        new="                        THEN 1 ELSE 1 END) AS measured",
        suites=[SUITE],
    ),
    dict(
        name="unscored_call_reads_as_worst_score",
        control="A report that timed the network but did not rate the call contributes no score. "
                "A plain MIN over the measured rows returns nought for a call nobody rated, so an "
                "absent rating renders as the worst possible one. This was really the first draft "
                "of this query, and the test for it was written because of that.",
        path=OPS,
        old="               MIN(CASE WHEN COALESCE(quality_score,0) > 0 THEN quality_score END) AS worst_score,",
        new="               MIN(quality_score) AS worst_score,",
        suites=[SUITE],
    ),
    dict(
        name="quality_leaks_device_fingerprint",
        control="The quality panel reads a table that also stores device_info_json. Reading a table "
                "puts every column in it one SELECT away from the page, and a device fingerprint "
                "beside a call identifies a person rather than describing a network.",
        path=OPS,
        old='    stats = _one(cur)\n    worst_score = stats.get("worst_score")\n'
            '    return {\n        "measurable": True,',
        new='    stats = _one(cur)\n'
            '    cur.execute("SELECT device_info_json AS d FROM communication_call_quality_reports "\n'
            '                "ORDER BY id DESC LIMIT 1")\n'
            '    _device = str(_one(cur).get("d") or "")\n'
            '    worst_score = stats.get("worst_score")\n'
            '    return {\n        "measurable": True,\n        "device_info_json": _device,',
        suites=[SUITE],
    ),
    dict(
        name="failed_section_reads_ready",
        control="A section whose query raised reports state 'error'. Reported 'ready' it carries no "
                "numbers and an absent number renders as nothing happening -- the page would answer "
                "'is anything wrong?' with a confident no precisely when it cannot see.",
        path=OPS,
        old='        return {"state": "error", "error": type(exc).__name__, "query_ms": elapsed}',
        new='        return {"state": "ready", "error": type(exc).__name__, "query_ms": elapsed}',
        suites=[SUITE],
    ),
    dict(
        name="provider_failure_reads_healthy",
        control="A channel whose every real attempt failed is rated FAILED. Rated healthy, the one "
                "panel an operator checks to answer 'is push broken?' says no while push is broken.",
        path=OPS,
        old='            state, detail = "failed", f"All {attempted} real attempts failed in 7d."',
        new='            state, detail = "healthy", f"All {attempted} real attempts failed in 7d."',
        suites=[SUITE],
    ),
    dict(
        name="partial_failure_reads_healthy",
        control="A channel failing some of the time is rated DEGRADED. Collapsed into healthy, a "
                "partial outage is invisible until it becomes a total one.",
        path=OPS,
        old='            state, detail = "degraded", f"{failed} of {attempted} real attempts failed in 7d."',
        new='            state, detail = "healthy", f"{failed} of {attempted} real attempts failed in 7d."',
        suites=[SUITE],
    ),
    dict(
        name="lookup_authz",
        control="The identifier lookup is authorised on system.view, independently of the page that "
                "calls it. This is the brief's eighth mutation: an admin search reachable without "
                "the permission answers questions about specific sessions to anyone with a session.",
        path="bot.py",
        old='    admin, denied = require_admin_api("system.view")\n    if denied:\n        return denied\n\n'
            '    term = (request.args.get("q") or "").strip()[:80]',
        new='    admin, denied = {"id": 0, "email": "anyone@example.com", "role": "owner"}, None\n\n'
            '    term = (request.args.get("q") or "").strip()[:80]',
        suites=[SUITE],
    ),
    dict(
        name="lookup_accepts_row_numbers",
        control="A numeric term is refused. Primary keys run in sequence, so a lookup that accepts "
                "one is a for-loop away from walking the whole identifier space -- the difference "
                "between searching for a session and enumerating all of them.",
        path=OPS,
        old="    if text.isdigit():",
        new="    if text.isdigit() and False:",
        suites=[SUITE],
    ),
    dict(
        name="lookup_accepts_addresses",
        control="An address is refused. Answering found/not-found for an email turns an operations "
                "box into an oracle for whether a person has an account, which is the surveillance "
                "window the brief exists to prevent.",
        path=OPS,
        old='    if "@" in text:',
        new='    if "@" in text and False:',
        suites=[SUITE],
    ),
    dict(
        name="lookup_leaks_room_credential",
        control="A looked-up call withholds room_name for the same reason the active list does. A "
                "lookup is the most tempting place to add it, because the operator has asked about "
                "that specific call -- and it is a joinable handle either way.",
        path=OPS,
        old='                "live": status in LIVE_CALL_STATUSES,\n'
            '                "active": status in ACTIVE_CALL_STATUSES,',
        new='                "live": status in LIVE_CALL_STATUSES,\n'
            '                "active": status in ACTIVE_CALL_STATUSES,\n'
            '                "room": str(row.get("room_name") or ""),',
        suites=[SUITE],
    ),
    dict(
        name="lookup_is_not_audited",
        control="Every lookup writes an audit row naming the admin and the identifier. §23 asks who "
                "did which sensitive operation when, and on this surface the sensitive operation is "
                "asking about one named session.",
        path="bot.py",
        old='    log_admin_audit(\n        admin_id,\n        "comms_ops_lookup",',
        new='    _ = lambda *a, **k: None\n    _(\n        admin_id,\n        "comms_ops_lookup",',
        suites=[SUITE],
    ),
    dict(
        name="failure_reason_is_surfaced_verbatim",
        control="A delivery failure reason is redacted of credential-shaped runs before it "
                "leaves the module. The reason is adapter output, not operator-authored text, "
                "and push providers routinely echo the offending token in it -- so this is the "
                "one field on the surface whose privacy would otherwise depend on what a third "
                "party chose to put in an error string. The comment at the call site asserted "
                "the opposite until a test proved it wrong.",
        path=OPS,
        old='    text = _NAMED_CREDENTIAL.sub("[redacted]", text)\n'
            '    text = _OPAQUE_RUN.sub("[redacted]", text)\n'
            '    return text[:REASON_MAX_CHARS]',
        new="    return text[:REASON_MAX_CHARS]",
        suites=[SUITE],
    ),
    dict(
        name="per_user_panel_names_the_other_participants",
        control="The per-user panel counts the calls a member joined and never names who else "
                "was on them. communication_call_participants holds every other participant and "
                "the join is already written, so 'spoke with' is one column away -- and it would "
                "read as support context while being a record of who knows whom.",
        path=OPS,
        old='            row = _one(cur)\n            return {\n'
            '                "total": _int(row.get("total")),\n'
            '                "7d": _int(row.get("week")),\n'
            '                "last_at": str(row.get("last_at") or ""),\n'
            '            }\n\n        def messages_sent()',
        new='            row = _one(cur)\n'
            '            cur.execute("SELECT DISTINCT p2.user_id AS peer FROM "\n'
            '                        "communication_call_participants p1 JOIN "\n'
            '                        "communication_call_participants p2 ON p2.call_id = p1.call_id "\n'
            '                        "WHERE p1.user_id = ? AND p2.user_id <> ?", (uid, uid))\n'
            '            return {\n'
            '                "total": _int(row.get("total")),\n'
            '                "7d": _int(row.get("week")),\n'
            '                "peers": [_int(r.get("peer")) for r in _rows(cur)],\n'
            '                "last_at": str(row.get("last_at") or ""),\n'
            '            }\n\n        def messages_sent()',
        suites=[SUITE],
    ),
    dict(
        name="per_user_failed_section_reads_as_no_activity",
        control="A per-user section whose query raised reports 'error' and carries no counts. "
                "Zeroed, the panel tells an admin this member has no calls and no notifications "
                "at the moment it cannot see -- which is the answer that sends them to tell a "
                "member their phone is fine.",
        path=OPS,
        old='            errors[name] = type(exc).__name__\n'
            '            # Never zeroes. "No calls" and "we could not look" are different\n'
            '            # answers to a support question, and the wrong one sends an admin\n'
            '            # to tell a member their phone is fine.\n'
            '            sections[name] = {"state": "error", "error": type(exc).__name__}',
        new='            errors[name] = type(exc).__name__\n'
            '            sections[name] = {"state": "ready", "total": 0, "7d": 0, "24h": 0,\n'
            '                              "threads": 0, "channels": [], "failing": [],\n'
            '                              "by_status": [], "reasons": [], "last_at": ""}',
        suites=[SUITE],
    ),
    dict(
        name="lookup_is_not_rate_limited",
        control="Lookups are capped per admin per minute. The grammar bounds which identifier "
                "spaces can be swept; the limit bounds how fast one can be swept within the "
                "grammar. Without it a compromised admin session is a scraper.",
        path="bot.py",
        old='    if security_guard.rate_limited(f"admin_comms_lookup:{admin_id}", limit=30, window_seconds=60):',
        new='    if False:',
        suites=[SUITE],
    ),
]

if __name__ == "__main__":
    sys.exit(run_matrix(ROOT, MUTATIONS))
