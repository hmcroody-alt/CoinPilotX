"""Answering a call must not cancel the call you just answered.

Accepting moves a call off the `ringing` edge, and that edge fans an
`answered_elsewhere` VoIP cancel out to every device belonging to the answering
user. That fan-out is deliberate and load-bearing: a participant row is per
*user*, not per device, so once the answering user's row flips to `joined` the
ringing-participants query can no longer see them — while their other handsets
are still showing a full-screen CallKit UI for a call somebody already took. The
only way out of that UI is to answer a dead call, so `_voip_stop_ringing` adds the
actor back into the recipient set on purpose.

The cost of that decision is that the answering device is now inside the blast
radius of its own cancel, and the cancel carries the *same* call UUID as the call
it just answered. On iOS the payload lands in `AppDelegate`'s `cancel_call`
branch, which calls `endCall(withUUID:reason:)` on the UUID the phone is
currently connected on. CallKit tears the system UI down as `answeredElsewhere`
while the media session keeps running underneath — the call is live and the
phone says it ended. Reported from a locked device as "PulseSoc Audio ended"
over a call that was in fact still connected.

Exactly one thing prevents that: the accept body naming the device that answered,
read by `_answering_device_ids` and threaded into the exclusion set. This file
pins both halves — that a named device is spared, and that an unnamed one is not.
The negative control is the whole point: it is what says the client MUST send the
id, and it is the state production shipped in.

These are mutation tests. Each names the change it is written to fail against.
"""

import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="answered_elsewhere_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

from services import pulsesoc_communications_engine as engine  # noqa: E402

ACTOR_ID = 77301
OTHER_PARTICIPANT_ID = 77302

ANSWERING_DEVICE = "native-ios-answering-0123456789"
SECOND_DEVICE = "native-ios-ipad-9876543210"


class _Cursor:
    """Just enough cursor to drive `_voip_stop_ringing`'s one query.

    A real sqlite fixture would work, but the query result is the entire input
    this function takes from the database, and stubbing it makes the *ringing
    set* an explicit part of each test rather than a consequence of participant
    rows written three helpers away. The distinction the tests turn on — which
    device is excluded — lives after this query, not in it.
    """

    def __init__(self, ringing_user_ids):
        self._ringing = list(ringing_user_ids)
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))

    def fetchall(self):
        return [{"user_id": user_id} for user_id in self._ringing]


def _call():
    return {"id": 5150, "public_id": "call_answered_elsewhere", "conversation_id": 9}


class _Fanout:
    """Records who the cancel went to, and which devices each recipient spared."""

    def __init__(self):
        self.calls = []

    def __call__(self, cur, call, recipient_id, reason, exclude_device_ids=None):
        self.calls.append(
            {
                "recipient_id": int(recipient_id),
                "reason": reason,
                "excluded": {str(v) for v in (exclude_device_ids or [])},
            }
        )
        return {"claimed_device_ids": []}

    def for_recipient(self, recipient_id):
        matches = [c for c in self.calls if c["recipient_id"] == int(recipient_id)]
        assert len(matches) <= 1, f"recipient {recipient_id} was cancelled {len(matches)} times"
        return matches[0] if matches else None


def _stop_ringing(ringing_user_ids, exclude_device_ids, new_status="accepted"):
    """Run the ringing teardown with the VoIP transport replaced by a recorder."""
    fanout = _Fanout()
    cur = _Cursor(ringing_user_ids)
    with mock.patch.object(engine.pulsesoc_voip_push, "is_configured", return_value=True), \
         mock.patch.object(engine.pulsesoc_voip_push, "cancel_devices", fanout), \
         mock.patch.object(engine, "_event", lambda *a, **k: None):
        engine._voip_stop_ringing(cur, _call(), ACTOR_ID, new_status, "", exclude_device_ids)
    return fanout


