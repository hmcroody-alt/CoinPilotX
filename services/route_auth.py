"""Make "is this route protected?" a question with an answer.

`bot.py` registers ~2,160 rules and **not one of them is gated by a decorator**.
Every view authenticates itself by calling a helper inside its own body —
`api_account_user()` in 566 of them, `require_owner_api()` in 78,
`require_admin_page()` in 135, and so on across a vocabulary that grew to more
than twenty names. Three properties follow, and the third is the one that matters:

1. There is no static way to prove a route is protected.
2. No linter can require it.
3. **Adding a route with no authentication is a silent, test-passing change.**

This module fixes (3), which is the only one that causes a breach. It does it in
two halves that must not be confused with each other.

**The declaration is the security mechanism.** `@auth_required`,
`@admin_required` and `@public_route(reason=...)` record what a route is *meant*
to be, on the function itself. A new route that declares nothing is a failure,
and `audit_app()` reports it. That is default-deny: not "we looked and found no
problem" but "you did not say, so we refuse".

**The detector is bookkeeping, not a security boundary.** `classify_view()`
greps a view's source for known gate helpers. It cannot prove authentication —
it proves a *name appears in the function body*, which a sufficiently strange
route could satisfy while remaining wide open. It exists for exactly one reason:
2,160 routes predate this module, hand-reviewing all of them is not a change
anyone could review, and without some classification the only options were to
declare every legacy route `public` (a lie that would bake in) or to block the
rebuild on a 2,160-line audit. So legacy routes are classified by evidence and
frozen into a baseline, and the baseline's job is to detect *movement*: a route
that had a gate and stops having one is a regression this catches.

Nothing here should ever be read as "the detector says this route is safe."
What it says is "this route still looks the way it looked when a human last
looked at it."

Why a helper vocabulary rather than call-graph analysis: the gates are called in
dozens of shapes (`user = api_account_user()`, `admin, denied =
require_owner_api()`, inside nested closures, behind early returns), and a
resolver that tried to be clever would be a second thing to trust. A name list
is legible, and it is checked against the tree by `undefined_gate_helpers()`,
which exists because a rename that silently emptied this vocabulary would turn
every check downstream green.

**Two tiers, because the helpers are two different things.** `REFUSAL_GATES`
hand the caller a denial to propagate; a view that returns one cannot serve an
anonymous request. `IDENTITY_HELPERS` answer "who is this?" and return ``None``
for nobody, which decides nothing on its own. The split is not cosmetic: an
earlier version of this file had `api_account_user` — 800-odd routes, and a body
that is nothing but `require_account()` with a `None` fallthrough — in the strong
tier, which meant the strong tier was certifying more than a thousand routes on
the strength of a call that cannot refuse anybody. A route whose only evidence is
an identity helper is classified by whether its body can refuse *at all*, and
when it cannot, it stays unknown and lands on a review list.
"""

from __future__ import annotations

import functools
import inspect
import os
import re
import sys
from typing import Callable, Iterable

#: A route that requires a signed-in member (`session['account_user_id']` or a
#: verified mobile bearer).
AUTH_USER = "user"

#: A route that requires administrative standing. Two different mechanisms grant
#: it, and anyone modelling authorization for the web client needs both:
#:
#: 1. The **admin identity system** — `session['admin_user_id']`, a separate
#:    login against `admin_users`. `require_admin_page()` and friends.
#: 2. **Elevation of an ordinary member session** — `require_super_user_page()`
#:    and `require_super_user_api()` resolve the *member* session and then check
#:    `user_is_super_user()`, which is true when the account row carries
#:    `is_super_user` or when the account's email equals the configured owner
#:    email.
#:
#: So a logged-in member *can* hold admin capability, via a column on their own
#: row. An earlier version of this comment asserted the opposite — that the two
#: identity systems were disjoint with no path between them — which is the kind
#: of tidy claim that gets copied into a new client's authorization model and
#: then quietly contradicted by `is_super_user`. Both mechanisms classify here as
#: AUTH_ADMIN because what this value means is "an administrative surface", not
#: "reached through the admin login".
AUTH_ADMIN = "admin"

#: Deliberately reachable without credentials: marketing pages, health checks,
#: provider webhooks (which authenticate by signature, not session), the login
#: routes themselves.
AUTH_PUBLIC = "public"

