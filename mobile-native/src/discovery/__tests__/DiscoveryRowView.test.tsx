/**
 * §5–§8, §11, §14 — the card the user actually touches.
 *
 * The single most consequential requirement across the whole discovery mission
 * is that a tap opens **the exact thing in the thumbnail**: the reel in that
 * poster, that person's profile, that status, that group. Everything else in the
 * feature is recoverable; a card that opens something else is the failure the
 * user reports as "it showed me the wrong video".
 *
 * So these assert the *arguments handed to the handlers*, not that a handler
 * fired. `toHaveBeenCalled()` would pass just as happily for a row that opens
 * the first item every time.
 *
 * The other thing under test is §11's arbitration: the Add Friend and Join
 * buttons live inside the pressable card, so a tap on the button must not also
 * open the profile behind it.
 */
import { act, fireEvent, render } from "@testing-library/react-native";
import { FlatList } from "react-native";
import { DiscoveryRowView, DiscoveryRowActions } from "../DiscoveryRowView";
import { resetDiscoveryImpressions, setDiscoveryAnalyticsSink } from "../analytics";
import { discoveryCardMetrics } from "../discoveryCardMetrics";
import { resetMediaPlayback } from "../../core/mediaPlaybackCoordinator";
import type { DiscoveryModule } from "../discoveryRows";

// The shell renders translated headings and button labels; the catalog itself is
// covered by the i18n validator, so here the key is echoed back to keep the
// assertions about behavior.
jest.mock("../../i18n/I18nContext", () => ({
  useTranslation: () => ({ t: (key: string) => key })
}));

// `expo-av` needs the ExponentAV native module at import time. The stand-in is a
// real host element rather than `null` so the player's props — muted, playing —
// stay assertable from the tree.
jest.mock("expo-av", () => {
  const ReactActual = require("react");
  const { View } = require("react-native");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Video: ReactActual.forwardRef((props: Record<string, unknown>, ref: unknown) => {
      ReactActual.useImperativeHandle(ref, () => ({
        playFromPositionAsync: () => Promise.resolve(),
        pauseAsync: () => Promise.resolve()
      }));
      return ReactActual.createElement(View, props);
    })
  };
});

function actions(): jest.Mocked<DiscoveryRowActions> {
  return {
    onOpenReel: jest.fn(),
    onOpenStatus: jest.fn(),
    onOpenGroup: jest.fn(),
    onOpenPerson: jest.fn(),
    onAddFriend: jest.fn(),
    onRemovePerson: jest.fn(),
    onJoinGroup: jest.fn(),
    onSeeAll: jest.fn(),
    onDismiss: jest.fn()
  };
}

const reelsModule: DiscoveryModule = {
  kind: "reels",
  titleKey: "social:feed.discovery.reelsTitle",
  items: [
    { reelId: 11, title: "First" },
    { reelId: 22, title: "Second" },
    { reelId: 33, title: "Third" }
  ]
};

const peopleModule: DiscoveryModule = {
  kind: "people",
  titleKey: "social:feed.discovery.peopleTitle",
  items: [
    { profileKey: "nova", username: "nova", displayName: "Nova" },
    { profileKey: "atlas", username: "atlas", displayName: "Atlas" },
    { profileKey: "vega", username: "vega", displayName: "Vega" }
  ]
};

const statusesModule: DiscoveryModule = {
  kind: "statuses",
  titleKey: "social:feed.discovery.statusesTitle",
  items: [
    { statusId: 101, title: "One", authorName: "Nova" },
    { statusId: 202, title: "Two", authorName: "Atlas" },
    { statusId: 303, title: "Three", authorName: "Vega" }
  ]
};

const groupsModule: DiscoveryModule = {
  kind: "groups",
  titleKey: "social:feed.discovery.groupsTitle",
  items: [
    { slug: "astro", name: "Astro" },
    { slug: "makers", name: "Makers" },
    { slug: "runners", name: "Runners" }
  ]
};

function renderRow(module: DiscoveryModule, extra: Partial<React.ComponentProps<typeof DiscoveryRowView>> = {}) {
  const handlers = actions();
  const utils = render(<DiscoveryRowView module={module} slot={0} {...handlers} {...extra} />);
  return { ...utils, handlers };
}

