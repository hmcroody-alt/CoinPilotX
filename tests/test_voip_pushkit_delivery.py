"""PushKit VoIP is the primary iOS incoming-call path; the alert push is fallback.

An APNs *alert* push cannot ring a phone. It posts a banner, and only if the user
granted notification authorization. So iOS incoming calls go out as PushKit VoIP
pushes, which wake the app and let it report the call to CallKit — over the lock
screen, over another app, under Focus. The pre-existing alert push stays exactly
where it was and keeps serving Android, web, and iOS builds with no VoIP token.

The rule the product actually depends on, and what every test here defends:

    A device that was rung over VoIP must NOT also receive the incoming-call
    alert push. Every other device must still receive it.

Get the first half wrong and one handset shows a full-screen system call UI *and*
a banner for the same call, and dismissing one does not stop the other. Get the
second half wrong and a call silently reaches nobody — which is much worse, so
every ambiguous case in the implementation falls back to the alert push.

These are mutation tests: each one is written to fail if a specific invariant is
removed, not merely to exercise the happy path. The invariant each defends is
named in its docstring.

Runs against a temp sqlite file so nothing touches coinpilotx.db.
"""

import json
import os
import sys
import tempfile
import unittest
import uuid
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="voip_tests_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

# Credentials that make `is_configured()` true without being usable. Nothing in
# this file performs real network I/O: the APNs transport is always mocked, and a
# test that accidentally reached Apple would fail on the fake key rather than
# quietly sending a push from CI.
os.environ["APNS_TEAM_ID"] = "TEAMTEST01"
os.environ["APNS_KEY_ID"] = "KEYTEST001"
os.environ["APNS_PRIVATE_KEY"] = "-----BEGIN PRIVATE KEY-----\nnot-a-real-key\n-----END PRIVATE KEY-----"
os.environ["APNS_BUNDLE_ID"] = "com.pulsesoc.app"
os.environ["APNS_USE_SANDBOX"] = "false"

from services import pulsesoc_notification_system as notifications  # noqa: E402
from services import pulsesoc_voip_push as voip  # noqa: E402

USER_ID = 88201
OTHER_USER_ID = 88202


def _use_module_database():
    os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"


def _open():
    """A connection to this module's temp database.

    `services.db.connect()` rather than the engine's `_open_db()` on purpose: the
    latter routes through `bot.db()` and would import the 111k-line Flask monolith
    to test a push module that does not depend on it.
    """
    from services import db as db_service

    conn = db_service.connect()
    return conn, conn.cursor()


def _accepted(*_args, **_kwargs):
    """APNs accepted the push."""
    return {"ok": True, "status": "sent", "apns_id": "test-apns-id", "environment": "production"}


def _rejected(*_args, **_kwargs):
    """APNs refused the push — a transient server-side failure, token still valid."""
    return {"ok": False, "status": "failed", "http_status": 503, "message": "ServiceUnavailable"}


def _unregistered(*_args, **_kwargs):
    """APNs says this token is dead (app uninstalled)."""
    return {"ok": False, "status": "invalid_device", "http_status": 410, "message": "Unregistered"}


class VoipBase(unittest.TestCase):
    def setUp(self):
        _use_module_database()
        self.conn, self.cur = _open()
        notifications.ensure_schema()
        voip.ensure_schema(self.cur)
        # Full isolation between tests: these tables are keyed by user, and a
        # leftover row from a previous test is exactly the kind of thing that
        # makes a suppression assertion pass for the wrong reason.
        self.cur.execute("DELETE FROM voip_push_tokens WHERE user_id IN (?, ?)", (USER_ID, OTHER_USER_ID))
        self.cur.execute("DELETE FROM notification_device_tokens WHERE user_id IN (?, ?)", (USER_ID, OTHER_USER_ID))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()

    def _add_alert_device(self, device_id, platform="ios", provider="expo", token=None):
        """Register a device in the *alert* push registry (what /api/push/subscribe writes)."""
        now = voip._now()
        self.cur.execute(
            """
            INSERT INTO notification_device_tokens
                (user_id, device_id, platform, push_token, push_provider, enabled, created_at, updated_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
            """,
            (USER_ID, device_id, platform, token or f"ExponentPushToken[{device_id}]", provider, now, now, now),
        )
        self.conn.commit()

    def _add_voip_device(self, device_id, user_id=USER_ID):
        result = voip.register_token(self.cur, user_id, device_id, f"voiptoken-{device_id}")
        self.conn.commit()
        return result

    def _notification(self, claimed=()):
        return {
            "id": 1,
            "recipient_user_id": USER_ID,
            "user_id": USER_ID,
            "type": "incoming_call",
            "title": "Someone is Pulsing You",
            "body": "Voice Connection",
            "deep_link": "/pulse/messages/1",
            "metadata": {voip.claimed_metadata_key(): list(claimed)},
        }


