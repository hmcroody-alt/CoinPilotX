/**
 * One user intent must survive every observation path as ONE message.
 *
 * A single send can be seen five times -- the optimistic bubble, the REST
 * response, a realtime echo, a reconnect replay and a push event -- arriving in
 * any order, some of them more than once, some carrying only half the message's
 * identity. Every test here drives one of those permutations and asserts the same
 * thing at the end: the conversation holds exactly one message, with one server
 * id and one client id.
 *
 * The outcome assertions matter as much as the counts. "There is one message" can
 * be true by luck; "the second observation was recognised as an ACKNOWLEDGE" is
 * the property that keeps being true when a sixth observation path is added.
 */

import {
  createMessengerState,
  reconcileMessage,
  reconcileMessages,
  stateForList,
  ReconcileOutcome
} from "../messengerReconciler";
import { createLocalMessage, MessengerMessage } from "../messenger";
import { mintClientMessageId } from "../messengerOrdering";
import { readFileSync } from "fs";
import { join } from "path";

const CONVERSATION = 42;

function serverRow(id: number, extra: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id,
    message_id: id,
    conversation_id: CONVERSATION,
    body: `body-${id}`,
    message_type: "text",
    delivery_status: "sent",
    created_at: new Date(1_700_000_000_000 + id * 1000).toISOString(),
    is_mine: true,
    ...extra
  } as MessengerMessage;
}

/** The REST response / realtime echo for a locally-composed message. */
function ackOf(local: MessengerMessage, id: number, extra: Partial<MessengerMessage> = {}): MessengerMessage {
  return serverRow(id, {
    body: local.body,
    message_type: local.message_type,
    client_message_id: local.client_message_id,
    ...extra
  });
}

function drive(events: MessengerMessage[][]): { messages: MessengerMessage[]; outcomes: ReconcileOutcome[] } {
  let state = createMessengerState([]);
  const outcomes: ReconcileOutcome[] = [];
  events.forEach((batch) => {
    const result = reconcileMessages(state, batch);
    state = result.state;
    outcomes.push(...result.outcomes);
  });
  return { messages: [...state.messages], outcomes };
}

