"""Recommendations engine — deterministic ranking over the append-only logs (Stage 6).

Records items and implicit-feedback interactions (idempotently), then computes a
per-user ranked recommendation list under a named, fully transparent model:

  * ``popularity``    — global weighted engagement count; a cold-start / fallback lens.
  * ``content_based`` — overlap between a candidate item's tags and the tag profile
                        the user built through prior positive engagement.
  * ``collaborative`` — item-to-item co-occurrence (Jaccard over the user sets who
                        engaged each item); "users who engaged what you did also
                        engaged this".
  * ``hybrid``        — a normalized blend of the three (0.2 popularity / 0.4 content
                        / 0.4 collaborative).

Determinism discipline: every model yields a stable ranked list with an explicit
tie-break (score descending, then ``item_id`` ascending) — no randomness. The
recommendation table is a *projection*: recomputing a user under a model is
deterministic and idempotent (it replaces that user/model's rows, and the UNIQUE key
guarantees exactly-one row per item). Items the user has already engaged (any
interaction, including ``dismiss``) are excluded from their recommendations.

Nothing here moves money or takes an action. A recommendation is a suggestion — a
reporting quantity, not an instruction.
"""

from __future__ import annotations

import json
from decimal import Decimal, getcontext
from typing import Any, Optional

from services import db
from services.business_os.recommendations import schema as _schema


getcontext().prec = 40

VALID_MODELS = ("popularity", "content_based", "collaborative", "hybrid")
_INTERACTION_TYPES = ("view", "click", "like", "purchase", "dismiss")
# Positive-signal types contribute affinity; ``dismiss`` only marks an item "seen"
# (so it is excluded from the user's recommendations) and adds no positive weight.
_POSITIVE = ("view", "click", "like", "purchase")

# Hybrid blend weights (transparent, fixed).
_HYBRID_W = {"popularity": Decimal("0.2"),
             "content_based": Decimal("0.4"),
             "collaborative": Decimal("0.4")}

_SCORE_Q = Decimal("0.000001")


class RecommendationError(ValueError):
    """Curated, user-safe validation error (never leaks internals)."""


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return _schema.utc_now_iso()


def _norm_ts(value: Any) -> str:
    if value in (None, ""):
        return _now()
    return str(value)


def _fmt(score: Decimal) -> str:
    return format(score.quantize(_SCORE_Q), "f")


def _tags_of(raw: Any) -> list:
    """Normalize a tags value (JSON string or list) into a sorted, de-duped list of
    lowercased non-empty string tags."""
    vals = raw
    if isinstance(raw, str):
        try:
            vals = json.loads(raw)
        except Exception:
            vals = [raw]
    if not isinstance(vals, (list, tuple, set)):
        return []
    out = set()
    for v in vals:
        s = str(v).strip().lower()
        if s:
            out.add(s)
    return sorted(out)


