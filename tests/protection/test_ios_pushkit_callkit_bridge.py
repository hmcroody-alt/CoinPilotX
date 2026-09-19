"""`AppDelegate.swift` is the incoming-call path, and nothing was watching it.

Why this file exists
--------------------
`mobile-native/ios/PulseSoc/AppDelegate.swift` carries the entire native half of
PulseSoc's incoming-call feature: the PushKit registry, the `PKPushRegistryDelegate`
conformance, and the CallKit report that iOS requires in response to every VoIP
push. The Apple capability brief names the PushKit/CallKit foundation as a hard
lock — "NO regression to the working PushKit / CallKit foundation" — and the file
itself is unusually well commented about *why* each line is where it is.

It was nevertheless unguarded. The realtime-audio manifest
(`config/realtime-audio-protected-paths.json`) lists 60-odd paths and every one
of them is TypeScript or Python; no native source is in it. The three existing
iOS protection tests cover neighbouring things and stop short of this file:
`test_ios_build_version_contract.py` pins the marketing version and build number,
`test_ios_aps_environment_contract.py` pins the APNs entitlement wiring, and
`test_ios_native_target_inventory.py` pins the target list and the 16.1 floor.
`tests/test_call_answered_elsewhere_self_cancel.py` reasons *about* this file in
prose and then tests the server-side fanout. Nothing read the Swift.

That gap matters more than it looks, because this file has two properties that
defeat ordinary review:

  * **It is a committed file in a bare workflow that a tool can regenerate.**
    `expo prebuild --clean` emits a stock `AppDelegate.swift` with none of this
    in it. The app still builds. It still launches. It simply never rings again.
  * **Its failure mode is delayed and then permanent.** iOS 13 made it a
    termination offence to accept a VoIP push without reporting a call to
    CallKit, and it enforces that against the push rather than the app's
    intentions. The first few offences kill the process; after that iOS stops
    delivering VoIP pushes to the app *at all*. So a regression here tests fine
    — the early pushes work — and then the feature dies on real devices with no
    error to read and no way back except a user reinstall.

What each assertion defends
---------------------------
Every check below corresponds to a specific way the call path has a plausible
silent death. None of them is enforced by the compiler; all of them would still
build, link, sign and ship.

  1. The registry is created natively at launch. A JS-side registration cannot
     run when the app was launched *by* the push.
  2. The delegate conformance exists at all.
  3. CallKit is told before JS is told, on every branch. This is the iOS 13 rule.
  4. Every early return still calls `completion()`. Not calling it is the same
     offence as not reporting.
  5. No network, bridge, or blocking work happens anywhere in the handler.
  6. `endedReason` still distinguishes "handled on another device" from
     "unanswered", which is the difference between a clean multi-device answer
     and a phantom missed call.
  7. `UIBackgroundModes` still contains `voip`.
  8. The set of bridge call sites is exactly the expected one, so a new call
     into RNCallKeep or RNVoipPushNotification has to be looked at by a person.

On vacuity
----------
The house hazard for a source-reading gate is that every failure of its reader
makes it greener: a regex that stops matching returns nothing, and "nothing" is
indistinguishable from "nothing wrong." `test_the_parse_is_not_vacuous` asserts
independently that the file was found, is substantial, and that the function-body
extraction returned real code — so a rename of the delegate method fails loudly
here instead of quietly disabling the five checks that read that body.
"""

import plistlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP_DELEGATE = ROOT / "mobile-native/ios/PulseSoc/AppDelegate.swift"
INFO_PLIST = ROOT / "mobile-native/ios/PulseSoc/Info.plist"

# The bridge surface this file is willing to vouch for. Both pods are
# Objective-C only and are reached through `PulseSoc-Bridging-Header.h`. Adding a
# row is fine; doing it in the same commit as the call site is the point.
EXPECTED_BRIDGE_CALLS = {
    "RNVoipPushNotificationManager.voipRegistration",
    "RNVoipPushNotificationManager.didUpdate",
    "RNVoipPushNotificationManager.didReceiveIncomingPush",
    "RNCallKeep.reportNewIncomingCall",
    "RNCallKeep.endCall",
}

# Work that must not happen between accepting a VoIP push and reporting the call.
# `await` and `URLSession` are the realistic ones — "just check the call is still
# ringing first" is an extremely natural thing to write here and it is fatal. The
# blocking primitives are listed because the other natural fix, "make it
# synchronous," is worse.
FORBIDDEN_IN_PUSH_HANDLER = (
    "URLSession",
    "await ",
    "DispatchSemaphore",
    "Thread.sleep",
    "RCTBridge",
)

