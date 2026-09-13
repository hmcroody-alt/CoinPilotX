"""One CSRF contract, and it stays one.

Before `services/csrf.py` there were five predicates answering "is this write
CSRF-safe?" -- `bot.verify_csrf`, `bot._business_os_ent_csrf_ok`,
`bot.pulse_ads_verify_write`, `bot._subscription_action_write_allowed` and
`business_os_commerce_routes._csrf_ok` -- plus a sixth spelled inline in
`admin_business_os_reconcile`. They disagreed on four of nine request shapes.

The disagreement that matters for the web rebuild: `verify_csrf` read
`request.form` and nothing else, and it is the verifier behind 52 call sites and
`enforce_admin_form_csrf`, the hook covering every state-changing `/admin` and
`/api/admin` request. A `fetch` client sending `X-CSRF-Token` was refused by all
of them -- and refused as "Security check failed", which reads as a stale tab
rather than as a contract mismatch. The SPA is a `fetch` client.

This file protects two different things, and they fail for different reasons:

* `test_*_is_accepted` / `test_*_is_refused` pin the **contract**. They are the
  spec the rebuilt web client is written against, executable.

* `test_no_module_spells_a_csrf_header_itself` and its siblings pin the
  **singularity**. The contract tests above would stay green if somebody added a
  sixth verifier tomorrow, because the fifth would still behave. Drift is not
  visible from behaviour; it is visible from the source.

Run: python3 -m pytest tests/protection/test_csrf_contract.py
"""

from __future__ import annotations

import ast
import inspect
import os
import re
import sys
import tempfile
import textwrap

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# `bot` resolves DATABASE_URL at import and never re-reads it, so this has to
# happen before the import below and it has to override rather than defer. The
# variable a developer already exported is the one that reaches a database they
# care about; importing bot runs init_db() and creates several hundred tables.
os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(
    prefix="csrf-contract-protection-", suffix=".db"
)[1]
os.environ.setdefault("FLASK_SECRET_KEY", "csrf-contract-protection")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import flask  # noqa: E402

import bot  # noqa: E402
from services import business_os_commerce_routes as commerce  # noqa: E402
from services import csrf  # noqa: E402

TOKEN = "protection-suite-session-token"
STALE = "protection-suite-stale-token"

#: Every verifier that must answer identically. Adding one here without routing
#: it through `services.csrf` will fail `test_every_verifier_agrees`.
VERIFIERS = {
    "bot.verify_csrf": bot.verify_csrf,
    "bot._business_os_ent_csrf_ok": bot._business_os_ent_csrf_ok,
    "bot.pulse_ads_verify_write": bot.pulse_ads_verify_write,
    "bot._subscription_action_write_allowed": bot._subscription_action_write_allowed,
    "services.business_os_commerce_routes._csrf_ok": commerce._csrf_ok,
}

#: The three that honour the bearer exemption, and the reason they may. Stated
#: as data so an accidental fourth is a failure rather than a shrug: these are
#: member-facing write gates for surfaces the native app uses, and the native
#: app has no CSRF token to echo. `verify_csrf` guards admin form posts among
#: other things, and a bearer resolves a *member* identity -- letting it vouch
#: for an admin action crosses a boundary for no gain.
BEARER_EXEMPT = {
    "bot.pulse_ads_verify_write",
    "bot._subscription_action_write_allowed",
    "services.business_os_commerce_routes._csrf_ok",
}


def ask(verifier, *, headers=None, form=None, bearer=False) -> bool:
    """Run one verifier against one request shape inside a real request context."""
    kwargs = {"method": "POST", "headers": dict(headers or {})}
    if form is not None:
        kwargs["data"] = form
    else:
        kwargs["data"] = "{}"
        kwargs["content_type"] = "application/json"
    with bot.app.test_request_context("/csrf-contract-probe", **kwargs):
        flask.session[csrf.CSRF_SESSION_KEY] = TOKEN
        if bearer:
            flask.g.mobile_access_user_id = 8
        return bool(verifier())


