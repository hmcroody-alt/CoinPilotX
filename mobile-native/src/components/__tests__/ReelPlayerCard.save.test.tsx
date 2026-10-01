/**
 * The Reel card's Save control reads the shared store, not the reel payload.
 *
 * A Reel is not only a Reel. The same content is a post in the feed, a row in
 * the Saved collection, and a result in search, and several of those can be
 * mounted at the same time. While this card rendered `reel.saved` it was showing
 * whichever answer its own screen last fetched, so saving from the feed left the
 * Reel viewer still offering Save — the "it does not reliably save" report, seen
 * from the other end: it did save, and this card said otherwise.
 *
 * These tests render the real card rather than a prop recorder, because what
 * needs pinning is the button itself: its label, its selected state, and the
 * fact that it goes quiet while a mutation is in flight.
 */
import React from "react";
import { render } from "@testing-library/react-native";

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn().mockResolvedValue(undefined) },
    Video: ReactActual.forwardRef(() => null)
  };
});
// Both must resolve rather than return undefined: the card chains `.catch()`
// onto them directly, including in an effect cleanup.
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn().mockResolvedValue(true),
  releaseMediaPlayback: jest.fn().mockResolvedValue(undefined)
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
  return {
    LikeBurst: ReactActual.forwardRef(() => null),
    MuteGlyphPulse: ReactActual.forwardRef(() => null)
  };
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

import { markSavePending, observeSavedState, resetSavedStoreForTests } from "../../social/savedStore";
import { ReelPlayerCard } from "../ReelPlayerCard";

const REEL_ID = 88;

function reel(overrides: Record<string, unknown> = {}) {
  return {
    id: REEL_ID,
    reel_id: REEL_ID,
    user_id: 9,
    title: "Reel under test",
    caption: "A reel fixture.",
    video_url: "https://cdn.example/r.mp4",
    poster_url: "https://cdn.example/r.jpg",
    author: { id: 9, user_id: 9, display_name: "Fixture Creator", username: "fixture_creator" },
    reactions_count: 0,
    comments_count: 0,
    media: [],
    ...overrides
  } as any;
}

function renderCard(overrides: Record<string, unknown> = {}) {
  const noop = jest.fn();
  return render(
    <ReelPlayerCard
      reel={reel(overrides)}
      active={false}
      muted
      onToggleMuted={noop}
      onReact={noop}
      onOpenReactions={noop}
      onOpenComments={noop}
      onSave={noop}
      onRepost={noop}
      onShare={noop}
      onNotInterested={noop}
      onReport={noop}
      onFollowCreator={noop}
      onAuthorPress={noop}
      onOpenMusic={noop}
      onOpenMore={noop}
      onJoinLive={noop}
    />
  );
}

beforeEach(() => {
  resetSavedStoreForTests();
});

describe("ReelPlayerCard save control", () => {
  it("seeds from the reel payload when the store has never heard of this Reel", () => {
    const { getByLabelText } = renderCard({ saved: true });
    expect(getByLabelText("Saved").props.accessibilityState.selected).toBe(true);
  });

  it("shows the store's state over a stale payload, so a save made elsewhere is not reverted", () => {
    // The user saved this Reel from the feed card. The Reel viewer's own copy was
    // fetched before that and still says false. Rendering the payload here is
    // exactly how the button appeared to un-save itself on navigation.
    observeSavedState("reel", REEL_ID, true);

    const { getByLabelText, queryByLabelText } = renderCard({ saved: false });

    expect(getByLabelText("Saved")).toBeTruthy();
    expect(queryByLabelText("Save")).toBeNull();
  });

  it("announces the in-flight state and refuses a second tap while it is pending", () => {
    markSavePending("reel", REEL_ID, true);

    const { getByLabelText } = renderCard({ saved: false });
    const button = getByLabelText("Saving");

    expect(button.props.accessibilityState.busy).toBe(true);
    expect(button.props.accessibilityState.disabled).toBe(true);
  });

  it("gives the control a hint that says which direction the tap goes", () => {
    // "Save"/"Saved" alone tells a screen reader user the current state but not
    // the consequence of pressing, which is the opposite of it.
    const unsaved = renderCard({ saved: false });
    expect(unsaved.getByLabelText("Save").props.accessibilityHint).toMatch(/adds this reel/i);
    unsaved.unmount();

    observeSavedState("reel", REEL_ID, true);
    const saved = renderCard({ saved: true });
    expect(saved.getByLabelText("Saved").props.accessibilityHint).toMatch(/removes this reel/i);
  });
});
