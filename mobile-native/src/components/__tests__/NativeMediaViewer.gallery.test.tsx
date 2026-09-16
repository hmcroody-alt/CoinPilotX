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
import { NativeMediaViewer, NativeMediaViewerItem, SWIPE_COMMIT_DISTANCE } from "../NativeMediaViewer";

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
