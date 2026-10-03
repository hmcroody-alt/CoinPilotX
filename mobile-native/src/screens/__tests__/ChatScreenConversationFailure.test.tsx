/**
 * What the buyer sees, and can do, when "Message seller" lands on a
 * conversation the server refuses.
 *
 * The reported screen made three claims at once — "Messages unavailable" in the
 * header, "Conversation not found." in the panel, and "PULSE LINK ·
 * RECONNECTING" over a composer that still took typing. Two were false: there
 * is no realtime link in this screen to reconnect, and the server had just
 * refused the conversation, so anything typed would have been dropped.
 *
 * `conversationFailure.test.ts` pins the rules. This file exists because that
 * one structurally cannot see the defect: the taxonomy was never the thing on
 * screen. What is pinned here is the *wiring* — the real ChatScreen, mounted,
 * with `getConversation` rejecting the way the v2 blueprint actually rejects.
 *
 * Mutation contract — each of these must turn this file red:
 *   - re-deriving the status line from an error string instead of the posture
 *     (which is what printed RECONNECTING);
 *   - dropping `editable`/`disabled` from any composer control;
 *   - pointing Retry back at `getConversation` instead of re-resolving the pair;
 *   - replacing `navigation.replace` with a `navigate`, which would leave the
 *     refused id on the stack behind the repaired one;
 *   - resolving the pair when the failure is not a missing conversation.
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
const mockLoadCachedMessages = jest.fn();
const mockResolveDirectConversation = jest.fn();

jest.mock("../../api/messenger", () => {
  const actual = jest.requireActual("../../api/messenger");
  return {
    ...actual,
    getConversation: (...args: unknown[]) => mockGetConversation(...args),
    resolveDirectConversation: (...args: unknown[]) => mockResolveDirectConversation(...args),
    loadCachedMessages: (...args: unknown[]) => mockLoadCachedMessages(...args),
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

/** The id `/api/pulse/messages/start` used to hand back: a legacy row the v2 reader has never heard of. */
const REFUSED_ID = 86;
/** What re-resolving the pair returns instead: the conversation that exists. */
const REAL_ID = 34;
/** M&W Store's owner — a store is a presentation of a user, and messaging is between the users. */
const SELLER_USER_ID = 41;

/**
 * A rejection shaped the way the v2 blueprint actually sends one.
 *
 * `code` stays undefined: the blueprint answers `{ok:false,status:"not_found"}`
 * and `pulseApi` only reads a code out of `error_code`/`error`, so the v2 code
 * arrives inside `details` and nowhere else.
 */
function apiError(status: number, message: string, v2Code?: string) {
  return {
    name: "PulseApiError",
    message,
    status,
    code: undefined,
    details: v2Code ? { ok: false, status: v2Code } : undefined
  };
}

type Nav = {
  canGoBack: jest.Mock;
  goBack: jest.Mock;
  navigate: jest.Mock;
  replace: jest.Mock;
  setOptions: jest.Mock;
  getState: () => { routes: never[] };
  addListener: jest.Mock;
};

function makeNavigation(): Nav {
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

async function renderChat(params: Record<string, unknown>) {
  const navigation = makeNavigation();
  render(
    <SafeAreaProvider initialMetrics={METRICS}>
      <ChatScreen
        route={{ key: "c", name: "Chat", params } as never}
        navigation={navigation as never}
      />
    </SafeAreaProvider>
  );
  await act(async () => {
    await Promise.resolve();
  });
  return navigation;
}

/** The buyer's path: tapped "Message seller", so the screen knows the seller. */
function messageSellerParams() {
  return { conversationId: REFUSED_ID, peerUserId: SELLER_USER_ID, title: "M&W Store" };
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  mockLoadCachedMessages.mockResolvedValue([]);
  mockResolveDirectConversation.mockResolvedValue(REAL_ID);
});

