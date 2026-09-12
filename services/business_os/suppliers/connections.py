"""Merchant-owned CJ connections. HTTP projections never contain credentials.

All user entry points re-use canonical business RBAC and the canonical store
row *before* accessing the vault or provider. Worker-only functions intentionally
have no actor argument; they require the persisted full tenant tuple and must
never be bound to a public controller. No fallback account exists.
"""
from __future__ import annotations

import json
import math
import os
import time
import uuid
from datetime import datetime, timedelta, timezone

from services import db
from services.business_os.store import service as store_service
from services.business_os.suppliers import merchant_scope, schema, vault
from services.business_os.suppliers.errors import SupplierError

STATUSES = frozenset({"CONNECTED", "API_SUSPENDED", "REACTIVATION_REQUIRED",
                      "RATE_LIMITED", "AUTH_EXPIRED", "REAUTH_REQUIRED",
                      "PROVIDER_UNAVAILABLE", "VERIFICATION_REQUIRED"})
_SAFE_QUOTA_STATES = {"NORMAL", "CONSTRAINED", "CRITICAL", "EXHAUSTED", "UNKNOWN"}
REFRESH_WINDOW_SECONDS = 120
REFRESH_LEASE_SECONDS = 120


class SupplierConnectionError(ValueError):
    def __init__(self, message="Supplier connection is unavailable.",
                 http_status=400, code="invalid"):
        super().__init__(message)
        self.http_status = http_status
        self.code = code


def _now():
    return datetime.now(timezone.utc)


def _iso(value=None):
    return (value or _now()).isoformat(timespec="microseconds")


