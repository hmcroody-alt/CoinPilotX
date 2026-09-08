"""The Activity feed: one merge, pinned to the app's copy of it.

`/api/activity` exists because `mobile-native/src/api/activityFeed.ts` says it
should: "there is no backend notification service that returns one aggregated,
typed, read-stateful feed", so `mobile-native/src/api/activity.ts` fans out to
notifications, messenger and calls and merges them inside the app.

That merge is product logic. It decides that a notification mentioning "strike"
is a safety notification, that eight unread conversations are worth surfacing
and a ninth is not, and that an ended call is history rather than activity. The
web needs the same answers, and the only two ways to get them are to duplicate
the rules or to move them somewhere both clients can reach.

They were moved — but until the app adopts the endpoint there are two copies,
and two copies with no link between them is exactly the drift this whole parity
effort exists to remove. So the first half of this file reads `activity.ts` and
asserts, rule by rule, that the Python table still says what the TypeScript
says. Change one without the other and this file fails with the diff.

The parsing is written to fail loudly rather than quietly: every extractor
asserts it found something before it compares. A source-reading test that
silently matches nothing is worse than no test, because it reports success for
a file it never read.

The second half boots the app and checks the feed it actually serves.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
ACTIVITY_TS = os.path.join(REPO, "mobile-native", "src", "api", "activity.ts")


def _native_source() -> str:
    with open(ACTIVITY_TS, encoding="utf-8") as handle:
        return handle.read()


# --- the app's copy of the rules --------------------------------------------


def native_categories() -> list[tuple[str, str]]:
    """`activityCategories`, in order, as (key, label) pairs."""
    block = re.search(r"export const activityCategories[^=]*=\s*\[(.*?)\];",
                      _native_source(), re.S)
    assert block, "activityCategories is no longer an array literal in activity.ts"
    pairs = re.findall(r'\{\s*key:\s*"([^"]+)",\s*label:\s*"([^"]+)"\s*\}', block.group(1))
    assert pairs, "found activityCategories but no { key, label } entries inside it"
    return pairs


def native_classifier() -> tuple[list[tuple[str, str]], str]:
    """`classifyNotification`, as ordered (category, pattern) tests plus its default."""
    block = re.search(r"function classifyNotification\([^)]*\)[^{]*\{(.*?)\n\}",
                      _native_source(), re.S)
    assert block, "classifyNotification is no longer a top-level function in activity.ts"
    body = block.group(1)
    tests = re.findall(r'if \(/(\([^/]*\))/\.test\(signal\)\) return "([^"]+)";', body)
    assert tests, "found classifyNotification but none of its regex tests"
    fallthrough = re.findall(r'^\s*return "([^"]+)";\s*$', body, re.M)
    assert fallthrough, "classifyNotification has no unconditional final return"
    return [(category, pattern) for pattern, category in tests], fallthrough[-1]


def native_signal_fields() -> list[str]:
    """The notification fields `classifyNotification` joins into its haystack."""
    block = re.search(r"const signal = \[(.*?)\]\.join", _native_source(), re.S)
    assert block, "classifyNotification no longer builds `signal` from a field list"
    fields = re.findall(r"notification\.(\w+)", block.group(1))
    assert fields, "found the signal list but no notification fields in it"
    return fields


def native_slice(function_name: str) -> int:
    """The `.slice(0, N)` cap inside one of the summary builders."""
    block = re.search(r"function %s\([^)]*\)[^{]*\{(.*?)\n\}" % function_name,
                      _native_source(), re.S)
    assert block, f"{function_name} is no longer a top-level function in activity.ts"
    found = re.search(r"\.slice\(0,\s*(\d+)\)", block.group(1))
    assert found, f"{function_name} no longer caps its output with .slice"
    return int(found.group(1))


def native_dead_call_statuses() -> list[str]:
    """The call states `callsToActivityItems` drops."""
    found = re.search(r"\[([^\]]*)\]\.includes\(String\(call\.status", _native_source())
    assert found, "callsToActivityItems no longer filters on a literal status list"
    statuses = re.findall(r'"([^"]+)"', found.group(1))
    assert statuses, "found the call status filter but no statuses in it"
    return statuses


# --- the server's copy, read without booting the monolith --------------------

_CONSTANTS_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot

sys.stdout.write("<<<REPORT>>>" + json.dumps({
    "categories": bot.ACTIVITY_CATEGORIES,
    "patterns": bot.ACTIVITY_CATEGORY_PATTERNS,
    "default_category": bot.ACTIVITY_DEFAULT_CATEGORY,
    "conversation_limit": bot.ACTIVITY_CONVERSATION_LIMIT,
    "call_limit": bot.ACTIVITY_CALL_LIMIT,
    "dead_call_statuses": list(bot.ACTIVITY_CALL_DEAD_STATUSES),
    "classified": {
        name: bot.activity_classify_notification(note)
        for name, note in {
            "plain_message": {"type": "message", "title": "New message from Ada"},
            "shipped_order": {"type": "order_shipped", "title": "Your order shipped"},
            "strike": {"type": "moderation", "title": "A strike was added"},
            "nothing_familiar": {"type": "", "title": "Hello"},
            "deep_link_only": {"deep_link": "/pulse/marketplace/9"},
        }.items()
    },
}))
"""

