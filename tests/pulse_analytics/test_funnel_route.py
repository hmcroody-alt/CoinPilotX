"""The endpoint must answer for the session's seller and no other.

The route is tested against a real database and a stubbed ``bot`` module rather
than the Flask monolith, because what is under test here is the route's own
decisions — whose id it uses, which window it resolves, what it does when a
table will not read — and importing 111k lines to observe them would mean the
test suite tolerates only one of them being wrong at a time.

``auth_required`` is declarative (see ``services/route_auth.py``): it records
the intent for the audit and adds no check. So the refusal below is a test of
the view body, which is where the gate actually is.
"""

from __future__ import annotations

import json
import sys
import types

import pytest
from flask import Flask

from .conftest import (
    A_LISTINGS,
    B_LISTINGS,
    SELLER_A,
    SELLER_B,
    write_engagement,
    write_impression,
    write_order,
)


class _NoCloseConn:
    """The fixture's connection, minus ``close``.

    The route closes the connection it is handed in a ``finally``, which is
    correct there and would end the test's database half way through.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def close(self):
        return None


@pytest.fixture
def client(conn, monkeypatch):
    """A Flask app carrying only this blueprint, over the fixture database."""
    state = {"user": {"user_id": SELLER_A}, "db_calls": 0}

    stub = types.ModuleType("bot")
    stub.init_db = lambda: None
    stub.api_account_user = lambda: state["user"]

    def _db():
        state["db_calls"] += 1
        return _NoCloseConn(conn)

    stub.db = _db
    monkeypatch.setitem(sys.modules, "bot", stub)

    from services import pulse_analytics_routes

    app = Flask(__name__)
    pulse_analytics_routes.register(app)
    test_client = app.test_client()
    test_client.state = state
    return test_client


def _body(response):
    return json.loads(response.data.decode("utf-8"))


def test_an_anonymous_request_is_refused_before_any_query(client):
    """Not "returns nothing" — refused, and without touching the database."""
    client.state["user"] = None

    response = client.get("/api/pulse/analytics/seller/funnel")

    assert response.status_code == 401
    assert _body(response)["error_code"] == "LOGIN_REQUIRED"
    assert client.state["db_calls"] == 0


def test_a_seller_gets_their_own_traffic_and_not_the_other_seller_s(client, cur):
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_impression(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_B)
    write_engagement(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_B, action="click")

    report = _body(client.get("/api/pulse/analytics/seller/funnel"))["funnel"]

    assert report["impressions"] == 1
    assert report["steps"]["click"] == 0


def test_a_seller_user_id_parameter_does_not_move_the_answer(client, cur):
    """The horizontal escalation this endpoint is shaped to not have.

    Seller B's id in the query string, seller A in the session. If a future
    change ever reads the seller from the request, this is where it is caught —
    seller B's single impression would appear and seller A's two would not.
    """
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_impression(cur, listing_id=A_LISTINGS[1], seller_user_id=SELLER_A, minutes_ago=2)
    write_impression(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_B)

    response = client.get(
        f"/api/pulse/analytics/seller/funnel?seller_user_id={SELLER_B}&seller_id={SELLER_B}"
    )

    assert _body(response)["funnel"]["impressions"] == 2


def test_the_window_is_echoed_so_the_client_can_label_it(client):
    report = _body(client.get("/api/pulse/analytics/seller/funnel?days=30"))["funnel"]

    assert report["window_days"] == 30


@pytest.mark.parametrize(
    "requested,expected", [("0", 1), ("-5", 1), ("400", 90), ("abc", 7), ("", 7)]
)
def test_an_out_of_range_window_is_clamped_and_reported_honestly(
    client, requested, expected
):
    """Clamped, not refused — but labelled with what was measured, not asked for.

    A request for 400 days answered with 90 days of rows under the label "400"
    would be the module's own headline defect: a number that is not the answer
    to the question it is printed beside.
    """
    response = client.get(f"/api/pulse/analytics/seller/funnel?days={requested}")

    assert _body(response)["funnel"]["window_days"] == expected


def test_the_window_bounds_the_orders_as_well_as_the_events(client, cur):
    """One window, or the client/server gap compares a week against a lifetime."""
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    write_order(cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A, minutes_ago=1)
    write_order(
        cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A,
        minutes_ago=60 * 24 * 30, order_id=999,
    )

    week = _body(client.get("/api/pulse/analytics/seller/funnel?days=7"))["funnel"]
    year = _body(client.get("/api/pulse/analytics/seller/funnel?days=90"))["funnel"]

    assert week["outcome"]["confirmed_orders"] == 1
    assert year["outcome"]["confirmed_orders"] == 2


def test_an_unreadable_order_table_reports_unavailable_rather_than_zero_sales(
    client, cur
):
    """`0` is a claim. This seller may have sold plenty; we cannot see."""
    write_impression(cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    cur.execute("DROP TABLE seller_transactions")

    report = _body(client.get("/api/pulse/analytics/seller/funnel"))["funnel"]

    assert report["impressions"] == 1, "the readable half must still render"
    assert report["outcome"]["source"] == "unavailable"
    assert report["outcome"]["confirmed_orders"] is None


def test_an_unreadable_event_log_still_reports_the_money(client, cur):
    """And the converse: the two halves fail independently."""
    write_order(cur, item_id=A_LISTINGS[0], seller_user_id=SELLER_A)
    cur.execute("DROP TABLE commerce_discovery_impression_events")

    report = _body(client.get("/api/pulse/analytics/seller/funnel"))["funnel"]

    assert report["impressions"] == 0
    assert report["click_through_rate"] is None, "unmeasured, not zero"
    assert report["outcome"]["confirmed_orders"] == 1


def test_the_response_is_never_cached(client):
    response = client.get("/api/pulse/analytics/seller/funnel")

    assert "no-store" in response.headers["Cache-Control"]


def test_no_viewer_identifier_appears_anywhere_in_the_payload(client, cur):
    """A stable hash is enough to join one person across a whole store."""
    write_impression(
        cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, subject_ref="viewer-hash-9"
    )
    write_engagement(
        cur, listing_id=A_LISTINGS[0], seller_user_id=SELLER_A, action="click",
        subject_ref="viewer-hash-9",
    )

    raw = client.get("/api/pulse/analytics/seller/funnel").data.decode("utf-8")

    assert "viewer-hash-9" not in raw
    assert "subject_ref" not in raw
    assert "session_id" not in raw
    assert "sess-1" not in raw


def test_a_session_carrying_no_usable_seller_id_is_a_server_error_not_a_wide_read(
    client, cur
):
    """The failure mode to avoid is a missing id meaning "every seller"."""
    write_impression(cur, listing_id=B_LISTINGS[0], seller_user_id=SELLER_B)
    client.state["user"] = {"user_id": None}

    response = client.get("/api/pulse/analytics/seller/funnel")

    assert response.status_code == 500
    assert "funnel" not in _body(response)


def test_the_route_declares_its_auth_for_the_default_deny_audit(client):
    from services import pulse_analytics_routes
    from services.route_auth import declaration_of

    view = pulse_analytics_routes.api_pulse_analytics_seller_funnel

    assert (declaration_of(view) or {}).get("kind") == "user"
