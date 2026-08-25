"""Business OS — Section 9: Events service (canonical events / ticketing domain).

Flag-gated (``BUSINESS_OS_EVENTS``) lifecycle over the additive ``business_os_event*``
tables. A business member (staff+) creates an event, adds ticket types, publishes it, and
runs check-in; any user buys a ticket to a published event.

Money reuse — there is NO second payment system:

  * a PAID ticket purchase captures buyer funds into the event's escrow account through the
    ONE canonical ledger (:mod:`services.business_os.ledger.ledger`), idempotently keyed to
    the ticket; a FREE ticket skips the ledger;
  * settlement splits escrow → business payable (net) + platform revenue (fee) using the
    marketplace order engine's own ``_fee_split`` / ``DEFAULT_FEE_BPS`` — the same take-rate
    the rest of commerce uses;
  * a refund reverses the capture (escrow → intake) on the same ledger.

Who may manage a business's events is resolved against S1 canonical RBAC
(``business.service._effective_role``) — never re-modeled here. Management reads/writes
require membership. A stranger managing sees 404 (existence not leaked).

A published event is *visible* to anyone, but not in the same shape: a manager gets the
stored row (:func:`_event_manage`), everyone else gets a field allowlist
(:func:`_event_visitor`) with manager identity, the owning business id and per-tier sales
counts removed. Those two functions are the only ways an event leaves this module.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any, Optional

from services import db
from services.business_os.events import schema as _schema
from services.business_os.business import service as biz_svc
from services.business_os.ledger import ledger as _ledger
from services.business_os.marketplace.orders import _fee_split, DEFAULT_FEE_BPS


FLAG_ENV = "BUSINESS_OS_EVENTS"

MANAGE_ROLE = "staff"   # staff+ may create/manage a business's events

# Ledger account namespace for events (distinct from marketplace's).
INTAKE_ACCOUNT = "platform:events_intake"
PLATFORM_REVENUE_ACCOUNT = "platform:events_revenue"

EVENT_STATUSES = ("draft", "published", "cancelled", "completed")
TICKET_STATUSES = ("confirmed", "checked_in", "cancelled", "refunded")


class EventError(ValueError):
    """One stable domain-facing error carrying an HTTP status + machine code."""

    def __init__(self, message: str, http_status: int = 400, code: str = "invalid"):
        super().__init__(message)
        self.http_status = int(http_status)
        self.code = code


def escrow_account(event_id: Any) -> str:
    return "event_escrow:%s" % str(event_id)


def business_payable_account(business_id: Any) -> str:
    return "event_business_payable:%s" % str(business_id)


# ---------------------------------------------------------------------------
def is_enabled() -> bool:
    raw = (os.environ.get(FLAG_ENV) or "").strip().lower()
    return raw in {"1", "true", "on", "yes", "enabled", "canonical"}


def _require_enabled() -> None:
    if not is_enabled():
        raise EventError("Events is not enabled in this environment.", 503, "disabled")


def _require_not_held(context: Optional[dict]) -> None:
    ctx = context or {}
    status = str(ctx.get("account_status") or "").lower()
    access = ctx.get("access_enabled")
    if status in {"suspended", "banned", "disabled", "hold"}:
        raise EventError("Account is on hold.", 403, "account_hold")
    if access is not None and not access:
        raise EventError("Account access is disabled.", 403, "account_hold")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _sid(user_id: Any) -> str:
    return biz_svc._sid(user_id)


def _row(row):
    if row is None:
        return None
    try:
        return dict(row)
    except Exception:
        return None


def _rows(rows) -> list:
    return [d for d in (_row(r) for r in (rows or [])) if d is not None]


def _clean_text(value: Any, field: str, *, required: bool = False,
                limit: int = 500) -> Optional[str]:
    if value is None:
        if required:
            raise EventError("%s is required." % field, 400, "invalid")
        return None
    if not isinstance(value, str):
        raise EventError("%s must be text." % field, 400, "invalid")
    v = value.strip()[:limit]
    if required and not v:
        raise EventError("%s is required." % field, 400, "invalid")
    return v


def _int_or_none(value: Any, field: str, *, minimum: int = 0) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise EventError("%s must be an integer." % field, 400, "invalid")
    if n < minimum:
        raise EventError("%s must be >= %d." % (field, minimum), 400, "invalid")
    return n


# ---------------------------------------------------------------------------
# Authorization — reuse S1 canonical RBAC verbatim.
# ---------------------------------------------------------------------------
def _business_exists(conn, business_id: Any) -> bool:
    row = conn.execute(
        "SELECT 1 FROM business_os_business WHERE business_id = ? LIMIT 1",
        (str(business_id),)).fetchone()
    return row is not None


def _can_manage(conn, business_id: Any, user_id: Any) -> bool:
    role = biz_svc._effective_role(conn, str(business_id), user_id)
    if role is None:
        return False
    return biz_svc._role_rank(role) <= biz_svc._role_rank(MANAGE_ROLE)


def _require_manager(conn, business_id: Any, user_id: Any) -> None:
    """404 when the business is missing OR the caller cannot manage it — the two are
    indistinguishable to the caller (existence not leaked)."""
    if not business_id or not _business_exists(conn, business_id):
        raise EventError("Not found.", 404, "not_found")
    if not _can_manage(conn, business_id, user_id):
        raise EventError("Not found.", 404, "not_found")


def _get_event(conn, event_id: Any) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_events WHERE event_id = ? LIMIT 1",
        (str(event_id),)).fetchone())


def _get_ticket_type(conn, ticket_type_id: Any) -> Optional[dict]:
    return _row(conn.execute(
        "SELECT * FROM business_os_event_ticket_types WHERE ticket_type_id = ? LIMIT 1",
        (str(ticket_type_id),)).fetchone())


def _audit(conn, action: str, *, event_id=None, ticket_id=None, actor=None,
           detail=None) -> None:
    import json
    conn.execute(
        "INSERT INTO business_os_event_audit "
        "(audit_id, event_id, ticket_id, actor_user_id, action, detail_json, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (_schema.new_id("eva"), event_id, ticket_id, _sid(actor) if actor else None,
         action, json.dumps(detail) if detail else None, _now()))


def _event_manage(conn, event: dict, *, with_types: bool = True) -> dict:
    """The whole row, for someone entitled to manage this event.

    Was called ``_event_public``, which was not true of it and was the reason
    the leak below went unnoticed for so long: it is ``dict(event)``, so it
    carries ``created_by_user_id`` and every ticket type's ``quantity_sold``.
    A name that claims a projection has been applied invites callers to hand
    the result to whoever asked.
    """
    out = dict(event)
    if with_types:
        out["ticket_types"] = _rows(conn.execute(
            "SELECT * FROM business_os_event_ticket_types WHERE event_id = ? "
            "ORDER BY price_cents ASC, created_at ASC", (event["event_id"],)).fetchall())
    return out


# What a stranger may see of an event. An allowlist, not a blocklist: a column
# added to `business_os_events` later is invisible here until somebody decides
# it is public, which is the safe direction for that mistake to fail in.
_EVENT_VISITOR_FIELDS = ("event_id", "title", "description", "venue",
                         "starts_at", "ends_at", "status", "currency")


def _event_visitor(conn, event: dict, *, with_types: bool = True) -> dict:
    """An event as a visitor may see it.

    Withheld, deliberately:

      * ``created_by_user_id`` — who runs this business is manager metadata,
        and it is a real name attached to a real account.
      * ``business_id`` — the caller reached this event through a page or an
        event id; handing back the internal key invites walking it into the
        management endpoints to see what answers differently.
      * ``quantity_total`` / ``quantity_sold`` — sales figures. "47 of 50 sold"
        is a revenue statement about somebody else's business.
      * every ticket, and therefore every attendee.

    ``sold_out`` is derived from those withheld numbers instead of exposing
    them, because it is the one thing a visitor genuinely needs from them: a
    ticket tier they cannot buy has to say so, and saying it does not require
    telling them how many were sold.
    """
    out = {key: event.get(key) for key in _EVENT_VISITOR_FIELDS}
    if with_types:
        rows = _rows(conn.execute(
            "SELECT ticket_type_id, name, price_cents, quantity_total, quantity_sold "
            "FROM business_os_event_ticket_types "
            "WHERE event_id = ? AND COALESCE(status,'active') = 'active' "
            "ORDER BY price_cents ASC, created_at ASC",
            (event["event_id"],)).fetchall())
        out["ticket_types"] = [{
            "ticket_type_id": r.get("ticket_type_id"),
            "name": r.get("name"),
            "price_cents": _num(r.get("price_cents")),
            # NULL `quantity_total` means unlimited supply, which can never be
            # sold out. Reading it as 0 would mark every unlimited tier sold
            # out and silently take the whole event off sale.
            "sold_out": (r.get("quantity_total") is not None
                         and _num(r.get("quantity_sold")) >= _num(r.get("quantity_total"))),
        } for r in rows]
    return out


def _num(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# Event lifecycle (management)
# ---------------------------------------------------------------------------
def create_event(business_id: Any, actor_user_id: Any, payload: dict, *,
                 context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    payload = payload if isinstance(payload, dict) else {}
    title = _clean_text(payload.get("title"), "title", required=True, limit=200)
    conn = db.connect()
    try:
        _require_manager(conn, business_id, actor_user_id)
        eid = _schema.new_id("evt")
        now = _now()
        conn.execute(
            "INSERT INTO business_os_events "
            "(event_id, business_id, created_by_user_id, title, description, venue, "
            "starts_at, ends_at, capacity, currency, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (eid, str(business_id), _sid(actor_user_id), title,
             _clean_text(payload.get("description"), "description", limit=4000),
             _clean_text(payload.get("venue"), "venue", limit=300),
             _clean_text(payload.get("starts_at"), "starts_at", limit=40),
             _clean_text(payload.get("ends_at"), "ends_at", limit=40),
             _int_or_none(payload.get("capacity"), "capacity", minimum=1),
             (payload.get("currency") or "usd").lower(), "draft", now, now))
        _audit(conn, "event_created", event_id=eid, actor=actor_user_id,
               detail={"title": title})
        conn.commit()
        return _event_manage(conn, _get_event(conn, eid))
    finally:
        conn.close()


def add_ticket_type(event_id: Any, actor_user_id: Any, payload: dict, *,
                    context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    payload = payload if isinstance(payload, dict) else {}
    name = _clean_text(payload.get("name"), "name", required=True, limit=120)
    price = _int_or_none(payload.get("price_cents"), "price_cents", minimum=0) or 0
    qty = _int_or_none(payload.get("quantity_total"), "quantity_total", minimum=1)
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            raise EventError("Not found.", 404, "not_found")
        _require_manager(conn, event["business_id"], actor_user_id)
        if event["status"] not in ("draft", "published"):
            raise EventError("Cannot add ticket types to a %s event." % event["status"],
                             409, "invalid_state")
        ttid = _schema.new_id("ett")
        now = _now()
        conn.execute(
            "INSERT INTO business_os_event_ticket_types "
            "(ticket_type_id, event_id, name, price_cents, quantity_total, "
            "quantity_sold, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (ttid, str(event_id), name, price, qty, 0, "active", now, now))
        _audit(conn, "ticket_type_added", event_id=str(event_id), actor=actor_user_id,
               detail={"name": name, "price_cents": price})
        conn.commit()
        return _get_ticket_type(conn, ttid)
    finally:
        conn.close()


def publish_event(event_id: Any, actor_user_id: Any, *,
                  context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            raise EventError("Not found.", 404, "not_found")
        _require_manager(conn, event["business_id"], actor_user_id)
        if event["status"] == "published":
            return _event_manage(conn, event)
        if event["status"] != "draft":
            raise EventError("Only a draft event can be published.", 409, "invalid_state")
        types = conn.execute(
            "SELECT COUNT(*) AS n FROM business_os_event_ticket_types "
            "WHERE event_id = ? AND status = 'active'", (str(event_id),)).fetchone()
        if int(dict(types)["n"]) == 0:
            raise EventError("Add at least one ticket type before publishing.",
                             409, "not_ready")
        conn.execute(
            "UPDATE business_os_events SET status = 'published', updated_at = ? "
            "WHERE event_id = ?", (_now(), str(event_id)))
        _audit(conn, "event_published", event_id=str(event_id), actor=actor_user_id)
        conn.commit()
        return _event_manage(conn, _get_event(conn, event_id))
    finally:
        conn.close()


def cancel_event(event_id: Any, actor_user_id: Any, *,
                 context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            raise EventError("Not found.", 404, "not_found")
        _require_manager(conn, event["business_id"], actor_user_id)
        if event["status"] in ("cancelled", "completed"):
            raise EventError("Event is already %s." % event["status"],
                             409, "invalid_state")
        conn.execute(
            "UPDATE business_os_events SET status = 'cancelled', updated_at = ? "
            "WHERE event_id = ?", (_now(), str(event_id)))
        _audit(conn, "event_cancelled", event_id=str(event_id), actor=actor_user_id)
        conn.commit()
        return _event_manage(conn, _get_event(conn, event_id))
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------
def get_event(event_id: Any, requester_user_id: Any = None) -> Optional[dict]:
    """Published events are publicly readable; draft/cancelled only by a manager.

    The manager check runs first now, for every status rather than only the
    non-public ones. It used to run second and only as a fallback, which read
    as "published is public, so nobody needs checking" — and that was true of
    who may *see* the event but not of *what they see*. A published event went
    back as the whole row to anyone who asked, manager-identity and
    per-tier sales counts included. Deciding the audience once, at the top,
    is what makes the two projections below mutually exhaustive: a caller is
    either a manager and gets the row, or is not and gets the allowlist.
    """
    _require_enabled()
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            return None
        is_manager = requester_user_id is not None and _can_manage(
            conn, event["business_id"], requester_user_id)
        if is_manager:
            return _event_manage(conn, event)
        if event["status"] in ("published", "completed"):
            return _event_visitor(conn, event)
        # Draft or cancelled, and the caller cannot manage it: as far as they
        # are concerned it does not exist. Not a 403 — that would confirm the
        # id names something real.
        return None
    finally:
        conn.close()


# How many rows `list_public_events` will look at before giving up. The
# "has it already happened" test cannot be pushed into SQL — see `_parse_when`
# — so the filter runs in Python over a bounded window. A business whose most
# recent 500 events are all in the past shows none upcoming, which is very
# probably the truth about that business.
PUBLIC_EVENT_SCAN_CAP = 500


def _parse_when(value: Any) -> Optional[datetime]:
    """A stored date, or ``None`` when it is not one.

    `starts_at` / `ends_at` are `_clean_text` columns: free text, capped at 40
    characters, never format-checked on the way in. So there is no SQL
    comparison that can be trusted here — `'2026-08-23' < '2026-08-23T09:00Z'`
    is true as text and false as a date, and an offset like `+02:00` sorts
    against a `Z` timestamp by its punctuation. Anything that does not parse
    returns ``None`` and is treated as "no date given" rather than guessed at.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _has_ended(event: dict, now: datetime) -> bool:
    """Whether this event is over.

    `ends_at` decides when it is given, because a festival that started on
    Friday is still on on Saturday. Falling back to `starts_at` treats a
    single-moment event as over once it has begun.

    An event with no usable date has *not* ended. "Date to be announced" is a
    real state for an event page, and dropping those rows would hide a real
    upcoming event because somebody typed the date in a format we don't read.
    """
    when = _parse_when(event.get("ends_at")) or _parse_when(event.get("starts_at"))
    if when is None:
        return False
    return when < now


