"""Messenger consumers of presence: no subsystem-local logic, no disagreement.

The mission's hard rule is that no subsystem may maintain its own presence
logic. Three places in pulse_communications_v2 previously did -- the
conversation list, the control-centre stats block, and conversation_presence()
-- each ageing its own `comm_v2_presence` rows on its own schedule.

These tests do not check that the code was edited. They check the property the
edit was for: that every Messenger surface returns the *same answer* as the
unified service for the same user at the same instant, and that none of them
can be made to disagree.
"""

import harness
from harness import check, check_eq, cursor, conn, section, summary
from services import presence_service as ps
import pulse_communications_v2.service as svc

cur = cursor()
harness.bootstrap_users(cur, 8)
ps.ensure_schema(cur, conn())

ALICE, BOB, CARL = 1, 2, 3


def make_direct(a, b):
    """Create a direct conversation through the real service."""
    result = svc.create_conversation(a, {"conversation_type": "direct", "target_user_id": b})
    assert result.get("ok"), result
    return int(result["conversation"]["id"])


# ---------------------------------------------------------------------------
section("Setup")

CONV = make_direct(ALICE, BOB)
check("direct conversation created", CONV > 0, f"id={CONV}")

# ---------------------------------------------------------------------------
section("1. conversation_presence() agrees with the unified service")

ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone", platform="iphone")
conn().commit()

result = svc.conversation_presence(ALICE, CONV)
check("conversation_presence returns ok", result.get("ok"), str(result)[:200])
by_id = {int(item["user_id"]): item for item in result.get("presence", [])}

truth_bob = ps.presence_of(cur, ALICE, BOB)
check_eq("Messenger status matches the service for an online peer", by_id[BOB]["status"], truth_bob["status"])
check_eq("...and active_now matches", by_id[BOB]["active_now"], truth_bob["online"])

# Now expire Bob with no reaper. The Messenger surface must flip on the very
# next read, because it no longer keeps its own copy of liveness.
harness.age_session(cur, BOB, 1)
result = svc.conversation_presence(ALICE, CONV)
by_id = {int(item["user_id"]): item for item in result.get("presence", [])}
truth_bob = ps.presence_of(cur, ALICE, BOB)
check_eq("expired peer flips to offline in Messenger", by_id[BOB]["status"], "offline")
check_eq("Messenger still agrees with the service", by_id[BOB]["status"], truth_bob["status"])
check("last seen text is carried through, not invented", by_id[BOB]["last_seen_text"] == truth_bob["last_seen_text"],
      f"messenger={by_id[BOB]['last_seen_text']!r} service={truth_bob['last_seen_text']!r}")

# ---------------------------------------------------------------------------
section("2. conversation_presence() no longer leaks a 'hidden' status")

# This is the block-detection leak: the old implementation emitted
# status="hidden" for privacy-restricted users, a value that appears nowhere
# else. A client could read that single field and learn it had been blocked.
ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone", platform="iphone")
ps.set_privacy(cur, BOB, invisible_mode=True)
conn().commit()

result = svc.conversation_presence(ALICE, CONV)
by_id = {int(item["user_id"]): item for item in result.get("presence", [])}
statuses = {item["status"] for item in result.get("presence", [])}

check("no 'hidden' status anywhere in the payload", "hidden" not in statuses, str(statuses))
check_eq("invisible peer reads as plain offline", by_id[BOB]["status"], "offline")
check_eq("...with active_now false", by_id[BOB]["active_now"], False)
check_eq("...and no activity leaked", by_id[BOB]["activity"], "idle")
check_eq("...and no last-seen timestamp leaked", by_id[BOB]["last_seen_text"], "")
check("server still knows the invisible user is online", ps.is_online(cur, BOB))