def _date(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result.astimezone(timezone.utc)
    except Exception:
        raise SupplierConnectionError("CJ expiry metadata is invalid.", 502,
                                      "invalid_auth_metadata") from None


def _enabled():
    if os.environ.get("BUSINESS_OS_SUPPLIERS_CJ", "").lower() not in {"1", "on", "true", "yes"}:
        raise SupplierConnectionError("CJ integration is disabled.", 503, "disabled")


def _canonical_scope(conn, business_id, store_id):
    # A seller-backed store has no Business OS row to join to; the seller record
    # is its canonical owner. Resolved here rather than only in _authorize so the
    # worker and hydrate paths, which have no actor, agree on the merchant id.
    owner = merchant_scope.seller_scope_owner(business_id, store_id)
    if owner is not None:
        try:
            return merchant_scope.seller_merchant(conn, owner)
        except merchant_scope.ScopeError as exc:
            raise SupplierConnectionError(str(exc), exc.http_status, exc.code) from None
    row = conn.execute("SELECT b.owner_user_id, b.status AS business_status, "
                       "s.status AS store_status FROM business_os_business b "
                       "JOIN business_os_store_storefront s ON s.business_id=b.business_id "
                       "WHERE b.business_id=? AND s.storefront_id=?",
                       (business_id, store_id)).fetchone()
    if row is None:
        raise SupplierConnectionError("Store not found.", 404, "store_not_found")
    if row["business_status"] in {"archived", "suspended"} or row["store_status"] in {"archived", "suspended"}:
        raise SupplierConnectionError("Business or store access is on hold.", 403, "account_hold")
    return str(row["owner_user_id"])


def _stale_scope_denial(conn, business_id, store_id, actor_user_id):
    """Is this refusal a stale client store context rather than a tenancy breach?

    Only ever asked about a scope that names the caller themselves, so the
    answer can describe nobody else's store: a request carrying another
    merchant's scope keeps its 404 and learns nothing. When the caller's own
    canonical store has moved on — the app cached a seller-backed scope and the
    merchant has since created a Business OS workspace — "your store context is
    out of date" is the true answer, and the client can repair it by resolving
    the scope again instead of making the merchant sign in again.

    The scope that was sent is still refused. This only names the refusal.
    """
    if merchant_scope.seller_scope_owner(business_id, store_id) != str(actor_user_id or ""):
        return None
    try:
        canonical = merchant_scope.resolve(conn, actor_user_id)
    except merchant_scope.ScopeError:
        return None
    if canonical.get("status") != "ok":
        return None
    if (canonical["business_id"], canonical["store_id"]) == (str(business_id), str(store_id)):
        return None
    return SupplierConnectionError("Your store context is out of date.",
                                   409, "stale_store_context")


def _authorize(conn, business_id, store_id, actor_user_id, *, context=None, write=False):
    _enabled()
    if actor_user_id is None or not str(actor_user_id).strip():
        raise SupplierConnectionError("Authentication required.", 401, "unauthorized")
    # A merchant whose store is a marketplace seller record has no Business OS
    # row for RBAC to consult, and demanding one is the defect this branch
    # fixes. Ownership is still proved — against the authority that actually
    # owns the store — so a caller cannot reach another merchant's scope.
    owner = merchant_scope.seller_scope_owner(business_id, store_id)
    try:
        store_service._require_not_held(context)
        if owner is not None:
            merchant_scope.verify_seller_scope(conn, owner, actor_user_id)
        else:
            store_service._require_biz_permission(conn, business_id, actor_user_id,
                                                 "store.manage" if write else "store.read")
    except merchant_scope.ScopeError as exc:
        raise (_stale_scope_denial(conn, business_id, store_id, actor_user_id)
               or SupplierConnectionError(str(exc), exc.http_status, exc.code)) from None
    except store_service.StoreError as exc:
        raise SupplierConnectionError("Supplier access denied.", exc.http_status, exc.code) from None
    return _canonical_scope(conn, business_id, store_id)


def _row(conn, connection_id, business_id, store_id, merchant_id=None):
    row = conn.execute("SELECT * FROM business_os_supplier_connections WHERE id=? "
                       "AND business_id=? AND store_id=? AND provider='CJ'",
                       (connection_id, business_id, store_id)).fetchone()
    if row is None or (merchant_id is not None and row["merchant_id"] != merchant_id):
        raise SupplierConnectionError("Supplier connection not found.", 404, "not_found")
    return dict(row)


def _credential_scope(row):
    return {key: row[key] for key in ("merchant_id", "business_id", "store_id",
                                     "credential_reference")} | {"connection_id": row["id"]}


def _public(row):
    # Explicit allowlist: adding a DB column can never expose it automatically.
    out = {key: row.get(key) for key in ("id", "merchant_id", "business_id", "store_id",
        "provider", "connection_type", "external_account_id", "external_shop_id", "status",
        "access_expires_at", "refresh_expires_at", "quota_state", "last_verified_at",
        "last_sync_at", "created_at", "updated_at")}
    out["credential_present"] = bool(row.get("credential_reference"))
    out["environment"] = "SANDBOX"
    out["production_fulfillment_enabled"] = False
    try:
        quota = json.loads(row.get("quota_json") or "{}")
        out["points_info"] = {key: value for key, value in quota.items()
                              if key in {"remaining", "usedToday", "total", "qps", "retry_after"}
                              and type(value) in (int, float) and math.isfinite(value) and value >= 0}
    except (ValueError, TypeError, AttributeError):
        out["points_info"] = {}
    # `status` is the provider's verdict on this merchant's account, and every
    # value in STATUSES names something the merchant can act on at CJ. Only
    # facts of that kind may be projected onto it here.
    #
    # A previous version also mapped "we have not re-verified in five minutes"
    # onto VERIFICATION_REQUIRED -- "Your supplier account needs verifying on
    # their site." That is a claim about CJ, made from a local clock, and it was
    # inescapable: `last_verified_at` is only written on connect and on token
    # refresh, and CJ access tokens run six months, so every connection began
    # reporting a broken CJ account five minutes after setup and kept reporting
    # it until the token expired. `connectionIsUsable` requires CONNECTED, so
    # the whole dropshipping hub went to "can't load" while search, detail and
    # import all kept working. The one remedy offered -- Check connection --
    # calls `health_connection`, which on a healthy connection hydrates locally
    # and writes nothing, so tapping it could never clear the label.
    #
    # Staleness is not deleted, it is just no longer disguised: `last_verified_at`
    # is in this payload already and the Suppliers screen renders it as
    # "Checked 7m". That is the honest form of the same fact, and unlike a
    # status it does not disable the feature.
    if out["status"] == "CONNECTED":
        try:
            if _date(out["access_expires_at"]) <= _now():
                out["status"] = "AUTH_EXPIRED"
        except SupplierConnectionError:
            # An unreadable expiry means we cannot establish the credential is
            # still good, which is a genuine local fact and errs toward caution.
            out["status"] = "REAUTH_REQUIRED"
    if out["status"] in {"API_SUSPENDED", "REACTIVATION_REQUIRED"}:
        out["message"] = "CJ API access requires reactivation in your CJ account."
    return out


def get_connection(connection_id, business_id, store_id, actor_user_id, *, context=None, write=False):
    conn = db.connect()
    try:
        merchant = _authorize(conn, business_id, store_id, actor_user_id, context=context, write=write)
        return _public(_row(conn, connection_id, business_id, store_id, merchant))
    finally:
        conn.close()


def list_connections(business_id, store_id, actor_user_id, *, context=None):
    conn = db.connect()
    try:
        merchant = _authorize(conn, business_id, store_id, actor_user_id, context=context)
        rows = conn.execute("SELECT * FROM business_os_supplier_connections WHERE business_id=? "
                            "AND store_id=? AND merchant_id=? AND provider='CJ' ORDER BY created_at",
                            (business_id, store_id, merchant)).fetchall()
        return [_public(dict(row)) for row in rows]
    finally:
        conn.close()


def account_reference(open_id):
    """Keyed opaque provider identity index, never raw or enumerable openId."""
    if not isinstance(open_id, str) or not open_id or len(open_id) > 16384:
        raise SupplierConnectionError("CJ account identity is invalid.", 502, "invalid_identity")
    return vault.account_fingerprint(open_id)


def _new_adapter(api_key=None):
    from services.business_os.suppliers.cj import CJAdapter
    pending = vault.account_fingerprint(str(api_key or ""), namespace="auth")
    return CJAdapter(account_ref=pending, environment="SANDBOX")


def _release_pending_slot(adapter):
    """Best effort: failing to reclaim a slot must not replace CJ's own error.

    The merchant is already being told their key was refused, which is the
    true and useful thing. A bookkeeping failure on the way out must not
    become the message they read instead.
    """
    try:
        adapter.quota.release_pending(adapter.account_ref)
    except Exception:
        pass


def _auth_fields(auth, *, previous_open_id=None):
    try:
        open_id = auth.open_id if auth.open_id is not None else previous_open_id
        bundle = vault.SecretBundle(api_key="pending", access_token=auth.access_token,
                                    refresh_token=auth.refresh_token, open_id=open_id)
        if any(not isinstance(value, str) or not value or len(value) > 16384 for value in bundle.values()):
            raise ValueError
        access, refresh = _date(auth.access_expires_at), _date(auth.refresh_expires_at)
        if access <= _now() or refresh <= _now():
            raise ValueError
        if previous_open_id is not None and open_id != previous_open_id:
            raise ValueError
        return bundle, _iso(access), _iso(refresh)
    except Exception:
        raise SupplierConnectionError("CJ authentication response is invalid.", 502,
                                      "invalid_auth_metadata") from None


def _safe_shops(shops, sensitive_values=()):
    if not isinstance(shops, list) or len(shops) > 1000:
        raise SupplierConnectionError("CJ shop response is invalid.", 502, "invalid_shops")
    clean = []
    for shop in shops:
        if (not isinstance(shop, dict) or not isinstance(shop.get("shop_id"), str)
                or not shop["shop_id"] or len(shop["shop_id"]) > 256):
            raise SupplierConnectionError("CJ shop response is invalid.", 502, "invalid_shops")
        for key in ("shop_id", "name", "platform"):
            value = str(shop.get(key) or "")
            if any(secret and (value == secret or (len(secret) >= 8 and secret in value))
                   for secret in sensitive_values):
                raise SupplierConnectionError("CJ shop response is unsafe.", 502, "unsafe_provider_response")
        clean.append({key: str(shop.get(key) or "")[:256] for key in ("shop_id", "name", "platform")} |
                     {"status": shop.get("status") if type(shop.get("status")) is int else None})
    if len({shop["shop_id"] for shop in clean}) != len(clean):
        raise SupplierConnectionError("CJ shop response is invalid.", 502, "invalid_shops")
    return clean


def _verify(adapter, auth, selected_shop=None, *, sensitive_values=()):
    """Prove the credential resolves to this account; list shops if it can.

    Identity is the mandatory half and is unchanged: `setting/get` must return
    the same account the token authenticated as, which is the check that keeps
    one merchant's credential from answering for another's.

    Shop listing is the optional half, and used not to be. Requiring it meant a
    real CJ account that owns no external storefront could never connect, even
    though importing products needs no CJ shop at all -- and in practice CJ
    answers `shop/getShops` for such an account with a business code its own
    documentation does not list, which we correctly refuse to interpret and
    which therefore killed the whole connection.

    So an unreadable shop list is survivable *only when nothing was selected*.
    If a shop was selected, an unreadable list is fatal: the alternative is
    treating "we could not ask" as "you are authorized", which is exactly the
    tenant check this function exists to perform.
    """
    adapter.set_credentials(auth, account_ref=account_reference(auth.open_id))
    settings = adapter.get_settings()
    if not isinstance(settings, dict):
        raise SupplierConnectionError("CJ account verification failed.", 502, "invalid_identity")
    # The settings response must independently resolve the authenticated ID.
    identity = settings.get("account_id")
    if not isinstance(identity, str) or identity != auth.open_id:
        raise SupplierConnectionError("CJ account verification failed.", 502, "identity_mismatch")
    try:
        shops = _safe_shops(adapter.get_shops(), sensitive_values)
    except SupplierConnectionError:
        raise  # Our own refusal of an unsafe or malformed list is never survivable.
    except SupplierError:
        if selected_shop:
            raise
        shops = []
    if selected_shop and selected_shop not in {shop["shop_id"] for shop in shops if shop["status"] == 1}:
        raise SupplierConnectionError("Select a shop belonging to this CJ connection.", 403,
                                      "shop_not_authorized")
    return shops


def _quota_metadata(adapter):
    from services.business_os.suppliers.quota import budget_state
    points = getattr(adapter, "points_info", None)
    clean = {}
    if isinstance(points, dict):
        clean = {key: points[key] for key in ("remaining", "usedToday", "total")
                 if type(points.get(key)) is int and points[key] >= 0}
    controller = getattr(adapter, "quota", None)
    if controller is not None and hasattr(controller, "snapshot"):
        try:
            snap = controller.snapshot(adapter.account_ref)
            for target, source in (("remaining", "remaining"), ("usedToday", "used_today"),
                                   ("total", "total"), ("qps", "qps"), ("retry_after", "retry_after")):
                value = snap.get(source)
                if type(value) in (int, float) and math.isfinite(value) and value >= 0:
                    clean[target] = value
        except Exception:
            pass  # Missing telemetry cannot fabricate a budget or healthy status.
    state = budget_state(clean.get("remaining"), clean.get("total"))
    return (state if state in _SAFE_QUOTA_STATES else "UNKNOWN"), json.dumps(clean, sort_keys=True)


def _bootstrap(business_id, store_id, actor_user_id, api_key, *, context=None, adapter=None):
    conn = db.connect()
    try:
        merchant = _authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
    finally:
        conn.close()
    vault.require_available()  # before consuming a one-time credential or quota
    vault.require_index_available()
    if not isinstance(api_key, str) or not api_key.strip() or len(api_key) > 16384:
        raise SupplierConnectionError("A valid CJ API key is required.", 400, "invalid_api_key")
    adapter = adapter or _new_adapter(api_key)
    if hasattr(adapter, "register_secrets"):
        adapter.register_secrets([api_key])
    try:
        auth = adapter.authenticate(api_key)
    except SupplierError as exc:
        # Authenticating claimed one of this egress IP's three account slots
        # under a fingerprint of the key. A key CJ has just refused is not an
        # account and never will be, so it gives the slot back -- otherwise a
        # merchant's typos accumulate until nobody on the IP can connect.
        #
        # Only on CJ's own refusal. Anything else leaves the slot held, because
        # a key that timed out may be perfectly good and a released slot is a
        # way around the cap.
        if getattr(exc, "code", None) == "REAUTH_REQUIRED":
            _release_pending_slot(adapter)
        raise
    secrets, access, refresh = _auth_fields(auth)
    secrets["api_key"] = api_key
    return merchant, adapter, auth, secrets, access, refresh


def discover_shops(business_id, store_id, actor_user_id, api_key, *, context=None, adapter=None):
    _, adapter, auth, secrets, _, _ = _bootstrap(business_id, store_id, actor_user_id, api_key,
                                          context=context, adapter=adapter)
    return {"shops": _verify(adapter, auth, sensitive_values=secrets.values()), "requires_explicit_shop_selection": True}


def connect_cj(business_id, store_id, actor_user_id, api_key, external_shop_id=None, *, context=None, adapter=None):
    """Bind a merchant's own CJ credential to this store. A CJ shop is optional.

    A CJ "shop" is an external storefront (Shopify, Woo, ...) authorized inside
    the merchant's CJ account. Importing CJ products into PulseSoc needs none of
    that -- PulseSoc *is* the storefront -- so demanding one made a normal CJ
    account unconnectable. The empty string, not NULL, records its absence:
    every downstream binding check compares this column as a string, and a NULL
    would make those comparisons quietly true-ish instead of plainly unequal.

    Selecting a shop remains as strict as it was. It is still validated against
    the live list under this credential, and an existing binding still cannot be
    silently replaced -- including replacing a bound shop with no shop.
    """
    if external_shop_id is None:
        external_shop_id = ""
    if not isinstance(external_shop_id, str) or len(external_shop_id) > 256:
        raise SupplierConnectionError("Choose an explicit CJ shop.", 400, "shop_required")
    external_shop_id = external_shop_id.strip()
    merchant, adapter, auth, secrets, access, refresh = _bootstrap(
        business_id, store_id, actor_user_id, api_key, context=context, adapter=adapter)
    _verify(adapter, auth, external_shop_id, sensitive_values=secrets.values())
    quota_state, quota_json = _quota_metadata(adapter)
    account_ref = account_reference(auth.open_id)
    conn = db.connect()
    try:
        # Recheck after network awaits: revocation or business transfer must win.
        current_merchant = _authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
        if current_merchant != merchant:
            raise SupplierConnectionError("Supplier access denied.", 403, "forbidden")
        now = _iso()
        conn.execute("INSERT INTO business_os_supplier_account_owners (account_reference, merchant_id, created_at) "
                     "VALUES (?, ?, ?) ON CONFLICT(account_reference) DO NOTHING", (account_ref, merchant, now))
        owner = conn.execute("SELECT merchant_id FROM business_os_supplier_account_owners "
                             "WHERE account_reference=?", (account_ref,)).fetchone()
        if owner["merchant_id"] != merchant:
            raise SupplierConnectionError("CJ account cannot be bound to this merchant.", 403,
                                          "account_ownership_conflict")
        existing = conn.execute("SELECT * FROM business_os_supplier_connections WHERE business_id=? "
                                "AND store_id=? AND provider='CJ'", (business_id, store_id)).fetchone()
        if existing:
            existing = dict(existing)
            if (existing["merchant_id"] != merchant or existing["external_account_id"] != account_ref
                    or existing["external_shop_id"] != external_shop_id):
                raise SupplierConnectionError("Existing CJ account or shop cannot be silently replaced.",
                                              409, "connection_binding_conflict")
            # Explicit reauthentication rotates the bundle, not the account/shop
            # binding underneath already persisted fulfillment intents.
            vault.save(conn, secrets, **_credential_scope(existing))
            conn.execute("UPDATE business_os_supplier_connections SET access_expires_at=?, "
                         "refresh_expires_at=?, status='CONNECTED', last_verified_at=?, updated_at=?, "
                         "quota_state=?, quota_json=?, version=version+1, refresh_lease_token=NULL, "
                         "refresh_lease_until=NULL WHERE id=?",
                         (access, refresh, now, now, quota_state, quota_json, existing["id"]))
            store_service._audit(conn, business_id=business_id, subject_type="supplier_connection",
                                 subject_ref=existing["id"], action="supplier.cj.reauthenticate", actor=actor_user_id,
                                 after={"provider": "CJ", "status": "CONNECTED"})
            result = _public(_row(conn, existing["id"], business_id, store_id, merchant))
            conn.commit()
            return result
        connection_id = "sc_" + uuid.uuid4().hex
        ref = vault.save(conn, secrets, merchant_id=merchant, business_id=business_id,
                         store_id=store_id, connection_id=connection_id)
        conn.execute("INSERT INTO business_os_supplier_connections "
                     "(id, merchant_id, business_id, store_id, external_account_id, external_shop_id, "
                     "status, credential_reference, access_expires_at, refresh_expires_at, "
                     "last_verified_at, created_at, updated_at, quota_state, quota_json) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (connection_id, merchant, business_id, store_id, account_ref, external_shop_id,
                      "CONNECTED", ref, access, refresh, now, now, now, quota_state, quota_json))
        store_service._audit(conn, business_id=business_id, subject_type="supplier_connection",
                             subject_ref=connection_id, action="supplier.cj.connect", actor=actor_user_id,
                             after={"provider": "CJ", "status": "CONNECTED"})
        result = _public(_row(conn, connection_id, business_id, store_id, merchant))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def worker_connection(connection_id, business_id, store_id):
    """INTERNAL: persisted tenant context only. Never register as an HTTP action."""
    _enabled()
    conn = db.connect()
    try:
        merchant = _canonical_scope(conn, business_id, store_id)
        row = _row(conn, connection_id, business_id, store_id, merchant)
        return {"connection": row, "credentials": vault.load(conn, **_credential_scope(row))}
    finally:
        conn.close()


