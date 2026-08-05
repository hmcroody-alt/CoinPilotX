"""The readiness registry must measure its claims, not merely assert them.

`services/backend_management_registry.py` is a tuple of 151 hand-written
`BackendFeature(...)` literals. Before this suite existed, `launch_readiness()`
read those literals back out and rendered them on the Command Center as system
state. Nothing verified that a declared admin route was ever registered, or
that a declared audit table was ever created. The consequence was structural:
no code path in the module could produce a blocked feature, so the Launch
Readiness card could only ever report "Blocked: 0" - including while an
optional route pack had failed to register and its surfaces were returning 404.

Two real defects found this way, both locked below:

  * `ads.delivery` and `ads.sponsored_layers` - both launch-critical, both
    status "active" - pointed at `/admin/pulse-ads-delivery-intelligence`,
    which is not implemented anywhere in the repository.
  * Seven features declared audit tables that no `CREATE TABLE` ever creates
    (`command_center_security_events`, `command_center_ai_events`,
    `pulse_ad_review_logs`, `ai_usage_logs`, `music_review_logs`), plus two
    that named the `posts` content table as their audit target.

The same class of defect existed in the Command Center's own metric strip:
`backend_command_safe_scalar` swallowed every failure and returned 0, so seven
metrics querying non-existent tables rendered as confident zeros.

These tests parse sources rather than importing bot.py, which would boot a
111k-line Flask monolith with ~1,538 routes and live integrations.
"""

import ast
import importlib.util
import pathlib
import re
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[2]
BOT = ROOT / "bot.py"
REGISTRY_PATH = ROOT / "services" / "backend_management_registry.py"
BOT_SOURCE = BOT.read_text(encoding="utf-8")


def _load_registry():
    """Import the registry without pulling in the rest of the service package."""
    if "services.backend_management_registry" in sys.modules:
        return sys.modules["services.backend_management_registry"]
    if "services" not in sys.modules:
        package = types.ModuleType("services")
        package.__path__ = [str(ROOT / "services")]
        sys.modules["services"] = package
    if "services.db" not in sys.modules:
        db_stub = types.ModuleType("services.db")
        db_stub.IS_POSTGRES = False
        sys.modules["services.db"] = db_stub
    spec = importlib.util.spec_from_file_location(
        "services.backend_management_registry", REGISTRY_PATH
    )
    module = importlib.util.module_from_spec(spec)
    # The dataclass decorator resolves annotations through sys.modules, so the
    # module has to be registered before it is executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo_python_files():
    files = [BOT] + sorted((ROOT / "services").glob("*.py")) + sorted(ROOT.glob("*.py"))
    for name in ("pulse_communications_v2", "models", "migrations", "backend"):
        directory = ROOT / name
        if directory.exists():
            files.extend(directory.rglob("*.py"))
    return files


def _registered_routes():
    """Every URL rule declared anywhere in the backend.

    Covers `.route(...)` as well as the method shorthands (`.get`, `.post`, ...)
    used by the communications blueprint.
    """
    pattern = re.compile(r"""\.(?:route|get|post|put|delete|patch)\(\s*(['"])(/.+?)\1""")
    rules = set()
    for path in _repo_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        rules.update(match.group(2) for match in pattern.finditer(text))
    return rules


def _created_tables():
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?([A-Za-z_][A-Za-z0-9_]*)", re.I
    )
    tables = set()
    for path in _repo_python_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        tables.update(match.group(1).lower() for match in pattern.finditer(text))
    return tables


def _bot_function_source(name):
    tree = ast.parse(BOT_SOURCE)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(BOT_SOURCE, node)
    raise AssertionError(f"{name}() not found in bot.py")


# --- 1. Every declared surface must actually exist ---------------------------

def test_every_declared_admin_route_is_implemented():
    registry = _load_registry()
    rules = _registered_routes()
    verified = registry.verify_features(registered_rules=sorted(rules))
    unreachable = [item for item in verified if item.get("route_registered") is False]
    assert not unreachable, (
        "Registry features declare admin routes that no decorator implements: "
        + ", ".join(f"{item['feature_key']} -> {item['route']}" for item in unreachable)
    )


