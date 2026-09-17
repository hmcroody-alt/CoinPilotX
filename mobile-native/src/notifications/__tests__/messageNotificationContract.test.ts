/**
 * What the classifier is allowed to believe.
 *
 * These tests are mostly negative, and that is the point. Dismissing a delivered
 * notification destroys the only copy of something the user may not have seen,
 * so the interesting assertions are all about what does NOT get classified as a
 * dismissable message — not about the happy path, which is one line.
 *
 * Several of them feed in decoys: payloads whose title, body and sender name say
 * "message" as loudly as possible while the structural fields say otherwise. A
 * classifier that read any of those fields would pass a naive happy-path suite
 * and fail these, which is the whole reason they are written this way. Anyone
 * who can send a message can choose those strings.
 */

import {
  MESSAGE_PUSH_SCHEMA_VERSION,
  MESSAGE_READ_STATE_BATCH_LIMIT,
  PRESERVED_NOTIFICATION_FAMILIES,
  classifyNotificationData,
  flattenNotificationData,
  ownershipFor,
  parseMessageNotification
} from "../messageNotificationContract";

const ME = 4242;
const THEM = 99;

/** A current-contract message push, as `pulse_communications_v2/service.py` emits it. */
function v2Payload(overrides: Record<string, unknown> = {}) {
  return {
    schemaVersion: MESSAGE_PUSH_SCHEMA_VERSION,
    notificationType: "message",
    messageId: 555,
    conversationId: 12,
    recipientUserId: ME,
    senderId: THEM,
    sentAt: "2026-09-16T10:00:00",
    notificationKey: "message:12:555",
    ...overrides
  };
}

/** A pre-contract message push — ids and a legacy type, but no recipient. */
function v1Payload(overrides: Record<string, unknown> = {}) {
  return {
    type: "chat_message",
    push_type: "chat_message",
    message_id: 555,
    conversation_id: 12,
    sender_id: THEM,
    ...overrides
  };
}

describe("classifyNotificationData — recognising a message", () => {
  it("reads the current contract and records that ownership was claimed", () => {
    const result = classifyNotificationData(v2Payload());
    expect(result.kind).toBe("message");
    if (result.kind !== "message") return;
    expect(result.ref.messageId).toBe(555);
    expect(result.ref.conversationId).toBe(12);
    expect(result.ref.recipientUserId).toBe(ME);
    expect(result.ref.ownership).toBe("claimed");
    expect(result.ref.schemaVersion).toBe(MESSAGE_PUSH_SCHEMA_VERSION);
  });

  it("still recognises a legacy payload, but refuses to guess who it was for", () => {
    // This is the alert already sitting in Notification Center on upgrade day.
    // It must be classifiable — otherwise the pile never clears — while being
    // honest that it cannot answer "is this mine?".
    const result = classifyNotificationData(v1Payload());
    expect(result.kind).toBe("message");
    if (result.kind !== "message") return;
    expect(result.ref.messageId).toBe(555);
    expect(result.ref.ownership).toBe("unknown");
    expect(result.ref.recipientUserId).toBeNull();
    expect(result.ref.schemaVersion).toBe(0);
  });

  it.each(["message", "chat_message", "private_message", "voice_message"])(
    "accepts the legacy type %s",
    (legacyType) => {
      expect(classifyNotificationData(v1Payload({ type: legacyType, push_type: legacyType })).kind).toBe("message");
    }
  );

  it("accepts snake_case and camelCase ids interchangeably", () => {
    const snake = classifyNotificationData({ notification_type: "message", message_id: 7, conversation_id: 3 });
    const camel = classifyNotificationData({ notificationType: "message", messageId: 7, conversationId: 3 });
    expect(snake.kind).toBe("message");
    expect(camel.kind).toBe("message");
  });

  it("tolerates ids arriving as strings, which APNs does", () => {
    const result = classifyNotificationData(v2Payload({ messageId: "555", conversationId: "12" }));
    expect(result.kind).toBe("message");
    if (result.kind !== "message") return;
    expect(result.ref.messageId).toBe(555);
  });
});

describe("classifyNotificationData — refusing to recognise a message", () => {
  it("preserves a payload that declares any other type", () => {
    const result = classifyNotificationData({ notificationType: "security", messageId: 555, conversationId: 12 });
    expect(result).toEqual({ kind: "other", reason: "declared_other_type" });
  });

  it("preserves a security alert even when it carries a conversation id", () => {
    // Cross-posted alerts really do carry conversation context. The declared
    // type has to win, or a login alert about a suspicious device disappears
    // because the conversation it references was read.
    const result = classifyNotificationData({
      notificationType: "login_alert",
      conversationId: 12,
      messageId: 555,
      title: "New message from Alex"
    });
    expect(result).toEqual({ kind: "other", reason: "declared_other_type" });
  });

  it.each(PRESERVED_NOTIFICATION_FAMILIES)("preserves the %s family", (family) => {
    // The list is documentation, not a filter — the filter is the positive test
    // for "message". This asserts the two agree, so a family added to the list
    // without thinking cannot silently be a message.
    expect(classifyNotificationData({ notificationType: family, messageId: 1, conversationId: 1 }).kind).toBe("other");
  });

  it("preserves a payload that identifies no type at all", () => {
    expect(classifyNotificationData({ messageId: 555, conversationId: 12 })).toEqual({
      kind: "other",
      reason: "no_type_declared"
    });
  });

  it("preserves a message that cannot name its message", () => {
    expect(classifyNotificationData(v2Payload({ messageId: 0 }))).toEqual({
      kind: "other",
      reason: "message_without_id"
    });
  });

  it("preserves a message that cannot name its conversation", () => {
    expect(classifyNotificationData(v2Payload({ conversationId: undefined }))).toEqual({
      kind: "other",
      reason: "message_without_conversation"
    });
  });

  it.each([null, undefined, "", "a string", 12, [], [{ notificationType: "message" }], true])(
    "preserves the unreadable payload %p",
    (data) => {
      expect(classifyNotificationData(data)).toEqual({ kind: "other", reason: "unreadable_payload" });
    }
  );

  it.each(["12abc", "-1", "0", "1.5", "NaN", " ", "1e3"])("refuses the malformed id %p", (raw) => {
    // `parseInt("12abc")` is 12. A classifier built on it would dismiss message
    // 12 on the strength of a payload that never named message 12.
    expect(classifyNotificationData(v2Payload({ messageId: raw }))).toEqual({
      kind: "other",
      reason: "message_without_id"
    });
  });
});

