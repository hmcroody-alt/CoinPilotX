"""Read-only reconciliation of Mux live streams/assets against pulse_live_sessions.

Never prints stream keys or credentials. Emits JSON to stdout.

    railway run --service CoinPilotX .venv/bin/python scripts/mux_replay_reconciliation.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from collections import Counter, defaultdict

MUX_API_BASE = "https://api.mux.com/video/v1"
SECRET_FIELDS = {"stream_key", "srt_passphrase"}


def _auth() -> str:
    token_id = os.getenv("MUX_TOKEN_ID", "").strip()
    token_secret = os.getenv("MUX_TOKEN_SECRET", "").strip()
    if not token_id or not token_secret:
        sys.exit("MUX_TOKEN_ID / MUX_TOKEN_SECRET are required.")
    return "Basic " + base64.b64encode(f"{token_id}:{token_secret}".encode()).decode()


def _scrub(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in SECRET_FIELDS else _scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_scrub(v) for v in obj]
    return obj


def _get(path: str, auth: str) -> dict:
    request = urllib.request.Request(MUX_API_BASE + path, headers={"Authorization": auth})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def _page_all(path: str, auth: str, *, max_pages: int = 60) -> list:
    out = []
    for page in range(1, max_pages + 1):
        joiner = "&" if "?" in path else "?"
        data = _get(f"{path}{joiner}limit=100&page={page}", auth).get("data") or []
        out.extend(_scrub(item) for item in data)
        if len(data) < 100:
            break
    return out


def fetch_mux(auth: str) -> tuple[list, list]:
    return _page_all("/live-streams", auth), _page_all("/assets", auth)


def fetch_db() -> dict:
    """Pull every Mux identity the product references, so nothing in use is called an orphan."""
    url = os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL") or ""
    if not url:
        return {}
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(url, connect_timeout=20)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT id, status, recording_status, audience, record_replay, replay_publish_enabled,
               mux_live_stream_id, mux_playback_id, mux_recording_asset_id, mux_recording_playback_id,
               mux_recording_duration_seconds, replay_url, replay_reel_id, replay_asset_id,
               agora_recording_sid, agora_recording_filename, ended_at, created_at, updated_at
        FROM pulse_live_sessions
        ORDER BY id
        """
    )
    sessions = [dict(row) for row in cur.fetchall()]

    referenced: dict[str, set] = defaultdict(set)
    for table, column in (
        ("pulse_media_assets", "mux_asset_id"),
        ("chat_media_uploads", "mux_asset_id"),
        ("pulse_live_streams", "mux_recording_asset_id"),
        ("pulse_live_streams", "mux_live_stream_id"),
    ):
        try:
            cur.execute(
                f"SELECT DISTINCT {column} AS v FROM {table} "
                f"WHERE {column} IS NOT NULL AND {column} <> ''"
            )
            referenced[column].update(str(row["v"]) for row in cur.fetchall())
        except Exception as exc:  # table/column may not exist in every environment
            conn.rollback()
            referenced.setdefault("_errors", set()).add(f"{table}.{column}: {str(exc)[:120]}")

    cur.close()
    conn.close()
    return {"sessions": sessions, "referenced": {k: sorted(v) for k, v in referenced.items()}}


