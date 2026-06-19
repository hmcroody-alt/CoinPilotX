"""Security and scam-shield detection foundation for Command Center."""

from __future__ import annotations

import json
import re
import secrets
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from services import db as db_service


VALID_EVENT_TYPES = {
    "login_failed",
    "login_bruteforce",
    "suspicious_signup",
    "mass_dm",
    "spam_post",
    "phishing_link",
    "scam_keyword",
    "rapid_following",
    "rapid_commenting",
    "unusual_device",
    "unusual_country",
    "account_takeover_risk",
}
SEVERITY_ORDER = {"Low": 1, "Medium": 2, "High": 3, "Critical": 4}
URL_SHORTENERS = {
    "bit.ly",
    "tinyurl.com",
    "t.co",
    "goo.gl",
    "ow.ly",
    "is.gd",
    "buff.ly",
    "cutt.ly",
    "rebrand.ly",
    "shorturl.at",
    "s.id",
}
PHISHING_PATTERNS = (
    "walletconnect",
    "verify-wallet",
    "connect-wallet",
    "seed-phrase",
    "recovery-phrase",
    "airdrop-claim",
    "claim-reward",
    "free-mint",
    "urgent-verify",
)
SCAM_KEYWORDS = (
    "seed phrase",
    "recovery phrase",
    "private key",
    "wallet password",
    "guaranteed profit",
    "risk-free crypto",
    "double your",
    "claim airdrop",
    "verify your wallet",
    "connect wallet",
)
MAX_PAYLOAD_BYTES = 12_000


class SecurityValidationError(ValueError):
    pass


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _positive_int(value: Any, field: str, *, required: bool = False) -> int:
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError):
        parsed = 0
    if required and parsed <= 0:
        raise SecurityValidationError(f"invalid_{field}")
    return max(0, parsed)


def _clean_text(value: Any, limit: int) -> str:
    text_value = re.sub(r"<[^>]*>", "", str(value or ""))
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text_value).strip()[:limit]


