/**
 * The chip that sits on someone's video, and the four ways it could misbehave.
 *
 * Reels is the surface with the least room for error: the placement shares the
 * frame with something the user chose and is actively watching. `reelSlots`
 * decides *whether* a chip exists; this component decides what it does once it
 * does, and four of those behaviours are load-bearing:
 *
 *   - **It goes away on its own.** A product suggestion that sits on a video for
 *     the whole watch is an advert with no close button, whatever it is
 *     labelled. So it folds after `IGNORED_MS` of being visible and ignored.
 *
 *   - **Folding on its own reports nothing.** This is the one worth stating
 *     twice. Auto-collapse is *not* feedback: the user kept watching their
 *     video, which is not an opinion about a product. Writing a `see_fewer`
 *     there would train the model on a preference nobody expressed, and it would
 *     do it silently, at scale, on the majority of placements.
 *
 *   - **The timer only runs while the reel is being watched.** A chip on a reel
 *     the user swiped past mid-countdown must still get its full moment if they
 *     swipe back — otherwise the chip is gone before it was ever seen, and the
 *     impression accounting says it was shown.
 *
 *   - **It cannot touch playback.** Asserted structurally, by the component
 *     having no playback prop in scope at all, rather than by reading the file
 *     and promising.
 */
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";
import { ReelsCommerceChip } from "../ReelsCommerceChip";
import {
  recordCommerceEngagement,
  recordCommerceImpression,
  type CommerceFeedbackAction,
  type CommercePlacement
} from "../../api/commerceDiscovery";

jest.mock("../../api/commerceDiscovery", () => ({
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true))
}));

// Keys echoed back, so these assertions are about *which* key was chosen rather
// than about English. The catalog's completeness is the i18n validator's job.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

// Reduced motion by default so a fold resolves synchronously. The animated path
// gets its own test, where the delay is the point.
let mockReducedMotion = true;
jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => mockReducedMotion
}));

const impression = recordCommerceImpression as jest.MockedFunction<typeof recordCommerceImpression>;
const engagement = recordCommerceEngagement as jest.MockedFunction<typeof recordCommerceEngagement>;

/** Mirrors `IGNORED_MS` in the component. */
const IGNORED_MS = 8000;

function placement(overrides: Partial<CommercePlacement> = {}): CommercePlacement {
  const { product: productOverrides, ...rest } = overrides as Partial<CommercePlacement> & {
    product?: Partial<CommercePlacement["product"]>;
  };
  return {
    placementId: "p1",
    impressionToken: "tok-1",
    surface: "reels",
    slot: 0,
    score: 0.71,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reasonKey: "commerce:discovery.why.interest",
    ...rest,
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120,
      ...(productOverrides || {})
    }
  } as CommercePlacement;
}

type Harness = {
  onFeedback: jest.Mock<void, [CommercePlacement, CommerceFeedbackAction]>;
  navigate: jest.Mock;
};

function renderChip(props: Partial<React.ComponentProps<typeof ReelsCommerceChip>> = {}) {
  const harness: Harness = { onFeedback: jest.fn(), navigate: jest.fn() };
  const view = render(
    <ReelsCommerceChip
      placement={placement()}
      isActive
      visibleDwellMs={1000}
      navigation={{ navigate: harness.navigate }}
      onFeedback={harness.onFeedback}
      {...props}
    />
  );
  return { ...view, ...harness };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockReducedMotion = true;
});

describe("ReelsCommerceChip — what it shows", () => {
  it("renders the product, the store and the price in one compact row", () => {
    const { getByText } = renderChip();
    expect(getByText("Women's Casual Sneakers")).toBeTruthy();
    expect(getByText("$49.99")).toBeTruthy();
  });

  it("labels an organic placement as a recommendation and never as an ad", () => {
    // The one failure in this system with a legal shape rather than a UX one.
    // Reels serves organic and house placements only.
    const { getByText, queryByText } = renderChip();
    expect(getByText("commerce:discovery.label.recommended")).toBeTruthy();
    expect(queryByText(/sponsored/i)).toBeNull();
    expect(queryByText(/^ad$/i)).toBeNull();
  });

  it("uses the server's label key rather than one of its own", () => {
    const { getByText } = renderChip({
      placement: placement({ promotionClass: "house", labelKey: "commerce:discovery.label.trending" })
    });
    expect(getByText("commerce:discovery.label.trending")).toBeTruthy();
  });

  it("renders nothing at all for a product with no title", () => {
    // Half a chip on someone's video is worse than no chip.
    const { queryByTestId } = renderChip({ placement: placement({ product: { title: "" } } as never) });
    expect(queryByTestId("reels-commerce-body")).toBeNull();
  });

  it("announces the whole chip as one button rather than four loose texts", () => {
    const { getByTestId } = renderChip();
    const body = getByTestId("reels-commerce-body");
    expect(body.props.accessibilityRole).toBe("button");
    expect(body.props.accessibilityLabel).toContain("Women's Casual Sneakers");
    expect(body.props.accessibilityLabel).toContain("M&W Store");
  });
});