def ask_all(**shape) -> dict:
    return {name: ask(fn, **shape) for name, fn in VERIFIERS.items()}


def read(path: str) -> str:
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as handle:
        return handle.read()


# --- the contract -----------------------------------------------------------


#: Any `X-...CSRF...` string appearing in client code, however spelled. The
#: `<meta name="csrf-token">` element does not match, and should not: it is a
#: source the client reads, not a header it sends.
CLIENT_HEADER_RE = re.compile(r"""["']([Xx]-[A-Za-z]*[Cc][Ss][Rr][Ff][A-Za-z-]*)["']""")


def test_the_accepted_spellings_are_the_ones_clients_actually_send():
    """The wire spelling is pinned to the literal, not to whatever the constant says.

    Every other test in this file parameterises on `csrf.CSRF_HEADER`, which is
    right for them -- they ask whether the verifiers agree, and the name is
    incidental to that. But it means renaming the constant renames both sides of
    every assertion, and the suite stays green while every deployed client
    breaks. Mutation M1 of the harness proved exactly that: `CSRF_HEADER` was
    rebound to `X-Pulse-CSRF` and all fourteen other tests passed.

    A rename is not a refactor here. `X-CSRF-Token` is hardcoded in six client
    call sites in this repo, in cached bundles this deploy cannot reach, and in
    a native app that ships on Apple's review cycle rather than ours. So the
    literal is asserted, and the accept-set is checked against what the client
    source actually sends -- which also catches a client inventing a seventh
    spelling, the direction the server-side tests cannot see.
    """
    assert csrf.CSRF_HEADER == "X-CSRF-Token", (
        f"the contract header is {csrf.CSRF_HEADER!r}, not 'X-CSRF-Token'. "
        f"Clients hardcode this string; changing it refuses every write from "
        f"every already-loaded page until they reload, and from the native app "
        f"until Apple approves a build."
    )
    assert "X-CSRFToken" in csrf.CSRF_HEADER_ALIASES, (
        f"the Django-spelling alias is gone from {csrf.CSRF_HEADER_ALIASES!r}. "
        f"It was accepted by the commerce and supplier packs before unification, "
        f"so dropping it is a narrowing against traffic this repo cannot see."
    )

    accepted = {name.lower() for name in (csrf.CSRF_HEADER, *csrf.CSRF_HEADER_ALIASES)}
    sent: dict[str, list[str]] = {}
    for directory, suffix in (("static", ".js"), ("templates", ".html")):
        for base, _dirs, files in os.walk(os.path.join(ROOT, directory)):
            for filename in files:
                if not filename.endswith(suffix):
                    continue
                full = os.path.join(base, filename)
                with open(full, "r", encoding="utf-8", errors="replace") as handle:
                    for spelling in CLIENT_HEADER_RE.findall(handle.read()):
                        sent.setdefault(spelling.lower(), []).append(
                            os.path.relpath(full, ROOT)
                        )

    assert sent, (
        "no client in static/ or templates/ sends a CSRF header at all. Either "
        "the scan is broken or the clients regressed to form-only posts; both "
        "are worth knowing before the SPA is built on top of this contract."
    )
    unaccepted = {k: sorted(set(v)) for k, v in sent.items() if k not in accepted}
    assert not unaccepted, (
        f"these client files send a CSRF header the server does not accept: "
        f"{unaccepted}. Accepted spellings are {sorted(accepted)}. This is the "
        f"drift that produced six verifiers in the first place -- a client picks "
        f"a spelling, one route pack happens to accept it, and the contract "
        f"becomes 'whatever that pack does'."
    )