def list_public_events(business_id: Any, *, limit: int = 20,
                       now: Optional[datetime] = None) -> list:
    """Upcoming published events for a business, as a visitor may see them.

    No actor argument, deliberately: there is nothing here that changes with
    who is asking, so there is no requester to get wrong. Draft and cancelled
    events are excluded in SQL rather than filtered afterwards — an unpublished
    event must never be *loaded* into a response this function builds.

    `completed` is excluded too. It is a status a manager sets after the fact,
    and an event still listed as upcoming when the organiser has marked it done
    is a worse answer than one row fewer.
    """
    _require_enabled()
    try:
        capped = max(0, min(int(limit), 100))
    except (TypeError, ValueError):
        capped = 20
    if not business_id or capped == 0:
        return []
    moment = now or datetime.now(timezone.utc)
    conn = db.connect()
    try:
        rows = _rows(conn.execute(
            "SELECT * FROM business_os_events "
            "WHERE business_id = ? AND status = 'published' "
            # Undated events sort last rather than first. NULLs sort first in
            # SQLite and last in Postgres, so the sort key is made non-NULL here
            # instead of relying on the engine's default.
            "ORDER BY COALESCE(NULLIF(starts_at, ''), '9999') ASC, created_at ASC "
            "LIMIT ?",
            (str(business_id), PUBLIC_EVENT_SCAN_CAP)).fetchall())
        upcoming = [e for e in rows if not _has_ended(e, moment)][:capped]
        return [_event_visitor(conn, e) for e in upcoming]
    finally:
        conn.close()


