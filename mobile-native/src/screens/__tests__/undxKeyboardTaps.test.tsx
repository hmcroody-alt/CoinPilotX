/**
 * The first press of a card control, made with the keyboard up, must reach the control.
 *
 * Found on the iPhone 17 Pro Max simulator while demonstrating Batch 21, not by any test
 * here. An approval was left to lapse and Confirm was pressed twice with the software
 * keyboard raised. Both presses closed the keyboard and did nothing else: no request left
 * the device, the card did not change, and both controls stayed live. From the outside it
 * is indistinguishable from a button that is not wired up.
 *
 * The cause is a default. `ScrollView` and `FlatList` take `keyboardShouldPersistTaps`
 * `"never"` unless told otherwise, and under `"never"` the first touch anywhere outside
 * the focused input is consumed to dismiss the keyboard and is never delivered to the
 * child beneath it. A person reaches an UNDX card by typing, so the keyboard is always up
 * when the card arrives — the swallow is not an edge case here, it is every first press.
 *
 * ## Why these are contract assertions rather than presses
 *
 * The swallow lives in the native responder system. `fireEvent.press` dispatches straight
 * at the element's handler and never consults a scroll container, so a test that presses
 * Confirm passes identically with the prop set, unset, or set to `"never"` — it would be a
 * test that cannot fail, on the one property the whole batch is about.
 *
 * So what is asserted is the contract with the platform: the value React Native is given.
 * That is the entire fix, and it is the entire thing that was wrong. The behaviour itself
 * was verified the only way it can be, by a finger on a simulator, and that run is
 * recorded in `reports/batch22_keyboard_taps.md`.
 */

import React from "react";
import { FlatList, ScrollView } from "react-native";
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

jest.mock("expo-av", () => ({ Audio: { setAudioModeAsync: jest.fn(), Recording: class {}, Sound: class {} } }));
jest.mock("expo-file-system", () => ({ File: class {} }));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({ launchImageLibraryAsync: jest.fn(), requestMediaLibraryPermissionsAsync: jest.fn() }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));

const confirmationComponent = {
  component: "action_confirmation",
  capability_id: "crypto.alerts.pause",
  status: "confirmation_required",
  action_name: "Pause one crypto alert so it stops triggering",
  resource_label: "BTC alert · above · 999,999",
  current_value: "active",
  proposed_value: "paused",
  confirmation_id: "undx_confirm_22",
  confirmation_token: "tok-batch22",
  expires_at: "2026-07-30T23:59:00+00:00",
};

const mockSend = jest.fn();

jest.mock("../../api/messenger", () => {
  const actual = jest.requireActual("../../api/messenger");
  return {
    ...actual,
    confirmPulseAiAction: jest.fn(),
    cancelPulseAiAction: jest.fn(),
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

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 },
};

/**
 * The two values under which a touch is delivered to the child. Anything else — including
 * the default, which is `undefined` and means `"never"` — swallows the first press.
 */
const DELIVERS_THE_TOUCH = ["handled", "always"];

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

async function askForAConfirmation() {
  mockSend.mockResolvedValue({
    conversation: { id: PULSE_AI_CONVERSATION_ID, title: "UNDX" },
    messages: [],
    response_components: [confirmationComponent],
  });
  renderChat();
  fireEvent.changeText(await screen.findByPlaceholderText("Message UNDX…"), "can you pause my btc alert");
  await act(async () => {
    fireEvent.press(screen.getByLabelText("Send message"));
  });
  await screen.findAllByText("Confirm");
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("a card control can be pressed while the keyboard is up", () => {
  it("gives the action rail a value that delivers the touch", async () => {
    await askForAConfirmation();

    // By label rather than by type: the rail is the scrollable that holds the controls,
    // and it is the only one whose default cost a person a press of Confirm.
    const rail = screen.getByLabelText("UNDX action cards");
    expect(DELIVERS_THE_TOUCH).toContain(rail.props.keyboardShouldPersistTaps);
  });

  it("does not leave the rail on the default", async () => {
    // Stated separately and in the negative because the failure mode is an absence. A
    // prop nobody wrote reads as `undefined`, which is exactly what the defect looked
    // like, and `undefined` is easy to skim past in an assertion about allowed values.
    await askForAConfirmation();

    const rail = screen.getByLabelText("UNDX action cards");
    expect(rail.props.keyboardShouldPersistTaps).toBeDefined();
    expect(rail.props.keyboardShouldPersistTaps).not.toBe("never");
  });

  it("keeps the rail at handled, so an unclaimed tap still puts the keyboard away", async () => {
    // "always" would also deliver the touch, and would also stop the keyboard closing
    // when a person taps the empty part of the rail — which is the one gesture that
    // means "put it away". The looser value fixes the bug and creates a smaller one.
    await askForAConfirmation();

    expect(screen.getByLabelText("UNDX action cards").props.keyboardShouldPersistTaps).toBe("handled");
  });

  it("gives the message list a value that delivers the touch", async () => {
    // Same default, same consequence, different control: a message that failed to send
    // carries Retry, and a person retries it while still looking at the composer.
    await askForAConfirmation();

    const lists = screen.UNSAFE_getAllByType(FlatList);
    expect(lists.length).toBeGreaterThan(0);
    lists.forEach((list) => {
      expect(DELIVERS_THE_TOUCH).toContain(list.props.keyboardShouldPersistTaps);
    });
  });

  it("leaves no scrollable on this screen taking the default", async () => {
    // The two found were found by using the app, not by reading it. This is the reading
    // done exhaustively, so a third does not have to be found the same way.
    await askForAConfirmation();

    const scrollables = [...screen.UNSAFE_getAllByType(ScrollView), ...screen.UNSAFE_getAllByType(FlatList)];
    expect(scrollables.length).toBeGreaterThan(0);
    const onTheDefault = scrollables.filter((node) => !DELIVERS_THE_TOUCH.includes(node.props.keyboardShouldPersistTaps));
    expect(onTheDefault).toHaveLength(0);
  });
});
