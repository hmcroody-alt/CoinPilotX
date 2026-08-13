"""Sentinel relational edge abstraction (Stage 9).

A plain relational edge table — no graph database. Stage 0 measured event
volume and found nothing that justifies one; revisit only with data.
Edges are typed (user—device, user—ip, account—payment_method, …) and
upserted with weight + first/last seen for cheap neighborhood queries.
"""

from __future__ import annotations

from services.sentinel import store

EDGE_TYPES = (
    "used_device", "used_ip", "owns_account", "funded_by", "shares_device",
    "admin_of", "reported", "related_incident",
)


def upsert_edge(src_type: str, src_id: str, edge_type: str,
                dst_type: str, dst_id: str, weight: float = 1.0, conn=None) -> None:
    if edge_type not in EDGE_TYPES:
        raise ValueError(f"unknown edge type {edge_type!r} (SC15)")
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            """SELECT id, weight FROM sentinel_edges
               WHERE src_type=? AND src_id=? AND edge_type=? AND dst_type=? AND dst_id=?""",
            (src_type, src_id, edge_type, dst_type, dst_id))
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE sentinel_edges SET weight = weight + ?, last_seen = datetime('now') "
                "WHERE id = ?", (float(weight), int(row[0])))
        else:
            cur.execute(
                """INSERT INTO sentinel_edges
                   (src_type, src_id, edge_type, dst_type, dst_id, weight)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (src_type, src_id, edge_type, dst_type, dst_id, float(weight)))


def neighbors(src_type: str, src_id: str, edge_type: str | None = None,
              limit: int = 100, conn=None) -> list[dict]:
    limit = max(1, min(int(limit), 500))
    with store.connection(conn) as c:
        cur = c.cursor()
        if edge_type:
            cur.execute(
                "SELECT edge_type, dst_type, dst_id, weight, first_seen, last_seen "
                "FROM sentinel_edges WHERE src_type=? AND src_id=? AND edge_type=? "
                "ORDER BY weight DESC LIMIT ?", (src_type, src_id, edge_type, limit))
        else:
            cur.execute(
                "SELECT edge_type, dst_type, dst_id, weight, first_seen, last_seen "
                "FROM sentinel_edges WHERE src_type=? AND src_id=? "
                "ORDER BY weight DESC LIMIT ?", (src_type, src_id, limit))
        rows = cur.fetchall()
    return [{"edge_type": r[0], "dst_type": r[1], "dst_id": r[2],
             "weight": r[3], "first_seen": r[4], "last_seen": r[5]} for r in rows]


def shared_destination(dst_type: str, dst_id: str, edge_type: str,
                       limit: int = 100, conn=None) -> list[dict]:
    """Reverse lookup: which sources share this destination (e.g. accounts on
    one device) — the core fraud-cluster query."""
    limit = max(1, min(int(limit), 500))
    with store.connection(conn) as c:
        cur = c.cursor()
        cur.execute(
            "SELECT src_type, src_id, weight, last_seen FROM sentinel_edges "
            "WHERE dst_type=? AND dst_id=? AND edge_type=? ORDER BY weight DESC LIMIT ?",
            (dst_type, dst_id, edge_type, limit))
        rows = cur.fetchall()
    return [{"src_type": r[0], "src_id": r[1], "weight": r[2], "last_seen": r[3]}
            for r in rows]