# ---------------------------------------------------------------------------
# 1-3. One call identity, issued by the server
# ---------------------------------------------------------------------------


class CallIdentityTest(unittest.TestCase):
    def test_call_uuid_is_derived_not_generated(self):
        """MUTATION: replace the UUIDv5 derivation with uuid4().

        CallKit names a call by UUID. If the client invents one, the UUID in
        CallKit matches nothing the backend or the push payload knows, so a call
        answered from the lock screen cannot be correlated to the server's call.
        Deriving it from `public_id` makes backend, push, CallKit and the call
        store agree without a schema change.
        """
        first = voip.call_uuid_for("call_abc123xyz")
        second = voip.call_uuid_for("call_abc123xyz")
        self.assertEqual(first, second, "the same call must always map to the same CallKit UUID")
        self.assertNotEqual(first, voip.call_uuid_for("call_different"), "different calls must not collide")
        uuid.UUID(first)  # must be a well-formed UUID or CallKit rejects it

    def test_a_redelivered_push_maps_to_the_same_callkit_call(self):
        """MUTATION: make the UUID depend on send time or a counter.

        APNs may deliver the same VoIP push more than once. Two CallKit calls for
        one real call means the user answers one and the other keeps ringing. The
        UUID being a pure function of the call is what makes this impossible —
        deduplication by construction rather than by bookkeeping.
        """
        call = {"public_id": "call_dedupe01", "call_type": "audio", "conversation_id": 5}
        first = voip._incoming_payload(call, 1, "Ada", 5)
        second = voip._incoming_payload(call, 1, "Ada", 5)
        self.assertEqual(first["uuid"], second["uuid"])

    def test_serialized_call_and_push_payload_agree_on_the_uuid(self):
        """MUTATION: change either derivation independently.

        The push and the REST call object are two different routes to the same
        call and the client may see either first. If they disagree, answering from
        a push and answering from the in-app sheet produce two different calls.
        """
        from services import pulsesoc_communications_engine as engine

        public_id = "call_agree0001"
        payload = voip._incoming_payload({"public_id": public_id, "call_type": "audio"}, 1, "Ada", 1)
        self.assertEqual(payload["uuid"], engine.pulsesoc_voip_push.call_uuid_for(public_id))


# ---------------------------------------------------------------------------
# 4-5. The payload is minimal and carries no media credentials
# ---------------------------------------------------------------------------


