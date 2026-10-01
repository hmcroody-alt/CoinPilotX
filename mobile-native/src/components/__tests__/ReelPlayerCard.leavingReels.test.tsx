/**
 * The card half of "stop playing the moment the user leaves Reels".
 *
 * `ReelsScreen.playbackLifecycle.test.tsx` proves the screen stops asking for
 * playback on blur. This file proves the card obeys — that `active: false` is
 * not merely a prop change but an immediate `pauseAsync()` plus a surrendered
 * coordinator claim, and that nothing can put the reel back afterwards.
 *
 * The Video mock exposes a real imperative handle rather than rendering null,
 * because the whole defect lived in imperative calls: the element ships
 * `shouldPlay={false}` and is driven entirely by `playAsync()`/`pauseAsync()`,
 * so a test that only inspected props would have been green throughout the bug.
 */
import React from "react";
import { act, render } from "@testing-library/react-native";

const mockVideoHandle = {
  playAsync: jest.fn().mockResolvedValue(undefined),
  pauseAsync: jest.fn().mockResolvedValue(undefined),
  stopAsync: jest.fn().mockResolvedValue(undefined)
};

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn().mockResolvedValue(undefined) },
    Video: ReactActual.forwardRef((_props: any, ref: any) => {
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

const REEL_ID = 501;

function reel(id = REEL_ID) {
  return {
    id,
    reel_id: id,
    user_id: 9,
    title: `Reel ${id}`,
    caption: "A reel fixture.",
    video_url: "https://cdn.example/r.mp4",
    poster_url: "https://cdn.example/r.jpg",
    author: { id: 9, user_id: 9, display_name: "Fixture Creator", username: "fixture_creator" },
    reactions_count: 0,
    comments_count: 0,
    media: []
  } as any;
}

function cardProps(active: boolean, id = REEL_ID) {
  const noop = jest.fn();
  return {
    reel: reel(id),
    active,
    muted: false,
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

beforeEach(() => {
  resetSavedStoreForTests();
  mockVideoHandle.playAsync.mockClear();
  mockVideoHandle.pauseAsync.mockClear();
  mockVideoHandle.stopAsync.mockClear();
  mockClaim.mockReset().mockResolvedValue(true);
  mockRelease.mockReset().mockResolvedValue(undefined);
});

describe("ReelPlayerCard playback ownership", () => {
  it("plays and claims the shared coordinator while it is the active reel", async () => {
    render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);

    expect(mockClaim).toHaveBeenCalledTimes(1);
    expect(mockClaim.mock.calls[0][0]).toMatchObject({ id: `reel:${REEL_ID}`, kind: "reel" });
    expect(mockVideoHandle.playAsync).toHaveBeenCalled();
  });

  it("pauses and releases the instant it stops being active", async () => {
    const view = render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);
    mockVideoHandle.pauseAsync.mockClear();
    mockRelease.mockClear();

    // This is the navigation blur, as the card sees it.
    await act(async () => {
      view.rerender(<ReelPlayerCard {...cardProps(false)} />);
    });

    expect(mockVideoHandle.pauseAsync).toHaveBeenCalled();
    expect(mockRelease).toHaveBeenCalledWith(`reel:${REEL_ID}`);
  });

  it("never starts a preloaded neighbour — PRELOAD is not PLAY", async () => {
    render(<ReelPlayerCard {...cardProps(false, 777)} />);
    await act(async () => undefined);

    expect(mockClaim).not.toHaveBeenCalled();
    expect(mockVideoHandle.playAsync).not.toHaveBeenCalled();
  });

  it("does not resurrect the reel when a claim resolves after the user has left", async () => {
    // The coordinator awaits the outgoing owner's pause() before it answers, so
    // "yes, you may play" can arrive after the user is already on Home. Without
    // the generation guard this resolution called playAsync() on a screen that
    // was no longer visible — audio with no video anywhere to explain it.
    let grant: (value: boolean) => void = () => undefined;
    mockClaim.mockImplementation(() => new Promise<boolean>((resolve) => { grant = resolve; }));

    const view = render(<ReelPlayerCard {...cardProps(true)} />);
    await act(async () => undefined);
    expect(mockVideoHandle.playAsync).not.toHaveBeenCalled();

    await act(async () => {
      view.rerender(<ReelPlayerCard {...cardProps(false)} />);
    });
    mockRelease.mockClear();

    await act(async () => {
      grant(true);
      await Promise.resolve();
    });

    expect(mockVideoHandle.playAsync).not.toHaveBeenCalled();
    expect(mockRelease).toHaveBeenCalledWith(`reel:${REEL_ID}`);
  });
});
