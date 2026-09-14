/**
 * The two attachment defects, asserted against a rendered conversation.
 *
 * `ChatScreenAttachmentWiring.test.ts` reads `ChatScreen.tsx` as text and says so
 * in its own docstring: it pins the *expressions* that were fixed. That catches a
 * revert and nothing else. A press handler can be spelled `onPress={open}` and
 * still not open anything, and an `<Image>` can be guarded by a truthy check on a
 * variable that was assigned the movie's URL two lines earlier.
 *
 * So this file mounts the real screen with a real conversation and presses the
 * real card. Two defects are under test:
 *
 *   1. A document arrived, said "Sent", and did nothing when tapped — the
 *      `onPress` evaluated to `undefined` for every non-video attachment.
 *   2. A video bubble fed an entire movie to `<Image>`, because the bubble asked
 *      the same hook for the same identity twice and both calls resolved to the
 *      same `/download` URL, so the "thumbnail" slot was handed the original.
 *
 * The access grant runs for real here, through a mocked `pulseApi`. That matters
 * for (2): what the poster slot receives is whatever the grant said, and the
 * grant is the thing that used to be asked twice.
 */

import React from "react";
import { Image } from "react-native";
import { act, fireEvent, render, screen } from "@testing-library/react-native";
import { SafeAreaProvider } from "react-native-safe-area-context";

// Native modules the screen reaches on import. Stubbed rather than exercised:
// none is under test and all of them throw at require time under jest-expo.
// `Video` is stubbed to nothing on purpose: the full-screen viewer is the one
// component that is *supposed* to receive the movie's URL, and leaving it out of
// the tree keeps `imageUris()` an honest census of what reached the image loader.
jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn(), Recording: class {}, Sound: class {} },
  ResizeMode: { CONTAIN: "contain", COVER: "cover", STRETCH: "stretch" },
  Video: jest.requireActual("react").forwardRef(() => null)
}));
jest.mock("expo-file-system", () => ({ File: class {} }));
jest.mock("expo-document-picker", () => ({ getDocumentAsync: jest.fn() }));
jest.mock("expo-image-picker", () => ({ launchImageLibraryAsync: jest.fn(), requestMediaLibraryPermissionsAsync: jest.fn() }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7 } } }) }));

// The body of a message is rendered through the translation engine, which wants
// locale storage and a translation API. Reduced to the text it is handed so the
// filename is still assertable.
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return {
    ContentTranslation: ({ text, textStyle }: { text?: string; textStyle?: unknown }) =>
      ReactActual.createElement(Text, { style: textStyle }, text)
  };
});

const CONVERSATION_ID = 6104;

/** Foundation `message_attachments` ids — what the access endpoint is keyed on. */
const DOCUMENT_MEDIA_ID = 3301;
const VIDEO_MEDIA_ID = 3302;
const PHOTO_MEDIA_ID = 3303;
/**
 * A transport attachment id carried by the same rows. Deliberately a different
 * number: Comm-v2 messages carry both, and asking the access endpoint for this
 * one is a hard 404. Every assertion about which id was used reads against this.
 */
const TRANSPORT_ATTACHMENT_ID = 777;

const DOCUMENT_ACCESS_URL = "https://media.pulsesoc.test/grants/invoice.pdf?token=doc-grant";
const MOVIE_ACCESS_URL = "https://media.pulsesoc.test/grants/clip.mp4?token=movie-grant";
const POSTER_ACCESS_URL = "https://media.pulsesoc.test/grants/clip-poster.jpg?token=poster-grant";
const PHOTO_ACCESS_URL = "https://media.pulsesoc.test/grants/photo.jpg?token=photo-grant";

/** What `/api/messages/media/<id>/access` answers, per test. */
const mockGrants = new Map<number, {
  access_url: string;
  thumbnail_access_url: string;
  /** The attachment row `/access` embeds. Absent means the grant reported none. */
  attachment?: Record<string, unknown>;
}>();
/** Every attachment id the screen asked a grant for, in order. */
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

const mockOpenDocument = jest.fn();

jest.mock("../../media/mediaActions", () => {
  const actual = jest.requireActual("../../media/mediaActions");
  return {
    ...actual,
    openDocument: (...args: unknown[]) => mockOpenDocument(...args)
  };
});

