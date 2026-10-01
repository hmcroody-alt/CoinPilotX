"""A listing the merchant published can still be approved by a moderator.

## The bug this closes

Publication and moderation are two independent axes.
``marketplace_listing_lifecycle.is_public`` requires *both* -- a public status
and ``approval_status='approved'`` -- which is why the supplier package's
``drafts.publish`` sets ``status='published'`` and deliberately leaves
moderation alone. A published, unapproved listing is correctly invisible to
buyers, and ``publish`` says so in its own return value: ``awaiting_moderation:
True``.

``/admin/marketplace-command`` is the only moderation surface, and its approve
action asked the wrong axis::

    if action in {"approve", "reject", "request_changes"} and \\
            previous_status not in {"pending_review", "review_ready"}:
        return api_error("Listing review state changed. Reload before deciding.", 409)

``status='published'`` is not in that set, so the Approve button returned 409 on
exactly the listings ``drafts.publish`` produces. Nor could the merchant get
back to a state it accepted: nothing in the supplier package moves a published
listing to ``pending_review``. So the CJ import path terminated in a state no
moderator could act on and no merchant could leave, and the only way a real
imported product ever went live was a hand-written UPDATE against production
(``scripts/publish_and_approve_listing14.py``).

The 409 is a staleness guard -- it exists to stop a moderator deciding from a
page loaded before someone else changed the row. That is worth keeping. It was
just reading "has a decision been recorded" off ``status``, where the answer
does not live.

## Why the fix is a conjunction and not the other single column

Swapping ``status`` for ``approval_status`` alone would be worse than the bug.
``approval_status`` is ``TEXT DEFAULT 'pending_review'``, so every row is born
awaiting review, including an untouched draft with no price, no cover and
quantity 0. A moderator would have been one click from publishing a product its
own merchant had never finished -- bypassing ``drafts._validate`` entirely.

So ``awaiting_moderation`` asks both: no decision recorded *and* the merchant
has released it. ``test_an_untouched_draft_is_still_not_approvable`` is the test
that holds that line, and it is the one to read first if this file ever fails.

## Why these assertions POST to the route

The route is where the bug was, and a predicate that is right in isolation
while the route keeps its own inline copy fixes nothing. Every assertion below
is made against the status code the app returned and the row the app left
behind.
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

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: What ``drafts.publish`` leaves behind: published, moderation untouched. The
#: shape the Approve button used to 409 on, and the reason this file exists.
DROPSHIP = 78101

#: What the seller submit route leaves behind. The path that always worked --
#: present so a fix that breaks ordinary moderation cannot pass.
SUBMITTED = 78102

#: Never released by its merchant, carrying the column default on the approval
#: axis. Must stay unapprovable.
DRAFT = 78103

#: Already decided. The staleness guard's original job.
APPROVED = 78104

#: (id, status, approval_status)
SEED = (
    (DROPSHIP, "published", "pending_review"),
    (SUBMITTED, "pending_review", "pending_review"),
    (DRAFT, "draft", "pending_review"),
    (APPROVED, "published", "approved"),
)

_PROBE = r"""
import json, sys, sqlite3
sys.path.insert(0, %(repo)r)
import bot

app = bot.webhook_app
app.config["SECRET_KEY"] = "marketplace-moderation-reachability-test"
report = {}

with app.app_context():
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("INSERT INTO users (user_id, username, email, display_name, "
                "hidden_from_discovery, account_status) "
                "VALUES (9301,'seller9301','seller9301@example.com','Seller9301',0,'active')")
    cur.execute("INSERT INTO marketplace_sellers (user_id, status, display_name) "
                "VALUES (9301,'approved','Seller9301 Store')")
    # must_change_password=0 or a before_request hook redirects every admin path
    # to /admin/change-password and none of this is about moderation.
    cur.execute("INSERT INTO admin_users (email, password_hash, role, status, "
                "full_name, must_change_password) "
                "VALUES ('mod@example.com','x','owner','active','Moderator',0)")
    admin_id = cur.lastrowid
    for lid, status, approval in %(seed)r:
        cur.execute(
            "INSERT INTO marketplace_listings (id, seller_user_id, title, description, "
            "category, price_label, currency, status, approval_status, quantity, "
            "cover_image_url) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (lid, 9301, "Moderation listing %%d" %% lid, "Body of %%d" %% lid,
             "Home", "$25.00", "USD", status, approval, 3,
             "https://example.com/%%d.jpg" %% lid))
    conn.commit()
    # Proof the seed is the shape the assertions name. A DEFAULT or trigger that
    # rewrote either axis would make every result below mean something else.
    cur.execute("SELECT id, status, approval_status FROM marketplace_listings "
                "WHERE id>=78100 ORDER BY id")
    report["seeded"] = {str(r["id"]): [r["status"], r["approval_status"]]
                        for r in cur.fetchall()}
    conn.close()

client = app.test_client()
with client.session_transaction() as session:
    session["admin_user_id"] = admin_id
    session["admin_session_issued_at"] = bot.datetime.now().isoformat()
    session["admin_session_last_seen"] = bot.datetime.now().isoformat()
    session["csrf_token"] = "moderation-probe-token"

# The queue as a moderator loads it, before any decision. Captured first so the
# count reflects the four seeded rows and nothing this probe went on to change.
queue = client.get("/admin/marketplace-command")
report["queue_status"] = queue.status_code
report["queue_body"] = queue.get_data(as_text=True)

