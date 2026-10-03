/**
 * What a failed conversation load is allowed to say and do.
 *
 * The defect: "Message seller" handed the buyer a conversation id the server
 * did not recognise, and the screen answered a 404 with three claims at once —
 * "Conversation not found." in the panel, "Messages unavailable" in the
 * header, and "PULSE LINK · RECONNECTING" over a composer that still accepted
 * typing. Two of those were false. There is no realtime link in that screen to
 * reconnect, and the server had just refused the conversation, so anything
 * typed would have been lost.
 *
 * Mutation contract — each of these must turn this file red:
 *   - mapping 404 to the `retrying` posture (which is what prints
 *     "RECONNECTING");
 *   - `blocksSending: false` for any of the four refusal kinds;
 *   - `conversationComposerAvailable` returning true for a fatal failure;
 *   - `needsResolution` on a kind other than a missing conversation, or off it;
 *   - reading a 403 block as a plain `forbidden`;
 *   - classifying on `error.code` alone (the v2 blueprint never sets it).
 */

import {
  CONVERSATION_FAILURE_COPY,
  classifyConversationFailure,
  conversationComposerAvailable,
  conversationRecovery
} from "../conversationFailure";

const FALLBACK = "Messages could not load.";

/**
 * A rejection shaped the way `pulseApi` actually throws one for this backend.
 *
 * `code` is left undefined on purpose. The communications blueprint answers
 * `{ok:false, status:"not_found", ...}` and `pulseApi` reads a code from
 * `error_code`/`error` only, so the code reaches the client *inside* `details`
 * and nowhere else. A classifier that trusted `code` would see nothing.
 */
function apiError(status: number, message: string, v2Code?: string) {
  return { name: "PulseApiError", message, status, code: undefined, details: v2Code ? { ok: false, status: v2Code } : undefined };
}

describe("the failure the buyer actually hit", () => {
  it("calls a 404 a missing conversation, not a reconnection", () => {
    const failure = classifyConversationFailure(apiError(404, "Conversation not found.", "not_found"), FALLBACK);
    expect(failure.kind).toBe("conversation_not_found");
    expect(failure.posture).toBe("unavailable");
    // The posture is what selects the wording, so this is the assertion that
    // actually pins "RECONNECTING" out of this path.
    expect(CONVERSATION_FAILURE_COPY[failure.posture].state).not.toBe(CONVERSATION_FAILURE_COPY.retrying.state);
  });

  it("takes the composer away, because the server would refuse the message", () => {
    const failure = classifyConversationFailure(apiError(404, "Conversation not found."), FALLBACK);
    expect(failure.blocksSending).toBe(true);
    expect(conversationComposerAvailable(failure, false)).toBe(false);
    // Not even with history on screen: a conversation the server does not have
    // cannot receive a message whatever is cached in front of it.
    expect(conversationComposerAvailable(failure, true)).toBe(false);
  });

  it("recovers by resolving the pair when it knows who the other party is", () => {
    const failure = classifyConversationFailure(apiError(404, "Conversation not found."), FALLBACK);
    expect(failure.needsResolution).toBe(true);
    expect(conversationRecovery(failure, true)).toBe("resolve");
    // Re-requesting a refused id can only be refused again, so without a peer
    // to resolve against there is no Retry worth offering.
    expect(conversationRecovery(failure, false)).toBe("back");
    expect(failure.refetchable).toBe(false);
  });
});

describe("the other ways a conversation refuses to open", () => {
  it("separates a block from a plain lack of access", () => {
    const blocked = classifyConversationFailure(apiError(403, "Messaging is unavailable for this conversation.", "blocked"), FALLBACK);
    expect(blocked.kind).toBe("messaging_not_allowed");
    expect(blocked.posture).toBe("messaging_off");

    const forbidden = classifyConversationFailure(apiError(403, "You do not have access to this conversation.", "forbidden"), FALLBACK);
    expect(forbidden.kind).toBe("forbidden");
    expect(forbidden.posture).toBe("unavailable");
  });

  it("never offers a retry for a refusal", () => {
    for (const failure of [
      classifyConversationFailure(apiError(403, "no", "blocked"), FALLBACK),
      classifyConversationFailure(apiError(403, "no", "forbidden"), FALLBACK),
      classifyConversationFailure(apiError(401, "Sign in.", "auth_required"), FALLBACK)
    ]) {
      expect(failure.blocksSending).toBe(true);
      expect(failure.refetchable).toBe(false);
      expect(conversationRecovery(failure, true)).toBe("back");
    }
  });

  it("reads an expired session as a session problem", () => {
    const failure = classifyConversationFailure(apiError(401, "Sign in again."), FALLBACK);
    expect(failure.kind).toBe("auth_required");
    expect(failure.posture).toBe("signed_out");
  });
});