def internal_webhook_secret(connection_id):
    """Opaque callback routing only; signature verifier is the sole consumer."""
    _enabled()
    conn = db.connect()
    try:
        row = conn.execute("SELECT business_id, store_id FROM business_os_supplier_connections "
                           "WHERE id=? AND provider='CJ'", (connection_id,)).fetchone()
        if row is None:
            raise SupplierConnectionError("Supplier connection not found.", 404, "not_found")
        scope = (row["business_id"], row["store_id"])
    finally:
        conn.close()
    result = worker_connection(connection_id, *scope)
    return {"connection": result["connection"], "open_id": result["credentials"]["open_id"]}


def _status_for(exc, refresh=False):
    code = str(getattr(exc, "code", "")).lower()
    if "suspend" in code or "reactivat" in code:
        return "REACTIVATION_REQUIRED"
    if "rate" in code or "quota" in code or getattr(exc, "http_status", None) == 429:
        return "RATE_LIMITED"
    if refresh or any(part in code for part in ("auth", "token", "identity", "shop")):
        return "REAUTH_REQUIRED"
    return "PROVIDER_UNAVAILABLE"


def _set_status(row, status):
    if status not in STATUSES:
        raise ValueError("Invalid supplier health state")
    conn = db.connect()
    try:
        conn.execute("UPDATE business_os_supplier_connections SET status=?, updated_at=? "
                     "WHERE id=? AND merchant_id=? AND business_id=? AND store_id=?",
                     (status, _iso(), row["id"], row["merchant_id"], row["business_id"], row["store_id"]))
        conn.commit()
    finally:
        conn.close()


