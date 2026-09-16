/**
 * The card half of "video and attached music start together".
 *
 * `attachedMusicTimeline.test.ts` proves the arithmetic: given a video position
 * and a track position, it computes the right correction. That module could be
 * perfect and the product still broken, because the defect this mission removed
 * was never in the arithmetic -- there was no arithmetic. It was in the wiring:
 * the track was created inside an effect gated on `ownsPlayback`, which is set
 * from the resolution of an async claim, so the sequence was
 *
 *   claim resolves -> video plays -> React commits -> track begins downloading
 *
 * and the music arrived a render plus a network fetch after the picture.
 *
 * So these tests assert on the imperative calls, not on props. A test that only
 * checked `shouldPlay` would have been green throughout the bug, for the same
 * reason the sibling lifecycle test mocks a real imperative handle.
 */
import React from "react";
import { act, render } from "@testing-library/react-native";

const mockVideoHandle = {
  playAsync: jest.fn().mockResolvedValue(undefined),
  pauseAsync: jest.fn().mockResolvedValue(undefined),
  stopAsync: jest.fn().mockResolvedValue(undefined)
};

/** Captures the Video's props so a test can drive real status ticks through it. */
let videoProps: any = null;

const mockSound = {
  setStatusAsync: jest.fn().mockResolvedValue(undefined),
  pauseAsync: jest.fn().mockResolvedValue(undefined),
  stopAsync: jest.fn().mockResolvedValue(undefined),
  unloadAsync: jest.fn().mockResolvedValue(undefined),
  setOnPlaybackStatusUpdate: jest.fn()
};
const mockCreateAsync = jest.fn();

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: {
      Sound: { createAsync: (...args: any[]) => mockCreateAsync(...args) },
      setAudioModeAsync: jest.fn().mockResolvedValue(undefined)
    },
    Video: ReactActual.forwardRef((props: any, ref: any) => {
      videoProps = props;
      ReactActual.useImperativeHandle(ref, () => mockVideoHandle);
      return null;
    })
  };
});

const mockClaim = jest.fn();
const mockRelease = jest.fn();
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: (...args: any[]) => mockClaim(...args),
  releaseMediaPlayback: (...args: any[]) => mockRelease(...args)
}));

jest.mock("../../media/mediaAccess", () => ({
  canonicalMediaPlaybackUrl: (url: string) => url,
  refreshCanonicalMediaAccess: jest.fn().mockResolvedValue(undefined)
}));
jest.mock("../../media/useTapMuteLike", () => ({
  useTapMuteLike: () => ({ onPress: jest.fn(), onLongPress: jest.fn() })
}));
jest.mock("../../media/MediaGestureFeedback", () => {
  const ReactActual = jest.requireActual("react");
  return { LikeBurst: ReactActual.forwardRef(() => null), MuteGlyphPulse: ReactActual.forwardRef(() => null) };
});
jest.mock("../reels/ReelPhotoSurface", () => ({ ReelPhotoSurface: () => null }));
jest.mock("../reels/ReelCarouselSurface", () => ({ ReelCarouselSurface: () => null }));
jest.mock("../reels/ReelLiveViewerSurface", () => ({ ReelLiveViewerSurface: () => null }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));
jest.mock("../ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return { ContentTranslation: ({ text }: any) => ReactActual.createElement(Text, null, text) };
});

import { resetSavedStoreForTests } from "../../social/savedStore";
import { ReelPlayerCard } from "../ReelPlayerCard";

const REEL_ID = 733;
const TRACK_URL = "https://cdn.example/track.m4a";

function reel(id = REEL_ID) {
  return {
    id,
    reel_id: id,
    user_id: 9,
    title: `Reel ${id}`,
    caption: "A reel with an attached track.",
    video_url: "https://cdn.example/r.mp4",
    poster_url: "https://cdn.example/r.jpg",
    author: { id: 9, user_id: 9, display_name: "Fixture Creator", username: "fixture_creator" },
    reactions_count: 0,
    comments_count: 0,
    media: [],
    audio: {
      attached_audio_url: TRACK_URL,
      audio_start_time: 0,
      audio_volume: 1,
      audio_baked_in: false,
      original_audio_muted: true
    }
  } as any;
}

