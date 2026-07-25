"""Performance engine — deterministic summary projection over the append-only logs
(Stage 6).

Records metric samples and optional targets (idempotently), then computes a per-org
projection:

  * for each ``(metric_key, window)`` seen in the samples, roll up count / min / max /
    mean / p50 / p95 over that cell's values;
  * label the cell against the **newest active target** for its ``metric_key`` — comparing
    the target's chosen statistic (``compare_stat``) to the warn / breach thresholds under
    the target's ``direction`` (lower-is-better or higher-is-better) — yielding
    ``ok`` / ``warn`` / ``breach``; a cell with no target is ``none``.

Determinism discipline: no randomness. Percentiles use a fixed linear-interpolation method;
rollup stats are rounded to a fixed precision. The newest recorded target row wins per
``metric_key`` (append-only corrections). Summaries are ordered by an explicit tie-break —
status (``breach`` < ``warn`` < ``ok`` < ``none``, so problems surface first), then
``metric_key`` ascending, then ``window`` ascending — so the output is fully reproducible.
The summary table is a *projection*: recomputing an org is deterministic and idempotent (it
replaces the org's rows, and the UNIQUE ``(org_id, metric_key, window)`` key guarantees
exactly-one summary per cell).

Hard boundary — nothing here renders, alerts, pages, or scales. A summary is a reporting
label describing what the samples say; it takes no side effect.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from services import db
from services.business_os.performance import schema as _schema


# Deterministic summary ordering: surface problems first, healthy/untargeted last.
_STATUS_ORDER = {"breach": 0, "warn": 1, "ok": 2, "none": 3}

_DIRECTIONS = ("lower_is_better", "higher_is_better")
_STATS = ("count", "min", "max", "mean", "p50", "p95")

# Rollup rounding precision (keeps float output stable across engines/runs).
_PRECISION = 4


class PerformanceError(ValueError):
    """Curated, user-safe validation error (never leaks internals)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _schema.utc_now_iso()


def _num(value: Any, field: str) -> float:
    """Parse a finite number or raise a curated error."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        raise PerformanceError(f"{field} is required")
    try:
        f = float(value)
    except (TypeError, ValueError):
        raise PerformanceError(f"{field} must be a number")
    if math.isnan(f) or math.isinf(f):
        raise PerformanceError(f"{field} must be finite")
    return f


def _opt_num(value: Any, field: str) -> Optional[float]:
    if value is None or (isinstance(value, str) and str(value).strip() == ""):
        return None
    return _num(value, field)


def _norm_key(value: Any, field: str) -> str:
    s = str(value or "").strip()
    if s == "":
        raise PerformanceError(f"{field} is required")
    return s


def _norm_window(value: Any) -> str:
    """Window bucket label. Empty/None means ungrouped (whole-metric rollup)."""
    if value is None:
        return ""
    return str(value).strip()


def _meta_json(meta: Any) -> Optional[str]:
    if meta in (None, ""):
        return None
    try:
        return json.dumps(meta, sort_keys=True)[:4000]
    except Exception:
        return None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


def _percentile(sorted_vals: list, q: float) -> Optional[float]:
    """Linear-interpolation percentile (q in [0, 100]) over an ascending list.
    Deterministic; matches numpy's default 'linear' method."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n == 1:
        return sorted_vals[0]
    pos = (q / 100.0) * (n - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return sorted_vals[lo]
    frac = pos - lo
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * frac


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, _PRECISION)