def test_the_documented_header_is_accepted_everywhere():
    """`X-CSRF-Token` is the one spelling clients are told to send.

    This is the test the web rebuild depends on. Before unification it failed
    for three of the five verifiers, silently, in production.
    """
    answers = ask_all(headers={csrf.CSRF_HEADER: TOKEN})
    refused = sorted(name for name, ok in answers.items() if not ok)
    assert not refused, (
        f"{csrf.CSRF_HEADER} is the documented contract but these verifiers "
        f"refuse it: {refused}. A fetch client talking to those routes gets "
        f"'Security check failed' with no indication that the header name is "
        f"the problem."
    )


def test_the_form_field_is_accepted_everywhere():
    """Server-rendered forms still work.

    15 templates emit `csrf_token` and `inject_admin_form_csrf` writes it into
    every admin POST form by construction. Unifying on a header must not have
    quietly moved the admin console onto a channel it does not use.
    """
    answers = ask_all(form={csrf.CSRF_FORM_FIELD: TOKEN})
    refused = sorted(name for name, ok in answers.items() if not ok)
    assert not refused, (
        f"the {csrf.CSRF_FORM_FIELD} form field is refused by: {refused}. Every "
        f"server-rendered form post to those routes is broken."
    )


def test_a_legacy_header_alias_is_still_accepted():
    """The deprecated spellings are honoured until there is evidence to drop them.

    `X-CSRFToken` was accepted by exactly one of the five before unification, so
    whether a client worked depended on which route pack it happened to reach.
    Dropping it would have been a narrowing that fails closed on traffic this
    repo cannot see; it is kept, named, and deprecated in one place instead.
    """
    assert csrf.CSRF_HEADER_ALIASES, (
        "the alias list is empty. If it was retired deliberately, delete this "
        "test and say so in the commit -- but check first that no client sends "
        "the old spelling, because a narrowed CSRF gate refuses writes without "
        "failing anything."
    )
    for alias in csrf.CSRF_HEADER_ALIASES:
        answers = ask_all(headers={alias: TOKEN})
        refused = sorted(name for name, ok in answers.items() if not ok)
        assert not refused, f"legacy alias {alias} refused by: {refused}"


def test_a_stale_token_on_one_channel_does_not_veto_a_valid_one():
    """Any channel carrying the current token is enough.

    This is not a nicety. The first draft of `services/csrf.py` made the header
    win outright, and an exhaustive sweep of the five verifiers against their
    pre-unification source found that it *narrowed* the gate in 12 request
    shapes: a valid `csrf_token` form field alongside a stale `X-CSRF-Token`
    header passed before and would have been refused after. A page renders its
    form field and its meta tag together, but JS that caches the header value
    and a form re-submitted after a session rotation do not go stale together.

    It concedes nothing: an attacker who cannot produce the token on one channel
    cannot produce it on two.
    """
    answers = ask_all(headers={csrf.CSRF_HEADER: STALE},
                      form={csrf.CSRF_FORM_FIELD: TOKEN})
    refused = sorted(name for name, ok in answers.items() if not ok)
    assert not refused, (
        f"a valid form field was vetoed by a stale header in: {refused}. This "
        f"is the exact narrowing the unification was checked against."
    )

    answers = ask_all(headers={csrf.CSRF_HEADER: TOKEN},
                      form={csrf.CSRF_FORM_FIELD: STALE})
    refused = sorted(name for name, ok in answers.items() if not ok)
    assert not refused, f"a valid header was vetoed by a stale form field in: {refused}"


def test_a_wrong_token_is_refused_on_every_channel():
    """The part that is actually security. Everything above is compatibility."""
    channels = [
        ("header", {"headers": {csrf.CSRF_HEADER: STALE}}),
        ("form", {"form": {csrf.CSRF_FORM_FIELD: STALE}}),
    ]
    for alias in csrf.CSRF_HEADER_ALIASES:
        channels.append((f"alias {alias}", {"headers": {alias: STALE}}))
    for label, shape in channels:
        answers = ask_all(**shape)
        accepted = sorted(name for name, ok in answers.items() if ok)
        assert not accepted, (
            f"a token that does not match the session was ACCEPTED on the "
            f"{label} channel by: {accepted}"
        )