describe("the failures that are about the connection, not the conversation", () => {
  it("keeps a thread usable through a transport failure", () => {
    // A paging or polling hiccup over a thread that works. Taking the composer
    // away here is the lie in the other direction — the message would send.
    const failure = classifyConversationFailure({ message: "Network request failed" }, FALLBACK);
    expect(failure.kind).toBe("network_error");
    expect(failure.blocksSending).toBe(false);
    expect(conversationComposerAvailable(failure, true)).toBe(true);
    expect(conversationRecovery(failure, false)).toBe("refetch");
  });

  it("still quiets the composer when the failure left nothing on screen", () => {
    // The fatal panel owns the view in this case, and a live composer beneath
    // it offers to send into a conversation that never opened.
    const failure = classifyConversationFailure({ message: "Network request failed" }, FALLBACK);
    expect(conversationComposerAvailable(failure, false)).toBe(false);
  });

  it("reads the shared read deadline as a connection problem", () => {
    const failure = classifyConversationFailure(
      { message: "PulseSoc took too long to respond. Try again.", status: 504, code: "request_timeout" },
      FALLBACK
    );
    expect(failure.kind).toBe("network_error");
    expect(failure.refetchable).toBe(true);
  });

  it("is the one posture still allowed to say reconnecting", () => {
    const failure = classifyConversationFailure(apiError(500, "Something went wrong."), FALLBACK);
    expect(failure.posture).toBe("retrying");
    expect(CONVERSATION_FAILURE_COPY.retrying.state).toBe("messaging:chat.stateReconnecting");
  });

  it("does not blame the network for a failure it cannot explain", () => {
    // A client-side bug that throws here must not be reported to the person as
    // "check your connection" — that sends them to fix something that is fine.
    const failure = classifyConversationFailure(new TypeError("undefined is not an object"), FALLBACK);
    expect(failure.kind).toBe("load_failed");
    expect(failure.posture).toBe("retrying");
  });
});

describe("the message carried alongside", () => {
  it("keeps the server's sentence for the banner", () => {
    expect(classifyConversationFailure(apiError(404, "Conversation not found."), FALLBACK).message).toBe(
      "Conversation not found."
    );
  });

  it("falls back when the rejection said nothing", () => {
    expect(classifyConversationFailure({ status: 500 }, FALLBACK).message).toBe(FALLBACK);
    expect(classifyConversationFailure(null, FALLBACK).message).toBe(FALLBACK);
  });
});

describe("no failure at all", () => {
  it("leaves the composer alone", () => {
    expect(conversationComposerAvailable(null, false)).toBe(true);
    expect(conversationComposerAvailable(null, true)).toBe(true);
  });
});

describe("the copy table", () => {
  it("gives every posture a distinct status line", () => {
    const states = Object.values(CONVERSATION_FAILURE_COPY).map((copy) => copy.state);
    expect(new Set(states).size).toBe(states.length);
  });

  it("names keys that exist in the shipped catalog", () => {
    // Guards the failure mode the i18n engine makes invisible: an absent key
    // humanizes into plausible English, so a typo ships as real-looking copy.
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const catalog = require("../../i18n/catalogs/en/extended.json") as {
      messaging: { chat: Record<string, string> };
    };
    for (const copy of Object.values(CONVERSATION_FAILURE_COPY)) {
      for (const key of [copy.header, copy.state, copy.title, copy.body]) {
        expect(key.startsWith("messaging:chat.")).toBe(true);
        expect(catalog.messaging.chat).toHaveProperty(key.slice("messaging:chat.".length));
      }
    }
  });
});
