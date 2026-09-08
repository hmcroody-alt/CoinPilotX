#!/usr/bin/env python3
"""Enumerate native destinations and web routes, and join them into a parity census.

Phase 70 of the web<->native parity program. The point is that the parity matrix
is *derived* rather than hand-maintained: a hand-written matrix goes stale the
first time somebody adds a screen, and a stale matrix is worse than none because
it reads as evidence.

The join is possible because the two clients already share a URL vocabulary.
`mobile-native/src/navigation/linking.ts` declares its prefixes as
`pulsesoc://` *and* `https://pulsesoc.com`, so a native deep-link path and a web
route path are the same string. That is the backbone of this script: for every
native destination path, is there a Flask route that serves HTML at that path?

Sources of truth, in order of authority:

  masterNavigation.ts  the curated destination registry (label/route/status)
  linking.ts           the deep-link path -> screen map
  src/**/*Screen.tsx   every screen file that exists
  bot.py + services/   every Flask route, classified HTML vs JSON

Nothing here infers behaviour. A route is called an HTML page only when it can
be traced to something that emits a document -- a template, a doctype, or a
`text/html` response -- following `return helper(...)` edges to a fixpoint,
because almost no page route in this app calls `render_template` directly. The
strongest verdict the join can issue is therefore `PAGE` ("a page is served at
this path"), never `PARITY`: whether that page carries the same product as the
native screen is a behavioural question no static scan can answer.

Usage:
    .venv/bin/python scripts/parity/web_native_census.py            # write docs
    .venv/bin/python scripts/parity/web_native_census.py --json     # machine output
    .venv/bin/python scripts/parity/web_native_census.py --check    # CI drift gate
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, asdict

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

NATIVE_ROOT = os.path.join(REPO, "mobile-native", "src")
MASTER_NAV = os.path.join(NATIVE_ROOT, "navigation", "masterNavigation.ts")
LINKING = os.path.join(NATIVE_ROOT, "navigation", "linking.ts")
BOT_PY = os.path.join(REPO, "bot.py")
SERVICES_DIR = os.path.join(REPO, "services")


# --------------------------------------------------------------------------
# native side
# --------------------------------------------------------------------------

@dataclass
class NativeDestination:
    label: str
    route: str
    status: str          # native | shell | provider | gated
    section: str
    description: str = ""
    source: str = "masterNavigation.ts"


@dataclass
class NativeDeepLink:
    screen: str
    path: str            # normalised to a leading slash


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def parse_master_navigation(text: str) -> list[NativeDestination]:
    """Pull the curated destination registry out of the TS literal.

    Regex rather than a TS parser because the file is a flat object literal that
    is checked by `tsc` on every CI run; if its shape changes enough to break
    this, the count assertion at the bottom fails loudly rather than silently
    returning fewer rows.
    """
    destinations: list[NativeDestination] = []
    section = "?"
    # Sections and actions are interleaved in source order, so a single pass
    # keeps each action attributed to the section heading above it.
    token = re.compile(
        r'title:\s*"(?P<title>[^"]*)"'
        r'|label:\s*"(?P<label>[^"]*)",\s*route:\s*"(?P<route>[^"]*)",\s*'
        r'status:\s*"(?P<status>[^"]*)",\s*description:\s*"(?P<description>[^"]*)"'
    )
    for match in token.finditer(text):
        if match.group("title"):
            section = match.group("title")
            continue
        if match.group("label"):
            destinations.append(NativeDestination(
                label=match.group("label"),
                route=match.group("route"),
                status=match.group("status"),
                section=section,
                description=match.group("description"),
            ))
    return destinations


def parse_linking(text: str) -> list[NativeDeepLink]:
    """Extract the deep-link path map.

    Two shapes appear in the file: `ScreenName: "pulse/x"` and an expanded
    `{ path: "pulse/x" }` for screens that also carry params. The expanded form
    does not name its screen on the same line, so those are recorded against the
    nearest preceding identifier key.
    """
    links: list[NativeDeepLink] = []
    pattern = re.compile(r'(?P<key>[A-Za-z][A-Za-z0-9_]*)\s*:\s*"(?P<path>[a-z0-9/:?_-]+)"')
    last_screen = "?"
    for match in pattern.finditer(text):
        key = match.group("key")
        path = match.group("path")
        if key == "path":
            screen = last_screen
        else:
            screen = key
            last_screen = key
        if not path or path.startswith("http"):
            continue
        links.append(NativeDeepLink(screen=screen, path="/" + path.lstrip("/")))
    return links


def list_native_screens() -> list[str]:
    """Every `*Screen.tsx` under src/, not just under src/screens/.

    `src/launch/ComingSoonScreen.tsx` is a real destination that lives outside
    the screens directory. `src/components/Screen.tsx` matches the same suffix
    but is the layout primitive every screen wraps itself in, so it is excluded
    by name rather than by directory -- a screen is a file whose name has
    something in front of "Screen".
    """
    names: list[str] = []
    for root, _dirs, files in os.walk(NATIVE_ROOT):
        if "__tests__" in root or "node_modules" in root:
            continue
        for name in files:
            if name.endswith("Screen.tsx") and name != "Screen.tsx":
                names.append(name[: -len(".tsx")])
    return sorted(set(names))


def native_systems() -> list[str]:
    """Feature directories under src/ are the app's own system decomposition."""
    skip = {"__tests__", "assets", "utils", "components", "core", "data", "api", "i18n"}
    return sorted(
        name for name in os.listdir(NATIVE_ROOT)
        if os.path.isdir(os.path.join(NATIVE_ROOT, name)) and name not in skip
    )


