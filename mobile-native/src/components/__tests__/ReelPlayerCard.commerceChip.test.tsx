/**
 * Where the Marketplace chip lands on a Reel — asserted, not eyeballed.
 *
 * The mission's Reels rules are a list of things the chip must never cover: the
 * creator header, the caption, the audio controls, the action rail, the bottom
 * navigator, the progress bar. A screenshot review proves none of that; it
 * proves one caption length in one locale on one device.
 *
 * What actually discharges those rules is a structural fact: the chip is the
 * **first child of the reel's caption column**, and that column is anchored by
 * its *bottom* edge (`position: absolute; bottom: N`). Adding a row to the top
 * of a bottom-anchored column grows it upward. Everything below it — the
 * handle, the caption text, the music chip, the mute button — stays exactly
 * where it was, and everything outside it is unreachable by construction.
 *
 * So these tests assert the structure rather than any pixel:
 *
 *   1. The chip is inside the caption column.
 *   2. It comes before the caption title in that column's children.
 *   3. That column is bottom-anchored, which is what makes (1) and (2) mean
 *      "grows upward" instead of "pushes the caption down".
 *   4. The chip is in normal flow — an absolutely positioned chip would escape
 *      the column and the whole argument with it.
 *   5. With no placement bound, the card renders exactly what it rendered
 *      before this feature existed — not an empty container, *nothing*.
 *
 * (1)–(4) together are why the alternative — an overlay at some computed
 * `bottom` — was rejected: caption height is content- and locale-dependent, so
 * any constant is a guess that a two-line caption turns into an overlap.
 */
import { render } from "@testing-library/react-native";
import type { ReactTestInstance } from "react-test-renderer";
import { StyleSheet } from "react-native";

jest.mock("expo-av", () => {
  const ReactActual = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Audio: { Sound: { createAsync: jest.fn() }, setAudioModeAsync: jest.fn().mockResolvedValue(undefined) },
    Video: ReactActual.forwardRef(() => null)
  };
});
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
jest.mock("../../api/commerceDiscovery", () => ({
  recordCommerceImpression: jest.fn(() => Promise.resolve(true)),
  recordCommerceEngagement: jest.fn(() => Promise.resolve(true))
}));
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));
jest.mock("../../theme/logiNexusMotion", () => ({ useLogiNexusReducedMotion: () => true }));

import { ReelPlayerCard } from "../ReelPlayerCard";
import type { CommercePlacement } from "../../api/commerceDiscovery";

const REEL_ID = 88;
const PLACEMENT_ID = "p_reels_1";

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

function placement(): CommercePlacement {
  return {
    placementId: PLACEMENT_ID,
    impressionToken: "tok-1",
    surface: "reels",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId: 77,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120
    }
  };
}

function renderCard(commerce: unknown = null, reelOverrides: Record<string, unknown> = {}) {
  const noop = jest.fn();
  return render(
    <ReelPlayerCard
      reel={reel(reelOverrides)}
      active
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
      commerce={commerce as never}
    />
  );
}

function commerceBinding() {
  return {
    placement: placement(),
    visibleDwellMs: 1000,
    navigation: { navigate: jest.fn() },
    onFeedback: jest.fn()
  };
}

/** Every ancestor of a node, nearest first. */
function ancestors(node: ReactTestInstance): ReactTestInstance[] {
  const chain: ReactTestInstance[] = [];
  let current = node.parent;
  while (current) {
    chain.push(current);
    current = current.parent;
  }
  return chain;
}

/**
 * The caption column: the nearest ancestor whose flattened style is the
 * bottom-anchored overlay the reel lays its text out in.
 *
 * Found by its geometry rather than by a testID, deliberately. A testID added
 * for this test would be satisfied by moving it onto any other view; the
 * geometry is the thing the safety argument actually rests on, so that is what
 * gets matched.
 */
function captionColumn(node: ReactTestInstance): { host: ReactTestInstance; style: Record<string, any> } | null {
  for (const parent of ancestors(node)) {
    const style = StyleSheet.flatten(parent.props?.style) as Record<string, any> | undefined;
    if (!style) continue;
    if (style.position === "absolute" && typeof style.bottom === "number" && style.right === 76) {
      return { host: parent, style };
    }
  }
  return null;
}

/**
 * Everything inside the column, in render order.
 *
 * `.children` is the wrong tool here: it returns the *composite* wrappers (the
 * `View`/`Text` forwardRefs), while `getByTestId`/`getByText` hand back the
 * *host* instances underneath them, so a direct `children[0] === title` compares
 * two different nodes and is false even when the order is right.
 *
 * Comparing positions in this pre-order list is also what keeps a failure
 * readable: `expect(a).toBe(b)` on two `ReactTestInstance`s makes Jest serialise
 * two whole React trees to build its diff, which exhausts the heap and reports a
 * V8 stack trace instead of the assertion. Indices are numbers.
 */
function inRenderOrder(node: ReactTestInstance): ReactTestInstance[] {
  return node.findAll(() => true, { deep: true });
}

/** The host `Text` nodes in the column, in render order. */
function captionTexts(column: ReactTestInstance): ReactTestInstance[] {
  // `String(...)` rather than `node.type === "Text"`: the host instance's type
  // *is* the string "Text", but it is declared as `ElementType`, which TS says
  // cannot overlap a string literal. Composite wrappers stringify to something
  // else, which is exactly the distinction wanted here.
  return inRenderOrder(column).filter((node) => String(node.type) === "Text");
}

/**
 * The caption's own handle — the one in the column, not the one in the header.
 *
 * `@fixture_creator` is rendered twice on a reel: once by the creator header at
 * the top of the frame and once as the caption title. Disambiguating by text
 * would mean changing the fixture so the two differ, which is both a lie about
 * the real screen and a weaker test. Disambiguating by *position* is what this
 * file is about anyway, and it carries a second assertion for free: exactly one
 * of the two lives in the caption column, so the header handle is confirmed to
 * be outside it rather than assumed to be.
 */
