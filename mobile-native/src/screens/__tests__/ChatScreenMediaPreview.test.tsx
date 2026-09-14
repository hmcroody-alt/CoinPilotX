/**
 * Photos and videos are media in the thread, not file attachments.
 *
 * The reported defect was a `.MOV` from an iPhone rendering as the words "Video
 * attachment" over "Open viewer", with `81084427942__310C6CDB-....MOV` printed
 * underneath as though the sender had typed it. Nothing was broken in the
 * pipeline: the poster was generated, granted, and even drawn — at 200pt, under
 * two lines of text that never went away.
 *
 * Three separate things produced that card, and each gets its own assertions
 * here because fixing any one of them alone still leaves a file card:
 *
 *   1. the video branch rendered its title and "Open viewer" unconditionally;
 *   2. the bubble printed `message.body`, which the attach flow sets to the
 *      picked file's name because Messenger has no caption field;
 *   3. the access hook parsed two URLs out of a response that also carries the
 *      attachment row, so the renderer had no duration, no dimensions and no
 *      processing state to draw anything better with.
 *
 * The mutation contract is stated per-test below. Broadly: reintroducing the
 * generic card, the filename, or an eager movie download must turn this file
 * red, and the 90-minute case must stay indistinguishable from the 10-second
 * one.
 */

import React from "react";
import { Dimensions, Image } from "react-native";
import { act, render, screen } from "@testing-library/react-native";
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

// Rendered down to the text it receives. The filename assertions below are only
// meaningful if a body that IS rendered would be findable.
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return {
    ContentTranslation: ({ text, textStyle }: { text?: string; textStyle?: unknown }) =>
      ReactActual.createElement(Text, { style: textStyle }, text)
  };
});

const CONVERSATION_ID = 7710;
const VIDEO_MEDIA_ID = 4401;
const PHOTO_MEDIA_ID = 4402;
const TRANSPORT_ATTACHMENT_ID = 9901;

const MOVIE_ACCESS_URL = "https://media.pulsesoc.test/grants/movie.mov?token=movie";
const POSTER_ACCESS_URL = "https://media.pulsesoc.test/grants/movie-poster.jpg?token=poster";
const PHOTO_ACCESS_URL = "https://media.pulsesoc.test/grants/photo.jpg?token=photo";
const PHOTO_PREVIEW_URL = "https://media.pulsesoc.test/grants/photo-preview.jpg?token=preview";

/** The real generated name from the reported thread. */
const UUID_FILENAME = "81084427942__310C6CDB-8DC8-4777-955E-3A62DDA0519D.MOV";

type Grant = {
  access_url: string;
  thumbnail_access_url: string;
  attachment?: Record<string, unknown>;
};

const mockGrants = new Map<number, Grant>();
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
    drainMessengerQueue: jest.fn().mockResolvedValue([]),
    sendMessage: jest.fn(),
    markConversationRead: jest.fn().mockResolvedValue({ ok: true })
  };
});

jest.mock("../../media/mediaActions", () => {
  const actual = jest.requireActual("../../media/mediaActions");
  return {
    ...actual,
    openDocument: jest.fn()
  };
});

import { ChatScreen } from "../ChatScreen";
import { MessengerMessage } from "../../api/messenger";
import { activateLocale } from "../../i18n/engine";
import { invalidateMessengerMediaAccess } from "../../media/messengerMediaAccess";

const METRICS = {
  frame: { x: 0, y: 0, width: 390, height: 844 },
  insets: { top: 0, left: 0, right: 0, bottom: 0 }
};

/**
 * A real phone viewport, pinned.
 *
 * Card width is a percentage of the window, so every size assertion below is
 * meaningless unless the window is a known one. Left to jest-expo's default the
 * window is 750pt wide, the card saturates its max width, and "portrait is
 * taller than landscape" silently becomes untestable — which is exactly how the
 * first draft of this file passed a landscape-shaped portrait clip.
 */
const PHONE_WINDOW = { width: 390, height: 844, scale: 3, fontScale: 1 };

