"""Presence service: expiry, multi-device, activities, privacy, formatting.

These are the tests for the property the whole system rests on -- that presence
is *derived* at read time rather than stored as a flag. Almost every case below
runs with no background worker of any kind, because that is the point: if any
assertion here needs a reaper to have run, the design has failed.
"""

from datetime import datetime, timedelta, timezone

import harness
from harness import check, check_eq, cursor, conn, section, summary
from services import presence_service as ps

cur = cursor()
harness.bootstrap_users(cur, 12)
ps.reset_schema_cache()
ps.ensure_schema(cur, conn())

VIEWER = 99

# ---------------------------------------------------------------------------
section("1. Liveness is derived, not stored (no reaper running)")

ps.connect(cur, 1, device_id="laptop", device_label="Desktop", platform="web")
p = ps.presence_of(cur, VIEWER, 1)
check_eq("connect makes the user online", p["status"], "online")
check_eq("one device counted", p["devices"], 1)

# Expire the heartbeat. Nothing is told about it -- no sweep, no worker, no
# cleanup call. The very next read must report offline anyway.
harness.age_session(cur, 1, 1)
p = ps.presence_of(cur, VIEWER, 1)
check_eq("expired session reads offline with NO reaper", p["status"], "offline")
check_eq("device count drops to zero", p["devices"], 0)
check("last seen is populated on expiry", p["last_seen_text"].startswith("Last seen"), p["last_seen_text"])

# This is the original production defect, reproduced as a regression guard: a
# user who loaded a page once stayed 'online' forever because nothing ever
# wrote them back to offline.
check("REGRESSION: page-load-then-leave no longer sticks online", not p["online"])

# ---------------------------------------------------------------------------
section("2. Multi-device")

a = ps.connect(cur, 2, device_id="iphone", device_label="iPhone", platform="iphone")
b = ps.connect(cur, 2, device_id="ipad", device_label="iPad", platform="ipad")
c = ps.connect(cur, 2, device_id="web", device_label="Chrome", platform="web")
check_eq("three devices are three sessions", ps.presence_of(cur, VIEWER, 2)["devices"], 3)

ps.disconnect(cur, 2, a["session_id"])
p = ps.presence_of(cur, VIEWER, 2)
check("closing one device does NOT go offline", p["online"], f"status={p['status']} devices={p['devices']}")
check_eq("device count decrements", p["devices"], 2)

ps.disconnect(cur, 2, b["session_id"])
ps.disconnect(cur, 2, c["session_id"])
p = ps.presence_of(cur, VIEWER, 2)
check_eq("offline only after the last device leaves", p["status"], "offline")

# Reconnecting the same device id must replace, not accumulate. Mobile gets a
# fresh session on every foreground; without replacement a user who opened the
# app ten times would look like ten devices and stay online long after leaving.
for _ in range(5):
    ps.connect(cur, 3, device_id="same-phone", device_label="iPhone", platform="iphone")
check_eq("5 reconnects of one device = 1 session", ps.presence_of(cur, VIEWER, 3)["devices"], 1)

# A second device of a *different* id must still be additive.
ps.connect(cur, 3, device_id="other-phone", device_label="Android", platform="android")
check_eq("distinct device ids are additive", ps.presence_of(cur, VIEWER, 3)["devices"], 2)

# ---------------------------------------------------------------------------
section("3. Activities cannot get stuck")

s = ps.connect(cur, 4, device_id="d1", device_label="iPhone")
ps.set_activity(cur, 4, s["session_id"], "typing", "conv-7")
check_eq("typing is reported", ps.presence_of(cur, VIEWER, 4)["activity"], "typing")

# Simulate the app dying mid-keystroke: the "stop typing" message never
# arrives. The transient TTL must defuse it on its own.
cur.execute(
    "UPDATE presence_sessions SET activity_expires_at=? WHERE user_id=4",
    (ps.iso(ps.utc_now() - timedelta(seconds=1)),),
)
conn().commit()
check_eq("STUCK TYPING: expires on its own TTL", ps.presence_of(cur, VIEWER, 4)["activity"], "idle")

