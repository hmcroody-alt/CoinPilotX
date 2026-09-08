"""Three published app URLs that resolved nowhere, and what each one turned out
to be.

They arrived together in the deep-link census as three identical-looking gaps —
`/pulse/seller-store`, `/pulse/presence`, `/pulse/messages/new` — and they are
three different kinds of thing. Getting that wrong is the expensive mistake in
this work: the same census once reported `/pulse/intelligence` as having no web
surface while Flask was serving it, and that one false MISSING was enough to
justify rebuilding a page that already existed.

  seller-store   not a page at all. The native screen shows a subset of seven
                 panels per `mode`; the web split those same panels across
                 pages years ago. So it routes, and building a web Seller Store
                 would have created a second merchant console with its own
                 opinion of a seller's status.

  presence       a second front door onto `/api/pages`, which the native screen
                 says of itself in as many words. Not a second backend, so not
                 a second backend here either.

  messages/new   a real missing surface, and the only one of the three with a
                 write in it. Choosing a person creates a conversation.

The first half of this file reads the app's own TypeScript and fails if the
mapping stops agreeing with it. Every extractor asserts it matched before it
compares — a source-reading test that silently matches nothing reports success
for a file it never read, which is worse than having no test.

The second half boots the app and checks what it actually serves.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
NATIVE = os.path.join(REPO, "mobile-native", "src")
SELLER_MODE_TS = os.path.join(NATIVE, "navigation", "sellerStoreMode.ts")
MARKETPLACE_TS = os.path.join(NATIVE, "api", "marketplace.ts")
MESSENGER_TS = os.path.join(NATIVE, "api", "messenger.ts")
NEW_CHAT_TSX = os.path.join(NATIVE, "screens", "NewChatScreen.tsx")
PRESENCE_TSX = os.path.join(NATIVE, "screens", "PresenceHubScreen.tsx")
LINKING_TS = os.path.join(NATIVE, "navigation", "linking.ts")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


# --- what the app says -------------------------------------------------------


def native_seller_store_modes() -> list[str]:
    """Every key of `PANELS_BY_MODE`, which is the full set of modes."""
    block = re.search(r"const PANELS_BY_MODE[^=]*=\s*\{(.*?)\n\};",
                      _read(SELLER_MODE_TS), re.S)
    assert block, "PANELS_BY_MODE is no longer an object literal in sellerStoreMode.ts"
    # Values are sometimes a literal array and sometimes a named constant
    # (`overview: ALL_PANELS`). Matching only on `[` silently dropped a whole
    # mode the first time this ran, so the value shape is deliberately not part
    # of the pattern.
    modes = re.findall(r"^\s+(\w+):\s*\S", block.group(1), re.M)
    assert modes, "found PANELS_BY_MODE but no mode keys inside it"
    assert "overview" in modes, (
        "PANELS_BY_MODE parsed without its default mode — the extractor is "
        "matching a subset of the object")
    return modes


def native_seller_store_web_urls() -> tuple[dict[str, str], str]:
    """`sellerStoreWebUrl`: its explicit route -> path map, and its fallback.

    Returned separately because they mean different things. The explicit ones
    are the app telling the web where a mode belongs; the fallback is the app's
    answer for everything it did not name, and the router has to agree with both
    or a member following a link from the app lands somewhere else than a member
    following the same link from a browser.
    """
    block = re.search(r"export function sellerStoreWebUrl\([^)]*\)\s*\{(.*?)\n\}",
                      _read(MARKETPLACE_TS), re.S)
    assert block, "sellerStoreWebUrl is no longer a top-level function in marketplace.ts"
    body = block.group(1)
    explicit = dict(re.findall(
        r'if \(route === "(\w+)"[^)]*\) return `\$\{PULSE_API_BASE_URL\}([^`]+)`;', body))
    assert explicit, "found sellerStoreWebUrl but none of its `if (route === ...)` returns"
    tail = re.search(r"\n\s*return `\$\{PULSE_API_BASE_URL\}([^`]+)`;\s*$", body)
    assert tail, "sellerStoreWebUrl has no unconditional final return"
    return explicit, tail.group(1)


def native_presence_backend() -> str:
    """The API `PresenceHubScreen` imports its list from."""
    found = re.search(r'import \{[^}]*\blistMyPages\b[^}]*\} from "([^"]+)"',
                      _read(PRESENCE_TSX))
    assert found, "PresenceHubScreen no longer imports listMyPages"
    return found.group(1)


def native_messenger_api_prefix() -> str:
    found = re.search(r'const MESSENGER_API = "([^"]+)"', _read(MESSENGER_TS))
    assert found, "messenger.ts no longer defines MESSENGER_API"
    return found.group(1)


def native_new_chat_paths() -> tuple[str, str]:
    """The two endpoints the New Chat flow calls, fully resolved."""
    source = _read(MESSENGER_TS)
    search = re.search(r"searchMessengerUsers.*?\$\{MESSENGER_API\}([^?`]+)", source, re.S)
    assert search, "searchMessengerUsers no longer calls a MESSENGER_API path"
    opener = re.search(r"openDirectConversation.*?\$\{MESSENGER_API\}([^?`]+)`,\s*\{",
                       source, re.S)
    assert opener, "openDirectConversation no longer POSTs to a MESSENGER_API path"
    prefix = native_messenger_api_prefix()
    return prefix + search.group(1), prefix + opener.group(1)


def native_direct_open_field() -> str:
    """The body key `openDirectConversation` sends the recipient in."""
    found = re.search(r"openDirectConversation.*?body: JSON\.stringify\(\{\s*(\w+):",
                      _read(MESSENGER_TS), re.S)
    assert found, "openDirectConversation no longer sends a JSON body with one key"
    return found.group(1)


def native_new_chat_route_params() -> list[str]:
    """Which `route.params` the New Chat screen actually reads."""
    source = _read(NEW_CHAT_TSX)
    assert "NewChatScreen" in source, "NewChatScreen.tsx does not define NewChatScreen"
    params = sorted(set(re.findall(r"route\.params\?\.(\w+)", source)))
    assert params, "NewChatScreen reads no route params at all"
    return params


def native_published_paths() -> dict[str, str]:
    """`linking.ts` screen -> path, for the three screens under test."""
    source = _read(LINKING_TS)
    found = {}
    for screen in ("SellerStore", "Presence", "NewChat"):
        match = re.search(r'%s:\s*(?:"([^"]+)"|\{\s*path:\s*"([^"]+)")' % screen, source)
        assert match, f"{screen} is no longer published in linking.ts"
        found[screen] = match.group(1) or match.group(2)
    return found


# --- what the server does ----------------------------------------------------

_PROBE = r"""
import json, sys
sys.path.insert(0, %(repo)r)
import bot
from pulse_communications_v2 import service as comm_v2_service