#: Nobody has said, and no known gate was found. The only value that is a defect.
AUTH_UNKNOWN = "unknown"

#: Attribute stamped on a view function by the decorators below.
DECLARATION_ATTR = "__pulse_route_auth__"

#: Helpers that **hand the caller a denial**. Calling one and returning what it
#: gives back is a refusal: `require_owner_api()` yields `(None, <403>)`,
#: `api_pro_required(user)` yields a `(<401 body>, 401)` directly,
#: `dashboard_account_shell()` yields a `redirect` to the login page. A view that
#: propagates one of these cannot serve an anonymous caller.
#:
#: This is the strong tier. It is not the same thing as "a function whose name
#: begins with require_" — see IDENTITY_HELPERS for two that do and don't belong
#: here.
#:
#: Absent on purpose: `verify_csrf`, `_csrf_ok`, `_business_os_ent_csrf_ok`.
#: CSRF is not authentication. A CSRF token proves the request came from our own
#: page, not that anyone is signed in, and treating it as a gate would classify
#: an open endpoint as protected.
#:
#: Also absent: `log_admin_audit` (records a decision, does not make one) and the
#: `_business_os_*_enabled` feature flags (decide whether a surface exists, not
#: who may use it). A flag-gated route with no auth gate is still an open route.
#:
#: And absent after review: `render_account_page`. An earlier version of this
#: table listed it as a member gate, which was simply wrong — it renders
#: `account.html` with `current_user` set from `account_user_id()`, and that
#: value being `None` does not stop the render. It gates nothing. Listing it here
#: would have certified every unauthenticated account page as protected, which is
#: the precise failure this module is supposed to make impossible.
REFUSAL_GATES: dict[str, str] = {
    # --- administrative standing -------------------------------------------
    "require_admin_api": AUTH_ADMIN,
    "require_admin_page": AUTH_ADMIN,
    "require_owner_api": AUTH_ADMIN,
    "require_owner_admin_page": AUTH_ADMIN,
    # These two refuse with `redirect(...)` / `restricted_owner_message_response()`
    # rather than a status literal, which is why a scan keyed on 401/403/423
    # alone does not find them. They are gates all the same.
    "require_owner_account_page": AUTH_ADMIN,
    "require_super_user_page": AUTH_ADMIN,
    "require_super_user_api": AUTH_ADMIN,
    # Resolves the admin identity, then rejects any role outside the union of
    # `ADMIN_ROLES`, `READONLY_ADMIN_ROLES` and {owner, super_admin, admin} —
    # returning a redirect to the admin login for anonymous callers and a bare
    # 403 for signed-in admins without the role.
    "_verification_admin_or_redirect": AUTH_ADMIN,
    # --- member identity ---------------------------------------------------
    "pulse_ads_api_user_required": AUTH_USER,
    # Inverted shape: returns the *refusal* (or None when the caller may
    # proceed), so call sites read `denied = api_pro_required(user)`. Still a
    # refusal the caller propagates.
    "api_pro_required": AUTH_USER,
    # Refuses by returning `redirect(url_for("login_page", ...))` before it
    # renders anything.
    "dashboard_account_shell": AUTH_USER,
    # Module-private, and defined ten times over — once in each blueprint
    # (`presence_routes`, `market_pulse_routes`, `pulse_settings_routes`,
    # the four marketplace modules, both undx run modules,
    # `business_os_commerce_routes`). All ten are the same shape: resolve the
    # user, and *return a 401 response* when there is none. That returned
    # denial is what makes it a gate rather than a lookup, and it is why the
    # name is trusted here even though it resolves to ten different functions.
    "_require_user": AUTH_USER,
    # The Private Office `(user, refusal)` family. Same contract as
    # `_require_user`, one per surface because each also applies that surface's
    # tier gate and, for most of them, the second lock: `_entry` in the eight
    # shield/records/documents/facts modules, plus the operations, security and
    # concierge variants.
    #
    # These were not guessed. See `_GATE_SHAPE_SCAN` on how the set was found.
    "_entry": AUTH_USER,
    "_operations_entry": AUTH_USER,
    "_security_entry": AUTH_USER,
    "_operator_entry": AUTH_USER,
    # --- the same shape, defined in bot.py --------------------------------
    #
    # The first pass at the scan below covered `services/` and
    # `pulse_communications_v2/` and omitted `bot.py`, then recorded its result
    # as "the complete set". It was not: bot.py holds most of the views and
    # these gates, and missing them left their routes classified `unknown` while
    # the comment claimed the vocabulary was exhaustive — the worst of both, an
    # inaccurate baseline wearing a guarantee. Found by re-running the scan over
    # the whole first-party tree.
    "_crypto_api_user_conn": AUTH_USER,
    "_messenger_media_user": AUTH_USER,
    "_subscription_action_user": AUTH_USER,
}

