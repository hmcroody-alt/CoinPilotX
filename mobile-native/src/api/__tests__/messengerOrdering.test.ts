jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import { compareMessengerMessages, mergeConversationMessages } from "../messengerOrdering";
import { createLocalMessage, MessengerMessage } from "../messenger";

function serverMessage(id: number, extra: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id,
    message_id: id,
    conversation_id: 1,
    body: `server-${id}`,
    message_type: "text",
    delivery_status: "sent",
    created_at: new Date(1_700_000_000_000 + id * 1000).toISOString(),
    is_mine: false,
    ...extra
  } as MessengerMessage;
}

describe("mergeConversationMessages ordering", () => {
  it("keeps server messages in ascending chronological id order", () => {
    const merged = mergeConversationMessages([serverMessage(3), serverMessage(1)], [serverMessage(2)]);
    expect(merged.map((m) => m.id)).toEqual([1, 2, 3]);
  });

  it("pins a freshly-sent local bubble to the BOTTOM, not the top", () => {
    const local = createLocalMessage(1, "hello", "text");
    // A negative local id would sort to the front under a naive `a.id - b.id` sort;
    // the merge must instead place it after every server row.
    const merged = mergeConversationMessages([serverMessage(10), serverMessage(11)], [local]);
    expect(merged.map((m) => m.id)).toEqual([10, 11, local.id]);
    expect(merged[merged.length - 1].id).toBe(local.id);
  });

  it("orders multiple pending bubbles by creation time after the server rows", () => {
    const first = createLocalMessage(1, "first", "text");
    const second: MessengerMessage = {
      ...createLocalMessage(1, "second", "text"),
      id: first.id - 5,
      message_id: first.id - 5,
      created_at: new Date(Date.parse(first.created_at || "") + 1000).toISOString(),
      client_message_id: "native-second"
    };
    const merged = mergeConversationMessages([serverMessage(2)], [second, first]);
    expect(merged.map((m) => m.body)).toEqual(["server-2", "first", "second"]);
  });

  it("does not make the bubble jump when the server acks it (same client id collapses in place)", () => {
    const local = createLocalMessage(1, "hi", "text");
    const withLocal = mergeConversationMessages([serverMessage(100)], [local]);
    expect(withLocal[withLocal.length - 1].id).toBe(local.id);

    // Server acks with a positive id but the same client_message_id.
    const acked = serverMessage(101, { client_message_id: local.client_message_id, is_mine: true });
    const reconciled = mergeConversationMessages(withLocal.filter((m) => m.id !== local.id), [acked]);
    expect(reconciled.map((m) => m.id)).toEqual([100, 101]);
    // Exactly one bubble for that client id — no duplicate.
    expect(reconciled.filter((m) => m.client_message_id === local.client_message_id)).toHaveLength(1);
    // Still anchored at the bottom, so the eye never sees it leap.
    expect(reconciled[reconciled.length - 1].id).toBe(101);
  });
});

describe("mergeConversationMessages dedupe + status", () => {
  it("clears local_status once the server accepts the message", () => {
    const local = createLocalMessage(1, "hi", "text");
    const acked = serverMessage(200, {
      client_message_id: local.client_message_id,
      local_status: "sending",
      delivery_status: undefined as unknown as string
    });
    const merged = mergeConversationMessages([local], [acked]);
    const bubble = merged.find((m) => m.client_message_id === local.client_message_id);
    expect(bubble?.id).toBe(200);
    expect(bubble?.local_status).toBeUndefined();
    expect(bubble?.delivery_status).toBe("sent");
  });

  it("preserves a pending message's sending status until it is acked", () => {
    const local = createLocalMessage(1, "hi", "text");
    const merged = mergeConversationMessages([], [local]);
    expect(merged[0].local_status).toBe("sending");
  });

  it("dedupes by client_message_id so an optimistic + realtime copy never double-render", () => {
    const local = createLocalMessage(1, "hi", "text");
    const realtimeEcho = { ...local };
    const merged = mergeConversationMessages([local], [realtimeEcho]);
    expect(merged).toHaveLength(1);
  });
});

describe("compareMessengerMessages", () => {
  it("sorts every pending row after every server row regardless of raw id", () => {
    const pending = createLocalMessage(1, "p", "text");
    expect(compareMessengerMessages(pending, serverMessage(1))).toBeGreaterThan(0);
    expect(compareMessengerMessages(serverMessage(1), pending)).toBeLessThan(0);
  });
});