function cardProps(active: boolean, muted = false) {
  const noop = jest.fn();
  return {
    reel: reel(),
    active,
    muted,
    onToggleMuted: noop,
    onReact: noop,
    onOpenReactions: noop,
    onOpenComments: noop,
    onSave: noop,
    onRepost: noop,
    onShare: noop,
    onNotInterested: noop,
    onReport: noop,
    onFollowCreator: noop,
    onAuthorPress: noop,
    onOpenMusic: noop,
    onOpenMore: noop,
    onJoinLive: noop
  } as any;
}

/** Feed the card a status update the way expo-av would. */
async function videoTick(over: Record<string, unknown>) {
  await act(async () => {
    videoProps?.onPlaybackStatusUpdate?.({
      isLoaded: true,
      positionMillis: 0,
      durationMillis: 15000,
      isPlaying: true,
      isBuffering: false,
      ...over
    });
  });
}

/** Report the track's own state, the way the sound's status callback would. */
function musicTick(over: Record<string, unknown>) {
  const cb = mockSound.setOnPlaybackStatusUpdate.mock.calls.at(-1)?.[0];
  cb?.({ isLoaded: true, positionMillis: 0, isPlaying: false, durationMillis: 30000, ...over });
}

beforeEach(() => {
  resetSavedStoreForTests();
  videoProps = null;
  mockVideoHandle.playAsync.mockClear();
  mockVideoHandle.pauseAsync.mockClear();
  Object.values(mockSound).forEach((fn: any) => fn.mockClear?.());
  mockCreateAsync.mockReset().mockResolvedValue({ sound: mockSound });
  mockClaim.mockReset().mockResolvedValue(true);
  mockRelease.mockReset().mockResolvedValue(undefined);
});