describe("ReelsCommerceChip — it disappears on its own, and says nothing when it does", () => {
  it("folds itself away after being visible and ignored", () => {
    jest.useFakeTimers();
    try {
      const { queryByTestId } = renderChip();
      expect(queryByTestId("reels-commerce-body")).toBeTruthy();
      act(() => {
        jest.advanceTimersByTime(IGNORED_MS + 10);
      });
      expect(queryByTestId("reels-commerce-body")).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });

  it("reports no feedback when it folds on its own", () => {
    // "Kept watching their video" is not a preference. Writing one here would
    // poison the model with an opinion nobody expressed — quietly, and on the
    // majority of placements, since most are ignored.
    jest.useFakeTimers();
    try {
      const { onFeedback } = renderChip();
      act(() => {
        jest.advanceTimersByTime(IGNORED_MS + 10);
      });
      expect(onFeedback).not.toHaveBeenCalled();
    } finally {
      jest.useRealTimers();
    }
  });

  it("does not run the ignore timer on a reel nobody is watching", () => {
    // The pager keeps neighbours mounted. A countdown that ran on them would
    // retire a chip before its reel ever reached the screen.
    jest.useFakeTimers();
    try {
      const { queryByTestId } = renderChip({ isActive: false });
      act(() => {
        jest.advanceTimersByTime(IGNORED_MS * 3);
      });
      expect(queryByTestId("reels-commerce-body")).toBeTruthy();
    } finally {
      jest.useRealTimers();
    }
  });

  it("gives a chip its full moment when the user swipes back to it", () => {
    jest.useFakeTimers();
    try {
      const { rerender, queryByTestId, navigate, onFeedback } = renderChip();
      const chip = placement();
      const props = {
        placement: chip,
        visibleDwellMs: 1000,
        navigation: { navigate },
        onFeedback
      };
      // Watched for most of the countdown, then swiped away…
      act(() => {
        jest.advanceTimersByTime(IGNORED_MS - 500);
      });
      rerender(<ReelsCommerceChip {...props} isActive={false} />);
      act(() => {
        jest.advanceTimersByTime(IGNORED_MS * 2);
      });
      expect(queryByTestId("reels-commerce-body")).toBeTruthy();
      // …and back. The countdown restarts rather than resuming with 500ms left,
      // which is the forgiving direction: the chip is never retired by time the
      // user spent somewhere else.
      rerender(<ReelsCommerceChip {...props} isActive />);
      act(() => {
        jest.advanceTimersByTime(IGNORED_MS - 500);
      });
      expect(queryByTestId("reels-commerce-body")).toBeTruthy();
      act(() => {
        jest.advanceTimersByTime(600);
      });
      expect(queryByTestId("reels-commerce-body")).toBeNull();
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("ReelsCommerceChip — dismissal is a real preference", () => {
  it("reports `hide` when the user taps the close control", async () => {
    const { getByTestId, onFeedback } = renderChip();
    fireEvent.press(getByTestId("reels-commerce-dismiss"));
    await waitFor(() => expect(onFeedback).toHaveBeenCalled());
    const [reported, action] = onFeedback.mock.calls[0];
    expect(action).toBe("hide");
    expect(reported.placementId).toBe("p1");
  });

  it("removes the chip immediately rather than leaving a hole", async () => {
    const { getByTestId, queryByTestId } = renderChip();
    fireEvent.press(getByTestId("reels-commerce-dismiss"));
    await waitFor(() => expect(queryByTestId("reels-commerce-body")).toBeNull());
  });

  it("gives the close control a generous touch target without growing the box", () => {
    // A 22pt glyph on a video is hard to hit. The slop is expanded outward so
    // the chip's own bounds — and therefore the caption underneath it — do not
    // move.
    const { getByTestId } = renderChip();
    expect(getByTestId("reels-commerce-dismiss").props.hitSlop).toEqual({
      top: 10,
      bottom: 10,
      left: 10,
      right: 10
    });
  });
});

describe("ReelsCommerceChip — opening the product", () => {
  it("records the click before navigating, so the beacon is not racing a teardown", async () => {
    const { getByTestId, navigate } = renderChip();
    fireEvent.press(getByTestId("reels-commerce-body"));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(engagement).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "click");
    expect(engagement.mock.invocationCallOrder[0]).toBeLessThan(navigate.mock.invocationCallOrder[0]);
  });

  it("navigates to the product, carrying the listing id", async () => {
    const { getByTestId, navigate } = renderChip();
    fireEvent.press(getByTestId("reels-commerce-body"));
    await waitFor(() => expect(navigate).toHaveBeenCalled());
    expect(navigate).toHaveBeenCalledWith(
      "MarketplaceProduct",
      expect.objectContaining({ listingId: 501 })
    );
  });

  it("still records the click but goes nowhere when the listing id is missing", async () => {
    const { getByTestId, navigate } = renderChip({
      placement: placement({ product: { listingId: 0 } } as never)
    });
    fireEvent.press(getByTestId("reels-commerce-body"));
    await waitFor(() => expect(engagement).toHaveBeenCalled());
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe("ReelsCommerceChip — served and seen stay separate", () => {
  it("reports `served` on mount without claiming it was seen", () => {
    renderChip();
    expect(impression).toHaveBeenCalledTimes(1);
    expect(impression).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), {
      visible: false
    });
  });

  it("reports `seen` only once the server's dwell has elapsed", () => {
    jest.useFakeTimers();
    try {
      renderChip({ visibleDwellMs: 1500 });
      expect(impression).toHaveBeenCalledTimes(1);
      act(() => {
        jest.advanceTimersByTime(1600);
      });
      const visible = impression.mock.calls.filter(([, options]) => options?.visible);
      expect(visible).toHaveLength(1);
    } finally {
      jest.useRealTimers();
    }
  });

  it("does not count a reel the user never reached as seen", () => {
    jest.useFakeTimers();
    try {
      renderChip({ isActive: false, visibleDwellMs: 1000 });
      act(() => {
        jest.advanceTimersByTime(5000);
      });
      expect(impression.mock.calls.filter(([, options]) => options?.visible)).toHaveLength(0);
    } finally {
      jest.useRealTimers();
    }
  });
});

describe("ReelsCommerceChip — it cannot touch playback", () => {
  /**
   * Read as source rather than exercised through the renderer.
   *
   * The behavioural version of this test — inspect the rendered element's props
   * and assert none of them look like playback — is worthless: those props are
   * the ones this file passes in, so it can only ever confirm its own fixture.
   * The regression that actually matters is somebody later giving the component
   * a `videoRef` or an `onPauseReel`, and only the source can see that.
   *
   * The rule the mission states is that a chip never interferes with the reel.
   * It holds here because there is nothing in scope to interfere *with* — no
   * player, no media module, no audio session. That is a property of the import
   * list and the prop list, so that is what gets asserted.
   */
  const source = require("fs").readFileSync(
    require("path").join(__dirname, "..", "ReelsCommerceChip.tsx"),
    "utf8"
  );

  it("imports nothing that can control media", () => {
    const imports = source.match(/from\s+"([^"]+)"/g) || [];
    const forbidden = imports.filter((line: string) =>
      /expo-av|expo-audio|Video|mediaPlaybackCoordinator|audioSession|agora|liveAudio/i.test(line)
    );
    expect(forbidden).toEqual([]);
  });

  it("calls no playback, mute or seek API", () => {
    const calls = [
      "playAsync",
      "pauseAsync",
      "stopAsync",
      "setPositionAsync",
      "setIsMutedAsync",
      "setStatusAsync",
      "setAudioModeAsync",
      "claimMediaPlayback",
      "releaseMediaPlayback"
    ];
    expect(calls.filter((name) => source.includes(name))).toEqual([]);
  });

  it("declares no prop that hands it a player", () => {
    // The props block, not the whole file — a word like "video" is allowed to
    // appear in a comment explaining why it must not appear in the props.
    const props = source.slice(
      source.indexOf("export type ReelsCommerceChipProps"),
      source.indexOf("/** How long the chip lingers")
    );
    expect(props.length).toBeGreaterThan(50);
    expect(props).not.toMatch(/videoRef|playerRef|onPause|onPlay|onSeek|onMute|playbackStatus/i);
  });
});