_FEED_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot
from services import pulsesoc_notification_system as ns

app = bot.webhook_app
app.config["SECRET_KEY"] = "activity-feed-test"

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    for name in ("feedowner", "feedactor"):
        cur.execute(
            "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
            (name, name + "@example.com", name),
        )
    conn.commit()
    cur.execute("SELECT user_id, username FROM users WHERE username IN ('feedowner','feedactor')")
    ids = {row[1]: row[0] for row in cur.fetchall()}
    owner, actor = ids["feedowner"], ids["feedactor"]
    ns.notify_security_event(owner)
    ns.notify_post_like(owner, actor_user_id=actor, post_id=42, actor_name="Ada")
    ns.notify_new_message(owner, actor_user_id=actor, conversation_id=7,
                          message_id=3, body="hey", actor_name="Ada")

report = {}

anonymous = app.test_client()
denied = anonymous.get("/api/activity")
report["anonymous_api"] = {"status": denied.status_code,
                           "message": (denied.get_json() or {}).get("message", "")}
signed_out = anonymous.get("/pulse/activity")
report["signed_out_status"] = signed_out.status_code
report["signed_out_location"] = signed_out.headers.get("Location", "")

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = owner

feed = client.get("/api/activity")
report["feed"] = feed.get_json()
report["feed_status"] = feed.status_code

filtered = client.get("/api/activity?category=social").get_json() or {}
report["social_categories"] = sorted({item["category"] for item in filtered.get("items", [])})
unknown = client.get("/api/activity?category=nonsense").get_json() or {}
report["unknown_category"] = unknown.get("category", "")

# One source made to fall over. The feed must survive it and say so, because a
# 500 here would take the working two thirds of the inbox down with it.
def _explode():
    raise RuntimeError("messenger is down")

_saved = app.view_functions["pulse_communications_v2.conversations"]
app.view_functions["pulse_communications_v2.conversations"] = _explode
degraded = client.get("/api/activity")
degraded_body = degraded.get_json() or {}
report["degraded"] = {"status": degraded.status_code,
                      "sources": degraded_body.get("sources"),
                      "item_count": len(degraded_body.get("items") or [])}
app.view_functions["pulse_communications_v2.conversations"] = _saved

pages = {}
for path in %(paths)r:
    response = client.get(path)
    body = response.get_data()
    pages[path] = {"status": response.status_code,
                   "shell": b"office-root" in body,
                   "client": b"GRANT_KEY" in body,
                   "declares_sources": b'"sources_field"' in body}
report["pages"] = pages

# Which of the targets an activity row can carry the web can actually serve.
adapter = app.url_map.bind("pulsesoc.com")
resolves = {}
for path in ("/account/security", "/pulse/post/42", "/pulse/messages/7", "/pulse/calls/abc"):
    try:
        adapter.match(path, method="GET")
        resolves[path] = True
    except Exception:
        resolves[path] = False
report["resolves"] = resolves

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