# ---------------------------------------------------------------------------
# ingest (append-only, idempotent)
# ---------------------------------------------------------------------------
def record_item(item_id: str, item_type: str, *, title: Optional[str] = None,
                category: Optional[str] = None, tags: Any = None,
                owner_ref: Optional[str] = None, source: str = "manual",
                external_ref: Optional[str] = None, meta: Any = None,
                conn=None) -> dict:
    """Register a recommendable item. If the ``item_id`` already exists it is a no-op
    (``deduped=True``); a replayed ``(source, external_ref)`` is likewise a no-op."""
    item_id = str(item_id or "").strip()
    if not item_id:
        raise RecommendationError("item_id is required")
    item_type = str(item_type or "").strip()
    if not item_type:
        raise RecommendationError("item_type is required")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        existing = conn.execute(
            "SELECT item_id FROM business_os_rec_items WHERE item_id = ?",
            (item_id,)).fetchone()
        if existing is not None:
            return {"item_id": item_id, "recorded": False, "deduped": True}
        if external_ref is not None:
            dup = conn.execute(
                "SELECT item_id FROM business_os_rec_items "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if dup is not None:
                return {"item_id": dup["item_id"], "recorded": False,
                        "deduped": True}
        tags_list = _tags_of(tags)
        meta_json = None
        if meta not in (None, ""):
            try:
                meta_json = json.dumps(meta, sort_keys=True)[:4000]
            except Exception:
                meta_json = None
        conn.execute(
            "INSERT INTO business_os_rec_items "
            "(item_id,item_type,title,category,tags_json,owner_ref,source,"
            "external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (item_id, item_type, title, category,
             json.dumps(tags_list) if tags_list else None, owner_ref, source,
             external_ref, meta_json, _now()))
        if owned:
            conn.commit()
        return {"item_id": item_id, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


def record_interaction(user_id: str, item_id: str, interaction_type: str, *,
                       weight: Any = 1, occurred_at: Any = None,
                       source: str = "manual", external_ref: Optional[str] = None,
                       meta: Any = None, conn=None) -> dict:
    """Append one implicit-feedback interaction. Idempotent on
    ``(source, external_ref)`` (NULL ref exempt)."""
    if not user_id:
        raise RecommendationError("user_id is required")
    item_id = str(item_id or "").strip()
    if not item_id:
        raise RecommendationError("item_id is required")
    if interaction_type not in _INTERACTION_TYPES:
        raise RecommendationError(f"unknown interaction_type: {interaction_type!r}")
    try:
        weight = int(weight)
    except (TypeError, ValueError):
        raise RecommendationError("weight must be an integer")
    if weight < 0:
        raise RecommendationError("weight must be non-negative")

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if external_ref is not None:
            existing = conn.execute(
                "SELECT interaction_id FROM business_os_rec_interactions "
                "WHERE source = ? AND external_ref = ?",
                (source, external_ref)).fetchone()
            if existing is not None:
                return {"interaction_id": existing["interaction_id"],
                        "recorded": False, "deduped": True}
        iid = _schema.new_id()
        meta_json = None
        if meta not in (None, ""):
            try:
                meta_json = json.dumps(meta, sort_keys=True)[:4000]
            except Exception:
                meta_json = None
        conn.execute(
            "INSERT INTO business_os_rec_interactions "
            "(interaction_id,user_id,item_id,interaction_type,weight,occurred_at,"
            "source,external_ref,meta_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (iid, str(user_id), item_id, interaction_type, weight,
             _norm_ts(occurred_at), source, external_ref, meta_json, _now()))
        if owned:
            conn.commit()
        return {"interaction_id": iid, "recorded": True, "deduped": False}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# scoring internals (read-only over the logs)
# ---------------------------------------------------------------------------
def _catalog(conn) -> dict:
    """item_id -> {item_type, category, tags:list} for all catalog items."""
    rows = conn.execute(
        "SELECT item_id,item_type,category,tags_json FROM business_os_rec_items"
    ).fetchall()
    out = {}
    for r in rows:
        d = dict(r)
        out[d["item_id"]] = {"item_type": d.get("item_type"),
                             "category": d.get("category"),
                             "tags": _tags_of(d.get("tags_json"))}
    return out


def _user_signals(conn, user_id: str) -> tuple:
    """Return (seen:set, positives:list[(item_id, eff_weight)]) for a user. ``seen``
    includes every item the user touched (dismiss included) so it is excluded from
    their recommendations; ``positives`` carries only positive-type affinity."""
    rows = conn.execute(
        "SELECT item_id,interaction_type,weight FROM business_os_rec_interactions "
        "WHERE user_id = ?", (str(user_id),)).fetchall()
    seen = set()
    positives = []
    for r in rows:
        d = dict(r)
        seen.add(d["item_id"])
        if d["interaction_type"] in _POSITIVE:
            positives.append((d["item_id"], int(d["weight"])))
    return seen, positives