def _auth_object(row, credentials):
    from services.business_os.suppliers.cj import AuthBundle
    return AuthBundle(access_token=credentials["access_token"], refresh_token=credentials["refresh_token"],
                      open_id=credentials["open_id"], access_expires_at=row["access_expires_at"],
                      refresh_expires_at=row["refresh_expires_at"])


def _refresh(row, credentials, adapter):
    if _date(row["refresh_expires_at"]) <= _now():
        _set_status(row, "REAUTH_REQUIRED")
        raise SupplierConnectionError("Reconnect this CJ account.", 409, "reauth_required")
    lease = uuid.uuid4().hex
    conn = db.connect()
    try:
        # Durable CAS, not a per-process lock. A worker cannot refresh another
        # tenant, and concurrent requests receive retryable busy rather than race.
        cursor = conn.execute("UPDATE business_os_supplier_connections SET refresh_lease_token=?, "
            "refresh_lease_until=? WHERE id=? AND merchant_id=? AND business_id=? AND store_id=? "
            "AND version=? AND (refresh_lease_until IS NULL OR refresh_lease_until < ?)",
            (lease, _iso(_now() + timedelta(seconds=REFRESH_LEASE_SECONDS)), row["id"],
             row["merchant_id"], row["business_id"], row["store_id"], row["version"], _iso()))
        if cursor.rowcount != 1:
            raise SupplierConnectionError("CJ authentication refresh is in progress.", 409, "refresh_in_progress")
        conn.commit()
    finally:
        conn.close()
    try:
        auth = adapter.refresh_authentication(credentials["refresh_token"])
        fresh, access, refresh = _auth_fields(auth, previous_open_id=credentials["open_id"])
        fresh["api_key"] = credentials["api_key"]
        # CJ refresh may omit openId; retain only the already authenticated
        # connection identity. An explicit different identity was rejected above.
        auth = _auth_object({"access_expires_at": access, "refresh_expires_at": refresh}, fresh)
        conn = db.connect()
        try:
            merchant = _canonical_scope(conn, row["business_id"], row["store_id"])
            if merchant != row["merchant_id"]:
                raise SupplierConnectionError("Supplier access denied.", 403, "forbidden")
            cursor = conn.execute("UPDATE business_os_supplier_connections SET access_expires_at=?, "
                "refresh_expires_at=?, version=version+1, refresh_lease_token=NULL, refresh_lease_until=NULL, "
                "status='VERIFICATION_REQUIRED', updated_at=? WHERE id=? AND refresh_lease_token=? AND version=? "
                "AND refresh_lease_until > ?",
                (access, refresh, _iso(), row["id"], lease, row["version"], _iso()))
            if cursor.rowcount != 1:
                raise SupplierConnectionError("CJ authentication refresh was superseded.", 409, "refresh_superseded")
            vault.save(conn, fresh, **_credential_scope(row))
            conn.commit()
            row.update(access_expires_at=access, refresh_expires_at=refresh, version=row["version"] + 1)
            return row, fresh, auth
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    except Exception as exc:
        _set_status(row, _status_for(exc, refresh=True))
        raise
    finally:
        conn = db.connect()
        try:
            conn.execute("UPDATE business_os_supplier_connections SET refresh_lease_token=NULL, "
                         "refresh_lease_until=NULL WHERE id=? AND refresh_lease_token=?", (row["id"], lease))
            conn.commit()
        finally:
            conn.close()