#: How the `(subject, refusal)` family above was collected, so it can be redone
#: rather than trusted: an AST pass over every first-party ``.py`` file, looking
#: for a function with a ``return`` of a tuple that contains a literal ``None``
#: and mentions 401/403/423.
#:
#: Excluded from that walk: ``tests/`` and ``scripts/`` (their helpers of the
#: same shape are fixtures, not gates) and ``.claude/worktrees/`` (checkouts of
#: this same repo — including them made every bot.py name appear three times and
#: made the counts meaningless).
#:
#: The scan is a *finding aid, not the definition*. It keys on status literals,
#: so it misses page gates that refuse with `redirect(...)`; both
#: `require_super_user_page` and `require_owner_account_page` are real gates it
#: does not return. Every name above was confirmed by reading its definition.
#: Treat a new name from the scan as "read this one", not as "add this one".
_GATE_SHAPE_SCAN = "ast: return (..., None, ...) mentioning 401/403/423"

#: Helpers that answer "who is this?" and **do not deny**. They hand back a user,
#: an id, or ``None``, and then it is entirely up to the view whether anonymous
#: means "refuse" or "serve the logged-out version".
#:
#: The names here are not weaker-sounding than the ones above. `api_account_user`
#: reads like a gate and is the single most-called auth helper in the codebase —
#: 800-odd routes — and its whole body is `user = require_account(); if not user:
#: return None; return user`. `admin_login_required` reads like a decorator and
#: is `admin = admin_current_user(); if admin: return admin; return None`. Both
#: were in the strong tier in an earlier version of this file, which made that
#: tier mean nothing: it would have been certifying ~1,200 routes as protected on
#: the strength of a call that cannot protect anything.
#:
#: So calling one of these is a *lead*, and the classifier follows it up by
#: asking whether the view body contains a refusal at all. Measured when this
#: split was introduced: 858 routes had only this kind of evidence, 826 of them
#: did refuse, and the 32 that did not are a list short enough to read — which is
#: the entire point. Reviewed at that time, the 32 were deliberately
#: anonymous-tolerant endpoints (`/api/security/report` and
#: `/api/scam-shield/analyze` both build their row with `user_id or 0`) plus
#: routes that delegate the refusal one call deeper (`/api/pulse/groups/<id>/join`
#: hands off to `pulse_group_join_common`, which returns 401 for a falsy user).
#: None was an open route; all of them are worth someone looking again if the
#: list grows.
IDENTITY_HELPERS: dict[str, str] = {
    "api_account_user": AUTH_USER,
    "require_account": AUTH_USER,
    "account_user_id": AUTH_USER,
    "_current_user": AUTH_USER,
    "_dashboard_json_user": AUTH_USER,
    # Documents itself as "a lookup, never a rotation" — it reads the session row
    # for a persistent-cookie holder and stops. An unrecognised cookie is simply
    # not an identity here.
    "messenger_media_cookie_user_id": AUTH_USER,
    "admin_login_required": AUTH_ADMIN,
    "admin_current_user": AUTH_ADMIN,
    "_current_admin": AUTH_ADMIN,
    # A predicate, not a resolver: `admin_has_permission(admin, permission)`
    # takes an already-resolved admin and returns a bool. It is in this table
    # because its presence means an admin was obtained upstream, and out of the
    # strong tier because returning `False` denies nobody by itself.
    "admin_has_permission": AUTH_ADMIN,
}

#: `require_admin_password()` is a gate, and a bad one: it accepts a shared
#: secret from the **query string**, compares it with `==` rather than
#: `secrets.compare_digest`, and carries no per-actor identity, so its call sites
#: produce no attributable audit trail. It is listed separately from
#: REFUSAL_GATES so that a route relying on it classifies as ADMIN (it is not
#: open) while remaining individually findable — the web rebuild must not expose
#: any of these, and `routes_behind_weak_gates()` is how that stays checkable
#: instead of being a sentence in a document.
WEAK_GATE_HELPERS: dict[str, str] = {
    "require_admin_password": AUTH_ADMIN,
}