# The decisive test, stated precisely.
#
# The achievable property is that a hidden user is byte-identical to an offline
# user *whose last-seen is not available* -- the state shared by anyone using
# Hide Last Seen and by anyone who has never connected. That is a real and
# populated crowd to hide in, and no field in the payload distinguishes them.
#
# The property that is NOT achievable, deliberately: a hidden user is not
# identical to an offline user with a *visible* last-seen timestamp, because
# the hidden payload carries no timestamp. Closing that last gap would mean
# inventing a plausible-looking timestamp for someone who did not generate one,
# which is exactly the fabricated presence data this mission exists to remove.
# We take the honest gap over the dishonest fix.
CARL_HIDDEN = 4
ps.connect(cur, CARL_HIDDEN, device_id="d", device_label="iPhone")
ps.set_privacy(cur, CARL_HIDDEN, hide_last_seen=True)
ps.disconnect_all(cur, CARL_HIDDEN)
conn().commit()
control_offline = ps.presence_of(cur, ALICE, CARL_HIDDEN)          # offline, no timestamp
control_never = ps.presence_of(cur, ALICE, 7)                       # never connected
hidden = ps.presence_of(cur, ALICE, BOB)                            # online but invisible

for label, control in (("offline w/ hidden last seen", control_offline), ("never connected", control_never)):
    differing = {k for k in control if k != "user_id" and control.get(k) != hidden.get(k)}
    check(f"invisible is byte-identical to '{label}'", not differing, f"fields that differ: {sorted(differing)}")

check("the crowd to hide in is real: control is genuinely offline", not control_offline["online"])
check("while the hidden user is genuinely online", ps.is_online(cur, BOB))

ps.set_privacy(cur, BOB, invisible_mode=False)
conn().commit()

# ---------------------------------------------------------------------------
section("3. Control-centre stats agree with the same service")

# The control centre used to count comm_v2_presence rows directly -- a second
# store with its own ageing -- so it could report "Online" for someone the
# thread header showed as offline.
ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone", platform="iphone")
conn().commit()

detail = svc.conversation_control_center(ALICE, CONV)
stats = None
if detail and detail.get("ok"):
    stats = detail.get("stats") or {}
    check_eq("control centre reports Online for a live peer", stats.get("activity_status"), "Online")

    harness.age_session(cur, BOB, 1)
    detail = svc.conversation_control_center(ALICE, CONV)
    stats = detail.get("stats") or {}
    truth = ps.presence_of(cur, ALICE, BOB)
    check("control centre shows the real last-seen sentence, not 'Recently active'",
          stats.get("activity_status") == truth["last_seen_text"] or stats.get("activity_status") == "Offline",
          f"activity_status={stats.get('activity_status')!r} last_seen={truth['last_seen_text']!r}")
    check("control centre never says Online for an expired peer",
          stats.get("activity_status") != "Online", str(stats.get("activity_status")))
    check_eq("online peer count is zero once the peer expires", int(stats.get("online_count") or 0), 0)
else:
    check("conversation_control_center reachable", False, f"detail={str(detail)[:200]}")

# ---------------------------------------------------------------------------
section("4. The three surfaces cannot disagree")

# Drive one user through a full lifecycle and assert all three Messenger
# surfaces move together at every step. Any surface still holding its own
# presence copy would lag here.
def surfaces():
    presence_call = svc.conversation_presence(ALICE, CONV)
    from_presence = {int(i["user_id"]): i for i in presence_call.get("presence", [])}.get(BOB, {})
    # The conversation list carries the peer's presence on a `presence` key
    # (verified against the live payload, not assumed) -- this is the third
    # surface, and the one users see most often.
    listing = svc.list_conversations(ALICE, {})
    from_list = {}
    for row in listing.get("conversations", []) if listing.get("ok") else []:
        if int(row.get("id") or 0) == CONV:
            from_list = row.get("presence") or {}
    truth = ps.presence_of(cur, ALICE, BOB)
    return from_presence, from_list, truth


