/**
 * The viewer's side of the conversation gallery contract.
 *
 * `useConversationMediaGallery` is tested separately and owns *which* item is
 * active. This file tests the other half: that the viewer actually obeys the
 * owner. A viewer that keeps its own private index would pass every hook test
 * in the repo and still open the 17th photo on the 1st, because the hook would
 * be right about the answer and nobody would be reading it.
 *
 * Each test names the §37 mutation it is here to kill.
 */

import { act, fireEvent, render } from "@testing-library/react-native";
import { State } from "react-native-gesture-handler";

const mockPauseAsync = jest.fn().mockResolvedValue(undefined);
const mockPlayAsync = jest.fn().mockResolvedValue(undefined);
/**
 * How many times a player has been CONSTRUCTED, not rendered.
 *
 * Retry's whole job is to make expo-av attempt a source it has already
 * rejected, and expo-av will not do that for a player that stays mounted. A
 * test asserting on the source URL cannot see the difference -- the URL is
 * identical before and after -- so a retry that quietly does nothing passes.
 * Counting mounts is the only thing in a unit test that distinguishes "tried
 * again" from "cleared the panel".
 */
const mockVideoMounts = jest.fn();

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn().mockResolvedValue(undefined) },
    Video: ReactActual.forwardRef((props: any, ref: any) => {
      ReactActual.useImperativeHandle(ref, () => ({
        playAsync: mockPlayAsync,
        pauseAsync: mockPauseAsync,
        stopAsync: jest.fn().mockResolvedValue(undefined),
        setStatusAsync: jest.fn().mockResolvedValue(undefined),
        setPositionAsync: jest.fn().mockResolvedValue(undefined),
        unloadAsync: jest.fn().mockResolvedValue(undefined)
      }));
      ReactActual.useEffect(() => {
        mockVideoMounts(props.source?.uri);
      }, []);
      return ReactActual.createElement("View", { testID: "viewer-video", ...props });
    })
  };
});

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn().mockResolvedValue(true),
  releaseMediaPlayback: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../core/reelsAudioSession", () => ({
  configureReelsAudioSession: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../media/MediaGestureFeedback", () => {
  const ReactActual = jest.requireActual("react");
  return { LikeBurst: ReactActual.forwardRef(() => null) };
});
jest.mock("../../media/mediaActions", () => ({
  saveMediaToGallery: jest.fn().mockResolvedValue({ status: "saved" }),
  shareMedia: jest.fn().mockResolvedValue({ status: "shared" })
}));
jest.mock("../../media/nativeMediaUpload", () => ({
  pollNativeMediaProcessing: jest.fn().mockResolvedValue({ processing_status: "ready" })
}));

import { saveMediaToGallery, shareMedia } from "../../media/mediaActions";
import { FIRST_FRAME_TIMEOUT_MS, NativeMediaViewer, NativeMediaViewerItem, SWIPE_COMMIT_DISTANCE } from "../NativeMediaViewer";

function photo(position: number): NativeMediaViewerItem {
  return {
    id: 5000 + position,
    kind: "image",
    url: `https://cdn.example/photo-${position}.jpg`,
    subtitle: `Photo from Maria Cherie, ${position} of 43`,
    alt: `Photo ${position}`
  };
}

const collection = Array.from({ length: 43 }, (_, position) => photo(position + 1));

/** Drive a horizontal pan the way the gesture handler would. */
function swipe(tree: ReturnType<typeof render>, translationX: number) {
  const stage = tree.UNSAFE_getByType(require("react-native-gesture-handler").PanGestureHandler);
  fireEvent(stage, "handlerStateChange", {
    nativeEvent: { state: State.END, translationX, translationY: 0 }
  });
}

beforeEach(() => {
  mockPauseAsync.mockClear();
  mockPlayAsync.mockClear();
});

