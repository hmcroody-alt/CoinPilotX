"""Localization engine — deterministic string-resolution projection over the
append-only logs (Stage 6).

Records declared locales and translated strings (idempotently), then computes a per-org
projection:

  * for each **active** target locale and each known ``string_key``, resolve the value by
    walking a transparent fallback chain: the exact ``(key, locale)`` value first, then
    the locale's explicit ``fallback_locale`` (if declared), then the language base of the
    locale (``en-us`` -> ``en``), then the org **default** locale;
  * when nothing in the chain has a value, the resolution is ``missing`` (value NULL) — a
    surfaced gap, never a silent blank;
  * a per-locale coverage rollup counts exact / resolved-via-fallback / missing keys.

Determinism discipline: no randomness. The newest recorded row wins for a
``(key, locale)`` (append-only corrections). Resolutions are ordered by an explicit
tie-break — match type (``missing`` < ``default`` < ``base`` < ``fallback`` < ``exact``,
so gaps and weak fallbacks surface first), then ``locale`` ascending, then ``string_key``
ascending — so the output is fully reproducible. The resolution table is a *projection*:
recomputing an org is deterministic and idempotent (it replaces that org's rows, and the
UNIQUE ``(org_id, locale, string_key)`` key guarantees exactly-one resolution per cell).

Hard boundary — nothing here renders or ships a string. A resolution is a reporting label
summarizing which value localization *would* serve; it takes no side effect.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from services import db
from services.business_os.localization import schema as _schema


# Deterministic resolution ordering: surface gaps and weak fallbacks first, exact last.
_MATCH_ORDER = {"missing": 0, "default": 1, "base": 2, "fallback": 3, "exact": 4}


class LocalizationError(ValueError):
    """Curated, user-safe validation error (never leaks internals)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _schema.utc_now_iso()


def _norm_locale(value: Any, field: str = "locale") -> str:
    """Canonicalize a locale tag: strip, ``_`` -> ``-``, lowercase. Deterministic so
    ``en_US``, ``en-us`` and ``EN-US`` all resolve identically."""
    s = str(value or "").strip().replace("_", "-").lower()
    if s == "":
        raise LocalizationError(f"{field} is required")
    return s


def _base_language(locale: str) -> str:
    """The language subtag of a locale (``en-us`` -> ``en``)."""
    return locale.split("-", 1)[0]


def _norm_ts(value: Any) -> str:
    if value in (None, ""):
        return _now()
    return str(value)


