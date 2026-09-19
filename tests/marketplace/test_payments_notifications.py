"""The seller and buyer payment notification path.

These are behavioural tests over the mapping and the rendering, not over the
delivery engine — the engine is exercised by its own suite. What is asserted
here is the set of properties a seller's money depends on:

* every event has an email, and every email has an event;
* every field a template reads survives the safety allowlist;
* nothing outside the allowlist ever reaches a notification row;
* a Stripe redelivery produces the same dedupe key, so nobody is told twice;
* the transparency copy does not claim something the charge model makes false.
"""

from __future__ import annotations

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from services import payments_email_templates as templates  # noqa: E402
from services import payments_notifications as notifications  # noqa: E402


TEMPLATE_SOURCE = open(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                 "services", "payments_email_templates.py"),
    encoding="utf-8",
).read()

TEMPLATE_FIELDS = set(re.findall(r"ctx\.get\(.([a-z_]+).\)", TEMPLATE_SOURCE))

SAMPLE_CONTEXT = {
    "seller_first_name": "Ada",
    "buyer_first_name": "Grace",
    "store_name": "Ada Labs",
    "application_id": "41",
    "reviewer_message": "We need a clearer photo of your ID.",
    "seller_application_status": "approved",
    "stripe_connect_status": "payments_ready",
    "card_payment_status": "enabled",
    "payout_status": "enabled",
    "order_id": "9001",
    "order_reference": "PS-9001",
    "item_summary": "Vintage 50mm lens",
    "amount_cents": 4250,
    "seller_net_cents": 4038,
    "currency": "USD",
    "payout_id": "po_1TestOnly",
    "dispute_id": "dp_1TestOnly",
    "arrival_date": "September 25, 2026",
    "requirements": "individual.id_number, external_account",
    "tracking_reference": "1Z999AA10123456784",
}


class TemplateEventPairing(unittest.TestCase):
    def test_every_event_has_a_template(self):
        missing = {
            event: spec["template"]
            for event, spec in notifications.SPECS.items()
            if spec["template"] not in templates.TEMPLATES
        }
        self.assertEqual({}, missing, "an event that cannot render is an event nobody is emailed about")

    def test_every_template_has_an_event(self):
        claimed = {spec["template"] for spec in notifications.SPECS.values()}
        orphans = sorted(set(templates.TEMPLATES) - claimed)
        self.assertEqual([], orphans, "a template no event reaches is dead copy")

    def test_every_event_is_registered_with_the_engine(self):
        # An event missing from the registry resolves to category "system",
        # which is not in EMAIL_DEFAULT_CATEGORIES — the email would be dropped
        # by policy and nothing would say so.
        for event in notifications.SPECS:
            self.assertIn(event, notifications.EVENT_DEFINITIONS)
            self.assertIn(
                notifications.EVENT_DEFINITIONS[event]["category"],
                {"marketplace", "payments"},
                f"{event} must sit in a category the email channel serves",
            )


class ContextSafety(unittest.TestCase):
    def test_allowlist_covers_every_template_field(self):
        missing = sorted(TEMPLATE_FIELDS - notifications.SAFE_CONTEXT_KEYS)
        self.assertEqual(
            [], missing,
            "these fields are read by a template but stripped before it renders, so they arrive blank",
        )

    def test_dedupe_fields_are_allowlisted(self):
        for event, spec in notifications.SPECS.items():
            for field in spec["dedupe_fields"]:
                self.assertIn(
                    field, notifications.SAFE_CONTEXT_KEYS,
                    f"{event} dedupes on {field}, which is stripped before the key is built",
                )

    def test_forbidden_material_cannot_reach_a_notification(self):
        payload = notifications.build_event("seller_approved", 7, {
            "seller_first_name": "Ada",
            "card_number": "4242424242424242",
            "cvv": "123",
            "iban": "GB29NWBK60161331926819",
            "stripe_secret_key": "sk_live_not_a_real_key",
            "id_document": "passport.png",
            "verification_code": "884213",
            "password": "hunter2",
        })
        serialized = repr(payload)
        for forbidden in ("4242", "123456", "NWBK", "sk_live", "passport.png", "884213", "hunter2"):
            self.assertNotIn(forbidden, serialized, f"{forbidden!r} reached the notification payload")

    def test_safe_context_coerces_to_json_scalars(self):
        cleaned = notifications.safe_context({
            "amount_cents": 4250,
            "store_name": object(),
            "currency": None,
        })
        self.assertEqual(4250, cleaned["amount_cents"])
        self.assertIsInstance(cleaned["store_name"], str)
        self.assertNotIn("currency", cleaned, "a None value carries no information and should be dropped")


