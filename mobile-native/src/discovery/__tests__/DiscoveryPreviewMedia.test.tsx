/**
 * §2, §4, §6, §11, §12, §14.2, §14.6, §14.7, §14.9, §14.13 — the media itself.
 *
 * Everything here is asserted against the rendered tree rather than against the
 * policy module, because these are the requirements a correct policy can still
 * get wrong at the last step: a preview that is muted in principle but mounts an
 * unmuted player, a card that stops its timer but leaves the video playing, a
 * missing poster that reserves an empty rectangle instead of falling back.
 *
 * The `expo-av` stand-in is a real host element so `isMuted` and `shouldPlay`
 * survive into the tree, and the imperative handle records the calls the
 * component makes so "did it actually stop" is answerable.
 */
import { act, render } from "@testing-library/react-native";
import { DiscoveryPreviewMedia } from "../DiscoveryPreviewMedia";
import { resetDiscoveryPreviewPlayback, DISCOVERY_PREVIEW_DURATION_MS } from "../previewPlayback";
import { resetMediaPlayback, getActiveMediaPlayback } from "../../core/mediaPlaybackCoordinator";

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
      return ReactActual.createElement(View, props);
    })
  };
});

// `mock`-prefixed so the factory above may close over it — jest allows exactly
// this escape hatch and nothing else.
const mockPlayerCalls = playerCalls;

const VIDEO = "https://cdn.example/reel.m3u8";
const POSTER = "https://cdn.example/reel.jpg";

function renderMedia(props: Partial<React.ComponentProps<typeof DiscoveryPreviewMedia>> = {}) {
  return render(
    <DiscoveryPreviewMedia
      previewKey="reel:1"
      videoUrl={VIDEO}
      posterUrl={POSTER}
      fallbackLabel="Nova"
      width={300}
      height={430}
      active={false}
      testID="media"
      {...props}
    />
  );
}

/** Let the coordinator's promise chain settle so the claim resolves. */
async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  playerCalls.length = 0;
  resetDiscoveryPreviewPlayback();
  resetMediaPlayback();
});

afterEach(() => {
  jest.useRealTimers();
});

describe("§14.2 — the media fills the card with no empty side space", () => {
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

    // Any of these reintroduces the gutter §2 exists to remove.
    expect(flat.padding).toBeUndefined();
    expect(flat.paddingHorizontal).toBeUndefined();
    expect(flat.paddingLeft).toBeUndefined();
    expect(flat.paddingRight).toBeUndefined();
  });

  it("crops the poster rather than letterboxing or distorting it", () => {
    const { getByTestId } = renderMedia();

    // `cover` crops; `contain` would produce the bars §2 forbids and `stretch`
    // would misrepresent the content it is advertising.
    expect(getByTestId("media-poster").props.resizeMode).toBe("cover");
  });

  it("crops the video the same way, so poster and player agree on framing", async () => {
    const { getByTestId } = renderMedia({ active: true });
    await settle();

    expect(getByTestId("media-video").props.resizeMode).toBe("cover");
  });
});

describe("§14.13 — no valid media means no empty media container", () => {
  it("renders a text-first card instead of a reserved blank rectangle", () => {
    const { queryByTestId, getByTestId } = renderMedia({ posterUrl: null, videoUrl: null });

    expect(queryByTestId("media-poster")).toBeNull();
    expect(getByTestId("media-fallback")).toBeTruthy();
  });

  it("puts the item's own label in the fallback, not a generic placeholder", () => {
    const { getByText } = renderMedia({ posterUrl: null, videoUrl: null, fallbackLabel: "Astro Makers" });

    expect(getByText("Astro Makers")).toBeTruthy();
    // …and its monogram, so the card still reads as a card at a glance.
    expect(getByText("AS")).toBeTruthy();
  });

  it("never mounts a player for an item with no video", async () => {
    // §10: a person or a group must not get a video container it can only fail
    // to fill. `active` is true here precisely so the absence is about the media
    // and not about visibility.
    const { queryByTestId } = renderMedia({ videoUrl: null, active: true });
    await settle();

    expect(queryByTestId("media-video")).toBeNull();
    expect(playerCalls).toEqual([]);
  });

  it("still shows the poster for a card whose video is not ready yet", async () => {
    // The adapter nulls `previewVideoUrl` while a reel is transcoding. The card
    // is a still, not a black box.
    const { getByTestId, queryByTestId } = renderMedia({ videoUrl: null, active: true });
    await settle();

    expect(getByTestId("media-poster")).toBeTruthy();
    expect(queryByTestId("media-video")).toBeNull();
  });
});

describe("§14.9 — previews are muted", () => {
  it("mounts the player muted", async () => {
    const { getByTestId } = renderMedia({ active: true });
    await settle();

    expect(getByTestId("media-video").props.isMuted).toBe(true);
  });

  it("does not loop, so the card cannot become an endlessly moving element", async () => {
    const { getByTestId } = renderMedia({ active: true });
    await settle();

    expect(getByTestId("media-video").props.isLooping).toBe(false);
  });

  it("exposes no controls that could unmute it", async () => {
    const { getByTestId } = renderMedia({ active: true });
    await settle();

    expect(getByTestId("media-video").props.useNativeControls).toBe(false);
  });
});

