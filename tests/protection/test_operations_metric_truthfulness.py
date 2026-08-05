"""Operations Center metrics must not present unknowns or archives as facts.

These are regression locks for two classes of dashboard lie found during the
backend operations recovery:

1. An UNINSTRUMENTED metric rendered as a measured zero. `visitor_logs` is only
   written when PULSESOC_VISITOR_LOGGING_ENABLED is truthy, and that gate
   defaults off, so the "Visitors - 24h" KPI displayed a confident `0` next to
   hundreds of analytics events on the same screen.

2. An UNBOUNDED archive rendered as a working queue. Department warning counts
   had no time bound and (for messaging) counted every status that was not
   'sent', including 'queued' and 'skipped'. That produced counts in the tens of
   thousands that no operator action could ever reduce, and a health score that
   saturated at its floor so every loaded department looked identical.

The tests parse bot.py's source rather than importing it: importing the module
boots a 111k-line Flask monolith with ~1,538 routes and live integrations, which
is not appropriate for a protection unit test.
"""

import ast
import math
import pathlib
import re

BOT = pathlib.Path(__file__).resolve().parents[2] / "bot.py"
SOURCE = BOT.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _function(name):
    for node in ast.walk(TREE):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() not found in bot.py")


#: `ast.get_source_segment` re-splits the entire 111k-line source on every call,
#: which costs about two thirds of a second each time. Splitting once and
#: slicing turns a suite that got slower with every added test into one that
#: does not - and a protection suite people avoid running is a protection suite
#: that eventually gets deleted.
LINES = SOURCE.splitlines()


def _source_of(name):
    node = _function(name)
    return "\n".join(LINES[node.lineno - 1 : node.end_lineno])


def _code_of(name):
    """Source with the docstring removed.

    These tests assert on what a function *does*, and several of the functions
    they guard carry docstrings that quote the very defect being forbidden - the
    `/health` docstring contains the literal `"ok": True` it exists to explain.
    Matching against prose would make a correct fix fail and, worse, would push
    the next person to delete the explanation rather than the defect.
    """
    node = _function(name)
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    if not body:
        return ""
    return "\n".join(LINES[body[0].lineno - 1 : body[-1].end_lineno])


def _constant(name):
    for node in TREE.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    return ast.literal_eval(node.value)
    raise AssertionError(f"{name} not found at module scope in bot.py")


# --- 1. Uninstrumented metrics must be distinguishable from measured zero -----

def test_visitor_logging_gate_is_exposed_as_a_helper():
    """The gate must be readable by metric consumers, not inlined in the hook.

    If the env check only exists inside the before_request hook, no dashboard can
    tell "nobody visited" apart from "we are not recording visits".
    """
    helper = _source_of("visitor_logging_enabled")
    assert "PULSESOC_VISITOR_LOGGING_ENABLED" in helper


def test_admin_metrics_reports_whether_visitors_are_instrumented():
    metrics = _source_of("admin_saas_summary")
    assert "visitors_instrumented" in metrics, (
        "admin_saas_summary() must publish whether visitor logging is on, so the KPI "
        "can render 'not instrumented' instead of asserting zero visitors."
    )
    assert "visitor_logging_enabled()" in metrics


def test_visitor_kpi_renders_unknown_when_not_instrumented():
    """The rendered KPI must branch on instrumentation."""
    assert re.search(
        r"Visitors[^\n]*?&mdash;|visitors_instrumented", SOURCE
    ), "The Visitors KPI must render an explicit unknown state."
    # The branch must actually consult the flag near the KPI markup.
    kpi_index = SOURCE.index("Visitors &middot; 24h")
    window = SOURCE[kpi_index - 400 : kpi_index + 900]
    assert "visitors_instrumented" in window


def test_visitor_logging_variable_is_documented():
    env_example = (BOT.parent / ".env.example").read_text(encoding="utf-8")
    assert "PULSESOC_VISITOR_LOGGING_ENABLED" in env_example, (
        "A gate that silently disables a headline metric must be documented in "
        ".env.example or operators cannot know to turn it on."
    )


def test_landing_and_entry_pages_are_not_excluded_from_visitor_logging():
    """`/`, /login and /signup are the arrival pages - the whole visitor signal."""
    hook = _source_of("log_visitor_request")
    assert '"/login"' not in hook and "'/login'" not in hook, (
        "The landing/entry pages must no longer be skipped by visitor logging."
    )


def test_api_traffic_stays_excluded_from_per_request_visitor_logging():
    """Guard the performance decision so it is not silently reverted.

    This hook performs SELECT + INSERT + COMMIT per logged request. The native
    app drives nearly all traffic through /api/, so logging it would add a write
    to every mobile call.
    """
    hook = _source_of("log_visitor_request")
    assert "/api/" in hook


# --- 2. Warning counts are working queues, not lifetime archives -------------

def test_department_warning_counts_are_time_bounded():
    counts = _source_of("department_counts")
    assert "warning_since" in counts
    assert _constant("DEPARTMENT_WARNING_WINDOW_HOURS") > 0

    # Every warning query against a high-volume delivery table must be bounded.
    # Each cur.execute(...) block is inspected on its own, because the SQL text
    # itself contains quoted literals that defeat naive statement splitting.
    blocks = counts.split("cur.execute(")[1:]
    assert blocks, "department_counts() issues no queries"

    checked = set()
    for block in blocks:
        # A warning query is one that filters on the actionable-status allowlist.
        if "_DELIVERY_WARNING_PLACEHOLDERS" not in block:
            continue
        for table in ("alert_delivery_jobs", "notification_delivery_logs"):
            if table not in block:
                continue
            checked.add(table)
            assert "created_at>=?" in block, (
                f"Unbounded warning count against {table}: {block[:200]!r}. "
                "Lifetime archives cannot be driven to zero by fixing anything."
            )
            assert "warning_since" in block, (
                f"Warning count against {table} must use the rolling window bound."
            )

    assert checked == {"alert_delivery_jobs", "notification_delivery_logs"}, (
        f"Expected bounded warning queries against both delivery tables, checked {checked}"
    )


