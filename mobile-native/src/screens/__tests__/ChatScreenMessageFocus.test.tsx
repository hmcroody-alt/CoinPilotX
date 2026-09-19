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
 * a missing one -- the user cannot tell it apart from a failure -- so the two
 * kinds of test here pull in opposite directions and both matter: the first
 * describe asserts that a row is *not* drawn when this screen cannot act on it,
 * and the second asserts what each drawn row actually does.
 *
 * Mutation contract:
 *   - flipping `save` or `saveMedia` in `MESSAGE_ACTION_IMPLEMENTED` to `true`
 *     must turn "never lists an action it cannot carry out" red;
 *   - flipping `forward` or `info` back to `false` must turn the same test red
 *     from the other side;
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
const mockEditMessage = jest.fn();
const mockForwardMessage = jest.fn();
const mockListConversations = jest.fn().mockResolvedValue([]);
const mockLoadCachedConversations = jest.fn().mockResolvedValue([]);

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
    reportMessage: (...args: unknown[]) => mockReportMessage(...args),
    editMessage: (...args: unknown[]) => mockEditMessage(...args),
    forwardMessage: (...args: unknown[]) => mockForwardMessage(...args),
    listConversations: (...args: unknown[]) => mockListConversations(...args),
    loadCachedConversations: (...args: unknown[]) => mockLoadCachedConversations(...args)
  };
});

/**
 * The gallery's own page fetch, which is how View proves it opened.
 *
 * Asserting on the viewer's rendered output would test the viewer; asserting
 * that the host was handed this message's attachment id tests the only thing
 * the menu row is responsible for. The page comes back empty on purpose -- the
 * seed alone is a valid gallery, and that is the state a menu-opened viewer is
 * in for the first round trip anyway.
 */