app = bot.webhook_app
app.config["SECRET_KEY"] = "seller-presence-newchat-test"
report = {"min_query": comm_v2_service.PEOPLE_SEARCH_MIN_QUERY,
          "modes": bot.PULSE_SELLER_STORE_MODES}

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    for name in ("chatowner", "chattarget"):
        cur.execute(
            "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
            (name, name + "@example.com", name.title()))
    conn.commit()
    cur.execute("SELECT user_id, username FROM users WHERE username IN ('chatowner','chattarget')")
    ids = {row[1]: row[0] for row in cur.fetchall()}
    owner, target = ids["chatowner"], ids["chattarget"]

anonymous = app.test_client()
report["signed_out"] = {path: [anonymous.get(path).status_code,
                               anonymous.get(path).headers.get("Location", "")]
                        for path in ("/pulse/presence", "/pulse/messages/new")}

# The router is not behind the sign-in guard: it resolves an app URL to a web
# one, and each of those pages asks for a sign-in in its own words.
report["redirects"] = {}
for query in %(queries)r:
    response = anonymous.get("/pulse/seller-store" + query)
    report["redirects"][query] = [response.status_code,
                                  response.headers.get("Location", "")]

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = owner

pages = {}
for path in ("/pulse/presence", "/pulse/messages/new"):
    response = client.get(path)
    body = response.get_data()
    pages[path] = {"status": response.status_code,
                   "shell": b"office-root" in body,
                   "config": json.loads(
                       (body.decode("utf-8").split('var CFG = ', 1)[1]
                        .split(';\n', 1)[0]) if b"var CFG = " in body else "null")}
report["pages"] = pages

seeded = client.get("/pulse/messages/new?initialQuery=chattarget")
report["seeded_query"] = json.loads(
    seeded.get_data().decode("utf-8").split('var CFG = ', 1)[1].split(';\n', 1)[0]
).get("initial_query")

short = client.get("/api/pulse/communications/v2/people/search?q=c")
report["short_query"] = short.get_json()
full = client.get("/api/pulse/communications/v2/people/search?q=chattarget")
report["full_query"] = full.get_json()

# Counted only after a messenger read has built the schema. Measured before
# that, both counts came back "table missing" and the before/after assertion
# below passed without ever looking at a conversation.
def conversation_count():
    with app.app_context():
        c = bot.db()
        cu = c.cursor()
        cu.execute("SELECT COUNT(*) FROM comm_v2_conversations")
        return int(cu.fetchone()[0])

before = conversation_count()
# The published parameter the native screen declares and never reads. Rendering
# the page must not act on it: opening a conversation is a write, and a write a
# stranger can trigger by sending a link is not a feature.
client.get("/pulse/messages/new?targetUserId=%%d" %% target)
report["conversations_after_page_load"] = [before, conversation_count()]