# Session-bound activities (calls, Live) must NOT expire on the short transient
# TTL -- a quiet ten-minute call is still a call.
s5 = ps.connect(cur, 5, device_id="d1", device_label="iPhone")
ps.set_activity(cur, 5, s5["session_id"], "in_video_call", "room-1")
harness.age_last_beat(cur, 5, 8)
check_eq("call activity survives the transient TTL", ps.presence_of(cur, VIEWER, 5)["activity"], "in_video_call")

# But it must die with the session -- a crashed call cannot strand someone as
# permanently "in a call".
harness.age_session(cur, 5, 1)
p = ps.presence_of(cur, VIEWER, 5)
check_eq("crashed call clears with the session", p["activity"], "idle")
check_eq("...and the user is offline", p["status"], "offline")

# Priority across devices: phone in a call, laptop typing -> the call wins.
s6a = ps.connect(cur, 6, device_id="phone", device_label="iPhone")
s6b = ps.connect(cur, 6, device_id="laptop", device_label="Desktop")
ps.set_activity(cur, 6, s6b["session_id"], "typing", "conv-1")
ps.set_activity(cur, 6, s6a["session_id"], "in_audio_call", "room-9")
check_eq("call outranks typing across devices", ps.presence_of(cur, VIEWER, 6)["activity"], "in_audio_call")

# ---------------------------------------------------------------------------
section("4. Away")

ps.connect(cur, 7, device_id="d1", device_label="Desktop")
check_eq("fresh heartbeat is online", ps.presence_of(cur, VIEWER, 7)["status"], "online")
harness.age_last_beat(cur, 7, ps.AWAY_AFTER_SECONDS + 30)
cur.execute(
    "UPDATE presence_sessions SET expires_at=? WHERE user_id=7",
    (ps.iso(ps.utc_now() + timedelta(seconds=600)),),
)
conn().commit()
p = ps.presence_of(cur, VIEWER, 7)
check_eq("idle past the away threshold reads away", p["status"], "away")
check("away is still reachable (not offline)", p["online"])

# ---------------------------------------------------------------------------
section("5. Privacy is indistinguishable from offline")

# The security property: a hidden user's payload must be byte-identical to a
# genuinely offline user's. If it differs by even one key, a client can diff
# the two and learn it has been blocked, or that someone is invisible -- which
# defeats the entire purpose of both features.
ps.connect(cur, 8, device_id="d", device_label="iPhone")   # blocked
ps.connect(cur, 9, device_id="d", device_label="iPhone")   # invisible
ps.connect(cur, 10, device_id="d", device_label="iPhone")  # hide last seen

# Column names mirror the production schema exactly (verified against
# coinpilotx.db). Using different names here would make the test pass for the
# wrong reason: the lookup tolerates a missing block store, so a mistyped
# fixture reads as "no blocks" rather than as a failure.
cur.execute(
    "CREATE TABLE IF NOT EXISTS blocked_users (id INTEGER PRIMARY KEY AUTOINCREMENT, blocker_user_id INTEGER, blocked_user_id INTEGER, reason TEXT, created_at TEXT)"
)
# Block recorded in the *target's* direction: user 8 blocked the viewer. The
# viewer must not be able to see them, and equally must not be able to tell
# the difference between that and user 8 simply being offline.
cur.execute(
    "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, created_at) VALUES (?,?,?)",
    (8, VIEWER, ps.iso_now()),
)
ps.set_privacy(cur, 9, invisible_mode=True)
ps.set_privacy(cur, 10, hide_last_seen=True)
conn().commit()

# The control: a user who has never connected at all.
control = ps.presence_of(cur, VIEWER, 11)
check_eq("control user is offline", control["status"], "offline")

for uid, label in ((8, "blocked viewer"), (9, "invisible mode")):
    payload = ps.presence_of(cur, VIEWER, uid)
    same_keys = set(payload) == set(control)
    same_values = all(payload[k] == control[k] for k in control if k != "user_id")
    check(
        f"{label}: payload indistinguishable from offline",
        same_keys and same_values,
        "" if (same_keys and same_values) else f"{payload} != {control}",
    )
    # And the crucial half: the server still knows the truth internally.
    check(f"{label}: server still knows they are online", ps.is_online(cur, uid))

