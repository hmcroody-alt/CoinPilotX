/**
 * The card answers the press that was made on it.
 *
 * `tapOutcome.test.ts` proves `readTapOutcome` reads a rejection correctly. That proves
 * nothing about whether the reading is ever drawn, and drawing is the whole defect. The
 * server now distinguishes six ways an approval can be dead and sends one sentence per
 * state; the client put that sentence into a status banner rendered
 * `&& !keyboardVisible`. A person taps Confirm on a card they summoned by typing, so the
 * keyboard is up and the banner is not drawn. The refused press also left
 * `undxComponents` untouched, so the card stayed exactly as it was, and the token was
 * already in the spent set, so Confirm *and* Cancel went grey — including Cancel, which
 * left no way to clear the card at all.
 *
 * The whole visible consequence of pressing Confirm was two buttons dimming. So these
 * assertions render the real screen, send a real message, press the real control, and
 * look for the sentence.
 */

import React from "react";
import { Keyboard } from "react-native";
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

// Native modules this screen reaches on import. Stubbed rather than exercised: none of
// them is under test and all of them throw at require time under jest-expo.
jest.mock("expo-av", () => ({ Audio: { setAudioModeAsync: jest.fn(), Recording: class {}, Sound: class {} } }));
jest.mock("expo-file-system", () => ({ File: class {} }));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({ launchImageLibraryAsync: jest.fn(), requestMediaLibraryPermissionsAsync: jest.fn() }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));

/** Verbatim shape of an agent confirmation, as `undx_agent_runtime.build_card` emits it. */
const confirmationComponent = {
  component: "action_confirmation",
  capability_id: "crypto.alerts.pause",
  status: "confirmation_required",
  action_name: "Pause one crypto alert so it stops triggering",
  resource_label: "BTC alert · above · 999,999",
  current_value: "active",
  proposed_value: "paused",
  confirmation_id: "undx_confirm_21",
  confirmation_token: "tok-batch21",
  expires_at: "2026-07-30T23:59:00+00:00",
};

/**
 * A second, unrelated approval, so the rail can hold two at once.
 *
 * The agent produces one card per pending approval and the screen renders them from a
 * single list, so two is a state a person reaches by asking for two things. It is also
 * the only state in which the outcome's token match does anything: with one card on
 * screen, matching on the token and matching on nothing at all look identical.
 */
const otherConfirmationComponent = {
  ...confirmationComponent,
  capability_id: "crypto.alerts.delete",
  action_name: "Delete one crypto alert permanently",
  resource_label: "ETH alert · below · 1,000",
  proposed_value: "deleted",
  confirmation_id: "undx_confirm_22",
  confirmation_token: "tok-batch21-other",
};

const mockConfirm = jest.fn();
const mockCancel = jest.fn();
/** What the server answers a sent message with. Reset per test. */
const mockSend = jest.fn();

jest.mock("../../api/messenger", () => {
  const actual = jest.requireActual("../../api/messenger");
  return {
    ...actual,
    confirmPulseAiAction: (...args: unknown[]) => mockConfirm(...args),
    cancelPulseAiAction: (...args: unknown[]) => mockCancel(...args),
    getPulseAiConversation: jest.fn().mockResolvedValue({
      conversation: { id: actual.PULSE_AI_CONVERSATION_ID, title: "UNDX" },
      messages: [],
      presence: { typing: [] },
    }),
    sendPulseAiMessage: (...args: unknown[]) => mockSend(...args),
    loadCachedMessages: jest.fn().mockResolvedValue([]),
    cacheMessages: jest.fn().mockResolvedValue(undefined),
    updateCachedConversationPreview: jest.fn().mockResolvedValue(undefined),
    syncConversation: jest.fn().mockResolvedValue({ messages: [], presence: { typing: [] } }),
    markConversationSeen: jest.fn().mockResolvedValue(undefined),
    drainMessengerQueue: jest.fn().mockResolvedValue(undefined),
  };
});

import { ChatScreen } from "../ChatScreen";
import { PULSE_AI_CONVERSATION_ID } from "../../api/messenger";
import { PulseApiError } from "../../api/pulseApi";

/** `APPROVAL_STATE_MESSAGE["expired"]`, verbatim. The state a lapsed approval reaches. */
const EXPIRED_SENTENCE =
  "That confirmation ran out of time before it was used, so nothing changed. Ask again and confirm the new one.";
/** `APPROVAL_STATE_MESSAGE["consumed"]`. The one that must not read as "nothing happened". */
const CONSUMED_SENTENCE =
  "That confirmation was already used, so what it authorised has already been attempted. " +
  "Check where things stand before confirming it again.";
/** Minted by `pulseApi` itself, in the catch around `fetch`. No server answered. */
const UNREACHABLE_SENTENCE = "PulseSoc could not be reached. Check your connection and try again.";

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};