class Idempotency(unittest.TestCase):
    def test_same_stripe_object_yields_the_same_key(self):
        first = notifications.build_event("payout_paid", 7, {"payout_id": "po_1", "amount_cents": 4250})
        replay = notifications.build_event("payout_paid", 7, {"payout_id": "po_1", "amount_cents": 4250})
        self.assertEqual(first["dedupe_key"], replay["dedupe_key"])

    def test_a_different_payout_is_a_different_notification(self):
        first = notifications.build_event("payout_paid", 7, {"payout_id": "po_1"})
        second = notifications.build_event("payout_paid", 7, {"payout_id": "po_2"})
        self.assertNotEqual(first["dedupe_key"], second["dedupe_key"])

    def test_two_sellers_never_share_a_key(self):
        one = notifications.build_event("seller_approved", 7, {"application_id": "41"})
        two = notifications.build_event("seller_approved", 8, {"application_id": "41"})
        self.assertNotEqual(one["dedupe_key"], two["dedupe_key"])

    def test_routine_account_updates_cannot_resend_the_ready_email(self):
        # Stripe sends account.updated often. stripe_account_ready and
        # card_payments_enabled dedupe on the seller alone precisely so the
        # tenth one does not send an eleventh "you're verified" email.
        for event in ("stripe_account_ready", "card_payments_enabled"):
            self.assertEqual((), notifications.SPECS[event]["dedupe_fields"], event)
            noisy = notifications.build_event(event, 7, {"requirements": "changed"})
            quiet = notifications.build_event(event, 7, {})
            self.assertEqual(noisy["dedupe_key"], quiet["dedupe_key"], event)

    def test_a_new_stripe_requirement_can_notify_again(self):
        first = notifications.build_event("stripe_verification_required", 7, {"requirements": "id_number"})
        second = notifications.build_event("stripe_verification_required", 7, {"requirements": "external_account"})
        self.assertNotEqual(
            first["dedupe_key"], second["dedupe_key"],
            "a second, different requirement must reach the seller or they wait forever",
        )


class OrderLifecycleChannels(unittest.TestCase):
    """The order events already had an in-app row before this module existed.

    ``pulse_emit_payment_checkout_event`` and ``notify_user`` write the in-app
    notification and send the push for a paid order. Email was the only missing
    channel, so those two events are emitted email-only — otherwise the buyer
    sees the same order twice in one feed.
    """

    def test_email_only_requests_email_and_suppresses_the_legacy_mirror(self):
        payload = notifications.build_event(
            "payment_succeeded", 7, {"order_id": "9"}, email_only=True)
        self.assertEqual(["email"], payload["channels"])
        self.assertTrue(payload["metadata"]["skip_pulse_legacy_mirror"])

    def test_the_default_still_uses_every_channel(self):
        payload = notifications.build_event("payout_paid", 7, {"payout_id": "po_1"})
        self.assertEqual(list(notifications.CHANNELS), payload["channels"])
        self.assertFalse(payload["metadata"]["skip_pulse_legacy_mirror"])

    def test_email_only_does_not_change_the_dedupe_key(self):
        # A redelivery must collide whichever channel set it asked for.
        quiet = notifications.build_event("payment_succeeded", 7, {"order_id": "9"}, email_only=True)
        loud = notifications.build_event("payment_succeeded", 7, {"order_id": "9"})
        self.assertEqual(loud["dedupe_key"], quiet["dedupe_key"])

    def test_sms_is_never_a_channel(self):
        self.assertNotIn("sms", notifications.CHANNELS)