def list_business_events(business_id: Any, actor_user_id: Any, *,
                         limit: int = 100) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        _require_manager(conn, business_id, actor_user_id)
        rows = conn.execute(
            "SELECT * FROM business_os_events WHERE business_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (str(business_id), int(limit))).fetchall()
        return _rows(rows)
    finally:
        conn.close()


def list_my_tickets(user_id: Any, *, limit: int = 200) -> list:
    _require_enabled()
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT t.*, e.title AS event_title, e.starts_at AS event_starts_at "
            "FROM business_os_event_tickets t "
            "JOIN business_os_events e ON e.event_id = t.event_id "
            "WHERE t.holder_user_id = ? ORDER BY t.created_at DESC LIMIT ?",
            (_sid(user_id), int(limit))).fetchall()
        return _rows(rows)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Ticketing (purchase / check-in / refund) — money via the ONE canonical ledger
# ---------------------------------------------------------------------------
def purchase_ticket(event_id: Any, ticket_type_id: Any, buyer_user_id: Any, *,
                    client_ref: Optional[str] = None,
                    context: Optional[dict] = None) -> dict:
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            raise EventError("Not found.", 404, "not_found")
        if event["status"] != "published":
            raise EventError("Tickets are only sold for a published event.",
                             409, "not_on_sale")
        tt = _get_ticket_type(conn, ticket_type_id)
        if not tt or tt["event_id"] != str(event_id) or tt["status"] != "active":
            raise EventError("Not found.", 404, "not_found")

        # Idempotent replay: same (ticket_type, client_ref) returns the original ticket.
        if client_ref:
            existing = _row(conn.execute(
                "SELECT * FROM business_os_event_tickets "
                "WHERE ticket_type_id = ? AND client_ref = ? LIMIT 1",
                (str(ticket_type_id), str(client_ref))).fetchone())
            if existing:
                out = dict(existing)
                out["idempotent"] = True
                return out

        # Capacity: per-tier quantity, then overall event capacity.
        if tt["quantity_total"] is not None and \
                int(tt["quantity_sold"]) >= int(tt["quantity_total"]):
            raise EventError("This ticket type is sold out.", 409, "sold_out")
        if event["capacity"] is not None:
            sold = int(dict(conn.execute(
                "SELECT COUNT(*) AS n FROM business_os_event_tickets "
                "WHERE event_id = ? AND status IN ('confirmed','checked_in')",
                (str(event_id),)).fetchone())["n"])
            if sold >= int(event["capacity"]):
                raise EventError("This event is sold out.", 409, "sold_out")

        price = int(tt["price_cents"])
        currency = (event.get("currency") or "usd").lower()
        tid = _schema.new_id("etk")
        now = _now()

        # PAID: capture into escrow through the canonical ledger BEFORE issuing the
        # ticket, idempotently keyed to the ticket id. FREE: skip the ledger entirely.
        capture_ref = None
        if price > 0:
            txn = _ledger.post_entry(
                idempotency_key="event_capture:%s" % tid,
                actor=_sid(buyer_user_id),
                amount_cents=price,
                currency=currency,
                entry_type="event_ticket_capture",
                source=INTAKE_ACCOUNT,
                destination=escrow_account(event_id),
                reason="Event ticket captured into escrow.",
                related_object=str(event_id))
            capture_ref = txn.get("transaction_id")

        try:
            conn.execute(
                "INSERT INTO business_os_event_tickets "
                "(ticket_id, event_id, ticket_type_id, holder_user_id, price_cents_paid, "
                "currency, capture_txn_ref, client_ref, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (tid, str(event_id), str(ticket_type_id), _sid(buyer_user_id), price,
                 currency, capture_ref, client_ref, "confirmed", now, now))
        except Exception as exc:
            # Unique client_ref race: return the winner rather than double-issuing.
            if client_ref:
                existing = _row(conn.execute(
                    "SELECT * FROM business_os_event_tickets "
                    "WHERE ticket_type_id = ? AND client_ref = ? LIMIT 1",
                    (str(ticket_type_id), str(client_ref))).fetchone())
                if existing:
                    out = dict(existing)
                    out["idempotent"] = True
                    return out
            raise EventError("Could not issue ticket.", 500, "issue_failed") from exc

        conn.execute(
            "UPDATE business_os_event_ticket_types "
            "SET quantity_sold = quantity_sold + 1, updated_at = ? "
            "WHERE ticket_type_id = ?", (now, str(ticket_type_id)))
        _audit(conn, "ticket_purchased", event_id=str(event_id), ticket_id=tid,
               actor=buyer_user_id, detail={"price_cents": price})
        conn.commit()
        out = _row(conn.execute(
            "SELECT * FROM business_os_event_tickets WHERE ticket_id = ?",
            (tid,)).fetchone())
        out["idempotent"] = False
        return out
    finally:
        conn.close()


