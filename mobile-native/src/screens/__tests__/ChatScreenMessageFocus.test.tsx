/**
 * The long-press menu, asserted against the real conversation.
 *
 * `pulseCommand/__tests__/messageActions` proves which rules a message earns and
 * `focusedMessageLayout`'s tests prove where the three bands land. Both can be
 * right while the feature is absent or, worse, present and inert: the rules are
 * a list of *possible* actions, and the screen is the only place that knows
 * which of them it can actually carry out.
 *
 * That gap is what this file is for. A menu row that does nothing is worse than
 * a missing one -- the user cannot tell it apart from a failure -- so the tests
 * that matter most here are the ones asserting a row is *not* drawn.
 *
 * Mutation contract:
 *   - flipping any `false` in `MESSAGE_ACTION_IMPLEMENTED` to `true` must turn
 *     "never lists an action it cannot carry out" red;
 *   - dropping the `.filter(...MESSAGE_ACTION_IMPLEMENTED[rule.key])` must turn
 *     the same test red;
 *   - dropping `rule.available` from that filter must turn "an incoming message
 *     is not offered the author's powers" red;
 *   - passing `links: []` to `messageActionRules` must turn "routes a lone link
 *     straight through" red;
 *   - making the overlay open only from the measured anchor must turn every
 *     test in this file red, because `measureInWindow` never answers here --
 *     which is exactly the point of opening first and measuring second;
 *   - calling `translatePulseContent` from the menu instead of bumping
 *     `translateRequestId` must turn "translates through the bubble's own
 *     control" red, since the mocked router is the bubble's, not the menu's.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react-native";
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

/**
 * The two native capabilities the menu drives, mocked at the leaf rather than
 * at `src/native`'s barrel. Mocking the barrel would replace a dozen exports
 * the screen needs for unrelated reasons; mocking the owner of each capability
 * leaves the rest real.
 */
const mockCopyToClipboard = jest.fn().mockResolvedValue({ ok: true });
const mockHaptic = jest.fn().mockResolvedValue(undefined);
jest.mock("../../native/clipboard", () => ({
  copyToClipboard: (...args: unknown[]) => mockCopyToClipboard(...args)
}));
jest.mock("../../native/haptics", () => ({
  haptic: (...args: unknown[]) => mockHaptic(...args),
  hapticsEnabled: () => true,
  setHapticsEnabled: jest.fn()
}));

const mockOpenSystemShare = jest.fn().mockResolvedValue({ ok: true });
jest.mock("../../sharing/nativeShare", () => {
  const actual = jest.requireActual("../../sharing/nativeShare");
  return { ...actual, openSystemShare: (...args: unknown[]) => mockOpenSystemShare(...args) };
});

/**
 * The translation router, not the hook. Translate is supposed to run the
 * bubble's own control rather than a second path of the menu's own, so the
 * thing that proves it is a request arriving from the component under a real
 * `useContentTranslation`.
 */
const mockTranslateText = jest.fn();
jest.mock("../../services/translation/router", () => ({
  translateText: (...args: unknown[]) => mockTranslateText(...args),
  cancelTranslationRequests: jest.fn()
}));
jest.mock("../../api/translation", () => ({
  peekTranslationPreference: jest.fn(),
  subscribeTranslationPreference: jest.fn(() => () => undefined),
  updateTranslationPreference: jest.fn()
}));

const mockOpenURL = jest.fn().mockResolvedValue(true);
jest.mock("react-native/Libraries/Linking/Linking", () => ({
  default: {
    openURL: (url: string) => mockOpenURL(url),
    canOpenURL: jest.fn().mockResolvedValue(true),
    addEventListener: jest.fn(() => ({ remove: jest.fn() })),
    getInitialURL: jest.fn().mockResolvedValue(null)
  }
}));

const mockGetConversation = jest.fn();
const mockReactToMessage = jest.fn().mockResolvedValue({ ok: true, reactions: {} });
const mockDeleteMessage = jest.fn().mockResolvedValue({ ok: true });
const mockReportMessage = jest.fn().mockResolvedValue({ ok: true });

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
    markConversationRead: jest.fn().mockResolvedValue({ ok: true }),
    reactToMessage: (...args: unknown[]) => mockReactToMessage(...args),
    deleteMessage: (...args: unknown[]) => mockDeleteMessage(...args),
    reportMessage: (...args: unknown[]) => mockReportMessage(...args)
  };
});

import { ChatScreen } from "../ChatScreen";
import { activateLocale } from "../../i18n/engine";

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 }
};

const CONVERSATION_ID = 44;

function makeNavigation() {
  return {
    canGoBack: jest.fn(() => true),
    goBack: jest.fn(),
    navigate: jest.fn(),
    replace: jest.fn(),
    setOptions: jest.fn(),
    getState: () => ({ routes: [] }),
    addListener: jest.fn(() => jest.fn())
  };
}