const mockFetchConversationMedia = jest.fn().mockResolvedValue({
  items: [], total: 0, hasOlder: false, hasNewer: false, oldestId: 0, newestId: 0
});
jest.mock("../../api/conversationMedia", () => ({
  fetchConversationMedia: (...args: unknown[]) => mockFetchConversationMedia(...args)
}));

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
    // Save passes `messageActionRules` for this message -- it is
    // server-accepted, not deleted, not mine -- and is absent anyway, because
    // `SavableContentType` has no member for a message and so there is nowhere
    // for the tap to go. A row that swallows a press in silence is worse than
    // no row: the user cannot tell it apart from a failure.
    //
    // Forward and Message Info were in that sentence and are no longer. They
    // are asserted *present* here rather than quietly dropped from the test,
    // so deleting either sheet turns this red instead of turning it back into
    // the comment it used to be.
    await renderChat([message({ body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");

    expect(screen.getByTestId("message-action-reply")).toBeTruthy();
    expect(screen.getByTestId("message-action-forward")).toBeTruthy();
    expect(screen.getByTestId("message-action-info")).toBeTruthy();
    expect(screen.queryByTestId("message-action-save")).toBeNull();
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

/**
 * The four rows that used to be absent.
 *
 * Each is asserted on the effect it is supposed to have, never on the presence
 * of the row -- a row is what the previous describe proves, and a row that
 * draws and does nothing is the exact failure this whole file exists to catch.
 *
 * Mutation contract:
 *   - dropping `beginEdit`'s `setDraft` must turn "hands the composer over" red;
 *   - making `submitEdit` paint the new body before the server answers must
 *     turn "does not show an edit the server refused" red;
 *   - reporting `conversationIds.length` instead of the server's `count` must
 *     turn "reports what the server did" red;
 *   - seeding the gallery from anything but this message must turn "opens the
 *     viewer on the message that was pressed" red.
 */
describe("the actions that used to be listed and inert", () => {
  it("hands the composer over to the message being edited, and gives the draft back", async () => {
    await renderChat([message({ id: 910, message_id: 910, body: "Meet at seven", is_mine: true, created_at: minutesAgo(2) })]);

    const composer = screen.getByLabelText("Message composer");
    await act(async () => {
      fireEvent.changeText(composer, "half a sentence");
    });

    await longPressMessage("Meet at seven");
    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-edit"));
    });

    // The stored body, not the displayed one. They agree for text and they
    // agree by coincidence, so the test pins the one an edit has to start from.
    expect(screen.getByLabelText("Message composer").props.value).toBe("Meet at seven");
    expect(screen.getByText("Editing message")).toBeTruthy();

    // The half-sentence was displaced by a menu row, so cancelling has to put
    // it back. Losing it silently is the whole reason `restoreDraft` exists.
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Cancel edit"));
    });
    expect(screen.getByLabelText("Message composer").props.value).toBe("half a sentence");
  });

  it("does not show an edit the server refused", async () => {
    mockEditMessage.mockRejectedValue(new Error("Edit window closed."));
    await renderChat([message({ id: 911, message_id: 911, body: "Meet at seven", is_mine: true, created_at: minutesAgo(2) })]);

    await longPressMessage("Meet at seven");
    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-edit"));
    });
    await act(async () => {
      fireEvent.changeText(screen.getByLabelText("Message composer"), "Meet at eight");
    });
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Save edit"));
    });

    // An optimistic edit would show "Meet at eight" for a round trip and then
    // take it back -- and the case where it lies is the case the user most
    // needs told.
    await waitFor(() => expect(screen.getByText("Edit window closed.")).toBeTruthy());
    // Twice over: the bubble, and the banner preview of what is being amended.
    // Both are the original, which is the point.
    expect(screen.getAllByText("Meet at seven").length).toBeGreaterThan(0);
    expect(screen.queryByText("Meet at eight")).toBeNull();
  });

  it("reports what the server forwarded, not what was selected", async () => {
    const threads = [
      { id: 51, conversation_id: 51, title: "Maria Cherie", conversation_domain: "SOCIAL" },
      { id: 52, conversation_id: 52, title: "Studio crew", conversation_domain: "SOCIAL" },
      // The thread we are standing in. Forwarding a message into the thread it
      // is already in puts a copy of it directly beneath itself.
      { id: CONVERSATION_ID, conversation_id: CONVERSATION_ID, title: "ROODY CHERIE", conversation_domain: "SOCIAL" }
    ];
    // Both legs, and deliberately the same list. The refresh overwrites the
    // cached rows whenever it lands, so leaving it at the default empty array
    // would test a server that had just deleted every conversation.
    mockLoadCachedConversations.mockResolvedValue(threads);
    mockListConversations.mockResolvedValue(threads);
    // One of the two went away between the cache write and the send.
    mockForwardMessage.mockResolvedValue({ ok: true, count: 1, forwarded_message_ids: [7781] });

    await renderChat([message({ id: 912, message_id: 912, body: "Morning from Port-au-Prince" })]);
    await longPressMessage("Morning from Port-au-Prince");
    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-forward"));
    });

    await waitFor(() => expect(screen.getByLabelText("Maria Cherie")).toBeTruthy());
    expect(screen.queryByLabelText("ROODY CHERIE")).toBeNull();

    // TWO destinations chosen, ONE accepted. The numbers have to differ or the
    // test cannot tell "the server's count" from "the length of the selection"
    // -- which is the entire distinction it exists to pin.
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Maria Cherie"));
    });
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Studio crew"));
    });
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Forward"));
    });

    expect(mockForwardMessage).toHaveBeenCalledWith(912, [51, 52]);
    await waitFor(() => expect(screen.getByText("Forwarded to 1.")).toBeTruthy());
    expect(screen.queryByText("Forwarded to 2.")).toBeNull();
  });

  it("tells the truth about read state in a group", async () => {
    await renderChat(
      [message({ id: 913, message_id: 913, body: "Morning from Port-au-Prince", is_mine: true, delivery_status: "seen", created_at: minutesAgo(3) })],
      { is_group: true, member_count: 6 }
    );

    await longPressMessage("Morning from Port-au-Prince");
    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-info"));
    });

    // "Read" beside six participants reads as "all six" and means "at least
    // one" -- the server aggregates to one status for the whole message and
    // has no per-person breakdown to offer.
    await waitFor(() => expect(screen.getByText("Read by at least one person")).toBeTruthy());
    // Facts that do not apply are absent rather than blank. A blank row invites
    // the reading that the fact is missing.
    expect(screen.queryByText("Edited")).toBeNull();
    expect(screen.queryByText("Duration")).toBeNull();
  });

  it("opens the viewer on the message that was pressed", async () => {
    await renderChat([
      message({
        id: 914,
        message_id: 914,
        message_type: "image",
        // A caption, so the long press has a text node to start from. Media
        // with a caption is also the case `messageActionKind` was widened for,
        // so this is the harder of the two shapes rather than the easier one.
        body: "Sunset over the bay",
        media_url: "/api/messenger/media/331",
        media_upload_id: 331,
        attachment_id: 5501
      })
    ]);

    await longPressMessage("Sunset over the bay");
    await act(async () => {
      fireEvent.press(screen.getByTestId("message-action-viewMedia"));
    });

    // The host paged outward from THIS attachment. Seeding from anything else
    // would open a gallery on somebody else's photo.
    await waitFor(() => expect(mockFetchConversationMedia).toHaveBeenCalled());
    const seededIds = mockFetchConversationMedia.mock.calls.map((call) => call[1]);
    expect(seededIds).toContainEqual(expect.objectContaining({ beforeId: 5501 }));
    expect(seededIds).toContainEqual(expect.objectContaining({ afterId: 5501 }));
  });
});
