"""Default-deny for routes: a new route must say what it is, and an old route
must not quietly stop being protected.

`bot.py` gates nothing with decorators — every one of its ~2,160 views
authenticates itself by calling a helper inside its own body. The consequence
that matters is that **adding a route with no authentication is a silent,
test-passing change**. Nothing goes red. Nothing is reported. It ships.

This file is what makes that loud, in two independent ways, and they protect
against different failures:

* `test_new_routes_must_declare_their_auth` is the **default-deny rule**. A route
  that exists today and is not in `config/route_auth_baseline.json` has to carry
  `@auth_required`, `@admin_required` or `@public_route(reason=...)`. Not "we
  looked and found no problem" — "you did not say, so we refuse". This is the
  rule the web rebuild is built under, and it applies to new code only, because
  applying it to the 2,160 legacy routes would mean either a 2,160-line audit
  nobody can review or declaring them all public, which would bake in a lie.

* `test_no_route_loses_its_gate` is the **regression rule**. It compares every
  legacy route's evidence against the frozen baseline. A route whose evidence
  degrades — an admin surface that stops calling an admin gate, a member route
  whose refusal disappears — fails here.

Neither test claims the baselined routes are safe. `services/route_auth.py`
explains at length why the detector is bookkeeping and not a security boundary;
the short version is that it proves a *name appears in a function body*, and the
one thing it is designed never to do is let an open route present as protected.

Run: python3 -m pytest tests/protection/test_route_auth.py
Regenerate the baseline (deliberately, reading the diff):
    python3 scripts/protection/generate_route_auth_baseline.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BASELINE_PATH = os.path.join(ROOT, "config", "route_auth_baseline.json")

# `bot` resolves DATABASE_URL at import and never re-reads it, so this has to
# happen before the import below and it has to override rather than defer. The
# variable a developer already exported is the one that reaches a database they
# care about; importing bot runs init_db() and creates several hundred tables.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(
    prefix="route-auth-protection-", suffix=".db"
)[1]
os.environ.setdefault("FLASK_SECRET_KEY", "route-auth-protection")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import bot  # noqa: E402
from services import route_auth  # noqa: E402


#: Evidence strings, ranked by how much they actually show. The order is the
#: point: the test below allows a route to move up and fails when it moves down.
#:
#: `gates` outranks `identity+refusal` because a refusal-returning helper cannot
#: be ignored by its caller, whereas an identity helper plus a refusal somewhere
#: in the same body is two facts that are *usually* connected.
EVIDENCE_RANK = {
    "no-view-function": 0,
    "source-unavailable": 0,
    "no-known-gate": 1,
    "identity-without-refusal": 2,
    "via": 3,
    "identity+refusal": 4,
    "gates": 5,
    "declared": 6,
}


def evidence_kind(evidence: str) -> str:
    return (evidence or "").split(":")[0]


def rank(evidence: str) -> int:
    return EVIDENCE_RANK.get(evidence_kind(evidence), 0)


#: Memo for the three expensive inputs. Plain functions and a dict rather than
#: pytest fixtures, and the reason is not style.
#:
#: `scripts/protection/run_protection_suite.py` executes each file in this
#: directory as a *script* and requires it to report a non-zero check count, so
#: every suite here also runs under `tests/protection/_runner.py`, which calls
#: each `test_*` with no arguments. Fixture parameters would raise TypeError
#: there — the file would be green under pytest and counted as failing under the
#: runner.
#:
#: The obvious repair — a fixture *and* a default, `def test_x(baseline=None)` —
#: was measured and is worse than either: pytest honours the default and never
#: calls the fixture at all, silently. A test that reads its inputs from a stale
#: default while appearing to be parameterised is the exact shape of failure the
#: rest of this file exists to make loud.
_STATE: dict = {}


def load_baseline() -> dict:
    if "baseline" not in _STATE:
        with open(BASELINE_PATH, "r", encoding="utf-8") as handle:
            _STATE["baseline"] = json.load(handle)
    return _STATE["baseline"]


def audit_records() -> list[dict]:
    """The per-rule audit, walked once per process.

    Classifying 2,160 views means reading 2,160 function bodies; a file that
    redoes it per test is a file people start skipping.
    """
    if "records" not in _STATE:
        _STATE["records"] = route_auth.audit_app(bot.app)
    return _STATE["records"]


def audit_by_endpoint() -> dict:
    if "current" not in _STATE:
        _STATE["current"] = group_records(audit_records())
    return _STATE["current"]


def group_records(raw: list[dict]) -> dict:
    """Collapse per-rule audit records onto the endpoint that owns them."""
    grouped: dict[str, dict] = {}
    for record in raw:
        entry = grouped.setdefault(record["endpoint"], {
            "rules": set(), "methods": set(),
            "auth": record["auth"], "evidence": record["evidence"],
            "declared": record["declared"],
        })
        entry["rules"].add(record["rule"])
        entry["methods"].update(record["methods"])
    return grouped


def test_the_vocabulary_still_names_real_functions():
    """Guard the guard.

    Every check in this file runs through a list of helper names. Rename one of
    those helpers and the detector stops recognising the routes it guards —
    which, for a helper used by a handful of routes, is a silent loss of
    accuracy while every assertion here stays green. This is the one failure the
    baseline cannot catch, because the baseline would degrade in exactly the
    same direction.
    """
    undefined = route_auth.undefined_gate_helpers(ROOT)
    assert undefined == [], (
        f"services/route_auth.py believes in helpers that no longer define "
        f"anything: {undefined}. Either update the vocabulary to the new names, "
        f"or remove them and reclassify the routes that used them."
    )


def test_new_routes_must_declare_their_auth():
    """Default-deny. A route not in the baseline must say what it is."""
    baseline, current = load_baseline(), audit_by_endpoint()
    known = set(baseline["routes"])
    undeclared = sorted(
        endpoint for endpoint, entry in current.items()
        if endpoint not in known and not entry["declared"]
    )
    assert undeclared == [], (
        "These routes are new since the baseline and do not declare their "
        "authentication:\n  "
        + "\n  ".join(
            f"{e} ({', '.join(sorted(current[e]['rules']))})" for e in undeclared
        )
        + "\n\nAdd one of @auth_required / @admin_required / "
        "@public_route(reason='...') from services.route_auth. The detector "
        "finding a gate in the body is NOT a substitute: it cannot tell a route "
        "that forgot its check from one that never needed it, which is the "
        "entire reason declarations exist.\n\n"
        "If these are not new -- if you moved or renamed an existing route -- "
        "regenerate the baseline as part of the same commit so the diff shows "
        "the move."
    )


def test_no_route_loses_its_gate():
    """Regression. Evidence may improve; it may not degrade."""
    baseline, current = load_baseline(), audit_by_endpoint()
    regressions = []
    for endpoint, was in baseline["routes"].items():
        now = current.get(endpoint)
        if now is None:
            # Deleting a route is not a security regression, and route packs
            # register conditionally (`except Exception` around whole families),
            # so absence here is routine rather than alarming. Covered instead
            # by test_no_route_family_vanished_wholesale.
            continue
        if rank(now["evidence"]) < rank(was["evidence"]):
            regressions.append(
                f"{endpoint} ({', '.join(sorted(now['rules']))})\n"
                f"      was: {was['auth']:<8s} {was['evidence']}\n"
                f"      now: {now['auth']:<8s} {now['evidence']}"
            )

    assert regressions == [], (
        "These routes have weaker authentication evidence than the baseline "
        "recorded:\n  " + "\n  ".join(regressions)
        + "\n\nThe usual cause is a gate helper being removed from a view, or a "
        "refusal being refactored out of it. If the route genuinely changed "
        "shape for a good reason, regenerate the baseline in the same commit so "
        "the diff is reviewable."
    )


def test_admin_routes_do_not_become_member_routes():
    """A privilege drop is a different bug from a missing gate.

    Evidence rank cannot see this one: `gates:require_owner_api` and
    `gates:api_pro_required` are both rank 5, so a route that stopped requiring
    an administrator and started requiring merely a logged-in member passes the
    test above without complaint. That is a privilege escalation for every
    member of the site, and it is exactly the sort of thing a hurried refactor
    of an admin surface produces.
    """
    baseline, current = load_baseline(), audit_by_endpoint()
    downgrades = []
    for endpoint, was in baseline["routes"].items():
        now = current.get(endpoint)
        if now is None or was["auth"] != route_auth.AUTH_ADMIN:
            continue
        if now["auth"] != route_auth.AUTH_ADMIN:
            downgrades.append(
                f"{endpoint} ({', '.join(sorted(now['rules']))}): "
                f"admin -> {now['auth']} ({now['evidence']})"
            )

    assert downgrades == [], (
        "These routes were administrative in the baseline and no longer "
        "classify that way:\n  " + "\n  ".join(downgrades)
        + "\n\nAdministrative standing comes from the admin session OR from "
        "user_is_super_user() on a member row; losing either check on an admin "
        "surface exposes it to every signed-in member."
    )


def test_no_new_route_hides_behind_the_weak_admin_gate():
    """`require_admin_password()` must not spread.

    It takes a shared secret from the **query string** (so it lands in access
    logs, proxies and browser history), compares it with `==` rather than
    `secrets.compare_digest`, and carries no per-actor identity, so nothing it
    guards produces an attributable audit trail. The existing call sites are a
    known debt with a documented count; this test's only job is to keep that
    count from growing while nobody is looking.
    """
    baseline = load_baseline()
    weak_now = {
        r["endpoint"] for r in route_auth.routes_behind_weak_gates(audit_records())
    }
    # Both sides go through the same function rather than this test restating
    # the rule. Restating it means the day someone adds a second weak helper,
    # the "before" set keeps the old definition and the count silently resets:
    # every route already using the new helper reads as pre-existing.
    weak_before = {
        r["endpoint"] for r in route_auth.routes_behind_weak_gates(
            dict(entry, endpoint=endpoint)
            for endpoint, entry in baseline["routes"].items()
        )
    }
    added = sorted(weak_now - weak_before)
    assert added == [], (
        "These routes newly rely on require_admin_password() as their only "
        f"gate:\n  {chr(10) + '  '.join(added)}\n\n"
        "Use the admin session (require_admin_api / require_admin_page) "
        "instead. A query-string shared secret is not an identity and cannot be "
        "audited."
    )


def test_no_route_family_vanished_wholesale():
    """Catch a route pack that failed to register, rather than a route deleted.

    Optional route packs are registered inside `except Exception` blocks so one
    broken import cannot stop the process booting. The cost is that a whole
    subsystem can disappear silently -- and if it disappears during this run,
    every one of its routes is simply absent, which the regression test above
    deliberately ignores. A missing *handful* of endpoints is ordinary
    deletion; a missing *hundred* is a blueprint that did not load, and it would
    otherwise make this entire file vacuously green for that subsystem.
    """
    baseline, current = load_baseline(), audit_by_endpoint()
    missing = set(baseline["routes"]) - set(current)
    total = len(baseline["routes"])
    assert len(missing) <= max(25, total // 50), (
        f"{len(missing)} of {total} baselined endpoints are not registered in "
        f"this process. That is too many to be deletions -- check the boot log "
        f"for a route pack that raised during registration, because every route "
        f"it owns is currently unprotected-by-absence rather than gated.\n"
        f"Examples: {sorted(missing)[:15]}"
    )


def test_the_boot_assertion_refuses_an_undeclared_route():
    """The fail-closed half, exercised rather than assumed.

    `assert_routes_declared()` runs once at the bottom of `bot.py` and is inert
    while `DECLARATION_REQUIRED_MODULES` is empty. Inert code that has never been
    observed doing its job is indistinguishable from broken code, and this one
    would be discovered broken on the day it was supposed to stop a breach.

    Exercised on a throwaway Flask app: pointing it at `bot.app` would mean
    registering routes on the object every other test in this file measures.
    """
    import flask

    probe = flask.Flask("route_auth_boot_probe")

    def undeclared():
        return {"ok": True}

    declared = route_auth.public_route("protection probe")(lambda: {"ok": True})
    declared.__name__ = "declared_probe"
    undeclared.__module__ = declared.__module__ = "route_auth_boot_probe_module"

    probe.add_url_rule("/probe/declared", "declared_probe", declared)

    original = route_auth.DECLARATION_REQUIRED_MODULES
    try:
        route_auth.DECLARATION_REQUIRED_MODULES = frozenset({"route_auth_boot_probe_module"})

        # A guarded module whose routes all declare must not block a boot.
        route_auth.assert_routes_declared(probe)

        probe.add_url_rule("/probe/undeclared", "undeclared_probe", undeclared)
        try:
            route_auth.assert_routes_declared(probe)
        except RuntimeError as exc:
            assert "/probe/undeclared" in str(exc), (
                f"the guard refused but did not name the offending route: {exc}"
            )
        else:
            raise AssertionError(
                "assert_routes_declared() accepted an undeclared route in a "
                "declaration-required module. The boot-time half of default-deny "
                "is not enforcing anything."
            )
    finally:
        route_auth.DECLARATION_REQUIRED_MODULES = original


def test_the_boot_assertion_is_still_wired_into_bot():
    """Guard the guard, again -- and for a different reason than the vocabulary.

    The check above proves the function works. It says nothing about whether
    anything calls it. Deleting the two lines at the bottom of `bot.py` leaves
    every test in this file green, because they all measure route *shape* and
    the shape does not change when the assertion stops running.

    Matched on the call, not on the import: an import with no call is exactly
    what a careless merge conflict resolution leaves behind.
    """
    with open(os.path.join(ROOT, "bot.py"), "r", encoding="utf-8") as handle:
        source = handle.read()
    assert "_assert_routes_declared(webhook_app)" in source, (
        "bot.py no longer calls assert_routes_declared(). The boot-time half of "
        "default-deny is dead: services/route_auth.py still defines it, and "
        "nothing runs it, so a guarded module can register an undeclared route "
        "and the process will start happily. Restore the call at the end of "
        "bot.py, after every route pack has registered."
    )


if __name__ == "__main__":
    # The suite runner executes this file as a script and fails it for reporting
    # zero checks, so it has to be runnable both ways -- see the note on _STATE.
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