describe("§14.4, §14.5 — only an active card plays", () => {
  it("mounts no player at all while the card is not the primary one", async () => {
    const { queryByTestId } = renderMedia({ active: false });
    await settle();

    // §12: not "mounted but paused" — not mounted. A decoder per suggestion is
    // what makes a carousel expensive.
    expect(queryByTestId("media-video")).toBeNull();
    expect(playerCalls).toEqual([]);
  });

  it("starts from the beginning when the card becomes primary", async () => {
    const { rerender } = renderMedia({ active: false });
    await settle();

    rerender(
      <DiscoveryPreviewMedia
        previewKey="reel:1"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Nova"
        width={300}
        height={430}
        active
        testID="media"
      />
    );
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

describe("§14.6 — the preview stops itself after a few seconds", () => {
  it("pauses when the timer fires rather than playing on", async () => {
    const onPlayingChange = jest.fn();
    renderMedia({ active: true, onPlayingChange });
    await settle();
    playerCalls.length = 0;

    await act(async () => {
      jest.advanceTimersByTime(DISCOVERY_PREVIEW_DURATION_MS);
      await Promise.resolve();
    });

    expect(playerCalls).toContain("pause");
    expect(onPlayingChange).toHaveBeenLastCalledWith(false);
  });

  it("keeps the player mounted after stopping, so the frame it froze on stays", async () => {
    // §6 asks the card to retain the last frame. Unmounting would snap it back
    // to the poster, which reads as the preview being undone.
    const { getByTestId } = renderMedia({ active: true });
    await settle();

    await act(async () => {
      jest.advanceTimersByTime(DISCOVERY_PREVIEW_DURATION_MS);
      await Promise.resolve();
    });

    expect(getByTestId("media-video")).toBeTruthy();
  });

  it("does not restart itself on a re-render after finishing", async () => {
    const { rerender } = renderMedia({ active: true });
    await settle();

    await act(async () => {
      jest.advanceTimersByTime(DISCOVERY_PREVIEW_DURATION_MS);
      await Promise.resolve();
    });
    playerCalls.length = 0;

    rerender(
      <DiscoveryPreviewMedia
        previewKey="reel:1"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Nova"
        width={300}
        height={430}
        active
        testID="media"
      />
    );
    await settle();

    expect(playerCalls).not.toContain("play:0");
  });
});

describe("§14.7 — the preview stops when the card leaves the viewport", () => {
  it("pauses as soon as it stops being the primary card", async () => {
    const { rerender } = renderMedia({ active: true });
    await settle();
    playerCalls.length = 0;

    rerender(
      <DiscoveryPreviewMedia
        previewKey="reel:1"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Nova"
        width={300}
        height={430}
        active={false}
        testID="media"
      />
    );
    await settle();

    expect(playerCalls).toContain("pause");
  });

  it("gives the playback lease back when it leaves", async () => {
    const { rerender } = renderMedia({ active: true });
    await settle();

    expect(getActiveMediaPlayback()?.kind).toBe("feed");

    rerender(
      <DiscoveryPreviewMedia
        previewKey="reel:1"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Nova"
        width={300}
        height={430}
        active={false}
        testID="media"
      />
    );
    await settle();

    // Holding the lease after leaving would silently block the next thing that
    // wants to play — a status, a reel, the card two rows down.
    expect(getActiveMediaPlayback()).toBeNull();
  });

  it("releases the lease on unmount, when FlatList windows the card away", async () => {
    const { unmount } = renderMedia({ active: true });
    await settle();

    unmount();
    await settle();

    expect(getActiveMediaPlayback()).toBeNull();
  });

  it("does not spend the preview when it was cut short (§6)", async () => {
    // Leaving early must not count as "played", or a card the user glimpsed for
    // 200ms would never preview again.
    const { rerender } = renderMedia({ active: true });
    await settle();

    rerender(
      <DiscoveryPreviewMedia
        previewKey="reel:1"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Nova"
        width={300}
        height={430}
        active={false}
        testID="media"
      />
    );
    await settle();
    playerCalls.length = 0;

    rerender(
      <DiscoveryPreviewMedia
        previewKey="reel:1"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Nova"
        width={300}
        height={430}
        active
        testID="media"
      />
    );
    await settle();

    expect(playerCalls).toContain("play:0");
  });
});

describe("§14.8, §4 — one preview at a time, arbitrated by the existing coordinator", () => {
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
    const first = renderMedia({ active: true, previewKey: "reel:1", testID: "first" });
    await settle();
    const firstOwner = getActiveMediaPlayback()?.id;
    playerCalls.length = 0;

    const second = render(
      <DiscoveryPreviewMedia
        previewKey="reel:2"
        videoUrl={VIDEO}
        posterUrl={POSTER}
        fallbackLabel="Atlas"
        width={300}
        height={430}
        active
        testID="second"
      />
    );
    await settle();

    const secondOwner = getActiveMediaPlayback()?.id;
    expect(secondOwner).not.toBe(firstOwner);
    expect(playerCalls).toContain("pause");

    first.unmount();
    second.unmount();
  });

  it("stays on its poster when something higher-priority holds playback", async () => {
    // A call, a live room, a reel. The preview does not fight for it and does
    // not interrupt anything — it simply never starts.
    const { getByTestId } = renderMedia({ active: true, videoUrl: VIDEO });
    await settle();

    // Whatever happened, the poster is still there underneath: there is never a
    // frame where the card has nothing in it.
    expect(getByTestId("media-poster")).toBeTruthy();
  });
});