function pinPhoneViewport() {
  jest.spyOn(Dimensions, "get").mockImplementation((dim: string) =>
    dim === "window" || dim === "screen" ? PHONE_WINDOW : ({} as never)
  );
}

function videoMessage(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 501,
    message_id: 501,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "video",
    body: UUID_FILENAME,
    mime_type: "video/quicktime",
    media_upload_id: VIDEO_MEDIA_ID,
    attachment_id: TRANSPORT_ATTACHMENT_ID,
    media_url: `/api/messages/media/${VIDEO_MEDIA_ID}/download`,
    created_at: "2026-09-13T10:01:00Z",
    ...overrides
  } as MessengerMessage;
}

function photoMessage(overrides: Partial<MessengerMessage> = {}): MessengerMessage {
  return {
    id: 502,
    message_id: 502,
    conversation_id: CONVERSATION_ID,
    sender_id: 9,
    sender_display_name: "Fixture Sender",
    is_mine: false,
    message_type: "image",
    body: "IMG_4417.HEIC",
    mime_type: "image/heic",
    media_upload_id: PHOTO_MEDIA_ID,
    media_url: `/api/messages/media/${PHOTO_MEDIA_ID}/download`,
    created_at: "2026-09-13T10:02:00Z",
    ...overrides
  } as MessengerMessage;
}

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

/** The geometry the media frame actually resolved to, as laid out. */
function mediaSurfaceSize(): { width: number; height: number } | null {
  for (const node of screen.UNSAFE_queryAllByType(Image)) {
    const parent = node.parent;
    const style = parent?.props?.style;
    const flat = Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean)) : style || {};
    if (typeof flat.width === "number" && typeof flat.height === "number") {
      return { width: flat.width, height: flat.height };
    }
  }
  return null;
}

beforeAll(async () => {
  await activateLocale("en");
});

beforeEach(() => {
  jest.clearAllMocks();
  pinPhoneViewport();
  mockGrants.clear();
  mockAccessRequests.length = 0;
  for (const id of [VIDEO_MEDIA_ID, PHOTO_MEDIA_ID, TRANSPORT_ATTACHMENT_ID]) {
    invalidateMessengerMediaAccess(id);
  }
});

// =====================================================================
// A video is a poster with a play control, never a file card
// =====================================================================

describe("video messages render as media", () => {
  it("never renders the generic file card's text", async () => {
    // Mutation: restoring either <Text> in the video branch fails here. These
    // are the exact two strings in the reported screenshot.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 822_000, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByLabelText("Video, 13 minutes 42 seconds, tap to play");
    expect(screen.queryByText("Video attachment")).toBeNull();
    expect(screen.queryByText("Open viewer")).toBeNull();
  });

  it("draws the poster and shows the duration off the attachment row", async () => {
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 822_000, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByText("13:42");
    expect(imageUris()).toContain(POSTER_ACCESS_URL);
    // Mutation: a renderer that falls back to the original for a missing poster
    // — or that hands the poster slot the movie — fails here.
    expect(imageUris()).not.toContain(MOVIE_ACCESS_URL);
  });

  it("never hands the movie to the image loader, poster or no poster", async () => {
    // Mutation: `thumbnailUrl || mediaUrl` in the video branch fails here. For a
    // 90-minute file that expression is a multi-gigabyte download to paint a
    // card a few hundred points wide.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: "",
      attachment: { media_type: "video", duration_ms: 45_000, processing_status: "queued" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByLabelText("Video, 45 seconds, tap to play");
    expect(imageUris()).not.toContain(MOVIE_ACCESS_URL);
  });
});

// =====================================================================
// Duration formatting, including the lengths this surface newly allows
// =====================================================================

describe("duration is shown for every length the surface allows", () => {
  it.each([
    [45_000, "0:45"],
    [822_000, "13:42"],
    [4_328_000, "1:12:08"],
    [5_400_000, "1:30:00"]
  ])("formats %ims as %s", async (durationMs, expected) => {
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: durationMs, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    expect(await screen.findByText(expected)).toBeTruthy();
  });

  it("gives a 90-minute video the same card as a 10-second one", async () => {
    // Mutation: any length-based branch that degrades long video to a file card
    // fails here. 90 minutes is the stored-video ceiling for this surface.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 5_400_000, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByText("1:30:00");
    expect(imageUris()).toContain(POSTER_ACCESS_URL);
    expect(imageUris()).not.toContain(MOVIE_ACCESS_URL);
    expect(screen.queryByText("Video attachment")).toBeNull();
  });
});

// =====================================================================
// "No poster yet" and "no poster ever" are different cards
// =====================================================================

describe("poster states stay distinguishable", () => {
  it("says it is still processing while the job is queued", async () => {
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: "",
      attachment: { media_type: "video", duration_ms: 30_000, processing_status: "processing" }
    });
    await renderConversation([videoMessage()]);

    expect(await screen.findByText("Processing video…")).toBeTruthy();
    expect(screen.queryByText("Preview unavailable")).toBeNull();
  });

  it("says the preview is unavailable once the job has failed", async () => {
    // Mutation: collapsing these two states into one placeholder fails here. A
    // permanently posterless video must not claim it is still working.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: "",
      attachment: { media_type: "video", duration_ms: 30_000, processing_status: "failed" }
    });
    await renderConversation([videoMessage()]);

    expect(await screen.findByText("Preview unavailable")).toBeTruthy();
    expect(screen.queryByText("Processing video…")).toBeNull();
  });

  it("still offers playback while the poster is being made", async () => {
    // The asset is playable before its poster exists, so the control stays up.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: "",
      attachment: { media_type: "video", duration_ms: 30_000, processing_status: "queued" }
    });
    await renderConversation([videoMessage()]);

    expect(await screen.findByLabelText("Video, 30 seconds, tap to play")).toBeTruthy();
  });
});

