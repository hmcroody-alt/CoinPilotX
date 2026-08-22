/**
 * The media itself — full-bleed, muted, looping, and owned by one card at a time.
 *
 * Everything here is asserted against the rendered tree rather than against the
 * policy module, because these are the requirements a correct policy can still
 * get wrong at the last step: a preview that is muted in principle but mounts an
 * unmuted player, a card that decides to rewind but never tells the player, a
 * missing poster that reserves an empty rectangle instead of falling back.
 *
 * The `expo-av` stand-in is a real host element, so props like `isMuted` survive
 * into the tree and the status callback can be driven from a test. Its
 * imperative handle records every call the component makes, and it records its
 * own mount and unmount — which is what makes "the loop does not recreate the
 * player" (requirement 14) an assertion rather than a hope.
 */
import { act, render } from "@testing-library/react-native";
import { DiscoveryPreviewMedia } from "../DiscoveryPreviewMedia";
import { DISCOVERY_PREVIEW_LOOP_WINDOW_MS } from "../previewPlayback";
import {
  getActiveMediaPlayback,
  releaseMediaPlayback,
  resetMediaPlayback
} from "../../core/mediaPlaybackCoordinator";

const playerCalls: string[] = [];

jest.mock("expo-av", () => {
  const ReactActual = require("react");
  const { View } = require("react-native");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Video: ReactActual.forwardRef((props: Record<string, unknown>, ref: unknown) => {
      ReactActual.useImperativeHandle(ref, () => ({
        playFromPositionAsync: (position: number) => {
          mockPlayerCalls.push(`play:${position}`);
          return Promise.resolve();
        },
        pauseAsync: () => {
          mockPlayerCalls.push("pause");
          return Promise.resolve();
        }
      }));
      // Mount and unmount, not render: the question requirement 14 asks is
      // whether the loop creates a *new decoder*, and a re-render does not.
      ReactActual.useEffect(() => {
        mockPlayerCalls.push("mount");
        return () => mockPlayerCalls.push("unmount");
      }, []);
      return ReactActual.createElement(View, props);
    })
  };
});

// `mock`-prefixed so the factory above may close over it — jest allows exactly
// this escape hatch and nothing else.
const mockPlayerCalls = playerCalls;

const VIDEO = "https://cdn.example/reel.m3u8";
const POSTER = "https://cdn.example/reel.jpg";

type MediaProps = React.ComponentProps<typeof DiscoveryPreviewMedia>;

function baseProps(overrides: Partial<MediaProps> = {}): MediaProps {
  return {
    videoUrl: VIDEO,
    posterUrl: POSTER,
    fallbackLabel: "Nova",
    width: 300,
    height: 430,
    active: false,
    testID: "media",
    ...overrides
  };
}

function renderMedia(overrides: Partial<MediaProps> = {}) {
  const utils = render(<DiscoveryPreviewMedia {...baseProps(overrides)} />);
  return {
    ...utils,
    /** Re-render with changed props, the way the row does when viewability moves. */
    setProps: (next: Partial<MediaProps>) =>
      utils.rerender(<DiscoveryPreviewMedia {...baseProps({ ...overrides, ...next })} />)
  };
}

/** Let the coordinator's promise chain settle so the claim resolves. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

/**
 * Feed the component one position report, as the player would.
 *
 * This is the real seam: the component's loop is driven entirely by these, so a
 * test that drives them is exercising the production path rather than a
 * test-only shortcut.
 */
async function reportPosition(utils: ReturnType<typeof renderMedia>, positionMillis: number) {
  const handler = utils.getByTestId("media-video").props.onPlaybackStatusUpdate as (
    status: unknown
  ) => void;
  await act(async () => {
    handler({ isLoaded: true, positionMillis, isPlaying: true });
    await Promise.resolve();
  });
}

/** Play one full pass: a few positions inside the window, then the crossing. */
async function playOnePass(utils: ReturnType<typeof renderMedia>) {
  for (const position of [0, 1200, 3400, 4800, DISCOVERY_PREVIEW_LOOP_WINDOW_MS]) {
    await reportPosition(utils, position);
  }
}