function renderChat() {
  return render(
    <SafeAreaProvider initialMetrics={METRICS}>
      <ChatScreen
        route={{ key: "c", name: "Chat", params: { conversationId: PULSE_AI_CONVERSATION_ID, title: "UNDX" } } as never}
        navigation={{ setOptions: jest.fn(), navigate: jest.fn(), addListener: jest.fn(() => jest.fn()) } as never}
      />
    </SafeAreaProvider>,
  );
}

/** Type a message, send it, and wait for the confirmation card(s) the server answers with. */
async function askForAConfirmation(components = [confirmationComponent]) {
  mockSend.mockResolvedValue({
    conversation: { id: PULSE_AI_CONVERSATION_ID, title: "UNDX" },
    messages: [],
    response_components: components,
  });
  renderChat();
  fireEvent.changeText(await screen.findByPlaceholderText("Message UNDX…"), "can you pause my btc alert");
  await act(async () => {
    fireEvent.press(screen.getByLabelText("Send message"));
  });
  await screen.findAllByText("Confirm");
}

/** The sentence the card itself is drawing, or null when it is drawing none. */
function cardOutcome(): string | null {
  const node = screen.queryByLabelText("UNDX action outcome");
  return node ? String(node.props.children) : null;
}

async function pressConfirm(index = 0) {
  await act(async () => {
    fireEvent.press(screen.getAllByLabelText("Confirm UNDX action")[index]);
  });
}

/**
 * The keyboard handlers `ChatScreen` registers, captured so a test can raise the keyboard.
 *
 * Nothing in jest ever opens one, so `keyboardVisible` is permanently false and any
 * assertion made without this is made in the one state the defect does not occur in.
 * The banner the sentence used to live in is drawn `&& !keyboardVisible`, so a suite
 * that cannot raise the keyboard would pass just as happily with the card's sentence
 * gated the same way — which is the defect, unmoved.
 */
const keyboardHandlers: Record<string, () => void> = {};

beforeEach(() => {
  jest.clearAllMocks();
  for (const key of Object.keys(keyboardHandlers)) delete keyboardHandlers[key];
  jest.spyOn(Keyboard, "addListener").mockImplementation(((event: string, handler: () => void) => {
    keyboardHandlers[event] = handler;
    return { remove: () => undefined };
  }) as never);
});

afterEach(() => {
  jest.restoreAllMocks();
});

/** Raise the keyboard, as tapping into the composer does on a device. */
async function raiseKeyboard() {
  await act(async () => {
    keyboardHandlers.keyboardWillShow?.();
  });
}

