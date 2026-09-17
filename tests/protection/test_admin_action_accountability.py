"""Every admin action must be authenticated, attributable, and forgeable-proof.

The Command Center presents ~110 state-changing admin endpoints as a single
trustworthy control surface. Three separate audits of that surface found gaps
that the dashboard itself could never reveal, because a missing audit row and a
missing CSRF check both look exactly like a working button.

What this suite locks:

1. AUTHENTICATION - no mutating /admin or /api/admin endpoint may run without
   establishing an admin identity. (This was already true when measured; the
   test exists so it stays true.)

2. ATTRIBUTION - the highest-consequence actions must write to
   `admin_audit_logs`, which is the table /admin/audit-logs reads and the table
   `services/backend_management_registry.py` declares as canonical. Four real
   gaps were found and fixed:

     * `admin_super_users_page` granted and revoked super-user - the highest
       privilege in the system - writing only to `log_product_event`, an
       analytics stream that no reviewer reads.
     * `api_admin_pulse_music_approval` and `api_admin_pulse_music_remove`
       made commercial-rights decisions with no permission scope and no record,
       though the registry declares `media.music` as `pulse.moderate` gated and
       audited to `pulse_music_events`.
     * `api_admin_pulse_music_import_metadata` inserted rows with
       `safety_status='approved'` - a licensing assertion - unscoped and
       unlogged.

   Two more actions authenticate with the shared admin password and therefore
   have no attributable identity. They must say so in the audit row rather than
   logging actor 0 silently: `admin_repair_user_pro` and `admin_brevo_resync`
   (which transmits every lead and user email address to a third party).

3. FORGERY RESISTANCE - CSRF verification used to be opt-in per handler. Of 79
   form-driven admin POST endpoints, 42 never called `verify_csrf()`, and 39 of
   the rendered forms never emitted a token field at all - so adding the calls
   alone would have broken those pages. Both halves are now structural:
   `inject_admin_form_csrf` gives every admin POST form a token by construction,
   and `enforce_admin_form_csrf` rejects form posts that arrive without one.

   The enforcement scope is load-bearing and is asserted here, because widening
   it would break non-browser callers and narrowing it would reopen the hole.

These tests parse bot.py rather than importing it: importing boots a 111k-line
Flask monolith with ~1,538 routes and live integrations.

Note on ast.get_source_segment: it re-splits the entire source on every call,
which is unusable on a file this size. Slice a precomputed line list instead.
"""

import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT = ROOT / "bot.py"
SOURCE = BOT.read_text(encoding="utf-8")
LINES = SOURCE.splitlines()
TREE = ast.parse(SOURCE)

STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}

# Any expression that establishes an admin identity for the request, directly or
# via a helper that is itself verified below. `music_mutation_route` is the shared
# body of the five owner music lifecycle routes; naming it here rather than
# inlining a resolver into each route is only safe because
# `test_the_music_authority_helpers_really_establish_an_identity` pins that it
# authenticates before it does anything else.
AUTH = re.compile(
    r"admin_login_required|admin_current_user|require_admin_api|require_admin_page"
    r"|require_admin_password|require_owner|_admin_or_redirect"
    r"|music_authority_actor\(|music_mutation_route\("
)
# Any expression that writes an accountability record, directly or via a helper
# that is itself verified below.
AUDIT = re.compile(
    r"log_admin_audit\(|admin_user_action\(|audit_log\(|record_admin_action\("
    r"|log_admin_action\(|pulse_music_event\(|INSERT INTO \w*audit|admin_audit_logs",
    re.I,
)