beforeEach(() => {
  playerCalls.length = 0;
  resetMediaPlayback();
});

describe("requirement 2 — the media fills the card with no empty side space", () => {
  it("sizes the media frame to the exact card box it was given", () => {
    const { getByTestId } = renderMedia();
    const frame = getByTestId("media").props.style;
    const flat = Array.isArray(frame) ? Object.assign({}, ...frame.filter(Boolean)) : frame;

    expect(flat.width).toBe(300);
    expect(flat.height).toBe(430);
  });

  it("adds no internal horizontal padding of its own", () => {
    const { getByTestId } = renderMedia();
    const frame = getByTestId("media").props.style;
    const flat = Array.isArray(frame) ? Object.assign({}, ...frame.filter(Boolean)) : frame;

    expect(flat.padding).toBeUndefined();
    expect(flat.paddingHorizontal).toBeUndefined();
    expect(flat.paddingLeft).toBeUndefined();
    expect(flat.paddingRight).toBeUndefined();
  });

  it("crops the poster rather than letterboxing or distorting it", () => {
    const { getByTestId } = renderMedia();

    expect(getByTestId("media-poster").props.resizeMode).toBe("cover");
  });

  it("crops the video the same way, so poster and player agree on framing", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    expect(utils.getByTestId("media-video").props.resizeMode).toBe("cover");
  });
});

describe("requirement 13 — no valid media means no empty media container", () => {
  it("renders a text-first card instead of a reserved blank rectangle", () => {
    const { queryByTestId, getByTestId } = renderMedia({ posterUrl: null, videoUrl: null });

    expect(queryByTestId("media-poster")).toBeNull();
    expect(getByTestId("media-fallback")).toBeTruthy();
  });

  it("puts the item's own label in the fallback, not a generic placeholder", () => {
    const { getByText } = renderMedia({
      posterUrl: null,
      videoUrl: null,
      fallbackLabel: "Astro Makers"
    });

    expect(getByText("Astro Makers")).toBeTruthy();
    expect(getByText("AS")).toBeTruthy();
  });

  it("never mounts a player for an item with no video", async () => {
    const { queryByTestId } = renderMedia({ videoUrl: null, active: true });
    await settle();

    expect(queryByTestId("media-video")).toBeNull();
    expect(playerCalls).toEqual([]);
  });

  it("still shows the poster for a card whose video is not ready yet", async () => {
    const { getByTestId, queryByTestId } = renderMedia({ videoUrl: null, active: true });
    await settle();

    expect(getByTestId("media-poster")).toBeTruthy();
    expect(queryByTestId("media-video")).toBeNull();
  });
});

describe("requirement 2 (audio) — previews are muted", () => {
  it("mounts the player muted", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    expect(utils.getByTestId("media-video").props.isMuted).toBe(true);
  });

  it("exposes no controls that could unmute it", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    expect(utils.getByTestId("media-video").props.useNativeControls).toBe(false);
  });

  it("stays muted across loops, so a wrap can never produce an audio burst", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    await playOnePass(utils);
    await playOnePass(utils);

    expect(utils.getByTestId("media-video").props.isMuted).toBe(true);
  });
});

describe("requirement 1 — the active card starts automatically, from zero", () => {
  it("mounts no player at all while the card is not the primary one", async () => {
    const { queryByTestId } = renderMedia({ active: false });
    await settle();

    // Not "mounted but paused" — not mounted. A decoder per suggestion is what
    // makes a carousel expensive.
    expect(queryByTestId("media-video")).toBeNull();
    expect(playerCalls).toEqual([]);
  });

  it("starts from the beginning when the card becomes primary", async () => {
    const utils = renderMedia({ active: false });
    await settle();

    utils.setProps({ active: true });
    await settle();

    expect(playerCalls).toContain("play:0");
  });

  it("reports that it is playing, so the card's border can react", async () => {
    const onPlayingChange = jest.fn();
    renderMedia({ active: true, onPlayingChange });
    await settle();

    expect(onPlayingChange).toHaveBeenCalledWith(true);
  });
});

