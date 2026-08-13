from services.marketplace_payment_errors import stripe_response_value


class StripeResource:
    def __init__(self):
        self.id = "pi_live_safe_id"
        self.client_secret = "pi_live_safe_id_secret_redacted"

    def __getattr__(self, name):
        # Mirrors generated Stripe resources: asking for the nonexistent
        # ``get`` method raises an attribute error whose text is just "get".
        raise AttributeError(name)


def test_stripe_response_value_supports_generated_resource_objects():
    intent = StripeResource()

    assert stripe_response_value(intent, "id") == "pi_live_safe_id"
    assert stripe_response_value(intent, "client_secret") == "pi_live_safe_id_secret_redacted"
    assert stripe_response_value(intent, "missing") == ""


def test_stripe_response_value_keeps_mapping_test_doubles_supported():
    intent = {"id": "pi_test_safe_id", "client_secret": "pi_test_safe_id_secret_redacted"}

    assert stripe_response_value(intent, "id") == "pi_test_safe_id"
    assert stripe_response_value(intent, "client_secret") == "pi_test_safe_id_secret_redacted"
