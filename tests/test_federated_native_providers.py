"""Which provider buttons a phone is told to draw, and why the server decides.

A native client cannot answer "can this provider complete a sign-in". It can
only answer "can this *device* present the sheet", and the two are different
questions with different answers. `AppleAuthentication.isAvailableAsync()` is
true on every iPhone since iOS 13 regardless of whether PulseSoc holds a single
Apple credential, so a client that gated on the device alone would show a live
Apple button on every phone the moment the login screen shipped: it would open
Apple's real sheet, take a real credential, and then eat the 503 that
`/api/mobile/auth/federated` answers for an unconfigured provider. The member
would read that as PulseSoc rejecting their Apple account, having already
committed an identity choice.

`/api/mobile/auth/providers` exists so the server can answer it instead. The
tests below are about one property above all others: **the set advertised is
the set enforced.** That is asserted behaviourally -- every state drives both
endpoints and compares them -- rather than by observing that the two call a
function with the same name, because the drift that matters is silent and a
shared name is not a shared answer.

The specific drift guarded against is one-directional. An advertisement
*stricter* than enforcement costs a button that would have worked, which is a
missed opportunity. An advertisement *looser* than enforcement is the dead
button above, which costs trust. They are not symmetric, and the second is the
one that reads as fine in a staging environment where everything is set.

## Why `configured()` is not the predicate

The web test for a provider is `configured()`, and it is correct there. It is
wrong here, and the gap between them is the whole reason this file exists: each
adapter's `audiences()` is the web client id *plus* the native client ids, so a
provider that is fully configured for pulsesoc.com and has no native client id
has an audience set containing only the browser's client. Honouring a "phone"
token against that set would mean honouring a token minted for the website.
The native predicate is therefore `configured() AND native_client_ids()`, and
the middle state -- web yes, native no -- is production's current state for
Google, so it is tested explicitly rather than treated as a corner.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Must precede `import bot`: importing it connects and runs init_db() at module
# scope, so an assignment afterwards would be read too late and this suite would
# build its schema in the real development database.
_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="federated_providers_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

# Cleared before import so that whatever the developer or CI happens to have
# exported cannot make the "nothing configured" baseline pass for the wrong
# reason. A fail-closed test that runs in an ambiently-configured process is
# asserting the opposite of what it claims.
for _name in (
    "APPLE_SIGNIN_SERVICES_ID",
    "APPLE_SIGNIN_TEAM_ID",
    "APPLE_SIGNIN_KEY_ID",
    "APPLE_SIGNIN_PRIVATE_KEY",
    "APPLE_SIGNIN_NATIVE_CLIENT_IDS",
    "APPLE_SIGNIN_ENABLED",
    "PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS",
    "GOOGLE_SIGNIN_CLIENT_ID",
    "GOOGLE_SIGNIN_NATIVE_CLIENT_IDS",
    "GOOGLE_SIGNIN_ENABLED",
):
    os.environ.pop(_name, None)

import bot  # noqa: E402

# Not a real key, and never used to sign anything: `configured()` tests for a
# non-empty private key, and these tests never reach a token exchange because
# the configuration gate they exercise sits in front of it.
FAKE_P8 = "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----"

APPLE_WEB = {
    "APPLE_SIGNIN_SERVICES_ID": "com.pulsesoc.web",
    "APPLE_SIGNIN_TEAM_ID": "TEAMID1234",
    "APPLE_SIGNIN_KEY_ID": "KEYID12345",
    "APPLE_SIGNIN_PRIVATE_KEY": FAKE_P8,
}
APPLE_NATIVE = {"APPLE_SIGNIN_NATIVE_CLIENT_IDS": "com.pulsesoc.app"}
GOOGLE_WEB = {"GOOGLE_SIGNIN_CLIENT_ID": "web.apps.googleusercontent.com"}
GOOGLE_NATIVE = {"GOOGLE_SIGNIN_NATIVE_CLIENT_IDS": "ios.apps.googleusercontent.com"}

# Every variable any of these tests sets, so `patch.dict` can clear the ones a
# given state leaves out. Listing them explicitly rather than clearing the whole
# environment keeps DATABASE_URL -- and therefore the temp database -- intact.
ALL_PROVIDER_VARS = tuple(
    list(APPLE_WEB) + list(APPLE_NATIVE) + list(GOOGLE_WEB) + list(GOOGLE_NATIVE)
) + (
    "APPLE_SIGNIN_ENABLED",
    "GOOGLE_SIGNIN_ENABLED",
    "PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS",
)


class FederatedNativeProviderTests(unittest.TestCase):
    """The advertisement endpoint, and its agreement with the enforcement."""

    def setUp(self):
        self.client = bot.webhook_app.test_client()

    def _env(self, **overrides):
        """Put the process in exactly one provider configuration state.

        Clears every provider variable first so a state is defined by what it
        sets, not by what the previous test happened to leave behind. The
        adapters read `os.getenv` at call time -- not at import -- which is what
        makes this possible at all.
        """

        env = {name: "" for name in ALL_PROVIDER_VARS}
        env.update(overrides)
        # `clear=False` keeps DATABASE_URL; the empty strings above do the
        # clearing, and the adapters' `_env` treats empty as absent.
        return mock.patch.dict(os.environ, env, clear=False)

    def _advertised(self):
        response = self.client.get("/api/mobile/auth/providers")
        self.assertEqual(response.status_code, 200)
        body = json.loads(response.data)
        self.assertIs(body.get("ok"), True)
        return body

    def _enforced(self, provider):
        """What the real endpoint answers for a token from this provider.

        The token is deliberately nonsense. The configuration gate under test
        sits in front of verification, so a provider that is natively ready
        reaches the crypto and fails there -- a *different* refusal, which is
        exactly how these tests tell "refused for configuration" apart from
        "refused for a bad token" without holding a signing key.
        """

        response = self.client.post(
            "/api/mobile/auth/federated",
            json={"provider": provider, "id_token": "not.a.token", "nonce": "n"},
        )
        body = {}
        try:
            body = json.loads(response.data)
        except ValueError:
            pass
        return response.status_code, str(body.get("error_code") or body.get("error") or "")

    # ---- the baseline, and the state production is actually in --------------

    def test_nothing_configured_advertises_nothing(self):
        """An unconfigured server offers no buttons at all."""

        with self._env():
            self.assertEqual(self._advertised()["available"], [])

    def test_web_configuration_alone_does_not_advertise_to_a_phone(self):
        """The drift case, and production's current state for Google.

        Google is configured for the website here. `configured()` is true, the
        web login page would render a Google button, and a phone must still be
        told no -- because the only audience in the set is the browser's client
        id, so a token presented from a phone could only be a token minted for
        the browser.
        """

        with self._env(**GOOGLE_WEB):
            from services import google_identity

            # Stated as a precondition so that a future change making
            # `configured()` false would fail here, loudly, rather than making
            # the assertion below pass for an unrelated reason.
            self.assertTrue(google_identity.configured())
            self.assertEqual(google_identity.native_client_ids(), ())
            self.assertEqual(self._advertised()["available"], [])

    def test_native_client_id_alone_does_not_advertise(self):
        """The mirror: a native client id with no provider configuration.

        Half-configured in the other direction. `native_client_ids()` is
        non-empty and there is still nothing to verify a token against, so the
        predicate must be a conjunction rather than either half.
        """

        with self._env(**GOOGLE_NATIVE):
            self.assertEqual(self._advertised()["available"], [])

    def test_web_and_native_together_advertise_google(self):
        with self._env(**{**GOOGLE_WEB, **GOOGLE_NATIVE}):
            self.assertEqual(self._advertised()["available"], ["google"])

    def test_apple_needs_every_credential_plus_a_bundle_id(self):
        with self._env(**{**APPLE_WEB, **APPLE_NATIVE}):
            self.assertIn("apple", self._advertised()["available"])

        # Each credential is load-bearing: drop any one and the provider must
        # disappear. Asserted per-variable because a predicate that checks three
        # of four looks identical to a correct one until the fourth is the one
        # that is missing.
        for omitted in APPLE_WEB:
            partial = {k: v for k, v in APPLE_WEB.items() if k != omitted}
            with self.subTest(omitted=omitted):
                with self._env(**{**partial, **APPLE_NATIVE}):
                    self.assertEqual(self._advertised()["available"], [])

        # And the bundle id specifically, which is the native-only half.
        with self._env(**APPLE_WEB):
            self.assertEqual(self._advertised()["available"], [])

    def test_the_associated_bundle_ids_fallback_counts_as_native(self):
        """`PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS` is the documented alternative.

        The adapter accepts it in place of `APPLE_SIGNIN_NATIVE_CLIENT_IDS`. If
        this endpoint read only the first variable it would under-advertise on a
        server configured the documented second way -- a dead *absence* rather
        than a dead button, but still a disagreement with what the POST endpoint
        would have honoured.
        """

        with self._env(
            **APPLE_WEB, PULSESOC_APPLE_ASSOCIATED_BUNDLE_IDS="com.pulsesoc.app"
        ):
            self.assertIn("apple", self._advertised()["available"])

    def test_the_off_switch_beats_a_complete_configuration(self):
        """A provider turned off is not advertised, however well configured.

        The explicit variable only ever turns a provider *off*, and this is the
        state an operator uses to withdraw a provider in an incident. If the
        advertisement ignored it, the buttons would outlive the shutdown.
        """

        for off in ("0", "false", "no", "off"):
            with self.subTest(value=off):
                with self._env(
                    **{**GOOGLE_WEB, **GOOGLE_NATIVE}, GOOGLE_SIGNIN_ENABLED=off
                ):
                    self.assertEqual(self._advertised()["available"], [])
                    # And the POST endpoint agrees, which is the property that
                    # makes the withdrawal real rather than cosmetic.
                    status, code = self._enforced("google")
                    self.assertEqual(status, 503)
                    self.assertEqual(code, "provider_config_error")

    # ---- the property this file exists for ---------------------------------

    def test_advertised_and_enforced_agree_in_every_configuration(self):
        """Drive both endpoints through a matrix and compare their answers.

        This is the anti-drift assertion, and it is behavioural on purpose. The
        two call sites share a predicate function today; this test keeps them
        honest if somebody later inlines one of them, which is precisely the
        change that would pass review and break the product.
        """

        states = {
            "nothing": {},
            "google web only": GOOGLE_WEB,
            "google native only": GOOGLE_NATIVE,
            "google fully native": {**GOOGLE_WEB, **GOOGLE_NATIVE},
            "apple web only": APPLE_WEB,
            "apple fully native": {**APPLE_WEB, **APPLE_NATIVE},
            "both fully native": {
                **APPLE_WEB,
                **APPLE_NATIVE,
                **GOOGLE_WEB,
                **GOOGLE_NATIVE,
            },
            "google off despite config": {
                **GOOGLE_WEB,
                **GOOGLE_NATIVE,
                "GOOGLE_SIGNIN_ENABLED": "0",
            },
        }

        for label, overrides in states.items():
            with self.subTest(state=label):
                with self._env(**overrides):
                    advertised = set(self._advertised()["available"])

                    for provider in ("apple", "google"):
                        status, code = self._enforced(provider)
                        refused_for_config = (
                            status == 503 and code == "provider_config_error"
                        )

                        if provider in advertised:
                            # Advertised: must NOT be refused for configuration.
                            # It will still be refused -- the token is nonsense
                            # -- but for the token, which is the client's
                            # problem to fix and not a dead button.
                            self.assertFalse(
                                refused_for_config,
                                f"{provider} is advertised but refused as unconfigured "
                                f"in state {label!r}: this is the dead-button bug",
                            )
                        else:
                            # Not advertised: must be refused for configuration.
                            # If it were honoured, the client would be
                            # under-offering a provider that works -- the
                            # harmless direction, but still a disagreement.
                            self.assertTrue(
                                refused_for_config,
                                f"{provider} is not advertised yet not refused as "
                                f"unconfigured in state {label!r}: the advertisement "
                                f"is stricter than the enforcement",
                            )

    # ---- what the response may and may not contain -------------------------

    def test_answers_without_a_session(self):
        """The caller is by definition not signed in yet.

        A login screen asks this before any credential exists. If it required
        authentication the buttons could never render.
        """

        with self._env(**{**GOOGLE_WEB, **GOOGLE_NATIVE}):
            response = self.client.get("/api/mobile/auth/providers")
            self.assertEqual(response.status_code, 200)

    def test_reports_configuration_and_nothing_else(self):
        """No credential material, and nothing that varies per caller.

        This endpoint is public, so its body is public. Client ids are
        identifiers rather than secrets and still have no business here: the
        client needs a provider name and a label to draw a button, and anything
        more would be a disclosure with no consumer.
        """

        with self._env(**{**APPLE_WEB, **APPLE_NATIVE, **GOOGLE_WEB, **GOOGLE_NATIVE}):
            body = self._advertised()

            self.assertEqual(sorted(body.keys()), ["available", "ok", "providers"])
            for option in body["providers"]:
                self.assertEqual(sorted(option.keys()), ["label", "provider"])

            # The private key, the team id, the key id and every client id, by
            # value, anywhere in the serialised body.
            serialised = json.dumps(body)
            for secret in (
                FAKE_P8,
                APPLE_WEB["APPLE_SIGNIN_TEAM_ID"],
                APPLE_WEB["APPLE_SIGNIN_KEY_ID"],
                APPLE_WEB["APPLE_SIGNIN_SERVICES_ID"],
                APPLE_NATIVE["APPLE_SIGNIN_NATIVE_CLIENT_IDS"],
                GOOGLE_WEB["GOOGLE_SIGNIN_CLIENT_ID"],
                GOOGLE_NATIVE["GOOGLE_SIGNIN_NATIVE_CLIENT_IDS"],
            ):
                self.assertNotIn(secret, serialised)

    def test_available_is_a_list_of_plain_strings(self):
        """The client's gate is `available.includes(provider)`.

        A dict or a list of objects there would make that a membership test
        against the wrong thing, and `.includes` on a string would make it a
        substring test. The client shape-checks the answer too -- this is the
        other end of the same contract.
        """

        with self._env(**{**APPLE_WEB, **APPLE_NATIVE, **GOOGLE_WEB, **GOOGLE_NATIVE}):
            available = self._advertised()["available"]
            self.assertIsInstance(available, list)
            for entry in available:
                self.assertIsInstance(entry, str)

    def test_a_broken_adapter_costs_its_own_button_only(self):
        """An adapter that raises is not an advertisement.

        Fail closed per provider: one provider's misconfiguration throwing must
        not take out the other provider's working button, and must not 500 a
        login screen.
        """

        with self._env(**{**APPLE_WEB, **APPLE_NATIVE, **GOOGLE_WEB, **GOOGLE_NATIVE}):
            with mock.patch.object(
                bot.google_identity,
                "native_client_ids",
                side_effect=RuntimeError("config backend down"),
            ):
                body = self._advertised()
                self.assertEqual(body["available"], ["apple"])


if __name__ == "__main__":
    unittest.main()