def check_in_ticket(ticket_id: Any, actor_user_id: Any, *,
                    context: Optional[dict] = None) -> dict:
    _require_enabled()
    conn = db.connect()
    try:
        ticket = _row(conn.execute(
            "SELECT * FROM business_os_event_tickets WHERE ticket_id = ? LIMIT 1",
            (str(ticket_id),)).fetchone())
        if not ticket:
            raise EventError("Not found.", 404, "not_found")
        event = _get_event(conn, ticket["event_id"])
        _require_manager(conn, event["business_id"], actor_user_id)
        if ticket["status"] == "checked_in":
            return ticket
        if ticket["status"] != "confirmed":
            raise EventError("Only a confirmed ticket can be checked in.",
                             409, "invalid_state")
        now = _now()
        conn.execute(
            "UPDATE business_os_event_tickets SET status = 'checked_in', "
            "checked_in_at = ?, updated_at = ? WHERE ticket_id = ?",
            (now, now, str(ticket_id)))
        _audit(conn, "ticket_checked_in", event_id=ticket["event_id"],
               ticket_id=str(ticket_id), actor=actor_user_id)
        conn.commit()
        return _row(conn.execute(
            "SELECT * FROM business_os_event_tickets WHERE ticket_id = ?",
            (str(ticket_id),)).fetchone())
    finally:
        conn.close()