_CALL = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")

#: Evidence that a view body can turn a caller away. Used only to follow up an
#: IDENTITY_HELPERS hit: "this view resolved an identity — does it ever refuse?"
#:
#: Frankly heuristic. `redirect(` is here because that is how page routes send an
#: anonymous visitor to the login screen, and it will also match a redirect that
#: has nothing to do with auth. That imprecision is in the safe direction for
#: what it is used for (finding routes to *review*, where a false "this refuses"
#: only shortens the list) and in the unsafe direction for certification, which
#: is why a match never upgrades anything past the identity helper's own kind.
_REFUSAL_MARKER = re.compile(
    r"\b(?:401|403|423)\b"
    r"|\b(?:api_error|abort|redirect|login_required_response|mobile_login_gate_error"
    r"|restricted_owner_message_response|premium_required_response)\s*\("
)


def _declare(kind: str, reason: str = ""):
    def decorator(fn: Callable) -> Callable:
        setattr(fn, DECLARATION_ATTR, {"kind": kind, "reason": reason})

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            return fn(*args, **kwargs)

        # Also on the wrapper: Flask registers whatever it is handed, and the
        # audit walks `app.view_functions`. Stamping only the inner function
        # would make every declaration invisible the moment another decorator
        # sat between this one and the route.
        setattr(wrapper, DECLARATION_ATTR, {"kind": kind, "reason": reason})
        return wrapper

    return decorator


def auth_required(fn: Callable) -> Callable:
    """Declare that this route requires a signed-in member.

    Declarative, not enforcing. It does not add a check — the view still calls
    `api_account_user()` or equivalent — because a decorator that silently
    changed the auth behaviour of 2,160 existing routes is not a change anyone
    could review, and the failure mode of getting it subtly wrong is an outage
    or a privilege bug. What this buys is that the *intent* is now machine
    readable, so a route that forgets its gate is visible.
    """
    return _declare(AUTH_USER)(fn)


def admin_required(fn: Callable) -> Callable:
    """Declare that this route requires an administrator identity."""
    return _declare(AUTH_ADMIN)(fn)


def public_route(reason: str):
    """Declare that this route is deliberately reachable without credentials.

    `reason` is required and must be non-empty. The whole value of an allowlist
    is that each entry was a decision someone made and can be argued with later;
    an allowlist you can join without saying why is just a list of routes that
    happen to be open.
    """
    if not (reason or "").strip():
        raise ValueError(
            "public_route() requires a reason. An undocumented public route is "
            "indistinguishable from a forgotten auth check."
        )
    return _declare(AUTH_PUBLIC, reason.strip())


def declaration_of(view: Callable) -> dict | None:
    """The decorator's stamp, looking through any wrappers between us and it."""
    seen = set()
    current = view
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found = getattr(current, DECLARATION_ATTR, None)
        if found:
            return found
        current = getattr(current, "__wrapped__", None)
    return None


def view_source(view: Callable) -> str:
    """Source of the innermost function, or "" when it cannot be read.

    An empty result is never treated as "no gate found" by callers — it is its
    own classification. A view whose source is unreadable has not been shown to
    be unprotected; it has not been examined at all, and those are different
    claims.
    """
    current = view
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        nxt = getattr(current, "__wrapped__", None)
        if nxt is None:
            break
        current = nxt
    try:
        return inspect.getsource(current)
    except (OSError, TypeError):
        return ""


def gates_called(source: str) -> list[str]:
    """Refusal-returning helper names that appear as calls in this source."""
    if not source:
        return []
    names = set(_CALL.findall(source))
    return [n for n in sorted(names) if n in REFUSAL_GATES or n in WEAK_GATE_HELPERS]


def identities_called(source: str) -> list[str]:
    """Identity-resolving helpers that appear as calls in this source."""
    if not source:
        return []
    names = set(_CALL.findall(source))
    return [n for n in sorted(names) if n in IDENTITY_HELPERS]


def refuses(source: str) -> bool:
    """Whether this body contains anything that could turn a caller away."""
    return bool(source) and bool(_REFUSAL_MARKER.search(source))


