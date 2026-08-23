"""What each order type asks the buyer for, and what it must never ask for.

The incident these pin down: a `booking` listing reached the payment sheet with
address collection switched on, because the client recognised only four
fulfilment values and everything unrecognised fell through to `shipping`. So the
negative assertions here matter as much as the positive ones — "digital requires
nothing" and "a booking is never asked for an address" are the actual bugs.
"""

from __future__ import annotations

import os
import pathlib
import re

import pytest

from services import marketplace_fulfillment as mf


class TestResolveKind:
    def test_digital_listing_is_digital_whatever_the_delivery_column_says(self):
        assert mf.resolve_kind("digital", "shipping", {}) == "digital"

    def test_booking_defaults_to_remote(self):
        assert mf.resolve_kind("booking", "booking", {}) == "booking_remote"

    def test_booking_in_person_is_the_only_booking_with_an_address(self):
        assert mf.resolve_kind("booking", "booking", {"meeting_mode": "in_person"}) == "booking_in_person"
        assert mf.resolve_kind("booking", "booking", {"meeting_mode": "video"}) == "booking_remote"

    @pytest.mark.parametrize(
        "location,expected",
        [("remote", "service_remote"), ("in_person", "service_in_person"), ("both", "service_choice"), ("", "service_remote")],
    )
    def test_service_reads_its_own_location(self, location, expected):
        assert mf.resolve_kind("service", "service", {"service_location": location}) == expected

    @pytest.mark.parametrize("venue,expected", [("in_person", "event_in_person"), ("online", "event_online"), ("pulsesoc_live", "event_online")])
    def test_event_reads_its_venue_mode(self, venue, expected):
        assert mf.resolve_kind("event", "event", {"venue_mode": venue}) == expected

    @pytest.mark.parametrize(
        "delivery,expected",
        [("shipping", "shipping"), ("pickup", "pickup"), ("both", "shipping_or_pickup"), ("", "shipping")],
    )
    def test_physical_reads_its_delivery_option(self, delivery, expected):
        assert mf.resolve_kind("physical", delivery, {}) == expected

    def test_physical_falls_back_to_metadata_when_the_column_is_empty(self):
        assert mf.resolve_kind("physical", "", {"delivery_options": "pickup"}) == "pickup"

    def test_a_legacy_row_with_no_type_at_all_ships(self):
        assert mf.resolve_kind(None, None, None) == "shipping"


class TestResolveChoice:
    def test_a_settled_kind_passes_through_untouched(self):
        assert mf.resolve_choice("digital", None) == ("digital", "")

    def test_an_undecided_kind_without_an_answer_is_refused(self):
        _, error = mf.resolve_choice("shipping_or_pickup", "")
        assert error == mf.LANE_REQUIRED_CODE

    def test_the_buyers_answer_settles_it(self):
        assert mf.resolve_choice("shipping_or_pickup", "pickup") == ("pickup", "")
        assert mf.resolve_choice("service_choice", "in_person") == ("service_in_person", "")
        assert mf.resolve_choice("service_choice", "remote") == ("service_remote", "")

    def test_an_answer_from_the_wrong_lane_does_not_settle_it(self):
        _, error = mf.resolve_choice("shipping_or_pickup", "remote")
        assert error == mf.LANE_REQUIRED_CODE


class TestRequiredFields:
    def test_digital_requires_nothing(self):
        assert mf.required_fields("digital") == ()
        assert mf.field_spec("digital") == ()

    @pytest.mark.parametrize(
        "kind",
        ["digital", "pickup", "service_remote", "booking_remote", "event_online", "event_in_person"],
    )
    def test_no_order_that_ships_nowhere_asks_for_an_address(self, kind):
        keys = {field["key"] for field in mf.field_spec(kind)}
        assert not {key for key in keys if key.startswith("address_")}
        assert not mf.needs_shipping_address(kind)

    def test_shipping_asks_for_a_name_and_an_address(self):
        required = mf.required_fields("shipping")
        assert "contact_name" in required
        assert "address_line1" in required
        assert "address_city" in required
        assert "address_country" in required

    def test_a_scheduled_order_asks_when_not_where(self):
        required = mf.required_fields("booking_remote")
        assert required == ("contact_name", "scheduled_date", "scheduled_time", "timezone")

    def test_an_event_without_published_tiers_does_not_ask_which_ticket(self):
        assert mf.required_fields("event_online", {}) == ("attendee_name",)

    def test_an_event_with_tiers_asks_which_one(self):
        meta = {"tickets": [{"name": "General"}, {"name": "VIP"}]}
        assert mf.required_fields("event_online", meta) == ("attendee_name", "ticket_type")
        spec = {field["key"]: field for field in mf.field_spec("event_online", meta)}
        assert spec["ticket_type"]["options"] == ["General", "VIP"]

    def test_pickup_asks_for_a_phone_because_someone_has_to_be_met(self):
        assert "contact_phone" in mf.required_fields("pickup")