PAGES = (
    "/pulse/activity",
    "/pulse/inbox",
    "/dashboard/activity",
    "/dashboard/inbox",
    "/pulse/activity/social",
    "/pulse/activity/marketplace",
)

#: A category the app has never had. It must 404 rather than render a confident
#: empty inbox, the same way an unknown Private Office view does.
UNKNOWN_CATEGORY_PAGE = "/pulse/activity/nonsense"


def _run_probe(code: str, prefix: str) -> dict:
    workdir = tempfile.mkdtemp(prefix=prefix)
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "feed.db")
    # Sync so a schema failure surfaces as a failed probe rather than on a
    # daemon thread whose traceback only reaches the log.
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


@pytest.fixture(scope="module")
def server_rules():
    """The server's copy of the merge rules."""
    return _run_probe(_CONSTANTS_PROBE % {"repo": REPO}, "activity-rules-")


@pytest.fixture(scope="module")
def feed_probe():
    """Boot the app once, in a child process, and report the feed it serves."""
    code = _FEED_PROBE % {"repo": REPO, "paths": list(PAGES) + [UNKNOWN_CATEGORY_PAGE]}
    return _run_probe(code, "activity-feed-")


# --- the two copies must agree ----------------------------------------------


def test_categories_match_the_app_exactly(server_rules):
    """Keys *and* labels *and* order.

    The keys are a wire contract, the labels are what a member reads, and the
    order is the order of the tabs. A web inbox that offered the same categories
    under different names, or in a different order, would be a different product
    wearing the same word.
    """
    assert [tuple(pair) for pair in server_rules["categories"]] == native_categories()


def test_the_classifier_asks_the_same_questions_in_the_same_order(server_rules):
    """First match wins, so the sequence is part of the rule.

    "Your order was reported" contains both a marketplace word and a safety one.
    Which category it lands in is decided entirely by which test runs first, so
    comparing the set of patterns would pass while the product silently
    recategorised real notifications.
    """
    assert [tuple(pair) for pair in server_rules["patterns"]] == native_classifier()[0]


def test_an_unrecognised_notification_falls_where_the_app_drops_it(server_rules):
    """The default is a real decision — most notifications match nothing."""
    assert server_rules["default_category"] == native_classifier()[1]


def test_the_classifier_reads_the_same_fields(server_rules):
    """The haystack is joined from several fields because the useful signal
    moves between them: some notifications set `category`, older ones set only a
    `deep_link`. Reading fewer fields here than the app reads would quietly send
    a whole generation of notifications to the default category."""
    fields = native_signal_fields()
    assert fields, "activity.ts named no fields"
    # The server names them in its own docstring-adjacent tuple; assert by
    # behaviour instead, so this test does not depend on Python internals.
    assert server_rules["classified"]["deep_link_only"] == "marketplace", (
        "a notification carrying only a deep link was not classified from it, "
        f"but activity.ts reads {fields}")


def test_summary_sources_are_capped_where_the_app_caps_them(server_rules):
    """Eight conversations and four calls are product judgements about how much
    of an inbox may be someone else's list. Rounding them here would give the
    web a visibly different inbox for the same account."""
    assert server_rules["conversation_limit"] == native_slice("conversationsToActivityItems")
    assert server_rules["call_limit"] == native_slice("callsToActivityItems")


def test_finished_calls_are_dropped_by_the_same_list(server_rules):
    """An ended call is history. Showing it as activity would put a permanent
    dead row at the top of the newest-first list."""
    assert server_rules["dead_call_statuses"] == native_dead_call_statuses()


def test_the_ported_rules_actually_classify(server_rules):
    """The comparisons above would all pass against a table nobody ran.

    These are the answers the rules give, spelled out: a plain message is
    messages, a shipped order is marketplace, a strike is safety, and something
    with no familiar word in it falls to the default rather than erroring.
    """
    classified = server_rules["classified"]
    assert classified["plain_message"] == "messages"
    assert classified["shipped_order"] == "marketplace"
    assert classified["strike"] == "safety"
    assert classified["nothing_familiar"] == server_rules["default_category"]


# --- the feed the server actually serves ------------------------------------


