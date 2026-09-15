import { Platform } from "react-native";
import RNCallKeep, { AudioSessionMode } from "react-native-callkeep";
import VoipPushNotification from "react-native-voip-push-notification";
import { NativeCallKitProvider, rememberCallKitCall } from "./callKitBridge";

/**
 * The real `NativeCallKitProvider`, bound to react-native-callkeep + PushKit.
 *
 * `callKitBridge.ts` holds every decision this feature makes — which call id an answer maps
 * to, when to accept versus decline, when to tell the backend. This file holds none of them.
 * It is the adapter between that logic and two Objective-C pods, and the split is what lets
 * the bridge be unit-tested against a fake provider on a machine with no pods and no
 * simulator.
 *
 * ## Audio ownership
 *
 * This provider never configures `AVAudioSession`. PulseSoc has exactly one governed audio
 * coordinator (`src/core/realtimeAudioEngine.ts`) and a second writer is the documented way
 * this app goes silent mid-call, so the rule here is that CallKit reports call *state* and
 * the engine owns the *session*.
 *
 * react-native-callkeep does not fully honour that on its own: `RNCallKeep.m` calls its
 * internal `configureAudioSession` from `performAnswerCallAction` and again from
 * `didActivateAudioSession`, and that helper writes category, mode, sample rate and buffer
 * duration on the shared session. It cannot be prevented from JS. What it *can* be told is
 * which mode to write, so `setup()` below pins it to `voiceChat` — the mode a PulseSoc call
 * wants anyway. That turns a write which would otherwise contradict the engine
 * (`AVAudioSessionModeDefault`: no echo cancellation, wrong routing for a call) into one
 * that agrees with it.
 *
 * This is a mitigation, not a proof. The ordering that still has to be confirmed on a
 * physical device is CallKit's asynchronous `didActivateAudioSession` landing *after* Agora
 * has joined and configured the session. See the regression-risk section of
 * `reports/realtime_audio_change_declaration.md`.
 */

type Unsubscribe = () => void;

/** Whether both native modules are actually linked into this binary. */
export function isNativeCallKitAvailable() {
  return Platform.OS === "ios" && Boolean(RNCallKeep) && Boolean(VoipPushNotification);
}