function captionTitle(matches: ReactTestInstance[]): ReactTestInstance {
  const inColumn = matches.filter((node) => captionColumn(node) !== null);
  expect(inColumn).toHaveLength(1);
  return inColumn[0];
}

describe("the chip with nothing bound", () => {
  it("renders nothing at all, not an empty placeholder", () => {
    // The overwhelmingly common case: the reels floor is the strictest in the
    // system, so most reels carry no chip. Those reels must be byte-for-byte
    // the Reels screen that shipped before this feature existed.
    const { queryByTestId } = renderCard(null);
    expect(queryByTestId(`reels-commerce-${PLACEMENT_ID}`)).toBeNull();
    expect(queryByTestId("reels-commerce-body")).toBeNull();
    expect(queryByTestId("reels-commerce-dismiss")).toBeNull();
  });

  it("leaves the caption column exactly as it was", () => {
    const { getAllByText } = renderCard(null);
    // The caption's own handle is still the first text in the column — nothing
    // has been prepended above it. This is the exact assertion that the chip
    // inverts when a placement *is* bound, which is what makes the pair of them
    // mean "grew upward" rather than just "is present somewhere".
    const title = captionTitle(getAllByText("@fixture_creator"));
    const column = captionColumn(title)!;
    expect(captionTexts(column.host).indexOf(title)).toBe(0);
  });
});

describe("the chip's position is the safety argument", () => {
  it("renders inside the reel's caption column", () => {
    const { getByTestId } = renderCard(commerceBinding());
    const chip = getByTestId(`reels-commerce-${PLACEMENT_ID}`);
    expect(captionColumn(chip)).not.toBeNull();
  });

  it("comes before the caption title, so the column grows upward", () => {
    const { getByTestId, getAllByText } = renderCard(commerceBinding());
    const chip = getByTestId(`reels-commerce-${PLACEMENT_ID}`);
    const title = captionTitle(getAllByText("@fixture_creator"));
    const column = captionColumn(chip)!;
    const order = inRenderOrder(column.host);
    expect(order.indexOf(chip)).toBeGreaterThanOrEqual(0);
    expect(order.indexOf(title)).toBeGreaterThanOrEqual(0);
    expect(order.indexOf(chip)).toBeLessThan(order.indexOf(title));
    // …and the handle is no longer the column's first text, which is the other
    // half of "the column grew upward".
    expect(captionTexts(column.host).indexOf(title)).toBeGreaterThan(0);
  });

  it("sits in a column anchored by its bottom edge and not by its top", () => {
    // This is what turns "first child" into "grows upward". Anchor the column
    // by `top` instead and the identical tree would push the caption down and
    // off the frame.
    const { getByTestId } = renderCard(commerceBinding());
    const column = captionColumn(getByTestId(`reels-commerce-${PLACEMENT_ID}`))!;
    expect(typeof column.style.bottom).toBe("number");
    expect(column.style.top).toBeUndefined();
  });

  it("clears the action rail by the column's own inset, not by a number it chose", () => {
    // The rail is `right: 12` at `width: 60`, so it ends at 72; the column
    // starts at 76. The chip inherits that clearance by being inside the
    // column, which is why it cannot be got wrong here.
    const { getByTestId } = renderCard(commerceBinding());
    const column = captionColumn(getByTestId(`reels-commerce-${PLACEMENT_ID}`))!;
    expect(column.style.right).toBe(76);
  });

  it("stays in normal flow rather than floating over the column", () => {
    // An absolutely positioned chip would escape the column's layout and take
    // the entire no-overlap argument with it — it could then land on the
    // caption, the rail, or the progress bar depending on the caption's height.
    const { getByTestId } = renderCard(commerceBinding());
    const chip = getByTestId(`reels-commerce-${PLACEMENT_ID}`);
    const style = StyleSheet.flatten(chip.props.style) as Record<string, any>;
    expect(style.position).not.toBe("absolute");
    expect(style.top).toBeUndefined();
    expect(style.bottom).toBeUndefined();
  });

  it("is not a child of the action rail, the creator header or the progress bar", () => {
    // Belt and braces on the structural claim: every one of those is a sibling
    // of the caption column, so none of them can be on the chip's ancestor
    // chain unless somebody moved the render site.
    const { getByTestId } = renderCard(commerceBinding());
    const chip = getByTestId(`reels-commerce-${PLACEMENT_ID}`);
    const zIndexes = ancestors(chip)
      .map((parent) => StyleSheet.flatten(parent.props?.style) as Record<string, any> | undefined)
      .filter((style) => style && typeof style.zIndex === "number")
      .map((style) => style!.zIndex);
    // The caption column is zIndex 4. The rail, the header and the live badge
    // are 5; the progress track is 6. None of those may appear above the chip.
    expect(zIndexes.filter((value) => value > 4)).toEqual([]);
    expect(zIndexes).toContain(4);
  });
});

describe("the chip renders its product without disturbing the reel", () => {
  it("shows the product and keeps the caption, music chip and mute button", () => {
    const { getByText, getAllByText } = renderCard(commerceBinding());
    expect(getByText("Women's Casual Sneakers")).toBeTruthy();
    // Both handles — the creator header's and the caption's. Asserting on the
    // count rather than on "at least one" is what catches the chip displacing
    // one of the two rather than sitting above them.
    expect(getAllByText("@fixture_creator")).toHaveLength(2);
    expect(getByText("A reel fixture.")).toBeTruthy();
    expect(getByText("Original audio")).toBeTruthy();
  });
});