# Hide-last-seen is a narrower control: still shows online/offline, just no
# timestamp. Verify it suppresses the text without suppressing liveness.
harness.age_session(cur, 10, 1)
p10 = ps.presence_of(cur, VIEWER, 10)
check_eq("hide last seen: no timestamp leaked", p10["last_seen_text"], "")
check_eq("hide last seen: no raw timestamp leaked", p10["last_seen_at"], "")

# Blocks must apply in BOTH directions. Above, the target blocked the viewer;
# here the viewer blocks the target. Enforcing only one direction would let a
# user keep watching someone they had themselves blocked.
ps.connect(cur, 12, device_id="d", device_label="iPhone")
cur.execute(
    "INSERT INTO blocked_users (blocker_user_id, blocked_user_id, created_at) VALUES (?,?,?)",
    (VIEWER, 12, ps.iso_now()),
)
conn().commit()
check_eq("block applies viewer->target too", ps.presence_of(cur, VIEWER, 12)["status"], "offline")
check("...while the target is genuinely online", ps.is_online(cur, 12))
# A third party with no block relationship must be unaffected -- blocking is
# not allowed to leak into anyone else's view.
check_eq("block does not affect an unrelated viewer", ps.presence_of(cur, 2, 12)["status"], "online")

# Self-view must never be censored -- you can always see your own state.
own = ps.presence_of(cur, 9, 9)
check("invisible user still sees their own presence", own.get("self") is True or own["online"], str(own))

# ---------------------------------------------------------------------------
section("6. Fail-closed: unknown input never becomes online")

check_eq("unknown user reads offline", ps.presence_of(cur, VIEWER, 4242)["status"], "offline")
check_eq("user id 0 reads offline", ps.presence_of(cur, VIEWER, 0)["status"], "offline")
bogus = ps.heartbeat(cur, 1, "does-not-exist")
check("heartbeat on an unknown session refuses", bogus.get("ok") is False, str(bogus))
check("...and asks the client to reconnect immediately", bool(bogus.get("reconnect")))

# ---------------------------------------------------------------------------
section("7. Last seen formatting")

now = datetime(2026, 7, 25, 20, 42, tzinfo=timezone.utc)
cases = [
    (timedelta(seconds=10), "Last seen just now"),
    (timedelta(minutes=1), "Last seen 1 minute ago"),
    (timedelta(minutes=3), "Last seen 3 minutes ago"),
    (timedelta(hours=1), "Last seen 1 hour ago"),
]
for delta, expected in cases:
    check_eq(f"format {delta}", ps.format_last_seen(ps.iso(now - delta), now=now), expected)

yesterday = ps.format_last_seen(ps.iso(now - timedelta(days=1)), now=now)
check("yesterday uses the word 'yesterday'", yesterday.startswith("Last seen yesterday at "), yesterday)

older = ps.format_last_seen(ps.iso(now - timedelta(days=60)), now=now)
check("older dates use an absolute date", "May 26 at" in older, older)

# Locale must change the clock format, not the shape of the sentence.
de = ps.format_last_seen(ps.iso(now - timedelta(days=1)), now=now, locale="de")
check("locale switches to a 24-hour clock", "20:42" in de, de)
check("en keeps the 12-hour clock", "8:42 PM" in yesterday, yesterday)

check_eq("empty timestamp formats to empty, not to a fake date", ps.format_last_seen(""), "")
check_eq("garbage timestamp formats to empty", ps.format_last_seen("not-a-date"), "")

# ---------------------------------------------------------------------------
section("8. Sweep is housekeeping, never correctness")

before = ps.presence_of(cur, VIEWER, 7)["status"]
ps.sweep(cur, conn())
after = ps.presence_of(cur, VIEWER, 7)["status"]
check_eq("sweep does not change any reported status", after, before)

snapshot = ps.health_snapshot(cur, conn())
check("health snapshot reports live session count", "live_sessions" in snapshot, str(snapshot))

summary("test_presence_core")