def refund_ticket(ticket_id: Any, actor_user_id: Any, *,
                  context: Optional[dict] = None) -> dict:
    _require_enabled()
    conn = db.connect()
    try:
        ticket = _row(conn.execute(
            "SELECT * FROM business_os_event_tickets WHERE ticket_id = ? LIMIT 1",
            (str(ticket_id),)).fetchone())
        if not ticket:
            raise EventError("Not found.", 404, "not_found")
        event = _get_event(conn, ticket["event_id"])
        _require_manager(conn, event["business_id"], actor_user_id)
        if ticket["status"] in ("refunded", "cancelled"):
            raise EventError("Ticket is already %s." % ticket["status"],
                             409, "invalid_state")

        # Reverse the capture on the same ledger (escrow → intake), idempotent per ticket.
        refund_ref = None
        if int(ticket["price_cents_paid"]) > 0 and ticket.get("capture_txn_ref"):
            txn = _ledger.post_entry(
                idempotency_key="event_refund:%s" % str(ticket_id),
                actor=_sid(actor_user_id),
                amount_cents=int(ticket["price_cents_paid"]),
                currency=(ticket.get("currency") or "usd").lower(),
                entry_type="event_ticket_refund",
                source=escrow_account(ticket["event_id"]),
                destination=INTAKE_ACCOUNT,
                reason="Event ticket refunded from escrow.",
                related_object=str(ticket["event_id"]),
                allow_negative=True)
            refund_ref = txn.get("transaction_id")

        now = _now()
        conn.execute(
            "UPDATE business_os_event_tickets SET status = 'refunded', "
            "refund_txn_ref = ?, updated_at = ? WHERE ticket_id = ?",
            (refund_ref, now, str(ticket_id)))
        conn.execute(
            "UPDATE business_os_event_ticket_types "
            "SET quantity_sold = MAX(quantity_sold - 1, 0), updated_at = ? "
            "WHERE ticket_type_id = ?", (now, ticket["ticket_type_id"]))
        _audit(conn, "ticket_refunded", event_id=ticket["event_id"],
               ticket_id=str(ticket_id), actor=actor_user_id)
        conn.commit()
        return _row(conn.execute(
            "SELECT * FROM business_os_event_tickets WHERE ticket_id = ?",
            (str(ticket_id),)).fetchone())
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Settlement + summary — money math reuses the marketplace fee split.
# ---------------------------------------------------------------------------
def settle_event(event_id: Any, actor_user_id: Any, *,
                 context: Optional[dict] = None) -> dict:
    """Move the event's escrow balance to the business (net) + platform (fee), then mark it
    completed. Idempotent: a settled event returns its existing split."""
    _require_enabled()
    _require_not_held(context)
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            raise EventError("Not found.", 404, "not_found")
        _require_manager(conn, event["business_id"], actor_user_id)
        if event["status"] == "completed":
            return {"event_id": str(event_id), "already_settled": True,
                    "escrow_balance_cents": _ledger.get_balance(
                        escrow_account(event_id),
                        (event.get("currency") or "usd").lower())}
        if event["status"] not in ("published", "cancelled"):
            raise EventError("Event cannot be settled from %s." % event["status"],
                             409, "invalid_state")
        currency = (event.get("currency") or "usd").lower()
        balance = _ledger.get_balance(escrow_account(event_id), currency)
        fee, net = _fee_split(balance, DEFAULT_FEE_BPS) if balance > 0 else (0, 0)
        if net > 0:
            _ledger.post_entry(
                idempotency_key="event_settle_net:%s" % str(event_id),
                actor=_sid(actor_user_id), amount_cents=net, currency=currency,
                entry_type="event_settlement_net",
                source=escrow_account(event_id),
                destination=business_payable_account(event["business_id"]),
                reason="Event net proceeds to business.",
                related_object=str(event_id))
        if fee > 0:
            _ledger.post_entry(
                idempotency_key="event_settle_fee:%s" % str(event_id),
                actor=_sid(actor_user_id), amount_cents=fee, currency=currency,
                entry_type="event_settlement_fee",
                source=escrow_account(event_id),
                destination=PLATFORM_REVENUE_ACCOUNT,
                reason="Event platform fee.",
                related_object=str(event_id))
        conn.execute(
            "UPDATE business_os_events SET status = 'completed', updated_at = ? "
            "WHERE event_id = ?", (_now(), str(event_id)))
        _audit(conn, "event_settled", event_id=str(event_id), actor=actor_user_id,
               detail={"gross_cents": balance, "fee_cents": fee, "net_cents": net})
        conn.commit()
        return {"event_id": str(event_id), "gross_cents": balance,
                "platform_fee_cents": fee, "business_net_cents": net,
                "currency": currency}
    finally:
        conn.close()


def event_summary(event_id: Any, actor_user_id: Any) -> dict:
    """Attendance + money rollup for a manager. Revenue is read straight off the ledger."""
    _require_enabled()
    conn = db.connect()
    try:
        event = _get_event(conn, event_id)
        if not event:
            raise EventError("Not found.", 404, "not_found")
        _require_manager(conn, event["business_id"], actor_user_id)
        currency = (event.get("currency") or "usd").lower()
        counts = {}
        for st in TICKET_STATUSES:
            counts[st] = int(dict(conn.execute(
                "SELECT COUNT(*) AS n FROM business_os_event_tickets "
                "WHERE event_id = ? AND status = ?",
                (str(event_id), st)).fetchone())["n"])
        return {
            "event_id": str(event_id),
            "status": event["status"],
            "currency": currency,
            "tickets": counts,
            "escrow_balance_cents": _ledger.get_balance(
                escrow_account(event_id), currency),
        }
    finally:
        conn.close()