# Server cancel reason -> CXCallEndedReason case. See `endedReason`.
EXPECTED_ENDED_REASONS = {
    "answered_elsewhere": "answeredElsewhere",
    "declined_elsewhere": "declinedElsewhere",
    "declined": "declinedElsewhere",
    "failed": "failed",
}


def _source() -> str:
    return APP_DELEGATE.read_text(encoding="utf-8")


def _function_body(source: str, signature_marker: str) -> str:
    """Return the source from a function's signature to the end of the extension.

    Deliberately crude: Swift brace matching is not worth writing here, and the
    delegate methods are the last thing in their extension. The slice starts at
    the marker and runs to the next `func ` at the same nesting or to the end of
    the file, which is enough to reason about ordering within the method.
    """
    start = source.find(signature_marker)
    if start < 0:
        return ""
    rest = source[start + len(signature_marker):]
    end = rest.find("\n  /// ")
    if end < 0:
        end = rest.find("\n  private func ")
    return rest if end < 0 else rest[:end]


def _returns_without_completion(body: str) -> list[str]:
    """Find `return`s whose innermost enclosing `{ ... }` never mentions completion.

    An earlier version of this check looked at a fixed window of characters
    before each `return`, which was wrong in a way worth recording: a new guard
    inserted immediately below the existing `guard type == .voIP else {
    completion(); return }` inherited *that* guard's `completion()` into its
    window and passed. The window measured proximity, and the invariant is
    containment.

    So track brace depth and judge each `return` by the block it is actually in.
    That also gets the cancel branch right without special-casing it: its
    `return` is not preceded by a literal `completion()` call, because the
    completion is handed to CallKit's completion handler further up the *same*
    block.
    """
    offenders: list[str] = []
    opens: list[int] = []
    index = 0
    while index < len(body):
        char = body[index]
        if char == "{":
            opens.append(index)
        elif char == "}":
            if opens:
                opens.pop()
        elif body.startswith("return", index) and (index == 0 or not body[index - 1].isalnum()):
            after = index + len("return")
            if after >= len(body) or not (body[after].isalnum() or body[after] == "_"):
                block_start = opens[-1] if opens else 0
                block = body[block_start:index]
                if "completion" not in block:
                    line = body.rfind("\n", 0, index) + 1
                    offenders.append(body[line:body.find("\n", index)].strip())
        index += 1
    return offenders


class PushKitRegistrationTest(unittest.TestCase):
    def test_the_voip_registry_is_created_natively_at_launch(self):
        """MUTATION: delete the `voipRegistration()` call, on the reasonable-sounding
        grounds that `callKitNativeProvider.ts` already calls
        `VoipPushNotification.registerVoipToken()` and reaches the same place.

        It does, when JS is running. The case this feature exists for is a
        *terminated* app: iOS relaunches PulseSoc to deliver the VoIP push, and it
        only does that if a `PKPushRegistry` whose `desiredPushTypes` contains
        `.voIP` already exists. At that moment React Native has not started, so the
        JS registration has not run and cannot run in time.

        Removing this line leaves calls working perfectly whenever the app happens
        to be warm, which is every case anyone tests by hand.
        """
        body = _function_body(_source(), "didFinishLaunchingWithOptions")
        self.assertTrue(body, "Could not locate didFinishLaunchingWithOptions.")
        self.assertIn(
            "RNVoipPushNotificationManager.voipRegistration()",
            body,
            "The VoIP registry is no longer created at launch. A terminated app "
            "will not be woken for an incoming call.",
        )

    def test_the_app_delegate_still_conforms_to_the_push_registry_delegate(self):
        """MUTATION: run `expo prebuild --clean` and commit the stock AppDelegate.

        The generated template has no PushKit in it. The app builds, launches,
        renders and passes every JS test; it just never rings. There is no error
        anywhere, because nothing asked for one.
        """
        source = _source()
        self.assertIn("import PushKit", source, "PushKit import is gone.")
        self.assertIn("import CallKit", source, "CallKit import is gone.")
        self.assertRegex(
            source,
            r"extension AppDelegate:\s*PKPushRegistryDelegate",
            "AppDelegate no longer conforms to PKPushRegistryDelegate. This is what "
            "a regenerated project looks like -- restore the file rather than "
            "updating this test.",
        )