describe("2.13 — every observation permutation resolves to exactly one message", () => {
  it("optimistic bubble then HTTP ack", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    const { messages, outcomes } = drive([[local], [ackOf(local, 900)]]);
    expect(messages).toHaveLength(1);
    expect(outcomes).toEqual(["INSERT", "ACKNOWLEDGE"]);
    expect(messages[0].id).toBe(900);
    expect(messages[0].client_message_id).toBe(local.client_message_id);
  });

  it("optimistic bubble, realtime echo, then the HTTP ack arrives late", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    const { messages, outcomes } = drive([[local], [ackOf(local, 901)], [ackOf(local, 901)]]);
    expect(messages).toHaveLength(1);
    expect(outcomes).toEqual(["INSERT", "ACKNOWLEDGE", "IGNORE"]);
  });

  it("optimistic bubble, HTTP ack, then the realtime echo arrives", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    const echo = ackOf(local, 902, { delivery_status: "delivered", delivered_at: "2026-01-01T00:00:00Z" });
    const { messages, outcomes } = drive([[local], [ackOf(local, 902)], [echo]]);
    expect(messages).toHaveLength(1);
    expect(outcomes).toEqual(["INSERT", "ACKNOWLEDGE", "UPDATE"]);
    expect(messages[0].delivery_status).toBe("delivered");
  });

  it("lost ACK: the send succeeded, the response did not, and the retry reuses the identity", () => {
    // The server wrote message 903 and the HTTP response evaporated, so the
    // client marked its bubble failed. The retry carries the SAME client id, the
    // server recognises it and returns the ORIGINAL row. The failed bubble must
    // become that message -- not sit beside it.
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    const failed: MessengerMessage = { ...local, local_status: "failed", local_error: "Network request failed" };
    const idempotentAck = ackOf(local, 903);

    let state = createMessengerState([]);
    state = reconcileMessage(state, local).state;
    state = reconcileMessage(state, failed).state;
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].local_status).toBe("failed");

    const result = reconcileMessage(state, idempotentAck);
    expect(result.outcome).toBe("ACKNOWLEDGE");
    expect(result.state.messages).toHaveLength(1);
    expect(result.state.messages[0].id).toBe(903);
    expect(result.state.messages[0].local_status).toBeUndefined();
    expect(result.state.messages[0].local_error).toBeUndefined();
  });

  it("a realtime frame delivered twice inserts once", () => {
    const row = serverRow(904, { client_message_id: "native-echo" });
    const { messages, outcomes } = drive([[row], [{ ...row }]]);
    expect(messages).toHaveLength(1);
    expect(outcomes).toEqual(["INSERT", "IGNORE"]);
  });

  it("a reconnect replays the whole page without duplicating any of it", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    const page = [serverRow(905), serverRow(906), ackOf(local, 907)];
    let state = createMessengerState([]);
    state = reconcileMessage(state, local).state;
    state = reconcileMessages(state, page).state;
    const replay = reconcileMessages(state, page);
    expect(replay.state.messages).toHaveLength(3);
    expect(replay.outcomes).toEqual(["IGNORE", "IGNORE", "IGNORE"]);
    expect(replay.state).toBe(state); // nothing changed, so nothing re-renders
  });

  it("two sends in the same millisecond stay two messages", () => {
    const frozen = jest.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
    let first: MessengerMessage;
    let second: MessengerMessage;
    try {
      first = createLocalMessage(CONVERSATION, "first", "text");
      second = createLocalMessage(CONVERSATION, "second", "text");
    } finally {
      frozen.mockRestore();
    }
    const { messages } = drive([[first!, second!], [ackOf(first!, 908), ackOf(second!, 909)]]);
    expect(messages).toHaveLength(2);
    expect(messages.map((m) => m.body)).toEqual(["first", "second"]);
    expect(messages.map((m) => m.id)).toEqual([908, 909]);
  });

  it("a media send that is retried after the upload succeeds is still one message", () => {
    // Capture, upload, send. The send failed visibly but had already reached the
    // server; the retry reuses the id the camera screen held across the attempt.
    const clientId = mintClientMessageId("camera");
    const local: MessengerMessage = {
      ...createLocalMessage(CONVERSATION, "", "image", clientId),
      media_url: "file:///local/capture.jpg",
      local_status: "failed"
    };
    const ack = serverRow(910, {
      client_message_id: clientId,
      message_type: "image",
      media_url: "https://cdn.pulsesoc.com/capture.jpg",
      attachment_id: 55
    });
    const { messages, outcomes } = drive([[local], [ack]]);
    expect(messages).toHaveLength(1);
    expect(outcomes).toEqual(["INSERT", "ACKNOWLEDGE"]);
    expect(messages[0].media_url).toBe("https://cdn.pulsesoc.com/capture.jpg");
    expect(messages[0].attachment_id).toBe(55);
    expect(messages[0].local_status).toBeUndefined();
  });

  it("an offline queue replay of the original payload does not un-ack the message", () => {
    // The queue holds the payload, not the server's answer. Draining it after the
    // message was already accepted must not push the row back into the pending
    // bucket, which would move it on screen and re-offer "retry" for a sent message.
    const local = createLocalMessage(CONVERSATION, "queued", "text");
    let state = createMessengerState([]);
    state = reconcileMessage(state, local).state;
    state = reconcileMessage(state, ackOf(local, 911)).state;

    const replayedPayload = createLocalMessage(CONVERSATION, "queued", "text", local.client_message_id);
    const result = reconcileMessage(state, replayedPayload);
    expect(result.state.messages).toHaveLength(1);
    expect(result.state.messages[0].id).toBe(911);
    expect(result.state.messages[0].local_status).toBeUndefined();
  });
});

describe("2.2 — the client id and the server id are aliases of one message", () => {
  it("an event carrying only the server id lands on the row it belongs to", () => {
    // A push payload knows `message_id` but has never seen the client id. Keyed
    // naively this became a second bubble; it must find the acked row instead.
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    let state = createMessengerState([]);
    state = reconcileMessage(state, local).state;
    state = reconcileMessage(state, ackOf(local, 912)).state;

    const pushOnly = serverRow(912, { delivery_status: "delivered" });
    const result = reconcileMessage(state, pushOnly);
    expect(result.outcome).toBe("UPDATE");
    expect(result.state.messages).toHaveLength(1);
    expect(result.state.messages[0].client_message_id).toBe(local.client_message_id);
    expect(result.state.messages[0].delivery_status).toBe("delivered");
  });

  it("collapses two rows that turn out to be one message once the alias appears", () => {
    // Worst case: a server-id-only observation arrived BEFORE the ack, so the
    // thread briefly holds two rows. The ack carries both identities and joins
    // them, and the row count must come back down rather than stay wrong.
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    let state = createMessengerState([]);
    state = reconcileMessage(state, local).state;
    state = reconcileMessage(state, serverRow(913)).state;
    expect(state.messages).toHaveLength(2);

    const result = reconcileMessage(state, ackOf(local, 913));
    expect(result.outcome).toBe("REKEY");
    expect(result.state.messages).toHaveLength(1);
    expect(result.state.messages[0].id).toBe(913);
    expect(result.state.messages[0].client_message_id).toBe(local.client_message_id);
  });

  it("keeps both indexes pointing at the surviving row", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    let state = createMessengerState([]);
    state = reconcileMessage(state, local).state;
    state = reconcileMessage(state, ackOf(local, 914)).state;
    const row = state.messages[0];
    expect(state.byClientId.get(local.client_message_id!)).toBe(row);
    expect(state.byServerId.get(914)).toBe(row);
    expect(state.byServerId.has(local.id)).toBe(false); // the negative local id is retired
  });
});