function minutesAgo(minutes: number) {
  return new Date(Date.now() - minutes * 60_000).toISOString();
}

function message(overrides: Record<string, unknown>) {
  return {
    id: 900,
    message_id: 900,
    conversation_id: CONVERSATION_ID,
    body: "",
    message_type: "text",
    is_mine: false,
    sender_display_name: "ROODY CHERIE",
    delivery_status: "sent",
    created_at: minutesAgo(1),
    ...overrides
  };
}

async function renderChat(messages: Record<string, unknown>[], conversation: Record<string, unknown> = {}) {
  mockGetConversation.mockResolvedValue({
    conversation: { id: CONVERSATION_ID, title: "ROODY CHERIE", ...conversation },
    messages,
    presence: { typing: [] }
  });
  const navigation = makeNavigation();
  render(
    <SafeAreaProvider initialMetrics={METRICS}>
      <ChatScreen
        route={{ key: "c", name: "Chat", params: { conversationId: CONVERSATION_ID, title: "ROODY CHERIE" } } as never}
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
 * Long-press the bubble carrying this text.
 *
 * The press goes to the bubble's `Pressable`, which is the node whose
 * `onLongPress` the screen owns -- `getByText` returns the inner `Text`, and
 * firing at that depth would rely on responder bubbling that RNTL does not
 * simulate.
 */
async function longPressMessage(body: string) {
  const label = screen.getByText(body);
  await act(async () => {
    fireEvent(label, "longPress");
  });
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  mockCopyToClipboard.mockResolvedValue({ ok: true });
  mockHaptic.mockResolvedValue(undefined);
  mockOpenSystemShare.mockResolvedValue({ ok: true });
  mockReactToMessage.mockResolvedValue({ ok: true, reactions: {} });
  mockDeleteMessage.mockResolvedValue({ ok: true });
});

describe("what a long press offers", () => {
  it("never lists an action it cannot carry out", async () => {
    // Forward, Save and Message Info all pass `messageActionRules` for this
    // message: it is server-accepted, not deleted, not mine. They are absent
    // because this screen has nowhere to send the tap -- no conversation
    // picker, no saved-items type for a message, no delivery-details screen.
    // Listing them would produce three rows that swallow a press in silence.
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");

    expect(screen.getByTestId("message-action-reply")).toBeTruthy();
    expect(screen.queryByTestId("message-action-forward")).toBeNull();
    expect(screen.queryByTestId("message-action-save")).toBeNull();
    expect(screen.queryByTestId("message-action-info")).toBeNull();
  });

  it("an incoming message is not offered the author's powers", async () => {
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");

    // Edit and Delete-for-everyone are the server's rules mirrored: comm_v2
    // refuses both to anyone but the sender, so a row here would be a button
    // that returns 403 every time.
    expect(screen.queryByTestId("message-action-edit")).toBeNull();
    expect(screen.queryByTestId("message-action-deleteEveryone")).toBeNull();
    // And the things only a recipient needs.
    expect(screen.getByTestId("message-action-report")).toBeTruthy();
    expect(screen.getByTestId("message-action-safety")).toBeTruthy();
    expect(screen.getByTestId("message-action-deleteSelf")).toBeTruthy();
  });

  it("the author gets Delete for everyone inside the window and not outside it", async () => {
    await renderChat([
      message({ id: 901, message_id: 901, body: "Just sent this", is_mine: true, created_at: minutesAgo(2) }),
      message({ id: 902, message_id: 902, body: "Sent this yesterday", is_mine: true, created_at: minutesAgo(60 * 26) })
    ]);

    await longPressMessage("Just sent this");
    expect(screen.getByTestId("message-action-deleteEveryone")).toBeTruthy();
    // Report and Mute/Block are about someone else. Reporting yourself to
    // Trust and Safety is not a thing a menu should suggest.
    expect(screen.queryByTestId("message-action-report")).toBeNull();
    expect(screen.queryByTestId("message-action-safety")).toBeNull();

    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-deleteSelf"));
    });

    await longPressMessage("Sent this yesterday");
    expect(screen.queryByTestId("message-action-deleteEveryone")).toBeNull();
    // Delete for me never expires: it is answerable on this device alone.
    expect(screen.getByTestId("message-action-deleteSelf")).toBeTruthy();
  });

  it("offers the link actions only when the message carries a link", async () => {
    await renderChat([
      message({ id: 903, message_id: 903, body: "Read this: https://apple.com/newsroom" }),
      message({ id: 904, message_id: 904, body: "No URL in this one at all" })
    ]);

    await longPressMessage("No URL in this one at all");
    expect(screen.queryByTestId("message-action-openLink")).toBeNull();
    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-reply"));
    });

    await longPressMessage("Read this: https://apple.com/newsroom");
    expect(screen.getByTestId("message-action-openLink")).toBeTruthy();
    expect(screen.getByTestId("message-action-copyLink")).toBeTruthy();
    expect(screen.getByTestId("message-action-shareLink")).toBeTruthy();
  });
});