class AnsweringDeviceIdsTest(unittest.TestCase):
    """What the client has to put in the accept body for any of this to work."""

    def test_reads_the_keys_the_native_client_sends(self):
        """MUTATION: drop either key from `_answering_device_ids`.

        `api/calls.ts` sends `device_id` and `installation_id` carrying the same
        value, because the two names exist for historical reasons and a backend
        that only learned one of them must still spare this device. If the reader
        stops honouring one, whichever name the client happens to lead with
        becomes load-bearing by accident.
        """
        ids = engine._answering_device_ids({"source": "native", "device_id": ANSWERING_DEVICE})
        self.assertIn(ANSWERING_DEVICE, ids)

        ids = engine._answering_device_ids({"source": "native", "installation_id": ANSWERING_DEVICE})
        self.assertIn(ANSWERING_DEVICE, ids)

    def test_reads_the_id_nested_under_device_info(self):
        """The CallKit answer handler and the call store build accept bodies
        separately and only one of them nests the id. Both must be understood."""
        ids = engine._answering_device_ids({"device_info": {"installation_id": ANSWERING_DEVICE}})
        self.assertIn(ANSWERING_DEVICE, ids)

    def test_ignores_blank_and_missing_ids_rather_than_excluding_everything(self):
        """MUTATION: keep empty strings in the returned list.

        A blank id must not reach the exclusion set. `cancel_devices` compares
        `str(device_id)` against that set, and a stored device id that is somehow
        empty would then match it and be spared — a device left ringing forever
        because the *other* device sent a blank field.
        """
        self.assertEqual(engine._answering_device_ids({"source": "native"}), [])
        self.assertEqual(engine._answering_device_ids({"device_id": "", "installation_id": "  "}), [])
        self.assertEqual(engine._answering_device_ids(None), [])


class AnsweredElsewhereExclusionTest(unittest.TestCase):
    """The fan-out itself: who gets cancelled, and who is spared."""

    def test_answering_device_is_spared_when_the_accept_body_names_it(self):
        """MUTATION: pass `set()` instead of `excluded` for the actor.

        This is the fix. The actor is still cancelled — their iPad has to stop
        ringing — but the handset that answered is named in the exclusion set, so
        `cancel_devices` filters it out and the phone never receives a cancel for
        the call it is on.
        """
        excluded_ids = engine._answering_device_ids({"device_id": ANSWERING_DEVICE})
        fanout = _stop_ringing([OTHER_PARTICIPANT_ID], excluded_ids)

        actor_cancel = fanout.for_recipient(ACTOR_ID)
        self.assertIsNotNone(actor_cancel, "the answering user's other devices must still be told")
        self.assertEqual(actor_cancel["excluded"], {ANSWERING_DEVICE})
        self.assertEqual(actor_cancel["reason"], "answered_elsewhere")

    def test_without_a_device_id_the_phone_cancels_its_own_call(self):
        """NEGATIVE CONTROL — the shipped bug, pinned as a fact about the backend.

        This is not an invariant to preserve; it is the reason the client change
        is mandatory. With nothing naming the answering device the exclusion set
        is empty, so the actor's cancel reaches every one of their devices
        including the one holding the live call. If this ever stops being true
        because the backend learned to infer the device some other way, the
        client-side requirement has changed and this test should be revisited
        deliberately rather than silently.
        """
        fanout = _stop_ringing([OTHER_PARTICIPANT_ID], engine._answering_device_ids({"source": "native"}))

        actor_cancel = fanout.for_recipient(ACTOR_ID)
        self.assertIsNotNone(actor_cancel)
        self.assertEqual(actor_cancel["excluded"], set())

    def test_other_participants_are_never_spared_a_device(self):
        """MUTATION: apply `excluded` to every recipient instead of the actor only.

        The exclusion is scoped to the answering user. Leaking it to the other
        party would be far worse than the bug it fixes: the caller's own device
        ids are unrelated strings, but a collision — or a future change that
        derives the id less uniquely — would leave a third party ringing with no
        cancel coming, and `_voip_stop_ringing` only runs on the single
        `ringing -> anything` edge, so no later transition would clear it.
        """
        fanout = _stop_ringing(
            [OTHER_PARTICIPANT_ID], engine._answering_device_ids({"device_id": ANSWERING_DEVICE})
        )

        other_cancel = fanout.for_recipient(OTHER_PARTICIPANT_ID)
        self.assertIsNotNone(other_cancel, "the other party must stop ringing")
        self.assertEqual(other_cancel["excluded"], set())

    def test_declining_does_not_spare_any_device(self):
        """MUTATION: drop the `answered` guard on the skip.

        A decline or a caller hangup is not an answer: no device of the actor is
        on a live call, so every one of them must stop ringing. Honouring an
        exclusion here would strand the CallKit UI on the very device the id
        names. The reason string also has to differ — `answered_elsewhere` must
        not be logged as a missed call, and a real cancel must be.
        """
        fanout = _stop_ringing(
            [ACTOR_ID, OTHER_PARTICIPANT_ID],
            engine._answering_device_ids({"device_id": ANSWERING_DEVICE}),
            new_status="declined",
        )

        actor_cancel = fanout.for_recipient(ACTOR_ID)
        self.assertIsNotNone(actor_cancel)
        self.assertEqual(actor_cancel["excluded"], set())
        self.assertNotEqual(actor_cancel["reason"], "answered_elsewhere")