# ---------------------------------------------------------------------------
# ingest (append-only, idempotent)
# ---------------------------------------------------------------------------
def record_sample(org_id: str, metric_key: str, value: Any, *, window: Any = "",
                  unit: Optional[str] = None, captured_at: Any = None,
                  source: str = "manual", external_ref: Optional[str] = None,
                  meta: Any = None, conn=None) -> dict:
    """Append one metric sample. Idempotent on ``(source, external_ref)`` (NULL ref
    exempt)."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise PerformanceError("org_id is required")
    metric = _norm_key(metric_key, "metric_key")
    val = _num(value, "value")
    win = _norm_window(window)
    cap = str(captured_at).strip() if captured_at not in (None, "") else _now()

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT sample_id FROM business_os_perf_samples "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"sample_id": dup["sample_id"], "recorded": False,
                        "deduped": True}
        sid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_perf_samples "
            "(sample_id,org_id,metric_key,window,value,unit,captured_at,source,"
            "external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, org_id, metric, win, val, unit, cap, source, external_ref,
             _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"sample_id": sid, "metric_key": metric, "window": win,
                "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_target(org_id: str, metric_key: str, *, direction: str = "lower_is_better",
                  compare_stat: str = "mean", warn_threshold: Any = None,
                  breach_threshold: Any = None, active: Any = True,
                  source: str = "manual", external_ref: Optional[str] = None,
                  meta: Any = None, conn=None) -> dict:
    """Declare a target for a metric. Idempotent on ``(source, external_ref)`` (NULL ref
    exempt). The newest active row for a ``metric_key`` governs its cells."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise PerformanceError("org_id is required")
    metric = _norm_key(metric_key, "metric_key")
    direction = str(direction or "").strip().lower()
    if direction not in _DIRECTIONS:
        raise PerformanceError("direction must be lower_is_better or higher_is_better")
    compare_stat = str(compare_stat or "").strip().lower()
    if compare_stat not in _STATS:
        raise PerformanceError("compare_stat must be one of " + ", ".join(_STATS))
    warn = _opt_num(warn_threshold, "warn_threshold")
    breach = _opt_num(breach_threshold, "breach_threshold")
    if warn is None and breach is None:
        raise PerformanceError("at least one of warn_threshold or breach_threshold "
                               "is required")
    active_i = 1 if _truthy(active) else 0

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT target_id FROM business_os_perf_targets "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"target_id": dup["target_id"], "recorded": False,
                        "deduped": True}
        tid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_perf_targets "
            "(target_id,org_id,metric_key,direction,compare_stat,warn_threshold,"
            "breach_threshold,active,source,external_ref,meta_json,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (tid, org_id, metric, direction, compare_stat, warn, breach, active_i,
             source, external_ref, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"target_id": tid, "metric_key": metric, "recorded": True,
                "deduped": False}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# computation (projection: replace, idempotent)
# ---------------------------------------------------------------------------
def _newest_targets(conn, org_id: str) -> dict:
    """``{metric_key: target}`` keeping the newest active target per metric. Newest =
    created_at asc then target_id asc, so a later correction wins deterministically."""
    rows = conn.execute(
        "SELECT metric_key,direction,compare_stat,warn_threshold,breach_threshold "
        "FROM business_os_perf_targets WHERE org_id = ? AND active = 1 "
        "ORDER BY created_at ASC, target_id ASC", (org_id,)).fetchall()
    out: dict = {}
    for r in rows:
        d = dict(r)
        out[d["metric_key"]] = d
    return out


def _sample_cells(conn, org_id: str) -> dict:
    """``{(metric_key, window): [values...]}`` for an org."""
    rows = conn.execute(
        "SELECT metric_key,window,value FROM business_os_perf_samples "
        "WHERE org_id = ?", (org_id,)).fetchall()
    cells: dict = {}
    for r in rows:
        d = dict(r)
        cells.setdefault((d["metric_key"], d["window"]), []).append(float(d["value"]))
    return cells


def _rollup(values: list) -> dict:
    """Deterministic rollup stats over a non-empty value list."""
    s = sorted(values)
    n = len(s)
    mean = sum(s) / n
    return {"count": n, "min": _round(s[0]), "max": _round(s[-1]),
            "mean": _round(mean), "p50": _round(_percentile(s, 50)),
            "p95": _round(_percentile(s, 95))}