class IncomingPushOrderingTest(unittest.TestCase):
    """The iOS 13 rule, expressed as assertions.

    Accepting a VoIP push obliges the app to report a call to CallKit before the
    handler returns. Everything in this class is about that obligation being met
    on *every* path, including the ones that look like they do not need it.
    """

    def setUp(self):
        self.body = _function_body(_source(), "didReceiveIncomingPushWith")
        self.assertTrue(self.body, "Could not locate didReceiveIncomingPushWith.")

    def test_callkit_is_told_before_javascript_is(self):
        """MUTATION: move `didReceiveIncomingPush` above `reportNewIncomingCall`,
        or wrap the report in the JS handler's completion.

        Handing the payload to JS first is the intuitive order -- JS "owns" the
        call UI -- and it is the order that gets the process killed. When the app
        was launched by this push the bridge does not exist yet, so the JS hop
        either no-ops or costs time the handler does not have.
        """
        report = [m.start() for m in re.finditer(r"RNCallKeep\.reportNewIncomingCall", self.body)]
        to_js = [m.start() for m in re.finditer(r"RNVoipPushNotificationManager\.didReceiveIncomingPush", self.body)]

        self.assertTrue(report, "No CallKit report in the incoming-push handler at all.")
        self.assertTrue(to_js, "The payload is never handed to JS; the call will ring and do nothing.")
        self.assertEqual(
            len(report),
            len(to_js),
            "The number of CallKit reports and JS hand-offs disagree, so at least "
            "one branch does one without the other.",
        )
        for index, js_at in enumerate(to_js):
            self.assertLess(
                report[index],
                js_at,
                "A branch hands the payload to JS before reporting to CallKit. iOS "
                "terminates the app for this, and after a few offences stops "
                "delivering VoIP pushes entirely.",
            )

    def test_the_cancel_branch_reports_a_call_before_ending_it(self):
        """MUTATION: delete the `reportNewIncomingCall` from the `cancel_call`
        branch, because reporting a call in order to immediately end it is
        obviously redundant.

        It is redundant, and it is required. A cancel arrives as a VoIP push like
        any other and owes CallKit the same report. When the call is already on
        screen -- the normal case -- CallKit rejects the duplicate UUID harmlessly
        and the `endCall` tears down the real one.
        """
        cancel_at = self.body.find('event == "cancel_call"')
        self.assertGreater(cancel_at, -1, "The cancel_call branch is gone.")
        cancel_branch = self.body[cancel_at:]
        end_at = cancel_branch.find("RNCallKeep.endCall")
        report_at = cancel_branch.find("RNCallKeep.reportNewIncomingCall")
        self.assertGreater(report_at, -1, "The cancel branch no longer reports to CallKit.")
        self.assertGreater(end_at, -1, "The cancel branch no longer ends the call.")
        self.assertLess(report_at, end_at, "The cancel branch ends a call it never reported.")

    def test_every_early_return_still_calls_completion(self):
        """MUTATION: add a guard that returns without `completion()` -- for example
        dropping a malformed payload, which is exactly what the existing UUID guard
        does and exactly why it calls completion first.

        Failing to call the completion handler is the same termination offence as
        failing to report. The handler must finish the push even when it refuses
        to act on it.
        """
        self.assertIn("return", self.body, "No returns found; the parse is wrong, not the code.")
        offenders = _returns_without_completion(self.body)
        self.assertEqual(
            offenders,
            [],
            "These returns leave the incoming-push handler from a block that never "
            "completes the push. Every path out of this method owes iOS a completed "
            f"push, including the ones that drop the payload: {offenders}",
        )

    def test_the_handler_stays_synchronous_and_payload_only(self):
        """MUTATION: add a `URLSession` call or an `await` to confirm the call is
        still ringing before showing the system UI.

        This is the single most reasonable-sounding change anyone will ever propose
        to this method, and it is the one that breaks it. Everything the system UI
        needs is already in the payload -- `_incoming_payload` in
        `services/pulsesoc_voip_push.py` is ten flat keys for this reason. The
        server is re-consulted only after the user answers.

        The scan covers the whole handler rather than only the text before the
        first report, because the first report is in the *cancel* branch: a
        prelude-only check reads none of the main branch and would have let an
        `await` sit directly above the report that matters. That is not a
        hypothetical -- it is what the first version of this test did, and the
        mutation run is how it was found.
        """
        for needle in FORBIDDEN_IN_PUSH_HANDLER:
            self.assertNotIn(
                needle,
                self.body,
                f"{needle!r} appears in the incoming-push handler. Nothing may delay "
                "the CallKit report -- not a fetch, not an await, not a bridge hop.",
            )


