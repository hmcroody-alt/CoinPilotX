/**
 * The graphite conversation surface, asserted against a rendered conversation.
 *
 * `chatGraphiteContrast.test.ts` proves the palette is internally sound and that
 * `ChatScreen.tsx` mentions the tokens. Neither of those is the same as the
 * screen *drawing* them. A style can be declared and never applied — a spread
 * order can drop it, a `[base, override]` array can put the old value last, a
 * component can ignore the `style` prop it was handed. All three have happened
 * in this file's history: `PulseCommandPanel` sets its own `borderColor` and the
 * composer's only wins because the caller's `style` is spread last.
 *
 * So this mounts the real screen with a real incoming and outgoing message and
 * reads the colours off the rendered tree. It also asserts the *absence* of the
 * navy palette it replaces, which is the half a source-grep cannot do: a stray
 * `rgba(7,15,32,0.96)` surviving in a conditional branch would leave one state
 * of the screen on the old design and every test above would still pass.
 */

import React from "react";
import { act, render } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { StyleSheet } from "react-native";

jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn(), Recording: class {}, Sound: class {} },
  ResizeMode: { CONTAIN: "contain", COVER: "cover", STRETCH: "stretch" },
  Video: jest.requireActual("react").forwardRef(() => null)
}));
jest.mock("expo-file-system", () => ({ File: class {} }));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({ launchImageLibraryAsync: jest.fn(), requestMediaLibraryPermissionsAsync: jest.fn() }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));

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
    getConversationControlCenter: jest.fn().mockRejectedValue(new Error("no control centre in this fixture"))
  };
});

jest.mock("../../core/TimeZoneContext", () => ({ useTimeZonePreference: () => ({ locale: "en-US" }) }));
jest.mock("../../api/translation", () => ({
  peekTranslationPreference: jest.fn(() => undefined),
  subscribeTranslationPreference: jest.fn(() => () => undefined),
  translatePulseContent: jest.fn(),
  updateTranslationPreference: jest.fn()
}));

import { ContentTranslation } from "../../components/ContentTranslation";
import { ChatScreen } from "../ChatScreen";
import { MessengerMessage } from "../../api/messenger";
import { chatGraphite } from "../../theme/chatGraphite";

const CONVERSATION_ID = 8801;

/**
 * A device with real chrome at both ends. The safe areas are the point: the
 * brief calls out the status bar and the home-indicator strip by name, because
 * a header that stops at `insets.top` leaves a band of whatever is behind it.
 */
const METRICS = {
  frame: { x: 0, y: 0, width: 393, height: 852 },
  insets: { top: 59, left: 0, right: 0, bottom: 34 }
};

function message(overrides: Partial<MessengerMessage>): MessengerMessage {
  return {
    id: 1,
    message_id: 1,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "text",
    body: "Graphite",
    created_at: "2026-09-16T10:00:00Z",
    ...overrides
  } as MessengerMessage;
}

type StyleBag = Record<string, unknown>;

/** Every flattened style object in the rendered tree, in render order. */
function renderedStyles(tree: unknown): StyleBag[] {
  const found: StyleBag[] = [];
  const walk = (node: unknown) => {
    if (Array.isArray(node)) {
      node.forEach(walk);
      return;
    }
    if (!node || typeof node !== "object") return;
    const element = node as { props?: { style?: unknown }; children?: unknown };
    const style = element.props?.style;
    if (style) {
      const flat = StyleSheet.flatten(style as never) as StyleBag | undefined;
      if (flat && typeof flat === "object") found.push(flat);
    }
    if (element.children) walk(element.children);
  };
  walk(tree);
  return found;
}

function withBackground(styles: StyleBag[], color: string): StyleBag[] {
  return styles.filter((style) => style.backgroundColor === color);
}

/** Every colour-valued style property in the tree, as one flat list. */
function allColorValues(styles: StyleBag[]): string[] {
  const values: string[] = [];
  for (const style of styles) {
    for (const [key, value] of Object.entries(style)) {
      if (typeof value === "string" && /color/i.test(key)) values.push(value);
    }
  }
  return values;
}

async function renderConversation() {
  mockGetConversation.mockResolvedValue({
    conversation: { id: CONVERSATION_ID, title: "Fixture thread" },
    messages: [
      message({ id: 1, message_id: 1, is_mine: false, body: "From them" }),
      message({ id: 2, message_id: 2, sender_id: 7, is_mine: true, body: "From me" })
    ],
    presence: { typing: [] }
  });
  const view = render(
    <SafeAreaProvider initialMetrics={METRICS}>
      <ChatScreen
        route={{ key: "c", name: "Chat", params: { conversationId: CONVERSATION_ID, title: "Fixture thread" } } as never}
        navigation={
          {
            setOptions: jest.fn(),
            navigate: jest.fn(),
            goBack: jest.fn(),
            getState: () => ({ routes: [] }),
            addListener: jest.fn(() => jest.fn())
          } as never
        }
      />
    </SafeAreaProvider>
  );
  await act(async () => {
    await Promise.resolve();
  });
  return renderedStyles(view.toJSON());
}

/**
 * The navy palette this change replaces.
 *
 * Listed as literals on purpose — these are the values that must be *gone*, so
 * importing them from anywhere would defeat the check. Each one is the exact
 * string that used to appear in the style block.
 */