def _status_for(stats: dict, target: Optional[dict]) -> tuple:
    """Return ``(status, target_stat)``. ``none`` when no target applies."""
    if target is None:
        return ("none", None)
    stat_val = stats.get(target["compare_stat"])
    if stat_val is None:
        return ("none", None)
    stat_val = float(stat_val)
    warn = target.get("warn_threshold")
    breach = target.get("breach_threshold")
    if target["direction"] == "lower_is_better":
        if breach is not None and stat_val >= breach:
            return ("breach", stat_val)
        if warn is not None and stat_val >= warn:
            return ("warn", stat_val)
        return ("ok", stat_val)
    # higher_is_better
    if breach is not None and stat_val <= breach:
        return ("breach", stat_val)
    if warn is not None and stat_val <= warn:
        return ("warn", stat_val)
    return ("ok", stat_val)


def summarize_org(org_id: str, *, conn=None) -> dict:
    """Compute (and persist) the summary projection for one org. Idempotent: replaces the
    org's rows. Returns the ranked summary list and a status rollup. Nothing is rendered —
    a summary is a reporting label."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise PerformanceError("org_id is required")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        targets = _newest_targets(conn, org_id)
        cells = _sample_cells(conn, org_id)

        summaries = []
        for (metric, window), values in cells.items():
            stats = _rollup(values)
            status, target_stat = _status_for(stats, targets.get(metric))
            summaries.append({"metric_key": metric, "window": window, **stats,
                              "target_stat": _round(target_stat), "status": status})

        # Deterministic ordering: status (problems first), then metric asc, window asc.
        summaries.sort(key=lambda x: (_STATUS_ORDER.get(x["status"], 9),
                                      x["metric_key"], x["window"]))

        conn.execute(
            "DELETE FROM business_os_perf_summaries WHERE org_id = ?", (org_id,))

        now = _now()
        out = []
        for rank, d in enumerate(summaries, start=1):
            conn.execute(
                "INSERT INTO business_os_perf_summaries "
                "(row_id,org_id,metric_key,window,count,min_value,max_value,mean_value,"
                "p50_value,p95_value,target_stat,status,rank,computed_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_schema.new_id(), org_id, d["metric_key"], d["window"], d["count"],
                 d["min"], d["max"], d["mean"], d["p50"], d["p95"], d["target_stat"],
                 d["status"], rank, now))
            d2 = dict(d)
            d2["rank"] = rank
            out.append(d2)
        if owned:
            conn.commit()
        return {"org_id": org_id, "count": len(out), "summaries": out,
                "status_rollup": _status_rollup(out)}
    finally:
        if owned:
            conn.close()


def _status_rollup(summaries: list) -> list:
    """Count of cells by status. Deterministic (status severity order)."""
    counts = {"breach": 0, "warn": 0, "ok": 0, "none": 0}
    for s in summaries:
        counts[s["status"]] = counts.get(s["status"], 0) + 1
    return [{"status": st, "count": counts[st]}
            for st in ("breach", "warn", "ok", "none")]


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def get_summaries(org_id: str, *, limit: int = 500, conn=None) -> list:
    """Read the stored summary projection for an org, worst status / best rank first."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT metric_key,window,count,min_value,max_value,mean_value,p50_value,"
            "p95_value,target_stat,status,rank FROM business_os_perf_summaries "
            "WHERE org_id = ? ORDER BY rank ASC LIMIT ?",
            (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_targets(org_id: str, *, limit: int = 200, conn=None) -> list:
    """The declared targets for an org (active first, metric asc, newest first)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT target_id,metric_key,direction,compare_stat,warn_threshold,"
            "breach_threshold,active,created_at FROM business_os_perf_targets "
            "WHERE org_id = ? ORDER BY active DESC, metric_key ASC, created_at DESC "
            "LIMIT ?", (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_samples(org_id: str, *, limit: int = 1000, conn=None) -> list:
    """The recorded metric samples for an org (newest first)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT sample_id,metric_key,window,value,unit,captured_at,created_at "
            "FROM business_os_perf_samples WHERE org_id = ? "
            "ORDER BY created_at DESC, sample_id ASC LIMIT ?",
            (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()
