/**
 * The Messenger-layer presence vocabulary.
 *
 * `presence.ts` owns the rich presence record (status + activity + last seen)
 * used by the chat header. The conversation *list* carries a much smaller
 * thing: a single token per row, normalized inside `messenger.ts` and rendered
 * through the `domain.ts` helpers. That is a second decoding path, and a second
 * path is exactly where a green dot can reappear after being removed from the
 * first one.
 *
 * These tests cover that second path only; the record-level normalizer is
 * covered in `presence.test.ts` and is deliberately not re-tested here.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import { ASSISTANT_PRESENCE, MessengerConversation, normalizeConversations } from "../messenger";
import { isActivePresence, isAssistantPresence, presenceLabel } from "../../pulseCommand/domain";

/** Build one conversation row carrying an arbitrary raw presence payload. */
function rowWithPresence(presence: unknown): MessengerConversation {
  return {
    id: 1,
    conversation_id: 1,
    conversation_type: "direct",
    title: "Peer",
    presence
  } as unknown as MessengerConversation;
}

function normalizedPresence(presence: unknown) {
  return normalizeConversations([rowWithPresence(presence)])[0].presence;
}

describe("conversation-list presence token", () => {
  it("passes the three canonical human statuses through unchanged", () => {
    expect(normalizedPresence("online")).toBe("online");
    expect(normalizedPresence("away")).toBe("away");
    expect(normalizedPresence("offline")).toBe("offline");
  });

  it("reads the token out of an object payload under any of its field names", () => {
    // The list endpoint has historically nested the value under `status`, and
    // other callers under `presence` or `state`. All three must decode the
    // same way rather than one of them silently yielding "unknown".
    expect(normalizedPresence({ status: "online" })).toBe("online");
    expect(normalizedPresence({ presence: "away" })).toBe("away");
    expect(normalizedPresence({ state: "offline" })).toBe("offline");
  });

  it("accepts a differently-cased token from the server", () => {
    expect(normalizedPresence("ONLINE")).toBe("online");
    expect(normalizedPresence({ status: "Away" })).toBe("away");
  });

  // The asymmetry, restated for this layer. Everything below must decode to ""
  // (unknown), never to an online-ish token.
  it.each([
    ["null", null],
    ["undefined", undefined],
    ["empty string", ""],
    ["a number", 1],
    ["an array", []],
    ["an empty object", {}],
    ["legacy 'active' token", "active"],
    ["legacy 'available' token", "available"],
    ["legacy 'live' token", "live"],
    ["legacy 'idle' token", "idle"],
    ["'typing' as a status", { status: "typing" }],
    ["'hidden' -- the leaked block-detection value", { status: "hidden" }],
    ["a nested object where a token belongs", { status: { value: "online" } }]
  ])("decodes %s to unknown rather than to a live token", (_label, input) => {
    const token = normalizedPresence(input);
    expect(token).toBe("");
    expect(isActivePresence(token)).toBe(false);
  });

  it("treats available:false as no presence at all, not as a status to read past", () => {
    // This is the privacy path. An invisible or block-restricted peer is
    // reported as available:false, and the row must then look exactly like a
    // row for someone we have no information about. If the normalizer fell
    // through to the status field here, a client could diff the two and learn
    // it had been blocked.
    expect(normalizedPresence({ available: false, status: "online" })).toBe("");
    expect(normalizedPresence({ available: false, status: "offline" })).toBe("");
    expect(normalizedPresence({ available: false })).toBe("");
    // Byte-identical to the no-information row.
    expect(normalizedPresence({ available: false, status: "online" })).toBe(normalizedPresence(undefined));
  });

  it("keeps available:true rows decoding normally", () => {
    expect(normalizedPresence({ available: true, status: "online" })).toBe("online");
  });

  it("preserves the assistant marker, which is outside the human vocabulary", () => {
    expect(normalizedPresence(ASSISTANT_PRESENCE)).toBe("assistant");
    expect(isActivePresence(ASSISTANT_PRESENCE)).toBe(false);
    expect(isAssistantPresence(ASSISTANT_PRESENCE)).toBe(true);
  });

  it("does not let a server payload claim assistant-hood for a conversation", () => {
    // The marker is a client-side constant this app stamps onto the one row it
    // knows is UNDX -- not a value the server may assert. Honouring it inside a
    // status object would let a payload give a human row the "Always available"
    // label, which is a fabricated availability claim of exactly the kind this
    // work exists to remove. It decodes to unknown instead.
    expect(normalizedPresence({ status: ASSISTANT_PRESENCE })).toBe("");
    expect(normalizedPresence({ presence: "assistant" })).toBe("");
  });

  it("never invents presence for a row that carried none", () => {
    const row = normalizeConversations([
      { id: 4, conversation_id: 4, conversation_type: "direct", title: "Peer" } as unknown as MessengerConversation
    ])[0];
    expect(row.presence).toBe("");
    expect(isActivePresence(row.presence)).toBe(false);
  });
});

describe("presence predicates over the list token", () => {
  it("isActivePresence is true for exactly one token", () => {
    expect(isActivePresence("online")).toBe(true);
    for (const token of ["", "away", "offline", "assistant", "active", "available", "live", "typing", undefined]) {
      expect(isActivePresence(token)).toBe(false);
    }
  });

  it("isAssistantPresence never overlaps isActivePresence", () => {
    // A bot must not light a human presence dot, and a human must not get the
    // always-available label. The two predicates partition the vocabulary.
    for (const token of ["online", "away", "offline", "assistant", "", "garbage"]) {
      expect(isActivePresence(token) && isAssistantPresence(token)).toBe(false);
    }
  });

  it("agrees with the normalizer on every payload shape", () => {
    // The decisive property: whatever the raw payload, the token the list
    // stores and the dot the row renders must be derived from the same
    // decision. Nothing may re-infer liveness from the raw value.
    for (const raw of [null, {}, "online", "active", { status: "online" }, { available: false, status: "online" }]) {
      const token = normalizedPresence(raw);
      expect(isActivePresence(token)).toBe(token === "online");
    }
  });
});

describe("presenceLabel", () => {
  it("labels the human statuses", () => {
    expect(presenceLabel("online")).toBe("Online");
    expect(presenceLabel("away")).toBe("Away");
    expect(presenceLabel("offline")).toBe("Offline");
  });

  it("gives the assistant its own wording instead of a human status", () => {
    expect(presenceLabel(ASSISTANT_PRESENCE)).toBe("Always available");
  });

  it("renders nothing for an unknown token rather than guessing", () => {
    // An unknown token means "we do not know", and the correct rendering of
    // that is empty space -- not "Offline", which would be a claim we cannot
    // support, and certainly not "Online".
    for (const token of ["", undefined, "active", "available", "live", "hidden", "juggling"]) {
      expect(presenceLabel(token)).toBe("");
    }
  });
});