def test_sending_nothing_is_refused():
    """Default-deny. A write with no token on any channel does not pass."""
    answers = ask_all()
    accepted = sorted(name for name, ok in answers.items() if ok)
    assert not accepted, (
        f"a write carrying no CSRF token at all was accepted by: {accepted}"
    )


def test_a_session_with_no_token_refuses_rather_than_matches_empty():
    """An absent session token must not make every request valid.

    The failure being excluded is the classic one: `expected == supplied` where
    both are empty. Two of the pre-unification verifiers guarded this by testing
    the *submitted* value for truthiness, which is the right answer reached from
    the wrong side -- it breaks the moment someone accepts a channel that can
    supply a default.
    """
    with bot.app.test_request_context(
        "/csrf-contract-probe", method="POST",
        headers={csrf.CSRF_HEADER: ""}, data="{}",
        content_type="application/json",
    ):
        flask.session.pop(csrf.CSRF_SESSION_KEY, None)
        for name, fn in VERIFIERS.items():
            assert not fn(), (
                f"{name} accepted a request when the session carries no CSRF "
                f"token at all. An empty-vs-empty comparison authorises every "
                f"write made before a token was ever minted."
            )


def test_every_verifier_agrees_on_every_shape():
    """The unification itself, measured rather than asserted.

    A contract test per channel would stay green if one verifier drifted on a
    combination nobody thought to write down, so this sweeps the product.
    """
    shapes = []
    for form in (None, {csrf.CSRF_FORM_FIELD: TOKEN}, {csrf.CSRF_FORM_FIELD: STALE}):
        for header in [None, (csrf.CSRF_HEADER, TOKEN), (csrf.CSRF_HEADER, STALE)] + [
            (alias, value) for alias in csrf.CSRF_HEADER_ALIASES for value in (TOKEN, STALE)
        ]:
            shapes.append((form, header))

    disagreements = []
    for form, header in shapes:
        answers = ask_all(headers=dict([header]) if header else None, form=form)
        if len(set(answers.values())) > 1:
            disagreements.append((form, header, answers))

    assert not disagreements, (
        "these request shapes get different answers from different CSRF "
        "verifiers, which is the condition this module was written to end:\n"
        + "\n".join(
            f"  form={f} header={h} -> {a}" for f, h, a in disagreements
        )
    )


def test_the_bearer_exemption_is_only_where_it_was_declared():
    """An exemption is a decision, so an accidental one has to fail.

    It used to be an undocumented difference between functions with similar
    names -- and in `pulse_ads_verify_write` it was an exemption that never
    fired, because it tested `g.mobile_access_user_id`, a flag
    `bot.account_user_id()` only sets when it reaches its bearer branch, which
    it skips whenever a session cookie is present. The native app sends both.
    """
    for name, fn in VERIFIERS.items():
        exempt = ask(fn, bearer=True)
        expected = name in BEARER_EXEMPT
        if expected:
            assert exempt, (
                f"{name} is declared bearer-exempt but refused a request "
                f"carrying a verified bearer and no CSRF token. Every native "
                f"app write through this gate is refused while every read "
                f"succeeds -- and it arrives as 403, so the client's 401 "
                f"recovery never runs."
            )
        else:
            assert not exempt, (
                f"{name} accepted a bearer-only request but is not in "
                f"BEARER_EXEMPT. A bearer resolves a member identity; this gate "
                f"guards surfaces whose authority is not the member session."
            )


def test_the_default_is_no_bearer_exemption():
    """`verify(allow_bearer=...)` defaults to off.

    So that a CSRF check added somewhere new next year cannot inherit an
    exemption by saying nothing.
    """
    default = inspect.signature(csrf.verify).parameters["allow_bearer"].default
    assert default is False, (
        f"services.csrf.verify defaults allow_bearer={default!r}. A new caller "
        f"writing csrf.verify() would silently accept any request carrying a "
        f"bearer."
    )