def _hydrate(connection_id, business_id, store_id, *, adapter=None, reauthorize=None):
    result = worker_connection(connection_id, business_id, store_id)
    row, credentials = result["connection"], result["credentials"]
    if row["status"] == "REAUTH_REQUIRED":
        raise SupplierConnectionError("Reconnect this CJ account.", 409, "reauth_required")
    if adapter is None:
        from services.business_os.suppliers.cj import CJAdapter
        adapter = CJAdapter(account_ref=row["external_account_id"], environment="SANDBOX")
    refresh_attempted = False
    try:
        if hasattr(adapter, "register_secrets"):
            adapter.register_secrets(credentials.values())
        auth = _auth_object(row, credentials)
        adapter.set_credentials(auth, account_ref=row["external_account_id"])
        if _date(row["access_expires_at"]) <= _now() + timedelta(seconds=REFRESH_WINDOW_SECONDS):
            refresh_attempted = True
            row, credentials, auth = _refresh(row, credentials, adapter)
        _verify(adapter, auth, row["external_shop_id"], sensitive_values=credentials.values())
        quota_state, quota_json = _quota_metadata(adapter)
        conn = db.connect()
        try:
            merchant = _canonical_scope(conn, business_id, store_id)
            if merchant != row["merchant_id"]:
                raise SupplierConnectionError("Supplier access denied.", 403, "forbidden")
            if reauthorize:
                reauthorize(conn)
            conn.execute("UPDATE business_os_supplier_connections SET status='CONNECTED', last_verified_at=?, "
                         "updated_at=?, quota_state=?, quota_json=? WHERE id=? AND merchant_id=? AND business_id=? AND store_id=?",
                         (_iso(), _iso(), quota_state, quota_json, connection_id, row["merchant_id"], business_id, store_id))
            conn.commit()
        finally:
            conn.close()
        return adapter
    except Exception as exc:
        if getattr(exc, "code", "") not in {"refresh_in_progress", "refresh_superseded"}:
            _set_status(row, _status_for(exc, refresh=refresh_attempted))
        raise