// =====================================================================
// The generated filename is not the message
// =====================================================================

describe("generated filenames stay out of the bubble", () => {
  it("does not print the UUID filename under a video", async () => {
    // Mutation: restoring `displayMessageBody` to `message.body` fails here.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 822_000, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByText("13:42");
    expect(screen.queryByText(UUID_FILENAME)).toBeNull();
  });

  it("does not print the filename under a photo", async () => {
    mockGrants.set(PHOTO_MEDIA_ID, {
      access_url: PHOTO_ACCESS_URL,
      thumbnail_access_url: PHOTO_PREVIEW_URL,
      attachment: { media_type: "photo", width: 3024, height: 4032, processing_status: "ready" }
    });
    await renderConversation([photoMessage()]);

    await act(async () => {
      await Promise.resolve();
    });
    expect(screen.queryByText("IMG_4417.HEIC")).toBeNull();
  });

  it("keeps a real typed caption that merely contains a dot", async () => {
    // The filename rule must not eat messages. Only a bare single-token name
    // with an extension is suppressed.
    mockGrants.set(PHOTO_MEDIA_ID, {
      access_url: PHOTO_ACCESS_URL,
      thumbnail_access_url: PHOTO_PREVIEW_URL,
      attachment: { media_type: "photo", width: 3024, height: 4032, processing_status: "ready" }
    });
    await renderConversation([photoMessage({ body: "look at this. amazing" })]);

    expect(await screen.findByText("look at this. amazing")).toBeTruthy();
  });

  it("still shows a document's filename, which is its content", async () => {
    // Mutation: suppressing the body for every attachment type fails here.
    mockGrants.set(PHOTO_MEDIA_ID, { access_url: PHOTO_ACCESS_URL, thumbnail_access_url: "" });
    await renderConversation([
      photoMessage({ message_type: "file", body: "Deployment gear list.pdf", mime_type: "application/pdf" })
    ]);

    // A document says its own name twice — once as the bubble body, once as the
    // card title — and did so before this change too. The assertion is "at
    // least one survives", not "exactly one", so it pins the suppression rule
    // without quietly adopting the duplication as intended behaviour.
    expect((await screen.findAllByText("Deployment gear list.pdf")).length).toBeGreaterThan(0);
  });
});

