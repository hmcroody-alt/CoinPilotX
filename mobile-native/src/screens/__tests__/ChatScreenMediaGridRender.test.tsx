/**
 * §21, asserted against a rendered conversation rather than against the source.
 *
 * `conversationGalleryArchitecture.test.ts` pins the *shapes* the grid must have,
 * which catches a revert and nothing else — a tile can be spelled
 * `onPress={() => onOpen({ ...tile, url, ... })}` and still open the wrong photo
 * if `tiles` was built in the wrong order, or draw the right photo from the wrong
 * URL. So this file mounts the real screen with a real three-photo message and
 * presses the real third tile.
 *
 * The grants run for real through a mocked `pulseApi`, which is the point: what
 * each tile draws, and what the viewer is handed when a tile is pressed, is
 * whatever the grant said. A grid that reused the bubble's single grant would
 * pass every source guard and paint the same photo three times.
 */

import React from "react";
import { Image } from "react-native";
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn(), Recording: class {}, Sound: class {} },
  ResizeMode: { CONTAIN: "contain", COVER: "cover", STRETCH: "stretch" },
  Video: jest.requireActual("react").forwardRef(() => null)
}));
jest.mock("expo-file-system", () => ({ File: class {} }));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({ launchImageLibraryAsync: jest.fn(), requestMediaLibraryPermissionsAsync: jest.fn() }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return {
    // `renderText` is honoured rather than ignored: the bubble passes one so
    // URLs in the body become link segments, and dropping it would render a
    // body of `undefined` while still reporting green.
    ContentTranslation: ({ text, textStyle, renderText }: { text?: string; textStyle?: unknown; renderText?: (value: string, translated: boolean) => unknown }) =>
      renderText
        ? renderText(String(text ?? ""), false)
        : ReactActual.createElement(Text, { style: textStyle }, text)
  };
});

const CONVERSATION_ID = 6105;
const MESSAGE_ID = 512;

/** Foundation `message_attachments` ids — one per photo, all distinct. */
const TILE_MEDIA_IDS = [4401, 4402, 4403];
/** Transport attachment ids. Deliberately a different number space. */
const TILE_ATTACHMENT_IDS = [881, 882, 883];

const mockGrants = new Map<number, { access_url: string; thumbnail_access_url: string }>();
const mockAccessRequests: number[] = [];

jest.mock("../../api/pulseApi", () => {
  const actual = jest.requireActual("../../api/pulseApi");
  return {
    ...actual,
    pulseApi: (path: string) => {
      const match = /\/api\/messages\/media\/(\d+)\/access$/.exec(String(path));
      if (!match) return Promise.resolve({ ok: true });
      const id = Number(match[1]);
      mockAccessRequests.push(id);
      const grant = mockGrants.get(id);
      if (!grant) return Promise.reject(new Error(`no grant fixture for attachment ${id}`));
      return Promise.resolve({ ok: true, expires_in: 600, ...grant });
    }
  };
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
    drainMessengerQueue: jest.fn().mockResolvedValue([])
  };
});

/**
 * The conversation's own media page, kept empty on purpose.
 *
 * With no server page, the collection is exactly the seed the pressed tile
 * handed over — which is the sharpest possible reading of "opening tile 3 opens
 * tile 3", because there is nothing else in the viewer for it to accidentally
 * land on that happens to look right.
 */
jest.mock("../../api/conversationMedia", () => ({
  fetchConversationMedia: jest.fn().mockResolvedValue({
    items: [], total: 0, has_older: false, has_newer: false, oldest_id: 0, newest_id: 0
  })
}));

import { ChatScreen } from "../ChatScreen";
import { MessengerMessage } from "../../api/messenger";
import { invalidateMessengerMediaAccess } from "../../media/messengerMediaAccess";
import { activateLocale } from "../../i18n/engine";

function original(position: number) {
  return `https://media.pulsesoc.test/grants/photo-${position}.jpg?token=original-${position}`;
}
function preview(position: number) {
  return `https://media.pulsesoc.test/grants/photo-${position}-thumb.jpg?token=preview-${position}`;
}

/**
 * One message carrying three photos — what the web composer can send and the
 * mobile composer cannot, which is why this went unnoticed.
 *
 * Note the attachments arrive out of order. The canonical order is ascending
 * attachment id, so a grid that rendered them as received would draw 3, 1, 2 and
 * "tile 3" would be the second photo.
 */