class PayloadSafetyTest(unittest.TestCase):
    def test_voip_payload_carries_no_rtc_secrets(self):
        """MUTATION: add the Agora token/app certificate to the payload "to save a round trip".

        A VoIP push is delivered before the user has authenticated anything. The
        device answers first and *then* requests media credentials over an
        authenticated request, so a captured push buys a ringing phone at worst,
        never call media.
        """
        call = {
            "public_id": "call_minimal1",
            "call_type": "video",
            "conversation_id": 9,
            "room_name": "room_x",
            "agora_token": "007eJxSHOULDNEVERAPPEAR",
            "app_certificate": "CERTSHOULDNEVERAPPEAR",
        }
        payload = voip._incoming_payload(call, 42, "Ada", 9)

        # An exact key allowlist, not a substring scan: a substring scan passes
        # the moment someone names the field something the blocklist did not
        # anticipate. Adding a key here should require arguing for it.
        self.assertEqual(
            set(payload),
            {
                "event", "uuid", "call_id", "caller_id", "caller_name",
                "call_type", "has_video", "handle", "conversation_id", "sent_at",
            },
        )
        blob = json.dumps(payload).lower()
        for forbidden in ("agora", "certificate", "app_id", "appid", "room_x", "channel", "007eejx", "007eejx"):
            self.assertNotIn(forbidden, blob, f"VoIP payload must not carry {forbidden!r}")
        self.assertNotIn("shouldneverappear", blob, "no field of the call row may be copied into the push wholesale")

    def test_callkit_handle_is_not_an_internal_user_id(self):
        """MUTATION: set `handle` to the caller's user id.

        CallKit writes the handle into Recents and the system call log — a
        user-visible, exportable surface that is not a place for an internal
        identifier.
        """
        payload = voip._incoming_payload({"public_id": "call_handle01", "call_type": "audio"}, 42, "Ada", 9)
        self.assertNotEqual(str(payload["handle"]), "42")
        self.assertEqual(payload["handle"], payload["uuid"])


# ---------------------------------------------------------------------------
# 6-9. Suppression: VoIP primary, alert fallback, per device
# ---------------------------------------------------------------------------


class SuppressionTest(VoipBase):
    def test_a_voip_rung_device_does_not_also_get_the_alert_push(self):
        """MUTATION: delete the `claimed` filter in `_dispatch_push`.

        THE CORE RULE. One handset must not show a CallKit ring and a banner for
        the same call.
        """
        self._add_alert_device("phone-1")
        result = notifications._dispatch_push(self.cur, self._notification(claimed=["phone-1"]), {})
        self.assertEqual(result.get("status"), "delivered_via_voip")
        self.assertTrue(result.get("ok"), "a VoIP-delivered call is a success, not a missing registration")

    def test_a_device_without_a_voip_token_still_gets_the_alert_push(self):
        """MUTATION: suppress per *user* instead of per device.

        Backward compatibility is the whole reason the alert path still exists.
        Suppressing by user would silence a second phone that has no VoIP token.
        """
        self._add_alert_device("phone-1")
        self._add_alert_device("phone-2")
        with mock.patch.object(
            notifications.push_service, "send_expo_push_token", return_value={"ok": True, "status": "sent"}
        ) as sender:
            notifications._dispatch_push(self.cur, self._notification(claimed=["phone-1"]), {})
        sent_tokens = " ".join(str(call.args[0]) for call in sender.call_args_list)
        self.assertIn("phone-2", sent_tokens, "the device with no VoIP token must still receive the alert push")
        self.assertNotIn("phone-1", sent_tokens, "the VoIP-rung device must not receive the alert push")

    def test_android_and_web_are_never_suppressed(self):
        """MUTATION: suppress by platform or by "is a call" rather than by claimed device id.

        VoIP exists only on iOS. Nothing else can ever be claimed, so nothing else
        may ever lose its alert push.
        """
        self._add_alert_device("android-1", platform="android")
        result = notifications._dispatch_push(self.cur, self._notification(claimed=["phone-1"]), {})
        self.assertNotEqual(result.get("status"), "delivered_via_voip")

    def test_a_failed_voip_send_falls_back_to_the_alert_push(self):
        """MUTATION: add the device to `claimed` before checking the APNs result.

        This is the dangerous direction. Claiming a device whose VoIP push APNs
        refused suppresses its alert push too, and the call reaches nobody. Only
        devices APNs actually accepted may be claimed.
        """
        self._add_voip_device("phone-1")
        call = {"id": 1, "public_id": "call_fail0001", "call_type": "audio", "conversation_id": 1}
        with mock.patch.object(voip, "send_voip_push", _rejected):
            result = voip.ring_devices(self.cur, call, USER_ID, 999, "Ada")
        self.assertEqual(result["claimed_device_ids"], [], "a refused VoIP push must not suppress the alert push")

    def test_an_accepted_voip_send_claims_exactly_that_device(self):
        """MUTATION: claim every device, or claim none.

        The positive control for the test above — proving the empty list there is
        caused by the failure, not by claiming being broken outright.
        """
        self._add_voip_device("phone-1")
        self._add_voip_device("phone-2")
        call = {"id": 1, "public_id": "call_ok000001", "call_type": "audio", "conversation_id": 1}
        with mock.patch.object(voip, "send_voip_push", _accepted):
            result = voip.ring_devices(self.cur, call, USER_ID, 999, "Ada")
        self.assertEqual(sorted(result["claimed_device_ids"]), ["phone-1", "phone-2"])

    def test_unconfigured_apns_claims_nothing(self):
        """MUTATION: return a claimed list before the `is_configured()` guard.

        A deployment with no APNs VoIP key must behave exactly as it did before
        this feature existed. Claiming devices it never rang would turn a missing
        env var into total call-notification silence.
        """
        self._add_voip_device("phone-1")
        with mock.patch.object(voip, "is_configured", return_value=False):
            result = voip.ring_devices(self.cur, {"id": 1, "public_id": "call_x"}, USER_ID, 999, "Ada")
        self.assertEqual(result["claimed_device_ids"], [])
        self.assertEqual(result["status"], "config_missing")


