/**
 * Where the `‹` in a conversation header actually goes.
 *
 * Reported as "when click the back button on the top left from the chat, it
 * brings to the dashboard. that's not normal", and reproduced on the simulator:
 * cold-launch `pulsesoc://pulse/messages/6`, press `‹`, land on Mission Control.
 *
 * The rule itself lives in `goBackFromChat` and is proven against a real
 * navigator in `undx/__tests__/undxNavigationReturn.test.tsx`. This file exists
 * because that one structurally cannot see the defect: the rule was always
 * capable of answering correctly, and the bug was that ChatScreen never told it
 * which conversation it was rendering. So what is pinned here is the *call*, in
 * the real screen, with a navigation object that answers `canGoBack()` the way
 * a cold-started stack does.
 *
 * Mutation contract:
 *   - ChatScreen passing no `conversationId` (or a hardcoded one) must turn
 *     this file red;
 *   - restoring the dashboard as the floor for a human conversation must turn
 *     this file red;
 *   - dropping the `canGoBack()` tier must turn this file red.
 */

import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn(), Recording: class {}, Sound: class {} },
  ResizeMode: { CONTAIN: "contain", COVER: "cover", STRETCH: "stretch" },
  Video: jest.requireActual("react").forwardRef(() => null)
}));
jest.mock("expo-file-system", () => ({ File: class {} }));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({
  launchImageLibraryAsync: jest.fn(),
  requestMediaLibraryPermissionsAsync: jest.fn()
}));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));

jest.mock("../../api/pulseApi", () => {
  const actual = jest.requireActual("../../api/pulseApi");
  return { ...actual, pulseApi: () => Promise.resolve({ ok: true }) };
});

const mockGetConversation = jest.fn();

jest.mock("../../api/messenger", () => {
  const actual = jest.requireActual("../../api/messenger");
  return {
    ...actual,
    getConversation: (...args: unknown[]) => mockGetConversation(...args),
    loadCachedMessages: jest.fn().mockResolvedValue([]),
    cacheMessages: jest.fn().mockResolvedValue(undefined),
    updateCachedConversationPreview: jest.fn().mockResolvedValue(undefined),
    syncConversation: jest.fn().mockResolvedValue({ messages: [], presence: { typing: [] } }),
    markConversationSeen: jest.fn().mockResolvedValue(undefined),
    drainMessengerQueue: jest.fn().mockResolvedValue([]),
    sendMessage: jest.fn(),
    markConversationRead: jest.fn().mockResolvedValue({ ok: true })
  };
});

import { ChatScreen } from "../ChatScreen";
import { PULSE_AI_CONVERSATION_ID } from "../../api/messenger";
import { activateLocale } from "../../i18n/engine";

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 }
};

/** ROODY CHERIE, the thread the report was made against. */
const HUMAN_CONVERSATION_ID = 6;

type Nav = {
  canGoBack: jest.Mock;
  goBack: jest.Mock;
  navigate: jest.Mock;
  setOptions: jest.Mock;
  getState: () => { routes: never[] };
  addListener: jest.Mock;
};

function makeNavigation(canGoBack: boolean): Nav {
  return {
    canGoBack: jest.fn(() => canGoBack),
    goBack: jest.fn(),
    navigate: jest.fn(),
    setOptions: jest.fn(),
    getState: () => ({ routes: [] }),
    addListener: jest.fn(() => jest.fn())
  };
}

async function renderChat(options: { conversationId: number; canGoBack: boolean }) {
  mockGetConversation.mockResolvedValue({
    conversation: { id: options.conversationId, title: "ROODY CHERIE" },
    messages: [],
    presence: { typing: [] }
  });
  const navigation = makeNavigation(options.canGoBack);
  render(
    <SafeAreaProvider initialMetrics={METRICS}>
      <ChatScreen
        route={
          {
            key: "c",
            name: "Chat",
            params: { conversationId: options.conversationId, title: "ROODY CHERIE" }
          } as never
        }
        navigation={navigation as never}
      />
    </SafeAreaProvider>
  );
  await act(async () => {
    await Promise.resolve();
  });
  return navigation;
}

/**
 * The header's own `‹`, found by the promise it makes to a screen reader.
 *
 * Matching on the label rather than the glyph is deliberate: the label is the
 * thing that was already contradicting the behaviour — it has said "Back to
 * conversations" the whole time the button was going to the dashboard.
 */
function pressBack() {
  fireEvent.press(screen.getByLabelText("Back to conversations"));
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe("back from a conversation with no stack beneath it", () => {
  it("goes to the conversations list, not the dashboard", async () => {
    // The device repro: a notification tap or a deep link lands on Chat as the
    // only entry, so `canGoBack()` is false and the floor is the whole answer.
    const navigation = await renderChat({
      conversationId: HUMAN_CONVERSATION_ID,
      canGoBack: false
    });

    await act(async () => {
      pressBack();
    });

    expect(navigation.navigate).toHaveBeenCalledWith("Tabs", { screen: "Messenger" });
    // Stated as its own assertion because "went to Messenger" and "did not go
    // to the dashboard" are not the same claim, and the defect is the second
    // one. A rule that navigated twice would pass the first and fail this.
    expect(navigation.navigate).not.toHaveBeenCalledWith("Tabs", { screen: "Dashboard" });
    expect(navigation.navigate).toHaveBeenCalledTimes(1);
  });

  it("still sends UNDX to the dashboard", async () => {
    // The floor that was right all along, and the reason the fix could not just
    // be "change Dashboard to Messenger". The PulseAI tab replaces itself with
    // Chat, so there is no conversation list standing behind UNDX to return to.
    const navigation = await renderChat({
      conversationId: PULSE_AI_CONVERSATION_ID,
      canGoBack: false
    });

    await act(async () => {
      pressBack();
    });

    expect(navigation.navigate).toHaveBeenCalledWith("Tabs", { screen: "Dashboard" });
    expect(navigation.navigate).not.toHaveBeenCalledWith("Tabs", { screen: "Messenger" });
  });
});

describe("back from a conversation opened on top of something", () => {
  it("pops the stack and navigates nowhere", async () => {
    // The ordinary path — tapping a row in the messages list. It was never
    // broken, and it is pinned because the fix edited the function that serves
    // it: a live entry underneath is the only answer that knows where the
    // member actually came from, so it must outrank the floor.
    const navigation = await renderChat({
      conversationId: HUMAN_CONVERSATION_ID,
      canGoBack: true
    });

    await act(async () => {
      pressBack();
    });

    expect(navigation.goBack).toHaveBeenCalledTimes(1);
    // A floor that also fired would re-navigate on top of the pop, which on a
    // real navigator means the member watches the screen they came from appear
    // and then get replaced.
    expect(navigation.navigate).not.toHaveBeenCalled();
  });
});
