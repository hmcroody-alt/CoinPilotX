"""Platform surfaces outside Messenger: Rooms, Live metrics, Arena, Feed.

These surfaces each derived their own "who is online" answer. The rule they
violated is the mission's central one -- no subsystem may maintain its own
presence logic -- and the way each of them broke was the same: they asked a
question that *correlates* with presence (did you do something recently? are
you a member? did a page view touch your row?) and reported the answer as if it
were presence.

`bot.py` cannot be imported: it is a single ~100k-line Flask application whose
import has side effects. Rather than test a copy, this suite lifts the exact
function definitions out of bot.py's AST, compiles them, and executes them
against the harness database. The code under test is therefore the shipping
source text, byte for byte -- only its module globals are supplied by us, and
each function's free names are enumerated below so that surface is explicit.

Where a function's dependency graph is too wide to execute (Rooms and the Arena
route pull in Flask and module-level config), the assertion is made structurally
against the AST instead. That is weaker than execution and is labelled as such.
"""

import ast
import io
import sqlite3
import tokenize
import types
from datetime import datetime, timedelta

import harness
from harness import check, check_eq, conn, cursor, section, summary
from services import presence_service as ps

cur = cursor()
harness.bootstrap_users(cur, 8)
ps.ensure_schema(cur, conn())

ALICE, BOB, CARL, DANA = 1, 2, 3, 4

BOT_SRC = open(harness.REPO + "/bot.py", encoding="utf-8").read()
BOT_TREE = ast.parse(BOT_SRC)
BOT_FUNCS = {n.name: n for n in BOT_TREE.body if isinstance(n, ast.FunctionDef)}