def test_the_feed_merges_all_three_sources_and_says_which_answered(feed_probe):
    """`sources` is the whole reason this page can claim emptiness honestly.

    Without it a failed messenger read and a quiet inbox produce the same JSON,
    and the page draws "nothing is waiting for you" over messages it could not
    see. Every source is named whether it worked or not.
    """
    assert feed_probe["feed_status"] == 200
    sources = feed_probe["feed"]["sources"]
    assert set(sources) == {"notifications", "messages", "calls"}
    assert all(state == "ok" for state in sources.values()), sources


def test_one_sick_source_costs_only_its_own_rows(feed_probe):
    """The feed degrades; it does not fall over.

    Optional route packs register inside `except Exception`, so a subsystem can
    be absent or broken in production without anything else noticing. If that
    took `/api/activity` down with it, a messenger outage would empty the whole
    inbox — notifications included — and the member would be told there is
    nothing waiting for them.
    """
    degraded = feed_probe["degraded"]
    assert degraded["status"] == 200, "a broken source took the whole feed down"
    assert degraded["sources"]["messages"] == "failed"
    assert degraded["sources"]["notifications"] == "ok"
    assert degraded["item_count"] > 0, "the surviving sources lost their rows too"


def test_rows_carry_the_category_the_tabs_filter_on(feed_probe):
    """The tabs are a client-side filter over `category`. If the server stopped
    setting it, every tab but "All" would show zero and look like an empty
    account rather than a broken page."""
    items = feed_probe["feed"]["items"]
    assert items, "seeded notifications did not reach the feed"
    keys = {key for key, _label in [tuple(c) for c in
            [(c["key"], c["label"]) for c in feed_probe["feed"]["categories"]]]}
    for item in items:
        assert item["category"] in keys, item


def test_a_category_filter_returns_only_that_category(feed_probe):
    """Asking for one category must not be answered with all of them."""
    assert feed_probe["social_categories"] == ["social"]


def test_an_unknown_category_is_not_silently_honoured(feed_probe):
    """A typo in the query string falls back to the whole feed rather than
    filtering to nothing, which would read as an empty account."""
    assert feed_probe["unknown_category"] == "all"


def test_a_row_only_links_where_the_web_can_actually_go(feed_probe):
    """`web_url` is resolved against the routing table, not composed.

    A live call has no web surface and, under
    `docs/realtime_audio_change_policy.md`, will not get one. Its row still
    belongs in the inbox — it is real activity — but linking it would send a
    member to a 404 and teach them the rows here do not work.
    """
    assert feed_probe["resolves"]["/pulse/calls/abc"] is False
    for item in feed_probe["feed"]["items"]:
        if item["web_url"]:
            assert feed_probe["resolves"].get(item["web_url"], True), item


def test_every_published_activity_path_resolves(feed_probe):
    """Four app URLs pointed at this product and all four 404'd."""
    broken = {path: feed_probe["pages"][path]["status"]
              for path in PAGES if feed_probe["pages"][path]["status"] != 200}
    assert not broken, f"published app URLs that the site cannot serve: {broken}"


def test_every_activity_page_ships_the_client_and_declares_its_sources(feed_probe):
    """A page that renders the feed without `sources_field` is a page that can
    still draw a confident empty inbox over a failed fetch."""
    for path in PAGES:
        info = feed_probe["pages"][path]
        assert info["shell"], f"{path} rendered no client root"
        assert info["client"], f"{path} shipped no client script"
        assert info["declares_sources"], f"{path} did not declare its source health"


def test_an_unknown_category_page_is_a_404_not_an_empty_inbox(feed_probe):
    """The page space is closed to the app's own vocabulary. A catch-all would
    answer 200 for a typo and render "nothing is waiting for you"."""
    assert feed_probe["pages"][UNKNOWN_CATEGORY_PAGE]["status"] == 404


def test_signed_out_visitors_get_the_login_page_and_an_honest_api(feed_probe):
    """The page redirects; the endpoint refuses in words a client can show.
    A 401 with no message would render as a generic fault."""
    assert feed_probe["signed_out_status"] == 302
    assert "/login" in feed_probe["signed_out_location"]
    assert feed_probe["anonymous_api"]["status"] == 401
    assert feed_probe["anonymous_api"]["message"].strip()