class TestValidateDetails:
    def test_digital_accepts_an_empty_submission(self):
        ok, cleaned = mf.validate_details("digital", None)
        assert ok and cleaned == {}

    def test_an_undecided_kind_is_refused_before_any_field_is_read(self):
        ok, error = mf.validate_details("shipping_or_pickup", {"contact_name": "A"})
        assert not ok
        assert error["code"] == mf.LANE_REQUIRED_CODE

    def test_a_missing_required_field_names_itself(self):
        ok, error = mf.validate_details("shipping", {"contact_name": "Ada Lovelace"})
        assert not ok
        assert error["code"] == mf.DETAILS_REQUIRED_CODE
        assert error["field"] == "address_line1"
        assert error["status"] == 400

    def test_a_complete_us_address_passes(self):
        ok, cleaned = mf.validate_details("shipping", {
            "contact_name": "Ada Lovelace", "address_line1": "1 Main St", "address_city": "Austin",
            "address_region": "TX", "address_postal_code": "78701", "address_country": "us",
        })
        assert ok
        assert cleaned["address_country"] == "US"
        assert cleaned["address_region"] == "TX"

    def test_a_us_address_without_a_state_is_refused(self):
        ok, error = mf.validate_details("shipping", {
            "contact_name": "Ada", "address_line1": "1 Main St", "address_city": "Austin",
            "address_postal_code": "78701", "address_country": "US",
        })
        assert not ok
        assert error["field"] == "address_region"

    def test_an_irish_address_is_accepted_without_a_postal_code(self, monkeypatch):
        monkeypatch.setenv("MARKETPLACE_SHIPPING_COUNTRIES", "US,IE")
        ok, cleaned = mf.validate_details("shipping", {
            "contact_name": "Ada", "address_line1": "1 Grafton St", "address_city": "Dublin",
            "address_country": "IE",
        })
        assert ok, cleaned
        assert "address_postal_code" not in cleaned

    def test_a_country_the_seller_does_not_ship_to_is_refused(self, monkeypatch):
        monkeypatch.setenv("MARKETPLACE_SHIPPING_COUNTRIES", "US")
        ok, error = mf.validate_details("shipping", {
            "contact_name": "Ada", "address_line1": "1 Rue", "address_city": "Paris",
            "address_postal_code": "75001", "address_country": "FR",
        })
        assert not ok
        assert error["field"] == "address_country"

    @pytest.mark.parametrize("bad", ["tomorrow", "2026/01/01", "01-01-2026"])
    def test_a_date_that_is_not_a_date_is_refused(self, bad):
        ok, error = mf.validate_details("booking_remote", {
            "contact_name": "Ada", "scheduled_date": bad, "scheduled_time": "14:00", "timezone": "UTC",
        })
        assert not ok
        assert error["field"] == "scheduled_date"

    def test_a_valid_booking_passes_and_carries_no_address(self):
        ok, cleaned = mf.validate_details("booking_remote", {
            "contact_name": "Ada", "scheduled_date": "2026-09-01", "scheduled_time": "14:00", "timezone": "UTC",
        })
        assert ok
        assert not any(key.startswith("address_") for key in cleaned)

    def test_a_ticket_the_seller_never_published_is_refused(self):
        meta = {"tickets": [{"name": "General"}]}
        ok, error = mf.validate_details("event_online", {"attendee_name": "Ada", "ticket_type": "Backstage"}, meta)
        assert not ok
        assert error["field"] == "ticket_type"

    def test_unknown_keys_are_dropped_rather_than_failing_the_order(self):
        ok, cleaned = mf.validate_details("pickup", {
            "contact_name": "Ada", "contact_phone": "+15125551234", "favourite_colour": "blue",
        })
        assert ok
        assert "favourite_colour" not in cleaned

    def test_markup_in_a_field_does_not_survive(self):
        ok, cleaned = mf.validate_details("pickup", {
            "contact_name": "<script>alert(1)</script>Ada", "contact_phone": "+15125551234",
        })
        assert ok
        assert "<" not in cleaned["contact_name"]