describe("what a long press does", () => {
  it("taps back before it measures anything", async () => {
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");
    // The acknowledgement that the press registered. `measureInWindow` never
    // answers under a test renderer, so a haptic fired from inside its callback
    // would be missing here -- and on a device it would arrive late.
    expect(mockHaptic).toHaveBeenCalledWith("selection");
  });

  it("copies the body and closes", async () => {
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");

    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-copy"));
    });

    expect(mockCopyToClipboard).toHaveBeenCalledWith("Morning from Port-au-Prince", "text");
    expect(screen.queryByTestId("message-action-copy")).toBeNull();
  });

  it("routes a lone link straight through without asking which one", async () => {
    await renderChat([message({ body: "Read this: https://apple.com/newsroom" })]);
    await longPressMessage("Read this: https://apple.com/newsroom");

    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-openLink"));
    });

    expect(mockOpenURL).toHaveBeenCalledWith("https://apple.com/newsroom");
    expect(screen.queryByText("Which link?")).toBeNull();
  });

  it("asks which link when the message carries several, and honours the answer", async () => {
    await renderChat([message({ body: "https://apple.com/one and https://apple.com/two" })]);
    await longPressMessage("https://apple.com/one and https://apple.com/two");

    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-openLink"));
    });

    // Nothing opened yet -- the action was chosen, the subject was not.
    expect(mockOpenURL).not.toHaveBeenCalled();
    expect(screen.getByText("Which link?")).toBeTruthy();

    await act(async () => {
      // By label, not by text: the URL also appears in the bubble behind the
      // chooser, and only the chooser rows label themselves with it.
      fireEvent.press(screen.getByLabelText("https://apple.com/two"));
    });
    expect(mockOpenURL).toHaveBeenCalledWith("https://apple.com/two");
    expect(mockOpenURL).toHaveBeenCalledTimes(1);
  });

  it("carries the chosen action through to the chooser rather than defaulting to Open", async () => {
    // Copy Link and Open Link share the chooser. If the sheet re-derived the
    // action instead of running the handler it was given, picking a link would
    // open it -- which is a different, irreversible thing from copying it.
    await renderChat([message({ body: "https://apple.com/one and https://apple.com/two" })]);
    await longPressMessage("https://apple.com/one and https://apple.com/two");

    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-copyLink"));
    });
    await act(async () => {
      fireEvent.press(screen.getByLabelText("https://apple.com/one"));
    });

    expect(mockCopyToClipboard).toHaveBeenCalledWith("https://apple.com/one", "link");
    expect(mockOpenURL).not.toHaveBeenCalled();
  });

  it("dismisses on a tap outside without doing anything", async () => {
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");

    await act(async () => {
      fireEvent.press(screen.getByLabelText("Close message menu"));
    });

    expect(screen.queryByTestId("message-action-reply")).toBeNull();
    expect(mockCopyToClipboard).not.toHaveBeenCalled();
    expect(mockDeleteMessage).not.toHaveBeenCalled();
  });

  it("sends a reaction from the strip above the message", async () => {
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");

    await act(async () => {
      fireEvent.press(screen.getByLabelText("React with ❤️"));
    });

    expect(mockReactToMessage).toHaveBeenCalledWith(900, "❤️");
    expect(screen.queryByTestId("message-action-reply")).toBeNull();
  });

  it("translates through the bubble's own control, not a second path", async () => {
    mockTranslateText.mockResolvedValue({
      ok: true,
      requestId: "chat:905#1",
      contentId: "chat:905",
      provider: "apple_on_device",
      translatedText: "Good morning",
      sourceLanguage: "es",
      targetLanguage: "en-us",
      cached: false,
      downloadPrepared: false,
      durationMs: 4
    });

    await renderChat([message({ id: 905, message_id: 905, body: "Buenos días a todos", source_language: "es" })]);
    await longPressMessage("Buenos días a todos");

    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-translate"));
    });

    // The router is the one `ContentTranslation` calls. A menu that translated
    // by itself would leave this untouched and put the result somewhere else.
    await waitFor(() => expect(screen.getByText("Good morning")).toBeTruthy());
    expect(mockTranslateText).toHaveBeenCalledTimes(1);
    expect(mockTranslateText.mock.calls[0][0]).toMatchObject({ userInitiated: true });
  });
});