def adapter_for(business_id, store_id, actor_user_id, connection_id, *, context=None, adapter=None, write=False):
    # This public check always precedes the internal credential lookup.
    get_connection(connection_id, business_id, store_id, actor_user_id, context=context, write=write)
    return _hydrate(connection_id, business_id, store_id, adapter=adapter,
                    reauthorize=lambda conn: _authorize(conn, business_id, store_id, actor_user_id,
                                                        context=context, write=write))


def worker_adapter(connection_id, business_id, store_id, *, adapter=None):
    """INTERNAL worker capability, never an alternative user authorization path."""
    return _hydrate(connection_id, business_id, store_id, adapter=adapter)


def _live_shops(connection_id, business_id, store_id, actor_user_id, *, context=None,
                adapter=None, write=False):
    """The shop list as this connection's stored credential sees it right now.

    ``discover_shops`` answers the same question from an API key, which only
    exists while the connect form is on screen. Afterwards the key is in the
    vault and the merchant has no copy to retype, so without this there was no
    way to see the list again -- and therefore no way to choose from it.

    Same cleaning as connect-time (``_safe_shops``), including the check that CJ
    has not echoed one of this connection's own secrets back inside a shop name.
    """
    cj = adapter_for(business_id, store_id, actor_user_id, connection_id,
                     context=context, adapter=adapter, write=write)
    credentials = worker_connection(connection_id, business_id, store_id)["credentials"]
    return cj, _safe_shops(cj.get_shops(), credentials.values())


def _annotated_shops(shops):
    from services.business_os.suppliers import fulfillment
    annotated = []
    for shop in shops:
        try:
            fulfillment.dispatch_shop(shops, shop["shop_id"])
            reason = ""
        except fulfillment.FulfillmentError as exc:
            reason = exc.code
        annotated.append(shop | {"fulfillable": not reason, "unfulfillable_reason": reason})
    return annotated


def connection_shops(connection_id, business_id, store_id, actor_user_id, *, context=None, adapter=None):
    """CJ shops visible to an existing connection, and which can take orders.

    ``fulfillable`` is not decoration. It is ``fulfillment.dispatch_shop``'s own
    verdict, so a merchant reading this list learns which choice will work
    before making it rather than at the first order they lose.

    An account that owns no shop is the *expected* answer here, not an error.
    CJ replies to ``shop/getShops`` for such an account with a business code its
    own documentation does not list, which the transport correctly refuses to
    interpret and reports as ``SUPPLIER_REJECTED`` (422). That is the state the
    live connection is in today. Left to propagate it reaches the app as a bare
    "Something went wrong" -- 422 matches none of the client's status classes --
    for the single most likely outcome of opening this screen.

    ``_verify`` already made this decision for connecting: an unreadable shop
    list is survivable when nothing was selected. This is the same rule for
    reading, and it is narrower on purpose. ``_verify`` survives *any*
    ``SupplierError`` because there the shop is irrelevant -- importing needs
    none, so connecting should not fail on it. Here the shop list *is* the
    answer, so collapsing a throttle or a dead credential into "you have no
    shops" would print a false instruction: it tells a merchant to go create a
    storefront when the truth is "ask again in a minute" or "your key is
    rejected". Only a rejection -- CJ answered, and the answer was not a list --
    is reported as an empty list. Everything else keeps its own meaning.

    Binding is unaffected. ``bind_shop`` selects, so it goes through
    ``_live_shops`` directly and an unreadable list stays fatal there, exactly
    as ``_verify`` requires when something was chosen.
    """
    try:
        _, shops = _live_shops(connection_id, business_id, store_id, actor_user_id,
                               context=context, adapter=adapter)
    except SupplierConnectionError:
        raise  # Our own refusal of an unsafe or malformed list is never survivable.
    except SupplierError as exc:
        if str(getattr(exc, "code", "")).upper() != "SUPPLIER_REJECTED":
            raise
        shops = []
    current = get_connection(connection_id, business_id, store_id, actor_user_id, context=context)
    return {"shops": _annotated_shops(shops), "external_shop_id": current["external_shop_id"]}