def _kind_of(names: Iterable[str], table: dict[str, str], *extra: dict[str, str]) -> str:
    """Admin wins when both kinds appear.

    A route that consults an admin helper at all is an admin surface;
    classifying it by the member helper it *also* calls would understate what is
    behind it.
    """
    kinds = set()
    for name in names:
        for source in (table, *extra):
            if name in source:
                kinds.add(source[name])
                break
    return AUTH_ADMIN if AUTH_ADMIN in kinds else AUTH_USER


#: How many delegation hops `classify_view` will follow. Two is enough for every
#: shape observed in this codebase (`api_pulse_group_leave_id` -> `_slug` ->
#: gate) and small enough that the walk stays cheap and obviously terminating.
MAX_DELEGATION_DEPTH = 2


def _delegates_of(view: Callable, source: str) -> list[tuple[str, Callable]]:
    """Functions called by this view that live in the view's own module.

    Restricted to the same module deliberately. Following imports would drag the
    walk through `json`, `datetime` and every service in the tree for no benefit;
    the delegation pattern this exists for — a route that is one line handing off
    to a private helper — is always module-local.
    """
    module = sys.modules.get(getattr(view, "__module__", "") or "")
    if module is None:
        return []
    out = []
    for name in sorted(set(_CALL.findall(source or ""))):
        if name.startswith("__"):
            continue
        candidate = getattr(module, name, None)
        if candidate is None or candidate is view:
            continue
        if inspect.isfunction(candidate) and getattr(candidate, "__module__", "") == module.__name__:
            out.append((name, candidate))
    return out


def classify_view(view: Callable, _depth: int = 0, _caller_refuses: bool = False) -> tuple[str, str]:
    """Return ``(classification, evidence)`` for one view function.

    A declaration always wins over detection. If someone wrote
    `@public_route("provider webhook, signature-authenticated")` on a view that
    happens to call `api_account_user()` somewhere inside, the declaration is
    what they meant and the detector does not get a vote.
    """
    declared = declaration_of(view)
    if declared:
        reason = declared.get("reason") or ""
        return declared["kind"], f"declared:{declared['kind']}" + (f" ({reason})" if reason else "")

    source = view_source(view)
    if not source:
        return AUTH_UNKNOWN, "source-unavailable"

    gates = gates_called(source)
    if gates:
        return _kind_of(gates, REFUSAL_GATES, WEAK_GATE_HELPERS), "gates:" + ",".join(gates)

    # "Somewhere at or above this frame, a caller can turn the request away."
    # Carried down through delegation because the two halves of a gate are
    # routinely split across a call: `/api/progress/invite` is `uid =
    # _progress_viewer(); if not uid: return 401`, and `_progress_viewer` is
    # `api_account_user()` with a `None` fallthrough. Neither frame on its own
    # looks protected — one resolves without refusing, the other refuses without
    # resolving — and only the pair does.
    #
    # This propagates one way only: caller-refuses flows *down* to the delegate,
    # and a delegate that refuses does not flow back *up*. So the mirror image —
    # a view that resolves an identity and hands it to a helper that does the
    # refusing — is still reported as identity-without-refusal. Two of the
    # eleven routes currently in that state are exactly this shape
    # (`/api/pulse/groups/<id>/join` passes its user to `pulse_group_join_common`,
    # which returns 401 for a falsy one). Closing it would mean computing refusal
    # transitively over the delegate graph, which is more inference for less
    # return: the remaining list is short enough to read, and every extra layer
    # of guessing makes the word "evidence" mean less. Left open knowingly, and
    # recorded in the baseline's review notes rather than papered over.
    can_refuse = _caller_refuses or refuses(source)

    identities = identities_called(source)
    if identities:
        if can_refuse:
            # The strongest thing sayable without reading it: the view resolved
            # an identity and the body can refuse. Not proof that the refusal is
            # wired to the identity — that would need dataflow — but the two
            # facts together are what every correctly written route here looks
            # like, and their absence is what the review list is built from.
            return _kind_of(identities, IDENTITY_HELPERS), "identity+refusal:" + ",".join(identities)
    # Before giving up, follow the delegation: an enormous share of this
    # codebase's routes are a single line handing off to a private helper that
    # holds the gate — `add_blocked()` is `return _blocked_route(True)`,
    # `offer_accept()` is `return _transition(offer_id, "accept")`. Classifying
    # those as ungated says nothing true about them and buries the routes that
    # really are ungated in a list nobody can read.
    #
    # This runs *after* the identity check but *before* reporting
    # identity-without-refusal, and the order is load-bearing. The common shape
    # is both at once: `api_pulse_group_join_id()` is `user = api_account_user();
    # return pulse_group_join_common(user, group_id=group_id)` — it resolves an
    # identity, never refuses, and hands the refusal to the delegate (which does
    # return 401 for a falsy user). Reporting identity-without-refusal first, as
    # an earlier version did, meant those routes never reached the delegation
    # step at all and sat on the review list permanently with a description of
    # them that was wrong.
    #
    # What this does *not* establish: that the delegate is reached on every path.
    # A view could call a gated helper in one branch and serve the request in
    # another. It is the same order of inference as the rest of the detector —
    # better than "no evidence", short of proof — and the evidence string records
    # the hop so a reviewer can see exactly what was followed.
    if _depth < MAX_DELEGATION_DEPTH:
        for name, delegate in _delegates_of(view, source):
            kind, evidence = classify_view(
                delegate, _depth=_depth + 1, _caller_refuses=can_refuse
            )
            if kind != AUTH_UNKNOWN:
                return kind, f"via:{name}>{evidence}"

    if identities:
        # Resolved somebody and never turns anyone away, and no delegate
        # accounts for it either. Deliberately anonymous-tolerant, or open.
        # Both need a human, so neither gets a classification.
        return AUTH_UNKNOWN, "identity-without-refusal:" + ",".join(identities)

    return AUTH_UNKNOWN, "no-known-gate"