# --------------------------------------------------------------------------
# web side
# --------------------------------------------------------------------------

@dataclass
class WebRoute:
    path: str
    methods: list[str]
    function: str
    file: str
    line: int
    kind: str = "unknown"        # html | json | redirect | unknown
    template: str = ""
    # How the route was found. The drift gate compares the parsed count against
    # a raw count of `@x.route(` lines, and that invariant only holds for routes
    # that actually have a decorator to count -- declarative tables mounted with
    # `add_url_rule` have none, so they are tallied separately.
    source: str = "decorator"    # decorator | table


# Flask offers `@bp.route(p, methods=["GET"])` and the shortcuts
# `@bp.get(p)` / `.post` / `.put` / `.patch` / `.delete`. Matching only the
# first form lost all 106 routes in pulse_communications_v2, which is how
# /pulse/intelligence came to be reported as having no web surface while Flask
# was serving it the whole time.
VERB = "get|post|put|patch|delete"
ROUTE_DECORATOR = re.compile(
    r'^@(?P<obj>[A-Za-z_][A-Za-z0-9_.]*)\.(?P<attr>route|' + VERB + r')\('
    r'\s*(?P<path>.+?)\s*'
    r'(?:,\s*methods\s*=\s*(?P<methods>\[[^\]]*\]))?'
    r'(?:,\s*[a-z_]+\s*=\s*[^,()]+)*\s*\)\s*$'
)
ROUTE_DECORATOR_START = re.compile(
    r'^@(?P<obj>[A-Za-z_][A-Za-z0-9_.]*)\.(?:route|' + VERB + r')\(')
DEF_LINE = re.compile(r'^\s*def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(')


def _join_decorator(lines: list[str], index: int) -> tuple[str, int] | None:
    """Collapse a `@x.route(...)` decorator onto one line, however it is wrapped.

    Returns (single_line_text, index_of_last_line) or None if the parentheses
    never balance. Roughly a third of the service blueprints put the path on its
    own continuation line, so a line-at-a-time matcher silently skipped them.
    """
    depth = 0
    parts: list[str] = []
    for cursor in range(index, min(index + 8, len(lines))):
        stripped = lines[cursor].strip()
        parts.append(stripped)
        for char in stripped:
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
        if depth <= 0:
            return " ".join(parts), cursor
    return None