def bind_shop(connection_id, business_id, store_id, actor_user_id, external_shop_id,
              *, context=None, adapter=None):
    """Choose the CJ shop this connection fulfils through, after connecting.

    Connecting without a shop is normal and deliberate -- importing products
    needs none, and demanding one made an ordinary CJ account unconnectable.
    Fulfilment does need one. Until this function existed there was no
    transition between those two states: ``connect_cj`` refuses to change an
    existing binding, and "none" is a binding for that purpose, so reconnecting
    with a shop returns ``connection_binding_conflict``. The only account that
    could ever fulfil was one that named its shop in the connect form, before it
    had any reason to know which shop it wanted. Every other connection could
    import, publish and sell, then refuse its own orders with
    ``shop_binding_required`` permanently.

    None of this relaxes that refusal. This is its other half: the provenance
    ``create_intent`` demands, established on purpose instead of assumed away.

    Binding only ever goes from none to one. Replacing a shop is a different
    operation with different consequences -- persisted intents carry the shop
    they were created against and ``_validate_binding`` compares it -- and this
    is not that operation. Going from none to one is safe precisely because the
    refusal guarantees no intent can exist yet.
    """
    if not isinstance(external_shop_id, str) or not external_shop_id.strip() or len(external_shop_id) > 256:
        raise SupplierConnectionError("Choose an explicit CJ shop.", 400, "shop_required")
    external_shop_id = external_shop_id.strip()
    from services.business_os.suppliers import fulfillment
    # Hydrating first proves the credential still resolves to this account, and
    # returns the live list the choice is checked against. A shop the merchant
    # names but CJ does not list under this credential is refused here.
    _, shops = _live_shops(connection_id, business_id, store_id, actor_user_id,
                           context=context, adapter=adapter, write=True)
    if external_shop_id not in {shop["shop_id"] for shop in shops}:
        raise SupplierConnectionError("Select a shop belonging to this CJ connection.", 403,
                                      "shop_not_authorized")
    fulfillment.dispatch_shop(shops, external_shop_id)
    conn = db.connect()
    try:
        merchant = _authorize(conn, business_id, store_id, actor_user_id, context=context, write=True)
        row = _row(conn, connection_id, business_id, store_id, merchant)
        if row["external_shop_id"] == external_shop_id:
            return _public(row)  # The same choice, already recorded.
        # Conditional on the column still being empty, so two concurrent binds
        # cannot both believe they won. The loser reads as a conflict, which is
        # what it is.
        cursor = conn.execute(
            "UPDATE business_os_supplier_connections SET external_shop_id=?, updated_at=?, "
            "version=version+1 WHERE id=? AND merchant_id=? AND business_id=? AND store_id=? "
            "AND provider='CJ' AND COALESCE(external_shop_id,'')=''",
            (external_shop_id, _iso(), connection_id, merchant, business_id, store_id))
        if cursor.rowcount != 1:
            raise SupplierConnectionError("Existing CJ shop cannot be silently replaced.",
                                          409, "connection_binding_conflict")
        store_service._audit(conn, business_id=business_id, subject_type="supplier_connection",
                             subject_ref=connection_id, action="supplier.cj.bind_shop",
                             actor=actor_user_id, before={"external_shop_id": row["external_shop_id"]},
                             after={"external_shop_id": external_shop_id})
        result = _public(_row(conn, connection_id, business_id, store_id, merchant))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


INACTIVITY_WARNING_DAYS = 7
INACTIVITY_DISABLE_DAYS = 30


def inactivity_forecast(connection_id, business_id, store_id, actor_user_id, *, context=None):
    """When CJ is likely to disable this connection for having no real orders.

    CJ warns after seven days without a real order and disables API access
    after thirty. Sandbox orders do not count, and this deployment sends
    nothing else -- so a connected merchant is on that clock from the moment
    they connect, and will reach the end of it. Surfacing that is the whole
    point: the alternative is a merchant discovering it from CJ.

    ## The estimate can be late, and says so

    We count from our own reference, and CJ counts from theirs. Ours is the
    last real order we sent, or failing that the moment the connection was
    created. CJ's is whatever activity they attribute to the account, which
    may include orders placed before PulseSoc existed, from a different tool,
    or through the CJ dashboard.

    Those differ in a direction that matters. If the account was already idle
    when the merchant connected, CJ's clock started earlier than ours and CJ
    will disable *before* the day we name. The error is optimistic, which is
    the unsafe kind, so `estimate_may_be_late` is True whenever we are counting
    from connection creation rather than from an order we actually sent -- and
    in this deployment that is always. It is not a hedge; it is the single most
    important field here, and a caller that renders the number without it is
    showing a deadline we cannot stand behind.

    Never used to justify placing an order. Manufacturing a real order to reset
    this clock is prohibited outright (see docs/cj/CJ_PROVIDER_POLICY_CONFIRMATION.md);
    this function reports the consequence of not doing so, and reporting it
    is not the same as recommending the way out.
    """
    from . import fulfillment
    connection = get_connection(connection_id, business_id, store_id, actor_user_id, context=context)
    conn = db.connect()
    try:
        fulfillment.ensure_schema(conn)
        count, last_at = fulfillment.real_order_activity(conn, connection_id)
    finally:
        conn.close()
    if last_at is not None:
        since = datetime.fromtimestamp(float(last_at), timezone.utc)
        reference, may_be_late = "last_real_order", False
    else:
        since = _date(connection["created_at"])
        reference, may_be_late = "connection_created", True
    days = (_now() - since).total_seconds() / 86400
    state = ("DISABLE_EXPECTED" if days >= INACTIVITY_DISABLE_DAYS
             else "WARNING" if days >= INACTIVITY_WARNING_DAYS else "OK")
    return {
        "state": state,
        "real_orders": count,
        "last_real_order_at": _iso(since) if last_at is not None else None,
        "reference": reference,
        "days_since_reference": round(days, 2),
        # Floored at zero: a negative countdown would read as time remaining.
        "days_until_disable_estimate": round(max(0.0, INACTIVITY_DISABLE_DAYS - days), 2),
        "estimate_may_be_late": may_be_late,
        "warning_days": INACTIVITY_WARNING_DAYS,
        "disable_days": INACTIVITY_DISABLE_DAYS,
        "sandbox_orders_count_toward_this": False,
    }