for label, action in (
    ("after connect", lambda: ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone")),
    ("after second device", lambda: ps.connect(cur, BOB, device_id="bob-ipad", device_label="iPad")),
    ("after expiry", lambda: harness.age_session(cur, BOB, 1)),
):
    action()
    conn().commit()
    from_presence, from_list, truth = surfaces()
    check(f"{label}: conversation_presence matches service",
          from_presence.get("status") == truth["status"],
          f"{from_presence.get('status')!r} vs {truth['status']!r}")
    # Assert unconditionally. An `if from_list:` guard here would let this whole
    # check evaporate the day the payload shape changes, which is precisely when
    # it would be most needed.
    check(f"{label}: conversation list carries presence", bool(from_list), str(from_list)[:120])
    check(f"{label}: conversation list matches service", from_list.get("status") == truth["status"],
          f"{from_list.get('status')!r} vs {truth['status']!r}")
    check(f"{label}: list active_now matches service", bool(from_list.get("active_now")) == truth["online"],
          f"{from_list.get('active_now')!r} vs {truth['online']!r}")

# ---------------------------------------------------------------------------
section("5. Multi-device is visible through Messenger, not just the service")

ps.disconnect_all(cur, BOB)
s_phone = ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone")
s_web = ps.connect(cur, BOB, device_id="bob-web", device_label="Chrome")
conn().commit()
result = svc.conversation_presence(ALICE, CONV)
by_id = {int(i["user_id"]): i for i in result["presence"]}
check_eq("two devices: Messenger shows online", by_id[BOB]["status"], "online")

ps.disconnect(cur, BOB, s_phone["session_id"])
conn().commit()
result = svc.conversation_presence(ALICE, CONV)
by_id = {int(i["user_id"]): i for i in result["presence"]}
check_eq("closing one device: Messenger keeps them online", by_id[BOB]["status"], "online")

ps.disconnect(cur, BOB, s_web["session_id"])
conn().commit()
result = svc.conversation_presence(ALICE, CONV)
by_id = {int(i["user_id"]): i for i in result["presence"]}
check_eq("closing the last device: Messenger shows offline", by_id[BOB]["status"], "offline")

# ---------------------------------------------------------------------------
section("6. Activity propagates to Messenger")

s = ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone")
ps.set_activity(cur, BOB, s["session_id"], "in_video_call", "room-42")
conn().commit()
result = svc.conversation_presence(ALICE, CONV)
by_id = {int(i["user_id"]): i for i in result["presence"]}
check_eq("in_video_call reaches the Messenger payload", by_id[BOB]["activity"], "in_video_call")

ps.set_activity(cur, BOB, s["session_id"], "recording_voice", str(CONV))
conn().commit()
result = svc.conversation_presence(ALICE, CONV)
by_id = {int(i["user_id"]): i for i in result["presence"]}
check_eq("recording_voice reaches the Messenger payload", by_id[BOB]["activity"], "recording_voice")

# ---------------------------------------------------------------------------
section("7. UNDX reports the assistant marker, not human presence")

# pulse_ai_service cannot be imported here -- it transitively pulls in the Flask
# request stack. Parse the literal out of the AST instead. This is stronger than
# a substring grep: it reads the actual value assigned to the "presence" key,
# so a match cannot come from a comment or an unrelated line.
import ast

tree = ast.parse(open(harness.REPO + "/services/pulse_ai_service.py", encoding="utf-8").read())
presence_literals = []
for node in ast.walk(tree):
    if isinstance(node, ast.Dict):
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and key.value == "presence" and isinstance(value, ast.Dict):
                try:
                    presence_literals.append(ast.literal_eval(value))
                except Exception:
                    pass

check("assistant presence literal found", bool(presence_literals), str(presence_literals))
for literal in presence_literals:
    check_eq("UNDX reports the assistant marker, not 'online'", literal.get("status"), "assistant")
    check_eq("UNDX never claims active_now", literal.get("active_now"), False)
    check("assistant is flagged as such", literal.get("assistant") is True, str(literal))
    check("'assistant' is outside the human vocabulary",
          literal.get("status") not in {"online", "away", "offline"})

summary("test_messenger_integration")