opened = client.post("/api/pulse/communications/v2/direct/open",
                     json={"target_user_id": target})
opened_body = opened.get_json() or {}
report["opened"] = {"status": opened.status_code,
                    "conversation_id": opened_body.get("conversation_id"),
                    "count_now": conversation_count()}
adapter = app.url_map.bind("pulsesoc.com")
try:
    adapter.match("/pulse/messages/%%d" %% int(opened_body.get("conversation_id") or 0),
                  method="GET")
    report["conversation_page_resolves"] = True
except Exception:
    report["conversation_page_resolves"] = False

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""

#: Every mode the app has, plus the two shapes a real link takes: no mode at
#: all, and a mode nobody has ever shipped.
QUERIES = ["", "?mode=overview", "?mode=dashboard", "?mode=apply", "?mode=create",
           "?mode=payouts", "?mode=orders", "?mode=profile",
           "?mode=profile&sellerId=ada", "?mode=not-a-mode"]


def _run_probe(code: str, prefix: str) -> dict:
    workdir = tempfile.mkdtemp(prefix=prefix)
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "web.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    if "<<<REPORT>>>" not in proc.stdout:
        pytest.fail("the app did not boot:\n" + proc.stdout[-4000:] + proc.stderr[-4000:])
    return json.loads(proc.stdout.split("<<<REPORT>>>", 1)[1])


@pytest.fixture(scope="module")
def probe():
    return _run_probe(_PROBE % {"repo": REPO, "queries": QUERIES}, "web-tranche4-")


# --- seller store: a router, and it has to route everywhere ------------------


def test_every_mode_the_app_has_is_routed(probe):
    """A mode with no entry would fall to the dashboard silently.

    That is the right *fallback* for a stale link, but it is the wrong answer
    for a mode that was added last week: the member asks for Payouts, gets the
    store, and nothing anywhere reports a problem. So the table is required to
    name every mode the app defines, and this fails when a new one appears.
    """
    assert sorted(probe["modes"]) == sorted(native_seller_store_modes())


def test_the_router_agrees_with_the_app_about_where_each_mode_lives(probe):
    """`sellerStoreWebUrl` is the app pointing at web pages. Where it names a
    destination, the router must send people to the same one — otherwise the
    same mode reaches two different pages depending on which client you started
    from, which is the drift this whole effort exists to remove."""
    explicit, fallback = native_seller_store_web_urls()
    for mode, path in explicit.items():
        if mode == "profile":
            # Only meaningful with a seller key; covered on its own below.
            continue
        assert probe["modes"][mode] == path, (
            f"mode {mode!r} routes to {probe['modes'][mode]} on the web but the "
            f"app sends members to {path}")
    assert probe["modes"]["dashboard"] == fallback


def test_a_stale_link_lands_on_a_working_page_rather_than_a_404(probe):
    """`sellerStorePanels` answers ALL_PANELS for an unknown mode and
    `sellerStoreWebUrl` returns the dashboard, so the app treats an unrecognised
    mode as "show them the store". A 404 here would turn every link written
    before a rename into a dead end."""
    _, fallback = native_seller_store_web_urls()
    for query in ("", "?mode=not-a-mode"):
        status, location = probe["redirects"][query]
        assert status == 302
        assert location == fallback


def test_a_seller_profile_link_keeps_its_seller(probe):
    """`profile` is the one mode carrying a parameter. Dropping it would send
    someone asking for Ada's store to their own dashboard — a wrong page that
    looks like a working one."""
    status, location = probe["redirects"]["?mode=profile&sellerId=ada"]
    assert status == 302
    assert location == "/pulse/merchant/ada"


def test_a_profile_link_without_a_seller_falls_where_the_app_falls(probe):
    """`sellerStoreWebUrl("profile", "")` skips its own branch and returns the
    dashboard. Inventing a different answer here would be the web deciding what
    an incomplete link means."""
    _, fallback = native_seller_store_web_urls()
    assert probe["redirects"]["?mode=profile"][1] == fallback


def test_orders_lands_where_the_orders_panel_actually_is(probe):
    """The app does not map `orders`, so the web had to choose. It is asserted
    explicitly rather than left implicit: `orders` shows the orders panel, and
    on the web that panel is the transactions table on the payouts page. If
    someone later builds a dedicated seller-orders page, this is the line that
    should be changed to point at it."""
    assert probe["modes"]["orders"] == "/pulse/merchant/payouts"
    assert probe["redirects"]["?mode=orders"][1] == "/pulse/merchant/payouts"


# --- presence: the same Page OS, not a second one ---------------------------