def test_messaging_warnings_use_an_actionable_status_allowlist():
    counts = _source_of("department_counts")
    assert "status!='sent'" not in counts.replace(" ", ""), (
        "`status != 'sent'` counts queued, pending, processing and skipped rows "
        "as warnings. Use the actionable-status allowlist instead."
    )
    statuses = set(_constant("DELIVERY_WARNING_STATUSES"))
    # In-flight and success states must never be treated as warnings.
    assert not statuses & {"sent", "created", "queued", "pending", "processing", "skipped", "delegated", "read"}
    # The states an operator can actually fix must be present.
    assert {"failed", "not_configured", "permission_denied"} <= statuses


def test_department_counts_does_not_silently_swallow_query_failures():
    """A broken query previously produced 0 warnings and 100 health.

    That is the "green light that is not proof" failure mode: the dashboard is
    most reassuring exactly when the query behind it is broken.
    """
    counts = _source_of("department_counts")
    assert "logging.exception" in counts
    assert "degraded" in counts


# --- 3. The health score must stay informative across its whole range --------

def test_health_score_is_not_saturated_by_large_backlogs():
    """The old formula clamped at 42 for any backlog of 8 or more warnings."""
    import importlib.util

    spec = ast.parse(_source_of("department_health_score"))
    namespace = {"math": math}
    exec(compile(spec, "<health>", "exec"), namespace)  # noqa: S102 - trusted local source
    score = namespace["department_health_score"]

    assert score(0, 0) == 100
    # Distinct backlogs must produce distinct scores - the whole point.
    assert score(8, 0) > score(100, 0) > score(1000, 0) > score(93222, 0)
    # A catastrophic backlog must be able to read as critical, not as a floor.
    assert score(93222, 0) < 10
    # Monotonic: more warnings never improves health.
    previous = 101
    for warnings in (0, 1, 5, 20, 100, 1000, 10000, 93222):
        current = score(warnings, 0)
        assert current <= previous
        previous = current
    # Scores stay in range.
    for warnings in (0, 1, 10**6):
        assert 0 <= score(warnings, 0) <= 100


# ---------------------------------------------------------------------------
# 3. The health endpoint must report something a failure could change.
#
# `/health` returned a literal `"ok": True`. Nothing computed it, so no outage
# could falsify it: a web process whose database had gone away answered
# `200 {"ok": true}` for as long as gunicorn stayed up, and any uptime monitor
# pointed at it reported a green platform through a total data-layer outage.
#
# This is the same shape as the two lies above - a signal wired to nothing -
# and it is the one with the widest blast radius, because it is the endpoint a
# hosting platform is most likely to be probing.
# ---------------------------------------------------------------------------


def test_health_ok_is_computed_rather_than_hardcoded():
    source = _code_of("health_check")
    assert '"ok": True' not in source, (
        "/health reports a literal ok=True again. A constant cannot report an "
        "outage; derive it from db_service.ping() and route-pack registration."
    )
    assert "db_service.ping()" in source, (
        "/health must consult the database ping. Without a dependency check its "
        "'ok' is a statement about gunicorn, not about the service."
    )
    assert "ROUTE_PACK_STATUS" in source, (
        "/health must account for route packs. They register inside "
        "`except Exception` blocks, so a subsystem can vanish silently while "
        "every other signal stays green."
    )


def test_health_stays_200_so_a_database_blip_does_not_restart_the_container():
    """Liveness and readiness are different questions; /health is the former."""
    source = _code_of("health_check")
    assert "503" not in source, (
        "/health now returns 503. That conflates liveness with readiness: a "
        "platform that restarts a container on a transient database blip makes "
        "the outage longer. The 503 belongs in /health/ready."
    )


def test_readiness_endpoint_exists_and_can_fail():
    source = _code_of("readiness_check")
    assert "503" in source, (
        "/health/ready must be able to return 503, otherwise it is one more "
        "endpoint that cannot report a problem."
    )
    assert "db_service.ping()" in source
    assert "ROUTE_PACK_STATUS" in source


def test_readiness_does_not_echo_the_database_error_text():
    """Connection errors routinely contain the full DSN, password included."""
    source = _code_of("readiness_check")
    for leak in ('database.get("error")', "database['error']", 'database["error"]'):
        assert leak not in source, (
            "/health/ready echoes the raw database error. SQLAlchemy connection "
            "failures embed the connection string, and this endpoint is "
            "unauthenticated."
        )


def test_the_database_ping_is_cached_so_health_can_be_probed_at_platform_rates():
    db_source = (
        pathlib.Path(__file__).resolve().parents[2] / "services" / "db.py"
    ).read_text(encoding="utf-8")
    assert "def ping(" in db_source, "services/db.py must expose a cheap ping()."
    assert "_PING_CACHE" in db_source, (
        "ping() must cache. An uncached probe on a per-second health check is a "
        "self-inflicted load problem, and the pressure to avoid that load is "
        "exactly what produced the hard-coded constant in the first place."
    )
    ping_source = db_source[db_source.index("def ping(") :]
    ping_source = ping_source[: ping_source.index("\ndef health_check(")]
    assert "information_schema" not in ping_source, (
        "ping() must stay cheap. The heavyweight table inventory belongs in "
        "health_check(), which /health/database serves on demand."
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
