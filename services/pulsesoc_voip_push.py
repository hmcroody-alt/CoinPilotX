"""PushKit VoIP delivery for PulseSoc incoming calls.

WHAT THIS IS FOR

A normal APNs alert push cannot ring a phone. It posts a banner, and only if the
user granted notification authorization. An incoming call has to wake the app,
report itself to CallKit, and let iOS run its own ringer — over the lock screen,
over another app, under Focus rules. That path is PushKit, and PushKit only.

So this module is the PRIMARY incoming-call delivery path for any iOS device that
has registered a VoIP token. The existing alert push in
``pulsesoc_notification_system`` stays exactly where it is and keeps serving
Android, web, and iOS builds that predate PushKit — but never for the same device
in the same call. See ``claimed_device_ids`` below for how that exclusion is
enforced.

WHY THERE IS NO VoIP CERTIFICATE HERE

PulseSoc authenticates to APNs with a token-based ``.p8`` key (``APNS_KEY_ID`` /
``APNS_TEAM_ID`` / ``APNS_PRIVATE_KEY``). A ``.p8`` auth key is valid for every
push type, so VoIP needs no separate VoIP Services Certificate — only a different
topic (``<bundle>.voip``) and ``apns-push-type: voip``. The certificate flow
exists for certificate-based providers, which this is not. Reusing the key also
means there is one credential to rotate instead of two.

APPLE COMPLIANCE

A VoIP push may only be sent for a genuine incoming call, and the app must report
it to CallKit immediately on receipt. iOS terminates apps that take a VoIP push
without reporting a call. Nothing in this module may be reused for messages,
badges, sync, or marketing — that is an App Store rejection and a device-level
kill, not a style preference. The only two senders are ``ring_devices`` (a call
started) and ``cancel_devices`` (that same call stopped ringing).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from services import schema_guard

# Deterministic namespace for call UUIDs. CallKit identifies a call by UUID, but
# the canonical PulseSoc call identity is `communication_calls.public_id`
# ("call_<token>"), which is not a UUID. Deriving the UUID from the public id with
# UUIDv5 gives one stable identifier across backend, VoIP payload, CallKit, and
# the client call store without a schema change or a backfill — and, because it is
# a pure function of the call, a redelivered push maps to the same CallKit call
# instead of a second one. That is the deduplication requirement, satisfied by
# construction rather than by bookkeeping.
CALL_UUID_NAMESPACE = uuid.UUID("6f2a7b1c-5d3e-4f8a-9b6c-1e0d2a3b4c5d")

VOIP_PLATFORM = "ios"
VOIP_PROVIDER = "apns_voip"
ENVIRONMENT_SANDBOX = "sandbox"
ENVIRONMENT_PRODUCTION = "production"
VALID_ENVIRONMENTS = {ENVIRONMENT_SANDBOX, ENVIRONMENT_PRODUCTION}

APNS_HOSTS = {
    ENVIRONMENT_SANDBOX: "https://api.sandbox.push.apple.com",
    ENVIRONMENT_PRODUCTION: "https://api.push.apple.com",
}

VOIP_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS voip_push_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    device_id TEXT,
    platform TEXT,
    voip_token TEXT,
    token_hash TEXT,
    token_environment TEXT,
    app_bundle TEXT,
    app_version TEXT,
    active INTEGER DEFAULT 1,
    created_at TEXT,
    updated_at TEXT,
    last_seen_at TEXT,
    revoked_at TEXT,
    revoked_reason TEXT,
    UNIQUE(user_id, device_id, platform)
)
"""