export function createNativeCallKitProvider(): NativeCallKitProvider | null {
  if (!isNativeCallKitAvailable()) return null;

  return {
    async setup() {
      await RNCallKeep.setup({
        ios: {
          appName: "PulseSoc",
          supportsVideo: true,
          maximumCallGroups: "1",
          maximumCallsPerCallGroup: "1",
          // The handle PulseSoc reports is the call's UUID, never a username or a user id,
          // because CallKit hands the handle to the system call log. Keeping PulseSoc calls
          // out of Recents entirely means there is no place for that UUID to be retained,
          // and no "call back" affordance pointing at a string that dials nothing.
          includesCallsInRecents: false,
          // See the audio-ownership note above: this does not stop callkeep writing to the
          // shared session, it makes the write agree with the realtime audio engine.
          audioSession: { mode: AudioSessionMode.voiceChat }
        },
        // CallKit is iOS-only. Android keeps the existing in-app ringer, so these values are
        // required by the type but never reached.
        android: {
          alertTitle: "",
          alertDescription: "",
          cancelButton: "",
          okButton: "",
          additionalPermissions: []
        }
      });
    },

    displayIncomingCall(uuid, incoming) {
      RNCallKeep.displayIncomingCall(
        uuid,
        incoming.handle,
        incoming.displayName,
        "generic",
        incoming.hasVideo
      );
    },

    setCallConnected(uuid) {
      RNCallKeep.setCurrentCallActive(uuid);
    },

    endCall(uuid) {
      RNCallKeep.endCall(uuid);
    },

    registerVoipToken() {
      // AppDelegate already created the PKPushRegistry at launch, which is what makes a
      // terminated app reachable. This call is how JS asks for the resulting token: when the
      // registry is already registered, `voipRegistration` replays the last token rather
      // than registering twice.
      VoipPushNotification.registerVoipToken();
    },

    onAnswer(cb): Unsubscribe {
      const handler = ({ callUUID }: { callUUID: string }) => cb(normalizeUuid(callUUID));
      RNCallKeep.addEventListener("answerCall", handler);

      // The killed-app case. When iOS launches PulseSoc to deliver a VoIP push and the user
      // answers from the lock screen, the answer happens before the JS bundle has finished
      // evaluating, so there is no listener to receive it. CallKeep queues those events and
      // replays them through `didLoadWithEvents` the moment JS subscribes. Without this
      // branch the lock-screen answer — the single most important path in this feature —
      // silently does nothing and the user gets a connected CallKit UI with no call behind it.
      const replay = (events: Array<{ name: string; data: { callUUID?: string } }>) => {
        (events || []).forEach((event) => {
          if (event?.name === "RNCallKeepPerformAnswerCallAction" && event.data?.callUUID) {
            cb(normalizeUuid(event.data.callUUID));
          }
        });
      };
      RNCallKeep.addEventListener("didLoadWithEvents", replay as never);

      return () => {
        RNCallKeep.removeEventListener("answerCall");
        RNCallKeep.removeEventListener("didLoadWithEvents");
      };
    },

    onEnd(cb): Unsubscribe {
      const handler = ({ callUUID }: { callUUID: string }) => cb(normalizeUuid(callUUID));
      RNCallKeep.addEventListener("endCall", handler);
      return () => RNCallKeep.removeEventListener("endCall");
    },

    onVoipToken(cb): Unsubscribe {
      VoipPushNotification.addEventListener("register", (token: string) => cb(token));

      // The pod buffers any event emitted while JS had no listener and replays the whole
      // backlog as a single `didLoadWithEvents` at the moment the first listener attaches.
      // PushKit hands iOS the cached VoIP token during launch — before React Native is
      // running — so on every relaunch the real token is already sitting in that backlog
      // and arrives here, wrapped, rather than as a plain `register`. Reading only
      // `register` means the token is visible exactly once per install and never again.
      VoipPushNotification.addEventListener("didLoadWithEvents", ((
        events: Array<{ name: string; data: unknown }>
      ) => {
        (events || []).forEach((event) => {
          if (event?.name === "RNVoipPushRemoteNotificationsRegisteredEvent" && event.data) {
            cb(String(event.data));
          }
        });
      }) as never);

      // Every VoIP push also arrives here, after AppDelegate has already reported it to
      // CallKit. Nothing needs to be *displayed* from JS — that work is done — but the
      // call id ↔ UUID mapping only exists natively at this point, and the rest of the app
      // addresses calls by id. Recording it here is what lets a later `markCallKitConnected`
      // or `endCallKitCall` find the CallKit call that the push created.
      // The pod types this callback as `(args: object) => void`, so the payload arrives
      // without an index signature and has to be narrowed before its keys can be read.
      VoipPushNotification.addEventListener("notification", (args: object) => {
        const payload = (args || {}) as Record<string, unknown>;
        const callId = String(payload.call_id || "");
        const uuid = String(payload.uuid || "");
        if (callId && uuid) rememberCallKitCall(callId, normalizeUuid(uuid));
      });

      return () => {
        VoipPushNotification.removeEventListener("register");
        VoipPushNotification.removeEventListener("didLoadWithEvents");
        VoipPushNotification.removeEventListener("notification");
      };
    }
  };
}

/**
 * CallKit lowercases the UUIDs it hands back; the server issues them lowercase already.
 *
 * Normalising both sides means the bridge's `Map` lookups match. A case mismatch here does
 * not throw — it just misses, and a missed lookup is an answer that resolves to no call id.
 */
function normalizeUuid(uuid: string) {
  return String(uuid || "").toLowerCase();
}