def test_presence_reads_the_page_os_the_native_screen_reads(probe):
    """`PresenceHubScreen` says it "does not own a second backend, social graph
    or permission engine". Neither does this page: if it ever pointed at some
    `/api/presence`, PulseSoc would have two answers to what a Page is."""
    assert native_presence_backend().endswith("/pages")
    config = probe["pages"]["/pulse/presence"]["config"]
    assert config["api"] == "/api/pages"
    assert config["collection"] == "pages"


def test_presence_and_pages_are_both_served(probe):
    """The app ships two screens over one backend and publishes two URLs for
    them. Collapsing them into one web page would be the web deciding the
    product has one door where it has two."""
    published = native_published_paths()
    assert published["Presence"] == "pulse/presence"
    assert probe["pages"]["/pulse/presence"]["status"] == 200
    assert probe["pages"]["/pulse/presence"]["shell"] is True


def test_presence_rows_open_the_page_that_already_exists(probe):
    """Rows link into `/pulse/pages/<handle>`, the surface built for exactly
    this. A Presence detail page would be a third view of one record."""
    config = probe["pages"]["/pulse/presence"]["config"]
    assert config["row"]["href_prefix"] == "/pulse/pages/"
    assert config["row"]["href"] == "handle"


def test_a_signed_out_visitor_is_asked_to_sign_in_not_shown_an_empty_list(probe):
    """The failure this whole client is built to refuse: an empty page that
    reads as "you have nothing" when the truth is "we do not know who you are"."""
    for path in ("/pulse/presence", "/pulse/messages/new"):
        status, location = probe["signed_out"][path]
        assert status == 302
        assert "/login" in location


# --- new chat: search, then a write -----------------------------------------


def test_the_page_calls_the_same_two_endpoints_the_app_calls(probe):
    """Not "an equivalent endpoint". The same ones, so a person found in the app
    and a person found in a browser are found by one query with one set of
    visibility rules."""
    search_path, open_path = native_new_chat_paths()
    config = probe["pages"]["/pulse/messages/new"]["config"]
    assert config["api"] == search_path
    assert config["action"]["api"] == open_path
    assert config["action"]["field"] == native_direct_open_field()


def test_the_page_knows_the_servers_own_minimum_query(probe):
    """A one-character query returns an empty list, which reads exactly like
    "nobody by that name". The page can only say "keep typing" instead if it
    knows the threshold, and it must learn it from the server rather than carry
    a second copy that goes stale the day the number moves."""
    config = probe["pages"]["/pulse/messages/new"]["config"]
    assert config["min_query"] == probe["min_query"]
    assert probe["min_query"] >= 1


def test_a_query_too_short_to_run_is_not_reported_as_nobody_found(probe):
    """The distinction the endpoint now makes explicit. Without `searched`, the
    two are the same empty array and every client has to guess."""
    short = probe["short_query"]
    assert short["ok"] is True
    assert short["people"] == []
    assert short["searched"] is False
    assert short["min_query"] == probe["min_query"]


def test_a_query_that_ran_says_so_even_when_it_matched(probe):
    full = probe["full_query"]
    assert full["searched"] is True
    assert [person["display_name"] for person in full["people"]] == ["Chattarget"]


def test_loading_the_page_never_opens_a_conversation(probe):
    """`linking.ts` publishes `targetUserId` and the native screen never reads
    it. Acting on it here would mean a GET with a side effect: anyone who sends
    a link could create a conversation in the recipient's account. The count
    before and after loading that exact URL has to be the same number."""
    assert "targetUserId" in _read(LINKING_TS)
    assert "targetUserId" not in native_new_chat_route_params(), (
        "NewChatScreen now reads targetUserId; decide deliberately what the web "
        "should do with it rather than letting this test go stale")
    before, after = probe["conversations_after_page_load"]
    assert before == after
    # Both counts must be real. A missing table returns the same number twice
    # and turns this into a test that asserts nothing.
    assert before >= 0


def test_choosing_someone_opens_a_conversation_the_web_can_then_show(probe):
    """The end of the flow. A page that could find people but sent them to a
    404 afterwards would be worse than not having the page."""
    assert probe["opened"]["status"] == 200
    assert int(probe["opened"]["conversation_id"]) > 0
    assert probe["opened"]["count_now"] == probe["conversations_after_page_load"][1] + 1
    assert probe["conversation_page_resolves"] is True
    config = probe["pages"]["/pulse/messages/new"]["config"]
    assert config["action"]["goto_prefix"] == "/pulse/messages/"
    assert config["action"]["goto_key"] == "conversation_id"


def test_the_published_query_parameter_seeds_the_box(probe):
    """`initialQuery` is the name the app publishes, so a link written for the
    app has to work in a browser."""
    assert probe["seeded_query"] == "chattarget"
    assert "initialQuery" in native_new_chat_route_params()