class EndedReasonMappingTest(unittest.TestCase):
    def test_answering_elsewhere_is_not_logged_as_a_missed_call(self):
        """MUTATION: collapse `endedReason` to always return `.unanswered`.

        Nothing breaks. Calls still ring, answer and end. The only symptom is that
        answering on your iPhone leaves a phantom "missed call" on your iPad --
        a report that reads as a notification bug, is investigated on the server,
        and lives in this Swift file.
        """
        source = _source()
        body = source[source.find("private func endedReason"):]
        self.assertTrue(body, "endedReason is gone.")
        for reason, expected_case in EXPECTED_ENDED_REASONS.items():
            self.assertRegex(
                body,
                rf'"{re.escape(reason)}"[^\n]*(?:\n[^\n]*)?CXCallEndedReason\.{expected_case}\b',
                f"Server reason {reason!r} no longer maps to CXCallEndedReason.{expected_case}.",
            )
        self.assertIn(
            "CXCallEndedReason.unanswered",
            body,
            "The default case is gone. A genuine caller hang-up should be logged as "
            "missed, and only that case should be.",
        )


class BridgeSurfaceTest(unittest.TestCase):
    def test_the_set_of_bridge_calls_is_the_one_we_reviewed(self):
        """MUTATION: add any new RNCallKeep or RNVoipPushNotificationManager call.

        Not because a new call is wrong, but because this is the one file where an
        addition needs a person to think about ordering before it lands. An
        unreviewed `RNCallKeep.endCall` in the wrong branch ends live calls.
        """
        # The trailing `(` is load-bearing: without it this also matches the prose
        # reference to `RNVoipPushNotificationManager.m` in the launch comment, and
        # the gate fails on a sentence rather than on a call.
        found = set(
            re.findall(
                r"\b((?:RNCallKeep|RNVoipPushNotificationManager)\.[A-Za-z][A-Za-z0-9]*)\s*\(",
                _source(),
            )
        )
        self.assertEqual(
            found,
            EXPECTED_BRIDGE_CALLS,
            "The native bridge surface changed. If the change is deliberate, update "
            "EXPECTED_BRIDGE_CALLS in the same commit -- after checking where in "
            "the incoming-push handler the new call sits.",
        )

    def test_the_voip_background_mode_is_still_declared(self):
        """MUTATION: drop `voip` from UIBackgroundModes while tidying the plist.

        Without it `PKPushRegistry` cannot register for `.voIP` at all, so the
        device never gets a VoIP token, the server never has one to push to, and
        every incoming call silently degrades to an ordinary alert notification --
        which looks like a push problem, not a plist problem.

        `fetch` was deliberately removed on 2026-09-19 because nothing implemented
        it; this asserts the two that are load-bearing rather than the whole list,
        so adding a mode a future capability needs does not fail here spuriously.
        """
        plist = plistlib.loads(INFO_PLIST.read_bytes())
        modes = plist.get("UIBackgroundModes") or []
        self.assertIn("voip", modes, f"UIBackgroundModes lost 'voip': {modes}")
        self.assertIn(
            "remote-notification", modes, f"UIBackgroundModes lost 'remote-notification': {modes}"
        )


class VacuityTest(unittest.TestCase):
    def test_the_parse_is_not_vacuous(self):
        """MUTATION: rename `didReceiveIncomingPushWith`, or point APP_DELEGATE at a
        path that does not exist.

        Five of the checks above read a slice of this file located by string
        search. If that search silently returns "" they all inspect an empty
        string, and an empty string contains no forbidden symbols, no misordered
        calls and no bad returns -- a reader failure that makes the gate greener.
        This asserts the reader did real work, so the rot fails here instead.
        """
        self.assertTrue(APP_DELEGATE.exists(), f"Missing: {APP_DELEGATE}")
        source = _source()
        self.assertGreater(len(source), 3_000, "AppDelegate.swift is implausibly small.")

        launch = _function_body(source, "didFinishLaunchingWithOptions")
        push = _function_body(source, "didReceiveIncomingPushWith")
        self.assertGreater(len(launch), 200, "didFinishLaunchingWithOptions body extraction is empty.")
        self.assertGreater(len(push), 400, "didReceiveIncomingPushWith body extraction is empty.")
        self.assertIn("RNCallKeep", push, "The push body was located but contains no CallKit call.")


if __name__ == "__main__":
    unittest.main()