def _meta_json(meta: Any) -> Optional[str]:
    if meta in (None, ""):
        return None
    try:
        return json.dumps(meta, sort_keys=True)[:4000]
    except Exception:
        return None


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# ingest (append-only, idempotent)
# ---------------------------------------------------------------------------
def record_locale(org_id: str, locale: str, *, is_default: Any = False,
                  fallback_locale: Any = None, active: Any = True,
                  source: str = "manual", external_ref: Optional[str] = None,
                  meta: Any = None, conn=None) -> dict:
    """Declare a locale. Idempotent on ``(source, external_ref)`` (NULL ref exempt)."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise LocalizationError("org_id is required")
    locale_norm = _norm_locale(locale)
    fallback_norm = None
    if fallback_locale not in (None, ""):
        fallback_norm = _norm_locale(fallback_locale, "fallback_locale")
    is_default_i = 1 if _truthy(is_default) else 0
    active_i = 1 if _truthy(active) else 0

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            dup = conn.execute(
                "SELECT locale_id FROM business_os_l10n_locales "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"locale_id": dup["locale_id"], "recorded": False,
                        "deduped": True}
        locale_id = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_l10n_locales "
            "(locale_id,org_id,locale,is_default,fallback_locale,active,source,"
            "external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (locale_id, org_id, locale_norm, is_default_i, fallback_norm, active_i,
             source, external_ref, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"locale_id": locale_id, "locale": locale_norm, "recorded": True,
                "deduped": False}
    finally:
        if owned:
            conn.close()


def record_string(org_id: str, string_key: str, locale: str, value: str, *,
                  context: Optional[str] = None, source: str = "manual",
                  external_ref: Optional[str] = None, meta: Any = None,
                  conn=None) -> dict:
    """Append one translation fact. Idempotent on ``(source, external_ref)`` (NULL ref
    exempt). The newest row for a ``(string_key, locale)`` is the active value."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise LocalizationError("org_id is required")
    string_key = str(string_key or "").strip()
    if not string_key:
        raise LocalizationError("string_key is required")
    locale_norm = _norm_locale(locale)
    if value is None or str(value) == "":
        raise LocalizationError("value is required")
    value_s = str(value)

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT string_id FROM business_os_l10n_strings "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"string_id": existing["string_id"], "recorded": False,
                        "deduped": True}
        sid = _schema.new_id()
        conn.execute(
            "INSERT INTO business_os_l10n_strings "
            "(string_id,org_id,string_key,locale,value,context,source,external_ref,"
            "meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, org_id, string_key, locale_norm, value_s, context, source,
             external_ref, _meta_json(meta), _now()))
        if owned:
            conn.commit()
        return {"string_id": sid, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# computation (projection: replace, idempotent)
# ---------------------------------------------------------------------------
def _active_locales(conn, org_id: str) -> list:
    rows = conn.execute(
        "SELECT locale,is_default,fallback_locale FROM business_os_l10n_locales "
        "WHERE org_id = ? AND active = 1", (org_id,)).fetchall()
    return [dict(r) for r in rows]


def _default_locale(locales: list) -> Optional[str]:
    """The org default locale (is_default=1). If several, the lowest locale ascending —
    a deterministic tie-break, never ambiguous."""
    defaults = sorted(l["locale"] for l in locales if int(l["is_default"] or 0) == 1)
    return defaults[0] if defaults else None


def _value_map(conn, org_id: str) -> tuple:
    """Build ``{(string_key, locale): value}`` picking the newest row per cell, plus the
    set of all known string keys. Newest = created_at asc then string_id asc, so a later
    correction overwrites an earlier value deterministically."""
    rows = conn.execute(
        "SELECT string_key,locale,value FROM business_os_l10n_strings "
        "WHERE org_id = ? ORDER BY created_at ASC, string_id ASC",
        (org_id,)).fetchall()
    values: dict = {}
    keys: set = set()
    for r in rows:
        d = dict(r)
        values[(d["string_key"], d["locale"])] = d["value"]
        keys.add(d["string_key"])
    return values, keys


def _resolve_cell(values: dict, key: str, locale: str, fallback_locale: Optional[str],
                  default_locale: Optional[str]) -> tuple:
    """Resolve one (key, target locale). Returns ``(value_or_None, resolved_from_or_None,
    match_type)``. Chain: exact -> explicit fallback -> language base -> org default ->
    missing. The label reflects which step actually produced the value."""
    base = _base_language(locale)
    # Ordered candidate chain; skip duplicates so the label is unambiguous.
    chain = []
    for cand, label in ((locale, "exact"), (fallback_locale, "fallback"),
                        (base, "base"), (default_locale, "default")):
        if cand and cand not in [c for c, _ in chain]:
            chain.append((cand, label))
    for cand, label in chain:
        v = values.get((key, cand))
        if v is not None:
            return (v, cand, label)
    return (None, None, "missing")


def resolve_org(org_id: str, *, conn=None) -> dict:
    """Compute (and persist) the resolution projection for one org. Idempotent: replaces
    the org's rows. Returns the ranked resolution list and a per-locale coverage rollup.
    Nothing is rendered — a resolution is a reporting label."""
    org_id = str(org_id or "").strip()
    if not org_id:
        raise LocalizationError("org_id is required")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        locales = _active_locales(conn, org_id)
        default_locale = _default_locale(locales)
        values, keys = _value_map(conn, org_id)

        resolved = []
        for loc in sorted(locales, key=lambda l: l["locale"]):
            target = loc["locale"]
            fb = loc.get("fallback_locale")
            for key in keys:
                value, frm, mtype = _resolve_cell(values, key, target, fb,
                                                   default_locale)
                resolved.append({"locale": target, "string_key": key, "value": value,
                                 "resolved_from": frm, "match_type": mtype})

        # Deterministic ordering: match type (gaps first), then locale asc, then key asc.
        resolved.sort(key=lambda x: (_MATCH_ORDER.get(x["match_type"], 9),
                                     x["locale"], x["string_key"]))

        conn.execute(
            "DELETE FROM business_os_l10n_resolutions WHERE org_id = ?", (org_id,))

        now = _now()
        out = []
        for rank, d in enumerate(resolved, start=1):
            conn.execute(
                "INSERT INTO business_os_l10n_resolutions "
                "(row_id,org_id,locale,string_key,value,resolved_from,match_type,rank,"
                "computed_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (_schema.new_id(), org_id, d["locale"], d["string_key"], d["value"],
                 d["resolved_from"], d["match_type"], rank, now))
            d2 = dict(d)
            d2["rank"] = rank
            out.append(d2)
        if owned:
            conn.commit()
        return {"org_id": org_id, "count": len(out), "default_locale": default_locale,
                "resolutions": out, "coverage": _coverage(out)}
    finally:
        if owned:
            conn.close()


def _coverage(resolutions: list) -> list:
    """Per-locale rollup from a resolution list. Deterministic (locale ascending)."""
    by_locale: dict = {}
    for r in resolutions:
        c = by_locale.setdefault(r["locale"], {"locale": r["locale"], "total": 0,
                                               "exact": 0, "resolved": 0, "missing": 0})
        c["total"] += 1
        if r["match_type"] == "missing":
            c["missing"] += 1
        else:
            c["resolved"] += 1
            if r["match_type"] == "exact":
                c["exact"] += 1
    out = []
    for loc in sorted(by_locale):
        c = by_locale[loc]
        total = c["total"] or 1
        c["coverage_pct"] = round(100.0 * c["resolved"] / total, 2)
        out.append(c)
    return out


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def get_resolutions(org_id: str, *, limit: int = 500, conn=None) -> list:
    """Read the stored resolution projection for an org, best rank first."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT locale,string_key,value,resolved_from,match_type,rank "
            "FROM business_os_l10n_resolutions WHERE org_id = ? "
            "ORDER BY rank ASC LIMIT ?", (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_locales(org_id: str, *, limit: int = 200, conn=None) -> list:
    """The declared locales for an org (active first, then default first, locale asc)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT locale_id,locale,is_default,fallback_locale,active,created_at "
            "FROM business_os_l10n_locales WHERE org_id = ? "
            "ORDER BY active DESC, is_default DESC, locale ASC LIMIT ?",
            (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def list_strings(org_id: str, *, limit: int = 1000, conn=None) -> list:
    """The recorded translation facts for an org (newest first)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT string_id,string_key,locale,value,context,created_at "
            "FROM business_os_l10n_strings WHERE org_id = ? "
            "ORDER BY created_at DESC, string_id ASC LIMIT ?",
            (str(org_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()