describe("attached music starts with the picture", () => {
  it("loads the track silently instead of waiting to own playback", async () => {
    render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);

    // Created at all, and created NOT playing. `shouldPlay: true` here is the
    // old behaviour: it would make the track audible at whatever position it
    // loaded at, independently of where the video got to.
    expect(mockCreateAsync).toHaveBeenCalledTimes(1);
    expect(mockCreateAsync.mock.calls[0][0]).toEqual({ uri: TRACK_URL });
    expect(mockCreateAsync.mock.calls[0][1]).toMatchObject({ shouldPlay: false });
  });

  it("starts the track at the position the video has already reached", async () => {
    render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);
    musicTick({ positionMillis: 0, isPlaying: false });

    // The video is 900ms in by the time the track is ready -- which is exactly
    // the gap this mission exists to close. The track must enter at 900, not 0.
    await videoTick({ positionMillis: 900, isPlaying: true });

    expect(mockSound.setStatusAsync).toHaveBeenCalledWith(
      expect.objectContaining({ positionMillis: 900, shouldPlay: true })
    );
  });

  it("pauses the track when the video stalls, rather than letting it run ahead", async () => {
    render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);
    musicTick({ positionMillis: 2000, isPlaying: true });

    await videoTick({ positionMillis: 2000, isPlaying: true, isBuffering: true });

    expect(mockSound.pauseAsync).toHaveBeenCalled();
  });

  it("re-enters at the video's position after the stall clears, repaying the gap", async () => {
    render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);

    // The track stopped at 2s; the video resumes at 5s having buffered through.
    musicTick({ positionMillis: 2000, isPlaying: false });
    await videoTick({ positionMillis: 5000, isPlaying: true, isBuffering: false });

    expect(mockSound.setStatusAsync).toHaveBeenCalledWith(
      expect.objectContaining({ positionMillis: 5000, shouldPlay: true })
    );
  });

  it("silences the track the instant the reel is muted, not a tick later", async () => {
    // §11: an explicit mute outranks autoplay. The card is still active and the
    // video is still playing, so the correction loop is still ticking -- and it
    // WOULD eventually pause the track, on its next tick. That is the bug this
    // asserts against: "silent within about 250ms" is not what muting means.
    //
    // This is the case the edge-triggered pause effect uniquely covers. The
    // swipe-away edge looks like the obvious thing to test here and is not: the
    // ownership effect already pauses the track on `active -> false`, so a test
    // written against a swipe stays green with that effect deleted, which is
    // exactly what a mutation run showed.
    const { rerender } = render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);
    musicTick({ positionMillis: 3000, isPlaying: true });
    await videoTick({ positionMillis: 3000, isPlaying: true });
    mockSound.pauseAsync.mockClear();

    await act(async () => {
      rerender(<ReelPlayerCard {...cardProps(true, true)} />);
    });

    expect(mockSound.pauseAsync).toHaveBeenCalled();
  });

  it("silences the track when the reel is swiped away", async () => {
    // §14/§16. Kept even though the mutation run proved the ownership effect is
    // what satisfies it today: the requirement is that a swiped-past reel is
    // silent, not that one particular effect is the thing that silenced it. If
    // that effect is ever refactored, this is what notices.
    const { rerender } = render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);
    musicTick({ positionMillis: 3000, isPlaying: true });
    await videoTick({ positionMillis: 3000, isPlaying: true });
    mockSound.pauseAsync.mockClear();

    await act(async () => {
      rerender(<ReelPlayerCard {...cardProps(false)} />);
    });

    expect(mockSound.pauseAsync).toHaveBeenCalled();
  });

  it("timestamps the track's position so a stale reading is not read as drift", async () => {
    // The card is the half that knows WHEN each reading was taken, and it is
    // the only half that can know: the planner is handed two numbers and has no
    // way to tell that one of them is a quarter-second old.
    //
    // This is the wiring the measurement caught. An instrumented Release build
    // seeked the track four times a second forever, because the video's status
    // arrives on its own interval and the music's position is whatever the
    // sound's slower callback last left behind -- a gap wider than the deadband,
    // so every tick read as drift and every correction republished a reading one
    // tick old.
    //
    // Every other test in this file ticks both clocks inside the same
    // millisecond, which is exactly the condition under which the bug is
    // invisible. So this one moves the clock between them: that is the whole
    // point, and without it a mutation that deletes the card's timestamp
    // survives the entire suite.
    const nowSpy = jest.spyOn(Date, "now");
    try {
      nowSpy.mockReturnValue(1_000_000);
      render(<ReelPlayerCard {...cardProps(true)} />);
      await act(async () => undefined);
      musicTick({ positionMillis: 0, isPlaying: false });
      await videoTick({ positionMillis: 0, isPlaying: true });
      mockSound.setStatusAsync.mockClear();

      // The track reports 2750ms. 250ms later the video reports 3000ms. Those
      // are the same instant seen twice, not a 250ms error.
      nowSpy.mockReturnValue(1_000_000);
      musicTick({ positionMillis: 2750, isPlaying: true });
      nowSpy.mockReturnValue(1_000_250);
      await videoTick({ positionMillis: 3000, isPlaying: true });

      expect(mockSound.setStatusAsync).not.toHaveBeenCalled();
    } finally {
      nowSpy.mockRestore();
    }
  });

  it("does not make a muted reel's track audible", async () => {
    render(<ReelPlayerCard {...cardProps(true, true)} />);
    await act(async () => undefined);
    musicTick({ positionMillis: 0, isPlaying: false });

    await videoTick({ positionMillis: 1000, isPlaying: true });

    // A muted card owns nothing, so the correction loop must never hand it a
    // `shouldPlay: true`. §11: an explicit mute outranks autoplay.
    const started = mockSound.setStatusAsync.mock.calls.some(
      ([arg]: any[]) => arg?.shouldPlay === true
    );
    expect(started).toBe(false);
  });
});