describe("a controlled index", () => {
  /** MUTATION §37: "the viewer always opens the first media item". */
  it("renders the item the owner names, not the first one", () => {
    const tree = render(
      <NativeMediaViewer visible items={collection} index={16} onIndexChange={jest.fn()} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    expect(tree.getByTestId("native-media-viewer-image").props.source.uri).toBe("https://cdn.example/photo-17.jpg");
    expect(tree.getByTestId("native-media-viewer-position").props.children).toBe("Photo from Maria Cherie, 17 of 43");
  });

  /**
   * The viewer must not keep the position it is handed. If it copies `index`
   * into state on mount, the owner re-keying after an older page merges in
   * moves the photo underneath the user.
   */
  it("follows the owner when the position changes underneath it", () => {
    const tree = render(
      <NativeMediaViewer visible items={collection} index={16} onIndexChange={jest.fn()} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    tree.rerender(
      <NativeMediaViewer visible items={collection} index={20} onIndexChange={jest.fn()} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    expect(tree.getByTestId("native-media-viewer-image").props.source.uri).toBe("https://cdn.example/photo-21.jpg");
  });

  /** A position past either end must clamp, never render `undefined`. */
  it("clamps an out-of-range position instead of blanking", () => {
    const tree = render(
      <NativeMediaViewer visible items={collection} index={999} onIndexChange={jest.fn()} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    expect(tree.getByTestId("native-media-viewer-image").props.source.uri).toBe("https://cdn.example/photo-43.jpg");
  });
});

describe("swiping", () => {
  it("swipe left asks for the next item and swipe right the previous", () => {
    const onIndexChange = jest.fn();
    const tree = render(
      <NativeMediaViewer visible items={collection} index={16} onIndexChange={onIndexChange} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    swipe(tree, -(SWIPE_COMMIT_DISTANCE + 20));
    expect(onIndexChange).toHaveBeenLastCalledWith(17);
    swipe(tree, SWIPE_COMMIT_DISTANCE + 20);
    expect(onIndexChange).toHaveBeenLastCalledWith(15);
  });

  it("ignores a nudge that never commits", () => {
    const onIndexChange = jest.fn();
    const tree = render(
      <NativeMediaViewer visible items={collection} index={16} onIndexChange={onIndexChange} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    swipe(tree, -(SWIPE_COMMIT_DISTANCE - 10));
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  /**
   * A single-item surface (Marketplace, a profile avatar) must not acquire
   * navigation just because the gallery needed it.
   */
  it("does not navigate a surface that never opted in", () => {
    const onIndexChange = jest.fn();
    const tree = render(
      <NativeMediaViewer visible items={collection} index={16} onIndexChange={onIndexChange} totalCount={43} onClose={jest.fn()} />
    );
    swipe(tree, -(SWIPE_COMMIT_DISTANCE + 40));
    expect(onIndexChange).not.toHaveBeenCalled();
  });

  it("refuses to walk off either end", () => {
    const onIndexChange = jest.fn();
    const first = render(
      <NativeMediaViewer visible items={collection} index={0} onIndexChange={onIndexChange} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    swipe(first, SWIPE_COMMIT_DISTANCE + 20);
    expect(onIndexChange).not.toHaveBeenCalled();

    const last = render(
      <NativeMediaViewer visible items={collection} index={42} onIndexChange={onIndexChange} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    swipe(last, -(SWIPE_COMMIT_DISTANCE + 20));
    expect(onIndexChange).not.toHaveBeenCalled();
  });
});

describe("video playback across a swipe", () => {
  const withVideo = [
    { ...photo(1) },
    { id: 5002, kind: "video" as const, url: "https://cdn.example/clip.mp4", subtitle: "Video from Maria Cherie, 2 of 3" },
    { ...photo(3) }
  ];

  /** MUTATION §37: "a video keeps playing after you swipe away from it". */
  it("pauses the outgoing player when the active item changes", () => {
    const tree = render(
      <NativeMediaViewer visible items={withVideo} index={1} onIndexChange={jest.fn()} totalCount={3} swipeToNavigate onClose={jest.fn()} />
    );
    expect(tree.queryByTestId("viewer-video")).not.toBeNull();
    mockPauseAsync.mockClear();

    tree.rerender(
      <NativeMediaViewer visible={true} items={withVideo} index={2} onIndexChange={jest.fn()} totalCount={3} swipeToNavigate onClose={jest.fn()} />
    );
    expect(mockPauseAsync).toHaveBeenCalled();
  });

  it("pauses when the viewer closes, not only when it unmounts", () => {
    const tree = render(
      <NativeMediaViewer visible items={withVideo} index={1} onIndexChange={jest.fn()} totalCount={3} swipeToNavigate onClose={jest.fn()} />
    );
    mockPauseAsync.mockClear();
    tree.unmount();
    expect(mockPauseAsync).toHaveBeenCalled();
  });
});

describe("media that is gone", () => {
  /**
   * MUTATION §37: "a deleted item shows a black frame / spins forever".
   *
   * The gallery states unavailability by handing over an empty URL, so this is
   * the contract between the two: empty URL in, stated condition out.
   */
  it("states the condition for an item with no URL instead of spinning", () => {
    const gone = [{ id: 7, kind: "image" as const, url: "", subtitle: "Photo from Maria Cherie, 2 of 3 — Not available" }];
    const tree = render(
      <NativeMediaViewer visible items={gone} index={0} onIndexChange={jest.fn()} totalCount={3} swipeToNavigate onClose={jest.fn()} />
    );
    expect(tree.queryByTestId("native-media-viewer-image")).toBeNull();
    expect(tree.getByText("Unsupported media")).toBeTruthy();
    expect(tree.getByTestId("native-media-viewer-position").props.children).toContain("Not available");
  });

  /** §30: the counter and the VoiceOver label are one string, so they cannot drift. */
  it("reads the accessible position out of the subtitle the gallery built", () => {
    const tree = render(
      <NativeMediaViewer visible items={collection} index={11} onIndexChange={jest.fn()} totalCount={43} swipeToNavigate onClose={jest.fn()} />
    );
    expect(tree.getByTestId("native-media-viewer-position").props.children).toBe("Photo from Maria Cherie, 12 of 43");
  });
});

describe("what Save and Share are pointed at", () => {
  /**
   * A streamed video's playback source is not a file.
   *
   * Under Mux's signed playback policy `url` is `.../vod.m3u8?token=<jwt>` — a
   * few hundred bytes of text naming segments. That is exactly what makes the
   * viewer start on a first segment instead of a full transfer, and it is also
   * the reason it cannot be the thing Save to Photos downloads: the transfer
   * *succeeds*, a `.m3u8` lands in the cache, and the photo-library write
   * refuses it. The user is told their library rejected a video that is playing
   * on their screen, and nothing errors anywhere near the cause.
   */
  const streamed: NativeMediaViewerItem = {
    id: 601,
    kind: "video",
    url: "https://stream.mux.com/pb601.m3u8?token=eyJhbGciOi.abc.def",
    downloadUrl: "https://pulsesoc.com/api/messages/media/87/download?mt=grant",
    mimeType: "video/mp4",
    subtitle: "Video from Roody Cherie, 1 of 1"
  };

  function openOn(item: NativeMediaViewerItem) {
    return render(
      <NativeMediaViewer visible items={[item]} index={0} onIndexChange={jest.fn()} totalCount={1} onClose={jest.fn()} />
    );
  }

  beforeEach(() => {
    (saveMediaToGallery as jest.Mock).mockClear();
    (shareMedia as jest.Mock).mockClear();
  });

  /** MUTATION: `url: current.downloadUrl || current.url` -> `url: current.url`. */
  it("saves the file, not the playlist", async () => {
    const tree = openOn(streamed);
    await act(async () => {
      fireEvent.press(tree.getByTestId("native-media-viewer-save-to-photos"));
    });
    expect((saveMediaToGallery as jest.Mock).mock.calls[0][0].url).toBe(streamed.downloadUrl);
  });

  it("shares the file, not the playlist", async () => {
    const tree = openOn(streamed);
    await act(async () => {
      fireEvent.press(tree.getByTestId("native-media-viewer-share"));
    });
    expect((shareMedia as jest.Mock).mock.calls[0][0].url).toBe(streamed.downloadUrl);
  });

  /**
   * The player still gets the manifest. Splitting the two fields is only worth
   * anything if the fast path survives it — a fix that routed playback through
   * the progressive file too would pass the two tests above and silently undo
   * §9/§21.
   */
  it("still plays the manifest", () => {
    const tree = openOn(streamed);
    expect(tree.getByTestId("viewer-video").props.source.uri).toBe(streamed.url);
  });

  /**
   * MUTATION: make `downloadUrl` required, or drop the `|| current.url`.
   *
   * Every producer except conversation video has one URL that is both. They
   * must keep working untouched rather than silently losing Save.
   */
  it("falls back to url for producers that have only one", async () => {
    const tree = openOn({ id: 5, kind: "image", url: "https://cdn.example/photo.jpg", subtitle: "Photo 1 of 1" });
    await act(async () => {
      fireEvent.press(tree.getByTestId("native-media-viewer-save-to-photos"));
    });
    expect((saveMediaToGallery as jest.Mock).mock.calls[0][0].url).toBe("https://cdn.example/photo.jpg");
  });
});

/**
 * The first-frame watchdog, and the difference between saying and concluding.
 *
 * This block exists because of a device run, not a hypothesis. The viewer opened
 * on "Video from ROODY CHERIE, 9 of 10" showing a frame, and fifteen seconds
 * later showed a black "Media unavailable" card -- while the syslog showed the
 * transfer running (282x200, 31x206, 29x302, zero 4xx/5xx) and CoreMedia
 * reported 1335 kbps. Nothing had failed. The watchdog set `failed`, and
 * `kind === "video" && item.url && !failed` unmounted the player and, with it,
 * the poster.
 *
 * So the requirement under test is §3/§4: the content the user already saw in
 * the conversation must survive being tapped, including when loading it is
 * going badly.
 */
describe("a video that is slow rather than broken", () => {
  const slowVideo: NativeMediaViewerItem = {
    id: 601,
    kind: "video",
    url: "https://pulsesoc.com/api/messages/media/87/download?mt=grant",
    thumbnailUrl: "https://cdn.example/poster-601.jpg",
    subtitle: "Video from Roody Cherie, 9 of 10"
  };

  function openSlow(item: NativeMediaViewerItem = slowVideo) {
    return render(
      <NativeMediaViewer visible items={[item]} index={0} onIndexChange={jest.fn()} totalCount={10} onClose={jest.fn()} />
    );
  }

  /** Run past the first-frame deadline without any status ever arriving. */
  function starveThePlayer() {
    act(() => {
      jest.advanceTimersByTime(FIRST_FRAME_TIMEOUT_MS + 100);
    });
  }

  beforeEach(() => {
    jest.useFakeTimers();
    mockVideoMounts.mockClear();
  });
  afterEach(() => jest.useRealTimers());

  /**
   * MUTATION: restore BOTH `&& !failed` and `setSlow(true)` -> `setFailed(true)`.
   *
   * That pair is the code that shipped, and this is the test that describes what
   * it did on device. It is worth being precise about what this one pin does and
   * does not catch, because the mutation battery showed the two halves are
   * coupled: reverting `&& !failed` ALONE leaves this test green, since the
   * watchdog no longer sets `failed` and there is nothing for the condition to
   * bite on. Each half is killed on its own by the error and wording tests
   * below; this one kills the combination, which is the defect that actually
   * existed.
   *
   * Asserting on the overlay's wording would not be enough for any of them -- a
   * black card with the right words on it is still a black card -- so the
   * assertion is that the PLAYER and its POSTER are still there.
   */
  it("keeps the player and its poster on screen when the deadline passes", () => {
    const tree = openSlow();
    starveThePlayer();

    const video = tree.getByTestId("viewer-video");
    expect(video.props.source.uri).toBe(slowVideo.url);
    expect(video.props.posterSource.uri).toBe(slowVideo.thumbnailUrl);
    expect(video.props.usePoster).toBe(true);
    expect(tree.queryByText("The app cannot show this file yet. Open it from its source instead.")).toBeNull();
  });

  /**
   * MUTATION: `setSlow(true)` -> `setFailed(true)` in the watchdog.
   *
   * A deadline passing is the absence of news. Reporting it as "Media
   * unavailable" tells the user their video is gone while it is arriving, and
   * -- because `failed` also offers Retry -- invites them to discard the
   * transfer that was about to finish.
   */
  it("says it is still loading, not that it is unavailable", () => {
    const tree = openSlow();
    starveThePlayer();

    expect(tree.getByTestId("native-media-viewer-condition-title").props.children).toBe("Still loading");
    expect(tree.queryByTestId("native-media-viewer-retry")).toBeNull();
  });

  /**
   * MUTATION: drop `setSlow(false)` from the loaded branch.
   *
   * The notice has to take itself down. Without this the surface is honest for
   * one second and wrong for the rest of the video: a frame is playing under a
   * panel that says it is still loading, and only leaving the item clears it.
   */
  it("clears the notice by itself when the frame finally arrives", () => {
    const tree = openSlow();
    starveThePlayer();
    expect(tree.queryByTestId("native-media-viewer-condition")).not.toBeNull();

    act(() => {
      tree.getByTestId("viewer-video").props.onPlaybackStatusUpdate({
        isLoaded: true,
        isBuffering: false,
        isPlaying: false
      });
    });

    expect(tree.queryByTestId("native-media-viewer-condition")).toBeNull();
  });

  /**
   * A REPORTED failure is a different fact and gets a different surface: it may
   * state unavailability and it may offer Retry, because there is no transfer in
   * flight to discard. What it still may not do is take the poster away.
   */
  it("states unavailability for a reported error, still without going black", () => {
    const tree = openSlow();
    act(() => {
      tree.getByTestId("viewer-video").props.onError();
    });

    expect(tree.getByTestId("native-media-viewer-condition-title").props.children).toBe("Media unavailable");
    expect(tree.getByTestId("viewer-video").props.posterSource.uri).toBe(slowVideo.thumbnailUrl);
    expect(tree.getByTestId("native-media-viewer-retry")).toBeTruthy();
  });

  /**
   * MUTATION: drop `reloadNonce` from the `<Video>` key.
   *
   * Retry has to actually re-attempt, and expo-av will not reload a source it
   * has already rejected while the player stays mounted. The first version of
   * this test asserted that `source.uri` was unchanged across the press, which
   * is true whether or not anything happened -- it survived the mutation. The
   * only observable that separates "tried again" from "cleared the panel" is a
   * second construction of the player.
   */
  it("re-attempts the source on retry rather than only clearing the panel", () => {
    const tree = openSlow();
    expect(mockVideoMounts).toHaveBeenCalledTimes(1);
    act(() => {
      tree.getByTestId("viewer-video").props.onError();
    });

    act(() => {
      fireEvent.press(tree.getByTestId("native-media-viewer-retry"));
    });

    expect(tree.queryByTestId("native-media-viewer-condition")).toBeNull();
    expect(mockVideoMounts).toHaveBeenCalledTimes(2);
    expect(mockVideoMounts).toHaveBeenLastCalledWith(slowVideo.url);
  });

  /**
   * MUTATION: drop `reloadNonce` from the watchdog's dependency array.
   *
   * The ordering here is the whole test, and the first version got it wrong.
   * That version fired the error before ever advancing the clock, so the
   * ORIGINAL timer was still pending; the final `starveThePlayer` fired it and
   * produced "Still loading" whether or not the retry had re-armed anything.
   * The mutation survived.
   *
   * Starving the player first consumes that timer. After it has fired, only a
   * genuinely re-armed deadline can report a second failure to produce a frame
   * -- otherwise a retry that also hangs leaves the user on a silent poster with
   * nothing ever said about it again.
   */
  it("re-arms the deadline on retry, so a second stall is still reported", () => {
    const tree = openSlow();
    starveThePlayer();
    expect(tree.getByTestId("native-media-viewer-condition-title").props.children).toBe("Still loading");

    act(() => {
      tree.getByTestId("viewer-video").props.onError();
    });
    act(() => {
      fireEvent.press(tree.getByTestId("native-media-viewer-retry"));
    });
    expect(tree.queryByTestId("native-media-viewer-condition")).toBeNull();

    starveThePlayer();
    expect(tree.getByTestId("native-media-viewer-condition-title").props.children).toBe("Still loading");
  });

  /**
   * The no-poster case, which is the one the 600/601 backfill has not reached
   * yet. There is nothing to keep on screen, so the panel is all there is -- and
   * it must still be a sentence rather than an unexplained black rectangle.
   */
  it("still states the condition for an item with no poster", () => {
    const tree = openSlow({ ...slowVideo, thumbnailUrl: "" });
    starveThePlayer();

    expect(tree.getByTestId("viewer-video").props.usePoster).toBe(false);
    expect(tree.getByTestId("native-media-viewer-condition-title").props.children).toBe("Still loading");
  });

  /**
   * MUTATION: make the notice's background opaque.
   *
   * Keeping the player mounted buys nothing if the thing drawn over it hides
   * the poster anyway -- that is the same black screen with better copy on it,
   * and every other test in this block would stay green. Translucency is the
   * load-bearing property of this panel, so it is asserted directly. The
   * overlay that fills the stage must not paint at all; the card that carries
   * the words may, but only see-through.
   */
  it("draws the notice over the poster without hiding it", () => {
    const tree = openSlow();
    starveThePlayer();

    const overlay = tree.getByTestId("native-media-viewer-condition");
    const flat = (style: unknown): Record<string, unknown> =>
      Object.assign({}, ...(Array.isArray(style) ? style.flat(9) : [style]).filter(Boolean));

    expect(flat(overlay.props.style).backgroundColor).toBeUndefined();

    const panel = flat(tree.getByTestId("native-media-viewer-condition-panel").props.style);
    const alpha = String(panel.backgroundColor || "").match(/rgba\([^)]*,\s*([0-9.]+)\s*\)/);
    expect(alpha).not.toBeNull();
    expect(Number(alpha![1])).toBeLessThan(1);
  });

  /**
   * MUTATION: drop `setSlow(false)` from the per-item reset.
   *
   * The same class of bug this file already guards for `failed`: per-item load
   * state keyed on identity rather than position. One slow video would otherwise
   * hand its notice to whatever the user swiped to next, so a healthy photo
   * opens under "Still loading" and the thread reads as though everything after
   * the bad video is broken too.
   */
  it("does not carry the notice onto the next item", () => {
    const neighbour = photo(10);
    const tree = render(
      <NativeMediaViewer visible items={[slowVideo, neighbour]} index={0} onIndexChange={jest.fn()} totalCount={2} onClose={jest.fn()} />
    );
    starveThePlayer();
    expect(tree.queryByTestId("native-media-viewer-condition")).not.toBeNull();

    tree.rerender(
      <NativeMediaViewer visible items={[slowVideo, neighbour]} index={1} onIndexChange={jest.fn()} totalCount={2} onClose={jest.fn()} />
    );

    expect(tree.queryByTestId("native-media-viewer-condition")).toBeNull();
    expect(tree.getByTestId("native-media-viewer-image").props.source.uri).toBe(neighbour.url);
  });
});
