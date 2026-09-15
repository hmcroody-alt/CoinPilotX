jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import AsyncStorage from "@react-native-async-storage/async-storage";
import { messageKey, mintClientMessageId } from "../messengerOrdering";
import {
  createLocalMessage,
  enqueueMessengerMessage,
  messengerOutboxStream,
  MessengerMessage,
  normalizeMessages
} from "../messenger";
import { pendingMutations } from "../../core/mutations/outbox";
import { readFileSync } from "fs";
import { join } from "path";

const SRC = join(__dirname, "..", "..");

function source(relativePath: string): string {
  return readFileSync(join(SRC, relativePath), "utf-8");
}

describe("mintClientMessageId", () => {
  it("never repeats, even for ids minted inside the same millisecond", () => {
    // A double tap, an offline queue drain, or a share to several people at once
    // all mint in the same tick. The server now treats a repeated id as a repeat
    // of the SAME message and discards the second one, so a collision here is
    // silent message loss -- strictly worse than the duplicate it prevents.
    const frozen = jest.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
    try {
      const ids = new Set(Array.from({ length: 5000 }, () => mintClientMessageId()));
      expect(ids.size).toBe(5000);
    } finally {
      frozen.mockRestore();
    }
  });

  it("stays inside the column limit the server enforces", () => {
    expect(mintClientMessageId().length).toBeLessThanOrEqual(120);
    expect(mintClientMessageId("a".repeat(400)).length).toBeLessThanOrEqual(120);
  });

  it("carries the caller's prefix so a send path is identifiable in logs", () => {
    expect(mintClientMessageId("camera").startsWith("camera-")).toBe(true);
  });
});

describe("messageKey", () => {
  it("prefers the client id so a local bubble and its server ack share one identity", () => {
    const local = createLocalMessage(1, "hi", "text");
    const acked = { ...local, id: 500, message_id: 500 } as MessengerMessage;
    expect(messageKey(local)).toBe(messageKey(acked));
  });

  it("falls back to the server id for messages that never had a client id", () => {
    expect(messageKey({ id: 77 } as MessengerMessage)).toBe("77");
  });
});

describe("createLocalMessage identity", () => {
  it("mints an id when none is supplied", () => {
    expect(createLocalMessage(1, "hi", "text").client_message_id).toBeTruthy();
  });

  it("adopts a caller-supplied id so a retry keeps the identity of the first attempt", () => {
    const supplied = mintClientMessageId("retry");
    expect(createLocalMessage(1, "hi", "text", supplied).client_message_id).toBe(supplied);
  });

  it("gives two messages sent in the same tick distinct identities", () => {
    const frozen = jest.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
    try {
      const a = createLocalMessage(1, "first", "text");
      const b = createLocalMessage(1, "second", "text");
      expect(a.client_message_id).not.toBe(b.client_message_id);
    } finally {
      frozen.mockRestore();
    }
  });
});

describe("outbound queue identity", () => {
  beforeEach(async () => {
    await AsyncStorage.clear();
  });

  it("queues a message that arrived without an id under a unique minted one", async () => {
    const frozen = jest.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
    try {
      await enqueueMessengerMessage(1, { body: "first" });
      await enqueueMessengerMessage(1, { body: "second" });
    } finally {
      frozen.mockRestore();
    }
    // Observed through the outbox's own query rather than its storage key: the
    // property under test is "two messages, two identities", and a test that
    // reads the raw record stops testing that the moment the layout changes.
    const queued = await pendingMutations(messengerOutboxStream(1));
    expect(queued).toHaveLength(2);
    expect(new Set(queued.map((op) => op.idempotencyKey)).size).toBe(2);
  });

  it("does not enqueue the same logical message twice", async () => {
    const clientId = mintClientMessageId();
    await enqueueMessengerMessage(1, { body: "hi", client_message_id: clientId });
    await enqueueMessengerMessage(1, { body: "hi", client_message_id: clientId });
    expect(await pendingMutations(messengerOutboxStream(1))).toHaveLength(1);
  });

  it("queues under the client id the server dedupes on, not a key of its own", async () => {
    // The idempotency key IS the client_message_id. If the outbox minted its
    // own, a retry would be a new key to the server and the message would arrive
    // twice -- the one thing the client id exists to prevent.
    const clientId = mintClientMessageId();
    await enqueueMessengerMessage(7, { body: "hi", client_message_id: clientId });
    const [queued] = await pendingMutations(messengerOutboxStream(7));
    expect(queued.idempotencyKey).toBe(clientId);
  });

  it("carries a message queued by an older build into the outbox instead of losing it", async () => {
    // Upgrading while offline must not drop what the user already typed, and the
    // users it would affect are the least likely to report it.
    const legacyId = mintClientMessageId("legacy");
    await AsyncStorage.setItem(
      "pulsesoc.native.messenger.v2.outbound_queue",
      JSON.stringify([{ conversationId: 3, payload: { body: "written offline", client_message_id: legacyId } }])
    );

    await enqueueMessengerMessage(3, { body: "written after upgrade" });

    const queued = await pendingMutations(messengerOutboxStream(3));
    expect(queued.map((op) => op.idempotencyKey)).toContain(legacyId);
    expect(queued).toHaveLength(2);
    expect(await AsyncStorage.getItem("pulsesoc.native.messenger.v2.outbound_queue")).toBeNull();
  });
});