def _global_item_stats(conn) -> tuple:
    """Return (weight_total, item_users) over positive interactions:
    weight_total[item_id] = summed positive weight; item_users[item_id] = set of
    user_ids with a positive interaction on it."""
    rows = conn.execute(
        "SELECT user_id,item_id,interaction_type,weight "
        "FROM business_os_rec_interactions").fetchall()
    weight_total = {}
    item_users = {}
    for r in rows:
        d = dict(r)
        if d["interaction_type"] not in _POSITIVE:
            continue
        iid = d["item_id"]
        weight_total[iid] = weight_total.get(iid, 0) + int(d["weight"])
        item_users.setdefault(iid, set()).add(str(d["user_id"]))
    return weight_total, item_users


def _score_popularity(seen, weight_total) -> dict:
    """item_id -> Decimal raw popularity score (summed positive weight), excluding
    items the user has already seen."""
    return {iid: Decimal(w) for iid, w in weight_total.items()
            if iid not in seen and w > 0}


def _score_content(seen, positives, catalog) -> tuple:
    """Return (scores, matched_tags). Build the user's weighted tag profile from the
    tags of items they positively engaged, then score each unseen candidate by the
    profile-weight of its overlapping tags."""
    profile = {}
    for iid, w in positives:
        meta = catalog.get(iid)
        if not meta:
            continue
        for t in meta["tags"]:
            profile[t] = profile.get(t, 0) + int(w)
    scores = {}
    matched = {}
    if not profile:
        return scores, matched
    for iid, meta in catalog.items():
        if iid in seen:
            continue
        overlap = [t for t in meta["tags"] if t in profile]
        if not overlap:
            continue
        s = sum(profile[t] for t in overlap)
        if s > 0:
            scores[iid] = Decimal(s)
            matched[iid] = overlap
    return scores, matched


def _score_collaborative(seen, positives, item_users) -> dict:
    """item_to_item co-occurrence: for each item the user positively engaged (seed),
    add the Jaccard similarity between the seed's user set and each candidate's user
    set. Deterministic; excludes seen items."""
    seeds = sorted({iid for iid, _ in positives})
    scores = {}
    for seed in seeds:
        seed_users = item_users.get(seed)
        if not seed_users:
            continue
        for cand, cand_users in item_users.items():
            if cand in seen or cand == seed:
                continue
            inter = len(seed_users & cand_users)
            if inter == 0:
                continue
            union = len(seed_users | cand_users)
            if union == 0:
                continue
            jac = Decimal(inter) / Decimal(union)
            scores[cand] = scores.get(cand, Decimal(0)) + jac
    return scores


def _normalize(scores: dict) -> dict:
    """Scale a score dict into [0,1] by its max (deterministic; empty -> empty)."""
    if not scores:
        return {}
    mx = max(scores.values())
    if mx <= 0:
        return {}
    return {k: (v / mx) for k, v in scores.items()}