def _function(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in bot.py")


def _source_of(name):
    node = _function(name)
    return "\n".join(LINES[node.lineno - 1 : node.end_lineno])


def _segment(node):
    return "\n".join(LINES[node.lineno - 1 : node.end_lineno])


def _routes_of(node):
    """(path, methods) for every Flask route decorator on a function."""
    found = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        func = decorator.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in {"route", "get", "post", "put", "patch", "delete"}:
            continue
        if not decorator.args or not isinstance(decorator.args[0], ast.Constant):
            continue
        path = decorator.args[0].value
        if func.attr != "route":
            found.append((path, {func.attr.upper()}))
            continue
        methods = {"GET"}
        for keyword in decorator.keywords:
            if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
                methods = {
                    element.value
                    for element in keyword.value.elts
                    if isinstance(element, ast.Constant)
                }
        found.append((path, methods))
    return found


def _mutating_admin_endpoints():
    """Every admin route that can change state, with its handler source."""
    endpoints = []
    for node in ast.walk(TREE):
        if not isinstance(node, ast.FunctionDef):
            continue
        routes = _routes_of(node)
        if not routes:
            continue
        body = None
        for path, methods in routes:
            if not (methods & STATE_CHANGING):
                continue
            if not (path.startswith("/admin") or path.startswith("/api/admin")):
                continue
            if body is None:
                body = "\n".join(LINES[node.lineno - 1 : node.end_lineno])
            endpoints.append((path, node.name, body))
    return endpoints


# --- 1. Authentication -------------------------------------------------------

def test_no_mutating_admin_endpoint_is_unauthenticated():
    endpoints = _mutating_admin_endpoints()
    assert len(endpoints) > 80, (
        f"Only {len(endpoints)} mutating admin endpoints discovered - the route "
        "decorator parser has probably stopped matching and this suite is no "
        "longer measuring anything."
    )
    unauthenticated = sorted(
        {f"{path} -> {name}" for path, name, body in endpoints if not AUTH.search(body)}
    )
    assert not unauthenticated, (
        "Mutating admin endpoints with no authentication: " + ", ".join(unauthenticated)
    )


# --- 2. Attribution ----------------------------------------------------------

# Actions where a missing audit row is not a nit: privilege changes, rights
# decisions, and bulk personal-data transfers.
ACCOUNTABLE_HANDLERS = (
    "admin_super_users_page",
    "api_admin_pulse_music_approval",
    "api_admin_pulse_music_remove",
    "api_admin_pulse_music_import_metadata",
    "admin_repair_user_pro",
    "admin_brevo_resync",
)


def test_high_consequence_actions_write_to_the_canonical_audit_table():
    for name in ACCOUNTABLE_HANDLERS:
        body = _source_of(name)
        assert "log_admin_audit(" in body, (
            f"{name}() changes privilege, rights, or personal data without writing "
            "to admin_audit_logs. log_product_event() and pulse_music_event() are "
            "analytics streams; neither is the table /admin/audit-logs reads."
        )


def test_music_moderation_enforces_the_permission_the_registry_declares():
    """`media.music` declares `pulse.moderate`; the handlers must actually check it."""
    for name in (
        "api_admin_pulse_music_approval",
        "api_admin_pulse_music_remove",
        "api_admin_pulse_music_import_metadata",
    ):
        body = _source_of(name)
        assert 'require_admin_api("pulse.moderate")' in body, (
            f"{name}() must enforce the pulse.moderate scope the registry declares "
            "for media.music, not merely check that some admin session exists."
        )


def test_the_music_authority_helpers_really_establish_an_identity():
    """The two names AUTH trusts by indirection have to earn it.

    `music_authority_actor` is the only resolver the owner music routes have, and
    `music_mutation_route` is the shared body five of them delegate to. Adding
    both to AUTH means a route passes the authentication check without any admin
    primitive appearing in its own source, so the trust has to be re-established
    here or the entry is a hole rather than an indirection.

    The ordering assertion is the part that matters. A resolver called somewhere
    inside a long function is not the same as one called before the function
    touches the database: the first thing `music_mutation_route` does must be to
    find out who is asking, and the second must be to stop if the answer is
    nobody.
    """
    resolver = _source_of("music_authority_actor")
    assert "admin_current_user()" in resolver, (
        "music_authority_actor() must resolve the web-admin session leg through "
        "admin_current_user(), the same primitive every other admin route uses."
    )
    assert "require_permission(" in resolver, (
        "Resolving an identity is not authorization. music_authority_actor() must "
        "also check the named permission, or every music.* scope collapses into "
        "'is an admin'."
    )
    assert "account_user_id()" in resolver and "admin_user_by_account_user_id" in resolver, (
        "The native leg must resolve a proven account id through the stored "
        "admin_users.account_user_id link. Anything the request *sends* choosing "
        "the identity would let a caller nominate itself."
    )

    node = _function("music_mutation_route")
    body = [
        statement for statement in node.body
        if not (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
    ]
    first, second = body[0], body[1]
    assert isinstance(first, ast.Assign) and "music_authority_actor(" in _segment(first), (
        "music_mutation_route() must call music_authority_actor() as its first "
        "statement. A resolver reached only after some other work has already run "
        "is not what AUTH is being told it is."
    )
    assert isinstance(second, ast.If) and "return" in _segment(second), (
        "The denial from music_authority_actor() must be returned immediately. "
        "Resolving an actor and then continuing past the refusal authenticates "
        "nothing."
    )


def test_shared_password_actions_admit_they_cannot_attribute():
    """An audit row that looks like it has an actor but does not is worse than none.

    Both routes below authenticate with ADMIN_ANALYTICS_PASSWORD, which carries no
    identity. Recording actor 0 silently makes an unattributable action
    indistinguishable from one performed by user 0.
    """
    for name in ("admin_repair_user_pro", "admin_brevo_resync"):
        body = _source_of(name)
        assert "shared_admin_password_unattributed" in body, (
            f"{name}() must record that the actor is unattributable when the caller "
            "authenticated with the shared admin password."
        )


def test_super_user_change_is_not_recorded_only_as_analytics():
    body = _source_of("admin_super_users_page")
    assert "log_product_event" in body and "log_admin_audit(" in body
    audit_index = body.index("log_admin_audit(")
    window = body[audit_index : audit_index + 600]
    assert "previous_is_super_user" in window, (
        "The super-user audit row must record the prior state, or a reviewer "
        "cannot tell a grant from a no-op replay."
    )


# --- 3. Forgery resistance ---------------------------------------------------

def test_admin_form_csrf_is_enforced_by_default_not_per_handler():
    hook = _source_of("enforce_admin_form_csrf")
    # It must reject rather than merely observe.
    assert "verify_csrf()" in hook
    assert "400" in hook
    # Scope, in both directions. Each of these lines is load-bearing.
    assert '"POST", "PUT", "PATCH", "DELETE"' in hook, (
        "Enforcement must cover every state-changing method, not POST alone."
    )
    assert 'path.startswith("/admin")' in hook and 'path.startswith("/api/admin")' in hook
    assert "application/x-www-form-urlencoded" in hook and "multipart/form-data" in hook, (
        "Enforcement must stay scoped to form-encoded bodies. Widening it to JSON "
        "would break the mobile app and every bearer-token API caller; JSON posts "
        "are already protected by the CORS preflight a hostile page cannot satisfy."
    )
    assert 'session.get("admin_user_id")' in hook, (
        "Enforcement must apply only when the browser supplies an admin session "
        "cookie. Shared-password callers send no cookie, carry no ambient "
        "authority, and cannot be CSRF'd - gating them would break scripts."
    )


def test_every_admin_post_form_receives_a_token_by_construction():
    """39 of 79 rendered admin forms had no token field; enforcement alone would break them."""
    hook = _source_of("inject_admin_form_csrf")
    assert "get_csrf_token()" in hook
    assert "csrf_token" in hook
    assert 'path.startswith("/admin")' in hook
    assert "direct_passthrough" in hook, (
        "A streamed or file-download response must not be buffered by the injector."
    )
    assert "logging.exception" in hook, (
        "Injection failure must be visible. A silent failure here degrades to the "
        "old opt-in behaviour while every page still renders normally."
    )


def test_csrf_exemptions_stay_minimal():
    for node in TREE.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "CSRF_EXEMPT_ADMIN_PATHS"
            for t in node.targets
        ):
            value = node.value
            # Declared as `frozenset({...})`; unwrap the call to reach the literal.
            if isinstance(value, ast.Call) and value.args:
                value = value.args[0]
            exempt = set(ast.literal_eval(value))
            break
    else:
        raise AssertionError("CSRF_EXEMPT_ADMIN_PATHS not found at module scope")
    assert exempt <= {"/admin/login", "/admin/logout"}, (
        f"Unexpected CSRF exemptions: {sorted(exempt - {'/admin/login', '/admin/logout'})}. "
        "Session establishment and teardown are the only routes that legitimately "
        "cannot present a session-bound token."
    )


def test_admin_javascript_does_not_post_form_encoded_bodies():
    """The injector only reaches server-rendered forms.

    Admin JavaScript posts JSON, so the form-scoped enforcement hook cannot
    break it. A `FormData` or urlencoded fetch added later would be submitted
    without the hidden field and would be rejected at runtime - fail here
    instead, where the cause is obvious.
    """
    offenders = []
    for path in [BOT] + sorted((ROOT / "static").rglob("*.js")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for match in re.finditer(
            r"fetch\(\s*[`'\"]/(?:api/)?admin[^`'\"]*[`'\"](\{.{0,400}?\})?", text, re.S
        ):
            snippet = match.group(0)
            if "FormData" in snippet or "x-www-form-urlencoded" in snippet:
                offenders.append(f"{path.name}: {snippet[:120]}")
    assert not offenders, (
        "Admin fetch() calls posting form-encoded bodies bypass the injected "
        "hidden field and will be rejected by enforce_admin_form_csrf: " + "; ".join(offenders)
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