describe("a refused press is answered on the card it was made on", () => {
  it("draws the sentence with the keyboard up, which is when the tap happens", async () => {
    // The whole defect in one assertion. A card is summoned by typing, so the keyboard
    // is up at the moment Confirm is pressed, and the status banner — the only place
    // the sentence used to go — is rendered `&& !keyboardVisible`. With the keyboard
    // raised the banner is gone, so what this reads can only be the card's own text.
    mockConfirm.mockRejectedValue(new PulseApiError(EXPIRED_SENTENCE, 409, "confirmation_invalid"));
    await askForAConfirmation();
    await raiseKeyboard();
    await pressConfirm();

    expect(cardOutcome()).toBe(EXPIRED_SENTENCE);
    // And the banner really is absent, so the line above is not quietly reading it.
    expect(screen.queryAllByText(EXPIRED_SENTENCE)).toHaveLength(1);
  });

  it("draws the expired sentence where the person is looking", async () => {
    mockConfirm.mockRejectedValue(new PulseApiError(EXPIRED_SENTENCE, 409, "confirmation_invalid"));
    await askForAConfirmation();
    await pressConfirm();

    // Read off the card's own element, by its label. The status banner happens to
    // carry the same string in this environment because nothing focuses the composer
    // under jest — asserting on the text alone would pass even if the card drew
    // nothing, which is the whole defect.
    expect(cardOutcome()).toBe(EXPIRED_SENTENCE);
  });

  it("does not tell someone whose write already ran that nothing changed", async () => {
    // The one state of the six where "ask again" is the wrong advice: repeating the
    // press repeats a write. The sentence must survive the client unedited.
    mockConfirm.mockRejectedValue(new PulseApiError(CONSUMED_SENTENCE, 409, "confirmation_invalid"));
    await askForAConfirmation();
    await pressConfirm();

    expect(cardOutcome()).toBe(CONSUMED_SENTENCE);
    // "already been attempted", never "nothing changed" — the distinction the whole
    // server-side batch exists to make, asserted at the last layer that could lose it.
    expect(cardOutcome()).toContain("already been attempted");
    expect(String(cardOutcome()).toLowerCase()).not.toContain("nothing changed");
  });

  it("answers the card that was pressed, and not the one beside it", async () => {
    // Found by a surviving mutation. Dropping the token match from the outcome lookup
    // changed nothing at all while only one card was ever on screen, which meant the
    // match — the thing that makes this an answer rather than a notice — was not being
    // tested by anything. Two cards is the state that tells them apart.
    mockConfirm.mockRejectedValue(new PulseApiError(EXPIRED_SENTENCE, 409, "confirmation_invalid"));
    await askForAConfirmation([confirmationComponent, otherConfirmationComponent]);
    expect(screen.getAllByLabelText("Confirm UNDX action")).toHaveLength(2);

    await pressConfirm(0);

    // Exactly one card carries the sentence. Both carrying it would mean the person is
    // told the delete they never pressed also failed.
    expect(screen.getAllByLabelText("UNDX action outcome")).toHaveLength(1);
    // And it is the pressed one: that card lost its Confirm to the Dismiss branch, so
    // the single remaining Confirm belongs to the untouched card.
    expect(screen.getAllByLabelText("Confirm UNDX action")).toHaveLength(1);
    expect(screen.getAllByLabelText("Dismiss UNDX confirmation")).toHaveLength(1);
    expect(mockConfirm).toHaveBeenCalledWith(confirmationComponent.confirmation_token);
  });

  it("leaves a way out of a card the server called dead", async () => {
    // Confirm is disabled by the spent set and Cancel by the same flag, so without a
    // third control the card sits there permanently and cannot be cleared.
    mockConfirm.mockRejectedValue(new PulseApiError(EXPIRED_SENTENCE, 409, "confirmation_invalid"));
    await askForAConfirmation();
    await pressConfirm();

    // Queried rather than awaited: `pressConfirm` already flushed the state change
    // inside `act`, so there is nothing left to wait for, and a `findBy*` that is going
    // to fail spends its timeout first and then prints the entire component tree.
    const dismiss = screen.queryByLabelText("Dismiss UNDX confirmation");
    expect(dismiss).toBeTruthy();
    // The dead controls are gone rather than sitting there greyed: a button whose only
    // possible outcome is the same refusal teaches that Confirm sometimes does nothing.
    expect(screen.queryByLabelText("Confirm UNDX action")).toBeNull();
    expect(screen.queryByLabelText("Cancel UNDX action")).toBeNull();

    await act(async () => {
      fireEvent.press(dismiss as NonNullable<typeof dismiss>);
    });
    expect(screen.queryByLabelText("Dismiss UNDX confirmation")).toBeNull();
    expect(screen.queryByLabelText("UNDX action outcome")).toBeNull();
  });
});

describe("only a press that never reached an answering server may be pressed again", () => {
  it("keeps Confirm alive when the request did not complete", async () => {
    mockConfirm.mockRejectedValue(new PulseApiError(UNREACHABLE_SENTENCE, 503, "request_unreachable"));
    await askForAConfirmation();
    await pressConfirm();

    // The reason this is safe rather than merely kind: the server redeems a token
    // exactly once, so a second press produces the write or the sentence saying it
    // already ran. It cannot produce a second write.
    expect(cardOutcome()).toBe(UNREACHABLE_SENTENCE);
    expect(screen.getByLabelText("Confirm UNDX action")).toBeTruthy();
    expect(screen.queryByLabelText("Dismiss UNDX confirmation")).toBeNull();

    // And the press really goes through a second time rather than being dropped by the
    // spent-token guard, which is what made this a dead card before.
    mockConfirm.mockResolvedValue({ ok: true, message: "Done.", response_components: [] });
    await pressConfirm();
    expect(mockConfirm).toHaveBeenCalledTimes(2);
  });

  it("does not re-arm a press the server answered", async () => {
    mockConfirm.mockRejectedValue(new PulseApiError(EXPIRED_SENTENCE, 409, "confirmation_invalid"));
    await askForAConfirmation();
    await pressConfirm();
    expect(screen.queryByLabelText("Confirm UNDX action")).toBeNull();
    expect(mockConfirm).toHaveBeenCalledTimes(1);
  });
});

describe("a press that succeeds is unaffected", () => {
  it("replaces the card with what the server now says is true, and shows no outcome line", async () => {
    mockConfirm.mockResolvedValue({
      ok: true,
      message: "Done.",
      response_components: [
        {
          component: "verified_success_card",
          action_name: "Pause one crypto alert so it stops triggering",
          resource_label: "BTC alert · above · 999,999",
          verified: true,
        },
      ],
    });
    await askForAConfirmation();
    await pressConfirm();

    expect(await screen.findByText("VERIFIED RESULT")).toBeTruthy();
    expect(screen.queryByLabelText("UNDX action outcome")).toBeNull();
    expect(screen.queryByLabelText("Dismiss UNDX confirmation")).toBeNull();
  });
});