# --- the singularity --------------------------------------------------------


def test_the_comparison_is_constant_time():
    """One comparison, and it is `hmac.compare_digest`.

    Two of the five predecessors used `==`. The practical exposure on a 256-bit
    random token is small, but "small" is an argument that has to be re-made by
    every reader, and the constant-time call costs nothing.
    """
    source = inspect.getsource(csrf.token_matches)
    assert "compare_digest" in source, (
        "services.csrf.token_matches no longer uses hmac.compare_digest"
    )
    # Docstring and comments stripped first, via the parser rather than by
    # string surgery. The prose in that function explains why `==` is wrong, so
    # scanning the raw source matches the explanation and fails a correct
    # implementation -- which it did, on the first run of this file. A
    # `source.replace(fn.__doc__, "")` was tried next and does not work either:
    # `__doc__` is dedented and the source is not.
    tree = ast.parse(textwrap.dedent(source))
    function = tree.body[0]
    if (function.body and isinstance(function.body[0], ast.Expr)
            and isinstance(function.body[0].value, ast.Constant)
            and isinstance(function.body[0].value.value, str)):
        function.body = function.body[1:]
    body = ast.unparse(function)
    assert "==" not in body.replace("!=", ""), (
        "services.csrf.token_matches compares with == somewhere. Every "
        "comparison of a submitted token against the session token must be "
        "constant-time."
    )


def test_every_verifier_delegates_instead_of_reimplementing():
    """Source-level, because behaviour cannot see a copy that happens to agree.

    A reimplementation that is correct today is the starting state of every
    divergence this file documents. All five were correct-ish on the day they
    were written too.
    """
    # No `\b` before `csrf`: the call sites import the module under a private
    # alias (`_csrf`, `_shared_csrf`) to avoid shadowing local names, and `_` is
    # a word character, so a word boundary never matches there. The first run of
    # this file failed all five for that reason.
    offenders = []
    for name, fn in VERIFIERS.items():
        source = inspect.getsource(fn)
        if not re.search(r"[\w.]*csrf\.verify\(", source):
            offenders.append(name)
    assert not offenders, (
        f"these verifiers do not call services.csrf.verify(): {offenders}. "
        f"Whatever they do instead is a second implementation, and a second "
        f"implementation is how the five-way disagreement happened."
    )