import { ChatScreen } from "../ChatScreen";
import { MessengerMessage } from "../../api/messenger";
import { invalidateMessengerMediaAccess } from "../../media/messengerMediaAccess";
import { activateLocale } from "../../i18n/engine";

const DOCUMENT_FILENAME = "Q3-invoice.pdf";

function documentMessage(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 91,
    message_id: 91,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "file",
    // Messenger carries a document's filename in the body, and the card uses it
    // as the title it hands the viewer.
    body: DOCUMENT_FILENAME,
    mime_type: "application/pdf",
    file_size: 482311,
    media_upload_id: DOCUMENT_MEDIA_ID,
    attachment_id: TRANSPORT_ATTACHMENT_ID,
    media_url: `/api/messages/media/${DOCUMENT_MEDIA_ID}/download`,
    created_at: "2026-09-13T10:00:00Z",
    ...overrides
  } as MessengerMessage;
}

function videoMessage(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 92,
    message_id: 92,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "video",
    body: "",
    mime_type: "video/mp4",
    file_size: 734_003_200,
    media_upload_id: VIDEO_MEDIA_ID,
    attachment_id: TRANSPORT_ATTACHMENT_ID,
    media_url: `/api/messages/media/${VIDEO_MEDIA_ID}/download`,
    created_at: "2026-09-13T10:01:00Z",
    ...overrides
  } as MessengerMessage;
}

function photoMessage(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 93,
    message_id: 93,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "image",
    body: "",
    mime_type: "image/jpeg",
    file_size: 1_240_000,
    media_upload_id: PHOTO_MEDIA_ID,
    attachment_id: TRANSPORT_ATTACHMENT_ID,
    media_url: `/api/messages/media/${PHOTO_MEDIA_ID}/download`,
    created_at: "2026-09-13T10:02:00Z",
    ...overrides
  } as MessengerMessage;
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
  // The grant resolves in an effect after the fetch resolves, so the bubble
  // renders twice before it has a URL to draw.
  await act(async () => {
    await Promise.resolve();
  });
}

/** Every URI this screen handed to the platform image loader. */
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
  // The grant cache is module-level and survives between tests by design — a
  // conversation reopened seconds later must not re-ask. Cleared here so each
  // test's grant fixture is the one actually in force.
  for (const id of [DOCUMENT_MEDIA_ID, VIDEO_MEDIA_ID, PHOTO_MEDIA_ID, TRANSPORT_ATTACHMENT_ID]) {
    invalidateMessengerMediaAccess(id);
  }
  mockOpenDocument.mockResolvedValue({ status: "opened" });
});