class Rendering(unittest.TestCase):
    def test_every_template_renders(self):
        for key in templates.template_keys():
            with self.subTest(template=key):
                rendered = templates.render(key, SAMPLE_CONTEXT)
                self.assertTrue(rendered["subject"].strip())
                self.assertTrue(rendered["text"].strip())
                self.assertIn("<html", rendered["html"].lower())

    def test_email_is_rendered_from_the_event_metadata(self):
        payload = notifications.build_event("payout_paid", 7, SAMPLE_CONTEXT)
        rendered = notifications.render_email(payload["metadata"])
        self.assertIsNotNone(rendered)
        self.assertIn("42.50", rendered["subject"])

    def test_a_non_payment_notification_is_left_alone(self):
        self.assertIsNone(notifications.render_email({"conversation_id": 4}))
        self.assertIsNone(notifications.render_email(None))

    def test_an_unknown_template_degrades_instead_of_losing_the_notification(self):
        self.assertIsNone(notifications.render_email({"email_template": "not_a_template"}))

    def test_links_resolve_to_routes_that_exist(self):
        known = {
            "/pulse/merchant/payouts", "/pulse/merchant/dashboard", "/pulse/merchant/apply",
            "/pulse/seller-store", "/pulse/orders", "/pulse/help", "/terms", "/privacy",
        }
        for key in templates.template_keys():
            rendered = templates.render(key, SAMPLE_CONTEXT)
            for url in re.findall(r'href="([^"]+)"', rendered["html"]):
                if not url.startswith("https://pulsesoc.com"):
                    continue
                path = url[len("https://pulsesoc.com"):].split("?")[0]
                self.assertIn(path, known, f"{key} links to {path}, which is not a route")


class EmailClientSafety(unittest.TestCase):
    """Constraints imposed by Outlook, Gmail and dark mode rather than by taste."""

    def test_no_script_or_external_stylesheet(self):
        for key in templates.template_keys():
            html = templates.render(key, SAMPLE_CONTEXT)["html"]
            self.assertNotIn("<script", html.lower(), key)
            self.assertNotIn("<link", html.lower(), key)
            self.assertNotIn("@import", html.lower(), key)

    def test_no_rgba_colours(self):
        # Word's rendering engine drops rgba() outright, so the element loses
        # its background rather than falling back to something readable.
        for key in templates.template_keys():
            html = templates.render(key, SAMPLE_CONTEXT)["html"]
            self.assertNotIn("rgba(", html, key)

    def test_every_template_has_a_plain_text_alternative(self):
        for key in templates.template_keys():
            text = templates.render(key, SAMPLE_CONTEXT)["text"]
            self.assertNotIn("<", text, f"{key} leaked markup into the plain-text part")


class TransparencyCopy(unittest.TestCase):
    def test_payments_are_attributed_to_stripe(self):
        html = templates.render("seller_approved", SAMPLE_CONTEXT)["html"]
        self.assertIn("Stripe", html)

    def test_no_claim_that_pulsesoc_never_holds_money(self):
        # Under separate charges and transfers the buyer pays the platform, so
        # PulseSoc does hold the funds between charge and transfer. Publishing
        # the opposite would be false, not merely imprecise.
        for key in templates.template_keys():
            body = templates.render(key, SAMPLE_CONTEXT)["html"].lower()
            for claim in ("never touches", "never holds", "never hold your money", "does not hold"):
                self.assertNotIn(claim, body, f"{key} makes a funds-custody claim the charge model contradicts")

    def test_security_notice_tells_sellers_what_pulsesoc_will_never_ask_for(self):
        html = templates.render("seller_approved", SAMPLE_CONTEXT)["html"].lower()
        self.assertTrue(
            any(term in html for term in ("card number", "password", "full card")),
            "the anti-phishing notice is the seller's only defence against a spoofed payout email",
        )


if __name__ == "__main__":
    unittest.main()
