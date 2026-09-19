/**
 * A link sent through Messenger, asserted against the real rendered screen.
 *
 * `links/__tests__` proves the segmenter, the classifier and the renderer in
 * isolation. All three could be correct and the feature still absent: the
 * bubble reaches the body through `ContentTranslation`, and before this change
 * it handed that component a `textStyle` and nothing else — so the body was one
 * flat `<Text>` and no amount of correctness in `LinkedText` would have been
 * reachable from a conversation.
 *
 * So this file mounts `ChatScreen` with real messages and presses the real
 * glyph run. `ContentTranslation` is deliberately **not** mocked: the wiring
 * under test *is* the `renderText` prop it receives, and a mock would be free
 * to honour a prop the real component ignores.
 *
 * The four bodies are the ones the brief named for the device check, so the
 * simulator/hardware pass and this file are testing the same strings.
 *
 * Mutation contract:
 *   - reverting the bubble to `textStyle={styles.body}` must turn this red;
 *   - making the whole bubble `Pressable`-to-open must turn this red;
 *   - routing every pulsesoc.com URL through `openNativeRoute` must turn the
 *     invite-link test red;
 *   - letting a `javascript:` body render as a link must turn this red.
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

/**
 * Every URL the screen asked the operating system to open, in order.
 *
 * The module is an ES module whose real export lives under `default` — a mock
 * that returns the methods at the top level type-checks, runs, and silently
 * leaves `Linking.openURL` undefined, so the external-link tests would report
 * "Number of calls: 0" while the screen was in fact working.
 */
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
import { activateLocale } from "../../i18n/engine";

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 }
};

const CONVERSATION_ID = 6;

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

/** One incoming text message per body, oldest first. */
function messagesFrom(bodies: string[]) {
  return bodies.map((body, index) => ({
    id: 900 + index,
    message_id: 900 + index,
    conversation_id: CONVERSATION_ID,
    body,
    message_type: "text",
    is_mine: false,
    sender_display_name: "ROODY CHERIE",
    delivery_status: "sent",
    created_at: "2026-09-19T10:0" + index + ":00Z"
  }));
}