# ---------------------------------------------------------------------------
# computation (projection: replace, idempotent)
# ---------------------------------------------------------------------------
def compute_recommendations(user_id: str, model: str = "hybrid", *,
                            limit: int = 50, conn=None) -> dict:
    """Compute (and persist) the ranked recommendation list for one user under
    ``model``. Idempotent: replaces the user/model projection rows. Returns the
    ranked list."""
    if model not in VALID_MODELS:
        raise RecommendationError(f"unknown model: {model!r}")
    if not user_id:
        raise RecommendationError("user_id is required")
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 50
    if limit <= 0:
        limit = 50

    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        catalog = _catalog(conn)
        seen, positives = _user_signals(conn, user_id)
        weight_total, item_users = _global_item_stats(conn)

        reason_default = model
        if model == "popularity":
            raw = _score_popularity(seen, weight_total)
            reasons = {iid: "popular" for iid in raw}
        elif model == "content_based":
            raw, matched = _score_content(seen, positives, catalog)
            reasons = {iid: "matches your interests: " + ", ".join(matched[iid][:5])
                       for iid in raw}
        elif model == "collaborative":
            raw = _score_collaborative(seen, positives, item_users)
            reasons = {iid: "users like you also engaged" for iid in raw}
        else:  # hybrid
            pop_n = _normalize(_score_popularity(seen, weight_total))
            con_raw, _m = _score_content(seen, positives, catalog)
            con_n = _normalize(con_raw)
            col_n = _normalize(_score_collaborative(seen, positives, item_users))
            raw = {}
            for iid in set(pop_n) | set(con_n) | set(col_n):
                raw[iid] = (_HYBRID_W["popularity"] * pop_n.get(iid, Decimal(0))
                            + _HYBRID_W["content_based"] * con_n.get(iid, Decimal(0))
                            + _HYBRID_W["collaborative"] * col_n.get(iid, Decimal(0)))
            raw = {iid: s for iid, s in raw.items() if s > 0}
            reasons = {iid: "blended signal" for iid in raw}

        # Deterministic ordering: score desc, then item_id asc.
        ordered = sorted(raw.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

        # Recompute is a replace: clear the prior projection for this user/model.
        conn.execute(
            "DELETE FROM business_os_rec_recommendations "
            "WHERE user_id = ? AND model = ?", (str(user_id), model))

        now = _now()
        out = []
        for rank, (iid, score) in enumerate(ordered, start=1):
            meta = catalog.get(iid, {})
            conn.execute(
                "INSERT INTO business_os_rec_recommendations "
                "(rec_id,user_id,model,item_id,item_type,category,score,rank,reason,"
                "computed_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_schema.new_id(), str(user_id), model, iid, meta.get("item_type"),
                 meta.get("category"), _fmt(score), rank,
                 reasons.get(iid, reason_default), now))
            out.append({"item_id": iid, "item_type": meta.get("item_type"),
                        "category": meta.get("category"), "score": _fmt(score),
                        "rank": rank, "reason": reasons.get(iid, reason_default)})
        if owned:
            conn.commit()
        return {"user_id": str(user_id), "model": model, "count": len(out),
                "recommendations": out}
    finally:
        if owned:
            conn.close()


def recompute_user(user_id: str, models=None, *, limit: int = 50, conn=None) -> dict:
    """Recompute a user under several models (default: all)."""
    models = list(models or VALID_MODELS)
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        results = {}
        for m in models:
            results[m] = compute_recommendations(user_id, m, limit=limit, conn=conn)
        if owned:
            conn.commit()
        return {"user_id": str(user_id), "models": results}
    finally:
        if owned:
            conn.close()


# ---------------------------------------------------------------------------
# reporting (read-only)
# ---------------------------------------------------------------------------
def get_recommendations(user_id: str, model: str = "hybrid", *, limit: int = 50,
                        conn=None) -> list:
    """Read the stored projection for a user/model, best rank first."""
    if model not in VALID_MODELS:
        raise RecommendationError(f"unknown model: {model!r}")
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT item_id,item_type,category,score,rank,reason "
            "FROM business_os_rec_recommendations "
            "WHERE user_id = ? AND model = ? ORDER BY rank ASC LIMIT ?",
            (str(user_id), model, int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def user_interactions(user_id: str, *, limit: int = 200, conn=None) -> list:
    """The ordered interaction history for a user (oldest-first)."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT interaction_id,item_id,interaction_type,weight,occurred_at "
            "FROM business_os_rec_interactions WHERE user_id = ? "
            "ORDER BY occurred_at ASC, created_at ASC LIMIT ?",
            (str(user_id), int(limit))).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owned:
            conn.close()


def item_popularity(*, limit: int = 100, conn=None) -> dict:
    """Operator report: items ranked by global positive weight."""
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        weight_total, _ = _global_item_stats(conn)
        ordered = sorted(weight_total.items(), key=lambda kv: (-kv[1], kv[0]))
        return {"rows": [{"item_id": iid, "weight": int(w)}
                         for iid, w in ordered[:int(limit)]]}
    finally:
        if owned:
            conn.close()