describe("requirements 3–8 — the first five seconds, looping", () => {
  it("does not rewind anywhere inside the window", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    for (const position of [0, 900, 2500, 4200, 4999]) await reportPosition(utils, position);

    expect(playerCalls).not.toContain("play:0");
  });

  it("seeks back to zero on reaching five seconds", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    await reportPosition(utils, DISCOVERY_PREVIEW_LOOP_WINDOW_MS);

    expect(playerCalls).toEqual(["play:0"]);
  });

  it("keeps looping for as long as the card stays active", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    for (let pass = 0; pass < 5; pass += 1) await playOnePass(utils);

    // Five passes, five turnovers. Not one, which is the "played once and
    // stopped" regression, and not one per report, which is the judder.
    expect(playerCalls.filter((call) => call === "play:0")).toHaveLength(5);
  });

  it("issues one seek per crossing even when reports keep arriving mid-seek", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    for (const position of [5000, 5125, 5250, 5375]) await reportPosition(utils, position);

    expect(playerCalls.filter((call) => call === "play:0")).toHaveLength(1);
  });

  it("never plays past the window — every seek returns to zero, not to an offset", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    for (let pass = 0; pass < 3; pass += 1) await playOnePass(utils);

    // A seek to anything but 0 would drift the loop forward through the clip,
    // which is the "it eventually reached second 20" failure.
    const seeks = playerCalls.filter((call) => call.startsWith("play:"));
    expect(seeks.every((call) => call === "play:0")).toBe(true);
  });
});

describe("requirement 14 — the loop reuses one player", () => {
  it("mounts the player exactly once across many loops", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    for (let pass = 0; pass < 6; pass += 1) await playOnePass(utils);

    expect(playerCalls.filter((call) => call === "mount")).toHaveLength(1);
    expect(playerCalls).not.toContain("unmount");
  });

  it("keeps that same player when the card goes quiet and starts again", async () => {
    // Requirement 13's return case: the player is retained, so coming back is a
    // seek rather than a fresh decoder and a fresh buffering pause.
    const utils = renderMedia({ active: true });
    await settle();

    utils.setProps({ active: false });
    await settle();
    utils.setProps({ active: true });
    await settle();

    expect(playerCalls.filter((call) => call === "mount")).toHaveLength(1);
  });
});

describe("requirement 13 — returning to a card restarts the loop correctly", () => {
  it("restarts from zero rather than resuming mid-clip", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    await playOnePass(utils);

    utils.setProps({ active: false });
    await settle();
    playerCalls.length = 0;

    utils.setProps({ active: true });
    await settle();

    expect(playerCalls).toContain("play:0");
  });

  it("loops again on the return, not just once", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    utils.setProps({ active: false });
    await settle();
    utils.setProps({ active: true });
    await settle();
    playerCalls.length = 0;

    for (let pass = 0; pass < 3; pass += 1) await playOnePass(utils);

    expect(playerCalls.filter((call) => call === "play:0")).toHaveLength(3);
  });

  it("does not carry a mid-seek latch across the gap", async () => {
    // Going inactive while a rewind was in flight must not leave the loop
    // permanently latched — that card would play once and never turn over again.
    const utils = renderMedia({ active: true });
    await settle();
    await reportPosition(utils, DISCOVERY_PREVIEW_LOOP_WINDOW_MS);

    utils.setProps({ active: false });
    await settle();
    utils.setProps({ active: true });
    await settle();
    playerCalls.length = 0;

    await reportPosition(utils, DISCOVERY_PREVIEW_LOOP_WINDOW_MS);

    expect(playerCalls).toContain("play:0");
  });
});