function threePhotoMessage(): MessengerMessage {
  const rows = [2, 0, 1].map((index) => ({
    id: TILE_ATTACHMENT_IDS[index],
    attachment_id: TILE_ATTACHMENT_IDS[index],
    message_id: MESSAGE_ID,
    media_upload_id: TILE_MEDIA_IDS[index],
    media_type: "image",
    mime_type: "image/jpeg",
    url: `/api/messages/media/${TILE_MEDIA_IDS[index]}/download`,
    thumbnail_url: "",
    width: 1200,
    height: 900,
    sender_user_id: 9,
    sender_display_name: "Fixture Sender",
    created_at: "2026-09-13T10:02:00Z"
  }));
  return {
    id: MESSAGE_ID,
    message_id: MESSAGE_ID,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "image",
    body: "",
    mime_type: "image/jpeg",
    // `firstAttachment` flattens attachments[0] onto the message. Kept faithful
    // so the test exercises the same payload the screen really receives.
    media_upload_id: TILE_MEDIA_IDS[2],
    attachment_id: TILE_ATTACHMENT_IDS[2],
    media_url: `/api/messages/media/${TILE_MEDIA_IDS[2]}/download`,
    attachments: rows,
    created_at: "2026-09-13T10:02:00Z"
  } as unknown as MessengerMessage;
}

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 }
};

async function renderConversation(messages: MessengerMessage[]) {
  mockGetConversation.mockResolvedValue({
    conversation: { id: CONVERSATION_ID, title: "Fixture thread" },
    messages,
    presence: { typing: [] }
  });
  render(
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
}

function imageUris(): string[] {
  return screen
    .UNSAFE_queryAllByType(Image)
    .map((node) => String((node.props as { source?: { uri?: string } }).source?.uri || ""))
    .filter(Boolean);
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  mockGrants.clear();
  mockAccessRequests.length = 0;
  for (const id of [...TILE_MEDIA_IDS, ...TILE_ATTACHMENT_IDS]) invalidateMessengerMediaAccess(id);
  TILE_MEDIA_IDS.forEach((id, index) => {
    mockGrants.set(id, { access_url: original(index + 1), thumbnail_access_url: preview(index + 1) });
  });
});

describe("a message carrying three photos", () => {
  it("draws all three, not just the first", async () => {
    // The defect in one assertion: `firstAttachment` made this bubble render one
    // photo, with the other two reachable from nowhere.
    await renderConversation([threePhotoMessage()]);
    const drawn = imageUris();
    for (const position of [1, 2, 3]) {
      expect(drawn).toContain(preview(position));
    }
  });

  it("asks a separate grant per tile, against each tile's own foundation id", async () => {
    await renderConversation([threePhotoMessage()]);
    for (const id of TILE_MEDIA_IDS) {
      expect(mockAccessRequests).toContain(id);
    }
    // The transport ids are a different number space and asking for one is a
    // hard 404, so no tile may have used them to address the access endpoint.
    for (const id of TILE_ATTACHMENT_IDS) {
      expect(mockAccessRequests).not.toContain(id);
    }
  });

  it("draws the preview, never the original, in the grid", async () => {
    // Three tiles pulling three full-resolution originals to paint squares a
    // hundred points wide is the single-photo card's old mistake, multiplied.
    await renderConversation([threePhotoMessage()]);
    const drawn = imageUris();
    expect(drawn.length).toBeGreaterThanOrEqual(3);
    for (const position of [1, 2, 3]) {
      expect(drawn).not.toContain(original(position));
    }
  });

  it("labels each tile with its position for VoiceOver", async () => {
    await renderConversation([threePhotoMessage()]);
    expect(screen.getByLabelText(/Photo 1 of 3\./)).toBeTruthy();
    expect(screen.getByLabelText(/Photo 3 of 3\./)).toBeTruthy();
  });
});

describe("opening tile 3", () => {
  /**
   * MUTATION §37: "tapping the third photo of a multi-photo message opens a
   * different photo".
   *
   * The attachments arrive as 3, 1, 2, so a grid that skipped the canonical sort
   * renders the third photo in slot two — and pressing the third *tile* would
   * open photo 2 while still looking entirely plausible on screen.
   */
  it("opens the viewer on the third photo, not the first", async () => {
    await renderConversation([threePhotoMessage()]);

    await act(async () => {
      fireEvent.press(screen.getByLabelText(/Photo 3 of 3\./));
    });

    // The grid draws previews and the viewer draws originals, so the original
    // URLs are an unambiguous read of which photo the viewer is showing.
    const drawn = imageUris();
    expect(drawn).toContain(original(3));
    expect(drawn).not.toContain(original(1));
    expect(drawn).not.toContain(original(2));
  });

  it("opens the viewer on the first photo when the first is the one pressed", async () => {
    // The mirror image, because "always opens photo 3" would pass the test above.
    await renderConversation([threePhotoMessage()]);

    await act(async () => {
      fireEvent.press(screen.getByLabelText(/Photo 1 of 3\./));
    });

    const drawn = imageUris();
    expect(drawn).toContain(original(1));
    expect(drawn).not.toContain(original(3));
  });

  it("hands the viewer the granted url, never the protected download path", async () => {
    await renderConversation([threePhotoMessage()]);

    await act(async () => {
      fireEvent.press(screen.getByLabelText(/Photo 2 of 3\./));
    });

    expect(imageUris()).toContain(original(2));
    for (const uri of imageUris()) {
      expect(uri).not.toMatch(/\/api\/messages\/media\/\d+\/download/);
    }
  });
});