def _literal_path(raw: str, prefix_values: dict[str, str]) -> str | None:
    """Resolve the decorator's first argument to a literal path when possible.

    Four forms occur in this repo, and all four must resolve or whole systems
    vanish from the census: a bare string; `PREFIX + "/x"`; an f-string
    `f"{PREFIX}/x"`; and a bare constant `PREFIX`. The marketplace and Private
    Office blueprints use only the last three, so a parser that handled just the
    first two reported them as having no web surface at all -- an extractor bug
    that reads exactly like a real parity gap. Anything still unresolvable
    returns None rather than guessing.
    """
    raw = raw.strip()
    simple = re.fullmatch(r'["\'](?P<value>[^"\']*)["\']', raw)
    if simple:
        return simple.group("value")
    joined = re.fullmatch(
        r'(?P<name>[A-Z_][A-Z0-9_]*)\s*\+\s*["\'](?P<tail>[^"\']*)["\']', raw)
    if joined and joined.group("name") in prefix_values:
        return prefix_values[joined.group("name")] + joined.group("tail")
    bare = re.fullmatch(r'[A-Z_][A-Z0-9_]*', raw)
    if bare and raw in prefix_values:
        return prefix_values[raw]
    fstring = re.fullmatch(r'f["\'](?P<body>[^"\']*)["\']', raw)
    if fstring:
        body = fstring.group("body")
        names = re.findall(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', body)
        if names and all(name in prefix_values for name in names):
            for name in names:
                body = body.replace("{%s}" % name, prefix_values[name])
            return body
    return None


TOPLEVEL_DEF = re.compile(r'^def\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(')
DOCTYPE = re.compile(r'(?i)<!doctype\s+html')
RETURN_LINE = re.compile(r'^[ \t]*return[ \t]+(?P<expr>.*)$', re.MULTILINE)
CALL_NAME = re.compile(r'(?:^|[^.\w])(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(')
HTML_MIMETYPE = re.compile(r'(?:mimetype|content_type)\s*=\s*["\']text/html')
ANY_MIMETYPE = re.compile(r'(?:mimetype|content_type)\s*=\s*["\'](?P<value>[^"\']+)')


def _returned_calls(body: str) -> list[str]:
    """Function names invoked by any `return` expression in this body.

    Nested calls count: `return Response(render_gateway(...), ...)` is the admin
    gateway's way of serving a page, so only looking at the outermost callee
    would file the whole admin login surface under "redirect".
    """
    names: list[str] = []
    for match in RETURN_LINE.finditer(body):
        names.extend(m.group("name") for m in CALL_NAME.finditer(match.group("expr")))
    return names


TRIPLE_QUOTE = re.compile(r'"""|\'\'\'')


def _body_end(lines: list[str], start: int) -> int:
    """First line at or after `start + 1` that dedents out of the block.

    Triple-quoted strings have to be tracked, because the HTML this codebase
    embeds is written flush to column 0 inside `\"\"\"...\"\"\"`. Treating those
    lines as dedents cut `pulse_social_shell` off at 73 of its 92 lines --
    exactly the tail where it returns the document -- so the single most
    important page-producing function in the app looked like it only ever
    returned a redirect.
    """
    in_string = False
    for cursor in range(start + 1, len(lines)):
        candidate = lines[cursor]
        if not in_string and candidate.strip() and not candidate.startswith((" ", "\t", ")")):
            return cursor
        for _ in TRIPLE_QUOTE.finditer(candidate):
            in_string = not in_string
    return len(lines)


def _toplevel_functions(text: str) -> dict[str, str]:
    """Map every module-level function name to its source body."""
    lines = text.splitlines()
    bodies: dict[str, str] = {}
    for start, line in enumerate(lines):
        match = TOPLEVEL_DEF.match(line)
        if match:
            bodies[match.group("name")] = "\n".join(
                lines[start:_body_end(lines, start)])
    return bodies


def find_html_producers(sources: list[str]) -> set[str]:
    """Names of functions that ultimately return a rendered HTML page.

    This has to be a fixpoint rather than a single grep because almost no page
    route in this codebase calls `render_template`. The web surface is built by
    `pulse_social_shell(...)` and friends, which are themselves several calls
    deep from the route. Seeding on the functions that literally emit a document
    and then propagating through `return helper(...)` edges is what turns a
    "39 HTML routes" undercount into the real number; classifying on
    `render_template` alone reports almost the entire website as a JSON API.
    """
    bodies: dict[str, str] = {}
    for text in sources:
        bodies.update(_toplevel_functions(text))
    producers = {
        name for name, body in bodies.items()
        if "render_template(" in body or DOCTYPE.search(body)
    }
    producers |= {
        name for name, body in bodies.items() if HTML_MIMETYPE.search(body)
    }
    while True:
        grown = {
            name for name, body in bodies.items()
            if name not in producers
            and any(call in producers for call in _returned_calls(body))
        }
        if not grown:
            return producers
        producers |= grown


def classify_body(body: str, html_producers: set[str]) -> tuple[str, str]:
    """Decide what a route hands back: an html page, a file, json, or a redirect."""
    rendered = re.search(r'render_template\(\s*["\']([^"\']+)["\']', body)
    if rendered:
        return "html", rendered.group(1)
    if "render_template(" in body or DOCTYPE.search(body) or HTML_MIMETYPE.search(body):
        return "html", "inline"
    producers = [c for c in dict.fromkeys(_returned_calls(body)) if c in html_producers]
    if producers:
        # Report every producer, not the first. Several pages return an
        # `ios_paid_digital_unavailable_response()` guard before their real
        # shell, and naming only the first made the Premium, Courses and
        # Portfolio pages look like they serve nothing but a store-policy notice.
        return "html", "+".join(f"{name}()" for name in producers)
    mimetype = ANY_MIMETYPE.search(body)
    if mimetype and "jsonify" not in body:
        # A CSV export or an image stream is neither a page nor an API payload;
        # counting it as either one distorts both sides of the parity ratio.
        return "file", mimetype.group("value")
    if re.search(r'\breturn\s+redirect\(', body) and "jsonify" not in body:
        return "redirect", ""
    return "json", ""


def _blueprint_prefixes(text: str) -> dict[str, str]:
    """Map each Blueprint variable to its `url_prefix`, defaulting to "".

    A Blueprint's prefix is part of every path it serves. Ignoring it filed the
    sentinel routes under `/events` and `/incidents` rather than
    `/api/admin/sentinel/...` -- not merely a wrong path but a plausible-looking
    one, which is the kind of error that survives review.
    """
    prefixes: dict[str, str] = {}
    for match in BLUEPRINT_PREFIX.finditer(text):
        found = re.search(r'url_prefix\s*=\s*["\']([^"\']*)["\']', match.group("args"))
        prefixes[match.group("name")] = found.group(1) if found else ""
    return prefixes


def parse_flask_routes(path: str, html_producers: set[str] | None = None) -> list[WebRoute]:
    html_producers = html_producers or set()
    text = _read(path)
    lines = text.splitlines()
    prefix_values = {
        m.group("name"): m.group("value")
        for m in re.finditer(
            r'^(?P<name>[A-Z_][A-Z0-9_]*)\s*=\s*["\'](?P<value>/[^"\']*)["\']',
            text, re.MULTILINE)
    }
    blueprint_prefix = _blueprint_prefixes(text)
    routes: list[WebRoute] = []
    rel = os.path.relpath(path, REPO)
    for index, line in enumerate(lines):
        if not ROUTE_DECORATOR_START.match(line.strip()):
            continue
        joined = _join_decorator(lines, index)
        if joined is None:
            continue
        decorator, last_line = joined
        match = ROUTE_DECORATOR.match(decorator)
        if not match:
            continue
        literal = _literal_path(match.group("path"), prefix_values)
        if literal is None:
            continue
        literal = blueprint_prefix.get(match.group("obj"), "") + literal
        attr = match.group("attr")
        if attr == "route":
            methods_raw = match.group("methods") or '["GET"]'
            methods = re.findall(r'["\']([A-Z]+)["\']', methods_raw) or ["GET"]
        else:
            methods = [attr.upper()]

        # Walk forward past stacked decorators to the def, then take the body.
        cursor = last_line + 1
        while cursor < len(lines) and not DEF_LINE.match(lines[cursor]):
            if cursor - index > 12:
                break
            cursor += 1
        if cursor >= len(lines) or not DEF_LINE.match(lines[cursor]):
            continue
        function = DEF_LINE.match(lines[cursor]).group("name")

        body = "\n".join(lines[cursor:_body_end(lines, cursor)])

        kind, template = classify_body(body, html_producers)

        routes.append(WebRoute(
            path=literal, methods=methods, function=function,
            file=rel, line=index + 1, kind=kind, template=template))
    return routes


SKIP_DIRS = {
    ".git", ".venv", "node_modules", ".claude", "mobile", "mobile-native",
    "UNDX_RECON", "tests", "__pycache__", "migrations", "venv",
}

# Files that declare routes on some Flask app that is *not* `webhook_app`.
# Counting them inflates the web surface with endpoints no browser can reach.
# Each exclusion is checked against the live url_map by
# scripts/parity/reconcile_urlmap.py, so this list cannot quietly rot.
NON_WEB_ROUTE_FILES = {
    # Its own Flask() app; a local developer bridge, never mounted on the site.
    "undx_desktop_connector.py": "standalone Flask app (desktop connector)",
    # Its own Flask() app; an internal worker addressed over the private network.
    "services/command_center_worker/app.py": "standalone Flask app (worker)",
    # A Blueprint that is deliberately never registered. tests/sentinel/
    # test_ethical_regression.py actively asserts `sentinel_bp` stays out of the
    # app, so treating these as web surface would contradict a guardrail test.
    "services/sentinel/api.py": "blueprint intentionally left unregistered",
    # Extends bot.app, but nothing imports it, so the rule is never added.
    "cj_staging_backend.py": "module never imported at runtime",
}

BLUEPRINT_PREFIX = re.compile(
    r'^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*Blueprint\((?P<args>[^)]*)\)',
    re.MULTILINE | re.DOTALL)


def iter_python_sources() -> list[str]:
    """Every Python file that could register a route on the web app.

    Scanning bot.py plus `services/*.py` was not enough: `services/` has
    packages one level down, and `pulse_communications_v2/` is a top-level
    package holding 107 routes. The url_map diff is what surfaced this -- the
    census claimed /pulse/intelligence had no web surface while Flask was
    serving it. Directories are excluded only when they cannot contribute to
    `webhook_app`; anything else is scanned and then reconciled against the
    live url_map by tests/parity/.
    """
    files: list[str] = []
    for root, dirs, names in os.walk(REPO):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            full = os.path.join(root, name)
            if os.path.relpath(full, REPO) in NON_WEB_ROUTE_FILES:
                continue
            files.append(full)
    return files


def collect_web_routes() -> list[WebRoute]:
    files = iter_python_sources()
    sources = []
    for path in files:
        try:
            sources.append(_read(path))
        except (OSError, UnicodeDecodeError):
            continue
    html_producers = find_html_producers(sources)
    routes: list[WebRoute] = []
    for path in files:
        try:
            routes.extend(parse_flask_routes(path, html_producers))
            routes.extend(parse_route_tables(path))
        except (OSError, UnicodeDecodeError):
            continue
    return routes


ROUTE_TABLE = re.compile(r'^ROUTES\b[^=]*=\s*[\(\[]', re.MULTILINE)
TABLE_ENTRY = re.compile(
    r'\bmethod\s*=\s*["\'](?P<method>[A-Z]+)["\'][^)]*?\brule\s*=\s*["\'](?P<rule>/[^"\']*)["\']'
    r'|\brule\s*=\s*["\'](?P<rule2>/[^"\']*)["\'][^)]*?\bmethod\s*=\s*["\'](?P<method2>[A-Z]+)["\']',
    re.DOTALL)


def parse_route_tables(path: str) -> list[WebRoute]:
    """Routes declared as data and mounted with `add_url_rule`, not decorators.

    `services/business_os/commerce_gateway.py` holds a `ROUTES` tuple that the
    adapter loops over, so 31 live Business OS endpoints have no decorator
    anywhere and are invisible to a decorator scan. They are the entire
    marketplace offers/returns/inventory and storefront API -- exactly the
    surface the Store and Marketplace parity work depends on -- so leaving them
    out would have understated those systems while looking complete.
    """
    text = _read(path)
    start = ROUTE_TABLE.search(text)
    if not start:
        return []
    prefix = ""
    found = re.search(r'^API_PREFIX\s*=\s*["\'](?P<value>/[^"\']*)["\']', text, re.MULTILINE)
    if found:
        prefix = found.group("value")
    rel = os.path.relpath(path, REPO)
    line = text[:start.start()].count("\n") + 1
    routes: list[WebRoute] = []
    for match in TABLE_ENTRY.finditer(text, start.end()):
        rule = match.group("rule") or match.group("rule2")
        method = match.group("method") or match.group("method2")
        routes.append(WebRoute(
            path=prefix + rule, methods=[method], function="(route table)",
            file=rel, line=line, kind="json", template="", source="table"))
    return routes


def count_route_decorators() -> int:
    """Raw `@x.route(` occurrences, independent of whether they could be parsed.

    The gate compares this against the parsed total. Every parser gap found so
    far -- wrapped decorators, f-string paths, bare-constant paths -- shrank the
    census silently, and a census that quietly loses a subsystem is worse than
    one that fails, because the missing rows read as "the web does not have
    this".
    """
    total = 0
    for path in iter_python_sources():
        try:
            text = _read(path)
        except (OSError, UnicodeDecodeError):
            continue
        total += sum(1 for line in text.splitlines()
                     if ROUTE_DECORATOR_START.match(line.strip()))
    return total


# --------------------------------------------------------------------------
# the join
# --------------------------------------------------------------------------

# Deliberately NOT "PARITY". This census joins native destinations to Flask
# routes by URL; the strongest thing it can prove is that some HTML page is
# served at the same path. Whether that page carries the same product as the
# native screen is a question about behaviour, and no static route scan can
# answer it. Awarding "PARITY" here would be exactly the fake parity the mission
# forbids, so the best verdict this tool can issue is PAGE, and promotion to
# PARITY has to come from a per-system review that inspects the page itself.
PAGE = "PAGE"
PARTIAL = "PARTIAL"
MISSING = "MISSING"
BLOCKED = "BLOCKED"


@dataclass
class ParityRow:
    label: str
    native_route: str
    section: str
    native_status: str
    web_route: str = ""
    web_kind: str = ""
    web_source: str = ""
    verdict: str = MISSING
    note: str = ""


def _normalise(path: str) -> str:
    """Reduce a destination to the path a Flask route could actually serve.

    Both a query string and a fragment have to go. `Pulse Radio` is registered
    as `/pulse/music#pulse-radio`, and keeping the fragment reported it as
    having no route at all when `/pulse/music` is right there -- a false MISSING
    is the most expensive kind of error this file can make, because it sends
    somebody to build a page that already exists.
    """
    path = "/" + path.strip("/")
    return path.split("?")[0].split("#")[0]


def build_matrix(destinations: list[NativeDestination],
                 web_routes: list[WebRoute]) -> list[ParityRow]:
    by_path: dict[str, list[WebRoute]] = {}
    by_leaf: dict[str, list[WebRoute]] = {}
    for route in web_routes:
        normalised = _normalise(route.path)
        by_path.setdefault(normalised, []).append(route)
        # Admin pages are excluded as candidates: they share leaf names with
        # user destinations (/admin/alerts vs /dashboard/crypto/alerts) while
        # serving a different audience entirely, so they only ever generate
        # false leads.
        if route.kind == "html" and not normalised.startswith("/admin"):
            by_leaf.setdefault(normalised.rsplit("/", 1)[-1], []).append(route)

    rows: list[ParityRow] = []
    for dest in destinations:
        target = _normalise(dest.route)
        matches = by_path.get(target, [])
        html = [r for r in matches if r.kind == "html"]
        redirect = [r for r in matches if r.kind == "redirect"]
        row = ParityRow(
            label=dest.label, native_route=dest.route, section=dest.section,
            native_status=dest.status)
        if html:
            row.web_route = html[0].path
            row.web_kind = html[0].kind
            row.web_source = f"{html[0].file}:{html[0].line}"
            row.verdict = PAGE
            row.note = f"html page served, source={html[0].template or 'inline'}"
            if redirect:
                # Two handlers are registered for one path and only one can win.
                # /pulse/alerts is declared as a page in pulse_communications_v2
                # and as a redirect in bot.py; the blueprint currently wins, so
                # the page is reachable, but that is a property of registration
                # order rather than of anything written down. Preferring html
                # here is deliberate -- a rendering handler is the stronger
                # claim -- but the losing handler is named so a per-system
                # review can delete it instead of discovering it as an outage.
                row.note += (f"; NOTE also declared as a redirect at "
                             f"{redirect[0].file}:{redirect[0].line} — duplicate "
                             "registration, page wins only by ordering")
        elif redirect:
            row.web_route = redirect[0].path
            row.web_kind = "redirect"
            row.web_source = f"{redirect[0].file}:{redirect[0].line}"
            row.verdict = PARTIAL
            row.note = "route redirects rather than rendering a web surface"
        elif matches:
            row.web_route = matches[0].path
            row.web_kind = matches[0].kind
            row.web_source = f"{matches[0].file}:{matches[0].line}"
            row.verdict = PARTIAL
            row.note = "path exists but serves JSON, not a web page"
        else:
            row.verdict = MISSING
            row.note = "no Flask route serves this path"
            near = by_leaf.get(target.rsplit("/", 1)[-1], [])
            if near:
                # The web often has the feature under a different URL: native
                # advertises /dashboard/creator/content-planner while the site
                # serves /pulse/dashboard/content-planner. Reporting a bare
                # MISSING there invites a duplicate build, so name the candidate
                # and let the per-system review decide whether it is the same
                # product behind a divergent path or genuinely something else.
                row.note += (f"; candidate page at `{near[0].path}` "
                             f"({near[0].file}:{near[0].line}) — same last "
                             "segment, needs confirmation")
        rows.append(row)
    return rows


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------

GENERATED = (
    "<!-- GENERATED by scripts/parity/web_native_census.py — do not hand-edit.\n"
    "     Regenerate: .venv/bin/python scripts/parity/web_native_census.py -->\n"
)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for row in rows:
        cells = [str(cell).replace("|", "\\|") for cell in row]
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out)


