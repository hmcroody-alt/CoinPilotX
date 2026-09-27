"""A `data-emoji-for` button is inert unless its page also ships the picker.

`tests/web_surface/test_emoji_primitive.py` is the static half of this: it reads
sources and proves there is one dataset, one picker, and no surface with a
hand-written emoji list. It cannot prove the thing that actually breaks.

The delegated `data-emoji-for` listener lives in `static/js/pulse_emoji.js`. A
surface opts in by rendering one button attribute -- which is the whole point,
because a per-surface handler is how a product ends up with seven emoji
implementations. But the cost of making opt-in that cheap is a new failure that
is completely silent: add the attribute to a page that does not load
`pulse_emoji.js`, and the button renders, looks correct, is announced correctly
to a screen reader, and does nothing at all when clicked. Nothing fails. No
console error, because there is no handler to error.

Static analysis cannot catch it. Two of these surfaces get their `<head>` from
`arena_page_shell()`, several lines and one function call away from the markup
that carries the trigger, so "is the script in the same function as the button"
answers the wrong question. The only honest check is to render the page and
look at the bytes the browser would receive, which is what this does: boot the
real app against a throwaway SQLite file, sign in, GET each surface, and assert
the trigger and its runtime arrived together.

The list of surfaces is deliberately not a hardcoded census. Every page here is
a page a trigger was added to; a new surface adds a line, and the final test
asserts the set has not silently shrunk.

Run: python3 -m pytest tests/web_surface/test_emoji_surfaces_render.py
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile

import pytest

from tests.probe_report import parse_report

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))

#: The file that defines the delegated listener. A page must reference it by
#: name; the `?v=` token is deliberately not matched, because bumping the cache
#: token is a routine deploy step and must not fail this test.
PICKER = "/static/js/pulse_emoji.js"

_PROBE = r"""
import json, re, sys
sys.path.insert(0, %(repo)r)
import bot

# A comment naming the picker is not the picker, and a comment describing
# `data-emoji-for` is not a button. Counting the bare strings let prose satisfy
# the pairing: a page whose only mention of the runtime is an HTML comment --
# and `/pulse/messages` carries exactly that comment today, explaining what
# replaced its hand-written strip -- would pass "the trigger ships with its
# runtime" while every button on it sat inert. That is the one failure this
# file exists to catch, so the count has to read tags, not text.
SCRIPT = re.compile(r"<script[^>]+src=[\"'][^\"']*/static/js/pulse_emoji\.js")
COMMENT = re.compile(r"<!--.*?-->", re.S)

app = bot.webhook_app
app.config["SECRET_KEY"] = "emoji-surface-render-test"
report = {}

with app.app_context():
    conn = bot.db()
    cur = conn.cursor()
    for name in ("emojiowner", "emojipeer"):
        cur.execute(
            "INSERT INTO users (username, email, display_name) VALUES (?, ?, ?)",
            (name, name + "@example.com", name.title()))
    conn.commit()
    cur.execute("SELECT user_id, username FROM users WHERE username IN ('emojiowner','emojipeer')")
    ids = {row[1]: row[0] for row in cur.fetchall()}
    owner, peer = ids["emojiowner"], ids["emojipeer"]
    # A private thread has to exist before /chat/thread/<id> will render its
    # composer; the page 404s on a thread the signed-in user is not part of.
    thread_id = int(bot.direct_conversation_between(owner, peer))

    # /pulse/post/<id> renders a comment composer, and it is the one converted
    # surface that builds its own Response instead of going through
    # `pulse_social_shell`. That is exactly how it shipped a trigger with no
    # runtime behind it: the shell's <script> tag never reached it, the button
    # rendered, and clicking it did nothing. The path has to be in this list or
    # `test_every_rendered_emoji_trigger_ships_with_its_runtime` keeps passing
    # vacuously on the only page that can fail it that way.
    now = bot.datetime.now().isoformat()
    cur.execute(
        "INSERT INTO pulse_posts (user_id, post_type, body, visibility,"
        " moderation_status, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
        (owner, "text", "Emoji surface render probe.", "public", "approved", now, now))
    post_id = int(cur.lastrowid)
    conn.commit()

report["thread_id"] = thread_id
report["post_id"] = post_id

client = app.test_client()
with client.session_transaction() as session:
    session["account_user_id"] = owner