class CancelDeviceFilteringTest(unittest.TestCase):
    """The exclusion set only matters if `cancel_devices` actually applies it."""

    def test_excluded_device_is_filtered_out_of_the_delivery_list(self):
        """MUTATION: ignore `exclude_device_ids` in `cancel_devices`.

        `_voip_stop_ringing` can compute a perfect exclusion set and still cancel
        the live call if the transport does not honour it. Asserting on the
        engine's intent alone would leave that gap untested, so this pins the
        filtering against the device registry rather than the caller.
        """
        from services import pulsesoc_voip_push as voip

        devices = [{"device_id": ANSWERING_DEVICE}, {"device_id": SECOND_DEVICE}]
        delivered = {}

        def _capture(cur, recipient_id, device_list, payload, kind):
            delivered["device_ids"] = [str(d.get("device_id")) for d in device_list]
            return {"claimed_device_ids": delivered["device_ids"], "results": []}

        with mock.patch.object(voip, "is_configured", return_value=True), \
             mock.patch.object(voip, "active_devices", return_value=devices), \
             mock.patch.object(voip, "_deliver", _capture):
            result = voip.cancel_devices(
                None, _call(), ACTOR_ID, "answered_elsewhere", exclude_device_ids={ANSWERING_DEVICE}
            )

        self.assertEqual(result.get("status"), "dispatched")
        self.assertEqual(delivered["device_ids"], [SECOND_DEVICE])

    def test_excluding_the_only_device_sends_nothing_at_all(self):
        """The single-handset case, which is the common one.

        Most users answer on their only device. The exclusion then empties the
        delivery list, and `cancel_devices` must return without dispatching
        rather than falling through to a broadcast — the fallback-to-send
        instinct that is correct for *ringing* a call is exactly wrong here.
        """
        from services import pulsesoc_voip_push as voip

        with mock.patch.object(voip, "is_configured", return_value=True), \
             mock.patch.object(voip, "active_devices", return_value=[{"device_id": ANSWERING_DEVICE}]), \
             mock.patch.object(voip, "_deliver", side_effect=AssertionError("must not dispatch")):
            result = voip.cancel_devices(
                None, _call(), ACTOR_ID, "answered_elsewhere", exclude_device_ids={ANSWERING_DEVICE}
            )

        self.assertEqual(result.get("status"), "no_voip_device")
        self.assertEqual(result.get("claimed_device_ids"), [])


if __name__ == "__main__":
    unittest.main()