decisions = {}
for lid in %(ids)r:
    response = client.post("/admin/marketplace-command", data={
        "listing_id": str(lid),
        "action": "approve",
        "csrf_token": "moderation-probe-token",
    })
    conn = bot.db(); conn.row_factory = sqlite3.Row; cur = conn.cursor()
    cur.execute("SELECT status, approval_status, reviewed_by FROM marketplace_listings "
                "WHERE id=? LIMIT 1", (lid,))
    row = dict(cur.fetchone() or {})
    conn.close()
    decisions[str(lid)] = {
        "http": response.status_code,
        "status": row.get("status"),
        "approval_status": row.get("approval_status"),
        "reviewed_by": row.get("reviewed_by"),
    }
report["decisions"] = decisions

sys.stdout.write("<<<REPORT>>>" + json.dumps(report))
"""


@pytest.fixture(scope="module")
def moderation_probe():
    """Boot the app once, in a child process, and report what the route did.

    In a subprocess because importing ``bot`` binds ``DATABASE_URL``
    process-wide and cannot be undone.
    """
    workdir = tempfile.mkdtemp(prefix="marketplace-moderation-")
    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + os.path.join(workdir, "marketplace.db")
    env["COINPILOTX_DB_INIT_STARTUP_MODE"] = "sync"
    env["PYTHONPATH"] = REPO
    code = _PROBE % {"repo": REPO, "seed": SEED,
                     "ids": [DROPSHIP, SUBMITTED, DRAFT, APPROVED]}
    proc = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env,
                          capture_output=True, text=True, timeout=900)
    return parse_report(proc.stdout, proc.stderr)


def test_the_seed_is_what_these_tests_assume(moderation_probe):
    """Guard the fixture. Every result below is read against these four shapes."""
    seeded = moderation_probe["seeded"]
    expected = {str(lid): [status, approval] for lid, status, approval in SEED}
    assert seeded == expected, (
        "the probe did not create the four states these assertions name: %r" % seeded)


def test_the_moderator_can_reach_the_queue(moderation_probe):
    """The control. A 403 here would make every 409 below meaningless."""
    assert moderation_probe["queue_status"] == 200, (
        "the moderation page did not render (%r); the POST results below are "
        "not about moderation logic" % moderation_probe["queue_status"])


def test_a_published_dropship_listing_can_be_approved(moderation_probe):
    """The fix. This returned 409 before, on every CJ listing ever published."""
    decision = moderation_probe["decisions"][str(DROPSHIP)]
    assert decision["http"] != 409, (
        "approving a published, unmoderated listing still 409s. This is the "
        "dropship shape `drafts.publish` produces -- it sets status='published' "
        "and leaves moderation untouched on purpose, because `is_public` needs "
        "both axes. If the Approve button refuses it, a CJ import can never go "
        "live without a hand-written UPDATE.")
    assert decision["approval_status"] == "approved", (
        "the route accepted the decision but left approval_status=%r, so the "
        "listing is still not public" % decision["approval_status"])
    assert decision["status"] == "published"
    assert decision["reviewed_by"], (
        "no reviewer was recorded against the decision")


def test_an_ordinary_submitted_listing_is_still_approvable(moderation_probe):
    """No regression on the path that always worked."""
    decision = moderation_probe["decisions"][str(SUBMITTED)]
    assert decision["http"] != 409
    assert (decision["status"], decision["approval_status"]) == ("published", "approved")


def test_an_untouched_draft_is_still_not_approvable(moderation_probe):
    """The line the fix must not cross -- read this first if this file fails.

    ``approval_status`` is ``DEFAULT 'pending_review'``, so a draft is born
    looking review-ready on the moderation axis. Keying the guard on that column
    alone would let a moderator publish a product with no price, no cover and
    quantity 0 that its own merchant never released.
    """
    decision = moderation_probe["decisions"][str(DRAFT)]
    assert decision["http"] == 409, (
        "a draft was accepted for approval (HTTP %r). `approval_status` defaults "
        "to 'pending_review', so this is every unfinished import in the table."
        % decision["http"])
    assert (decision["status"], decision["approval_status"]) == ("draft", "pending_review"), (
        "the draft's row changed despite the 409: %r" % decision)


def test_an_already_approved_listing_is_not_re_decided(moderation_probe):
    """The staleness guard's original job, kept."""
    decision = moderation_probe["decisions"][str(APPROVED)]
    assert decision["http"] == 409, (
        "an already-approved listing accepted a second decision (HTTP %r)"
        % decision["http"])


def test_the_queue_counts_the_work_it_would_accept(moderation_probe):
    """The count and the button must agree about what is awaiting review.

    Two of the four seeded rows are approvable -- the dropship-published one and
    the submitted one. The old count asked ``status`` alone and could see only
    the submitted one. A moderator reading "1" has no reason to go looking for
    the dropship listing sitting further down the same page.
    """
    body = moderation_probe["queue_body"]
    marker = "Pending Products"
    assert marker in body, "the queue no longer renders a pending-products card"
    found = re.search(r"Pending Products</h2>\s*<p class='metric'>(\d+)</p>", body)
    assert found, (
        "could not read the pending-products metric out of the card: %r"
        % body.split(marker, 1)[1][:200])
    number = found.group(1)
    assert number == "2", (
        "the pending-products card reads %r, not 2. Two seeded listings await a "
        "decision: the dropship-published one and the submitted one. If this "
        "says 1, the count is asking `status` alone again and the dropship "
        "listing is invisible to the moderator who is supposed to act on it."
        % number)
