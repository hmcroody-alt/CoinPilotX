"""Stage 24/25 — what protects a write to the capability control plane.

Today ``feature_flags`` gates nothing, so a write to it is a write to a row
nobody reads. The moment this mission's wave 1 lands that stops being true and
the same form becomes a production access-control change. This module records
what stands in front of that form *as verified against the tree*, not as
remembered, so that the claim can be re-checked by a test rather than believed.

Each guard below carries a ``predicate``: a literal that must still be present
in ``bot.py`` for the guard to be doing what this file says it does. A test
re-greps them. That is deliberately a weaker check than proving the guard
works — it cannot tell you the permission string is the right one — but it is
the check that catches the realistic failure, which is a refactor quietly
dropping a line, not somebody rewriting authentication.

The two findings worth carrying forward
---------------------------------------

**1. CSRF coverage and admin authentication agree by coincidence.**

``enforce_admin_form_csrf`` (bot.py:3727) returns ``None`` — no enforcement —
when ``session.get("admin_user_id")`` is absent. That is safe today only
because ``admin_current_user`` (bot.py:16084) reads *the same session key* and
returns ``None`` for the same requests, so a request the CSRF hook declines to
check is a request the route declines to serve. Two independent-looking
defences are in fact keyed on one value.

If admin authentication ever gains a second leg — a bearer token, an API key,
an SSO header — the auth check starts passing for requests where
``admin_user_id`` is unset, and on those requests the CSRF hook silently stops
running. Not for this route: for all 79 form-driven admin POST endpoints that
the hook was introduced to cover. Nothing would fail; the protection would
just be absent.

This is not hypothetical enough to ignore. ``verify_csrf`` (bot.py:3476)
already carries ``allow_bearer=False`` and a comment explaining why, which
means somebody has previously stood at exactly this spot and considered
resolving an admin from a bearer.

**2. The owner check is in the handler body, not in a guard.**

``require_owner_admin_page()`` (bot.py:19586) exists and does precisely what
the capability matrix needs. The matrix does not use it. It calls
``require_admin_page("system.view")`` and then hand-rolls
``if not admin_is_owner_level(admin)`` inside the POST branch, after the
database connection is already open — which is why that branch has to remember
``conn.close()`` before returning 403. An in-body check placed after a resource
acquisition is the shape that gets dropped or short-circuited in a refactor,
and unlike a missing decorator it leaves no gap a reader would notice.

Not changed here. Swapping the guard would alter the response for a non-owner
admin from a rendered 403 page to a bare one, and this mission is not
authorised to change admin behaviour while it is still proving the model. It
is recorded so the cutover has it in hand.

Append-only (Stage 25)
----------------------

``log_admin_audit`` writes ``admin_audit_logs`` and ``admin_activity_logs``.
No HTTP route anywhere in the tree updates or deletes a row in either table —
the only ``DELETE`` is ``scripts/prelaunch_user_restriction_audit.py:144-145``,
a fixture script removing rows it created moments earlier, scoped by
``action LIKE 'prelaunch_%'``.

So the log is append-only *by convention*. There is no trigger, no revoked
DELETE grant, and no constraint enforcing it — the property holds because no
code violates it, which is a statement about today's tree and not about the
table. Recorded plainly rather than as "the audit log is append-only", because
the two sound alike and only one of them survives someone adding a cleanup job.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Where the guard stack was read. Any claim here is a claim about this tree.
VERIFIED_AGAINST = "bot.py @ 93766f9c2"

#: The single HTTP write path to ``feature_flags``. A grep of bot.py,
#: services/ and scripts/ finds three writes in total: this one, and the
#: INSERT + label UPDATE in ``_init_db_impl`` which have no request path.
WRITE_ROUTE = "/admin/capability-matrix"
WRITE_HANDLER = "admin_capability_matrix_page"


@dataclass(frozen=True)
class Guard:
    """One thing standing in front of the capability write."""

    name: str
    #: ``bot.py:<line>`` where the guard is applied, at time of verification.
    location: str
    #: A literal that must still appear in bot.py. Its absence means the guard
    #: has moved or gone, and either way this file is out of date.
    predicate: str
    stops: str
    #: What this guard specifically does NOT stop. Every guard has one of
    #: these; a stack documented without them reads as more complete than it is.
    does_not_stop: str


GUARDS: tuple[Guard, ...] = (
    Guard(
        name="session authentication",
        location="bot.py:103326",
        predicate='admin, denied = require_admin_page("system.view")',
        stops="An unauthenticated request. Redirected to the admin login page.",
        does_not_stop=(
            "A stolen or ridden admin session cookie. That is what the CSRF "
            "guard is for, and it is keyed on the same session value."
        ),
    ),
    Guard(
        name="role permission",
        location="bot.py:19571",
        predicate="if not admin_has_permission(admin, permission):",
        stops="An authenticated admin whose role lacks system.view.",
        does_not_stop=(
            "Anything about writing. system.view is a read permission and it "
            "gates the GET as well; on its own it would let any admin who can "
            "look at the matrix change it."
        ),
    ),
    Guard(
        name="owner level",
        location="bot.py:103333",
        # Anchored by the ``conn.close()`` that follows it, not by the bare
        # ``if`` line. A mutation test deleting the guard from the capability
        # handler left this predicate green, because the identical line also
        # appears in ``require_owner_admin_page`` (bot.py:19593) — so the
        # weaker form verified a guard standing somewhere else entirely.
        # Which is, once more, mention rather than use.
        predicate="if not admin_is_owner_level(admin):\n            conn.close()",
        stops=(
            "Every admin who is not role owner or super_admin, on POST only. "
            "This is the guard that makes the write narrower than the read."
        ),
        does_not_stop=(
            "Itself being skipped. It is an in-body check after the connection "
            "is opened, not a decorator — see the module docstring."
        ),
    ),
    Guard(
        name="CSRF",
        location="bot.py:3727",
        predicate="def enforce_admin_form_csrf():",
        stops=(
            "A cross-site form post riding the admin cookie. Enforced by a "
            "before_request hook over /admin and /api/admin, so it covers this "
            "route without the route knowing about it."
        ),
        does_not_stop=(
            "A request with no admin_user_id in session — the hook returns "
            "early. Safe only while that key is also the sole admin auth leg."
        ),
    ),
    Guard(
        name="audit record",
        location="bot.py:103364",
        predicate='log_admin_audit(\n            admin.get("id"), "feature_flag_updated"',
        stops=(
            "Nothing. It is not a guard and is listed here so that it is not "
            "mistaken for one: it records the write after the fact, including "
            "the pre-change row image, so a wrong change can be found and "
            "undone. Detection, not prevention."
        ),
        does_not_stop="The write. By design.",
    ),
)

#: Paths exempted from the CSRF hook. Small and worth pinning: an addition
#: here is an addition to the set of admin POSTs a foreign page can forge.
CSRF_EXEMPT = ("/admin/login", "/admin/logout")

#: Admin form POSTs the CSRF hook was introduced to cover, per the comment at
#: bot.py:3494. The number matters because finding 1 above is not scoped to
#: the capability matrix — it would unprotect all of them at once.
ADMIN_FORM_POSTS_COVERED = 79

#: The session key that both the CSRF hook and the admin auth check read.
#: Finding 1 is exactly the statement that this is one key, not two.
SHARED_SESSION_KEY = "admin_user_id"


def preventive_guards() -> tuple[Guard, ...]:
    """The guards that actually stop a write, excluding the audit record."""
    return tuple(g for g in GUARDS if g.name != "audit record")


def unverified_predicates(source: str) -> tuple[str, ...]:
    """Guard names whose predicate is no longer present in ``source``.

    Returns names rather than booleans so a failure says which guard moved.
    """
    return tuple(g.name for g in GUARDS if g.predicate not in source)


def report() -> str:
    lines = [
        f"CAPABILITY WRITE PATH — {WRITE_ROUTE} ({WRITE_HANDLER})",
        f"verified against {VERIFIED_AGAINST}",
        "",
    ]
    for guard in GUARDS:
        lines.append(f"{guard.name.upper()}  [{guard.location}]")
        lines.append(f"    stops:    {guard.stops}")
        lines.append(f"    does not: {guard.does_not_stop}")
        lines.append("")
    lines.append(
        "Both findings in the module docstring are recorded, not fixed: the "
        "CSRF hook and admin auth read one session key, and the owner check "
        "is in the handler body rather than a guard."
    )
    return "\n".join(lines)