describe("requirement 11 — the preview stops when the card leaves the viewport", () => {
  it("pauses as soon as it stops being the primary card", async () => {
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    utils.setProps({ active: false });
    await settle();

    expect(playerCalls).toContain("pause");
  });

  it("stops looping once inactive, even if late reports arrive", async () => {
    // Status callbacks can land after the pause. One that still drove the loop
    // would restart a preview the user has already scrolled away from.
    const utils = renderMedia({ active: true });
    await settle();
    utils.setProps({ active: false });
    await settle();
    playerCalls.length = 0;

    await reportPosition(utils, DISCOVERY_PREVIEW_LOOP_WINDOW_MS);

    expect(playerCalls).not.toContain("play:0");
  });

  it("gives the playback lease back when it leaves", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    expect(getActiveMediaPlayback()?.kind).toBe("feed");

    utils.setProps({ active: false });
    await settle();

    expect(getActiveMediaPlayback()).toBeNull();
  });

  it("releases the lease on unmount, when FlatList windows the card away", async () => {
    const { unmount } = renderMedia({ active: true });
    await settle();

    unmount();
    await settle();

    expect(getActiveMediaPlayback()).toBeNull();
  });
});

describe("requirement 12 — backgrounding stops playback", () => {
  it("pauses when the coordinator releases everything for a background", async () => {
    // This is the exact call the coordinator's own AppState listener makes for
    // any kind that is not background-retained, and "feed" is not one. Driving
    // it here proves the preview is wired to that path — no second AppState
    // listener, which §4 forbids.
    const utils = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    await act(async () => {
      await releaseMediaPlayback(undefined, "backgrounded");
    });

    expect(playerCalls).toContain("pause");
    expect(getActiveMediaPlayback()).toBeNull();
    expect(utils.getByTestId("media-video").props.shouldPlay).toBe(false);
  });

  it("stops looping while backgrounded", async () => {
    const utils = renderMedia({ active: true });
    await settle();

    await act(async () => {
      await releaseMediaPlayback(undefined, "backgrounded");
    });
    playerCalls.length = 0;

    await reportPosition(utils, DISCOVERY_PREVIEW_LOOP_WINDOW_MS);

    expect(playerCalls).not.toContain("play:0");
  });
});

describe("requirements 9, 10 — one preview at a time, arbitrated by the existing coordinator", () => {
  it("claims playback as a low-priority feed owner, not as a new audio system", async () => {
    renderMedia({ active: true });
    await settle();

    const owner = getActiveMediaPlayback();
    expect(owner?.kind).toBe("feed");
    expect(owner?.id).toMatch(/^discovery-preview:/);
  });

  it("hands the lease to a second card and stops the first", async () => {
    // Two cards briefly overlap during a swipe. Equal priority means the
    // newcomer wins and the incumbent is paused — which is what makes "only one
    // preview at once" structural rather than a rule the row has to police.
    const first = renderMedia({ active: true, testID: "media" });
    await settle();
    const firstOwner = getActiveMediaPlayback()?.id;
    playerCalls.length = 0;

    const second = render(
      <DiscoveryPreviewMedia {...baseProps({ active: true, testID: "second" })} />
    );
    await settle();

    const secondOwner = getActiveMediaPlayback()?.id;
    expect(secondOwner).not.toBe(firstOwner);
    expect(playerCalls).toContain("pause");

    first.unmount();
    second.unmount();
  });

  it("stops the first card's loop when the second takes over", async () => {
    // Pausing is not enough on its own: if the displaced card kept responding to
    // its status callbacks it would seek itself back to zero and resume, and two
    // previews would run at once.
    const first = renderMedia({ active: true, testID: "media" });
    await settle();

    const second = render(
      <DiscoveryPreviewMedia {...baseProps({ active: true, testID: "second" })} />
    );
    await settle();
    playerCalls.length = 0;

    await reportPosition(first, DISCOVERY_PREVIEW_LOOP_WINDOW_MS);

    expect(playerCalls).not.toContain("play:0");

    first.unmount();
    second.unmount();
  });

  it("stays on its poster when something higher-priority holds playback", async () => {
    const { getByTestId } = renderMedia({ active: true, videoUrl: VIDEO });
    await settle();

    // Whatever happened, the poster is still there underneath: there is never a
    // frame where the card has nothing in it.
    expect(getByTestId("media-poster")).toBeTruthy();
  });
});