describe("the screen the buyer was shown", () => {
  beforeEach(() => {
    mockGetConversation.mockRejectedValue(apiError(404, "Conversation not found.", "not_found"));
  });

  it("tells one story about the failure instead of three", async () => {
    await renderChat(messageSellerParams());

    expect(screen.getAllByText("Conversation unavailable").length).toBeGreaterThan(0);
    // The two false claims. RECONNECTING was the worst of them: there is no
    // realtime subscription in this screen, so it described a subsystem that
    // does not exist while the real refusal went unexplained.
    expect(screen.queryByText("RECONNECTING")).toBeNull();
    expect(screen.queryByText("Messages unavailable")).toBeNull();
  });

  it("stops offering to take a message the server would refuse", async () => {
    await renderChat(messageSellerParams());

    const composer = screen.getByLabelText("Message composer");
    expect(composer.props.editable).toBe(false);
    expect(screen.getByLabelText("Send message").props.accessibilityState.disabled).toBe(true);
    expect(screen.getByLabelText("Add attachment").props.accessibilityState.disabled).toBe(true);
    expect(screen.getByLabelText("Record voice message").props.accessibilityState.disabled).toBe(true);
    // Said out loud rather than only greyed, because a disabled control with an
    // inviting placeholder still reads as "type here".
    expect(composer.props.placeholder).toBe("Messaging unavailable");
  });

  it("repairs the conversation instead of re-asking for the refused id", async () => {
    const navigation = await renderChat(messageSellerParams());
    mockGetConversation.mockClear();

    await act(async () => {
      fireEvent.press(screen.getByLabelText("Retry loading messages"));
    });

    expect(mockResolveDirectConversation).toHaveBeenCalledWith(SELLER_USER_ID);
    // The loop the old Retry put the buyer in: the server had already refused
    // this id, so asking again could only be refused again.
    expect(mockGetConversation).not.toHaveBeenCalled();
    expect(navigation.replace).toHaveBeenCalledWith("Chat", {
      ...messageSellerParams(),
      conversationId: REAL_ID
    });
    // `replace`, not `navigate` — the refused id must not stay on the stack
    // under the repaired one for Back to walk into.
    expect(navigation.navigate).not.toHaveBeenCalled();
  });
});

describe("a refused conversation with no seller to resolve against", () => {
  it("offers a way out rather than a retry that cannot work", async () => {
    // Reached from a notification or a deep link, where only the id is known.
    mockGetConversation.mockRejectedValue(apiError(404, "Conversation not found.", "not_found"));
    await renderChat({ conversationId: REFUSED_ID, title: "M&W Store" });

    expect(screen.getByLabelText("Back")).toBeTruthy();
    expect(screen.queryByLabelText("Retry loading messages")).toBeNull();
    expect(mockResolveDirectConversation).not.toHaveBeenCalled();
  });
});

describe("a failure that is about the connection, not the conversation", () => {
  it("leaves a working thread usable", async () => {
    // Cached history on screen and a transport hiccup over it. Taking the
    // composer away here is the lie in the other direction: the send would work.
    mockGetConversation.mockRejectedValue({ message: "Network request failed" });
    mockLoadCachedMessages.mockResolvedValue([
      { id: 9001, conversation_id: REFUSED_ID, sender_id: SELLER_USER_ID, body: "Still here", created_at: "2026-10-01T00:00:00Z" }
    ]);

    await renderChat(messageSellerParams());

    expect(screen.getByLabelText("Message composer").props.editable).toBe(true);
    expect(screen.getByText("Still here")).toBeTruthy();
  });

  it("does not resolve the pair for a failure a resolve cannot fix", async () => {
    mockGetConversation.mockRejectedValue({ message: "Network request failed" });

    await renderChat(messageSellerParams());
    // Header and panel both say it, which is the point: one posture, one story.
    expect(screen.getAllByText("No connection").length).toBeGreaterThan(0);

    await act(async () => {
      fireEvent.press(screen.getByLabelText("Retry loading messages"));
    });

    // The conversation is fine; the fetch failed. Swapping the id here would be
    // a repair aimed at the wrong subsystem.
    expect(mockResolveDirectConversation).not.toHaveBeenCalled();
    expect(mockGetConversation).toHaveBeenCalled();
  });
});