# ---------------------------------------------------------------------------
# 10-12. Token lifecycle and ownership
# ---------------------------------------------------------------------------


class TokenOwnershipTest(VoipBase):
    def test_a_device_moving_accounts_stops_ringing_for_the_old_one(self):
        """MUTATION: drop the `token_hash=? AND user_id<>?` release on register.

        A VoIP token is the device's identity. If the previous account keeps an
        active row, the phone rings for a user who is no longer signed in on it —
        and the ring shows their caller's name on someone else's lock screen.
        """
        self._add_voip_device("shared-phone", user_id=USER_ID)
        voip.register_token(self.cur, OTHER_USER_ID, "shared-phone", "voiptoken-shared-phone")
        self.conn.commit()
        self.assertEqual(voip.active_devices(self.cur, USER_ID), [])
        self.assertEqual(len(voip.active_devices(self.cur, OTHER_USER_ID)), 1)

    def test_revocation_cannot_cross_accounts(self):
        """MUTATION: drop the `user_id` scoping in `revoke_token`.

        An unscoped revoke by raw token lets any signed-in account silence any
        handset whose token it can replay.
        """
        self._add_voip_device("victim-phone", user_id=USER_ID)
        voip.revoke_token(self.cur, user_id=OTHER_USER_ID, token="voiptoken-victim-phone")
        self.conn.commit()
        self.assertEqual(len(voip.active_devices(self.cur, USER_ID)), 1, "another account must not be able to silence this device")

    def test_logout_releases_only_the_device_that_logged_out(self):
        """MUTATION: revoke by user without the device scope.

        Signing out on an iPad must not stop the user's phone from ringing.
        """
        self._add_voip_device("phone-1")
        self._add_voip_device("ipad-1")
        voip.revoke_for_logout(self.cur, USER_ID, "ipad-1")
        self.conn.commit()
        remaining = [d["device_id"] for d in voip.active_devices(self.cur, USER_ID)]
        self.assertEqual(remaining, ["phone-1"])

    def test_an_unregistered_token_is_revoked_not_retried_forever(self):
        """MUTATION: ignore the 410 and leave the row active.

        APNs 410 Unregistered means the app is gone from that device. Keeping the
        row makes every future call pay a doomed APNs round trip in the ring path.
        """
        self._add_voip_device("dead-phone")
        with mock.patch.object(voip, "send_voip_push", _unregistered):
            voip.ring_devices(self.cur, {"id": 1, "public_id": "call_dead0001"}, USER_ID, 999, "Ada")
        self.conn.commit()
        self.assertEqual(voip.active_devices(self.cur, USER_ID), [], "a 410 must deactivate the token")

    def test_tokens_never_reach_the_logs(self):
        """MUTATION: log the token instead of its suffix.

        A VoIP token is a ring credential: whoever holds it can make that handset
        ring full-screen for as long as the token lives.
        """
        token = "voiptoken-abcdefghijklmnop"
        self.assertNotIn(token, voip._token_suffix(token))
        self.assertEqual(len(voip._token_suffix(token)), 8)