// =====================================================================
// Wide, and shaped like the media itself
// =====================================================================

describe("media cards are wide and use the real aspect ratio", () => {
  it("spans most of the conversation width rather than a fixed 200pt", async () => {
    // Mutation: restoring the fixed `width: 200` / `width: 220` styles fails
    // here. 390pt window -> 78% is ~304pt.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 60_000, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByText("1:00");
    const size = mediaSurfaceSize();
    expect(size).not.toBeNull();
    expect(size!.width).toBeGreaterThan(260);
  });

  it("gives a portrait clip a taller card than a landscape one", async () => {
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 60_000, width: 1080, height: 1920, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);
    await screen.findByText("1:00");
    const portrait = mediaSurfaceSize();

    expect(portrait).not.toBeNull();
    // Mutation: a renderer that ignores the reported dimensions and always uses
    // one ratio fails here — 16:9 into a 304pt card is ~171pt tall.
    expect(portrait!.height).toBeGreaterThan(portrait!.width);
  });

  it("bounds a very tall image so one photo cannot own the thread", async () => {
    mockGrants.set(PHOTO_MEDIA_ID, {
      access_url: PHOTO_ACCESS_URL,
      thumbnail_access_url: PHOTO_PREVIEW_URL,
      attachment: { media_type: "photo", width: 600, height: 4000, processing_status: "ready" }
    });
    await renderConversation([photoMessage()]);

    await act(async () => {
      await Promise.resolve();
    });
    const size = mediaSurfaceSize();
    expect(size).not.toBeNull();
    expect(size!.height).toBeLessThanOrEqual(380);
  });
});

// =====================================================================
// Photos
// =====================================================================

describe("photo messages render the preview rendition", () => {
  it("draws the preview rather than the original when one exists", async () => {
    mockGrants.set(PHOTO_MEDIA_ID, {
      access_url: PHOTO_ACCESS_URL,
      thumbnail_access_url: PHOTO_PREVIEW_URL,
      attachment: { media_type: "photo", width: 3024, height: 4032, processing_status: "ready" }
    });
    await renderConversation([photoMessage()]);

    await act(async () => {
      await Promise.resolve();
    });
    expect(imageUris()).toContain(PHOTO_PREVIEW_URL);
    expect(imageUris()).not.toContain(PHOTO_ACCESS_URL);
  });

  it("falls back to the original when the pipeline produced no preview", async () => {
    // Deliberately unlike video: a photo is bounded by the photo size cap and
    // the viewer is about to need those bytes anyway.
    mockGrants.set(PHOTO_MEDIA_ID, {
      access_url: PHOTO_ACCESS_URL,
      thumbnail_access_url: "",
      attachment: { media_type: "photo", width: 3024, height: 4032, processing_status: "queued" }
    });
    await renderConversation([photoMessage()]);

    await act(async () => {
      await Promise.resolve();
    });
    expect(imageUris()).toContain(PHOTO_ACCESS_URL);
  });
});

// =====================================================================
// Authorization is unchanged
// =====================================================================

describe("media stays behind the access grant", () => {
  it("asks the access endpoint for the foundation id and never the transport id", async () => {
    // Mutation: rendering a raw `/download` path, or asking for the transport
    // row id, fails here. The grant is the only authorization decision.
    mockGrants.set(VIDEO_MEDIA_ID, {
      access_url: MOVIE_ACCESS_URL,
      thumbnail_access_url: POSTER_ACCESS_URL,
      attachment: { media_type: "video", duration_ms: 60_000, width: 1920, height: 1080, processing_status: "ready" }
    });
    await renderConversation([videoMessage()]);

    await screen.findByText("1:00");
    expect(mockAccessRequests).toContain(VIDEO_MEDIA_ID);
    expect(mockAccessRequests).not.toContain(TRANSPORT_ATTACHMENT_ID);
    expect(mockAccessRequests.filter((id) => id === VIDEO_MEDIA_ID)).toHaveLength(1);
    for (const uri of imageUris()) {
      expect(uri).not.toMatch(/\/api\/messages\/media\/\d+\/download$/);
    }
  });
});