def health_connection(connection_id, business_id, store_id, actor_user_id, *, context=None, adapter=None):
    try:
        adapter_for(business_id, store_id, actor_user_id, connection_id, context=context, adapter=adapter)
    except Exception as exc:
        # Never conceal denied authorization behind an apparently valid status.
        if getattr(exc, "code", "") in {"not_found", "store_not_found", "forbidden", "unauthorized",
                                        "disabled", "account_hold", "store_not_approved",
                                        "store_access_revoked", "stale_store_context"}:
            raise
        if getattr(exc, "code", "") not in {"refresh_in_progress", "refresh_superseded"}:
            conn = db.connect()
            try:
                merchant = _authorize(conn, business_id, store_id, actor_user_id, context=context)
                row = _row(conn, connection_id, business_id, store_id, merchant)
            finally:
                conn.close()
            if row["status"] in {"CONNECTED", "VERIFICATION_REQUIRED", "AUTH_EXPIRED"}:
                _set_status(row, _status_for(exc))
    return get_connection(connection_id, business_id, store_id, actor_user_id, context=context)


def record_activity(connection_id, business_id, store_id, adapter=None, error=None, synced=False):
    """INTERNAL: provider-call telemetry after hydration, no secret lookup.

    A successful catalog read does not independently attest connection health.
    A failed call updates safe health vocabulary, never error text. Tenant scope
    is re-resolved, so a stale worker cannot write another owner's connection.
    """
    _enabled()
    conn = db.connect()
    try:
        merchant = _canonical_scope(conn, business_id, store_id)
        row = _row(conn, connection_id, business_id, store_id, merchant)
        state, quota = _quota_metadata(adapter) if adapter is not None else (row["quota_state"], row["quota_json"])
        status = _status_for(error) if error is not None else row["status"]
        last_sync = _iso() if synced and error is None else row["last_sync_at"]
        conn.execute("UPDATE business_os_supplier_connections SET status=?, quota_state=?, quota_json=?, "
                     "last_sync_at=?, updated_at=? WHERE id=? AND merchant_id=? AND business_id=? AND store_id=?",
                     (status, state, quota, last_sync, _iso(), connection_id, merchant, business_id, store_id))
        conn.commit()
    finally:
        conn.close()


def _operational_health(conn):
    now = time.time()
    worker = {"state": "NOT_INITIALIZED", "execution_observed": False}
    inbox = {"state": "NOT_INITIALIZED"}
    if db.get_table_columns(conn, "business_os_supplier_sync_jobs"):
        row = conn.execute("SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN available_at<=? AND lease_until<=? THEN 1 ELSE 0 END) AS due, "
            "SUM(CASE WHEN lease_until>? THEN 1 ELSE 0 END) AS leased, "
            "SUM(CASE WHEN failures>0 THEN 1 ELSE 0 END) AS failed, "
            "MIN(CASE WHEN available_at<=? AND lease_until<=? THEN available_at ELSE NULL END) AS oldest_due, "
            "MAX(last_verified_at) AS last_verified FROM business_os_supplier_sync_jobs",
            (now, now, now, now, now)).fetchone()
        worker = {"state": "BACKLOG" if row["due"] else "NO_DUE_JOBS", "jobs_count": row["total"],
                  "due_count": row["due"] or 0, "leased_count": row["leased"] or 0,
                  "failed_count": row["failed"] or 0, "execution_observed": row["last_verified"] is not None,
                  "last_verified_at": row["last_verified"],
                  "reconciliation_lag_seconds": max(0, now - row["oldest_due"]) if row["oldest_due"] is not None else 0}
    if db.get_table_columns(conn, "provider_webhook_events"):
        rows = conn.execute("SELECT status, COUNT(*) AS count FROM provider_webhook_events "
                            "WHERE provider='cj' GROUP BY status").fetchall()
        counts = {state: 0 for state in ("received", "processing", "processed", "failed", "skipped")}
        for row in rows:
            if row["status"] in counts:
                counts[row["status"]] = row["count"]
        oldest = conn.execute("SELECT MIN(received_at) AS oldest FROM provider_webhook_events WHERE provider='cj' "
                              "AND status IN ('received','processing','failed')").fetchone()["oldest"]
        try:
            lag = max(0, now - _date(oldest).timestamp()) if oldest else 0
        except SupplierConnectionError:
            lag = None
        inbox = {"state": "BACKLOG" if any(counts[key] for key in ("received", "processing", "failed")) else "NO_PENDING_EVENTS",
                 "counts": counts, "oldest_pending_lag_seconds": lag}
    return worker, inbox


def admin_health():
    """Aggregate-only INTERNAL operator surface; caller enforces admin access."""
    _enabled()
    conn = db.connect()
    try:
        rows = [dict(row) for row in conn.execute("SELECT * FROM business_os_supplier_connections "
                                                 "WHERE provider='CJ'").fetchall()]
        worker, inbox = _operational_health(conn)
    finally:
        conn.close()
    counts = {status: 0 for status in sorted(STATUSES)}
    quotas = {state: 0 for state in sorted(_SAFE_QUOTA_STATES)}
    latest = None
    for row in rows:
        projected = _public(row)
        status = projected["status"] if projected["status"] in STATUSES else "VERIFICATION_REQUIRED"
        counts[status] += 1
        state = row["quota_state"] if row["quota_state"] in _SAFE_QUOTA_STATES else "UNKNOWN"
        quotas[state] += 1
        if row["last_verified_at"] and (latest is None or row["last_verified_at"] > latest):
            latest = row["last_verified_at"]
    return {"provider": "CJ", "connections_count": len(rows), "states": counts,
            "quota_states": quotas, "provider_reachable_recently": counts["CONNECTED"] > 0,
            "last_verified_at": latest, "credential_vault_available": vault.available(),
            "worker": worker, "webhook_inbox": inbox,
            "environment": "SANDBOX", "production_fulfillment_enabled": False}