#: Directories with no first-party source in them. Skipped when scanning for
#: helper definitions, both for speed and to stop a vendored function that
#: happens to share a name from vouching for a helper we actually deleted.
_SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
    "build", "dist", "ios", "android", ".expo", "Pods",
})


def undefined_gate_helpers(root: str | None = None) -> list[str]:
    """Vocabulary entries that no longer define a function anywhere in the tree.

    The failure this exists to prevent is silent and total: rename
    `api_account_user` and this module stops recognising 566 routes as
    protected. Every one of them becomes `unknown`, which is loud. But delete or
    rename a helper that only guards a handful, and the detector quietly gets
    less accurate while every downstream check stays green. Checking that each
    name still defines something turns that into a failure.

    **Why a source scan and not `hasattr(bot, name)`.** That was the first
    version, and on a healthy checkout it reported five false positives:
    `_entry`, `_operations_entry`, `_operator_entry`, `_require_user` and
    `_security_entry` are module-private to the blueprints that define them and
    never become attributes of `bot`. A guard that fails on a clean tree gets an
    allowlist bolted onto it, and the allowlist is exactly the five entries most
    worth watching — the private ones, whose renames nothing else would notice.
    So the question had to change to one that is answerable for every entry in
    the vocabulary rather than only the public half.

    This asks a deliberately weak question: *does this name still define
    something?* It does not ask whether the definition is still a gate, or
    whether the routes that used to call it still do. The second of those is the
    baseline's job — a route whose evidence goes from `gates:...` to
    `no-known-gate` fails there, which is where a partial rename surfaces. This
    catches the case the baseline cannot: a vocabulary that has quietly stopped
    naming anything, where every route looks unchanged because the detector has
    gone blind uniformly.
    """
    names = sorted(set(REFUSAL_GATES) | set(IDENTITY_HELPERS) | set(WEAK_GATE_HELPERS))
    if not names:
        return []
    base = root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pattern = re.compile(
        r"^[ \t]*(?:async[ \t]+)?def[ \t]+(" + "|".join(re.escape(n) for n in names) + r")[ \t]*\(",
        re.MULTILINE,
    )

    defined: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for filename in filenames:
            if not filename.endswith(".py"):
                continue
            try:
                with open(os.path.join(dirpath, filename), "r", encoding="utf-8", errors="replace") as fh:
                    defined.update(pattern.findall(fh.read()))
            except OSError:
                continue
        if len(defined) == len(names):
            break

    return [n for n in names if n not in defined]