def test_every_declared_audit_table_is_created_somewhere():
    registry = _load_registry()
    tables = _created_tables()
    verified = registry.verify_features(existing_tables=sorted(tables))
    missing = [item for item in verified if item.get("audit_table_exists") is False]
    assert not missing, (
        "Registry features name audit tables that are never created: "
        + ", ".join(f"{item['feature_key']} -> {item['audit_log_table']}" for item in missing)
    )


def test_content_tables_are_not_passed_off_as_audit_targets():
    """`posts` is where content lives; it is not an audit trail.

    Naming a content table as the audit target satisfies the gap audit's
    presence check while providing no accountability record at all.
    """
    registry = _load_registry()
    content_tables = {"posts", "pulse_posts", "users", "pulse_reels", "pulse_videos"}
    offenders = [
        item["feature_key"]
        for item in registry.all_features()
        if str(item.get("audit_log_table") or "").lower() in content_tables
    ]
    assert not offenders, f"Content tables declared as audit targets: {offenders}"


# --- 2. Readiness must be measured, and must be able to fail ------------------

def test_launch_readiness_can_actually_report_blocked():
    """The regression that made "Blocked: 0" meaningless.

    Every feature literal is 'active' or 'partial' - none is 'blocked' - so a
    readiness function that only reads literals is structurally incapable of
    raising an alarm. Removing one critical route from the URL map must move
    the number.
    """
    registry = _load_registry()
    routes = sorted({item["route"] for item in registry.all_features() if item.get("route")})
    baseline = registry.launch_readiness(registered_rules=routes, existing_tables=[])
    assert baseline["critical_blocked"] == 0

    critical_route = next(
        item["route"] for item in registry.all_features()
        if item.get("launch_critical") and item.get("route")
    )
    degraded = registry.launch_readiness(
        registered_rules=[route for route in routes if route != critical_route],
        existing_tables=[],
    )
    assert degraded["critical_blocked"] > 0, (
        "A launch-critical surface that is not registered must count as blocked."
    )
    assert degraded["score"] < baseline["score"]
    assert degraded["status"] == "blocked"


def test_unverified_readiness_is_labelled_unverified():
    """Called without runtime facts, readiness must not imply it checked anything."""
    registry = _load_registry()
    result = registry.launch_readiness()
    assert result["verified"] is False
    assert "UNVERIFIED" in result["verification_note"]

    verified = registry.launch_readiness(registered_rules=["/admin/command-center"], existing_tables=[])
    assert verified["verified"] is True
    assert "UNVERIFIED" not in verified["verification_note"]


def test_command_center_page_passes_runtime_facts_to_readiness():
    """The page must not fall back to the literal-only reading."""
    page = _bot_function_source("admin_command_center_page")
    assert "live_registered_rules()" in page
    assert "live_table_names()" in page

    dashboard = _bot_function_source("admin_backend_launch_readiness_page")
    assert "live_registered_rules()" in dashboard


def test_live_table_names_reports_unknown_rather_than_empty():
    """An unreadable schema must not be reported as "no tables exist"."""
    helper = _bot_function_source("live_table_names")
    assert "return None" in helper, (
        "live_table_names() must return None on failure. Returning [] would mark "
        "every audit table as missing and every feature as broken."
    )


# --- 3. Command Center metrics must not fake a zero --------------------------

def test_command_center_metrics_distinguish_unavailable_from_zero():
    scalar = _bot_function_source("backend_command_safe_scalar")
    assert "return None, False" in scalar, (
        "A metric whose query cannot be answered must report unavailable, not 0."
    )
    assert "logging.warning" in scalar, (
        "An uncomputable metric is a defect and must log at warning, not info."
    )

    renderer = _bot_function_source("backend_command_metric_html")
    assert "available" in renderer and "&mdash;" in renderer, (
        "The metric strip must render an explicit unknown for unavailable metrics."
    )


def test_command_center_metrics_only_query_tables_that_exist():
    """Seven metrics queried tables that were never created and showed 0."""
    metrics = _bot_function_source("backend_command_live_metrics")
    queried = set(re.findall(r"FROM\s+([a-z_][a-z0-9_]*)", metrics))
    assert queried, "backend_command_live_metrics() issues no queries"
    created = _created_tables()
    missing = sorted(table for table in queried if table not in created)
    assert not missing, (
        f"Command Center metrics query non-existent tables {missing}. Each one "
        "renders as a permanent zero that no operator action can change."
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