describe("every send path carries a stable identity", () => {
  it("the transport refuses to send a message with no client id", () => {
    const transport = source("api/messenger.ts");
    const fn = transport.slice(transport.indexOf("export async function sendConversationMessage("));
    expect(fn.slice(0, fn.indexOf("if (conversationId ==="))).toContain("mintClientMessageId()");
  });

  it("ChatScreen retry reuses the identity of the attempt that failed", () => {
    // This is the defect that turned a lost response into a guaranteed duplicate:
    // the first attempt may have reached the server and only the ack went missing.
    const chat = source("screens/ChatScreen.tsx");
    const retry = chat.slice(chat.indexOf("const retryMessage = useCallback("));
    expect(retry.slice(0, retry.indexOf("}, [sendPayload]);"))).toContain(
      "client_message_id: message.client_message_id"
    );
  });

  it("both ChatScreen optimistic bubbles adopt the payload identity", () => {
    const chat = source("screens/ChatScreen.tsx");
    const calls = chat.match(/createLocalMessage\([^)]*\)/g) || [];
    expect(calls.length).toBeGreaterThan(0);
    calls.forEach((call) => expect(call).toContain("payload.client_message_id"));
  });

  it("CameraStudio holds one identity across publish retries", () => {
    // Publishing is upload-then-send; either step can fail after the send has
    // already reached the server, so the id must outlive the attempt.
    const camera = source("screens/CameraStudioScreen.tsx");
    expect(camera).toContain("messengerClientIdRef = useRef(");
    expect(camera).toContain("client_message_id: messengerClientIdRef.current");
  });

  it("PulseShare holds one identity per recipient", () => {
    const share = source("screens/PulseShareScreen.tsx");
    expect(share).toContain("client_message_id:");
  });
});

describe("media identity is never guessed from a transport id", () => {
  it("carries the foundation media id through normalisation, beside attachment_id", () => {
    // A Comm-v2 attachment payload states both. attachment_id is the
    // comm_v2 attachment row; media_upload_id is the foundation
    // message_attachments row, and only the latter addresses
    // /api/messages/media/<id>/access.
    const [message] = normalizeMessages(
      [
        {
          message_id: 771,
          message_type: "image",
          attachments: [
            {
              id: 422,
              attachment_id: 422,
              media_upload_id: 33,
              url: "/api/messages/media/33/download"
            }
          ]
        } as unknown as MessengerMessage
      ],
      9
    );
    expect(message.attachment_id).toBe(422);
    expect(message.media_upload_id).toBe(33);
    expect(message.media_url).toBe("/api/messages/media/33/download");
  });

  it("the forbidden truthy-integer fallback is gone from the access module", () => {
    // `Number(attachmentId || 0) || attachmentIdFromMediaUrl(fallbackUrl)` let
    // any truthy transport id shadow the canonical id in the URL next to it.
    const media = source("media/messengerMediaAccess.ts");
    expect(media).not.toMatch(/Number\(attachmentId \|\| 0\)\s*\|\|/);
    expect(media).toContain("resolveCanonicalMessengerMediaId");
  });

  it("ChatScreen passes a labelled identity, not a bare attachment integer", () => {
    const chat = source("screens/ChatScreen.tsx");
    expect(chat).not.toMatch(/useMessengerMediaAccessUrl\(\s*message\.attachment_id/);
    expect(chat).toContain("mediaUploadId: message.media_upload_id");
  });
});