VOIP_TOKEN_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_voip_push_tokens_user_active ON voip_push_tokens(user_id, active, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_voip_push_tokens_hash ON voip_push_tokens(token_hash)",
    "CREATE INDEX IF NOT EXISTS idx_voip_push_tokens_device ON voip_push_tokens(device_id)",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _env_value(*keys: str) -> str:
    for key in keys:
        value = os.getenv(key)
        if value:
            return value.strip()
    return ""


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _token_suffix(token: str) -> str:
    """Last 8 characters, for logs.

    A VoIP token is a device credential: anyone holding it can ring that phone
    for as long as it lives. It never goes to a log in full.
    """
    return str(token or "")[-8:]


def call_uuid_for(public_id: str) -> str:
    """The one CallKit UUID for a call, derived from its canonical public id."""
    return str(uuid.uuid5(CALL_UUID_NAMESPACE, f"pulsesoc:call:{str(public_id or '').strip()}"))


def normalize_environment(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"production", "prod", "release"}:
        return ENVIRONMENT_PRODUCTION
    if text in {"sandbox", "development", "dev", "debug"}:
        return ENVIRONMENT_SANDBOX
    return ""


def default_environment() -> str:
    """The environment to assume when a client does not report one.

    Follows the same ``APNS_USE_SANDBOX`` switch the alert sender already uses, so
    VoIP and alert pushes cannot disagree about which APNs host this deployment
    talks to.
    """
    sandbox = str(os.getenv("APNS_USE_SANDBOX", "")).strip().lower() in {"1", "true", "yes", "on"}
    return ENVIRONMENT_SANDBOX if sandbox else ENVIRONMENT_PRODUCTION


def voip_topic() -> str:
    """APNs topic for VoIP pushes: the app bundle id with a ``.voip`` suffix."""
    bundle = _env_value("APNS_VOIP_BUNDLE_ID") or _env_value("APNS_BUNDLE_ID")
    if not bundle:
        return ""
    return bundle if bundle.endswith(".voip") else f"{bundle}.voip"


def is_configured() -> bool:
    return bool(
        _env_value("APNS_TEAM_ID")
        and _env_value("APNS_KEY_ID")
        and _env_value("APNS_PRIVATE_KEY")
        and voip_topic()
    )


_SCHEMA_READY = False


def _forget_schema() -> None:
    """Forget that the table was created.

    Production never calls this. Tests do: each builds a fresh database while this
    module-level flag would otherwise persist for the whole session, and the
    second test to run would skip creation and then find no table.
    """
    global _SCHEMA_READY
    _SCHEMA_READY = False


schema_guard.register_resetter(_forget_schema)


def ensure_schema(cur: Any) -> None:
    """Create the VoIP token table.

    Takes a cursor rather than opening its own connection: passing a live route
    connection into a function that opens a second one is how this codebase has
    deadlocked on Postgres before (the DDL never commits, then the second
    connection blocks on the uncommitted catalog lock).

    Skipped once ``ensure_schema_committed`` has durably created the table in this
    process. Until then it runs unguarded, because a caller that did not arrive
    through ``_open_db`` still needs its table to exist.
    """
    if _SCHEMA_READY:
        return
    cur.execute(VOIP_TOKENS_TABLE)
    for statement in VOIP_TOKEN_INDEXES:
        try:
            cur.execute(statement)
        except Exception:  # pragma: no cover - index creation is best effort
            logging.debug("PULSESOC_VOIP_INDEX_SKIPPED statement=%s", statement[:80])


@schema_guard.run_once_per_process
def ensure_schema_committed(cur: Any, conn: Any) -> bool:
    """Create the VoIP token table once per worker, not once per call.

    ``ring_devices`` reads this table on *every* outgoing call, so the unguarded
    ``ensure_schema`` above put a ``CREATE INDEX IF NOT EXISTS`` — which takes a
    ShareLock on ``voip_push_tokens`` even when the index already exists — inside
    the same transaction as the call's own writes. That is the shape described in
    ``services/schema_guard``: two workers interleaving (write, DDL) against
    (DDL, write) is a lock cycle, and the losing thread strands its connection.

    The flag is set only after a durable commit. Caching it on DDL that a later
    ``rollback`` discards is how a worker convinces itself that a table exists
    which it then never finds.
    """
    global _SCHEMA_READY
    ensure_schema(cur)
    try:
        conn.commit()
    except Exception:
        # Leave the guard uncached and let the next caller retry. The caller
        # itself carries on exactly as unguarded code would have.
        logging.warning("PULSESOC_VOIP_SCHEMA_COMMIT_FAILED", exc_info=True)
        return False
    _SCHEMA_READY = True
    return True


def _event(name: str, **fields: Any) -> None:
    """Structured observability. Never carries a token or a key."""
    parts = " ".join(f"{key}={value}" for key, value in fields.items() if value not in (None, ""))
    logging.info("PULSESOC_VOIP %s %s", name, parts)


# --------------------------------------------------------------------------
# Token registration / rotation / revocation
# --------------------------------------------------------------------------


def register_token(
    cur: Any,
    user_id: int,
    device_id: str,
    token: str,
    *,
    environment: str = "",
    app_bundle: str = "",
    app_version: str = "",
) -> dict[str, Any]:
    """Store or refresh the VoIP token for one device.

    This deliberately writes to ``voip_push_tokens`` and never to
    ``notification_device_tokens``. They are different credentials for different
    APNs topics: writing a VoIP token into the alert registry would send alert
    pushes to a topic that rejects them and silently kill normal notifications
    for that device.
    """
    user_id = int(user_id or 0)
    device_id = str(device_id or "").strip()
    token = str(token or "").strip()
    if not user_id:
        return {"ok": False, "status": "unauthenticated", "message": "A signed-in member is required."}
    if not token:
        return {"ok": False, "status": "invalid", "message": "A VoIP token is required."}
    if not device_id:
        return {"ok": False, "status": "invalid", "message": "A device id is required."}

    env = normalize_environment(environment) or default_environment()
    now = _now()
    ensure_schema(cur)

    # A device that moves to another account must not keep ringing for the old
    # one. The token is the device's identity here, so claiming it for this user
    # also releases every other holder of the same token.
    cur.execute(
        """
        UPDATE voip_push_tokens
        SET active=0, revoked_at=?, revoked_reason='claimed_by_another_account', updated_at=?
        WHERE token_hash=? AND user_id<>?
        """,
        (now, now, _token_hash(token), user_id),
    )

    cur.execute(
        "SELECT id FROM voip_push_tokens WHERE user_id=? AND device_id=? AND platform=? LIMIT 1",
        (user_id, device_id, VOIP_PLATFORM),
    )
    existing = cur.fetchone()
    if existing:
        row_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
        cur.execute(
            """
            UPDATE voip_push_tokens
            SET voip_token=?, token_hash=?, token_environment=?, app_bundle=?, app_version=?,
                active=1, revoked_at=NULL, revoked_reason=NULL, updated_at=?, last_seen_at=?
            WHERE id=?
            """,
            (token, _token_hash(token), env, app_bundle, app_version, now, now, row_id),
        )
        _event("voip_token_rotated", user_id=user_id, device_id=device_id, environment=env, token_suffix=_token_suffix(token))
        return {"ok": True, "status": "rotated", "environment": env}

    cur.execute(
        """
        INSERT INTO voip_push_tokens
            (user_id, device_id, platform, voip_token, token_hash, token_environment,
             app_bundle, app_version, active, created_at, updated_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (user_id, device_id, VOIP_PLATFORM, token, _token_hash(token), env, app_bundle, app_version, now, now, now),
    )
    _event("voip_token_registered", user_id=user_id, device_id=device_id, environment=env, token_suffix=_token_suffix(token))
    return {"ok": True, "status": "registered", "environment": env}


def record_environment(cur: Any, *, token: str, environment: str) -> bool:
    """Persist the APNs host that actually accepted this token.

    Keyed on ``token_hash`` rather than the raw token because that column is the
    indexed one. Best effort on purpose: if this write is lost the next push simply
    mismatches and corrects itself again, so a failure here costs one extra APNs
    request and never a missed call.
    """
    env = normalize_environment(environment)
    token = str(token or "").strip()
    if not env or not token:
        return False
    try:
        now = _now()
        cur.execute(
            "UPDATE voip_push_tokens SET token_environment=?, updated_at=? WHERE token_hash=? AND active=1",
            (env, now, _token_hash(token)),
        )
    except Exception:  # pragma: no cover - persistence is best effort
        logging.debug("PULSESOC_VOIP_ENVIRONMENT_PERSIST_SKIPPED environment=%s", env)
        return False
    _event("voip_token_environment_recorded", environment=env, token_suffix=_token_suffix(token))
    return True


def revoke_token(cur: Any, *, user_id: int = 0, token: str = "", device_id: str = "", reason: str = "revoked") -> dict[str, Any]:
    """Deactivate VoIP tokens by token, by device, or for a whole account.

    Used by explicit revoke, by logout, and by an APNs ``Unregistered`` response.
    Idempotent: revoking an already-revoked token is a success, because the caller
    wanted it not to ring and it does not ring.
    """
    ensure_schema(cur)
    now = _now()
    clauses: list[str] = []
    params: list[Any] = []
    if token:
        clauses.append("token_hash=?")
        params.append(_token_hash(token))
    if device_id:
        clauses.append("device_id=?")
        params.append(str(device_id))
    if not clauses and not user_id:
        return {"ok": False, "status": "invalid", "message": "Nothing identified for revocation."}

    where = " AND ".join(clauses) if clauses else "1=1"
    if user_id:
        where = f"({where}) AND user_id=?"
        params.append(int(user_id))

    cur.execute(
        f"UPDATE voip_push_tokens SET active=0, revoked_at=?, revoked_reason=?, updated_at=? WHERE {where} AND active=1",
        (now, str(reason or "revoked")[:80], now, *params),
    )
    affected = getattr(cur, "rowcount", 0) or 0
    _event("voip_token_revoked", user_id=user_id or "", device_id=device_id or "", reason=reason, revoked=affected)
    return {"ok": True, "status": "revoked", "revoked": int(affected)}


def revoke_for_logout(cur: Any, user_id: int, device_id: str = "") -> dict[str, Any]:
    """Logout / account switch: this device stops ringing for this account.

    With a device id, only that device is released — the account keeps ringing on
    its other phones. Without one, the account is released everywhere.
    """
    return revoke_token(cur, user_id=int(user_id or 0), device_id=str(device_id or ""), reason="logout")


def active_devices(cur: Any, user_id: int) -> list[dict[str, Any]]:
    """Every iOS device eligible to receive a VoIP push for this account."""
    ensure_schema(cur)
    cur.execute(
        """
        SELECT id, user_id, device_id, voip_token, token_environment, app_bundle, app_version
        FROM voip_push_tokens
        WHERE user_id=? AND COALESCE(active,1)=1 AND revoked_at IS NULL AND platform=?
        ORDER BY last_seen_at DESC, id DESC
        """,
        (int(user_id or 0), VOIP_PLATFORM),
    )
    rows = cur.fetchall() or []
    devices: list[dict[str, Any]] = []
    seen_tokens: set[str] = set()
    for row in rows:
        record = dict(row) if hasattr(row, "keys") else {
            "id": row[0],
            "user_id": row[1],
            "device_id": row[2],
            "voip_token": row[3],
            "token_environment": row[4],
            "app_bundle": row[5],
            "app_version": row[6],
        }
        token = str(record.get("voip_token") or "")
        if not token or token in seen_tokens:
            continue
        seen_tokens.add(token)
        devices.append(record)
    return devices


# --------------------------------------------------------------------------
# APNs VoIP delivery
# --------------------------------------------------------------------------


def _apns_jwt() -> str:
    import jwt  # imported lazily so the module loads without the dependency

    return jwt.encode(
        {"iss": _env_value("APNS_TEAM_ID"), "iat": int(datetime.now(timezone.utc).timestamp())},
        _env_value("APNS_PRIVATE_KEY").replace("\\n", "\n"),
        algorithm="ES256",
        headers={"kid": _env_value("APNS_KEY_ID")},
    )


def other_environment(env: str) -> str:
    """The APNs host that is not this one."""
    return ENVIRONMENT_SANDBOX if env == ENVIRONMENT_PRODUCTION else ENVIRONMENT_PRODUCTION


def _post_voip(token: str, payload: dict[str, Any], env: str) -> dict[str, Any]:
    """One APNs request against one host. Makes no judgement about the answer.

    ``apns-push-type: voip`` and ``apns-priority: 10`` are both mandatory for the
    VoIP topic; APNs rejects the request otherwise. ``apns-expiration: 0`` says
    "deliver now or discard" — a call that could not be delivered while it was
    ringing must not arrive later and ring for a call that already ended.

    Separated from ``send_voip_push`` so the same request can be replayed against
    the other host without the first answer having already been interpreted as a
    verdict on the token.
    """
    try:
        import httpx
    except Exception as exc:  # pragma: no cover - dependency guard
        return {"transport": "config_missing", "message": f"APNs dependency missing: {type(exc).__name__}"}

    try:
        auth_token = _apns_jwt()
        with httpx.Client(http2=True, timeout=10) as client:
            response = client.post(
                f"{APNS_HOSTS[env]}/3/device/{token}",
                headers={
                    "authorization": f"bearer {auth_token}",
                    "apns-topic": voip_topic(),
                    "apns-push-type": "voip",
                    "apns-priority": "10",
                    "apns-expiration": "0",
                },
                json=payload,
            )
    except Exception as exc:
        _event("voip_push_failed", environment=env, token_suffix=_token_suffix(token), error=type(exc).__name__)
        return {"transport": "failed", "message": str(exc)[:200], "error_type": type(exc).__name__}

    return {
        "transport": "responded",
        "http_status": response.status_code,
        "body": response.text or "",
        "apns_id": response.headers.get("apns-id", ""),
    }


def _is_environment_mismatch(attempt: dict[str, Any]) -> bool:
    """Whether this answer could just mean "right token, wrong host".

    Two answers are ambiguous, and the second one cost a device its ability to
    ring before the evidence turned up.

    ``400 BadDeviceToken`` is the obvious one: the token may be perfectly live at
    the other host.

    ``410 Unregistered`` was previously excluded here, on the reasoning that it is
    "a positive statement that this host knew the token and the app is gone." That
    is wrong, and production disproved it. Token hash ``c3bd71e1…b239`` was revoked
    as ``apns_unregistered`` at 19:02:46Z; at 19:09:43Z the same handset registered
    **the identical hash** again, because iOS still considered that token valid and
    was still handing it to the app. A token cannot be both uninstalled and live.
    What actually happened is that a *sandbox* token (the build carries
    ``aps-environment: development``) was offered to the *production* host, which
    does not know it and says ``Unregistered``. That answer means "not known here",
    which is only the same as "app is gone" when you already know you asked the
    right host — and with ``token_environment`` recorded from the deployment-wide
    default rather than from the client's real entitlement, we do not know that.

    Treating it as ambiguous is safe in the direction that matters. A genuinely
    uninstalled app is unknown to *both* hosts, so the replay is refused too and
    the token is still revoked — ``test_a_token_both_hosts_reject_is_still_dead``
    pins that, and it is the property that must not regress, because a token kept
    alive for an uninstalled app holds alert-push suppression for a handset that
    can no longer be rung.

    ``DeviceTokenNotForTopic`` stays excluded: that is an alert token on the VoIP
    topic, a client registration bug which the other host rejects identically.
    """
    http_status = int(attempt.get("http_status") or 0)
    body = str(attempt.get("body") or "")
    if http_status == 400:
        return "BadDeviceToken" in body
    if http_status == 410:
        return "Unregistered" in body
    return False


def _apns_reason(body: str) -> str:
    """The APNs ``reason`` string, which is the only field that names the fault.

    HTTP status collapses opposite diagnoses onto one number. ``BadDeviceToken``
    and ``DeviceTokenNotForTopic`` are both 400: the first means the token may be
    perfectly live at the *other* host and is worth replaying, the second means an
    alert token reached the VoIP topic and no host will ever accept it. Both end
    as ``apns_status=invalid_device`` and both revoke the token, so an event that
    records only the status leaves a revoked device permanently undiagnosable —
    you cannot tell afterwards whether to fix the host, the topic, or the client.

    Kept deliberately total: a body that is not JSON, or not an object, yields ""
    rather than raising. This runs on the failure path of a call that is already
    ringing, and a logging helper must not be the thing that breaks it.
    """
    try:
        parsed = json.loads(body or "")
    except Exception:
        return ""
    if not isinstance(parsed, dict):
        return ""
    return str(parsed.get("reason") or "")[:64]


def send_voip_push(token: str, payload: dict[str, Any], environment: str = "") -> dict[str, Any]:
    """Deliver one VoIP push, correcting the APNs host if it was guessed wrong.

    ``BadDeviceToken`` is what APNs answers both for a token that is genuinely dead
    *and* for a perfectly live token offered to the wrong host — a sandbox token
    (any build carrying ``aps-environment: development``) sent to
    api.push.apple.com, or a production token sent to the sandbox. The client
    cannot tell those two entitlements apart at runtime, which is why
    ``register_token`` falls back to ``default_environment()``; that default is
    deployment-wide, so it is simply wrong for every build signed against the other
    entitlement. A deployment with ``APNS_USE_SANDBOX`` unset therefore misroutes
    every development-signed device, which is exactly the configuration a physical
    acceptance test runs in.

    Reading that as a dead token is the expensive mistake, because ``_deliver``
    revokes it — and since alert-push suppression is conditioned on an *active*
    token, the phone then quietly reverts to the alert push and never rings through
    CallKit again. The symptom is a device that rings once and then stops, which
    reads like a CallKit bug and is not one.

    So a ``BadDeviceToken`` is replayed once against the other host. If that is
    accepted the token was live all along, and ``environment_corrected`` tells the
    caller to persist the host that worked — so the extra request is paid once per
    token rather than once per call. Only a token *both* hosts reject is dead. When
    the replay also fails, the original answer is the one reported: the diagnosis
    should name the configured environment, not the speculative one.
    """
    if not is_configured():
        return {"ok": False, "status": "config_missing", "message": "APNs VoIP is not configured."}
    token = str(token or "").strip()
    if not token:
        return {"ok": False, "status": "skipped_no_device", "message": "VoIP token missing."}

    env = normalize_environment(environment) or default_environment()
    attempt = _post_voip(token, payload, env)
    corrected = False
    # "the replay never ran" and "the replay ran and the other host refused too"
    # are different faults that produce an identical rejection. Carried into the
    # event so the distinction survives past the request.
    replay_outcome = "not_attempted"

    if _is_environment_mismatch(attempt):
        replay_env = other_environment(env)
        replay = _post_voip(token, payload, replay_env)
        replay_outcome = "rejected"
        if 200 <= int(replay.get("http_status") or 0) < 300:
            replay_outcome = "accepted"
            _event(
                "voip_push_environment_corrected",
                token_suffix=_token_suffix(token),
                was=env,
                now=replay_env,
            )
            env, attempt, corrected = replay_env, replay, True

    transport = str(attempt.get("transport") or "")
    if transport == "config_missing":
        return {"ok": False, "status": "config_missing", "message": str(attempt.get("message") or "")}
    if transport == "failed":
        return {
            "ok": False,
            "status": "failed",
            "message": str(attempt.get("message") or ""),
            "error_type": str(attempt.get("error_type") or ""),
        }

    http_status = int(attempt.get("http_status") or 0)
    body = str(attempt.get("body") or "")
    apns_id = str(attempt.get("apns_id") or "")

    if 200 <= http_status < 300:
        _event("voip_push_accepted_by_apns", environment=env, token_suffix=_token_suffix(token), apns_id=apns_id)
        sent = {"ok": True, "status": "sent", "apns_id": apns_id, "environment": env}
        if corrected:
            sent["environment_corrected"] = env
        return sent

    # Reaching here with 410 Unregistered or 400 BadDeviceToken now means *both*
    # hosts refused the token, since either answer is replayed above — so it is
    # dead rather than merely misrouted. DeviceTokenNotForTopic is not replayed:
    # it means an alert token reached the VoIP topic, a registration bug rather
    # than an uninstalled app, and is worth surfacing distinctly.
    invalid = http_status == 410 or (
        http_status == 400 and ("BadDeviceToken" in body or "DeviceTokenNotForTopic" in body)
    )
    status = "invalid_device" if invalid else "failed"
    _event(
        "voip_push_rejected",
        environment=env,
        token_suffix=_token_suffix(token),
        http_status=http_status,
        apns_status=status,
        apns_reason=_apns_reason(body),
        replay=replay_outcome,
    )
    return {"ok": False, "status": status, "http_status": http_status, "message": body[:200], "apns_id": apns_id}


# --------------------------------------------------------------------------
# Call-driven senders — the only two legitimate VoIP push triggers
# --------------------------------------------------------------------------


def _incoming_payload(call: dict[str, Any], caller_id: int, caller_name: str, conversation_id: int) -> dict[str, Any]:
    """The minimum a device needs to report a call to CallKit.

    No Agora token, no channel credential, no app certificate. The device answers
    first and *then* asks the server for media credentials over an authenticated
    request, so a captured push buys an attacker a ringing phone at worst, never
    call media.

    ``handle`` is the call UUID rather than the caller's user id: CallKit shows the
    handle in Recents and in the system call log, which is not somewhere an
    internal identifier belongs.
    """
    public_id = str(call.get("public_id") or call.get("id") or "")
    call_type = str(call.get("call_type") or "audio").lower()
    call_uuid = call_uuid_for(public_id)
    return {
        "event": "incoming_call",
        "uuid": call_uuid,
        "call_id": public_id,
        "caller_id": int(caller_id or 0),
        "caller_name": str(caller_name or "PulseSoc caller")[:120],
        "call_type": "video" if call_type == "video" else "audio",
        "has_video": call_type == "video",
        "handle": call_uuid,
        "conversation_id": int(conversation_id or 0),
        "sent_at": _now(),
    }


def _cancel_payload(call: dict[str, Any], reason: str) -> dict[str, Any]:
    public_id = str(call.get("public_id") or call.get("id") or "")
    return {
        "event": "cancel_call",
        "uuid": call_uuid_for(public_id),
        "call_id": public_id,
        "reason": str(reason or "cancelled")[:60],
        "sent_at": _now(),
    }


def _deliver(cur: Any, user_id: int, devices: list[dict[str, Any]], payload: dict[str, Any], kind: str) -> dict[str, Any]:
    claimed: list[str] = []
    results: list[dict[str, Any]] = []
    for device in devices:
        token = str(device.get("voip_token") or "")
        result = send_voip_push(token, payload, str(device.get("token_environment") or ""))
        results.append({"device_id": device.get("device_id"), "status": result.get("status")})
        if result.get("environment_corrected"):
            # The recorded host was wrong and the other one worked. Write that down
            # now so the next call costs one request instead of two.
            record_environment(cur, token=token, environment=str(result.get("environment_corrected")))
        if result.get("status") == "invalid_device":
            revoke_token(cur, user_id=int(user_id), token=token, reason="apns_unregistered")
            continue
        if result.get("ok"):
            # Only a device APNs actually accepted is excluded from the alert
            # push. A failed VoIP send must fall back, or the call silently
            # reaches nobody — which is worse than a duplicate banner.
            claimed.append(str(device.get("device_id") or ""))
    _event(
        f"voip_{kind}_dispatched",
        user_id=user_id,
        call_id=payload.get("call_id"),
        devices=len(devices),
        claimed=len(claimed),
    )
    return {"claimed_device_ids": [d for d in claimed if d], "results": results}


def ring_devices(cur: Any, call: dict[str, Any], recipient_id: int, caller_id: int, caller_name: str = "") -> dict[str, Any]:
    """Ring every eligible iOS device for one recipient.

    Returns the device ids that APNs accepted. Those devices are excluded from the
    normal alert push for this call so a phone cannot ring twice.
    """
    if not is_configured():
        return {"claimed_device_ids": [], "results": [], "status": "config_missing"}
    devices = active_devices(cur, int(recipient_id))
    if not devices:
        return {"claimed_device_ids": [], "results": [], "status": "no_voip_device"}
    payload = _incoming_payload(call, int(caller_id), caller_name, int(call.get("conversation_id") or 0))
    _event("voip_push_requested", user_id=recipient_id, call_id=payload["call_id"], call_uuid=payload["uuid"], devices=len(devices))
    return {**_deliver(cur, int(recipient_id), devices, payload, "ring"), "status": "dispatched"}


def cancel_devices(
    cur: Any,
    call: dict[str, Any],
    recipient_id: int,
    reason: str = "cancelled",
    exclude_device_ids: Any = None,
) -> dict[str, Any]:
    """Tell a ringing device the call is over so CallKit tears its UI down.

    Without this the callee keeps a full-screen system call UI for a call that no
    longer exists, and the only way out is to answer a dead call. This is a
    genuine call event, so it is a legitimate VoIP push.

    ``exclude_device_ids`` exists for answered-elsewhere. When a user answers on
    their phone, their iPad must stop ringing — but the cancel carries the same
    call UUID as the answered call, so sending it to the phone that just answered
    would end the call the user is now on. The answering device excludes itself.
    """
    if not is_configured():
        return {"claimed_device_ids": [], "results": [], "status": "config_missing"}
    excluded = {str(value) for value in (exclude_device_ids or []) if str(value or "").strip()}
    devices = [d for d in active_devices(cur, int(recipient_id)) if str(d.get("device_id") or "") not in excluded]
    if not devices:
        return {"claimed_device_ids": [], "results": [], "status": "no_voip_device"}
    return {**_deliver(cur, int(recipient_id), devices, _cancel_payload(call, reason), "cancel"), "status": "dispatched"}


def claimed_metadata_key() -> str:
    """Metadata key carrying VoIP-claimed device ids into the alert dispatcher."""
    return "voip_claimed_device_ids"


def claimed_device_ids(notification: dict[str, Any]) -> set[str]:
    """Read back the device ids that already received a VoIP push for this call."""
    metadata = notification.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except Exception:
            metadata = {}
    if not isinstance(metadata, dict):
        return set()
    raw = metadata.get(claimed_metadata_key()) or []
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {str(value) for value in raw if str(value or "").strip()}
