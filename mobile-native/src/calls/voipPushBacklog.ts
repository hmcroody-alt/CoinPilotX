/**
 * Reading the PushKit event backlog that a cold launch always produces.
 *
 * `react-native-voip-push-notification` buffers any event it emits while JS has no
 * listener attached, and replays the whole buffer as a single `didLoadWithEvents` the
 * moment the first listener subscribes. On the path this feature exists for — phone
 * locked, app killed, iOS launching PulseSoc *because* of the VoIP push — every push
 * event is necessarily in that buffer, because the push is what started the process.
 *
 * Two different events land there and only one of them was being read. The token
 * (`RNVoipPushRemoteNotificationsRegisteredEvent`) was handled; the push itself
 * (`RNVoipPushRemoteNotificationReceivedEvent`) was not. That payload is the only thing
 * that carries the call id alongside the CallKit UUID, and recording that pair is what
 * lets `callKitBridge` turn an answer — which CallKit reports by UUID and nothing else —
 * back into a call id. With the pair missing, `onAnswer` looks the UUID up, finds
 * nothing, and returns: the user swipes to answer and the app does not even send
 * `/accept`. The symptom is indistinguishable from the accept-response bug this module
 * was written alongside — answer, then silence, then CallKit tears the call down while
 * the caller's UI reads "connected" — which is exactly why it is worth naming the two
 * separately rather than assuming one fix covered both.
 *
 * The live `notification` listener and this replay carry the *same* payload: the pod
 * sends `payload.dictionaryPayload` in both cases (`sendEventWithNameWrapper` either
 * emits it directly or stores it under `data`). So both paths narrow it through
 * `pushedCallFromPayload` here, and neither can drift from the other.
 *
 * This is a separate module, rather than inline in `callKitNativeProvider`, because that
 * provider imports `react-native-callkeep` and `react-native-voip-push-notification` at
 * module scope and cannot be loaded under jest at all. Keeping the parsing pure and
 * outside it is what makes the cold-launch path testable without the pods present.
 */

/** Emitted when PushKit hands over a device token. */
export const VOIP_REGISTERED_EVENT = "RNVoipPushRemoteNotificationsRegisteredEvent";
/** Emitted for the incoming push itself — the only event carrying the call id. */
export const VOIP_NOTIFICATION_EVENT = "RNVoipPushRemoteNotificationReceivedEvent";

export type VoipBacklogEvent = { name?: string; data?: unknown };

/** A call CallKit is already showing natively, named the way the rest of the app names calls. */
export type PushedCall = { callId: string; uuid: string };

/**
 * Narrow a VoIP push payload to the call it announces, or `null` if it announces none.
 *
 * Both keys are required. A payload carrying only one of them cannot be acted on: a UUID
 * with no call id addresses nothing on the server, and a call id with no UUID cannot be
 * matched to the CallKit call already on screen. Recording a half pair would be worse
 * than recording nothing, because `rememberCallKitCall` would evict a correct mapping to
 * store a useless one.
 */
export function pushedCallFromPayload(payload: unknown): PushedCall | null {
  if (!payload || typeof payload !== "object") return null;
  const record = payload as Record<string, unknown>;
  const callId = String(record.call_id ?? "");
  const uuid = String(record.uuid ?? "");
  if (!callId || !uuid) return null;
  return { callId, uuid };
}

/**
 * Split a replayed backlog into the tokens and the pushed calls it contains.
 *
 * Deliberately total: the pod types the payload loosely, a malformed or absent backlog is
 * normal (a launch where nobody called replays nothing), and a throw here would happen
 * inside a native event callback during app launch, where it is least recoverable and
 * least visible. Unknown event names are ignored rather than treated as errors — the pod
 * is free to add more, and none of them can be load-bearing for a call.
 */
export function readVoipBacklog(events: unknown): { tokens: string[]; calls: PushedCall[] } {
  const tokens: string[] = [];
  const calls: PushedCall[] = [];
  if (!Array.isArray(events)) return { tokens, calls };

  (events as VoipBacklogEvent[]).forEach((event) => {
    if (!event || typeof event !== "object") return;
    if (event.name === VOIP_REGISTERED_EVENT) {
      const token = event.data ? String(event.data) : "";
      if (token) tokens.push(token);
      return;
    }
    if (event.name === VOIP_NOTIFICATION_EVENT) {
      const call = pushedCallFromPayload(event.data);
      if (call) calls.push(call);
    }
  });

  return { tokens, calls };
}