# Every page that renders at least one trigger needs to be here, not just the
# ones that were already here. A surface absent from this list is a surface
# none of the assertions below say anything about.
paths = [
    "/pulse",
    "/pulse/messages",
    "/chat/thread/%%d" %% thread_id,
    "/arena/inbox",
    "/pulse/post/%%d" %% post_id,
    "/pulse/profile/edit",
    "/pulse/reels",
    "/pulse/videos",
]
pages = {}
for path in paths:
    response = client.get(path)
    raw = response.get_data().decode("utf-8", "replace")
    body = COMMENT.sub("", raw)
    pages[path] = {
        "status": response.status_code,
        "location": response.headers.get("Location", ""),
        "bytes": len(raw),
        "picker": len(SCRIPT.findall(body)),
        "picker_mentions_in_prose": raw.count("pulse_emoji.js") - len(SCRIPT.findall(body)),
        # Every opt-in shape, counted separately. `data-emoji-for` with no
        # value means "the field in my own form"; with a selector it names one.
        "delegated": body.count("data-emoji-for"),
        "composer": body.count("data-composer-emoji"),
        "comment": body.count("data-comment-emoji"),
        # The two things a regression would put back.
        "hardcoded_strip": body.count("data-emoji-value"),
    }
report["pages"] = pages

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


def _run_probe(code: str, prefix: str) -> dict:
    workdir = tempfile.mkdtemp(prefix=prefix)
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "emoji.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=600)
    return parse_report(proc.stdout, proc.stderr)


@pytest.fixture(scope="module")
def probe():
    return _run_probe(_PROBE % {"repo": REPO}, "emoji-surfaces-")


def test_the_probe_rendered_real_pages(probe):
    """Assert the harness worked before reading anything out of it.

    A rendering test that silently got four redirects would report that no page
    is missing the picker, which is true and useless. Every page below has to
    have answered 200 with a real body.

    The coverage floor is named rather than counted. A bare `len(pages) == N`
    is satisfied by any N paths, so swapping a converted surface out for an
    unconverted one keeps it green while quietly removing the only page an
    assertion below could fail on -- which is how /pulse/post/<id> shipped a
    trigger with no runtime. Listing the paths means dropping one is a failure.
    """
    pages = probe["pages"]
    required = {
        "/pulse",
        "/pulse/messages",
        "/arena/inbox",
        "/pulse/post/%d" % probe["post_id"],
        "/pulse/profile/edit",
        "/pulse/reels",
        "/pulse/videos",
        "/chat/thread/%d" % probe["thread_id"],
    }
    assert required <= set(pages), (
        "the probe stopped rendering "
        f"{sorted(required - set(pages))}; those surfaces carry emoji triggers, "
        "so dropping them makes every assertion in this file vacuous for them"
    )
    for path, page in pages.items():
        assert page["status"] == 200, (
            f"{path} answered {page['status']} -> {page['location'] or 'no redirect'}; "
            "this test proves nothing about a page it never rendered"
        )
        assert page["bytes"] > 2000, (
            f"{path} rendered {page['bytes']} bytes, which is not a page"
        )


def test_every_rendered_emoji_trigger_ships_with_its_runtime(probe):
    """The silent failure this file exists for.

    A trigger without `pulse_emoji.js` on the same page is a button that
    renders correctly and does nothing. Checked per page, in the served bytes,
    because two of these pages get their <head> from a shell helper and their
    trigger from somewhere else entirely.
    """
    for path, page in probe["pages"].items():
        triggers = page["delegated"] + page["composer"] + page["comment"]
        if not triggers:
            continue
        assert page["picker"], (
            f"{path} renders {triggers} emoji trigger(s) and does not load "
            f"{PICKER}. The buttons are inert: the delegated listener that "
            "serves them is in that file, so nothing is listening for the "
            "click and nothing reports an error either."
        )


def test_the_surfaces_that_were_converted_are_still_converted(probe):
    """Per-surface floor, so a page cannot quietly lose its emoji affordance.

    Counts are floors, never equalities. A page is allowed to grow triggers --
    that is the direction this work goes -- and a test that pinned exact counts
    would fail on the next surface added and teach whoever hit it to edit the
    number instead of reading it.
    """
    pages = probe["pages"]

    home = pages["/pulse"]
    assert home["composer"] >= 1, "the /pulse composer lost its emoji button"
    assert home["delegated"] >= 1, (
        "the Status reply box lost its data-emoji-for trigger"
    )

    messages = pages["/pulse/messages"]
    assert messages["picker"] >= 1, "the messenger stopped loading the picker"
    assert messages["hardcoded_strip"] == 0, (
        "the messenger's hand-written eight-emoji strip is back; it is a "
        "second emoji implementation and the picker replaced it"
    )

    thread = next(p for k, p in pages.items() if k.startswith("/chat/thread/"))
    assert thread["delegated"] >= 1, (
        "the private thread composer lost its emoji trigger"
    )

    arena = pages["/arena/inbox"]
    assert arena["picker"] >= 1, (
        "arena_page_shell stopped loading the picker, which takes the Arena "
        "chat reply box down with it -- both pages share this shell"
    )