describe("a document attachment opens when it is tapped", () => {
  beforeEach(() => {
    mockGrants.set(DOCUMENT_MEDIA_ID, { access_url: DOCUMENT_ACCESS_URL, thumbnail_access_url: "" });
  });

  it("calls the shared open action with the granted URL and the foundation media id", async () => {
    // The whole first defect in one assertion. Before the fix this press reached
    // an `onPress` that evaluated to `undefined`: the card was drawn, the tap
    // registered on the Pressable, and nothing happened for the life of the
    // conversation.
    await renderConversation([documentMessage()]);
    const card = await screen.findByLabelText(DOCUMENT_FILENAME);

    await act(async () => {
      fireEvent.press(card);
    });

    expect(mockOpenDocument).toHaveBeenCalledTimes(1);
    expect(mockOpenDocument).toHaveBeenCalledWith({
      // The short-lived grant, not the protected `/download` path. Handing the
      // document downloader an API path is what made media loads run session
      // refresh on the server.
      url: DOCUMENT_ACCESS_URL,
      // media_upload_id, never the transport attachment id sitting beside it in
      // the same row.
      mediaId: DOCUMENT_MEDIA_ID,
      // Load-bearing on iOS: without the MIME type the viewer is handed an
      // opaque blob and offers nothing that can read a PDF.
      mimeType: "application/pdf",
      expectedBytes: 482311,
      surface: "messenger",
      title: DOCUMENT_FILENAME
    });
  });

  it("uses the shared action rather than a Messenger-local open", async () => {
    // `openDocument` is where the access grant, the retry policy and the on-disk
    // cache live. A Messenger-local implementation would open the file and still
    // be the fourth copy of all three.
    await renderConversation([documentMessage()]);

    await act(async () => {
      fireEvent.press(await screen.findByLabelText(DOCUMENT_FILENAME));
    });

    expect(mockOpenDocument).toHaveBeenCalledWith(expect.objectContaining({ surface: "messenger" }));
  });

  it("says why on the card when the file will not open", async () => {
    // A tap that fails silently is the defect again, one layer along: the person
    // learns nothing and taps a second time.
    const failure = "That file is no longer available.";
    mockOpenDocument.mockResolvedValue({ status: "failed", reason: "not_found", message: failure });
    await renderConversation([documentMessage()]);

    await act(async () => {
      fireEvent.press(await screen.findByLabelText(DOCUMENT_FILENAME));
    });

    expect(screen.getByText(failure)).toBeTruthy();
  });

  it("is the card an unrecognised attachment type lands on", async () => {
    // The openable card is the renderer of last resort, not a branch gated on a
    // mime allowlist. An attachment type nobody enumerated must still open —
    // otherwise the original defect returns for whatever gets sent next, and it
    // returns silently.
    await renderConversation([
      documentMessage({ message_type: "attachment", mime_type: "application/vnd.unknown-thing", body: "contract.xyz" })
    ]);

    await act(async () => {
      fireEvent.press(await screen.findByLabelText("contract.xyz"));
    });

    expect(mockOpenDocument).toHaveBeenCalledWith(
      expect.objectContaining({ url: DOCUMENT_ACCESS_URL, title: "contract.xyz" })
    );
  });

  it("says nothing on the card when the file opened", async () => {
    await renderConversation([documentMessage()]);

    await act(async () => {
      fireEvent.press(await screen.findByLabelText(DOCUMENT_FILENAME));
    });

    expect(screen.queryByText(/no longer available|could not/i)).toBeNull();
    expect(screen.getByText(/Tap to open/)).toBeTruthy();
  });

  it("reports that it is working while the file is being fetched", async () => {
    // Opening a document downloads it first. Without this the card is inert for
    // however long that takes, which reads exactly like the original defect.
    let release: (value: { status: string }) => void = () => undefined;
    mockOpenDocument.mockReturnValue(
      new Promise<{ status: string }>((resolve) => {
        release = resolve;
      })
    );
    await renderConversation([documentMessage()]);
    const card = await screen.findByLabelText(DOCUMENT_FILENAME);

    await act(async () => {
      fireEvent.press(card);
    });
    expect(screen.getByText("Opening…")).toBeTruthy();
    expect(screen.getByLabelText(DOCUMENT_FILENAME).props.accessibilityState).toMatchObject({ busy: true });

    await act(async () => {
      release({ status: "opened" });
    });
    expect(screen.queryByText("Opening…")).toBeNull();
  });

  it("does not start a second download while the first is still running", async () => {
    // A download that is taking its time invites a second tap. Each one would be
    // its own request for the same bytes.
    mockOpenDocument.mockReturnValue(new Promise(() => undefined));
    await renderConversation([documentMessage()]);
    const card = await screen.findByLabelText(DOCUMENT_FILENAME);

    await act(async () => {
      fireEvent.press(card);
    });
    await act(async () => {
      fireEvent.press(screen.getByLabelText(DOCUMENT_FILENAME));
    });

    expect(mockOpenDocument).toHaveBeenCalledTimes(1);
  });
});