describe("2.7 — identity and ordering stay separate", () => {
  it("a pending bubble sits at the bottom and does not jump when it is acked", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    let state = createMessengerState([serverRow(100), serverRow(101)]);
    state = reconcileMessage(state, local).state;
    expect(state.messages[state.messages.length - 1].id).toBe(local.id);

    state = reconcileMessage(state, ackOf(local, 102)).state;
    expect(state.messages.map((m) => m.id)).toEqual([100, 101, 102]);
  });

  it("orders a page that arrives out of order", () => {
    const { messages } = drive([[serverRow(3), serverRow(1), serverRow(2)]]);
    expect(messages.map((m) => m.id)).toEqual([1, 2, 3]);
  });
});

describe("2.8 — delivery state only moves forward", () => {
  it("a replay reporting 'sent' does not walk a read message backwards", () => {
    const row = serverRow(920, { client_message_id: "native-x", delivery_status: "read", seen_at: "2026-01-01T00:00:00Z" });
    let state = createMessengerState([row]);
    state = reconcileMessage(state, serverRow(920, { client_message_id: "native-x", delivery_status: "sent" })).state;
    expect(state.messages[0].delivery_status).toBe("read");
    expect(state.messages[0].seen_at).toBe("2026-01-01T00:00:00Z");
  });

  it("an accepted message never keeps a pre-ack 'sending' marker", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    expect(local.delivery_status).toBe("sending");
    const state = reconcileMessage(createMessengerState([local]), ackOf(local, 921, { delivery_status: undefined })).state;
    expect(state.messages[0].delivery_status).toBe("sent");
    expect(state.messages[0].local_status).toBeUndefined();
  });

  it("a failure stays attached to the same logical message rather than forking it", () => {
    const local = createLocalMessage(CONVERSATION, "hello", "text");
    let state = createMessengerState([local]);
    state = reconcileMessage(state, { ...local, local_status: "failed", local_error: "offline" }).state;
    expect(state.messages).toHaveLength(1);
    expect(state.messages[0].local_status).toBe("failed");
    expect(state.messages[0].client_message_id).toBe(local.client_message_id);
  });
});

describe("2.14 — identity lookups are indexed, not scanned", () => {
  it("reuses a cached index instead of rebuilding it for the list it just produced", () => {
    const rows = Array.from({ length: 500 }, (_, i) => serverRow(i + 1, { client_message_id: `native-${i}` }));
    const first = stateForList(rows);
    expect(stateForList(first.messages)).toBe(first);
    expect(stateForList(rows)).toBe(first);
  });

  it("resolves a known message without consulting the message list", () => {
    const rows = Array.from({ length: 200 }, (_, i) => serverRow(i + 1, { client_message_id: `native-${i}` }));
    const state = createMessengerState(rows);
    expect(state.byClientId.size).toBe(200);
    expect(state.byServerId.size).toBe(200);
    expect(state.byServerId.get(150)?.client_message_id).toBe("native-149");
  });
});

/**
 * 2.1 says the reconciler is the ONLY component that decides whether two
 * observations are one message. That is a structural claim about the codebase,
 * not about a data structure, so it is asserted against the source: a behavioural
 * test would keep passing the day someone reintroduces a private `filter(id !==
 * localId)` next to the reconciler call, because the two would agree until the
 * one case they disagree on -- a retry, whose optimistic row carries a fresh
 * negative id -- and that case is exactly the duplicate this mission exists to
 * prevent.
 */
describe("2.1 — one reconciliation owner", () => {
  const chat = readFileSync(join(__dirname, "..", "..", "screens", "ChatScreen.tsx"), "utf-8");

  it("ChatScreen does not delete a local row to make room for its acknowledgement", () => {
    expect(chat).not.toContain("current.filter((message) => message.id !== localId)");
    expect(chat).toContain("acknowledgeLocalMessage");
  });

  it("ChatScreen stamps the client id onto the server row so both halves travel together", () => {
    const ack = chat.slice(chat.indexOf("const acknowledgeLocalMessage = useCallback("));
    expect(ack.slice(0, ack.indexOf("}, [mergeMessages]);"))).toContain(
      "next.client_message_id || local.client_message_id"
    );
  });

  it("a retry transforms the failed bubble instead of removing it", () => {
    const retry = chat.slice(chat.indexOf("const retryMessage = useCallback("));
    expect(retry.slice(0, retry.indexOf("}, [sendPayload]);"))).not.toContain("current.filter(");
  });

  it("the queued and failed transitions go through the reconciler, not an id scan", () => {
    expect(chat).not.toContain("message.id === local.id");
  });

  it("the offline queue drain preserves the identity the bubble on screen was keyed by", () => {
    const transport = readFileSync(join(__dirname, "..", "messenger.ts"), "utf-8");
    const drain = transport.slice(transport.indexOf("export async function drainMessengerQueue("));
    expect(drain.slice(0, drain.indexOf("return sent;"))).toContain(
      "result.data.client_message_id || item.payload.client_message_id"
    );
  });
});