def _sanitize_payload(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return None
    if isinstance(value, dict):
        output = {}
        for key, item in list(value.items())[:80]:
            safe_key = re.sub(r"[^a-zA-Z0-9_.-]", "", str(key or ""))[:80]
            if not safe_key or any(marker in safe_key.lower() for marker in ("token", "secret", "password", "credential", "private_key")):
                continue
            output[safe_key] = _sanitize_payload(item, depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_sanitize_payload(item, depth + 1) for item in list(value)[:80]]
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    return _clean_text(value, 2000)


def _payload_json(payload: dict | None) -> str:
    sanitized = _sanitize_payload(payload or {})
    serialized = json.dumps(sanitized, separators=(",", ":"), ensure_ascii=True)
    if len(serialized) <= MAX_PAYLOAD_BYTES:
        return serialized
    compact = {"truncated": True, "keys": sorted(list((payload or {}).keys()))[:40]}
    return json.dumps(_sanitize_payload(compact), separators=(",", ":"), ensure_ascii=True)


def risk_level(score: int | float) -> str:
    normalized = max(0, min(int(score or 0), 100))
    if normalized >= 75:
        return "Critical"
    if normalized >= 50:
        return "High"
    if normalized >= 25:
        return "Medium"
    return "Low"


def _open_db():
    conn = db_service.connect()
    cur = conn.cursor()
    ensure_security_schema(cur, conn)
    return conn, cur


def ensure_security_schema(cur=None, conn=None) -> bool:
    own_connection = cur is None
    if own_connection:
        conn = db_service.connect()
        cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS command_center_security_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                user_id INTEGER DEFAULT 0,
                actor_id INTEGER DEFAULT 0,
                event_type TEXT NOT NULL,
                severity TEXT NOT NULL,
                score INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT,
                status TEXT NOT NULL DEFAULT 'open',
                created_at TEXT
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS user_trust_score (
                user_id INTEGER PRIMARY KEY,
                trust_score INTEGER NOT NULL DEFAULT 100,
                risk_score INTEGER NOT NULL DEFAULT 0,
                risk_level TEXT NOT NULL DEFAULT 'Low',
                updated_at TEXT
            )
            """
        )
        for statement in (
            "CREATE INDEX IF NOT EXISTS idx_cc_security_events_user ON command_center_security_events(user_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_security_events_actor ON command_center_security_events(actor_id, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_security_events_type ON command_center_security_events(event_type, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_security_events_status ON command_center_security_events(status, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_security_events_severity ON command_center_security_events(severity, id)",
            "CREATE INDEX IF NOT EXISTS idx_cc_security_events_created ON command_center_security_events(created_at)",
        ):
            cur.execute(statement)
        if own_connection:
            conn.commit()
        return True
    finally:
        if own_connection:
            conn.close()


def scan_links(text: str) -> dict[str, Any]:
    content = str(text or "")
    urls = re.findall(r"""https?://[^\s<>'"]+|www\.[^\s<>'"]+""", content, flags=re.I)
    domains = []
    flags = []
    for raw_url in urls[:25]:
        url = raw_url if raw_url.lower().startswith(("http://", "https://")) else f"https://{raw_url}"
        parsed = urlparse(url)
        domain = (parsed.netloc or "").lower().split("@")[-1].split(":")[0].strip(".")
        if not domain:
            continue
        domains.append(domain)
        compact = f"{domain}{parsed.path}".lower()
        if domain in URL_SHORTENERS:
            flags.append({"type": "url_shortener", "domain": domain})
        if any(pattern in compact for pattern in PHISHING_PATTERNS):
            flags.append({"type": "phishing_pattern", "domain": domain})
        if re.search(r"(?:login|verify|secure|wallet)[-.][a-z0-9-]+\.(?:ru|top|xyz|info|click|live)$", domain):
            flags.append({"type": "suspicious_domain_shape", "domain": domain})
    return {"urls_detected": len(urls), "domains": sorted(set(domains)), "flags": flags}


def classify_event(event_type: str, payload: dict | None = None) -> dict[str, Any]:
    normalized_type = _clean_text(event_type, 80).lower().replace(" ", "_")
    if normalized_type not in VALID_EVENT_TYPES:
        raise SecurityValidationError("invalid_event_type")
    payload = payload or {}
    content = " ".join(str(payload.get(key) or "") for key in ("text", "body", "message", "caption", "comment", "url"))
    link_scan = scan_links(content)
    keyword_hits = [keyword for keyword in SCAM_KEYWORDS if keyword in content.lower()]
    reasons = []
    if link_scan["flags"]:
        reasons.append("suspicious_link")
    if keyword_hits:
        reasons.append("scam_keyword")
    if int(payload.get("recipient_count") or 0) >= 20:
        reasons.append("mass_recipient_count")
    if int(payload.get("repeat_count") or 0) >= 3:
        reasons.append("repeated_content")
    if int(payload.get("burst_count") or payload.get("count") or 0) >= 10:
        reasons.append("rapid_activity")
    if bool(payload.get("unusual_country")):
        reasons.append("unusual_country")
    if bool(payload.get("unusual_device")):
        reasons.append("unusual_device")
    return {"event_type": normalized_type, "reasons": reasons, "link_scan": link_scan, "keyword_hits": keyword_hits[:12]}


def score_event(event_type: str, payload: dict | None = None) -> dict[str, Any]:
    classification = classify_event(event_type, payload)
    payload = payload or {}
    event_type = classification["event_type"]
    base_scores = {
        "login_failed": 18,
        "login_bruteforce": 72,
        "suspicious_signup": 46,
        "mass_dm": 64,
        "spam_post": 48,
        "phishing_link": 76,
        "scam_keyword": 44,
        "rapid_following": 42,
        "rapid_commenting": 46,
        "unusual_device": 34,
        "unusual_country": 38,
        "account_takeover_risk": 82,
    }
    score = base_scores.get(event_type, 20)
    score += min(int(payload.get("burst_count") or payload.get("count") or 0) * 3, 24)
    score += min(int(payload.get("recipient_count") or 0), 25)
    score += min(int(payload.get("repeat_count") or 0) * 8, 24)
    score += 12 * len(classification["link_scan"]["flags"])
    score += 6 * len(classification["keyword_hits"])
    if payload.get("blocked"):
        score += 10
    normalized = max(0, min(int(score), 100))
    return {"score": normalized, "severity": risk_level(normalized), **classification}


def create_security_event(
    event_type: str,
    user_id: int = 0,
    actor_id: int = 0,
    payload: dict | None = None,
    event_id: str = "",
) -> dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    scored = score_event(event_type, payload)
    user_id = _positive_int(user_id, "user_id")
    actor_id = _positive_int(actor_id, "actor_id")
    normalized_event_id = re.sub(r"[^a-zA-Z0-9_.:-]", "", str(event_id or ""))[:160] or f"sec_evt_{secrets.token_urlsafe(18)}"
    now = iso_now()
    status = "alert" if scored["severity"] in {"High", "Critical"} else "open"
    stored_payload = {
        **payload,
        "classification": {
            "reasons": scored["reasons"],
            "link_scan": scored["link_scan"],
            "keyword_hits": scored["keyword_hits"],
        },
    }
    conn, cur = _open_db()
    try:
        cur.execute(
            """
            INSERT OR IGNORE INTO command_center_security_events
            (event_id, user_id, actor_id, event_type, severity, score, payload_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized_event_id,
                user_id,
                actor_id,
                scored["event_type"],
                scored["severity"],
                scored["score"],
                _payload_json(stored_payload),
                status,
                now,
            ),
        )
        if user_id > 0:
            risk = get_user_risk_score(user_id, cur=cur)
            trust_score = max(0, 100 - int(risk.get("risk_score") or scored["score"] or 0))
            cur.execute(
                """
                UPDATE user_trust_score
                SET trust_score=?, risk_score=?, risk_level=?, updated_at=?
                WHERE user_id=?
                """,
                (trust_score, int(risk.get("risk_score") or scored["score"]), risk.get("risk_level") or scored["severity"], now, user_id),
            )
            if int(cur.rowcount or 0) == 0:
                cur.execute(
                    """
                    INSERT INTO user_trust_score (user_id, trust_score, risk_score, risk_level, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (user_id, trust_score, int(risk.get("risk_score") or scored["score"]), risk.get("risk_level") or scored["severity"], now),
                )
        conn.commit()
        return {
            "accepted": True,
            "event_id": normalized_event_id,
            "user_id": user_id,
            "actor_id": actor_id,
            "event_type": scored["event_type"],
            "severity": scored["severity"],
            "score": scored["score"],
            "risk_level": scored["severity"],
            "status": status,
            "created_at": now,
            "reasons": scored["reasons"],
        }
    finally:
        conn.close()


def get_user_risk_score(user_id: int, cur=None) -> dict[str, Any]:
    user_id = _positive_int(user_id, "user_id", required=True)
    own_connection = cur is None
    conn = None
    if own_connection:
        conn, cur = _open_db()
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat(timespec="seconds").replace("+00:00", "Z")
        cur.execute(
            """
            SELECT score, severity
            FROM command_center_security_events
            WHERE user_id=? AND created_at>=? AND status NOT IN ('safe','dismissed')
            ORDER BY id DESC LIMIT 100
            """,
            (user_id, cutoff),
        )
        rows = [dict(row) for row in cur.fetchall()]
        if not rows:
            return {"user_id": user_id, "risk_score": 0, "risk_level": "Low", "event_count": 0}
        max_score = max(int(row.get("score") or 0) for row in rows)
        average = sum(int(row.get("score") or 0) for row in rows) / len(rows)
        score = max(max_score, min(100, int(average + min(len(rows) * 2, 20))))
        return {"user_id": user_id, "risk_score": score, "risk_level": risk_level(score), "event_count": len(rows)}
    finally:
        if own_connection and conn is not None:
            conn.close()


def get_recent_security_events(limit: int = 50, user_id: int = 0) -> dict[str, Any]:
    limit = max(1, min(int(limit or 50), 100))
    user_id = _positive_int(user_id, "user_id")
    conn, cur = _open_db()
    try:
        if user_id > 0:
            cur.execute(
                """
                SELECT event_id, user_id, actor_id, event_type, severity, score, status, created_at
                FROM command_center_security_events
                WHERE user_id=?
                ORDER BY id DESC LIMIT ?
                """,
                (user_id, limit),
            )
        else:
            cur.execute(
                """
                SELECT event_id, user_id, actor_id, event_type, severity, score, status, created_at
                FROM command_center_security_events
                ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            )
        events = [dict(row) for row in cur.fetchall()]
        counts = Counter(event.get("severity") or "Low" for event in events)
        return {"events": events, "severity_counts": dict(counts), "limit": limit}
    finally:
        conn.close()
