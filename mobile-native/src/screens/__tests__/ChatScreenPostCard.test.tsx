/**
 * A shared post, asserted against the real rendered conversation.
 *
 * `links/__tests__` proves the resolver and the preview store in isolation, and
 * both could be perfect with no card on screen: the card is reached through
 * `MessageBubble`, which decides on every render whether a body is about one
 * PulseSoc object. That decision is the feature. So this file mounts the real
 * `ChatScreen` with real message bodies.
 *
 * The property that matters most is that nothing here is stored. Every message
 * below is a plain `message_type: "text"` row whose body is just characters —
 * exactly what a conversation from before this change contains — and the card
 * is derived from it at render time. A test that seeded a `PULSESOC_ENTITY`
 * message type would pass against an implementation that only cards what it
 * sent, which would leave every existing conversation plain forever.
 *
 * Mutation contract:
 *   - making the card render from a stored field instead of the body must turn
 *     the "legacy" test red;
 *   - dropping the `disabled` on an unavailable card must turn the
 *     "does not offer a tap" test red;
 *   - drawing the raw URL alongside the card must turn the "replaces" test red;
 *   - suppressing the sender's prose must turn the "keeps the sentence" test red.
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

const mockGetPostDetail = jest.fn();

jest.mock("../../api/feed", () => {
  const actual = jest.requireActual("../../api/feed");
  return { ...actual, getPostDetail: (...args: unknown[]) => mockGetPostDetail(...args) };
});

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
import { PulseApiError } from "../../api/pulseApi";
import { clearEntityPreviewCache } from "../../links/entityPreview";

const METRICS = { frame: { x: 0, y: 0, width: 390, height: 844 }, insets: { top: 0, left: 0, right: 0, bottom: 0 } };
const CONVERSATION_ID = 6;
const POST_URL = "https://pulsesoc.com/pulse/post/2432";

function postDetail(overrides: Record<string, unknown> = {}) {
  return {
    post: {
      id: 2432,
      post_id: 2432,
      body: "Shipping the new upload engine today.",
      author: { display_name: "Ada Lovelace", username: "ada", avatar_url: "https://cdn/a.jpg" },
      media: [{ media_type: "image", media_url: "https://cdn/x.jpg" }],
      ...overrides
    }
  };
}

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

/**
 * A plain text row, which is what every conversation already holds. No new
 * message type, no preview columns -- the card has to come from the body.
 */
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
    created_at: `2026-09-19T10:0${index}:00Z`
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
  // Two flushes: one for the conversation load, one for the card's own fetch.
  await act(async () => {
    await Promise.resolve();
  });
  await act(async () => {
    await Promise.resolve();
  });
  return navigation;
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  clearEntityPreviewCache();
  mockGetPostDetail.mockResolvedValue(postDetail());
});

describe("a post link in a conversation", () => {
  it("builds a card from a plain text message written before cards existed", async () => {
    await renderChat([POST_URL]);
    // The message is characters and nothing else -- so this passing means the
    // card was derived, not sent.
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    expect(screen.getByText("@ada")).toBeTruthy();
    expect(screen.getByText("Shipping the new upload engine today.")).toBeTruthy();
    expect(screen.getByText("View Post →")).toBeTruthy();
  });

  it("replaces a bare link with its card rather than showing both", async () => {
    await renderChat([POST_URL]);
    expect(screen.queryByText(POST_URL)).toBeNull();
  });

  it("keeps the sentence the sender wrote around the link", async () => {
    await renderChat([`you have to read this ${POST_URL} before tonight`]);
    expect(screen.getByText("Ada Lovelace")).toBeTruthy();
    // The prose is the sender's; the card is additive, so both are on screen.
    expect(screen.getByText(/you have to read this/)).toBeTruthy();
  });

  it("opens the post natively when the card is pressed", async () => {
    const navigation = await renderChat([POST_URL]);
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Open the PulseSoc post by Ada Lovelace"));
    });
    expect(navigation.navigate).toHaveBeenCalledWith("PostDetail", expect.objectContaining({ postId: 2432 }));
    // Never a browser first, and never a WebView.
    expect(mockOpenURL).not.toHaveBeenCalled();
  });

  it("resolves each post once however many bubbles quote it", async () => {
    await renderChat([POST_URL, `again: ${POST_URL}`, POST_URL]);
    expect(mockGetPostDetail).toHaveBeenCalledTimes(1);
  });

  it("draws no card for a message that names two different posts", async () => {
    await renderChat([`${POST_URL} and https://pulsesoc.com/pulse/post/99`]);
    expect(screen.queryByText("View Post →")).toBeNull();
    // Both links stay tappable; the bubble just does not claim to be about one.
    expect(screen.queryAllByRole("link")).toHaveLength(2);
    expect(mockGetPostDetail).not.toHaveBeenCalled();
  });

  it("does not card an external link", async () => {
    await renderChat(["https://example.com/article"]);
    expect(screen.queryByText("View Post →")).toBeNull();
    expect(mockGetPostDetail).not.toHaveBeenCalled();
  });
});

describe("a post the viewer cannot see", () => {
  it("says so instead of showing any part of the post", async () => {
    mockGetPostDetail.mockRejectedValue(new PulseApiError("forbidden", 403));
    await renderChat([POST_URL]);
    expect(screen.getByText("This content isn't available to you.")).toBeTruthy();
    expect(screen.queryByText("Ada Lovelace")).toBeNull();
    expect(screen.queryByText("Shipping the new upload engine today.")).toBeNull();
  });

  it("says a deleted post is gone", async () => {
    mockGetPostDetail.mockRejectedValue(new PulseApiError("gone", 404));
    await renderChat([POST_URL]);
    expect(screen.getByText("This post is no longer available.")).toBeTruthy();
  });

  it("does not offer a tap that would only repeat the refusal", async () => {
    mockGetPostDetail.mockRejectedValue(new PulseApiError("forbidden", 403));
    const navigation = await renderChat([POST_URL]);
    await act(async () => {
      fireEvent.press(screen.getByLabelText("Open this PulseSoc post"));
    });
    expect(navigation.navigate).not.toHaveBeenCalledWith("PostDetail", expect.anything());
  });

  it("keeps the conversation up when preview resolution throws", async () => {
    mockGetPostDetail.mockImplementation(() => {
      throw new Error("boom");
    });
    await renderChat([POST_URL]);
    // The bubble, its sender and the rest of the screen are still rendered.
    expect(screen.getByText("Content unavailable")).toBeTruthy();
    // Header title and sender label both, which is the screen intact.
    expect(screen.getAllByText("ROODY CHERIE").length).toBeGreaterThan(0);
  });
});