async function renderChat(bodies: string[]) {
  mockGetConversation.mockResolvedValue({
    conversation: { id: CONVERSATION_ID, title: "ROODY CHERIE" },
    messages: messagesFrom(bodies),
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
 * The glyph run a node actually displays.
 *
 * `toHaveTextContent` is a jest-native matcher and this project does not install
 * it, so it is `undefined` at runtime and any assertion using it throws rather
 * than failing informatively.
 */
function textOf(node: { props?: { children?: unknown } }): string {
  const walk = (child: unknown): string => {
    if (child == null || child === false) return "";
    if (typeof child === "string" || typeof child === "number") return String(child);
    if (Array.isArray(child)) return child.map(walk).join("");
    const element = child as { props?: { children?: unknown } };
    return element.props ? walk(element.props.children) : "";
  };
  return walk(node.props?.children);
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
});

describe("a URL inside a message bubble", () => {
  it("renders as a link, not as prose", async () => {
    await renderChat(["https://pulsesoc.com/pulse/post/2432"]);
    const links = screen.queryAllByRole("link");
    expect(links).toHaveLength(1);
    expect(textOf(links[0])).toBe("https://pulsesoc.com/pulse/post/2432");
  });

  it("opens the post in the app when pressed", async () => {
    const navigation = await renderChat(["https://pulsesoc.com/pulse/post/2432"]);
    await act(async () => {
      fireEvent.press(screen.getByRole("link"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith("PostDetail", expect.objectContaining({ postId: 2432 }));
    expect(mockOpenURL).not.toHaveBeenCalled();
  });

  it("opens the bare site root on Home", async () => {
    const navigation = await renderChat(["https://pulsesoc.com/"]);
    await act(async () => {
      fireEvent.press(screen.getByRole("link"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith("Tabs", { screen: "Home" });
  });

  it("keeps the sentence around the link and links only the URL", async () => {
    const body = "Visit https://apple.com when you have time.";
    await renderChat([body]);
    // The bubble still reads as the exact string that was sent.
    expect(screen.getByText(body)).toBeTruthy();
    expect(screen.queryAllByRole("link")).toHaveLength(1);
  });

  it("hands an external link to the operating system, never to the navigator", async () => {
    const navigation = await renderChat(["Visit https://apple.com when you have time."]);
    await act(async () => {
      fireEvent.press(screen.getByRole("link"));
    });
    expect(mockOpenURL).toHaveBeenCalledWith("https://apple.com/");
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("links a URL that follows a line break", async () => {
    await renderChat(["Hey! Check this out:\nhttps://pulsesoc.com/"]);
    const links = screen.queryAllByRole("link");
    expect(links).toHaveLength(1);
    expect(textOf(links[0])).toBe("https://pulsesoc.com/");
  });

  it("links each URL separately when a message has several", async () => {
    const navigation = await renderChat([
      "one https://pulsesoc.com/pulse/reels/12 two https://apple.com/x"
    ]);
    const links = screen.queryAllByRole("link");
    expect(links).toHaveLength(2);
    await act(async () => {
      fireEvent.press(links[0]);
    });
    expect(navigation.navigate).toHaveBeenCalledWith("ReelDetail", expect.objectContaining({ reelId: 12 }));
    await act(async () => {
      fireEvent.press(links[1]);
    });
    expect(mockOpenURL).toHaveBeenCalledWith("https://apple.com/x");
  });

  it("sends an invite link to the browser so deferred attribution survives", async () => {
    // `/r/<code>` is the App-Store redirect the server owns. Swallowing it into
    // the app would strand the invite on the dashboard module fallback.
    const navigation = await renderChat(["join me https://pulsesoc.com/r/ab12cd"]);
    await act(async () => {
      fireEvent.press(screen.getByRole("link"));
    });
    expect(mockOpenURL).toHaveBeenCalledWith("https://pulsesoc.com/r/ab12cd");
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("never renders a javascript:, data: or file: body as a link", async () => {
    const navigation = await renderChat([
      "javascript:alert(1)",
      "data:text/html;base64,PHNjcmlwdD4=",
      "file:///etc/passwd"
    ]);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(mockOpenURL).not.toHaveBeenCalled();
    expect(navigation.navigate).not.toHaveBeenCalled();
  });

  it("does not turn a lookalike host into an in-app destination", async () => {
    const navigation = await renderChat(["https://pulsesoc.com.evil.net/pulse/post/1"]);
    await act(async () => {
      fireEvent.press(screen.getByRole("link"));
    });
    expect(navigation.navigate).not.toHaveBeenCalled();
    expect(mockOpenURL).toHaveBeenCalledWith("https://pulsesoc.com.evil.net/pulse/post/1");
  });

  it("leaves a message with no URL entirely unlinked", async () => {
    await renderChat(["no links here, just words"]);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
    expect(screen.getByText("no links here, just words")).toBeTruthy();
  });

  it("does not make the bubble itself open anything", async () => {
    // The requirement is that only the glyph run is the tap target. Pressing
    // the message's accessibility wrapper must do nothing navigational.
    const navigation = await renderChat(["Visit https://apple.com now"]);
    const paragraph = screen.getByText("Visit https://apple.com now");
    expect(paragraph.props.onPress).toBeUndefined();
    expect(navigation.navigate).not.toHaveBeenCalled();
    expect(mockOpenURL).not.toHaveBeenCalled();
  });

  it("still opens the message action sheet on a long press of the link", async () => {
    await renderChat(["https://pulsesoc.com/pulse/post/2432"]);
    await act(async () => {
      fireEvent(screen.getByRole("link"), "longPress");
    });
    // The sheet identifies itself by its own title; reply/react/report live
    // behind it, and they were the gestures at risk from a nested handler.
    expect(screen.getByText("Message controls")).toBeTruthy();
  });
});
