/**
 * What a person sees after pressing Confirm on an approval that is already dead.
 *
 * The server side of this was fixed first: `confirm_action` used to answer six
 * unrelated situations with one sentence, and now answers each of them with its own —
 * including the one that means the write already ran and must not be repeated. That
 * sentence arrives on the device as the `message` of a rejected request.
 *
 * It then went into a status banner that the chat screen renders `&& !keyboardVisible`.
 * A person taps Confirm on a card they summoned by typing, so the keyboard is up and
 * the banner is not drawn. The rejected press also left `undxComponents` untouched, so
 * the card stayed exactly as it was, and the token was already in the spent set, so
 * both of its buttons went grey. The whole visible consequence of the press was two
 * buttons dimming.
 *
 * These assertions are about the reading, not the drawing: `readTapOutcome` is the one
 * place that decides what a rejection means, and it is a named function rather than an
 * expression inside a two-thousand-line render for the reason this module's own header
 * records.
 */

import { PulseApiError } from "../../api/pulseApi";
import { readTapOutcome, UNDX_TAP_FALLBACK_MESSAGE } from "../actionCards";

/** The six sentences `services/undx_architecture.APPROVAL_STATE_MESSAGE` can send. */
const SERVER_SENTENCES = [
  "That confirmation is still valid and has not been used, but UNDX cannot carry out that action right now, so nothing changed.",
  "That confirmation ran out of time before it was used, so nothing changed. Ask again and confirm the new one.",
  "That confirmation was already used, so what it authorised has already been attempted. Check where things stand before confirming it again.",
  "That confirmation was cancelled, so nothing changed. Ask again if you still want it.",
  "What this confirmation was for changed after you approved it, so nothing changed. Check where things stand and confirm again.",
  "UNDX does not recognise that confirmation, so nothing changed.",
];

describe("the sentence the server sent is the sentence that survives", () => {
  it("carries each dead-approval sentence through unaltered", () => {
    SERVER_SENTENCES.forEach((sentence) => {
      expect(readTapOutcome(new PulseApiError(sentence, 409, "confirmation_invalid")).message).toBe(sentence);
    });
  });

  it("says something rather than nothing when the rejection carried no message", () => {
    expect(readTapOutcome(new PulseApiError("", 409, "confirmation_invalid")).message).toBe(UNDX_TAP_FALLBACK_MESSAGE);
    expect(readTapOutcome(undefined).message).toBe(UNDX_TAP_FALLBACK_MESSAGE);
    expect(readTapOutcome({ nothing: true }).message).toBe(UNDX_TAP_FALLBACK_MESSAGE);
  });

  it("resolves whether anything changed, for every sentence it can show", () => {
    // The property the server-side message map is held to. It is asserted again here
    // because this is the layer that could quietly substitute its own wording.
    [...SERVER_SENTENCES, UNDX_TAP_FALLBACK_MESSAGE].forEach((sentence) => {
      const shown = readTapOutcome(new PulseApiError(sentence, 409, "confirmation_invalid")).message;
      expect(/nothing changed|already been attempted/.test(shown)).toBe(true);
    });
  });
});

describe("only a press that never reached an answer may be pressed again", () => {
  it("re-arms when the request did not complete", () => {
    // Minted by pulseApi itself, in the catch around fetch. The server did not answer,
    // so the approval is very probably untouched — and a token is redeemable exactly
    // once, so a second press cannot produce a second write.
    const unreachable = new PulseApiError(
      "PulseSoc could not be reached. Check your connection and try again.",
      503,
      "request_unreachable",
    );
    expect(readTapOutcome(unreachable).retryable).toBe(true);
  });

  it("does not re-arm any state the server answered with", () => {
    SERVER_SENTENCES.forEach((sentence) => {
      expect(readTapOutcome(new PulseApiError(sentence, 409, "confirmation_invalid")).retryable).toBe(false);
    });
  });

  it("tells the two 503s apart by code, not by status", () => {
    // A reachable server answers 503 when the executor is switched off. An unreachable
    // one produces 503 because the client made it up. Reading the status alone would
    // re-arm a button against a server that had already refused it.
    const executorOff = new PulseApiError("UNDX actions are currently read-only for this account.", 503, "undx_actions_disabled");
    expect(executorOff.status).toBe(503);
    expect(readTapOutcome(executorOff).retryable).toBe(false);
  });

  it("does not re-arm something that is not a PulseApiError at all", () => {
    expect(readTapOutcome(new Error("boom")).retryable).toBe(false);
    expect(readTapOutcome("request_unreachable").retryable).toBe(false);
    expect(readTapOutcome(null).retryable).toBe(false);
  });
});