def reconcile(streams: list, assets: list, db: dict) -> dict:
    sessions = db.get("sessions") or []
    referenced = db.get("referenced") or {}

    assets_by_id = {a["id"]: a for a in assets}
    assets_by_stream = defaultdict(list)
    for asset in assets:
        if asset.get("live_stream_id"):
            assets_by_stream[asset["live_stream_id"]].append(asset)

    sessions_by_stream = defaultdict(list)
    sessions_by_asset = defaultdict(list)
    for session in sessions:
        if session.get("mux_live_stream_id"):
            sessions_by_stream[str(session["mux_live_stream_id"])].append(session)
        if session.get("mux_recording_asset_id"):
            sessions_by_asset[str(session["mux_recording_asset_id"])].append(session)

    in_use_assets = set(referenced.get("mux_asset_id") or []) | set(referenced.get("mux_recording_asset_id") or [])
    in_use_streams = set(referenced.get("mux_live_stream_id") or [])

    stream_rows = []
    for stream in streams:
        sid = stream["id"]
        linked = sessions_by_stream.get(sid) or []
        recent = stream.get("recent_asset_ids") or []
        live_assets = assets_by_stream.get(sid) or []
        has_asset = bool(recent or live_assets)
        if linked or sid in in_use_streams:
            verdict = "KEEP_REFERENCED"
        elif has_asset:
            verdict = "REVIEW_HAS_ASSET_NO_DB_REF"
        else:
            verdict = "ORPHAN_NO_DB_REF_NO_ASSET"
        stream_rows.append({
            "live_stream_id": sid,
            "status": stream.get("status"),
            "created_at": stream.get("created_at"),
            "latency_mode": stream.get("latency_mode"),
            "reconnect_window": stream.get("reconnect_window"),
            "records": bool(stream.get("new_asset_settings")),
            "playback_policies": [p.get("policy") for p in stream.get("playback_ids") or []],
            "recent_asset_ids": recent,
            "db_session_ids": [s["id"] for s in linked],
            "verdict": verdict,
        })

    asset_rows = []
    for asset in assets:
        aid = asset["id"]
        linked = sessions_by_asset.get(aid) or []
        passthrough = str(asset.get("passthrough") or "")
        replay_marker = passthrough.startswith("pulse_replay:")
        marker_live_id = passthrough.split(":")[1] if replay_marker and len(passthrough.split(":")) > 1 else ""
        if linked or aid in in_use_assets:
            verdict = "KEEP_REFERENCED"
        elif asset.get("status") == "errored":
            verdict = "REVIEW_ERRORED_UNREFERENCED"
        elif replay_marker:
            verdict = "REVIEW_REPLAY_MARKER_NO_DB_REF"
        else:
            verdict = "REVIEW_UNREFERENCED"
        asset_rows.append({
            "asset_id": aid,
            "status": asset.get("status"),
            "created_at": asset.get("created_at"),
            "duration": asset.get("duration"),
            "live_stream_id": asset.get("live_stream_id"),
            "playback_policies": [p.get("policy") for p in asset.get("playback_ids") or []],
            "playback_ids": [p.get("id") for p in asset.get("playback_ids") or []],
            "passthrough_live_id": marker_live_id,
            "errors": asset.get("errors"),
            "db_session_ids": [s["id"] for s in linked],
            "verdict": verdict,
        })

    # Session-side breakage
    broken = []
    for session in sessions:
        if (session.get("status") or "").lower() not in {"ended", "archived"}:
            continue
        asset_id = str(session.get("mux_recording_asset_id") or "")
        problems = []
        if asset_id and asset_id not in assets_by_id:
            problems.append("db_asset_missing_in_mux")
        asset = assets_by_id.get(asset_id)
        if asset:
            if asset.get("status") == "errored":
                problems.append("mux_asset_errored")
            policies = {p.get("policy") for p in asset.get("playback_ids") or []}
            if "signed" in policies and not os.getenv("MUX_SIGNING_KEY_ID"):
                problems.append("signed_asset_but_no_signing_key")
            mux_pid = [p.get("id") for p in asset.get("playback_ids") or []]
            if session.get("mux_recording_playback_id") and session["mux_recording_playback_id"] not in mux_pid:
                problems.append("playback_id_mismatch")
            if asset.get("status") == "ready" and not str(session.get("replay_url") or "").strip():
                problems.append("asset_ready_but_no_replay_url")
            if asset.get("status") == "ready" and (session.get("recording_status") or "") not in {"mux_asset_ready", "replay_ready"}:
                problems.append("asset_ready_but_session_not_ready")
        if not asset_id and str(session.get("recording_status") or "") not in {"replay_ready", "replay_unavailable", "replay_failed", "", "pending"}:
            problems.append("ended_without_asset")
        if problems:
            broken.append({
                "live_id": session["id"],
                "status": session.get("status"),
                "recording_status": session.get("recording_status"),
                "audience": session.get("audience"),
                "mux_live_stream_id": session.get("mux_live_stream_id"),
                "mux_recording_asset_id": asset_id,
                "mux_recording_playback_id": session.get("mux_recording_playback_id"),
                "has_replay_url": bool(str(session.get("replay_url") or "").strip()),
                "replay_reel_id": session.get("replay_reel_id"),
                "ended_at": str(session.get("ended_at") or ""),
                "problems": problems,
            })

    return {
        "summary": {
            "mux_live_streams": len(streams),
            "mux_live_stream_status": dict(Counter(s.get("status") for s in streams)),
            "mux_assets": len(assets),
            "mux_asset_status": dict(Counter(a.get("status") for a in assets)),
            "db_sessions": len(sessions),
            "db_sessions_ended": sum(1 for s in sessions if (s.get("status") or "").lower() in {"ended", "archived"}),
            "db_recording_status": dict(Counter(str(s.get("recording_status") or "") for s in sessions)),
            "stream_verdicts": dict(Counter(r["verdict"] for r in stream_rows)),
            "asset_verdicts": dict(Counter(r["verdict"] for r in asset_rows)),
            "broken_sessions": len(broken),
            "broken_problem_counts": dict(Counter(p for b in broken for p in b["problems"])),
            "signing_key_configured": bool(os.getenv("MUX_SIGNING_KEY_ID") and os.getenv("MUX_SIGNING_PRIVATE_KEY")),
        },
        "streams": stream_rows,
        "assets": asset_rows,
        "broken_sessions": broken,
        "db_errors": sorted(referenced.get("_errors") or []),
    }


def main() -> None:
    auth = _auth()
    streams, assets = fetch_mux(auth)
    db = fetch_db()
    report = reconcile(streams, assets, db)
    json.dump(report, sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