def test_the_picker_count_reads_script_tags_and_not_prose(probe):
    """Keep the hardening above honest by proving it is exercised.

    `/pulse/messages` explains in an HTML comment what replaced its old
    hand-written emoji strip, and that comment names the picker file. Counting
    the bare path made that page report two copies -- which would have failed
    the double-load test below for a page that loads it once, and, far worse,
    would let a page with nothing but the comment claim its buttons were wired.

    If that comment is ever deleted, this assertion fails. That is the point:
    it says out loud that the distinction between a tag and a mention is load
    bearing, so the day the last prose mention disappears someone re-reads this
    rather than quietly losing the only coverage the separation has.

    Two things keep prose out of the count -- comments are stripped, and the
    pattern matches a `<script src=>` rather than a filename -- and each is
    sufficient on its own. So neither can be killed by mutating it alone; only
    removing both turns this red, which is what was actually verified. They are
    both kept anyway: the comment strip is what protects the *attribute* counts
    below, where there is no tag shape to match on.
    """
    prose = {p: page["picker_mentions_in_prose"] for p, page in probe["pages"].items()}
    assert sum(prose.values()) > 0, (
        "no rendered page mentions pulse_emoji.js outside a <script> tag, so "
        "the comment stripping in the probe is never exercised and this file "
        f"cannot prove it counts tags rather than text: {prose}"
    )
    for path, page in probe["pages"].items():
        assert page["picker"] <= 1, path


def test_no_page_loads_the_picker_twice(probe):
    """Two copies means two delegated listeners, so one click opens two panels.

    Worth its own test because the ways it happens are invisible in review: a
    shell helper that adds the script and a page that adds it again, or a
    template extending a base that already had it. `pulse_reaction_system.css`
    is loaded twice on /pulse today for exactly that reason.
    """
    for path, page in probe["pages"].items():
        assert page["picker"] <= 1, (
            f"{path} loads {PICKER} {page['picker']} times; the file registers "
            "a document-level click listener at import, so a second copy means "
            "every emoji button opens two pickers"
        )


def test_an_opted_in_surface_can_actually_resolve_a_field():
    """`data-emoji-for` with no value needs a field inside the trigger's form.

    The empty form of the attribute means "the text field next to me", resolved
    as the first `input[type=text]`, bare `input` or `textarea` inside the
    trigger's own `<form>` or `[data-emoji-scope]`. A trigger that sits in no
    form and names no selector resolves nothing and silently does nothing --
    the same invisible failure as a missing script, one level down.

    Read out of the source rather than the render because it is a statement
    about the markup's shape, and because the render cannot tell a trigger
    whose form has no text field from one whose form has not loaded yet.
    """
    source = open(os.path.join(REPO, "bot.py"), encoding="utf-8").read()
    bare = [m.start() for m in re.finditer(r"data-emoji-for(?=[\s>])", source)]
    assert bare, "no valueless data-emoji-for trigger left in bot.py"
    for start in bare:
        # The enclosing scope, as written: back to the nearest opener and forward
        # to its closer. These are single-line inline templates, so a window of
        # the surrounding text is enough and does not need a real parser.
        window = source[max(0, start - 4000):start + 4000]
        offset = start - max(0, start - 4000)

        # `closest("form,[data-emoji-scope]")` stops at whichever comes FIRST
        # walking outward, so this has to consider both and take the nearer.
        # Checking only <form> would reject two surfaces the primitive handles
        # correctly -- the live-studio chat composer, which is a <div>, and the
        # profile-edit Bio field, which is a <label> on a page with no <form> at
        # all -- both of which carry an explicit [data-emoji-scope] wrapper.
        candidates = []
        form_at = window.rfind("<form", 0, offset)
        if form_at >= 0:
            candidates.append((form_at, window.find("</form>", offset)))
        scope_attr = window.rfind("data-emoji-scope", 0, offset)
        if scope_attr >= 0:
            tag_at = window.rfind("<", 0, scope_attr)
            tag = re.match(r"<([a-zA-Z][\w-]*)", window[tag_at:])
            if tag:
                candidates.append((tag_at, window.find(f"</{tag.group(1)}>", offset)))

        # Nearest opener wins, mirroring the DOM walk.
        candidates = [(o, c) for o, c in candidates if o >= 0 and c > offset]
        assert candidates, (
            "a valueless data-emoji-for trigger is in neither a <form> nor a "
            "[data-emoji-scope], so it has nothing to resolve; give it a "
            f"selector or a scope: {window[max(0, offset - 160):offset + 60]!r}"
        )
        open_at, close_at = max(candidates, key=lambda pair: pair[0])
        enclosing = window[open_at:close_at]
        assert re.search(r"<textarea|<input(?![^>]*type=)|<input[^>]*type=\"?text", enclosing), (
            "a valueless data-emoji-for trigger's scope has no text field for "
            f"the picker to write into: {enclosing[:180]!r}"
        )
