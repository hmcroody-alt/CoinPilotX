/**
 * The cold-launch backlog: a killed app's answer depends entirely on reading it.
 *
 * `react-native-voip-push-notification` buffers events emitted before JS has a listener
 * and replays them as one `didLoadWithEvents`. On the flagship path — locked phone, app
 * killed, iOS launching PulseSoc *because* of the push — everything is in that buffer by
 * construction. The reader handled the token event and ignored the push event, and the
 * push event is the only one carrying `call_id` beside `uuid`. Without that pair,
 * `callKitBridge.onAnswer` cannot resolve the UUID CallKit reports back to a call id, so
 * it returns early: the user answers and `/accept` is never even sent.
 *
 * That produces precisely the symptom of the accept-response bug fixed alongside this —
 * answer, silence, CallKit teardown, caller still reading "connected" — from a completely
 * different cause. Both had to be fixed; neither covers the other. These tests pin the
 * half that was silently dropped, and pin that a live push and a replayed one are narrowed
 * by the same code so they cannot drift.
 */
import {
  VOIP_NOTIFICATION_EVENT,
  VOIP_REGISTERED_EVENT,
  pushedCallFromPayload,
  readVoipBacklog
} from "../voipPushBacklog";

/** The payload shape the backend sends, and the pod forwards as `dictionaryPayload`. */
const PUSH_PAYLOAD = {
  call_id: "call_VFLHqc4Sj1XGUw",
  uuid: "9f1c7b2e-4d3a-4a51-9a0f-2c7d8e6b1a34",
  caller_name: "Alex",
  call_type: "audio"
};

describe("reading the PushKit cold-launch backlog", () => {
  it("recovers the call mapping from a replayed push, which is what was dropped", () => {
    // Exactly what a killed app sees: iOS launched the process for this push, so the
    // push event could not have had a listener and must arrive through the backlog.
    const { calls, tokens } = readVoipBacklog([
      { name: VOIP_REGISTERED_EVENT, data: "a1b2c3d4e5f6" },
      { name: VOIP_NOTIFICATION_EVENT, data: PUSH_PAYLOAD }
    ]);

    // MUTATION: delete the VOIP_NOTIFICATION_EVENT branch in readVoipBacklog and this
    // is where it fails — which is the exact state the code shipped in.
    expect(calls).toEqual([{ callId: "call_VFLHqc4Sj1XGUw", uuid: PUSH_PAYLOAD.uuid }]);
    expect(tokens).toEqual(["a1b2c3d4e5f6"]);
  });

  it("still reads the token, so fixing the call half cannot cost the registration half", () => {
    // The token path was the one that already worked. A regression here would stop the
    // device re-registering on relaunch, which ends with calls downgraded to a banner.
    const { tokens, calls } = readVoipBacklog([{ name: VOIP_REGISTERED_EVENT, data: "deadbeef" }]);
    expect(tokens).toEqual(["deadbeef"]);
    expect(calls).toEqual([]);
  });

  it("narrows a live push and a replayed push identically", () => {
    // The provider uses `pushedCallFromPayload` on the live `notification` event and
    // `readVoipBacklog` on the replay. The pod sends the same `dictionaryPayload` to
    // both, so the two must agree — otherwise a call answered from a cold launch and the
    // same call answered while running would resolve to different ids, or to none.
    const live = pushedCallFromPayload(PUSH_PAYLOAD);
    const [replayed] = readVoipBacklog([{ name: VOIP_NOTIFICATION_EVENT, data: PUSH_PAYLOAD }]).calls;
    expect(live).toEqual(replayed);
    expect(live).not.toBeNull();
  });

  it("refuses a half pair rather than recording a useless mapping", () => {
    // `rememberCallKitCall` evicts any existing mapping for the call it is given. A
    // payload with only one key cannot be acted on — a UUID addresses nothing on the
    // server, a call id cannot be matched to the CallKit call on screen — so recording
    // one would be strictly worse than recording nothing.
    expect(pushedCallFromPayload({ uuid: PUSH_PAYLOAD.uuid })).toBeNull();
    expect(pushedCallFromPayload({ call_id: PUSH_PAYLOAD.call_id })).toBeNull();
    expect(pushedCallFromPayload({ call_id: "", uuid: "" })).toBeNull();
  });

  it("is total: a malformed or absent backlog cannot throw during launch", () => {
    // This runs inside a native event callback while the app is starting, where a throw
    // is least visible and least recoverable. A launch where nobody called replays
    // nothing at all, so the empty and undefined cases are the common ones.
    expect(readVoipBacklog(undefined)).toEqual({ tokens: [], calls: [] });
    expect(readVoipBacklog(null)).toEqual({ tokens: [], calls: [] });
    expect(readVoipBacklog("not-an-array")).toEqual({ tokens: [], calls: [] });
    expect(readVoipBacklog([])).toEqual({ tokens: [], calls: [] });
    expect(readVoipBacklog([null, undefined, 7, { name: "SomethingNewThePodAdded" }])).toEqual({
      tokens: [],
      calls: []
    });
    expect(pushedCallFromPayload(undefined)).toBeNull();
    expect(pushedCallFromPayload("string")).toBeNull();
  });

  it("keeps every call in a multi-event backlog, in order", () => {
    // Two pushes can be buffered: a call that rang and was missed, then a second call.
    // Dropping either would strand a CallKit call that can never be ended by id.
    const second = { call_id: "call_r6c1WwLfAHQiHQ", uuid: "11111111-2222-3333-4444-555555555555" };
    const { calls } = readVoipBacklog([
      { name: VOIP_NOTIFICATION_EVENT, data: PUSH_PAYLOAD },
      { name: VOIP_REGISTERED_EVENT, data: "token" },
      { name: VOIP_NOTIFICATION_EVENT, data: second }
    ]);
    expect(calls).toEqual([
      { callId: PUSH_PAYLOAD.call_id, uuid: PUSH_PAYLOAD.uuid },
      { callId: second.call_id, uuid: second.uuid }
    ]);
  });
});