def test_no_module_spells_a_csrf_header_itself():
    """The header names live in one file.

    This is the test that would have caught the original drift. `X-CSRFToken`
    appeared in exactly one of the five verifiers; nothing anywhere said whether
    that was deliberate, and no behavioural test could tell, because each
    verifier was individually self-consistent.

    Reading a header name to *report* on it is fine -- the supplier pack's
    five-bit diagnostic does that. Only a comparison is forbidden, so the check
    is for the name appearing next to the session token, not for the name.
    """
    names = [csrf.CSRF_HEADER, *csrf.CSRF_HEADER_ALIASES]
    pattern = re.compile(
        r"session(?:\.get\(|\[)['\"]" + re.escape(csrf.CSRF_SESSION_KEY) + r"['\"]"
    )
    offenders = []
    for relative in ("bot.py", "services/business_os_commerce_routes.py",
                     "services/business_os_supplier_routes.py",
                     "services/business_os_dropshipping_routes.py"):
        source = read(relative)
        for number, line in enumerate(source.splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if pattern.search(line) and any(n in line for n in names):
                offenders.append(f"{relative}:{number}")
    assert not offenders, (
        f"these lines compare a CSRF header against the session token outside "
        f"services/csrf.py: {offenders}. Route the check through "
        f"services.csrf.verify() instead -- an accept-set in two places is an "
        f"accept-set that will differ."
    )


def test_the_session_key_is_not_reused_as_an_identifier():
    """The CSRF token is not a visitor id, and code that treats it as one is noted.

    `bot.py` uses `session['csrf_token']` as a fallback identity for rate-limit
    bucketing in two places. That is not a CSRF bug, but it couples an
    authorisation secret to a value that gets logged and grouped on, which means
    rotating the token silently resets a limiter and a limiter key leaks part of
    a token's lifecycle. Pinned at its current count so it cannot grow while
    nobody is looking; the fix is a separate `visitor_session_id`.
    """
    source = read("bot.py")
    uses = [
        number
        for number, line in enumerate(source.splitlines(), 1)
        if "session.get(\"csrf_token\")" in line and "visitor_session_id" in line
        or ("session.get(\"csrf_token\") or " in line and "request.cookies" in line)
    ]
    assert len(uses) <= 2, (
        f"the CSRF session token is now used as an identifier in {len(uses)} "
        f"places (was 2), at lines {uses}. It is an authorisation secret, not a "
        f"visitor id: grouping on it means a token rotation resets whatever is "
        f"being counted, and it puts part of the token's lifecycle into logs."
    )


def test_the_admin_form_hook_is_wired_and_refuses_a_tokenless_post():
    """The hook is the only CSRF coverage 42 admin routes have.

    An audit found 42 of 79 state-changing admin endpoints never called
    `verify_csrf()` themselves; `enforce_admin_form_csrf` is what protects them,
    and it protects them by existing as a `before_request` hook rather than by
    anything visible at those 42 call sites. Deleting the decorator leaves every
    other test in this file green, which is why registration is checked
    separately from behaviour.

    Driven through a request context rather than `test_client`. Going through
    the client was tried first and it does not reach this hook: an earlier
    `before_request`, `enforce_admin_first_password_change`, resolves the admin
    and clears `session['admin_user_id']` for a session that did not come from a
    real login -- so the hook sees no admin cookie, correctly skips, and the
    request 401s further in. That is right behaviour producing a useless test.
    Full-stack admin CSRF, including the login path, is covered by
    `tests/admin_auth/test_pre_auth_gateway.py`.
    """
    hooks = [
        f.__name__
        for funcs in bot.app.before_request_funcs.values()
        for f in funcs
    ]
    assert "enforce_admin_form_csrf" in hooks, (
        "enforce_admin_form_csrf is not registered as a before_request hook. "
        "The 42 admin write endpoints that never call verify_csrf() themselves "
        "are now unprotected, and nothing else in this file can see it."
    )

    def hook(headers=None):
        with bot.app.test_request_context(
            "/admin/business-os/reconcile", method="POST",
            data={"provider": "stripe"}, headers=dict(headers or {}),
        ):
            flask.session["admin_user_id"] = 8
            flask.session[csrf.CSRF_SESSION_KEY] = TOKEN
            return bot.enforce_admin_form_csrf()

    refusal = hook()
    assert refusal is not None, (
        "a form post to an admin path carrying an admin cookie and no CSRF "
        "token was waved through by enforce_admin_form_csrf."
    )

    # The same request with the token on the header the SPA will use. Before
    # unification this was refused, because the hook called a form-only
    # verifier -- the exact failure this work exists to remove.
    assert hook({csrf.CSRF_HEADER: TOKEN}) is None, (
        f"enforce_admin_form_csrf still refuses a valid {csrf.CSRF_HEADER} "
        f"header. Every fetch-based admin write is broken."
    )
    assert hook({csrf.CSRF_HEADER: STALE}) is not None, (
        "enforce_admin_form_csrf accepted a header carrying a token that does "
        "not match the session."
    )


if __name__ == "__main__":
    # The suite runner executes this file as a script and fails it for reporting
    # zero checks, so it has to be runnable both ways. Zero-argument tests, no
    # fixtures -- see the note in tests/protection/test_route_auth.py.
    import pathlib as _pathlib

    sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