# ---------------------------------------------------------------------------
# 13-15. APNs wire contract
# ---------------------------------------------------------------------------


class ApnsContractTest(unittest.TestCase):
    def test_voip_topic_is_the_bundle_id_with_a_voip_suffix(self):
        """MUTATION: send VoIP pushes to the plain bundle topic.

        APNs rejects a VoIP push on the alert topic. This is the single most
        common cause of "the push says it sent but the phone never rings".
        """
        self.assertEqual(voip.voip_topic(), "com.pulsesoc.app.voip")

    def test_voip_topic_is_not_double_suffixed(self):
        """MUTATION: append `.voip` unconditionally.

        An operator who sets APNS_VOIP_BUNDLE_ID to the full VoIP topic — the
        natural reading of the name — must not get `...voip.voip`.
        """
        with mock.patch.dict(os.environ, {"APNS_VOIP_BUNDLE_ID": "com.pulsesoc.app.voip"}):
            self.assertEqual(voip.voip_topic(), "com.pulsesoc.app.voip")

    def test_apns_headers_are_the_ones_voip_requires(self):
        """MUTATION: drop apns-push-type, lower the priority, or set an expiration.

        `apns-push-type: voip` and `apns-priority: 10` are mandatory — APNs
        rejects the request otherwise. `apns-expiration: 0` means "deliver now or
        discard": a call that could not be delivered while it was ringing must not
        arrive later and ring for a call that already ended.
        """
        captured = {}

        class _Response:
            status_code = 200
            headers = {"apns-id": "x"}
            text = ""

        class _Client:
            def __init__(self, *a, **kw):
                captured["http2"] = kw.get("http2")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def post(self, url, headers=None, json=None):
                captured["url"] = url
                captured["headers"] = headers or {}
                return _Response()

        with mock.patch.object(voip, "_apns_jwt", return_value="fake-jwt"), \
                mock.patch.dict(sys.modules, {"httpx": mock.Mock(Client=_Client)}):
            result = voip.send_voip_push("devicetoken123", {"event": "incoming_call"})

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(captured["headers"]["apns-push-type"], "voip")
        self.assertEqual(captured["headers"]["apns-priority"], "10")
        self.assertEqual(captured["headers"]["apns-expiration"], "0")
        self.assertEqual(captured["headers"]["apns-topic"], "com.pulsesoc.app.voip")
        self.assertTrue(captured["http2"], "APNs is HTTP/2 only")
        self.assertTrue(captured["url"].startswith("https://api.push.apple.com/3/device/"))

    def test_sandbox_and_production_never_share_a_host(self):
        """MUTATION: collapse the two hosts into one.

        A development token sent to the production host is rejected, and vice
        versa. This is why a TestFlight build rings and a debug build does not.
        """
        self.assertNotEqual(voip.APNS_HOSTS["sandbox"], voip.APNS_HOSTS["production"])
        self.assertIn("sandbox", voip.APNS_HOSTS["sandbox"])


# ---------------------------------------------------------------------------
# 15b. A wrong APNs host must not be mistaken for a dead token
# ---------------------------------------------------------------------------


def _scripted_httpx(script):
    """An httpx stand-in that answers from `script` and records the hosts it saw.

    `script` is a list of (status_code, body) answered in order. Returns the fake
    module and the list of URLs posted to, so a test can assert both *what* APNs
    replied and *how many* requests it took.
    """
    seen = []

    class _Response:
        def __init__(self, status, body):
            self.status_code = status
            self.text = body
            self.headers = {"apns-id": "fake-apns-id"}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, url, headers=None, json=None):
            seen.append(url)
            status, body = script[min(len(seen) - 1, len(script) - 1)]
            return _Response(status, body)

    return mock.Mock(Client=_Client), seen


BAD_TOKEN = '{"reason":"BadDeviceToken"}'