const REPLACED = [
  "rgba(7,15,32,0.96)", // header fill
  "rgba(97,216,255,0.26)", // header divider
  "rgba(12,24,43,0.88)", // incoming bubble
  "rgba(105,218,240,0.28)", // incoming border
  "rgba(37,83,158,0.82)", // outgoing bubble
  "rgba(93,174,255,0.58)", // outgoing border
  "rgba(2,10,20,0.98)", // composer fill
  "rgba(65,236,198,0.48)", // composer border
  "rgba(2,9,19,0.92)", // input fill
  "rgba(4,16,28,0.9)", // icon control fill
  "#050910" // the bottom safe-area strip, which used to be the app background
];

describe("ChatScreen draws the graphite surface", () => {
  it("paints the header, its safe area and its divider from the tokens", async () => {
    const styles = await renderConversation();
    const headers = withBackground(styles, chatGraphite.headerSurface).filter(
      (style) => style.borderBottomColor === chatGraphite.quietDivider
    );
    expect(headers.length).toBeGreaterThan(0);
    // The status-bar strip is the header's own top padding, so the graphite
    // reaches the top of the display rather than stopping below the notch.
    expect(headers.some((style) => style.paddingTop === METRICS.insets.top)).toBe(true);
  });

  it("paints the footer and the home-indicator strip from the same token", async () => {
    const styles = await renderConversation();
    const footerish = withBackground(styles, chatGraphite.headerSurface);
    // Two distinct surfaces carry `headerSurface`: the header, and the
    // composer plus the strip beneath it. Keyed on the composer's own rounded
    // top corners so this cannot be satisfied by the header twice.
    expect(footerish.some((style) => style.borderTopLeftRadius === 22)).toBe(true);
    // The bottom safe area, which the composer pads out to rather than letting
    // the shell own it — `bottomDock={false}` means the shell adds nothing.
    expect(footerish.some((style) => style.paddingBottom === METRICS.insets.bottom)).toBe(true);
  });

  it("paints both bubbles, and separates them by geometry rather than weight", async () => {
    const styles = await renderConversation();

    const incoming = withBackground(styles, chatGraphite.incomingSurface);
    const outgoing = withBackground(styles, chatGraphite.outgoingSurface);
    expect(incoming.length).toBe(1);
    expect(outgoing.length).toBe(1);

    expect(incoming[0].borderColor).toBe(chatGraphite.incomingBorder);
    expect(outgoing[0].borderColor).toBe(chatGraphite.outgoingBorder);

    // The non-colour signal, rendered rather than declared: the squared corner
    // is on opposite sides. This is what carries sender-vs-recipient in
    // grayscale, and the two fills are within 3.3% in luminance precisely
    // because it does.
    expect(incoming[0].borderBottomLeftRadius).toBe(6);
    expect(outgoing[0].borderBottomRightRadius).toBe(6);
    expect(incoming[0].borderBottomRightRadius).toBeUndefined();
    expect(outgoing[0].borderBottomLeftRadius).toBeUndefined();
  });

  it("paints the composer field one step under the footer, with its edge intact", async () => {
    const styles = await renderConversation();
    const fields = withBackground(styles, chatGraphite.composerSurface);
    expect(fields.length).toBeGreaterThan(0);
    // The cyan edge is load-bearing here: the field's fill is 1.12:1 against
    // the footer, so the border is the only thing that makes it a control.
    // See `chatGraphiteContrast.test.ts`.
    expect(fields.some((style) => style.borderColor === "rgba(97,216,255,0.5)")).toBe(true);
  });

  it("keeps the send button's teal and the disabled send's grey", async () => {
    const styles = await renderConversation();
    // The one saturated accent the brief asks to retain. An empty draft means
    // the disabled fill is what is actually rendered, over the teal base.
    expect(withBackground(styles, "rgba(146,161,181,0.2)").length).toBeGreaterThan(0);
  });

  it("leaves none of the navy palette anywhere in the tree", async () => {
    const colorValues = allColorValues(await renderConversation());
    for (const stale of REPLACED) {
      expect(colorValues).not.toContain(stale);
    }
  });
});

describe("the compact Translate control follows the bubble it sits in", () => {
  /**
   * `controlsMode="compact"` has one call site — the chat bubble — so this pill
   * is part of the conversation surface even though `ContentTranslation` is
   * shared with Reels, Marketplace and the feed. Rendered directly rather than
   * through the screen because the screen only shows it for a foreign-language
   * message, and `sourceLanguage` is the switch that decides that.
   */
  it("uses the graphite inset and metadata tokens", () => {
    const view = render(
      <ContentTranslation
        contentType="chat"
        contentRef="m-foreign"
        text="Hola PulseSoc"
        sourceLanguage="es"
        controlsMode="compact"
      />
    );
    const styles = renderedStyles(view.toJSON());
    const pill = withBackground(styles, chatGraphite.insetSurface);
    expect(pill.length).toBe(1);
    expect(styles.some((style) => style.color === chatGraphite.secondaryText)).toBe(true);
    // The old cyan wash, which measured 4.04:1 for its label once the bubble
    // behind it became `#505761`.
    expect(allColorValues(styles)).not.toContain("rgba(110,223,246,0.05)");
  });
});