describe("classifyNotificationData — the fields it must never read", () => {
  /**
   * Every case here has message-shaped human text and non-message structure.
   * All of them must be preserved. If any starts passing, something began
   * reading a field the sender controls.
   */
  const decoys: Array<[string, Record<string, unknown>]> = [
    ["title", { title: "New message from Alex", notificationType: "marketplace", messageId: 5, conversationId: 5 }],
    ["body", { body: "You have 3 unread messages", notificationType: "order", messageId: 5, conversationId: 5 }],
    ["subtitle", { subtitle: "message", notificationType: "crypto", messageId: 5, conversationId: 5 }],
    ["sender name", { sender_name: "message", actor_name: "message", messageId: 5, conversationId: 5 }],
    ["thread id", { threadId: "message", messageId: 5, conversationId: 5 }],
    ["category", { categoryIdentifier: "message", messageId: 5, conversationId: 5 }]
  ];

  it.each(decoys)("is not persuaded by a message-shaped %s", (_label, payload) => {
    expect(classifyNotificationData(payload).kind).toBe("other");
  });

  it("classifies identically whether or not the human-readable fields are present", () => {
    const bare = classifyNotificationData(v2Payload());
    const dressed = classifyNotificationData(
      v2Payload({
        title: "Alex",
        body: "see you at 6",
        subtitle: "Direct message",
        sender_name: "Alex Rivera",
        message_preview: "see you at 6"
      })
    );
    expect(dressed).toEqual(bare);
  });

  it("does not carry any human-readable field into the ref it returns", () => {
    // The ref is what gets passed around and (in counts form) logged. If a
    // preview string can ride along in it, it can end up in a device console.
    const result = classifyNotificationData(
      v2Payload({ title: "Alex", body: "see you at 6", message_preview: "see you at 6", sender_name: "Alex" })
    );
    expect(result.kind).toBe("message");
    if (result.kind !== "message") return;
    const serialised = JSON.stringify(result.ref);
    expect(serialised).not.toContain("Alex");
    expect(serialised).not.toContain("see you at 6");
  });
});

describe("flattenNotificationData", () => {
  it("reaches the dictionary one level down, where Expo puts it", () => {
    const result = classifyNotificationData({ data: v2Payload() });
    expect(result.kind).toBe("message");
  });

  it("lets the outer level win, so a transport field cannot be shadowed", () => {
    const flat = flattenNotificationData({ notificationType: "security", data: { notificationType: "message" } });
    expect(flat?.notificationType).toBe("security");
  });

  it("returns null rather than throwing on a hostile shape", () => {
    expect(flattenNotificationData(undefined)).toBeNull();
    expect(flattenNotificationData([1, 2, 3])).toBeNull();
  });
});

describe("ownershipFor", () => {
  const mine = parseMessageNotification(v2Payload())!;
  const theirs = parseMessageNotification(v2Payload({ recipientUserId: THEM }))!;
  const legacy = parseMessageNotification(v1Payload())!;

  it("answers mine when the payload named this account", () => {
    expect(ownershipFor(mine, ME)).toBe("mine");
  });

  it("answers theirs when the payload named a different account", () => {
    expect(ownershipFor(theirs, ME)).toBe("theirs");
  });

  it("answers unknown for a legacy payload rather than collapsing it either way", () => {
    // "mine" would let account A's stale alerts be cleared while B is signed in.
    // "theirs" would mean legacy alerts can never be cleared at all.
    expect(ownershipFor(legacy, ME)).toBe("unknown");
  });

  it("answers unknown when nobody is signed in", () => {
    expect(ownershipFor(mine, 0)).toBe("unknown");
    expect(ownershipFor(mine, -1)).toBe("unknown");
  });
});

describe("cross-language constants", () => {
  /**
   * These two numbers exist twice — once here, once in
   * `pulse_communications_v2/service.py` — and the failure mode when they drift
   * is silent in both directions: the server starts emitting a shape this parser
   * declines, or the client sends a batch the server clamps and reads the
   * dropped ids as "not unread". Neither errors. The Python side of this pin
   * lives in `tests/test_message_notification_read_reconciliation.py`, which
   * reads THIS file, so the two halves cannot be satisfied independently.
   */
  it("pins the schema version the Python test also reads", () => {
    expect(MESSAGE_PUSH_SCHEMA_VERSION).toBe(2);
  });

  it("pins the read-state batch limit the Python test also reads", () => {
    expect(MESSAGE_READ_STATE_BATCH_LIMIT).toBe(200);
  });
});