def load_function(name, extra_globals=None):
    """Compile one top-level function out of bot.py and return it.

    The returned object *is* the production function -- same source, same
    bytecode. Only the globals it closes over are provided here.
    """
    node = BOT_FUNCS[name]
    module = ast.Module(body=[node], type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {
        "datetime": datetime,
        "timedelta": timedelta,
        "sqlite3": sqlite3,
        "logging": __import__("logging"),
        "re": __import__("re"),
    }
    namespace.update(extra_globals or {})
    exec(compile(module, f"bot.py:{name}", "exec"), namespace)
    return namespace[name]


def source_of(name):
    return ast.get_source_segment(BOT_SRC, BOT_FUNCS[name]) or ""


def strip_comments(src):
    """Blank out comment spans, leaving every other character in place.

    Rebuilding from tokens would reflow the file; blanking in place keeps SQL
    inside string literals byte-identical, which is what most of the source
    assertions below actually look at.
    """
    lines = src.splitlines(keepends=True)
    try:
        spans = [
            (tok.start, tok.end)
            for tok in tokenize.generate_tokens(io.StringIO(src).readline)
            if tok.type == tokenize.COMMENT
        ]
    except (tokenize.TokenError, IndentationError):
        return "\n".join(line.split("#")[0] for line in src.splitlines())
    for (row, col_start), (_, col_end) in reversed(spans):
        line = lines[row - 1]
        lines[row - 1] = line[:col_start] + " " * (col_end - col_start) + line[col_end:]
    return "".join(lines)


def code_of(name):
    """Source of a function with comments removed.

    Each fix left behind a comment naming the construct it deleted, so that a
    later reader knows why the surface looks the way it does. A plain substring
    search over the raw source therefore matches the explanation as readily as a
    relapse -- and the tempting fix, deleting the explanation, is the wrong one.
    Dropping comments first lets the assertions be about code.
    """
    return strip_comments(source_of(name))


# The whole file with comments blanked, for the sweeps that must hold globally
# rather than inside one function.
BOT_CODE = strip_comments(BOT_SRC)


# ---------------------------------------------------------------------------
section("Setup: a conversation with four members, only some of them live")

cur.execute(
    "CREATE TABLE IF NOT EXISTS pulse_conversations (id INTEGER PRIMARY KEY, title TEXT, conversation_type TEXT, last_activity_at TEXT)"
)
cur.execute(
    "CREATE TABLE IF NOT EXISTS pulse_conversation_participants "
    "(conversation_id INTEGER, user_id INTEGER, role TEXT, left_at TEXT, joined_at TEXT, created_at TEXT, unread_count INTEGER, last_read_message_id INTEGER)"
)
cur.execute(
    "CREATE TABLE IF NOT EXISTS pulse_messages "
    "(id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id INTEGER, sender_user_id INTEGER, body TEXT, message_type TEXT, deleted_at TEXT, created_at TEXT)"
)
cur.execute("CREATE TABLE IF NOT EXISTS pulse_live_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, actor_user_id INTEGER, created_at TEXT)")
cur.execute("CREATE TABLE IF NOT EXISTS pulse_online_sessions (user_id INTEGER, session_id TEXT, last_seen_at TEXT, online_status TEXT)")
cur.execute("ALTER TABLE users ADD COLUMN last_seen_at TEXT") if "last_seen_at" not in [
    r[1] for r in cur.execute("PRAGMA table_info(users)").fetchall()
] else None

ROOM = 900
cur.execute("INSERT OR REPLACE INTO pulse_conversations (id, title, conversation_type) VALUES (?,?,?)", (ROOM, "Signals", "room"))
for uid in (ALICE, BOB, CARL, DANA):
    cur.execute(
        "INSERT INTO pulse_conversation_participants (conversation_id, user_id, role, left_at, joined_at) VALUES (?,?,?,?,?)",
        (ROOM, uid, "member", "", "2024-01-01T00:00:00"),
    )
conn().commit()
check("fixture: four participants, none of whom has ever left", True, "the exact shape the old count called 'online'")

# ---------------------------------------------------------------------------
section("1. Conversation presence reads the service, not last_seen_at")

conversation_presence = load_function("pulse_conversation_presence_payload")

# The decisive fixture. Every member is stale in BOTH of the tables the old
# implementation trusted -- and both timestamps sit inside its six-minute
# window, so the old code would have called all four of them online.
recent = (datetime.utcnow() - timedelta(minutes=2)).isoformat(timespec="seconds")
for uid in (ALICE, BOB, CARL, DANA):
    cur.execute("UPDATE users SET last_seen_at=? WHERE user_id=?", (recent, uid))
    cur.execute("INSERT INTO pulse_online_sessions (user_id, session_id, last_seen_at, online_status) VALUES (?,?,?,?)", (uid, f"s{uid}", recent, "online"))
conn().commit()

payload = conversation_presence(cur, ROOM, ALICE)
by_id = {int(m["id"]): m for m in payload.get("members", payload.get("active_members", []))}
if not by_id:
    # The payload key differs by version; find the member list rather than guess.
    for key, value in payload.items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "online" in value[0]:
            by_id = {int(m["id"]): m for m in value}
            break

check("member list is present in the payload", bool(by_id), f"keys={sorted(payload)[:12]}")
check_eq("nobody is online purely on the strength of a fresh users.last_seen_at", sum(1 for m in by_id.values() if m["online"]), 0)
check_eq("...and the reported online_count agrees", int(payload.get("online_count") or 0), 0)

# Now give exactly one of them a real session.
ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone")
conn().commit()
payload = conversation_presence(cur, ROOM, ALICE)
by_id = {int(m["id"]): m for m in payload["members"]} if "members" in payload else by_id
for key, value in payload.items():
    if isinstance(value, list) and value and isinstance(value[0], dict) and "online" in value[0]:
        by_id = {int(m["id"]): m for m in value}
        break
check_eq("the one member with a live session reads online", by_id[BOB]["online"], True)
check_eq("...and only that one", sum(1 for m in by_id.values() if m["online"]), 1)
check_eq("...and online_count matches the member flags", int(payload.get("online_count") or 0), 1)

# Expire him with no reaper. The surface must flip on the next read.
harness.age_session(cur, BOB, 1)
payload = conversation_presence(cur, ROOM, ALICE)
for key, value in payload.items():
    if isinstance(value, list) and value and isinstance(value[0], dict) and "online" in value[0]:
        by_id = {int(m["id"]): m for m in value}
        break
check_eq("an expired session flips the member offline on the very next read", by_id[BOB]["online"], False)
check_eq("...and the count follows", int(payload.get("online_count") or 0), 0)

# Agreement with the service, which is the property the whole mission rests on.
truth = ps.presence_of(cur, ALICE, BOB)
check_eq("the surface agrees with the unified service", by_id[BOB]["status"], truth["status"])
check_eq("...on the last-seen sentence too, which it does not compose itself", by_id[BOB]["last_seen_text"], truth["last_seen_text"])

# ---------------------------------------------------------------------------
section("2. Conversation presence respects privacy")

ps.connect(cur, CARL, device_id="carl-phone", device_label="iPhone")
ps.set_privacy(cur, CARL, invisible_mode=True)
conn().commit()
payload = conversation_presence(cur, ROOM, ALICE)
for key, value in payload.items():
    if isinstance(value, list) and value and isinstance(value[0], dict) and "online" in value[0]:
        by_id = {int(m["id"]): m for m in value}
        break
check_eq("an invisible member reads offline to a peer", by_id[CARL]["online"], False)
check_eq("...with no activity leaked", by_id[CARL]["activity"], "idle")
check_eq("...and no last-seen leaked", by_id[CARL]["last_seen_text"], "")
check("...while the server still knows they are connected", ps.is_online(cur, CARL))
ps.set_privacy(cur, CARL, invisible_mode=False)
conn().commit()

# ---------------------------------------------------------------------------
section("3. Conversation presence fails closed when presence is unreadable")

# Point the function at a cursor whose presence tables cannot be read. A surface
# that cannot determine presence must report offline, never online.
broken = sqlite3.connect(":memory:")
broken.row_factory = sqlite3.Row
bcur = broken.cursor()
bcur.execute("CREATE TABLE pulse_conversations (id INTEGER PRIMARY KEY, title TEXT, conversation_type TEXT, last_activity_at TEXT)")
bcur.execute("CREATE TABLE pulse_conversation_participants (conversation_id INTEGER, user_id INTEGER, role TEXT, left_at TEXT, joined_at TEXT, created_at TEXT, unread_count INTEGER, last_read_message_id INTEGER)")
bcur.execute("CREATE TABLE users (user_id INTEGER PRIMARY KEY, display_name TEXT, username TEXT, avatar_url TEXT, last_seen_at TEXT)")
bcur.execute("CREATE TABLE pulse_messages (id INTEGER PRIMARY KEY, conversation_id INTEGER, sender_user_id INTEGER, body TEXT, message_type TEXT, deleted_at TEXT, created_at TEXT)")
bcur.execute("CREATE TABLE pulse_live_events (id INTEGER PRIMARY KEY, event_type TEXT, actor_user_id INTEGER, created_at TEXT, conversation_id INTEGER)")
bcur.execute("INSERT INTO pulse_conversations VALUES (1,'x','room','')")
bcur.execute("INSERT INTO users VALUES (1,'A','a','', ?)", ((datetime.utcnow()).isoformat(timespec="seconds"),))
bcur.execute("INSERT INTO pulse_conversation_participants VALUES (1,1,'member','','','',0,0)")
broken.commit()
try:
    degraded = conversation_presence(bcur, 1, 1)
    members = []
    for key, value in degraded.items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "online" in value[0]:
            members = value
            break
    check("a presence read failure does not raise out of the surface", True)
    check_eq("...and everyone reports offline", sum(1 for m in members if m["online"]), 0)
    check_eq("...with an online_count of zero", int(degraded.get("online_count") or 0), 0)
except Exception as exc:
    check("a presence read failure does not raise out of the surface", False, f"{exc.__class__.__name__}: {exc}")

# ---------------------------------------------------------------------------
section("4. Live metrics count sessions, not recent actors")

class _RealtimeStub:
    @staticmethod
    def health_snapshot():
        # Deliberately large. The old expression took max() of this against the
        # event-log figure, so a stale transport number could only inflate the
        # result. Nothing here may reach online_users.
        return {"online_users": 9999, "active_realtime_clients": 3, "failed_broadcasts": 0, "reconnect_count": 0}


live_metrics = load_function(
    "pulse_live_metrics",
    {"db": lambda: harness._SharedConn(conn()), "realtime_engine": _RealtimeStub()},
)

# Five users each did something ten seconds ago, then all closed the app.
now_iso = datetime.utcnow().isoformat(timespec="seconds")
for uid in range(1, 6):
    cur.execute(
        "INSERT INTO pulse_live_events (event_type, actor_user_id, created_at) VALUES (?,?,?)",
        ("reaction_added", uid, now_iso),
    )
conn().commit()
ps.disconnect_all(cur, BOB)
ps.disconnect_all(cur, CARL)
conn().commit()

metrics = live_metrics()
check_eq("the event log really does see five recent actors", metrics.get("actors_last_minute"), 5)
check_eq("but online_users counts live sessions only", metrics.get("online_users"), 0)
check("online_users is not inflated by the transport's own figure", metrics.get("online_users") != 9999, str(metrics.get("online_users")))

ps.connect(cur, DANA, device_id="dana-web", device_label="Chrome")
conn().commit()
metrics = live_metrics()
check_eq("one real session raises online_users to exactly one", metrics.get("online_users"), 1)
check_eq("...while the actor count is unchanged", metrics.get("actors_last_minute"), 5)
check("the two figures are reported separately, not conflated",
      metrics.get("online_users") != metrics.get("actors_last_minute"),
      f"online={metrics.get('online_users')} actors={metrics.get('actors_last_minute')}")

# Multi-device must not double-count a person.
ps.connect(cur, DANA, device_id="dana-phone", device_label="iPhone")
conn().commit()
metrics = live_metrics()
check_eq("a second device does not make one user count twice", metrics.get("online_users"), 1)

# ---------------------------------------------------------------------------
section("5. The web page-view heartbeat opens a real, expiring session")

# `pulse_mark_online` used to write a pulse_online_sessions row with
# online_status='online' -- a flag nothing ever cleared. It now calls
# touch_device, so a web user is present on the same terms as a mobile one.
EVE = 5
ps.disconnect_all(cur, EVE)
conn().commit()
check_eq("a user with no traffic is offline", ps.is_online(cur, EVE), False)

first = ps.touch_device(cur, EVE, device_id="web:abc", device_label="http", platform="web")
conn().commit()
check("a page view opens a session", first.get("ok"))
check_eq("...and the user is now online", ps.is_online(cur, EVE), True)

# The property that distinguishes this from the flag it replaced: repeated
# traffic must extend one session, not accumulate rows or churn new ones.
for _ in range(4):
    ps.touch_device(cur, EVE, device_id="web:abc", device_label="http", platform="web")
conn().commit()
check_eq("repeated page views do not accumulate sessions", len(ps.active_sessions(cur, EVE)), 1)

cur.execute("SELECT COUNT(*) AS total FROM presence_sessions WHERE user_id=?", (EVE,))
check_eq("...and do not churn a new row per request", int(dict(cur.fetchone())["total"]), 1)

# A second browser is a second device, and must not be mistaken for the first.
ps.touch_device(cur, EVE, device_id="web:xyz", device_label="http", platform="web")
conn().commit()
check_eq("a different browser is a second device", len(ps.active_sessions(cur, EVE)), 2)

# The decisive one. Nobody sends a logout; traffic simply stops.
harness.age_session(cur, EVE, 1)
check_eq("when traffic stops, the user goes offline with no cleanup event", ps.is_online(cur, EVE), False)

# And a page view after that expiry recovers rather than staying dead.
recovered = ps.touch_device(cur, EVE, device_id="web:abc", device_label="http", platform="web")
conn().commit()
check("a page view after expiry re-establishes presence", recovered.get("ok"))
check_eq("...and the user is online again", ps.is_online(cur, EVE), True)
ps.disconnect_all(cur, EVE)
conn().commit()

# ---------------------------------------------------------------------------
section("6. Typing comes from the presence service and expires on its own")

# Reset the fixture: Bob and Carl live, Dana live from section 4.
ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone")
ps.connect(cur, CARL, device_id="carl-phone", device_label="iPhone")
conn().commit()


def typing_ids_in_room(viewer=ALICE):
    payload = conversation_presence(cur, ROOM, viewer)
    return sorted(int(t["id"]) for t in payload.get("typing_users", []))


check_eq("nobody is typing to begin with", typing_ids_in_room(), [])

# The write path the Messenger typing endpoint now uses.
ps.set_activity_for_user(cur, BOB, "typing", str(ROOM))
conn().commit()
check_eq("a typing user appears in the conversation payload", typing_ids_in_room(), [BOB])
check_eq("...and not to themselves", typing_ids_in_room(viewer=BOB), [])

# Context scoping: typing in one thread is not typing in another.
ps.set_activity_for_user(cur, CARL, "typing", "999")
conn().commit()
check_eq("typing in a different conversation does not leak into this one", typing_ids_in_room(), [BOB])

# The property the mission names explicitly. Nothing calls stop; the indicator
# must clear itself. No reaper runs in this test.
cur.execute(
    "UPDATE presence_sessions SET activity_expires_at=? WHERE user_id=?",
    (ps.iso(ps.utc_now() - timedelta(seconds=1)), BOB),
)
conn().commit()
check_eq("an abandoned typing indicator clears itself with no stop event", typing_ids_in_room(), [])

# A client that vanishes mid-keystroke: session dies, indicator must go too.
ps.set_activity_for_user(cur, BOB, "typing", str(ROOM))
conn().commit()
check_eq("typing again re-arms the indicator", typing_ids_in_room(), [BOB])
harness.age_session(cur, BOB, 1)
check_eq("...and an expired session takes the typing bubble with it", typing_ids_in_room(), [])

# Privacy: an invisible user is not observable, typing included.
ps.connect(cur, BOB, device_id="bob-phone", device_label="iPhone")
ps.set_activity_for_user(cur, BOB, "typing", str(ROOM))
ps.set_privacy(cur, BOB, invisible_mode=True)
conn().commit()
check_eq("an invisible user does not broadcast a typing bubble", typing_ids_in_room(), [])
ps.set_privacy(cur, BOB, invisible_mode=False)
conn().commit()
check_eq("...and reappears when invisible mode is switched off", typing_ids_in_room(), [BOB])

# Typing cannot be asserted for someone who is not connected at all -- the
# fabrication this endpoint would otherwise permit.
ps.disconnect_all(cur, DANA)
conn().commit()
result = ps.set_activity_for_user(cur, DANA, "typing", str(ROOM))
conn().commit()
check_eq("a disconnected user cannot be given a typing indicator", result.get("ok"), False)
check("...and none appears", DANA not in typing_ids_in_room())
ps.set_activity_for_user(cur, BOB, "idle", "")
conn().commit()

# ---------------------------------------------------------------------------
section("7. The fabricated patterns are gone from the source")

# Structural assertions for the two surfaces whose dependency graphs are too
# wide to execute here. These prove the defect cannot silently return; they do
# not prove behaviour, and are weaker than sections 1-4 for that reason.

rooms_src = code_of("pulse_ensure_default_rooms")
check("Rooms no longer counts participant rows as online",
      "COUNT(*) AS total FROM pulse_conversation_participants" not in rooms_src)
check("Rooms asks the presence service instead", "presence_service.presence_for" in rooms_src)
check("Rooms energy has no invented floor", "42 + online_count" not in rooms_src)

arena_src = code_of("api_arena_presence")
check("Arena gates its roster on the presence service", "presence_service.presence_for" in arena_src)
check("Arena no longer emits the never-reset online_status", "ap.online_status" not in arena_src)
check("Arena no longer invents filler activity",
      "Scam Hunter drills are active" not in arena_src)

conv_src = code_of("pulse_conversation_presence_payload")
check("Conversation presence no longer queries pulse_online_sessions", "pulse_online_sessions" not in conv_src)
check("...and no longer falls back to users.last_seen_at for liveness", "u.last_seen_at" not in conv_src)
check("...and does not floor online_count at one", "1 if active_members else 0" not in conv_src)
check("Conversation presence no longer queries pulse_conversation_typing", "pulse_conversation_typing" not in conv_src)

list_src = code_of("pulse_conversation_summaries")
check("The conversation list no longer queries pulse_conversation_typing", "pulse_conversation_typing" not in list_src)
check("...and asks the presence service for typing instead", "activity_by_context" in list_src)

typing_route_src = code_of("api_pulse_messages_typing")
check("The typing endpoint no longer writes a parallel typing row", "pulse_conversation_typing" not in typing_route_src)
check("...and records the activity in the presence service", "set_activity_for_user" in typing_route_src)

# Nothing anywhere may still read the retired table. Its DDL survives (dropping
# a table is a migration, not a code change), but a live reader would mean a
# second typing authority had come back.
reads = [
    line.strip() for line in BOT_CODE.splitlines()
    if "pulse_conversation_typing" in line and "CREATE " not in line
]
check("no code path anywhere in bot.py still reads or writes the typing table",
      not reads, "\n         ".join(reads[:5]))

# The whole-file sweep: the fabricated fallback must not have moved elsewhere.
check("no surviving `42 + online_count` energy floor anywhere in bot.py", "42 + online_count" not in BOT_CODE)

mark_src = code_of("pulse_mark_online")
check("the page-view heartbeat no longer writes the legacy online flag",
      "pulse_online_sessions" not in mark_src)
check("...and heartbeats the presence service instead", "touch_device" in mark_src)
check("no code path anywhere in bot.py still writes online_status='online'",
      "online_status='online'" not in BOT_CODE)

# --- the Arena card builder, which reached far more surfaces than the roster --

card_src = code_of("public_arena_player")
check("the shared Arena card no longer carries online_status", "online_status" not in card_src)

# `public_arena_player` is the builder behind leaderboards, match rosters, chat
# cards and profile pages. Fixing only api_arena_presence left the fabricated
# flag reaching all of those through this one function, so the whole-file sweep
# below is the assertion that actually matters -- not "no reader on the roster"
# but "no reader, and no writer, anywhere".
#
# Every write was removed too, rather than only the reads. A column that is
# written but never read is not a live defect, but it is a loaded one: the next
# person to want a status flag finds a populated column and uses it. Only the
# DDL survives, because dropping a column is a migration rather than a code
# change; `TEXT DEFAULT` is what a surviving declaration looks like.
online_status_uses = [
    line.strip() for line in BOT_CODE.splitlines()
    if "online_status" in line
    and "show_online_status" not in line   # an unrelated privacy-preference key
    and "TEXT DEFAULT" not in line         # the column declaration and its backfill
]
check("arena_profiles.online_status has no reader and no writer left in bot.py",
      not online_status_uses, "\n         ".join(online_status_uses[:5]))

# --- Arena chat: the hardcoded header and the typing endpoint that stored nothing

chat_page_src = code_of("arena_chat_page")
check("the Arena chat header no longer hardcodes an online claim",
      "Online / recently active" not in chat_page_src)
check("...and renders presence sent by the server instead",
      "data-arena-chat-presence" in chat_page_src and "renderPresence" in chat_page_src)
check("...with no hardcoded typing line", ">Ready.<" not in chat_page_src)

chat_payload_src = code_of("arena_chat_payload")
check("the Arena chat payload sources presence from the service",
      "presence_service.presence_for" in chat_payload_src)
check("...and defaults to offline rather than online when it cannot",
      '"status": "offline"' in chat_payload_src)

# The fallback dict must be shaped like a real presence_for entry. An earlier
# draft named the boolean `active_now`, which the service does not emit; the
# client's liveness test then read undefined and an online peer rendered as
# "Offline". That fails safe, which is why nothing caught it -- the failure was
# invisible in the direction we test for. These two assertions pin the contract
# from both ends so the names cannot drift apart again.
ps.disconnect_all(cur, BOB); conn().commit()
ps.connect(cur, BOB, device_id="shape-1", platform="web"); conn().commit()
live_entry = ps.presence_for(cur, ALICE, [BOB]).get(BOB) or {}
check_eq("the service's liveness boolean is named `online`", "online" in live_entry, True)
check_eq("...and there is no `active_now` key to mistake it for", "active_now" in live_entry, False)
check_eq("...and it is true for a live peer", live_entry.get("online"), True)
for key in ("status", "online", "last_seen_at", "last_seen_text"):
    check(f"the Arena fallback declares the real key `{key}`", f'"{key}"' in chat_payload_src)
check("the Arena chat renderer tests the service's boolean, not an invented one",
      "p.online===true" in chat_page_src and "p.active_now" not in chat_page_src)
check("...and renders the server's locale-formatted last-seen text",
      "p.last_seen_text" in chat_page_src)
ps.disconnect_all(cur, BOB); conn().commit()

# The page polls a delta route every two seconds; if that route omits presence
# the header freezes at whatever the first load said.
delta_src = code_of("api_arena_chat_new")
check("the Arena chat delta route also carries presence",
      "other_presence" in delta_src)

arena_typing_src = code_of("api_arena_chat_typing")
check("the Arena typing endpoint records the activity rather than echoing it",
      "set_activity_for_user" in arena_typing_src)
check("...and no longer asserts a fixed 'typing' status back to the caller",
      '"status": "typing"' not in arena_typing_src)

# The Arena typing context is namespaced, so an Arena thread id cannot collide
# with a Messenger conversation id and light a bubble in the wrong product.
check("...and namespaces its activity context away from Messenger's",
      'f"arena:{thread_id}"' in arena_typing_src)

# The same context-scoping property Messenger has, asserted for the Arena
# namespace: this is behaviour, not source text.
ps.disconnect_all(cur, BOB); conn().commit()
ps.connect(cur, BOB, device_id="arena-1", platform="web"); conn().commit()
ps.set_activity_for_user(cur, BOB, "typing", "arena:77"); conn().commit()
arena_typing = ps.activity_by_context(cur, ALICE, ["arena:77", "77"], activities=("typing",))
check_eq("Arena typing appears under its namespaced context",
         BOB in arena_typing.get("arena:77", {}), True)
check_eq("...and does not leak into the same-numbered Messenger conversation",
         BOB in arena_typing.get("77", {}), False)
ps.disconnect_all(cur, BOB); conn().commit()

summary("test_platform_surfaces")