class TestStripeHandoff:
    def test_an_address_becomes_stripes_shipping_object(self):
        _, cleaned = mf.validate_details("shipping", {
            "contact_name": "Ada Lovelace", "contact_phone": "+15125551234",
            "address_line1": "1 Main St", "address_city": "Austin", "address_region": "TX",
            "address_postal_code": "78701", "address_country": "US",
        })
        shipping = mf.stripe_shipping(cleaned)
        assert shipping["name"] == "Ada Lovelace"
        assert shipping["address"]["line1"] == "1 Main St"
        assert shipping["address"]["postal_code"] == "78701"
        assert shipping["phone"] == "+15125551234"

    def test_an_order_with_no_address_hands_stripe_nothing(self):
        _, cleaned = mf.validate_details("booking_remote", {
            "contact_name": "Ada", "scheduled_date": "2026-09-01", "scheduled_time": "14:00", "timezone": "UTC",
        })
        assert mf.stripe_shipping(cleaned) == {}


class TestSnapshot:
    def test_the_snapshot_carries_the_kind_and_a_copy_of_the_details(self):
        cleaned = {"contact_name": "Ada"}
        snap = mf.snapshot("pickup", cleaned)
        assert snap["kind"] == "pickup"
        cleaned["contact_name"] = "Someone else"
        # A later edit must not reach back into what was frozen onto the order.
        assert snap["details"]["contact_name"] == "Ada"


class TestClientServerAgreement:
    """The TS mirror is only a mirror if it says the same thing.

    Parsed rather than trusted: a field added on one side and forgotten on the
    other is exactly how the buyer gets a form the server will not accept.
    """

    @staticmethod
    def _client_fields() -> dict[str, list[tuple[str, str, bool]]]:
        source = (pathlib.Path(__file__).resolve().parents[1]
                  / "mobile-native/src/api/marketplaceFulfillment.ts").read_text()
        groups = {}
        for name, body in re.findall(r"^const (CONTACT|CONTACT_WITH_PHONE|ADDRESS|WHEN|NOTES): Triple\[\] = (.*?\];)$",
                                     source, re.S | re.M):
            groups[name] = re.findall(r'\["(\w+)", "(\w+)", (true|false)\]', body)
        table = re.search(r"const FIELDS: Record<MarketplaceFulfillmentKind, Triple\[\]> = \{(.*?)^\};",
                          source, re.S | re.M).group(1)
        out: dict[str, list[tuple[str, str, bool]]] = {}
        for kind, body in re.findall(r"^  (\w+): \[(.*?)\],\n(?=  \w+:|\Z)", table, re.S | re.M):
            fields: list[tuple[str, str, bool]] = []
            for token in re.finditer(r'\.\.\.(\w+)|\["(\w+)", "(\w+)", (true|false)\]', body):
                if token.group(1):
                    fields.extend(groups[token.group(1)])
                else:
                    fields.append((token.group(2), token.group(3), token.group(4)))
            out[kind] = [(key, ftype, flag == "true" if isinstance(flag, str) else flag)
                         for key, ftype, flag in fields]
        return out

    def test_every_server_kind_exists_on_the_client_with_the_same_fields(self):
        client = self._client_fields()
        for kind, spec in mf._FIELDS.items():
            assert kind in client, f"{kind} is missing from marketplaceFulfillment.ts"
            assert client[kind] == [tuple(field) for field in spec], kind

    def test_the_client_declares_no_kind_the_server_does_not_know(self):
        assert set(self._client_fields()) <= set(mf.KINDS)

    def test_the_undecided_kinds_ask_for_nothing_on_either_side(self):
        client = self._client_fields()
        for kind in mf.UNDECIDED_KINDS:
            assert client[kind] == []