def audit_app(app) -> list[dict]:
    """Classify every registered rule. One record per rule, sorted and stable.

    Sorted because this feeds a checked-in baseline: iteration order of
    `url_map` is not something to make a diff depend on.
    """
    records = []
    for rule in app.url_map.iter_rules():
        view = app.view_functions.get(rule.endpoint)
        methods = sorted((rule.methods or set()) - {"HEAD", "OPTIONS"})
        if view is None:
            kind, evidence = AUTH_UNKNOWN, "no-view-function"
        else:
            kind, evidence = classify_view(view)
        records.append({
            "rule": str(rule.rule),
            "endpoint": str(rule.endpoint),
            "methods": methods,
            "auth": kind,
            "evidence": evidence,
            "declared": bool(view is not None and declaration_of(view)),
        })
    records.sort(key=lambda r: (r["rule"], r["endpoint"], ",".join(r["methods"])))
    return records


def routes_behind_weak_gates(records: Iterable[dict]) -> list[dict]:
    """Records whose only evidence is a gate we have already judged inadequate."""
    weak = []
    for record in records:
        evidence = record.get("evidence") or ""
        if not evidence.startswith("gates:"):
            continue
        names = evidence[len("gates:"):].split(",")
        if names and all(n in WEAK_GATE_HELPERS for n in names if n):
            weak.append(record)
    return weak


def write_methods(record: dict) -> list[str]:
    """Methods that change state. GET is not on this list and HEAD/OPTIONS are
    already stripped, so anything remaining is a mutation."""
    return [m for m in record.get("methods") or [] if m != "GET"]


#: Modules whose every registered view must carry a declaration, enforced at
#: boot. Empty today, and that is correct: it is an opt-in list, and the code it
#: is waiting for has not been written yet.
#:
#: Why modules and not path prefixes. The obvious reading of "default-deny for
#: new routes" is to guard a prefix — and it does not work here, because the SPA
#: is planned to live under `/pulse/*` (`PULSESOC_WEB_TARGET_ARCHITECTURE.md`
#: §2.2) and 323 legacy rules are already there. A prefix guard would refuse to
#: boot the moment it was switched on, which means it would be switched off.
#:
#: A module is also the honest unit. "Code written under the new rule" is a
#: property of who wrote it, not of the URL it happens to be mounted at, and the
#: rebuild will mount new views at old-looking paths on purpose.
DECLARATION_REQUIRED_MODULES: frozenset[str] = frozenset()


def undeclared_in_guarded_modules(app) -> list[str]:
    """Registered rules from a guarded module that declare no authentication."""
    offenders = []
    for rule in app.url_map.iter_rules():
        view = app.view_functions.get(rule.endpoint)
        if view is None:
            continue
        module = getattr(view, "__module__", "") or ""
        if module not in DECLARATION_REQUIRED_MODULES:
            continue
        if declaration_of(view) is None:
            offenders.append(f"{rule.rule} -> {module}.{rule.endpoint}")
    return sorted(offenders)


def assert_routes_declared(app) -> None:
    """Refuse to finish booting if a guarded module registered an undeclared route.

    This is the second half of the mechanism, and it catches a case the merge
    gate structurally cannot. `tests/protection/test_route_auth.py` compares
    against a baseline, so it sees a route that exists *in the process it runs
    in*. Optional route packs register inside `except Exception` blocks; a
    blueprint that raises during a test run is simply absent, and absence is
    deliberately not an error there (a deleted route is not a regression). The
    same blueprint registering fine in production, carrying an undeclared route,
    is invisible to that test and visible to this one.

    Deliberately fatal, and deliberately not overridable by an environment
    variable. A fail-closed check with a documented escape hatch is a check that
    gets escaped at 3am and never un-escaped; the recovery here is to declare the
    route, which is a one-line change and the change that was owed anyway.

    Scoped to `DECLARATION_REQUIRED_MODULES` so this cannot take production down
    for the 2,160 legacy routes, none of which declares anything. The two halves
    cover different failures on purpose: the test is broad and advisory, this is
    narrow and fatal.
    """
    offenders = undeclared_in_guarded_modules(app)
    if not offenders:
        return
    raise RuntimeError(
        "Refusing to start: {n} route(s) in declaration-required modules carry "
        "no authentication declaration:\n  {routes}\n\n"
        "Add @auth_required, @admin_required or @public_route(reason='...') "
        "from services.route_auth to each. If a route is genuinely public, say "
        "so with a reason -- the reason is the part a reviewer reads.".format(
            n=len(offenders), routes="\n  ".join(offenders)
        )
    )