class ApnsEnvironmentCorrectionTest(VoipBase):
    """`BadDeviceToken` means "wrong token OR wrong host" and cannot be read as either alone."""

    def _send(self, script, environment=""):
        fake, seen = _scripted_httpx(script)
        with mock.patch.object(voip, "_apns_jwt", return_value="fake-jwt"), \
                mock.patch.dict(sys.modules, {"httpx": fake}):
            result = voip.send_voip_push("devicetoken123", {"event": "incoming_call"}, environment)
        return result, seen

    def test_a_live_token_on_the_wrong_host_is_not_treated_as_dead(self):
        """MUTATION: keep classifying every BadDeviceToken as invalid_device.

        This is the whole failure this class exists for. A development-signed build
        holds a *sandbox* token; a deployment with APNS_USE_SANDBOX unset records it
        as `production` and sends it to api.push.apple.com, which answers
        BadDeviceToken. Revoking there is terminal rather than transient: alert-push
        suppression is conditioned on an *active* token, so the phone silently drops
        back to the alert push and never rings through CallKit again.
        """
        result, seen = self._send([(400, BAD_TOKEN), (200, "")], environment="production")

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("status"), "sent")
        self.assertEqual(result.get("environment"), "sandbox")
        self.assertEqual(result.get("environment_corrected"), "sandbox")
        self.assertEqual(len(seen), 2, "the push must be replayed against the other host")
        self.assertTrue(seen[0].startswith("https://api.push.apple.com/"))
        self.assertTrue(seen[1].startswith("https://api.sandbox.push.apple.com/"))

    def test_the_correction_runs_in_both_directions(self):
        """MUTATION: hard-code the replay host to sandbox.

        A TestFlight build on a deployment with APNS_USE_SANDBOX=1 is the same bug
        mirrored, and a one-way fix leaves exactly the release builds that matter
        most unable to ring.
        """
        result, seen = self._send([(400, BAD_TOKEN), (200, "")], environment="sandbox")

        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("environment_corrected"), "production")
        self.assertTrue(seen[1].startswith("https://api.push.apple.com/"))

    def test_a_token_both_hosts_reject_is_still_dead(self):
        """MUTATION: treat the replay as proof the token is alive.

        The retry must not become a way for a genuinely uninstalled app to keep its
        token forever — that token holds alert-push suppression for a device that
        can no longer be rung, which is the one failure mode ending in a silent
        phone.
        """
        result, seen = self._send([(400, BAD_TOKEN), (400, BAD_TOKEN)], environment="production")

        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("status"), "invalid_device")
        self.assertEqual(len(seen), 2)

    def test_unregistered_is_not_replayed(self):
        """MUTATION: replay on any 4xx.

        410 Unregistered is a positive statement that this host knew the token and
        the app is gone. Replaying it doubles APNs traffic for every uninstalled
        app and can only ever produce a second rejection.
        """
        result, seen = self._send([(410, '{"reason":"Unregistered"}')], environment="production")

        self.assertEqual(result.get("status"), "invalid_device")
        self.assertEqual(len(seen), 1)

    def test_an_alert_token_on_the_voip_topic_is_not_replayed(self):
        """MUTATION: fold DeviceTokenNotForTopic into the mismatch check.

        That response means an *alert* token reached the VoIP topic — a registration
        bug the other host rejects identically. Replaying it hides a client defect
        behind twice the traffic.
        """
        result, seen = self._send([(400, '{"reason":"DeviceTokenNotForTopic"}')], environment="production")

        self.assertEqual(result.get("status"), "invalid_device")
        self.assertEqual(len(seen), 1)

    def test_an_accepted_push_is_never_replayed(self):
        """MUTATION: replay unconditionally. Every call would ring the device twice."""
        result, seen = self._send([(200, "")], environment="production")

        self.assertTrue(result.get("ok"))
        self.assertEqual(len(seen), 1)
        self.assertNotIn("environment_corrected", result)

    def test_the_corrected_host_is_persisted_so_the_next_call_costs_one_request(self):
        """MUTATION: correct the host but never write it down.

        Without persistence every call pays a guaranteed failed request first, which
        also delays the ring by a full APNs round trip on the one push where latency
        is the product.
        """
        self._add_voip_device("device-env")
        self.cur.execute(
            "UPDATE voip_push_tokens SET token_environment=? WHERE device_id=?",
            ("production", "device-env"),
        )
        self.conn.commit()

        fake, seen = _scripted_httpx([(400, BAD_TOKEN), (200, "")])
        with mock.patch.object(voip, "_apns_jwt", return_value="fake-jwt"), \
                mock.patch.dict(sys.modules, {"httpx": fake}):
            voip.ring_devices(
                self.cur,
                {"public_id": "call_env", "call_type": "audio"},
                recipient_id=USER_ID,
                caller_id=OTHER_USER_ID,
                caller_name="Caller",
            )
        self.conn.commit()

        self.cur.execute("SELECT token_environment FROM voip_push_tokens WHERE device_id=?", ("device-env",))
        row = self.cur.fetchone()
        stored = row[0] if not hasattr(row, "keys") else row["token_environment"]
        self.assertEqual(stored, "sandbox", "the host that worked must be remembered")

    def test_a_corrected_device_is_still_claimed_for_suppression(self):
        """MUTATION: claim only devices accepted on the first attempt.

        A device that rang through CallKit after the replay has rung. If it is not
        claimed it also receives the alert banner — the duplicate-ring outcome the
        whole suppression path exists to prevent.
        """
        self._add_voip_device("device-claim")
        fake, _seen = _scripted_httpx([(400, BAD_TOKEN), (200, "")])
        with mock.patch.object(voip, "_apns_jwt", return_value="fake-jwt"), \
                mock.patch.dict(sys.modules, {"httpx": fake}):
            result = voip.ring_devices(
                self.cur,
                {"public_id": "call_claim", "call_type": "audio"},
                recipient_id=USER_ID,
                caller_id=OTHER_USER_ID,
                caller_name="Caller",
            )

        self.assertIn("device-claim", result.get("claimed_device_ids", []))