/** Reels that actually have something to play, for the autoplay assertions. */
const playableReelsModule: DiscoveryModule = {
  kind: "reels",
  titleKey: "social:feed.discovery.reelsTitle",
  items: [
    { reelId: 11, title: "First", previewVideoUrl: "https://cdn.example/1.m3u8" },
    { reelId: 22, title: "Second", previewVideoUrl: "https://cdn.example/2.m3u8" },
    { reelId: 33, title: "Third", previewVideoUrl: "https://cdn.example/3.m3u8" }
  ]
};

/** The carousel's own FlatList, so its layout contract can be read directly. */
function carouselOf(utils: ReturnType<typeof render>) {
  return utils.UNSAFE_getByType(FlatList).props as Record<string, never>;
}

/**
 * Drive the carousel's viewability callback the way FlatList would.
 *
 * There is no layout pass in jest, so nothing ever becomes "viewable" on its
 * own. Calling the handler directly is the honest substitute: it is the same
 * function FlatList calls, with the same token shape.
 */
async function reportViewable(utils: ReturnType<typeof render>, indices: number[], items: unknown[]) {
  const onViewableItemsChanged = carouselOf(utils).onViewableItemsChanged as unknown as (
    info: { viewableItems: unknown[] }
  ) => void;

  await act(async () => {
    onViewableItemsChanged({
      viewableItems: indices.map((index) => ({ index, item: items[index], isViewable: true, key: String(index) }))
    });
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  resetDiscoveryImpressions();
  setDiscoveryAnalyticsSink(() => undefined);
  resetMediaPlayback();
});

afterEach(() => {
  setDiscoveryAnalyticsSink(null);
});

describe("exact destinations (§4–§7)", () => {
  it("opens the reel that was tapped, not the first in the row", () => {
    const { getByTestId, handlers } = renderRow(reelsModule);

    fireEvent.press(getByTestId("discovery-card-reels-22"));

    expect(handlers.onOpenReel).toHaveBeenCalledTimes(1);
    expect(handlers.onOpenReel.mock.calls[0][0]).toMatchObject({ reelId: 22 });
  });

  it("opens the person that was tapped", () => {
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-card-people-atlas"));

    expect(handlers.onOpenPerson.mock.calls[0][0]).toMatchObject({ profileKey: "atlas" });
  });

  it("opens the status that was tapped", () => {
    const { getByTestId, handlers } = renderRow(statusesModule);

    fireEvent.press(getByTestId("discovery-card-statuses-202"));

    expect(handlers.onOpenStatus.mock.calls[0][0]).toMatchObject({ statusId: 202 });
  });

  it("opens the group that was tapped", () => {
    const { getByTestId, handlers } = renderRow(groupsModule);

    fireEvent.press(getByTestId("discovery-card-groups-makers"));

    expect(handlers.onOpenGroup.mock.calls[0][0]).toMatchObject({ slug: "makers" });
  });
});

describe("in-card actions (§5, §7, §11)", () => {
  it("sends a friend request without also opening the profile", () => {
    // The button is nested inside the card's Pressable. If the press propagates,
    // the user gets a friend request *and* a navigation they did not ask for.
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-add-friend-nova"));

    expect(handlers.onAddFriend.mock.calls[0][0]).toMatchObject({ profileKey: "nova" });
    expect(handlers.onOpenPerson).not.toHaveBeenCalled();
  });

  it("joins a group without also opening it", () => {
    const { getByTestId, handlers } = renderRow(groupsModule);

    fireEvent.press(getByTestId("discovery-join-group-astro"));

    expect(handlers.onJoinGroup.mock.calls[0][0]).toMatchObject({ slug: "astro" });
    expect(handlers.onOpenGroup).not.toHaveBeenCalled();
  });

  it("shows the requested state for a pending friend request", () => {
    const { getByTestId } = renderRow(peopleModule, { pendingFriendKeys: new Set(["nova"]) });

    expect(getByTestId("discovery-add-friend-nova").props.accessibilityLabel).toContain(
      "social:feed.discovery.requestSent"
    );
  });

  it("shows the joined state for a group already joined this session", () => {
    const { getByTestId } = renderRow(groupsModule, { joinedGroupSlugs: new Set(["astro"]) });

    expect(getByTestId("discovery-join-group-astro").props.accessibilityLabel).toContain(
      "social:feed.discovery.joined"
    );
  });

  it("removes the suggestion that was tapped, not the first in the row", () => {
    // Same failure mode as the tap assertions above: a remove control wired to
    // the wrong card silently deletes someone the user wanted to keep.
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-remove-person-atlas"));

    expect(handlers.onRemovePerson.mock.calls[0][0]).toMatchObject({ profileKey: "atlas" });
  });

  it("removes a suggestion without opening the profile or sending a request", () => {
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-remove-person-nova"));

    expect(handlers.onOpenPerson).not.toHaveBeenCalled();
    expect(handlers.onAddFriend).not.toHaveBeenCalled();
  });

  it("keeps the row's own dismiss separate from removing one person", () => {
    // The header X hides the whole module; the card X hides one suggestion.
    // Wiring both to the same handler would make one annoyed tap remove the row.
    const { getByTestId, handlers } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-remove-person-vega"));

    expect(handlers.onDismiss).not.toHaveBeenCalled();
  });
});

describe("the module shell (§2)", () => {
  it("dismisses the module by kind", () => {
    const { getByTestId, handlers } = renderRow(groupsModule);

    fireEvent.press(getByTestId("discovery-dismiss-groups"));

    expect(handlers.onDismiss).toHaveBeenCalledWith("groups");
  });

  it("renders See all when a destination was supplied", () => {
    const { getByTestId, handlers } = renderRow(reelsModule);

    fireEvent.press(getByTestId("discovery-see-all-reels"));

    expect(handlers.onSeeAll).toHaveBeenCalledWith("reels");
  });

  it("omits See all entirely when there is no destination", () => {
    // §9's rule applied generally: an inert control is worse than no control.
    // People has no friends screen in the mobile app, so the row has no See all.
    const { queryByTestId } = renderRow(peopleModule, { onSeeAll: undefined });

    expect(queryByTestId("discovery-see-all-people")).toBeNull();
  });

  it("labels the row with its translated heading", () => {
    const { getByTestId } = renderRow(reelsModule);

    expect(getByTestId("discovery-row-reels").props.accessibilityLabel).toBe(
      "social:feed.discovery.reelsTitle"
    );
  });

  it("renders nothing for a kind with no approved card (§9, §10)", () => {
    const topics: DiscoveryModule = {
      kind: "topics",
      titleKey: "social:feed.discovery.reelsTitle",
      items: [{ topic: "astro", label: "Astro" }]
    };

    const { queryByTestId } = renderRow(topics);

    expect(queryByTestId("discovery-card-topics-astro")).toBeNull();
  });
});

describe("§14.3 — the carousel snaps, and the next card peeks", () => {
  it("snaps to exactly one card per swipe", () => {
    const utils = renderRow(reelsModule);
    const stride = discoveryCardMetrics(750, "reels").stride;

    // jest's fake window is 750pt wide; what matters is that the snap interval
    // and the layout stride are the *same* number, not what that number is.
    expect(carouselOf(utils).snapToInterval).toBe(stride);
  });

  it("measures every card with the same stride it snaps to", () => {
    // Two independent expressions for one number is how a carousel ends up
    // snapping to a position no card starts at.
    const utils = renderRow(reelsModule);
    const props = carouselOf(utils);
    const layout = (props.getItemLayout as unknown as (d: unknown, i: number) => { length: number; offset: number })(
      null,
      3
    );

    expect(layout.length).toBe(props.snapToInterval);
    expect(layout.offset).toBe((props.snapToInterval as unknown as number) * 3);
  });

  it("aligns the settled card to the start, so the peek is a constant", () => {
    const utils = renderRow(reelsModule);

    expect(carouselOf(utils).snapToAlignment).toBe("start");
  });

  it("settles quickly and refuses to skate past several cards on a flick", () => {
    const utils = renderRow(reelsModule);

    expect(carouselOf(utils).decelerationRate).toBe("fast");
    expect(carouselOf(utils).disableIntervalMomentum).toBe(true);
  });

  it("insets the carousel by less than the panel padding it replaced (§2)", () => {
    const utils = renderRow(reelsModule);
    const style = carouselOf(utils).contentContainerStyle as unknown as Record<string, number>[];
    const flat = Object.assign({}, ...(Array.isArray(style) ? style.filter(Boolean) : [style]));

    expect(flat.paddingHorizontal).toBeLessThan(16);
  });

  it("keeps the row itself full-bleed, with no horizontal margin or panel border", () => {
    // The exact chrome §2 names: the old row was a bordered box inset from both
    // screen edges, which is what made the media look like a widget.
    const { getByTestId } = renderRow(reelsModule);
    const style = getByTestId("discovery-row-reels").props.style;
    const flat = Array.isArray(style) ? Object.assign({}, ...style.filter(Boolean)) : style;

    expect(flat.marginHorizontal).toBeUndefined();
    expect(flat.borderWidth).toBeUndefined();
    expect(flat.paddingHorizontal).toBeUndefined();
  });
});

describe("§14.4, §14.5, §14.8 — one preview, on the primary visible card only", () => {
  it("plays nothing until the carousel reports something visible", async () => {
    const { queryByTestId } = renderRow(playableReelsModule, { isRowVisible: true });

    expect(queryByTestId("discovery-card-reels-11-media-video")).toBeNull();
  });

  it("plays only the primary card, even when three are viewable", async () => {
    // The requirement's sharpest edge: several cards clear the visibility
    // threshold at once during a swipe, and exactly one of them may play.
    const utils = renderRow(playableReelsModule, { isRowVisible: true });
    await reportViewable(utils, [0, 1, 2], playableReelsModule.items);

    expect(utils.getByTestId("discovery-card-reels-11-media-video")).toBeTruthy();
    expect(utils.queryByTestId("discovery-card-reels-22-media-video")).toBeNull();
    expect(utils.queryByTestId("discovery-card-reels-33-media-video")).toBeNull();
  });

  it("moves the preview to the new primary card as the user swipes", async () => {
    const utils = renderRow(playableReelsModule, { isRowVisible: true });
    await reportViewable(utils, [0, 1], playableReelsModule.items);
    await reportViewable(utils, [1, 2], playableReelsModule.items);

    // The first card keeps its player mounted — §6 retains the frozen frame —
    // but the *new* card is the one that got a player of its own.
    expect(utils.getByTestId("discovery-card-reels-22-media-video")).toBeTruthy();
    expect(utils.queryByTestId("discovery-card-reels-33-media-video")).toBeNull();
  });

  it("uses the 60% threshold the requirement names", () => {
    const utils = renderRow(reelsModule);

    expect((carouselOf(utils).viewabilityConfig as unknown as Record<string, number>).itemVisiblePercentThreshold)
      .toBeGreaterThanOrEqual(60);
  });

  it("plays nothing at all while the row is off screen in the vertical feed", async () => {
    // §5's "no offscreen preload autoplay". A carousel that is mounted but three
    // screens down reports its own cards as viewable — the horizontal list has
    // no idea the row is not on screen — so the row must be gated too.
    const utils = renderRow(playableReelsModule, { isRowVisible: false });
    await reportViewable(utils, [0, 1, 2], playableReelsModule.items);

    expect(utils.queryByTestId("discovery-card-reels-11-media-video")).toBeNull();
  });

  it("defaults to not visible, so a caller that forgets the prop stays silent", async () => {
    const utils = renderRow(playableReelsModule);
    await reportViewable(utils, [0], playableReelsModule.items);

    expect(utils.queryByTestId("discovery-card-reels-11-media-video")).toBeNull();
  });
});

describe("§14.11, §14.12 — the border reacts to which card is active", () => {
  it("lights the primary card's border and leaves the others resting", async () => {
    const utils = renderRow(reelsModule, { isRowVisible: true });
    await reportViewable(utils, [0, 1, 2], reelsModule.items);

    expect(utils.getByTestId("discovery-card-reels-11-frame-sweep")).toBeTruthy();
    expect(utils.queryByTestId("discovery-card-reels-22-frame-sweep")).toBeNull();
    expect(utils.queryByTestId("discovery-card-reels-33-frame-sweep")).toBeNull();
  });

  it("animates no border at all while the row is off screen", async () => {
    // §8 forbids animating every card in a carousel; a row nobody can see
    // animating any card is the same waste with none of the benefit.
    const utils = renderRow(reelsModule, { isRowVisible: false });
    await reportViewable(utils, [0, 1, 2], reelsModule.items);

    expect(utils.queryByTestId("discovery-card-reels-11-frame-sweep")).toBeNull();
  });

  it("brightens a card while it is pressed (§8)", () => {
    const { getByTestId, queryByTestId } = renderRow(reelsModule);

    expect(queryByTestId("discovery-card-reels-22-frame-sweep")).toBeNull();

    fireEvent(getByTestId("discovery-card-reels-22"), "pressIn");

    expect(getByTestId("discovery-card-reels-22-frame-sweep")).toBeTruthy();
  });

  it("points an unseen status out even when it is not the focused card", async () => {
    // §10: statuses carry their own "new" signal, which is worth more than
    // carousel position. A seen one falls back to resting.
    const module: DiscoveryModule = {
      kind: "statuses",
      titleKey: "social:feed.discovery.statusesTitle",
      items: [
        { statusId: 101, title: "One", seen: true },
        { statusId: 202, title: "Two", seen: false }
      ]
    };
    const utils = renderRow(module, { isRowVisible: true });

    expect(utils.queryByTestId("discovery-card-statuses-101-frame-sweep")).toBeNull();
    expect(utils.getByTestId("discovery-card-statuses-202-frame-sweep")).toBeTruthy();
  });
});

describe("§14.13, §10 — non-video kinds never get a video container", () => {
  it("gives a person a portrait, not an empty player", () => {
    const { getByTestId, queryByTestId } = renderRow(peopleModule, { isRowVisible: true });

    expect(getByTestId("discovery-card-people-nova-media-fallback")).toBeTruthy();
    expect(queryByTestId("discovery-card-people-nova-media-video")).toBeNull();
  });

  it("gives a group with no cover a text-first card rather than a blank frame", () => {
    const { getByTestId, queryByTestId } = renderRow(groupsModule);

    expect(getByTestId("discovery-card-groups-astro-media-fallback")).toBeTruthy();
    expect(queryByTestId("discovery-card-groups-astro-media-video")).toBeNull();
  });

  it("shows a cover image when the group has one", () => {
    const module: DiscoveryModule = {
      kind: "groups",
      titleKey: "social:feed.discovery.groupsTitle",
      items: [{ slug: "astro", name: "Astro", coverUrl: "https://cdn.example/astro.jpg" }]
    };
    const { getByTestId, queryByTestId } = renderRow(module);

    expect(getByTestId("discovery-card-groups-astro-media-poster")).toBeTruthy();
    expect(queryByTestId("discovery-card-groups-astro-media-fallback")).toBeNull();
  });

  it("never plays a reel that has no playable url yet", async () => {
    // The adapter nulls `previewVideoUrl` while a reel transcodes. Being the
    // primary card must not conjure a player for it.
    const utils = renderRow(reelsModule, { isRowVisible: true });
    await reportViewable(utils, [0], reelsModule.items);

    expect(utils.queryByTestId("discovery-card-reels-11-media-video")).toBeNull();
  });
});

describe("analytics (§14)", () => {
  it("reports the exact destination id on a tap", () => {
    const events: { name: string; target?: string; slot: number }[] = [];
    setDiscoveryAnalyticsSink((event) => events.push(event));
    const { getByTestId } = renderRow(reelsModule, { slot: 2 });

    fireEvent.press(getByTestId("discovery-card-reels-33"));

    expect(events).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ name: "reel_opened", target: "33", slot: 2 })
      ])
    );
  });

  it("reports a friend request against the person it was sent to", () => {
    const events: { name: string; target?: string }[] = [];
    setDiscoveryAnalyticsSink((event) => events.push(event));
    const { getByTestId } = renderRow(peopleModule);

    fireEvent.press(getByTestId("discovery-add-friend-vega"));

    expect(events).toEqual(
      expect.arrayContaining([expect.objectContaining({ name: "friend_request_sent", target: "vega" })])
    );
  });
});