def census() -> dict:
    destinations = parse_master_navigation(_read(MASTER_NAV))
    deep_links = parse_linking(_read(LINKING))
    screens = list_native_screens()
    systems = native_systems()
    web_routes = collect_web_routes()
    rows = build_matrix(destinations, web_routes)

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1

    return {
        "native": {
            "destinations": [asdict(d) for d in destinations],
            "deep_links": [asdict(d) for d in deep_links],
            "screens": screens,
            "systems": systems,
        },
        "web": {
            "routes": [asdict(r) for r in web_routes],
            "html_routes": sum(1 for r in web_routes if r.kind == "html"),
            "json_routes": sum(1 for r in web_routes if r.kind == "json"),
            "redirect_routes": sum(1 for r in web_routes if r.kind == "redirect"),
            "file_routes": sum(1 for r in web_routes if r.kind == "file"),
        },
        "matrix": [asdict(r) for r in rows],
        "counts": counts,
    }


def write_docs(data: dict) -> list[str]:
    written = []
    native = data["native"]
    web = data["web"]

    # ---- native inventory
    path = os.path.join(REPO, "PULSESOC_NATIVE_PRODUCT_INVENTORY.md")
    lines = [GENERATED, "# PulseSoc native product inventory\n",
             f"- Curated destinations (`masterNavigation.ts`): **{len(native['destinations'])}**",
             f"- Deep-link paths (`linking.ts`): **{len(native['deep_links'])}**",
             f"- Screen files (`src/**/*Screen.tsx`): **{len(native['screens'])}**",
             f"- Feature systems (`src/` dirs): **{len(native['systems'])}**\n",
             "## Systems\n", ", ".join(f"`{s}`" for s in native["systems"]), "",
             "## Curated destinations\n",
             _table(["Section", "Label", "Native route", "Status", "Purpose"],
                    [[d["section"], d["label"], f"`{d['route']}`", d["status"],
                      d["description"]] for d in native["destinations"]]),
             "\n## Deep-link paths\n",
             _table(["Screen", "Path"],
                    [[d["screen"], f"`{d['path']}`"] for d in native["deep_links"]]),
             "\n## Screen files\n",
             ", ".join(f"`{s}`" for s in native["screens"]), ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    written.append(path)

    # ---- web inventory
    path = os.path.join(REPO, "PULSESOC_WEB_PRODUCT_INVENTORY.md")
    html_routes = [r for r in web["routes"] if r["kind"] == "html"]
    lines = [GENERATED, "# PulseSoc web product inventory\n",
             f"- Flask routes discovered: **{len(web['routes'])}**",
             f"- HTML page routes: **{web['html_routes']}**",
             f"- JSON API routes: **{web['json_routes']}**",
             f"- Redirect-only routes: **{web['redirect_routes']}**",
             f"- File/stream routes: **{web['file_routes']}**\n",
             "The headline finding is *not* that the web is thin. With",
             f"**{web['html_routes']}** HTML page routes against",
             f"**{len(native['screens'])}** native screens, the website has more",
             "page endpoints than the app has screens. Counting templates badly",
             "understates this: only a handful of pages come from `templates/`,",
             "because almost every page is assembled in Python by shell helpers",
             "such as `pulse_social_shell` and `admin_page_html`. Any audit that",
             "greps for `render_template` will conclude the site barely exists.\n",
             "The real gap is therefore alignment, not volume — which of the",
             "destinations the native app advertises actually resolve on the web,",
             "and whether the page behind each one carries the same product. See",
             "the parity matrix for the first question; the second needs review",
             "per surface.\n",
             "## HTML page routes\n",
             _table(["Path", "Methods", "View", "Template", "Source"],
                    [[f"`{r['path']}`", ",".join(r["methods"]), r["function"],
                      r["template"] or "inline", f"{r['file']}:{r['line']}"]
                     for r in sorted(html_routes, key=lambda r: r["path"])]), ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    written.append(path)

    # ---- parity matrix
    path = os.path.join(REPO, "PULSESOC_WEB_NATIVE_PARITY_MATRIX.md")
    counts = data["counts"]
    order = {PAGE: 0, PARTIAL: 1, MISSING: 2, BLOCKED: 3}
    matrix = sorted(data["matrix"], key=lambda r: (order.get(r["verdict"], 9), r["section"]))
    lines = [GENERATED, "# PulseSoc web <-> native parity matrix\n",
             "Scope: the curated native destination registry. Every row is a",
             "destination the app itself advertises, joined against the Flask",
             "route table by path.\n",
             "**No row in this file says PARITY, and none can.** The strongest",
             "verdict here is `PAGE`, meaning *an HTML page is served at that",
             "path*. That is a routing claim. Whether the page carries the same",
             "product as the native screen is a behavioural question this scan",
             "cannot answer, so promotion from `PAGE` to `PARITY` must come from",
             "a per-system review that actually inspects the surface.\n",
             "## Counts\n",
             _table(["Verdict", "Count"],
                    [[k, str(v)] for k, v in sorted(counts.items())]), "",
             "## Matrix\n",
             _table(["Verdict", "Section", "Destination", "Native route",
                     "Web route", "Kind", "Evidence", "Note"],
                    [[r["verdict"], r["section"], r["label"], f"`{r['native_route']}`",
                      f"`{r['web_route']}`" if r["web_route"] else "—",
                      r["web_kind"] or "—", r["web_source"] or "—", r["note"]]
                     for r in matrix]), ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))
    written.append(path)
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit machine-readable census")
    parser.add_argument("--check", action="store_true",
                        help="fail if the extractors stopped matching (CI drift gate)")
    args = parser.parse_args()

    data = census()

    if args.check:
        problems = []
        if len(data["native"]["destinations"]) < 40:
            problems.append(
                f"masterNavigation.ts yielded only {len(data['native']['destinations'])} "
                "destinations; the extractor has stopped matching.")
        if len(data["native"]["screens"]) < 100:
            problems.append(
                f"only {len(data['native']['screens'])} screen files found.")
        if len(data["web"]["routes"]) < 1000:
            problems.append(
                f"only {len(data['web']['routes'])} Flask routes found.")
        declared = count_route_decorators()
        # Only decorator-derived routes are comparable to a count of decorator
        # lines. Table routes are checked by their own floor below.
        parsed = sum(1 for route in data["web"]["routes"]
                     if route["source"] == "decorator")
        if parsed != declared:
            problems.append(
                f"parsed {parsed} of {declared} route decorators; "
                f"{declared - parsed} were dropped, so whole subsystems may be "
                "absent from the census. Check for a decorator or path form the "
                "parser does not handle.")
        tabled = sum(1 for route in data["web"]["routes"]
                     if route["source"] == "table")
        if tabled < 30:
            problems.append(
                f"only {tabled} routes found in declarative route tables; the "
                "Business OS commerce gateway alone declares ~37, so the "
                "add_url_rule extractor has stopped matching and the entire "
                "marketplace/storefront API would read as absent.")
        if data["web"]["html_routes"] < 100:
            problems.append(
                f"only {data['web']['html_routes']} HTML page routes found; the "
                "producer fixpoint has probably stopped resolving the page "
                "shells, which makes the whole site look like a JSON API.")
        if problems:
            for problem in problems:
                print(f"CENSUS_DRIFT: {problem}", file=sys.stderr)
            return 1
        print("census extractors healthy")
        return 0

    if args.json:
        print(json.dumps(data, indent=2))
        return 0

    written = write_docs(data)
    counts = data["counts"]
    print(f"native destinations : {len(data['native']['destinations'])}")
    print(f"native deep links   : {len(data['native']['deep_links'])}")
    print(f"native screens      : {len(data['native']['screens'])}")
    print(f"native systems      : {len(data['native']['systems'])}")
    print(f"web routes          : {len(data['web']['routes'])}"
          f"  (html={data['web']['html_routes']} json={data['web']['json_routes']}"
          f" redirect={data['web']['redirect_routes']}"
          f" file={data['web']['file_routes']})")
    print("parity              : " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    for path in written:
        print(f"wrote {os.path.relpath(path, REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