# ---------------------------------------------------------------------------
# 16-17. Only genuine call events may send a VoIP push (Apple compliance)
# ---------------------------------------------------------------------------


class AppleComplianceTest(unittest.TestCase):
    def test_only_ring_and_cancel_send_voip_pushes(self):
        """MUTATION: add a VoIP sender for messages, badges, or sync.

        iOS terminates an app that takes a VoIP push without reporting a call to
        CallKit, and Apple rejects builds that use VoIP pushes for anything else.
        This is a device-level kill, not a style preference — so the set of
        functions that can reach `send_voip_push` is pinned.
        """
        import ast
        import pathlib

        source = pathlib.Path(voip.__file__).read_text()
        tree = ast.parse(source)
        senders = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call) and getattr(inner.func, "id", "") == "send_voip_push":
                    senders.add(node.name)
        self.assertEqual(
            senders,
            {"_deliver"},
            "send_voip_push must have exactly one call site; every new sender needs an Apple-compliance review",
        )

    def test_cancel_is_reachable_so_a_ring_can_always_be_torn_down(self):
        """MUTATION: remove the cancel path.

        Without it the callee keeps a full-screen system call UI for a call that
        no longer exists, and the only way out is to answer a dead call.
        """
        payload = voip._cancel_payload({"public_id": "call_cancel01"}, "declined")
        self.assertEqual(payload["event"], "cancel_call")
        self.assertEqual(payload["uuid"], voip.call_uuid_for("call_cancel01"))

    def test_answered_elsewhere_excludes_the_answering_device(self):
        """MUTATION: drop `exclude_device_ids` from the answered-elsewhere cancel.

        The cancel carries the same call UUID as the answered call. Sending it to
        the phone that just answered ends the call the user is now on.
        """
        conn, cur = _open()
        try:
            voip.ensure_schema(cur)
            cur.execute("DELETE FROM voip_push_tokens WHERE user_id=?", (USER_ID,))
            voip.register_token(cur, USER_ID, "answering-phone", "tok-answering")
            voip.register_token(cur, USER_ID, "idle-ipad", "tok-idle")
            conn.commit()
            with mock.patch.object(voip, "send_voip_push", _accepted):
                result = voip.cancel_devices(
                    cur, {"public_id": "call_elsewhere"}, USER_ID, "answered_elsewhere",
                    exclude_device_ids=["answering-phone"],
                )
            self.assertEqual(result["claimed_device_ids"], ["idle-ipad"])
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