describe("a video bubble never hands the movie to the image loader", () => {
  it("draws no poster at all when the pipeline has not produced one", async () => {
    // The second defect. An empty preview is a real state, not a missing value:
    // the previous bubble substituted the original, so opening a thread with a
    // 90-minute video in it downloaded the whole video to paint a card a few
    // hundred pixels wide.
    mockGrants.set(VIDEO_MEDIA_ID, { access_url: MOVIE_ACCESS_URL, thumbnail_access_url: "" });
    await renderConversation([videoMessage()]);

    expect(await screen.findByLabelText("Video, tap to play")).toBeTruthy();
    expect(imageUris()).not.toContain(MOVIE_ACCESS_URL);
    // Still a usable control rather than a blank rectangle -- and specifically
    // not the file card, whose two labels were the whole reported defect.
    expect(screen.queryByText("Video attachment")).toBeNull();
    expect(screen.queryByText("Open viewer")).toBeNull();
  });

  it("draws the poster when there is one, and still not the movie", async () => {
    mockGrants.set(VIDEO_MEDIA_ID, { access_url: MOVIE_ACCESS_URL, thumbnail_access_url: POSTER_ACCESS_URL });
    await renderConversation([videoMessage()]);

    await screen.findByLabelText("Video, tap to play");
    expect(imageUris()).toContain(POSTER_ACCESS_URL);
    expect(imageUris()).not.toContain(MOVIE_ACCESS_URL);
  });

  it("asks for one grant per attachment, not one per URL it needs", async () => {
    // The original and its preview are two objects behind one authorization
    // decision. Two grants per bubble is what resolved both slots to the same
    // `/download` URL in the first place.
    mockGrants.set(VIDEO_MEDIA_ID, { access_url: MOVIE_ACCESS_URL, thumbnail_access_url: POSTER_ACCESS_URL });
    await renderConversation([videoMessage()]);

    await screen.findByLabelText("Video, tap to play");
    expect(mockAccessRequests.filter((id) => id === VIDEO_MEDIA_ID)).toHaveLength(1);
    // And never for the transport id, which would 404.
    expect(mockAccessRequests).not.toContain(TRANSPORT_ATTACHMENT_ID);
  });
});

describe("a photo still falls back to the original", () => {
  it("draws the original when no preview was granted", async () => {
    // Deliberately unlike video: a photo's size is bounded by the photo upload
    // limit, and the viewer is about to need those bytes anyway. Making both
    // branches refuse the fallback would turn every pre-pipeline photo into a
    // blank tile.
    mockGrants.set(PHOTO_MEDIA_ID, { access_url: PHOTO_ACCESS_URL, thumbnail_access_url: "" });
    await renderConversation([photoMessage()]);

    // The bubble wrapper carries a summary label that also says "Image
    // attachment", so the photo control is addressed by its role instead.
    await screen.findByRole("imagebutton");
    expect(imageUris()).toContain(PHOTO_ACCESS_URL);
  });

  it("prefers the preview when there is one", async () => {
    const previewUrl = "https://media.pulsesoc.test/grants/photo-small.jpg?token=preview-grant";
    mockGrants.set(PHOTO_MEDIA_ID, { access_url: PHOTO_ACCESS_URL, thumbnail_access_url: previewUrl });
    await renderConversation([photoMessage()]);

    // The bubble wrapper carries a summary label that also says "Image
    // attachment", so the photo control is addressed by its role instead.
    await screen.findByRole("imagebutton");
    expect(imageUris()).toContain(previewUrl);
    // A thread of photos must not download every original at full size to paint
    // cards a few hundred pixels wide.
    expect(imageUris()).not.toContain(PHOTO_ACCESS_URL);
  });
});

describe("three attachments in one thread", () => {
  it("gives each bubble its own grant and its own behaviour", async () => {
    // The bubbles share one renderer, so a fix applied to one branch and not the
    // others is invisible in a single-message test.
    mockGrants.set(DOCUMENT_MEDIA_ID, { access_url: DOCUMENT_ACCESS_URL, thumbnail_access_url: "" });
    mockGrants.set(VIDEO_MEDIA_ID, { access_url: MOVIE_ACCESS_URL, thumbnail_access_url: "" });
    mockGrants.set(PHOTO_MEDIA_ID, { access_url: PHOTO_ACCESS_URL, thumbnail_access_url: "" });
    await renderConversation([documentMessage(), videoMessage(), photoMessage()]);

    await screen.findByLabelText(DOCUMENT_FILENAME);
    expect(mockAccessRequests.slice().sort()).toEqual([DOCUMENT_MEDIA_ID, VIDEO_MEDIA_ID, PHOTO_MEDIA_ID].sort());
    // The photo took its original; the video refused to.
    expect(imageUris()).toContain(PHOTO_ACCESS_URL);
    expect(imageUris()).not.toContain(MOVIE_ACCESS_URL);
    expect(imageUris()).not.toContain(DOCUMENT_ACCESS_URL);

    await act(async () => {
      fireEvent.press(screen.getByLabelText(DOCUMENT_FILENAME));
    });
    expect(mockOpenDocument).toHaveBeenCalledWith(expect.objectContaining({ url: DOCUMENT_ACCESS_URL }));
  });
});
